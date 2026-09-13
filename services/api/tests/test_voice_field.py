"""Voice studies in the field: invites, consent, budgets, public sessions."""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from participant_fixtures import PARTICIPANT_INFORMATION
from sqlalchemy import select

from sixsentences_server.api import app as api_app
from sixsentences_server.api.app import create_app
from sixsentences_server.config import get_settings
from sixsentences_server.core import study_participation
from sixsentences_server.core.db import (
    CapacityReservationRow,
    CreditEventRow,
    InterviewRow,
    Org,
    ProviderCostRow,
    VoiceInviteRow,
    VoiceSessionRow,
    VoiceStudyRow,
    db_session,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus

_PARTICIPANT_NOTICE = "The Example University research team studies onboarding experiences and uses the interview transcript only for this documented research project."
_PARTICIPANT_CONTACT = "Questions: research@example.org"


def _authed(app: FastAPI, email: str = "owner@example.org", org: str = "Acme") -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert resp.status_code == 201, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return client


def _reapprove_study(client: TestClient, study_id: str) -> dict:
    approved = client.patch(
        f"/voice/studies/{study_id}", json={"participant_information": PARTICIPANT_INFORMATION}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["participant_information_ready"] is True
    return approved.json()


def _field_setup(monkeypatch: pytest.MonkeyPatch, **invite_body) -> tuple[TestClient, dict, dict]:
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    monkeypatch.setenv("SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "sixsentences_server.voice.service.mint_gemini_token",
        lambda *args, **kwargs: pytest.fail("a browser provider credential was minted"),
    )
    client = _authed(create_app())
    with db_session() as session:
        org = session.scalar(select(Org))
        assert org is not None
        org.plan = "community"
    study = client.post(
        "/voice/studies", json={"title": "Onboarding-Studie", "language": "de"}
    ).json()
    prepared = client.patch(
        f"/voice/studies/{study['id']}",
        json={
            "consent_text": _PARTICIPANT_NOTICE,
            "contact_line": _PARTICIPANT_CONTACT,
            "participant_information": PARTICIPANT_INFORMATION,
        },
    )
    assert prepared.status_code == 200, prepared.text
    _reapprove_study(client, study["id"])
    invite = client.post(f"/voice/studies/{study['id']}/invites", json=invite_body).json()
    assert len(invite["id"]) == 26
    return (client, study, invite)


def _legacy_credential_session(public_id: str) -> None:
    """Model a historical issued credential for legacy settlement regression."""
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow).where(VoiceSessionRow.public_id == public_id))
        assert live is not None
        live.transport = "legacy"
        live.provider_token_issues = 1
        live.relay_ticket_hash = ""
        live.relay_ticket_expires_at = None
        live.relay_state = {}


_TURNS = [
    {
        "role": "interviewer",
        "text": "Hallo, ich bin eine KI. Wie war der Start?",
        "start_ms": 0,
        "end_ms": 5000,
    },
    {
        "role": "participant",
        "text": "Gut, die Mentorin hat den Unterschied gemacht.",
        "start_ms": 5000,
        "end_ms": 12000,
    },
]


def _talk_start_body(public: TestClient, invite_id: str, **overrides: object) -> dict[str, object]:
    """Bind consent to the exact public surface shown before start."""
    info = public.get(f"/public/talk/{invite_id}").json()
    return {
        "consent": True,
        "age_confirmed": True,
        "audio_consent": True,
        "consent_fingerprint": info["consent_fingerprint"],
        **overrides,
    }


def test_public_info_shows_surface_never_the_guide(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch, passcode="geheim")
    changed = client.patch(
        f"/voice/studies/{study['id']}",
        json={
            "consent_text": "Die Example University untersucht Erfahrungen beim Onboarding und verwendet das Transkript ausschließlich für dieses Forschungsprojekt.",
            "contact_line": "Fragen an study-contact@example.org",
        },
    )
    assert changed.status_code == 200
    _reapprove_study(client, study["id"])
    public = TestClient(create_app())
    info = public.get(f"/public/talk/{invite['id']}").json()
    assert info["state"] == "open"
    assert info["title"] == "Onboarding-Studie"
    assert info["expected_minutes"] == 60
    assert info["minimum_age"] == 18
    assert info["passcode_required"] is True
    assert "passcode" not in info
    assert "Example University" in info["consent_text"]
    assert info["contact_line"] == "Fragen an study-contact@example.org"
    assert info["live_available"] is True
    assert info["available_modes"] == ["text", "live"]
    assert info["live_provider"] == "Google Gemini"
    assert info["provider_disclosure_version"] == "2026-09-04"
    assert info["privacy_notice_url"] == "http://localhost:3000/privacy"
    assert info["terms_url"] == "http://localhost:3000/terms"
    assert len(info["consent_fingerprint"]) == 64
    assert len(info["study_scope_revision"]) == 32
    assert "study_scope_fingerprint" not in info
    assert "guide" not in info
    assert "must_cover" not in str(info) and "probes" not in str(info)
    assert invite["passcode"] == ""
    with db_session() as session:
        stored = session.scalar(
            select(VoiceInviteRow).where(VoiceInviteRow.public_id == invite["id"])
        )
        assert stored is not None
        assert stored.passcode.startswith("pbkdf2_sha256$")
    client.patch(f"/voice/invites/{invite['id']}", json={"active": False})
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "inactive"
    assert public.get("/public/talk/nope").status_code == 404
    longer_duration = client.patch(
        f"/voice/studies/{study['id']}", json={"max_session_minutes": 31}
    )
    assert longer_duration.status_code == 200
    assert longer_duration.json()["participant_information_ready"] is False


def test_public_spoken_mode_is_fail_closed_with_written_fallback(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _study, invite = _field_setup(monkeypatch, max_sessions=2)
    monkeypatch.setenv("SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED", "0")
    get_settings.cache_clear()
    public = TestClient(create_app())
    info = public.get(f"/public/talk/{invite['id']}").json()
    assert info["state"] == "open"
    assert info["live_available"] is False
    assert info["available_modes"] == ["text"]
    spoken = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="live"),
    )
    assert spoken.status_code == 409
    assert spoken.json()["detail"] == {
        "code": "voice_interview_spoken_mode_unavailable",
        "message": "Das gesprochene Interview ist derzeit nicht verfügbar. Bitte nehmen Sie stattdessen schriftlich teil.",
    }
    assert "Gemini" not in spoken.text
    with db_session() as session:
        assert session.scalar(select(VoiceSessionRow)) is None
        assert session.scalar(select(CapacityReservationRow)) is None
    written = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="text"),
    )
    assert written.status_code == 202
    assert written.json()["mode"] == "text"
    assert "token" not in written.json()


def test_material_study_change_invalidates_dpia_approval_and_closes_link(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "open"
    changed = client.patch(
        f"/voice/studies/{study['id']}",
        json={
            "sections": [
                {
                    "title": "Changed assessed scope",
                    "question": "What materially changed?",
                    "probes": [],
                    "must_cover": True,
                }
            ]
        },
    )
    assert changed.status_code == 200
    assert changed.json()["participant_information_ready"] is False
    assert (
        "completed data protection impact assessment"
        in changed.json()["participant_information_gaps"]
    )
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "unavailable"
    reapproved = client.patch(
        f"/voice/studies/{study['id']}", json={"participant_information": PARTICIPANT_INFORMATION}
    )
    assert reapproved.status_code == 200
    assert reapproved.json()["participant_information_ready"] is True
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "open"


def test_participant_information_cannot_rebind_approval_across_scope_change(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    detail = client.get(f"/voice/studies/{study['id']}").json()
    approved_information = dict(detail["participant_information"])
    previous_scope_token = approved_information["dpia_public_scope_token"]
    changed_information = {
        **approved_information,
        "purpose": "Evaluate a materially different research purpose.",
    }
    changed = client.patch(
        f"/voice/studies/{study['id']}", json={"participant_information": changed_information}
    )
    assert changed.status_code == 200
    changed_payload = changed.json()
    assert changed_payload["participant_information_ready"] is False
    assert changed_payload["participant_information"]["dpia_status"] == ""
    assert changed_payload["participant_information"]["dpia_reference"] == ""
    assert changed_payload["participant_information"]["ai_interview_scope_attested"] is False
    assert changed_payload["participant_information"]["spoken_processing_approved"] is False
    assert changed_payload["participant_information"]["researcher_reviewed"] is False
    assert changed_payload["participant_information"]["dpia_scope_fingerprint"] == ""
    assert changed_payload["participant_information"]["dpia_public_scope_token"] == ""
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "unavailable"
    fresh_approval = {
        **changed_payload["participant_information"],
        "dpia_status": PARTICIPANT_INFORMATION["dpia_status"],
        "dpia_reference": PARTICIPANT_INFORMATION["dpia_reference"],
        "ai_interview_scope_attested": True,
        "researcher_reviewed": True,
    }
    reapproved = client.patch(
        f"/voice/studies/{study['id']}", json={"participant_information": fresh_approval}
    )
    assert reapproved.status_code == 200
    assert reapproved.json()["participant_information_ready"] is True
    assert reapproved.json()["participant_information"]["dpia_public_scope_token"]
    assert (
        reapproved.json()["participant_information"]["dpia_public_scope_token"]
        != previous_scope_token
    )
    combined = client.patch(
        f"/voice/studies/{study['id']}",
        json={
            "participant_information": reapproved.json()["participant_information"],
            "sections": [
                {
                    "title": "Changed in the approval request",
                    "question": "What changed in this material scope?",
                    "probes": [],
                    "must_cover": True,
                }
            ],
        },
    )
    assert combined.status_code == 200
    assert combined.json()["participant_information_ready"] is False
    assert combined.json()["participant_information"]["dpia_scope_fingerprint"] == ""
    assert combined.json()["participant_information"]["dpia_public_scope_token"] == ""


def test_platform_policy_revision_invalidates_existing_dpia_approval(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "open"
    start_body = _talk_start_body(public, invite["id"])
    monkeypatch.setattr(
        study_participation, "VOICE_DPIA_SCOPE_VERSION", "future-material-policy-revision"
    )
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "unavailable"
    attempt = public.post(f"/public/talk/{invite['id']}/sessions", json=start_body)
    assert attempt.status_code == 409


def test_audio_consent_is_separate_and_exact_notice_is_preserved(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    shown = public.get(f"/public/talk/{invite['id']}").json()
    missing = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], audio_consent=False),
    )
    assert missing.status_code == 422
    result = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    )
    assert result.status_code == 202, result.text
    with db_session() as session:
        row = session.scalar(select(VoiceSessionRow))
        assert row is not None
        receipt = deepcopy(row.consent_snapshot)
        assert receipt["surface"]["participant_notices"] == shown["participant_notices"]
        assert receipt["fingerprint"] == shown["consent_fingerprint"]
        assert receipt["consent_given"] is True
        assert receipt["audio_consent_given"] is True
        assert receipt["age_confirmed"] is True
        assert receipt["mode"] == "live"
    monkeypatch.setattr(study_participation, "PARTICIPANT_INFORMATION_VERSION", "2099-01-01.test")
    refreshed = public.get(f"/public/talk/{invite['id']}").json()
    assert refreshed["consent_fingerprint"] != shown["consent_fingerprint"]
    assert refreshed["live_available"] is False
    assert "live" not in refreshed["participant_notices"]
    assert refreshed["participant_notices"]["text"]["version"] == "2099-01-01.test"
    with db_session() as session:
        row = session.scalar(select(VoiceSessionRow))
        assert row is not None and row.consent_snapshot == receipt


def test_text_session_cannot_upload_a_recording(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    changed = client.patch(f"/voice/studies/{study['id']}", json={"retention": "keep"})
    assert changed.status_code == 200
    _reapprove_study(client, study["id"])
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="text"),
    ).json()
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/finalize",
        json={
            "duration_ms": 10000,
            "audio_base64": base64.b64encode(b"unexpected audio").decode(),
            "turns": [
                {"role": "participant", "text": "Written response.", "start_ms": 0, "end_ms": 10000}
            ],
        },
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == "audio recording is not authorized for this session"
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.status == "running"
        assert session.scalar(select(InterviewRow)) is None


def test_public_live_recording_requires_recording_consent(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    changed = client.patch(f"/voice/studies/{study['id']}", json={"retention": "keep"})
    assert changed.status_code == 200
    _reapprove_study(client, study["id"])
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="live"),
    ).json()
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        live.consent_snapshot = {**dict(live.consent_snapshot or {}), "audio_consent_given": False}
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/finalize",
        json={"audio_base64": base64.b64encode(b"unexpected audio").decode()},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == "audio recording is not authorized for this session"
    with db_session() as session:
        assert session.scalar(select(InterviewRow)) is None


def test_public_live_recording_requires_current_spoken_scope_approval(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    changed = client.patch(f"/voice/studies/{study['id']}", json={"retention": "keep"})
    assert changed.status_code == 200
    _reapprove_study(client, study["id"])
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="live"),
    ).json()
    monkeypatch.setattr(
        study_participation, "VOICE_DPIA_SCOPE_VERSION", "future-spoken-recording-policy"
    )
    monkeypatch.setattr(
        "sixsentences_server.api.app.base64.b64decode",
        lambda *args, **kwargs: pytest.fail("stale-scope audio was decoded"),
    )
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/finalize",
        json={"audio_base64": "not-even-base64"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == "audio recording is not authorized for this session"
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.status == "running"
        assert session.scalar(select(InterviewRow)) is None


def test_policy_bump_preserves_relay_transcript_without_new_analysis_egress(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="live"),
    ).json()
    now = datetime.now(UTC)
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        live.relay_claimed_at = now
        live.relay_closed_at = now
        live.relay_state = {
            "turns": [
                {
                    "role": "interviewer",
                    "text": "How did onboarding feel?",
                    "start_ms": 0,
                    "end_ms": 4000,
                },
                {
                    "role": "participant",
                    "text": "The written guide helped.",
                    "start_ms": 4000,
                    "end_ms": 10000,
                },
            ],
            "duration_ms": 10000,
            "observed_cost_usd": 0.01,
            "customer_observed_cost_usd": 0.01,
            "accounted_cost_usd": 0.01,
            "turns_started": 2,
            "input_audio_ms": 6000,
            "provider_connections": 1,
            "meter_complete": True,
        }
    monkeypatch.setattr(study_participation, "VOICE_DPIA_SCOPE_VERSION", "future-analysis-policy")
    queued: list[str] = []

    def record_job(_background, function, *_args, **_kwargs):
        queued.append(function.__name__)

    monkeypatch.setattr("sixsentences_server.api.app.enqueue_job", record_job)
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda *_args, **_kwargs: pytest.fail("stale-scope transcript reached a provider"),
    )
    finalized = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/finalize", json={}
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "completed"
    assert "_execute_interview_analysis" not in queued
    with db_session() as session:
        interview = session.scalar(select(InterviewRow))
        assert interview is not None
        assert (
            session.scalar(
                select(CapacityReservationRow).where(
                    CapacityReservationRow.action == "interview_analysis"
                )
            )
            is None
        )
        assert [stage["status"] for stage in interview.config["pipeline"]][-1] == "failed"
        assert interview.config["segment_count"] == 2
        interview_id = interview.public_id
    retry = client.post(f"/interviews/{interview_id}/analyze", json={})
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "interview_analysis_scope_required"


def test_transcript_only_session_rejects_audio_before_decoding(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="live"),
    ).json()
    monkeypatch.setattr(
        "sixsentences_server.api.app.base64.b64decode",
        lambda *args, **kwargs: pytest.fail("unauthorized audio was decoded"),
    )
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/finalize",
        json={"audio_base64": "not-even-base64"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == "audio recording is not authorized for this session"
    with db_session() as session:
        assert session.scalar(select(InterviewRow)) is None


@pytest.mark.parametrize(
    "change", ["recording_retention", "interview_guide", "provider_notice_version"]
)
def test_public_start_rejects_stale_consent_surface(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    shown = public.get(f"/public/talk/{invite['id']}").json()
    if change == "provider_notice_version":
        monkeypatch.setattr(
            study_participation, "PARTICIPANT_INFORMATION_VERSION", "2099-01-01.test"
        )
    else:
        scope_change = (
            {
                "sections": [
                    {
                        "title": "Revised guide",
                        "question": "What changed since the previous consent screen?",
                        "probes": [],
                        "must_cover": True,
                    }
                ]
            }
            if change == "interview_guide"
            else {"retention": "keep"}
        )
        changed = client.patch(f"/voice/studies/{study['id']}", json=scope_change)
        assert changed.status_code == 200
        assert changed.json()["participant_information_ready"] is False
        reapproved = client.patch(
            f"/voice/studies/{study['id']}",
            json={"participant_information": PARTICIPANT_INFORMATION},
        )
        assert reapproved.status_code == 200
        assert reapproved.json()["participant_information_ready"] is True
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json={
            "consent": True,
            "age_confirmed": True,
            "consent_fingerprint": shown["consent_fingerprint"],
        },
    )
    assert refused.status_code == 409
    assert "consent information changed" in refused.json()["detail"]
    with db_session() as session:
        assert session.scalar(select(VoiceSessionRow)) is None
        assert session.scalar(select(CapacityReservationRow)) is None


def test_public_start_rejects_consent_from_a_different_mode_surface(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    spoken_surface = public.get(f"/public/talk/{invite['id']}").json()
    assert spoken_surface["available_modes"] == ["text", "live"]
    monkeypatch.setenv("SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED", "0")
    get_settings.cache_clear()
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json={
            "consent": True,
            "age_confirmed": True,
            "consent_fingerprint": spoken_surface["consent_fingerprint"],
            "mode": "text",
        },
    )
    assert refused.status_code == 409
    assert "consent information changed" in refused.json()["detail"]
    with db_session() as session:
        assert session.scalar(select(VoiceSessionRow)) is None
        assert session.scalar(select(CapacityReservationRow)) is None


def test_public_start_requires_adult_confirmation_server_side(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], age_confirmed=False),
    )
    assert refused.status_code == 422
    assert (
        refused.json()["detail"] == "Bitte bestätigen Sie, dass Sie mindestens 18 Jahre alt sind."
    )
    with db_session() as session:
        assert session.scalar(select(VoiceSessionRow)) is None
        assert session.scalar(select(CapacityReservationRow)) is None


def test_public_live_ticket_does_not_mint_a_browser_provider_credential(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    )
    assert started.status_code == 202
    payload = started.json()
    assert payload["transport"] == "relay"
    assert payload["token"].startswith("six_vr_")
    assert payload["ws_url"] == f"/voice/sessions/{payload['id']}/relay"
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        assert live is not None and live.provider_token_issues == 0
        assert live.relay_ticket_hash and live.relay_ticket_hash != payload["token"]
        assert live.relay_claimed_at is None
        assert reservation is not None and reservation.status == "active"


def test_running_or_settling_session_freezes_study_definition(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    ).json()
    blocked_patch = client.patch(
        f"/voice/studies/{study['id']}", json={"title": "Changed while active"}
    )
    assert blocked_patch.status_code == 409
    assert client.delete(f"/voice/studies/{study['id']}").status_code == 409
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow).where(VoiceSessionRow.public_id == started["id"])
        )
        assert live is not None
        live.status = "settling"
    settling_patch = client.patch(
        f"/voice/studies/{study['id']}", json={"retention": "transcript_only"}
    )
    assert settling_patch.status_code == 409
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow).where(VoiceSessionRow.public_id == started["id"])
        )
        assert live is not None
        live.status = "running"
    finalized = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/finalize",
        json={"aborted": True, "duration_ms": 1000, "turns": []},
    )
    assert finalized.status_code == 200
    allowed = client.patch(f"/voice/studies/{study['id']}", json={"retention": "transcript_only"})
    assert allowed.status_code == 200


def test_public_live_start_rolls_back_when_capacity_is_below_30_minutes(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _study, invite = _field_setup(monkeypatch, max_sessions=1)
    from sixsentences_server.core.entitlements import begin_ai_action as real_begin_ai_action

    def begin_with_short_ceiling(*args, **kwargs):
        action = real_begin_ai_action(*args, **kwargs)
        action.cost_limit_usd = 3.0
        return action

    monkeypatch.setattr("sixsentences_server.api.app.begin_ai_action", begin_with_short_ceiling)
    monkeypatch.setattr(
        "sixsentences_server.voice.service.mint_gemini_token",
        lambda *args, **kwargs: pytest.fail("a short provider session was minted"),
    )
    public = TestClient(create_app())
    refused = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "voice_interview_capacity_unavailable"
    assert "mindestens 30 Minuten" in detail["message"]
    assert "cost_limit" not in refused.text
    with db_session() as session:
        assert session.scalar(select(VoiceSessionRow)) is None
        assert session.scalar(select(CapacityReservationRow)) is None


def test_concurrent_live_reissue_never_mints_replacement_credentials(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _study, invite = _field_setup(monkeypatch, max_sessions=1)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    ).json()
    monkeypatch.setattr(
        "sixsentences_server.voice.service.mint_gemini_token",
        lambda *args, **kwargs: pytest.fail("a replacement credential was minted"),
    )

    def reissue() -> int:
        racing_client = TestClient(create_app())
        return racing_client.post(
            f"/public/talk/{invite['id']}/sessions/{started['id']}/reissue", json={"turns": []}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: reissue(), range(2)))
    assert statuses == [409, 409]
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow).where(VoiceSessionRow.public_id == started["id"])
        )
        assert live is not None and live.provider_token_issues == 0
        assert live.transport == "relay"


def test_legacy_early_live_abort_cannot_reuse_invite_or_budget(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch, max_sessions=1)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    ).json()
    _legacy_credential_session(started["id"])
    ended = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/finalize",
        json={"duration_ms": 0, "turns": [], "aborted": True},
    )
    assert ended.json() == {"status": "aborted"}
    blocked = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    )
    assert blocked.status_code == 409
    assert "full" in blocked.json()["detail"]
    detail = client.get(f"/voice/studies/{study['id']}").json()
    assert detail["used_minutes"] == 1
    assert detail["invites"][0]["used_sessions"] == 1
    with db_session() as session:
        event = session.scalar(select(CreditEventRow))
        provider_cost = session.scalar(select(ProviderCostRow))
        assert event is not None and event.credits == 250
        assert provider_cost is not None and provider_cost.cost_usd == pytest.approx(9.0)


def test_text_turn_rechecks_revocation_immediately_before_provider_egress(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _study, invite = _field_setup(monkeypatch, max_sessions=2)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="text"),
    ).json()
    render_context = api_app.render_conversation_context

    def revoke_between_claim_and_egress(*args, **kwargs):
        with db_session() as session:
            stored_invite = session.scalar(select(VoiceInviteRow))
            assert stored_invite is not None
            stored_invite.active = False
        return render_context(*args, **kwargs)

    monkeypatch.setattr(api_app, "render_conversation_context", revoke_between_claim_and_egress)
    monkeypatch.setattr(
        api_app,
        "_build_scoped_pool",
        lambda *args, **kwargs: pytest.fail("revoked text reached a provider"),
    )
    blocked = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/messages",
        json={"message": "Do not send this.", "history": []},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "this session is no longer authorized (inactive)"
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.text_turns_claimed == 0


def test_text_session_cannot_inherit_a_new_out_of_band_scope_approval(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old consent receipt cannot authorize a replacement study scope."""
    _client, _study, invite = _field_setup(monkeypatch, max_sessions=2)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="text"),
    ).json()
    with db_session() as session:
        study = session.scalar(select(VoiceStudyRow))
        assert study is not None
        study.guide = {
            "sections": [{"title": "Replacement scope", "questions": ["A new question?"]}]
        }
        information = dict(study.participant_information or {})
        information["dpia_scope_fingerprint"] = study_participation.voice_study_scope_fingerprint(
            study
        )
        information["dpia_public_scope_token"] = "f" * 32
        study.participant_information = information
        assert study_participation.voice_study_participation_ready(study)
    monkeypatch.setattr(
        api_app,
        "_build_scoped_pool",
        lambda *args, **kwargs: pytest.fail("old consent reached a provider"),
    )
    blocked = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/messages",
        json={"message": "Do not send this.", "history": []},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "this session is no longer authorized (unavailable)"
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.text_turns_claimed == 0


def test_legacy_short_limits_are_repaired_before_field_start(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch, max_sessions=2)
    with db_session() as session:
        row = session.scalar(select(VoiceStudyRow).where(VoiceStudyRow.public_id == study["id"]))
        assert row is not None
        row.max_session_minutes = 20
        row.budget_minutes = 5
    public = TestClient(create_app())
    info = public.get(f"/public/talk/{invite['id']}").json()
    assert info["state"] == "unavailable"
    assert info["expected_minutes"] == 30
    with db_session() as session:
        row = session.scalar(select(VoiceStudyRow))
        assert row is not None
        assert row.max_session_minutes == 30
        assert row.budget_minutes == 30
    _reapprove_study(client, study["id"])
    assert public.get(f"/public/talk/{invite['id']}").json()["state"] == "open"
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="text"),
    )
    assert started.status_code == 202
    assert started.json()["max_session_minutes"] == 30
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        assert live is not None and live.authorized_duration_ms == 30 * 60000
        assert reservation is not None and reservation.status == "active"


def test_failed_text_provider_call_releases_its_turn_claim(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _client, _study, invite = _field_setup(monkeypatch, max_sessions=1)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions",
        json=_talk_start_body(public, invite["id"], mode="text"),
    ).json()
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: None)
    with caplog.at_level("WARNING"):
        failed = public.post(
            f"/public/talk/{invite['id']}/sessions/{started['id']}/messages",
            json={"message": "Hallo", "history": []},
        )
    assert failed.status_code == 503
    assert (
        failed.json()["detail"]
        == "Die Interviewerin ist gerade nicht erreichbar. Bitte versuchen Sie es gleich noch einmal."
    )
    assert "no model provider configured" not in failed.text
    assert "no model provider configured" in caplog.text
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow).where(VoiceSessionRow.public_id == started["id"])
        )
        assert live is not None and live.text_turns_claimed == 0

    class FakePool:
        clients = {"fake": object()}

        def pinned(self, ref):
            return self

        def complete(self, task, *, system, prompt, max_tokens=2048, ref=None):

            class Result:
                text = "Interviewerin: Was ist konkret passiert?"

            return Result()

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    retried = public.post(
        f"/public/talk/{invite['id']}/sessions/{started['id']}/messages",
        json={"message": "Hallo", "history": []},
    )
    assert retried.status_code == 200
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow).where(VoiceSessionRow.public_id == started["id"])
        )
        assert live is not None and live.text_turns_claimed == 1


def test_legacy_stale_billable_field_session_counts_against_study_budget(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, study, invite = _field_setup(monkeypatch, max_sessions=2)
    public = TestClient(create_app())
    started = public.post(
        f"/public/talk/{invite['id']}/sessions", json=_talk_start_body(public, invite["id"])
    ).json()
    _legacy_credential_session(started["id"])
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow).where(VoiceSessionRow.public_id == started["id"])
        )
        assert live is not None
        live.started_at = live.started_at - timedelta(minutes=91)
    detail = client.get(f"/voice/studies/{study['id']}").json()
    assert detail["sessions"][0]["status"] == "aborted"
    assert detail["sessions"][0]["capacity_percent"] == 0.0
    assert detail["used_minutes"] == 60
    assert detail["invites"][0]["used_sessions"] == 1
    with db_session() as session:
        event = session.scalar(select(CreditEventRow))
        assert event is not None and event.credits == 60 * 250
