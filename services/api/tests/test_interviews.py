"""Interview transcription workspace: upload pipeline, evidence and export."""

import base64
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from participant_fixtures import PARTICIPANT_INFORMATION
from sqlalchemy import select

import sixsentences_server.api.app as api_app
from sixsentences_server.api.app import _scoped_action_pool, create_app
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    CreditEventRow,
    InterviewMessageRow,
    InterviewRow,
    Org,
    VoiceInviteRow,
    VoiceSessionRow,
    VoiceStudyRow,
    db_session,
)
from sixsentences_server.core.entitlements import EntitlementError
from sixsentences_server.core.study_participation import voice_study_scope_fingerprint
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.interviews.analysis import (
    InterviewAgentTurn,
    run_interview_agent,
    verify_quote,
)
from sixsentences_server.interviews.service import merge_segments
from sixsentences_server.llm.base import LLMCancelledError, LLMConfigError
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.writer.service import CompileResult

_AUDIO = base64.b64encode(b"RIFF fake recording bytes").decode()


def _authed(app: FastAPI, email: str = "owner@example.org", org: str = "Acme") -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert resp.status_code == 201, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return client


def _configure_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from sixsentences_server.llm.mock import mock_pool

    monkeypatch.setenv("SIX_GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda _settings: mock_pool())
    get_settings.cache_clear()


def _fake_audio_layer(monkeypatch: pytest.MonkeyPatch, duration_ms: int = 90_000) -> None:
    def fake_probe(path, *, command="ffprobe"):
        return duration_ms

    def fake_transcode(source, target, *, command="ffmpeg"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ID3" + b"\x00" * 64)
        return target.stat().st_size

    monkeypatch.setattr("sixsentences_server.api.app.probe_duration_ms", fake_probe)
    monkeypatch.setattr("sixsentences_server.api.app.transcode_to_mp3", fake_transcode)


def _fake_transcription(monkeypatch: pytest.MonkeyPatch, seen: dict) -> None:
    def fake_transcribe(
        part, *, api_key, model, provider, language, offset_ms, context_tail, timeout=480.0
    ):
        seen["model"] = model
        seen["provider"] = provider
        seen["language"] = language
        return [
            {
                "speaker": "S1",
                "start_ms": offset_ms,
                "end_ms": offset_ms + 4_000,
                "text": "Welcome, tell me about the onboarding.",
            },
            {
                "speaker": "S2",
                "start_ms": offset_ms + 4_000,
                "end_ms": offset_ms + 9_000,
                "text": "Happy to. The mentors made the difference for me.",
            },
        ]

    monkeypatch.setattr("sixsentences_server.api.app.transcribe_chunk", fake_transcribe)


def _fake_analysis(monkeypatch: pytest.MonkeyPatch, seen: dict) -> None:
    def fake_analysis(pool, *, segments, speakers, title, guide, language):
        seen["guide"] = guide
        seen["analysis_language"] = language
        return {
            "summary": "One participant credits mentoring for a smooth start.",
            "themes": [
                {
                    "name": "Mentoring",
                    "description": "Mentors carried the onboarding.",
                    "quotes": [
                        {
                            "segment": 2,
                            "text": "The mentors made the difference",
                            "verified": True,
                            "timestamp": "00:04",
                            "speaker": "S2",
                        }
                    ],
                }
            ],
            "key_findings": ["Mentoring outweighed documentation."],
            "tensions": [],
            "followups": ["Ask how mentors were selected."],
            "quotes_total": 1,
            "quotes_verified": 1,
            "language": language,
        }

    monkeypatch.setattr("sixsentences_server.api.app.run_interview_analysis", fake_analysis)


def _attach_approved_spoken_scope(interview_public_id: str) -> int:
    """Turn one synthetic upload into a fully approved voice-study result."""

    with db_session() as session:
        interview = session.scalar(
            select(InterviewRow).where(InterviewRow.public_id == interview_public_id)
        )
        assert interview is not None
        study = VoiceStudyRow(
            org_id=interview.org_id,
            title="Synthetic voice study",
            language="en",
            guide={
                "sections": [
                    {"title": "Onboarding", "questions": ["What helped?"]},
                ]
            },
        )
        session.add(study)
        session.flush()
        scope_revision = "a" * 32
        study.participant_information = dict(PARTICIPANT_INFORMATION)
        study.participant_information = {
            **PARTICIPANT_INFORMATION,
            "dpia_scope_fingerprint": voice_study_scope_fingerprint(study),
            "dpia_public_scope_token": scope_revision,
        }
        interview.kind = "live"
        interview.model = "synthetic-spoken-model"
        interview.config = {
            **dict(interview.config or {}),
            "study_id": study.public_id,
            "study_scope_revision": scope_revision,
        }
        session.flush()
        return study.id


def _create_approved_voice_interview(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, int]:
    _configure_provider(monkeypatch)
    _fake_audio_layer(monkeypatch)
    _fake_transcription(monkeypatch, {})
    _fake_analysis(monkeypatch, {})
    created = client.post(
        "/interviews",
        json={"filename": "voice-scope.mp3", "content_base64": _AUDIO},
    )
    assert created.status_code == 202, created.text
    interview_id = str(created.json()["id"])
    assert client.get(f"/interviews/{interview_id}").json()["status"] == "ready"
    return interview_id, _attach_approved_spoken_scope(interview_id)


def _revoke_spoken_scope(
    study_id: int,
    *,
    write_ready: threading.Event,
    committed: threading.Event,
    errors: list[BaseException],
) -> None:
    try:
        with db_session() as session:
            study = session.get(VoiceStudyRow, study_id)
            assert study is not None
            information = dict(study.participant_information or {})
            information["spoken_processing_approved"] = False
            study.participant_information = information
            write_ready.set()
        committed.set()
    except BaseException as exc:  # noqa: BLE001 - propagated by the owning test
        errors.append(exc)
        write_ready.set()


def test_interview_lifecycle(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(create_app())

    # unconfigured providers -> honest 409 before anything is stored
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "")
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    missing = client.post("/interviews", json={"filename": "a.mp3", "content_base64": _AUDIO})
    assert missing.status_code == 409

    _configure_provider(monkeypatch)
    _fake_audio_layer(monkeypatch)
    seen: dict = {}
    _fake_transcription(monkeypatch, seen)
    _fake_analysis(monkeypatch, seen)

    created = client.post(
        "/interviews",
        json={
            "filename": "study-interview-01.m4a",
            "content_base64": _AUDIO,
            "language": "en",
            "guide": "Ask about onboarding.",
        },
    ).json()
    assert created["status"] == "pending"
    assert created["title"] == "study interview 01"

    detail = client.get(f"/interviews/{created['id']}").json()
    assert detail["status"] == "ready"  # test client runs background tasks inline
    assert [stage["status"] for stage in detail["pipeline"]] == [
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    assert detail["speakers"] == {"S1": "S1", "S2": "S2"}
    assert [segment["idx"] for segment in detail["segments"]] == [1, 2]
    assert detail["analysis"]["quotes_verified"] == 1
    assert detail["audio_available"] is True
    assert seen["provider"] == "google"
    assert seen["model"] == "gemini-3.5-flash"
    assert seen["guide"] == "Ask about onboarding."

    # transcription settles once the transcript landed: 90s -> 2 started
    # minutes on the free profile (12 units each)
    with db_session() as session:
        settled = session.scalars(
            select(CreditEventRow).where(CreditEventRow.action == "interview")
        ).all()
        assert [event.credits for event in settled] == [10]

    # rename, name the speakers; unknown labels are ignored
    patched = client.patch(
        f"/interviews/{created['id']}",
        json={"title": "Onboarding P1", "speakers": {"S2": "P1", "S9": "ghost"}},
    ).json()
    assert patched["title"] == "Onboarding P1"
    assert patched["speakers"] == {"S1": "S1", "S2": "P1"}

    # segment corrections are marked for the audit trail
    segment = client.patch(
        f"/interviews/{created['id']}/segments/2",
        json={"text": "Happy to. The mentors made the difference."},
    ).json()
    assert segment["edited"] is True

    # the player streams with Range support
    audio = client.get(f"/interviews/{created['id']}/audio")
    assert audio.status_code == 200
    assert audio.headers["accept-ranges"] == "bytes"
    partial = client.get(f"/interviews/{created['id']}/audio", headers={"Range": "bytes=0-2"})
    assert partial.status_code == 206
    assert partial.content == b"ID3"
    assert partial.headers["content-range"].startswith("bytes 0-2/")

    # exports: plain transcript, LaTeX source, compiled PDF
    txt = client.get(f"/interviews/{created['id']}/report", params={"format": "txt"})
    assert "[1] 00:00 S1:" in txt.text
    assert "P1: Happy to." in txt.text
    tex = client.get(f"/interviews/{created['id']}/report", params={"format": "tex"})
    assert tex.text.startswith("\\documentclass")
    assert "Onboarding P1" in tex.text
    assert "Mentoring" in tex.text
    monkeypatch.setattr(
        "sixsentences_server.api.app.compile_document",
        lambda content, bib, *, command, files=None, text_files=None: CompileResult(
            ok=True, pdf=b"%PDF-fake"
        ),
    )
    pdf = client.get(f"/interviews/{created['id']}/report")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    # remove the audio, keep the transcript
    removed = client.delete(f"/interviews/{created['id']}/audio").json()
    assert removed["audio_available"] is False
    assert removed["byte_size"] == 0
    assert client.get(f"/interviews/{created['id']}/audio").status_code == 404
    still = client.get(f"/interviews/{created['id']}").json()
    assert len(still["segments"]) == 2

    with db_session() as session:
        org = session.scalar(select(Org))
        interview = session.scalar(
            select(InterviewRow).where(InterviewRow.public_id == created["id"])
        )
        assert org is not None and interview is not None
        study = VoiceStudyRow(org_id=org.id, title="Sensitive study prompt")
        session.add(study)
        session.flush()
        invite = VoiceInviteRow(
            org_id=org.id,
            study_id=study.id,
            label="Synthetic quota receipt",
            max_sessions=1,
        )
        session.add(invite)
        session.flush()
        linked_voice = VoiceSessionRow(
            org_id=org.id,
            study_id=study.id,
            invite_id=invite.id,
            interview_id=interview.id,
            status="completed",
            participant_label="Participant 7",
            prompt="Sensitive interview guide",
            duration_ms=90_000,
            cost_units=42,
            relay_ticket_hash="f" * 64,
            relay_state={
                "version": 1,
                "user_id": 123,
                "voice": "Kore",
                "turns": [{"role": "participant", "text": "Sensitive answer"}],
                "observed_cost_usd": 0.01,
                "turns_started": 2,
            },
            error="Sensitive provider error",
            consent_at=datetime.now(UTC),
            consent_fingerprint="a" * 64,
            consent_snapshot={"participant": "Sensitive receipt"},
        )
        session.add(linked_voice)
        session.flush()
        linked_voice_id = linked_voice.id
        linked_voice_public_id = linked_voice.public_id
        linked_voice_invite_id = linked_voice.invite_id
        study_public_id = study.public_id

    assert linked_voice_invite_id is not None
    before_delete = client.get(f"/voice/studies/{study_public_id}").json()
    assert before_delete["invites"][0]["used_sessions"] == 1

    assert client.delete(f"/interviews/{created['id']}").json() == {"ok": True}
    assert client.get(f"/interviews/{created['id']}").status_code == 404
    with db_session() as session:
        minimized = session.get(VoiceSessionRow, linked_voice_id)
        assert minimized is not None
        assert minimized.interview_id is None
        assert minimized.invite_id == linked_voice_invite_id
        assert minimized.public_id != linked_voice_public_id
        assert minimized.participant_label == "" and minimized.prompt == ""
        assert minimized.relay_ticket_hash == ""
        assert minimized.relay_state == {
            "version": 1,
            "observed_cost_usd": 0.01,
            "turns_started": 2,
        }
        assert minimized.error == ""
        assert minimized.consent_at is None
        assert minimized.consent_fingerprint == ""
        assert minimized.consent_snapshot == {}
        assert minimized.duration_ms == 90_000 and minimized.cost_units == 42
    after_delete = client.get(f"/voice/studies/{study_public_id}").json()
    assert after_delete["invites"][0]["used_sessions"] == 1


def test_interview_delete_rolls_back_when_artifact_cleanup_fails(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    with db_session() as session:
        org = session.scalar(select(Org))
        assert org is not None
        interview = InterviewRow(
            org_id=org.id,
            title="Synthetic deletion rollback",
            status="ready",
        )
        session.add(interview)
        session.flush()
        interview_id = interview.public_id

    def fail_cleanup(_path: object) -> None:
        raise OSError("synthetic storage refusal")

    monkeypatch.setattr("sixsentences_server.api.app._remove_tree_missing_ok", fail_cleanup)
    with pytest.raises(OSError, match="synthetic storage refusal"):
        client.delete(f"/interviews/{interview_id}")
    assert client.get(f"/interviews/{interview_id}").status_code == 200


def test_interview_chat_and_reanalysis(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    _configure_provider(monkeypatch)
    _fake_audio_layer(monkeypatch)
    _fake_transcription(monkeypatch, {})
    _fake_analysis(monkeypatch, {})
    created = client.post(
        "/interviews", json={"filename": "p1.mp3", "content_base64": _AUDIO}
    ).json()
    assert client.get(f"/interviews/{created['id']}").json()["status"] == "ready"

    class FakePool:
        clients = {"fake": object()}

        def pinned(self, ref):
            return self

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())

    agent_languages: list[str] = []

    def fake_agent(
        pool,
        *,
        request,
        segments,
        speakers,
        title,
        analysis,
        history,
        language,
        assistant_preferences,
    ):
        agent_languages.append(language)
        assert len(segments) == 2
        assert assistant_preferences["tone"] == "academic"
        return InterviewAgentTurn(
            answer="P1 credits the mentors.",
            quotes=[
                {
                    "segment": 2,
                    "text": "The mentors made the difference",
                    "verified": True,
                    "timestamp": "00:04",
                    "speaker": "S2",
                }
            ],
            outcome="completed",
        )

    monkeypatch.setattr("sixsentences_server.api.app.run_interview_agent", fake_agent)
    reviewed_results: list[dict[str, object]] = []
    reviewed_languages: list[str] = []

    def fake_result_review(pool, **kwargs):
        del pool
        reviewed_languages.append(kwargs["language"])
        reviewed_results.extend(kwargs["executed_results"])
        return SimpleNamespace(
            answer=kwargs["initial_answer"],
            complete=True,
            summary="Verified transcript result.",
        )

    monkeypatch.setattr(
        "sixsentences_server.api.app.review_execution_results",
        fake_result_review,
    )
    reply = client.post(
        f"/interviews/{created['id']}/chat", json={"question": "What helped P1?"}
    ).json()
    assert reply["answer"] == "P1 credits the mentors."
    assert reply["quotes"][0]["verified"] is True
    assert reviewed_results == [
        {
            "operation": "answer_from_transcript",
            "applied": False,
            "read_only": True,
            "status": "completed",
            "transcript_revision": 1,
            "transcript_segments": 2,
            "verified_quote_count": 1,
            "verified_quotes": [
                {
                    "segment": 2,
                    "speaker": "S2",
                    "timestamp": "00:04",
                    "text": "The mentors made the difference",
                    "verified": True,
                }
            ],
        }
    ]
    assert reply["transcript_revision"] == 1
    assert agent_languages == ["en"] and reviewed_languages == ["en"]
    history = client.get(f"/interviews/{created['id']}/chat").json()
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[1]["payload"]["quotes"][0]["segment"] == 2
    assert history[1]["payload"]["transcript_revision"] == 1

    # a fresh analysis pass is charged and lands in the background
    def fake_reanalysis(pool, *, segments, speakers, title, guide, language):
        return {
            "summary": "Second pass.",
            "themes": [],
            "key_findings": [],
            "tensions": [],
            "followups": [],
            "quotes_total": 2,
            "quotes_verified": 2,
            "language": language,
        }

    monkeypatch.setattr("sixsentences_server.api.app.run_interview_analysis", fake_reanalysis)
    accepted = client.post(f"/interviews/{created['id']}/analyze", json={})
    assert accepted.status_code == 202
    assert accepted.json()["analyzing"] is True
    detail = client.get(f"/interviews/{created['id']}").json()
    assert detail["analyzing"] is False
    assert detail["analysis"]["summary"] == "Second pass."
    with db_session() as session:
        actions = session.scalars(
            select(CreditEventRow.credits).where(CreditEventRow.action == "interview")
        ).all()
        # settlement (2 min x 12) plus one re-analysis (2 x 250 on free)
    assert sorted(actions) == [10, 120]

    german = client.post(
        f"/interviews/{created['id']}/chat",
        json={
            "question": (
                "Nur prüfen, nichts ändern: Hat der Pilot im gespeicherten Transkript einen "
                "konkreten Grund oder ein Beispiel für seinen Spaß genannt, oder bleibt er "
                "allgemein? Vergleiche seine frühere und letzte Aussage mit kurzen "
                "Originalzitaten samt Segmentnummern. Trenne Pilot-Aussagen von Fragen der KI; "
                "antworte auf Deutsch."
            )
        },
    )
    assert german.status_code == 200
    assert agent_languages[-1] == "de" and reviewed_languages[-1] == "de"


def test_interview_chat_cancellation_rolls_back_messages_and_charge(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    _configure_provider(monkeypatch)
    _fake_audio_layer(monkeypatch)
    _fake_transcription(monkeypatch, {})
    _fake_analysis(monkeypatch, {})
    created = client.post(
        "/interviews", json={"filename": "cancel.mp3", "content_base64": _AUDIO}
    ).json()

    class FakePool:
        clients = {"fake": object()}

        def pinned(self, ref):
            return self

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    monkeypatch.setattr(
        "sixsentences_server.api.app.run_interview_agent",
        lambda pool, **kwargs: InterviewAgentTurn(
            answer="Mentors helped.",
            quotes=[
                {
                    "segment": 2,
                    "text": "The mentors made the difference",
                    "verified": True,
                    "timestamp": "00:04",
                    "speaker": "S2",
                }
            ],
            outcome="completed",
        ),
    )
    monkeypatch.setattr(
        "sixsentences_server.api.app.review_execution_results",
        lambda pool, **kwargs: SimpleNamespace(
            answer=kwargs["initial_answer"],
            complete=True,
            summary="Verified.",
        ),
    )
    checks = iter([False, False, False, True])
    monkeypatch.setattr(
        "sixsentences_server.api.app.current_agent_cancel_check",
        lambda: lambda: next(checks),
    )

    with pytest.raises(LLMCancelledError):
        client.post(
            f"/interviews/{created['id']}/chat",
            json={"question": "What helped?"},
        )

    with db_session() as session:
        interview = session.scalar(
            select(InterviewRow).where(InterviewRow.public_id == created["id"])
        )
        assert interview is not None
        messages = session.scalars(
            select(InterviewMessageRow).where(InterviewMessageRow.interview_id == interview.id)
        ).all()
        charges = session.scalars(
            select(CreditEventRow).where(CreditEventRow.action == "question")
        ).all()
    assert messages == []
    assert charges == []


@pytest.mark.parametrize("review_error", [EntitlementError, LLMConfigError])
def test_interview_chat_review_setup_failure_uses_extractive_fallback(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
    review_error: type[RuntimeError],
) -> None:
    client = _authed(create_app())
    _configure_provider(monkeypatch)
    _fake_audio_layer(monkeypatch)
    _fake_transcription(monkeypatch, {})
    _fake_analysis(monkeypatch, {})
    created = client.post(
        "/interviews", json={"filename": "fallback.mp3", "content_base64": _AUDIO}
    ).json()

    class FakePool:
        clients = {"fake": object()}

        def pinned(self, ref):
            return self

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    monkeypatch.setattr(
        "sixsentences_server.api.app.run_interview_agent",
        lambda pool, **kwargs: InterviewAgentTurn(
            answer="Invented narrative that must not be persisted.",
            quotes=[
                {
                    "segment": 2,
                    "text": "The mentors made the difference",
                    "verified": True,
                    "timestamp": "00:04",
                    "speaker": "S2",
                }
            ],
            outcome="completed",
        ),
    )
    calls = 0

    @contextmanager
    def fail_review_pool(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise review_error("result review unavailable")
        with _scoped_action_pool(*args, **kwargs) as pool:
            yield pool

    monkeypatch.setattr("sixsentences_server.api.app._scoped_action_pool", fail_review_pool)

    response = client.post(
        f"/interviews/{created['id']}/chat",
        json={"question": "What helped?"},
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "Invented narrative" not in answer
    assert "The mentors made the difference" in answer
    assert "S2 · Seg. 2 · 00:04" in answer
    assert calls == 2


def test_interview_chat_rejects_a_transcript_revision_race(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    _configure_provider(monkeypatch)
    _fake_audio_layer(monkeypatch)
    _fake_transcription(monkeypatch, {})
    _fake_analysis(monkeypatch, {})
    created = client.post(
        "/interviews", json={"filename": "race.mp3", "content_base64": _AUDIO}
    ).json()

    class FakePool:
        clients = {"fake": object()}

        def pinned(self, ref):
            return self

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    monkeypatch.setattr(
        "sixsentences_server.api.app.run_interview_agent",
        lambda pool, **kwargs: InterviewAgentTurn(
            answer="Mentors helped.",
            quotes=[
                {
                    "segment": 2,
                    "text": "The mentors made the difference",
                    "verified": True,
                    "timestamp": "00:04",
                    "speaker": "S2",
                }
            ],
            outcome="completed",
        ),
    )

    def change_revision_during_review(pool, **kwargs):
        del pool
        with db_session() as race_session:
            interview = race_session.scalar(
                select(InterviewRow).where(InterviewRow.public_id == created["id"])
            )
            assert interview is not None
            config = dict(interview.config or {})
            config["revision"] = int(config.get("revision", 1)) + 1
            interview.config = config
        return SimpleNamespace(
            answer=kwargs["initial_answer"],
            complete=True,
            summary="Verified.",
        )

    monkeypatch.setattr(
        "sixsentences_server.api.app.review_execution_results",
        change_revision_during_review,
    )
    response = client.post(
        f"/interviews/{created['id']}/chat",
        json={"question": "What helped?"},
    )

    assert response.status_code == 409
    assert "transcript changed" in response.json()["detail"]
    with db_session() as session:
        interview = session.scalar(
            select(InterviewRow).where(InterviewRow.public_id == created["id"])
        )
        assert interview is not None
        messages = session.scalars(
            select(InterviewMessageRow).where(InterviewMessageRow.interview_id == interview.id)
        ).all()
        charges = session.scalars(
            select(CreditEventRow).where(CreditEventRow.action == "question")
        ).all()
    assert messages == []
    assert charges == []


def test_voice_interview_chat_rechecks_exact_scope_at_first_provider_egress(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _authed(create_app())
    interview_id, study_id = _create_approved_voice_interview(client, monkeypatch)
    original_build_scoped_pool = api_app._build_scoped_pool
    scope_replaced = False

    def replace_scope_before_provider(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal scope_replaced
        if not scope_replaced:
            with db_session() as session:
                study = session.get(VoiceStudyRow, study_id)
                assert study is not None
                information = dict(study.participant_information or {})
                # A fresh approval token represents a replacement assessed
                # scope. The old interview receipt must never inherit it.
                information["dpia_public_scope_token"] = "b" * 32
                study.participant_information = information
            scope_replaced = True
        return original_build_scoped_pool(*args, **kwargs)

    monkeypatch.setattr(api_app, "_build_scoped_pool", replace_scope_before_provider)
    monkeypatch.setattr(
        api_app,
        "run_interview_agent",
        lambda *args, **kwargs: pytest.fail("stale voice transcript reached a provider"),
    )

    response = client.post(
        f"/interviews/{interview_id}/chat",
        json={"question": "What helped?"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "interview_processing_scope_required"


def test_voice_interview_chat_holds_scope_fence_during_first_provider_call(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _authed(create_app())
    interview_id, study_id = _create_approved_voice_interview(client, monkeypatch)
    write_ready = threading.Event()
    committed = threading.Event()
    provider_returned = threading.Event()
    mutation_errors: list[BaseException] = []
    mutation_threads: list[threading.Thread] = []

    def fake_agent(pool, **kwargs):  # type: ignore[no-untyped-def]
        del pool, kwargs
        mutation = threading.Thread(
            target=_revoke_spoken_scope,
            kwargs={
                "study_id": study_id,
                "write_ready": write_ready,
                "committed": committed,
                "errors": mutation_errors,
            },
            daemon=True,
        )
        mutation_threads.append(mutation)
        mutation.start()
        assert write_ready.wait(2)
        assert not committed.wait(0.2), "scope revocation crossed the provider fence"
        provider_returned.set()
        return InterviewAgentTurn(
            answer="Mentors helped.",
            quotes=[],
            outcome="completed",
        )

    def cancel_check() -> bool:
        if provider_returned.is_set():
            assert committed.wait(3), "scope revocation did not resume after provider egress"
        return False

    monkeypatch.setattr(api_app, "run_interview_agent", fake_agent)
    monkeypatch.setattr(api_app, "current_agent_cancel_check", lambda: cancel_check)
    monkeypatch.setattr(
        api_app,
        "review_execution_results",
        lambda *args, **kwargs: pytest.fail("revoked voice transcript reached result review"),
    )

    response = client.post(
        f"/interviews/{interview_id}/chat",
        json={"question": "What helped?"},
    )
    for mutation in mutation_threads:
        mutation.join(timeout=3)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "interview_processing_scope_required"
    assert mutation_errors == []
    assert committed.is_set()


def test_voice_interview_chat_holds_scope_fence_during_result_review(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _authed(create_app())
    interview_id, study_id = _create_approved_voice_interview(client, monkeypatch)
    write_ready = threading.Event()
    committed = threading.Event()
    mutation_errors: list[BaseException] = []
    mutation_threads: list[threading.Thread] = []

    monkeypatch.setattr(
        api_app,
        "run_interview_agent",
        lambda pool, **kwargs: InterviewAgentTurn(
            answer="Mentors helped.",
            quotes=[],
            outcome="completed",
        ),
    )

    class DeferredReview:
        complete = True
        summary = "Verified."

        @property
        def answer(self) -> str:
            assert committed.wait(3), "scope revocation did not resume after result review"
            return "Mentors helped."

    def fake_review(pool, **kwargs):  # type: ignore[no-untyped-def]
        del pool, kwargs
        mutation = threading.Thread(
            target=_revoke_spoken_scope,
            kwargs={
                "study_id": study_id,
                "write_ready": write_ready,
                "committed": committed,
                "errors": mutation_errors,
            },
            daemon=True,
        )
        mutation_threads.append(mutation)
        mutation.start()
        assert write_ready.wait(2)
        assert not committed.wait(0.2), "scope revocation crossed the review fence"
        return DeferredReview()

    monkeypatch.setattr(api_app, "review_execution_results", fake_review)

    response = client.post(
        f"/interviews/{interview_id}/chat",
        json={"question": "What helped?"},
    )
    for mutation in mutation_threads:
        mutation.join(timeout=3)

    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Mentors helped."
    assert committed.is_set()
    assert mutation_errors == []


def test_interview_agent_recovers_when_selected_provider_fails() -> None:
    """A transient selected-model outage must not surface as an HTTP 500."""

    class RecoveryPool:
        def complete_json(self, *args, **kwargs):
            return SimpleNamespace(
                text=(
                    '{"answer":"P1 nennt Mentoring als hilfreich.",'
                    '"quotes":[{"segment":1,"text":"Mentoring hat mir geholfen."}],'
                    '"workspace_actions":[]}'
                )
            )

        def complete(self, *args, **kwargs):
            return self.complete_json(*args, **kwargs)

    class FailingPool:
        def complete_json(self, *args, **kwargs):
            raise ProviderError("provider unavailable")

        def complete(self, *args, **kwargs):
            raise ProviderError("provider unavailable")

        def pinned(self, ref):
            return RecoveryPool()

    turn = run_interview_agent(
        FailingPool(),  # type: ignore[arg-type]
        request="was hat p1 übers mentroing gesagt zeig original stelle mit zeit",
        segments=[
            {
                "idx": 1,
                "speaker": "P1",
                "start_ms": 12_000,
                "end_ms": 15_000,
                "text": "Mentoring hat mir geholfen.",
            }
        ],
        speakers={"P1": "P1"},
        title="Test interview",
        analysis={},
        history=[],
        language="de",
    )

    assert turn.answer == "P1 nennt Mentoring als hilfreich."
    assert turn.quotes == [
        {
            "segment": 1,
            "text": "Mentoring hat mir geholfen.",
            "verified": True,
            "start_ms": 12_000,
            "timestamp": "00:12",
            "speaker": "P1",
        }
    ]
    assert turn.workspace_actions == []
    assert turn.outcome == "completed"


def test_interview_agent_marks_unrecoverable_structure_as_failed() -> None:
    class FailingPool:
        def complete_json(self, *args, **kwargs):
            raise ProviderError("provider unavailable")

        def complete(self, *args, **kwargs):
            raise ProviderError("provider unavailable")

        def pinned(self, ref):
            return self

    turn = run_interview_agent(
        FailingPool(),  # type: ignore[arg-type]
        request="Summarize the transcript.",
        segments=[],
        speakers={},
        title="Test interview",
        analysis={},
        history=[],
        language="en",
    )

    assert turn.outcome == "failed"
    assert turn.quotes == []


def test_interview_upload_validation(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(create_app())
    _configure_provider(monkeypatch)

    bogus_model = client.post(
        "/interviews",
        json={"filename": "a.mp3", "content_base64": _AUDIO, "model": "whisper-9"},
    )
    assert bogus_model.status_code == 422

    bad_suffix = client.post(
        "/interviews", json={"filename": "notes.exe", "content_base64": _AUDIO}
    )
    assert bad_suffix.status_code == 422

    bad_base64 = client.post(
        "/interviews", json={"filename": "a.mp3", "content_base64": "not-base64!!"}
    )
    assert bad_base64.status_code == 422

    from sixsentences_server.interviews.audio import AudioProcessingError

    def broken_probe(path, *, command="ffprobe"):
        raise AudioProcessingError("This file does not contain a readable audio track.")

    monkeypatch.setattr("sixsentences_server.api.app.probe_duration_ms", broken_probe)
    unreadable = client.post("/interviews", json={"filename": "a.mp3", "content_base64": _AUDIO})
    assert unreadable.status_code == 422
    assert "readable audio" in unreadable.json()["detail"]
    # the rejected upload leaves nothing behind
    incoming = get_settings().data_dir / "interviews" / "incoming"
    assert not any(incoming.glob("*"))
    assert client.get("/interviews").json() == []


def test_verify_quote_relocates_and_flags() -> None:
    segments = [
        {
            "idx": 1,
            "speaker": "S1",
            "start_ms": 0,
            "end_ms": 4_000,
            "text": "We rolled out the new onboarding flow in March.",
        },
        {
            "idx": 2,
            "speaker": "S2",
            "start_ms": 4_000,
            "end_ms": 9_000,
            "text": "Absolutely, the mentors made the difference.",
        },
        {
            "idx": 3,
            "speaker": "S2",
            "start_ms": 9_000,
            "end_ms": 12_000,
            "text": "Also ich muss sagen, so, sagen wir, durchwachsen.",
        },
    ]
    # normalization tolerates case and quote characters
    verified = verify_quote(
        {"segment": 1, "text": "Rolled OUT the “new” onboarding flow"}, segments
    )
    assert verified["verified"] is True
    assert verified["segment"] == 1
    assert verified["text"] == "rolled out the new onboarding flow"
    # a quote claimed for the wrong segment is re-anchored, not trusted
    relocated = verify_quote({"segment": 1, "text": "the mentors made the difference"}, segments)
    assert relocated["verified"] is True
    assert relocated["segment"] == 2
    assert relocated["speaker"] == "S2"
    assert relocated["text"] == "the mentors made the difference"
    punctuation_tolerant = verify_quote(
        {
            "segment": 3,
            "text": "also ich muss sagen so sagen wir durchwachsen",
        },
        segments,
    )
    assert punctuation_tolerant["verified"] is True
    assert punctuation_tolerant["text"] == ("Also ich muss sagen, so, sagen wir, durchwachsen")
    # an invented quote survives as text but is flagged
    invented = verify_quote({"segment": 2, "text": "we doubled revenue"}, segments)
    assert invented["verified"] is False


def test_merge_segments_joins_boundary_fragments() -> None:
    chunks = [
        [
            {"speaker": "S1", "start_ms": 0, "end_ms": 4_000, "text": "Hello and"},
        ],
        [
            {"speaker": "S1", "start_ms": 4_500, "end_ms": 9_000, "text": "welcome back."},
            {"speaker": "S2", "start_ms": 9_000, "end_ms": 12_000, "text": "Thanks."},
        ],
    ]
    merged = merge_segments(chunks)
    assert [segment["idx"] for segment in merged] == [1, 2]
    assert merged[0]["text"] == "Hello and welcome back."
    assert merged[0]["end_ms"] == 9_000
    assert merged[1]["speaker"] == "S2"


def test_interview_moves_between_projects(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_provider(monkeypatch)
    _fake_audio_layer(monkeypatch)
    seen: dict = {}
    _fake_transcription(monkeypatch, seen)
    _fake_analysis(monkeypatch, seen)
    client = _authed(create_app())

    home = client.post("/projects", json={"name": "Onboarding study"}).json()
    created = client.post(
        "/interviews",
        json={
            "filename": "p1.mp3",
            "content_base64": _AUDIO,
            "project_id": home["id"],
        },
    ).json()
    assert created["project_id"] == home["id"]

    # the project workspace lists the transcript
    workspace = client.get(f"/projects/{home['id']}/workspace").json()
    assert workspace["counts"]["interviews"] == 1
    assert workspace["interviews"][0]["id"] == created["id"]
    assert workspace["interviews"][0]["kind"] == "upload"

    # move it to a second project, then detach it entirely
    second = client.post("/projects", json={"name": "Follow-up"}).json()
    moved = client.patch(f"/interviews/{created['id']}", json={"project_id": second["id"]}).json()
    assert moved["project_id"] == second["id"]
    emptied = client.get(f"/projects/{home['id']}/workspace").json()
    assert emptied["counts"]["interviews"] == 0
    detached = client.patch(f"/interviews/{created['id']}", json={"project_id": None}).json()
    assert detached["project_id"] is None

    # a patch that never mentions project_id leaves the assignment alone
    client.patch(f"/interviews/{created['id']}", json={"project_id": home["id"]})
    renamed = client.patch(f"/interviews/{created['id']}", json={"title": "P1, onboarding"}).json()
    assert renamed["title"] == "P1, onboarding"
    assert renamed["project_id"] == home["id"]

    # another workspace's project is invisible, not assignable
    foreign = _authed(create_app(), email="other@example.org", org="Other")
    foreign_project = foreign.post("/projects", json={"name": "Not yours"}).json()
    refused = client.patch(
        f"/interviews/{created['id']}", json={"project_id": foreign_project["id"]}
    )
    assert refused.status_code == 404
