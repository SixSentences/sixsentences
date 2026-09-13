"""Interview evidence linked to the manuscript assistant."""

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from participant_fixtures import PARTICIPANT_INFORMATION
from sqlalchemy import select

import sixsentences_server.api.app as api_app
from sixsentences_server.api.app import create_app
from sixsentences_server.core.db import (
    InterviewRow,
    InterviewSegmentRow,
    Org,
    VoiceStudyRow,
    db_session,
)
from sixsentences_server.core.study_participation import voice_study_scope_fingerprint
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.writer.assistant import AssistantTurn
from sixsentences_server.writer.interview_context import prepare_interview_evidence


def _authed(app: FastAPI, email: str = "owner@example.org", org: str = "Acme") -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _grant_scholar(client: TestClient) -> int:
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"
    return org_id


def _interview(org_id: int, title: str, summary: str, transcript: list[str]) -> str:
    with db_session() as session:
        row = InterviewRow(
            org_id=org_id,
            title=title,
            status="ready",
            language="en",
            guide="Semi-structured guide: onboarding, workload and support.",
            duration_ms=len(transcript) * 30000,
            speakers={"S1": "Interviewer", "S2": "P1"},
            analysis={
                "summary": summary,
                "themes": [
                    {
                        "name": "Mentoring",
                        "description": "Mentoring shaped the participant experience.",
                        "quotes": [
                            {
                                "segment": 2,
                                "text": transcript[1],
                                "verified": True,
                                "speaker": "S2",
                                "timestamp": "00:30",
                            }
                        ],
                    }
                ],
                "key_findings": [summary],
            },
            config={"segment_count": len(transcript)},
        )
        session.add(row)
        session.flush()
        for index, text in enumerate(transcript, start=1):
            session.add(
                InterviewSegmentRow(
                    org_id=org_id,
                    interview_id=row.id,
                    idx=index,
                    speaker="S1" if index == 1 else "S2",
                    start_ms=(index - 1) * 30000,
                    end_ms=index * 30000,
                    text=text,
                )
            )
        return row.public_id


def _attach_approved_spoken_scope(interview_public_id: str) -> int:
    """Turn one synthetic upload into approved voice-derived evidence."""
    with db_session() as session:
        interview = session.scalar(
            select(InterviewRow).where(InterviewRow.public_id == interview_public_id)
        )
        assert interview is not None
        study = VoiceStudyRow(
            org_id=interview.org_id,
            title="Synthetic Writer voice study",
            language="en",
            guide={"sections": [{"title": "Onboarding", "questions": ["What helped?"]}]},
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
    except BaseException as exc:
        errors.append(exc)
        write_ready.set()


class _WriterScopePool:
    def has_strong(self) -> bool:
        return True

    def pinned(self, ref: object) -> "_WriterScopePool":
        del ref
        return self


def test_writer_links_multiple_interviews_with_explicit_access(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app()
    client = _authed(app)
    org_id = _grant_scholar(client)
    analysis_only = _interview(
        org_id,
        "Participant Alpha",
        "Alpha found the onboarding guide useful.",
        [
            "How did you experience onboarding?",
            "The onboarding guide was useful.",
            "The private alpha transcript must remain hidden.",
        ],
    )
    transcript = _interview(
        org_id,
        "Participant Beta",
        "Beta relied on mentoring during onboarding.",
        [
            "What helped you during onboarding?",
            "Weekly mentoring reduced my workload and uncertainty.",
            "Documentation was useful after the first month.",
        ],
    )
    doc = client.post("/writer", json={"title": "Findings", "template": "blank"}).json()
    linked = client.put(
        f"/writer/{doc['public_id']}/interview-contexts",
        json={
            "sources": [
                {"interview_id": analysis_only, "mode": "analysis", "include_methodology": True},
                {"interview_id": transcript, "mode": "transcript"},
            ]
        },
    )
    assert linked.status_code == 200, linked.text
    catalog = linked.json()
    assert catalog["linked_count"] == 2
    assert catalog["analysis_count"] == 1
    assert catalog["transcript_count"] == 1
    assert catalog["methodology_count"] == 1
    assert [item["mode"] for item in catalog["items"][:2]] == ["analysis", "transcript"]
    prompts: list[str] = []

    class FakePool:
        def __init__(self) -> None:
            self.read_handles: set[str] = set()

        def has_strong(self) -> bool:
            return True

        def pinned(self, ref: object) -> "FakePool":
            return self

        def complete(
            self, task: object, *, system: str, prompt: str, max_tokens: int
        ) -> SimpleNamespace:
            prompts.append(prompt)
            for handle in (f"interview:{analysis_only}", f"interview:{transcript}"):
                if f"- {handle}:" in prompt and handle not in self.read_handles:
                    self.read_handles.add(handle)
                    return SimpleNamespace(
                        text=json.dumps(
                            {
                                "action": "tool",
                                "update": "I am reading the linked interview evidence.",
                                "tool": "read_source",
                                "arguments": {"handle": handle},
                            }
                        ),
                        provider="fake",
                        model="fake",
                    )
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "action": "finish",
                        "update": "I compared the inspected interview evidence.",
                        "final": {
                            "reply": "The linked interviews support a careful comparison.",
                            "edits": [],
                            "visual_request": None,
                            "workspace_actions": [],
                        },
                    }
                ),
                provider="fake",
                model="fake",
            )

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    response = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={"message": "Compare how mentoring affected onboarding workload."},
    )
    assert response.status_code == 200, response.text
    assert "Participant Alpha" in prompts[-1]
    assert "Alpha found the onboarding guide useful." in prompts[-1]
    assert "Semi-structured guide: onboarding, workload and support." in prompts[-1]
    assert "Recorded duration: 01:30" in prompts[-1]
    assert "private alpha transcript" not in prompts[-1]
    assert "Participant Beta" in prompts[-1]
    assert "Weekly mentoring reduced my workload" in prompts[-1]
    evidence = response.json()["interview_context"]
    assert len(evidence) == 2
    assert evidence[0]["passages"] == []
    assert any(passage["speaker"] == "P1" for passage in evidence[1]["passages"])
    updated = client.put(
        f"/writer/{doc['public_id']}/interview-contexts",
        json={"sources": [{"interview_id": analysis_only, "mode": "analysis"}]},
    ).json()
    assert updated["linked_count"] == 1
    client.post(
        f"/writer/{doc['public_id']}/chat", json={"message": "Summarize the remaining interview."}
    )
    assert "Participant Beta" not in prompts[-1]


def test_writer_rejects_linking_voice_evidence_after_scope_revocation(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    org_id = _grant_scholar(client)
    interview_id = _interview(
        org_id,
        "Voice participant",
        "The participant relied on mentoring.",
        ["What helped?", "Mentoring helped."],
    )
    study_id = _attach_approved_spoken_scope(interview_id)
    with db_session() as session:
        study = session.get(VoiceStudyRow, study_id)
        assert study is not None
        information = dict(study.participant_information or {})
        information["dpia_public_scope_token"] = "b" * 32
        study.participant_information = information
    doc = client.post("/writer", json={"title": "Findings", "template": "blank"}).json()
    response = client.put(
        f"/writer/{doc['public_id']}/interview-contexts",
        json={"sources": [{"interview_id": interview_id, "mode": "analysis"}]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "interview_processing_scope_required"
    assert client.get(f"/writer/{doc['public_id']}/interview-contexts").json()["linked_count"] == 0


def test_writer_rechecks_voice_scope_immediately_before_provider_egress(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    org_id = _grant_scholar(client)
    interview_id = _interview(
        org_id,
        "Voice participant",
        "VOICE_EVIDENCE_MUST_NOT_EGRESS",
        ["What helped?", "Mentoring helped."],
    )
    study_id = _attach_approved_spoken_scope(interview_id)
    doc = client.post("/writer", json={"title": "Findings", "template": "blank"}).json()
    linked = client.put(
        f"/writer/{doc['public_id']}/interview-contexts",
        json={"sources": [{"interview_id": interview_id, "mode": "analysis"}]},
    )
    assert linked.status_code == 200, linked.text
    monkeypatch.setattr(api_app, "_build_pool", lambda _settings: _WriterScopePool())
    original_build_scoped_pool = api_app._build_scoped_pool
    scope_replaced = False

    def replace_scope_before_egress(*args, **kwargs):
        nonlocal scope_replaced
        if not scope_replaced:
            with db_session() as session:
                study = session.get(VoiceStudyRow, study_id)
                assert study is not None
                information = dict(study.participant_information or {})
                information["dpia_public_scope_token"] = "b" * 32
                study.participant_information = information
            scope_replaced = True
        return original_build_scoped_pool(*args, **kwargs)

    monkeypatch.setattr(api_app, "_build_scoped_pool", replace_scope_before_egress)
    monkeypatch.setattr(
        api_app,
        "run_assistant_turn",
        lambda *args, **kwargs: pytest.fail("stale voice evidence reached a provider"),
    )
    response = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={"message": "Summarize the linked interview evidence."},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "interview_processing_scope_required"


def test_writer_holds_voice_scope_fence_during_provider_call(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    org_id = _grant_scholar(client)
    interview_id = _interview(
        org_id,
        "Voice participant",
        "The participant relied on mentoring.",
        ["What helped?", "Mentoring helped."],
    )
    study_id = _attach_approved_spoken_scope(interview_id)
    doc = client.post("/writer", json={"title": "Findings", "template": "blank"}).json()
    linked = client.put(
        f"/writer/{doc['public_id']}/interview-contexts",
        json={"sources": [{"interview_id": interview_id, "mode": "analysis"}]},
    )
    assert linked.status_code == 200, linked.text
    write_ready = threading.Event()
    committed = threading.Event()
    provider_returned = threading.Event()
    mutation_errors: list[BaseException] = []
    mutation_threads: list[threading.Thread] = []

    def fake_writer_turn(*args, **kwargs):
        del args, kwargs
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
        return AssistantTurn(reply="Mentoring helped.")

    monkeypatch.setattr(api_app, "_build_pool", lambda _settings: _WriterScopePool())
    monkeypatch.setattr(api_app, "run_assistant_turn", fake_writer_turn)
    response = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={"message": "Summarize the linked interview evidence."},
    )
    for mutation in mutation_threads:
        mutation.join(timeout=3)
    assert provider_returned.is_set()
    assert response.status_code == 200, response.text
    assert committed.is_set()
    assert mutation_errors == []


def test_writer_holds_voice_scope_fence_during_result_review(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    org_id = _grant_scholar(client)
    interview_id = _interview(
        org_id,
        "Voice participant",
        "The participant relied on mentoring.",
        ["What helped?", "Mentoring helped."],
    )
    study_id = _attach_approved_spoken_scope(interview_id)
    doc = client.post("/writer", json={"title": "Findings", "template": "blank"}).json()
    linked = client.put(
        f"/writer/{doc['public_id']}/interview-contexts",
        json={"sources": [{"interview_id": interview_id, "mode": "analysis"}]},
    )
    assert linked.status_code == 200, linked.text
    write_ready = threading.Event()
    committed = threading.Event()
    review_returned = threading.Event()
    mutation_errors: list[BaseException] = []
    mutation_threads: list[threading.Thread] = []
    monkeypatch.setattr(api_app, "_build_pool", lambda _settings: _WriterScopePool())
    monkeypatch.setattr(
        api_app,
        "run_assistant_turn",
        lambda *args, **kwargs: AssistantTurn(
            reply="Mentoring helped.",
            edits=[
                {"path": "main.tex", "find": "unused", "replace": "unused", "applicable": False}
            ],
        ),
    )

    def fake_review(*args, **kwargs):
        del args, kwargs
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
        assert not committed.wait(0.2), "scope revocation crossed the result-review fence"
        review_returned.set()
        return SimpleNamespace(answer="Mentoring helped.", complete=True, summary="Verified.")

    monkeypatch.setattr(api_app, "review_execution_results", fake_review)
    response = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={"message": "Summarize the linked interview evidence."},
    )
    for mutation in mutation_threads:
        mutation.join(timeout=3)
    assert review_returned.is_set()
    assert response.status_code == 200, response.text
    assert committed.is_set()
    assert mutation_errors == []


def test_transcript_retrieval_preserves_cross_interview_coverage() -> None:
    interviews = [
        {
            "interview_id": "one",
            "title": "One",
            "mode": "transcript",
            "analysis": {},
            "speakers": {"S1": "P1"},
            "segments": [
                {
                    "idx": 1,
                    "speaker": "S1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "Mentoring reduced uncertainty.",
                }
            ],
        },
        {
            "interview_id": "two",
            "title": "Two",
            "mode": "transcript",
            "analysis": {},
            "speakers": {"S1": "P2"},
            "segments": [
                {
                    "idx": 1,
                    "speaker": "S1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "Mentoring increased confidence.",
                }
            ],
        },
    ]
    contexts, provenance = prepare_interview_evidence(
        interviews, query="Compare mentoring outcomes", max_passages=2
    )
    assert [len(context["passages"]) for context in contexts] == [1, 1]
    assert [item["passages"][0]["speaker"] for item in provenance] == ["P1", "P2"]


def test_latest_survey_correction_does_not_reuse_linked_interview(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app()
    client = _authed(app)
    org_id = _grant_scholar(client)
    interview_id = _interview(
        org_id,
        "Interview-only source",
        "INTERVIEW_ONLY_EVIDENCE_MARKER",
        [
            "What did the participant experience?",
            "The participant described the interview-only result.",
        ],
    )
    doc = client.post("/writer", json={"title": "Evidence correction", "template": "blank"}).json()
    linked = client.put(
        f"/writer/{doc['public_id']}/interview-contexts",
        json={"sources": [{"interview_id": interview_id, "mode": "analysis"}]},
    )
    assert linked.status_code == 200, linked.text
    prompts: list[str] = []

    class FakePool:
        def has_strong(self) -> bool:
            return True

        def pinned(self, ref: object) -> "FakePool":
            del ref
            return self

        def complete(
            self, task: object, *, system: str, prompt: str, max_tokens: int
        ) -> SimpleNamespace:
            del task, system, max_tokens
            prompts.append(prompt)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "reply": "I kept the requested evidence scope.",
                        "edits": [],
                        "visual_request": None,
                        "workspace_actions": [],
                    }
                ),
                provider="fake",
                model="fake",
            )

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    first = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={"message": "Schreib auf Basis des Interviews einen Ergebnisteil."},
    )
    assert first.status_code == 200, first.text
    assert any("INTERVIEW_ONLY_EVIDENCE_MARKER" in prompt for prompt in prompts)
    corrected = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={
            "message": "Meinte auf Basis der Survey sollst du das Paper schreiben, kein Interview erstellen."
        },
    )
    assert corrected.status_code == 200, corrected.text
    corrected_prompt = next(
        prompt
        for prompt in reversed(prompts)
        if "The user requested survey evidence, but no survey is linked" in prompt
    )
    assert "INTERVIEW_ONLY_EVIDENCE_MARKER" not in corrected_prompt
    assert "The user requested survey evidence, but no survey is linked" in corrected_prompt
    assert corrected.json()["interview_context"] == []
    assert corrected.json()["survey_context"] == []
    assert corrected.json()["workspace_actions"] == []
