"""Lifecycle, tenancy and idempotency tests for Desktop Live Companion."""

from __future__ import annotations

import base64
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import sixsentences_server.api.app as appmod
import sixsentences_server.interviews.live as livemod
import sixsentences_server.jobs as jobs
from sixsentences_server.api.app import _privacy_export_tables, create_app
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.auth import add_member, authenticate, create_api_key
from sixsentences_server.core.db import (
    AuthToken,
    BackgroundJobRow,
    CapacityReservationRow,
    CreditEventRow,
    InterviewRow,
    InterviewSegmentRow,
    LiveCompanionAskRow,
    LiveCompanionSegmentRow,
    LiveCompanionSessionRow,
    Org,
    Run,
    RunEvent,
    User,
    db_session,
)
from sixsentences_server.core.entitlements import (
    EntitlementError,
    plan_for_org,
    question_cost,
    question_settlement_cost,
)
from sixsentences_server.interviews.analysis import InterviewAgentTurn

PASSWORD = "StrongPass123!"
PAIR_VERIFIER = "v" * 43
PAIR_CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(PAIR_VERIFIER.encode()).digest()).decode().rstrip("=")
)
PAIR_STATE = "desktop-state-00000001"


def _register(client: TestClient, email: str = "owner@example.org") -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "org_name": f"Workspace {email}",
            "name": "Owner",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def _scholar(client: TestClient) -> None:
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"


def _client(settings: Settings) -> tuple[TestClient, str]:
    del settings
    client = TestClient(create_app())
    token = _register(client)
    client.headers["Authorization"] = f"Bearer {token}"
    return (client, token)


def _create_session(
    client: TestClient,
    *,
    client_session_id: str = "desktop-session-0001",
    title: str = "Research meeting",
    project_id: int | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": client_session_id,
            "title": title,
            "project_id": project_id,
            "language": "en",
            "consent": {
                "participants_notified": True,
                "notice_text": "Participants agreed to live transcription.",
            },
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["web_url"].endswith(f"/interviews?tab=live&session={payload['id']}")
    return payload


def _append(
    client: TestClient,
    session_id: str,
    *,
    event_id: str = "segment-0001",
    start_ms: int = 0,
    end_ms: int = 1000,
    text: str = "Terraform manages infrastructure as code.",
    channel: str = "microphone",
) -> Any:
    return client.post(
        f"/interviews/live/sessions/{session_id}/segments",
        json={
            "segments": [
                {
                    "client_event_id": event_id,
                    "channel": channel,
                    "speaker": "Alex",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                    "is_final": True,
                }
            ]
        },
    )


def _pair(client: TestClient, *, device_name: str | None = None) -> Any:
    body: dict[str, Any] = {"code_challenge": PAIR_CHALLENGE, "state": PAIR_STATE}
    if device_name is not None:
        body["device_name"] = device_name
    return client.post("/interviews/live/pair", json=body)


def test_companion_account_identity_is_stable_across_keys_and_isolates_users(
    settings: Settings,
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.org"))
        assert owner is not None
        member = add_member(
            session, owner.org_id, "identity-member@example.org", PASSWORD, "member"
        )
        owner_keys = [
            create_api_key(
                session,
                owner,
                f"Live Companion · Owner {index}",
                scopes=("companion:read", "companion:write"),
            )
            for index in range(2)
        ]
        member_key = create_api_key(
            session, member, "Live Companion · Member", scopes=("companion:read", "companion:write")
        )

    def account_id(api_key: str) -> str:
        companion = TestClient(client.app)
        companion.headers["Authorization"] = f"Bearer {api_key}"
        response = companion.get("/interviews/live/config")
        assert response.status_code == 200, response.text
        assert "@example.org" not in response.text
        return str(response.json()["account_id"])

    owner_ids = [account_id(api_key) for api_key in owner_keys]
    assert owner_ids[0] == owner_ids[1]
    assert account_id(member_key) != owner_ids[0]


def test_pair_exchange_is_atomic_under_concurrency(settings: Settings) -> None:
    client, _token = _client(settings)
    _scholar(client)
    code = _pair(client).json()["code"]

    def exchange() -> int:
        return (
            TestClient(client.app)
            .post(
                "/interviews/live/pair/exchange",
                json={"code": code, "code_verifier": PAIR_VERIFIER},
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _index: exchange(), range(2)))
    assert statuses == [200, 401]


def test_connected_devices_are_named_private_filtered_and_revocable(settings: Settings) -> None:
    client, _token = _client(settings)
    _scholar(client)
    pair = _pair(client, device_name="  Alex\u202e\nMacBook   Pro  ")
    assert pair.status_code == 200, pair.text
    exchange = TestClient(client.app).post(
        "/interviews/live/pair/exchange",
        json={"code": pair.json()["code"], "code_verifier": PAIR_VERIFIER},
    )
    assert exchange.status_code == 200, exchange.text
    raw_companion_key = exchange.json()["api_key"]
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.org"))
        assert owner is not None
        create_api_key(session, owner, "Generic automation key", scopes=("research:read",))
        add_member(session, owner.org_id, "device-member@example.org", PASSWORD, "member")
        member_token = authenticate(session, "device-member@example.org", PASSWORD)
        member = session.scalar(select(User).where(User.email == "device-member@example.org"))
        assert member is not None
        create_api_key(
            session,
            member,
            "Live Companion · Member laptop",
            scopes=("companion:read", "companion:write"),
        )
    devices = client.get("/interviews/live/devices")
    assert devices.status_code == 200, devices.text
    assert len(devices.json()["devices"]) == 1
    device = devices.json()["devices"][0]
    assert device["name"] == "Live Companion · Alex MacBook Pro"
    assert device["prefix"].startswith("six_sk_")
    assert raw_companion_key not in devices.text
    assert set(device) == {"id", "name", "prefix", "created_at", "last_used_at", "expires_at"}
    companion = TestClient(client.app)
    companion.headers["Authorization"] = f"Bearer {raw_companion_key}"
    assert companion.get("/interviews/live/devices").status_code == 403
    assert companion.delete(f"/interviews/live/devices/{device['id']}").status_code == 403
    member_client = TestClient(client.app)
    member_client.headers["Authorization"] = f"Bearer {member_token}"
    assert member_client.delete(f"/interviews/live/devices/{device['id']}").status_code == 404
    assert (
        member_client.get("/interviews/live/devices").json()["devices"][0]["name"]
        == "Live Companion · Member laptop"
    )
    revoked = client.delete(f"/interviews/live/devices/{device['id']}")
    assert revoked.status_code == 200
    assert revoked.json() == {"id": device["id"], "revoked": True}
    assert client.get("/interviews/live/devices").json() == {"devices": []}
    assert client.delete(f"/interviews/live/devices/{device['id']}").status_code == 404
    assert companion.get("/interviews/live/config").status_code == 401
    with db_session() as session:
        revoked_row = session.get(AuthToken, device["id"])
        assert revoked_row is not None
        assert revoked_row.token_hash != raw_companion_key


def test_create_serializes_active_slot_and_reaps_stale_session(settings: Settings) -> None:
    client, token = _client(settings)
    _scholar(client)

    def create(index: int) -> Any:
        worker = TestClient(client.app)
        worker.headers["Authorization"] = f"Bearer {token}"
        return worker.post(
            "/interviews/live/sessions",
            json={
                "client_session_id": f"concurrent-session-{index:04d}",
                "consent": {
                    "participants_notified": True,
                    "notice_text": "Every participant was notified.",
                },
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(create, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    active_id = next(response.json()["id"] for response in responses if response.status_code == 201)
    with db_session() as session:
        active = session.scalar(
            select(LiveCompanionSessionRow).where(LiveCompanionSessionRow.public_id == active_id)
        )
        assert active is not None
        active.started_at = datetime.now(UTC) - timedelta(
            milliseconds=active.max_duration_ms, minutes=6
        )
    replacement = _create_session(client, client_session_id="replacement-session-0001")
    assert replacement["status"] == "recording"
    with db_session() as session:
        stale = session.scalar(
            select(LiveCompanionSessionRow).where(LiveCompanionSessionRow.public_id == active_id)
        )
        assert stale is not None
        assert stale.status == "failed"
        assert "maximum duration" in stale.failure_reason


def test_session_segment_event_complete_and_cleanup_lifecycle(settings: Settings) -> None:
    client, _token = _client(settings)
    _scholar(client)
    refused = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": "desktop-refused-0001",
            "consent": {"participants_notified": False, "notice_text": "Not yet."},
        },
    )
    assert refused.status_code == 422
    created = _create_session(client)
    session_id = created["id"]
    assert created["status"] == "recording"
    assert created["max_duration_ms"] == 180 * 60000
    duplicate = _create_session(client)
    assert duplicate["id"] == session_id
    conflict = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": "desktop-session-0001",
            "title": "Different payload",
            "language": "en",
            "consent": {
                "participants_notified": True,
                "notice_text": "Participants agreed to live transcription.",
            },
        },
    )
    assert conflict.status_code == 409
    assert (
        client.post(
            "/interviews/live/sessions",
            json={
                "client_session_id": "desktop-session-0002",
                "consent": {"participants_notified": True, "notice_text": "All notified."},
            },
        ).status_code
        == 409
    )
    first = _append(client, session_id)
    assert first.status_code == 200, first.text
    assert first.json()["accepted"] == 1
    oversized_text = _append(
        client,
        session_id,
        event_id="segment-oversized",
        start_ms=1000,
        end_ms=2000,
        text="x" * 4001,
    )
    assert oversized_text.status_code == 422
    oversized_batch = client.post(
        f"/interviews/live/sessions/{session_id}/segments",
        json={
            "segments": [
                {
                    "client_event_id": f"batch-segment-{index:04d}",
                    "channel": "system",
                    "speaker": "Participant",
                    "start_ms": index * 10,
                    "end_ms": index * 10 + 10,
                    "text": "Bounded text",
                    "is_final": True,
                }
                for index in range(101)
            ]
        },
    )
    assert oversized_batch.status_code == 422
    repeated = _append(client, session_id)
    assert repeated.json()["accepted"] == 0
    assert repeated.json()["duplicates"] == 1
    changed = _append(client, session_id, text="Different text")
    assert changed.status_code == 409
    overlap = _append(client, session_id, event_id="segment-0002", start_ms=500, end_ms=1500)
    assert overlap.status_code == 409
    too_long = _append(
        client,
        session_id,
        event_id="segment-too-late",
        start_ms=created["max_duration_ms"],
        end_ms=created["max_duration_ms"] + 1,
    )
    assert too_long.status_code == 413
    events = client.get(f"/interviews/live/sessions/{session_id}/events?after=0&limit=10")
    assert events.status_code == 200
    assert [item["type"] for item in events.json()["events"]] == ["session", "segment"]
    cursor = events.json()["cursor"]
    assert (
        client.get(f"/interviews/live/sessions/{session_id}/events?after={cursor}").json()["events"]
        == []
    )
    completed = client.post(f"/interviews/live/sessions/{session_id}/complete")
    assert completed.status_code == 200, completed.text
    interview_id = completed.json()["interview_id"]
    repeated_complete = client.post(f"/interviews/live/sessions/{session_id}/complete")
    assert repeated_complete.json()["interview_id"] == interview_id
    with db_session() as session:
        row = session.scalar(select(InterviewRow).where(InterviewRow.public_id == interview_id))
        assert row is not None
        assert row.audio_removed is True
        assert row.config["source"] == "desktop_live_companion"
        assert (
            session.scalar(
                select(func.count(InterviewSegmentRow.id)).where(
                    InterviewSegmentRow.interview_id == row.id
                )
            )
            == 1
        )
        assert session.scalar(select(func.count(InterviewRow.id))) == 1

    def set_analysis_state(
        *, analyzing: bool, stage_status: str, analysis: dict[str, Any] | None = None
    ) -> None:
        with db_session() as session:
            row = session.scalar(select(InterviewRow).where(InterviewRow.public_id == interview_id))
            assert row is not None
            row.analysis = analysis or {}
            row.config = {
                **dict(row.config or {}),
                "analyzing": analyzing,
                "pipeline": [{"id": "analyze", "label": "Analyze", "status": stage_status}],
            }

    set_analysis_state(analyzing=True, stage_status="running")
    assert (
        client.post(f"/interviews/live/sessions/{session_id}/complete").json()["analysis_status"]
        == "queued"
    )
    set_analysis_state(
        analyzing=False, stage_status="completed", analysis={"summary": "Grounded analysis"}
    )
    assert (
        client.post(f"/interviews/live/sessions/{session_id}/complete").json()["analysis_status"]
        == "completed"
    )
    set_analysis_state(analyzing=False, stage_status="failed")
    assert (
        client.post(f"/interviews/live/sessions/{session_id}/complete").json()["analysis_status"]
        == "failed"
    )
    set_analysis_state(analyzing=False, stage_status="pending")
    assert (
        client.post(f"/interviews/live/sessions/{session_id}/complete").json()["analysis_status"]
        == "unavailable"
    )
    removable = _create_session(client, client_session_id="desktop-session-0003")
    assert (
        client.post(f"/interviews/live/sessions/{removable['id']}/cancel").json()["status"]
        == "cancelled"
    )
    assert client.delete(f"/interviews/live/sessions/{removable['id']}").json() == {"deleted": True}


def test_creator_private_project_nulling_and_personal_export(settings: Settings) -> None:
    client, _token = _client(settings)
    _scholar(client)
    project_response = client.post(
        "/projects", json={"name": "Companion project", "question": "What did participants need?"}
    )
    assert project_response.status_code == 200, project_response.text
    project_id = int(project_response.json()["id"])
    created = _create_session(client, project_id=project_id)
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "owner@example.org"))
        assert owner is not None
        add_member(session, owner.org_id, "member@example.org", PASSWORD, "member", "Member")
        member_token = authenticate(session, "member@example.org", PASSWORD)
        tables, _inventory = _privacy_export_tables(session, user=owner)
        assert len(tables["live_companion_sessions"]) == 1
    member = TestClient(client.app)
    member.headers["Authorization"] = f"Bearer {member_token}"
    assert member.get(f"/interviews/live/sessions/{created['id']}").status_code == 404
    assert client.post(f"/interviews/live/sessions/{created['id']}/cancel").status_code == 200
    assert client.delete(f"/projects/{project_id}").status_code == 200
    assert client.get(f"/interviews/live/sessions/{created['id']}").json()["project_id"] is None


def test_ask_is_grounded_idempotent_and_does_not_block_appends(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, token = _client(settings)
    _scholar(client)
    created = _create_session(client)
    session_id = created["id"]
    assert _append(client, session_id).status_code == 200
    entered = threading.Event()
    release = threading.Event()
    provider_calls = 0

    def fake_turn(*_args: Any, **_kwargs: Any) -> InterviewAgentTurn:
        nonlocal provider_calls
        provider_calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return InterviewAgentTurn(
            answer="It is managed as code.",
            quotes=[
                {
                    "segment": 1,
                    "text": "Terraform manages infrastructure as code.",
                    "verified": True,
                }
            ],
            outcome="completed",
        )

    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "run_interview_agent", fake_turn)
    asker = TestClient(client.app)
    asker.headers["Authorization"] = f"Bearer {token}"

    def ask() -> Any:
        return asker.post(
            f"/interviews/live/sessions/{session_id}/ask",
            json={"client_request_id": "question-0001", "question": "What is it?"},
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(ask)
        assert entered.wait(timeout=5)
        appended = _append(
            client,
            session_id,
            event_id="segment-0002",
            start_ms=1000,
            end_ms=2000,
            text="The second immutable segment arrived during the answer.",
        )
        assert appended.status_code == 200, appended.text
        release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["status"] == "pending"
    payload = client.get(f"/interviews/live/sessions/{session_id}/asks").json()[0]
    assert payload["status"] == "completed"
    assert payload["transcript_sources"][0]["segment_id"] == "segment-0001"
    replay = ask()
    assert replay.status_code == 200
    assert replay.json()["id"] == payload["id"]
    assert provider_calls == 1
    with db_session() as session:
        assert session.scalar(select(func.count(LiveCompanionAskRow.id))) == 1
        assert session.scalar(select(func.count(LiveCompanionSegmentRow.id))) == 2
        org = session.scalar(select(Org))
        assert org is not None
        expected_credits = question_cost(1, plan=plan_for_org(org), input_chars=len("What is it?"))
        credits = session.scalars(
            select(CreditEventRow).where(CreditEventRow.action == "question")
        ).all()
        assert len(credits) == 1
        assert credits[0].credits == expected_credits
        reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action == "live_companion_ask"
            )
        )
        assert reservation is not None
        assert reservation.reserved_credits == expected_credits
        assert reservation.remaining_credits == 0
        assert reservation.status == "settled"


def test_ask_grounding_and_provider_failures_are_terminal_and_safe(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    assert _append(client, session_id).status_code == 200
    provider_calls = 0

    def ungrounded(*_args: Any, **_kwargs: Any) -> InterviewAgentTurn:
        nonlocal provider_calls
        provider_calls += 1
        return InterviewAgentTurn(answer="Unsupported answer", quotes=[], outcome="completed")

    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "run_interview_agent", ungrounded)
    request = {"client_request_id": "ungrounded-question-0001", "question": "What is unsupported?"}
    first = client.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
    assert first.status_code == 202
    replay = client.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
    assert replay.status_code == 200
    assert replay.json()["status"] == "failed"
    assert replay.json()["error_code"] == "answer_not_grounded"
    assert provider_calls == 1
    with db_session() as session:
        failed = session.scalar(
            select(LiveCompanionAskRow).where(
                LiveCompanionAskRow.client_request_id == "ungrounded-question-0001"
            )
        )
        assert failed is not None
        assert failed.status == "failed"
        assert session.scalar(select(func.count(CreditEventRow.id))) == 0
        evidence_reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id.like("live-ask:%ungrounded-question-0001")
            )
        )
        assert evidence_reservation is not None
        assert evidence_reservation.remaining_credits == 0
        assert evidence_reservation.status == "settled"

    def provider_failure(*_args: Any, **_kwargs: Any) -> InterviewAgentTurn:
        nonlocal provider_calls
        provider_calls += 1
        raise RuntimeError("provider-secret-that-must-not-leak")

    monkeypatch.setattr(livemod, "run_interview_agent", provider_failure)
    failed_request = {
        "client_request_id": "provider-failure-0001",
        "question": "Will the error stay safe?",
    }
    provider_response = client.post(
        f"/interviews/live/sessions/{session_id}/ask", json=failed_request
    )
    assert provider_response.status_code == 202
    assert "provider-secret" not in provider_response.text
    failed_replay = client.post(f"/interviews/live/sessions/{session_id}/ask", json=failed_request)
    assert failed_replay.status_code == 200
    assert "provider-secret" not in failed_replay.text
    assert provider_calls == 2
    with db_session() as session:
        assert session.scalar(select(func.count(CreditEventRow.id))) == 0
        provider_reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id.like("live-ask:%provider-failure-0001")
            )
        )
        assert provider_reservation is not None
        assert provider_reservation.remaining_credits == 0
        assert provider_reservation.status == "failed"


def test_live_ask_follow_up_receives_bounded_prior_chat_context(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    assert _append(client, session_id).status_code == 200
    received_history: list[list[dict[str, str]]] = []
    first_answer = "A" * 3000
    second_answer = "B" * 3000

    def grounded_turn(*_args: Any, **kwargs: Any) -> InterviewAgentTurn:
        received_history.append(kwargs["history"])
        return InterviewAgentTurn(
            answer=first_answer if len(received_history) == 1 else second_answer,
            quotes=[
                {
                    "segment": 1,
                    "text": "Terraform manages infrastructure as code.",
                    "verified": True,
                }
            ],
            outcome="completed",
        )

    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "run_interview_agent", grounded_turn)
    first = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={"client_request_id": "context-question-0001", "question": "Which option?"},
    )
    assert first.status_code == 202, first.text
    second = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={"client_request_id": "context-question-0002", "question": "Why that one?"},
    )
    assert second.status_code == 202, second.text
    assert received_history[0] == []
    assert received_history[1] == [
        {"role": "user", "content": "Which option?"},
        {"role": "assistant", "content": first_answer},
    ]
    with db_session() as session:
        org = session.scalar(select(Org))
        assert org is not None
        plan = plan_for_org(org)
        first_input_chars = len("Which option?")
        second_input_chars = len("Why that one?") + len("Which option?") + len(first_answer)
        expected = [
            question_cost(1, plan=plan, input_chars=first_input_chars)
            + question_settlement_cost(
                1,
                plan=plan,
                input_chars=first_input_chars,
                sources_considered=1,
                output_chars=len(first_answer),
            ),
            question_cost(1, plan=plan, input_chars=second_input_chars)
            + question_settlement_cost(
                1,
                plan=plan,
                input_chars=second_input_chars,
                sources_considered=1,
                output_chars=len(second_answer),
            ),
        ]
        charges = session.scalars(
            select(CreditEventRow)
            .where(CreditEventRow.action == "question")
            .order_by(CreditEventRow.id)
        ).all()
        assert [charge.credits for charge in charges] == expected
        reservations = session.scalars(
            select(CapacityReservationRow)
            .where(CapacityReservationRow.action == "live_companion_ask")
            .order_by(CapacityReservationRow.id)
        ).all()
        assert reservations[1].reserved_credits == question_cost(
            1, plan=plan, input_chars=second_input_chars
        )


def test_live_ask_history_has_a_hard_character_bound() -> None:
    newest = SimpleNamespace(question="newest", answer="N" * 40000)
    older = SimpleNamespace(question="older", answer="O" * 40000)
    history = livemod._bounded_ask_history([newest, older])
    assert sum(len(message["content"]) for message in history) <= livemod.LIVE_ASK_HISTORY_CHARS
    assert history[0] == {"role": "user", "content": "newest"}
    assert history[1]["role"] == "assistant"
    assert history[1]["content"].startswith("N")


@pytest.mark.parametrize("failure_point", ["check_can_ask", "begin_ai_action"])
@pytest.mark.parametrize(
    "code",
    [
        "feature_not_in_plan",
        "capacity_exhausted",
        "concurrency_limit",
        "resource_limit",
        "action_capacity_limit",
        "entitlement_limit",
    ],
)
def test_live_answer_preserves_entitlement_category_without_internal_error_text(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, failure_point: str, code: str
) -> None:
    """Both preflight and reservation errors retain their actual actionable cause."""
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    assert _append(client, session_id).status_code == 200

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise EntitlementError("Internal diagnostic must stay private.", code=code)

    monkeypatch.setattr(livemod, failure_point, refuse)
    response = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={"client_request_id": "typed-limit-0001", "question": "Summarize the discussion."},
    )
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"] == "Live answer cannot start right now."
    assert "Internal diagnostic" not in response.text
    with db_session() as session:
        assert session.scalar(select(func.count(LiveCompanionAskRow.id))) == 0
        assert session.scalar(select(func.count(CapacityReservationRow.id))) == 0


def _database_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    monkeypatch.setenv("SIX_JOBS_POLL_SECONDS", "0.1")
    get_settings.cache_clear()


def _grounded_live_turn(*_args: Any, **_kwargs: Any) -> InterviewAgentTurn:
    return InterviewAgentTurn(
        answer="It is managed as code.",
        quotes=[
            {"segment": 1, "text": "Terraform manages infrastructure as code.", "verified": True}
        ],
        outcome="completed",
    )


def test_live_ask_without_transcript_returns_machine_readable_conflict(settings: Settings) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    response = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={"client_request_id": "no-transcript-0001", "question": "What changed?"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "no_live_transcript",
        "message": "There is no final live transcript to answer from yet.",
    }
    with db_session() as session:
        assert session.scalar(select(func.count(LiveCompanionAskRow.id))) == 0
        assert session.scalar(select(func.count(CapacityReservationRow.id))) == 0


def test_durable_live_ask_acks_before_provider_and_uses_frozen_cutoff_after_complete(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    first_segment = _append(client, session_id)
    assert first_segment.status_code == 200
    cutoff = int(first_segment.json()["last_event_sequence"])
    _database_jobs(monkeypatch)
    provider_calls = 0
    seen_text: list[str] = []

    def turn(*_args: Any, **kwargs: Any) -> InterviewAgentTurn:
        nonlocal provider_calls
        provider_calls += 1
        seen_text.extend(str(item["text"]) for item in kwargs["segments"])
        return _grounded_live_turn()

    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "run_interview_agent", turn)
    request = {
        "client_request_id": "durable-question-0001",
        "question": "What is it?",
        "context_through_sequence": cutoff,
    }
    accepted = client.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "pending"
    assert accepted.json()["code"] == "ask_pending"
    assert accepted.json()["context_through_sequence"] == cutoff
    assert provider_calls == 0
    replay = client.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
    assert replay.status_code == 202
    assert replay.json()["id"] == accepted.json()["id"]
    with db_session() as session:
        ask = session.scalar(select(LiveCompanionAskRow))
        reservation = session.scalar(select(CapacityReservationRow))
        job = session.scalar(
            select(BackgroundJobRow).where(BackgroundJobRow.task == "_execute_live_companion_ask")
        )
        assert ask is not None and ask.context_through_sequence == cutoff
        assert reservation is not None and reservation.status == "active"
        assert reservation.action_id == ask.action_id
        assert job is not None and job.status == "queued" and (job.lane == "chat")
        assert job.args == [ask.id]
        assert session.scalar(select(func.count(LiveCompanionAskRow.id))) == 1
        assert session.scalar(select(func.count(CapacityReservationRow.id))) == 1
        assert (
            session.scalar(
                select(func.count(BackgroundJobRow.id)).where(
                    BackgroundJobRow.task == "_execute_live_companion_ask"
                )
            )
            == 1
        )
    second_segment = _append(
        client,
        session_id,
        event_id="segment-after-cutoff",
        start_ms=1000,
        end_ms=2000,
        text="This segment arrived after the frozen answer snapshot.",
    )
    assert second_segment.status_code == 200
    completed_session = client.post(f"/interviews/live/sessions/{session_id}/complete")
    assert completed_session.status_code == 200
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    assert worker.run_once()
    assert provider_calls == 1
    assert seen_text == ["Terraform manages infrastructure as code."]
    receipt = client.get(f"/interviews/live/sessions/{session_id}/asks").json()[0]
    assert receipt["status"] == "completed"
    assert receipt["code"] == "ask_completed"
    assert receipt["transcript_sources"][0]["segment_id"] == "segment-0001"
    completed_replay = client.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
    assert completed_replay.status_code == 200
    assert completed_replay.json()["id"] == accepted.json()["id"]
    assert provider_calls == 1
    with db_session() as session:
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.action == "question")
            )
            == 1
        )
        reservation = session.scalar(select(CapacityReservationRow))
        assert reservation is not None and reservation.status == "settled"


def test_live_ask_omitted_cutoff_is_replay_stable_and_future_cutoff_is_rejected(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    first = _append(client, session_id)
    first_cutoff = int(first.json()["last_event_sequence"])
    _database_jobs(monkeypatch)
    request = {"client_request_id": "legacy-cutoff-0001", "question": "What is it?"}
    accepted = client.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
    assert accepted.status_code == 202
    assert accepted.json()["context_through_sequence"] == first_cutoff
    second = _append(
        client,
        session_id,
        event_id="segment-legacy-0002",
        start_ms=1000,
        end_ms=2000,
        text="A later segment must not change the omitted-cutoff replay digest.",
    )
    second_cutoff = int(second.json()["last_event_sequence"])
    replay = client.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
    assert replay.status_code == 202
    assert replay.json()["id"] == accepted.json()["id"]
    assert replay.json()["context_through_sequence"] == first_cutoff
    changed = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={**request, "context_through_sequence": second_cutoff},
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "ask_request_conflict"
    future = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={
            "client_request_id": "future-cutoff-0001",
            "question": "What is next?",
            "context_through_sequence": second_cutoff + 10,
        },
    )
    assert future.status_code == 409
    assert future.json()["detail"]["code"] == "context_not_available"


def test_duplicate_segment_retry_returns_the_latest_segment_cutoff(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    first = _append(client, session_id)
    cutoff = int(first.json()["last_event_sequence"])
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "run_interview_agent", _grounded_live_turn)
    completed = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={
            "client_request_id": "segment-cursor-ask-0001",
            "question": "What is it?",
            "context_through_sequence": cutoff,
        },
    )
    assert completed.status_code == 202
    duplicate = _append(client, session_id)
    assert duplicate.status_code == 200
    assert duplicate.json()["accepted"] == 0
    assert duplicate.json()["duplicates"] == 1
    assert duplicate.json()["last_event_sequence"] == cutoff


def test_cancelled_session_still_completes_its_frozen_live_ask(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    cutoff = int(_append(client, session_id).json()["last_event_sequence"])
    _database_jobs(monkeypatch)
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "run_interview_agent", _grounded_live_turn)
    accepted = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={
            "client_request_id": "cancelled-session-ask-0001",
            "question": "What is it?",
            "context_through_sequence": cutoff,
        },
    )
    assert accepted.status_code == 202
    assert client.post(f"/interviews/live/sessions/{session_id}/cancel").status_code == 200
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    assert worker.run_once()
    receipt = client.get(f"/interviews/live/sessions/{session_id}/asks").json()[0]
    assert receipt["status"] == "completed"
    with db_session() as session:
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.action == "question")
            )
            == 1
        )


def test_live_ask_concurrent_replay_creates_one_job_and_reservation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    cutoff = int(_append(client, session_id).json()["last_event_sequence"])
    _database_jobs(monkeypatch)
    request = {
        "client_request_id": "concurrent-ask-0001",
        "question": "What is it?",
        "context_through_sequence": cutoff,
    }

    def submit() -> tuple[int, str]:
        caller = TestClient(client.app)
        caller.headers["Authorization"] = f"Bearer {token}"
        response = caller.post(f"/interviews/live/sessions/{session_id}/ask", json=request)
        return (response.status_code, str(response.json()["id"]))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: submit(), range(2)))
    assert [status for status, _ask_id in results] == [202, 202]
    assert len({ask_id for _status, ask_id in results}) == 1
    with db_session() as session:
        assert session.scalar(select(func.count(LiveCompanionAskRow.id))) == 1
        assert session.scalar(select(func.count(CapacityReservationRow.id))) == 1
        assert (
            session.scalar(
                select(func.count(BackgroundJobRow.id)).where(
                    BackgroundJobRow.task == "_execute_live_companion_ask"
                )
            )
            == 1
        )


def test_live_ask_worker_retry_reuses_snapshot_and_charges_once(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    cutoff = int(_append(client, session_id).json()["last_event_sequence"])
    _database_jobs(monkeypatch)
    calls = 0

    def flaky(*_args: Any, **_kwargs: Any) -> InterviewAgentTurn:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("transient-provider-detail")
        return _grounded_live_turn()

    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "run_interview_agent", flaky)
    accepted = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={
            "client_request_id": "retry-ask-0001",
            "question": "What is it?",
            "context_through_sequence": cutoff,
        },
    )
    assert accepted.status_code == 202
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    assert worker.run_once()
    with db_session() as session:
        ask = session.scalar(select(LiveCompanionAskRow))
        job = session.scalar(
            select(BackgroundJobRow).where(BackgroundJobRow.task == "_execute_live_companion_ask")
        )
        reservation = session.scalar(select(CapacityReservationRow))
        assert ask is not None and ask.status == "pending"
        assert job is not None and job.status == "queued" and (job.attempts == 1)
        assert reservation is not None and reservation.status == "active"
        job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert worker.run_once()
    assert calls == 2
    with db_session() as session:
        ask = session.scalar(select(LiveCompanionAskRow))
        job = session.scalar(
            select(BackgroundJobRow).where(BackgroundJobRow.task == "_execute_live_companion_ask")
        )
        reservation = session.scalar(select(CapacityReservationRow))
        assert ask is not None and ask.status == "completed"
        assert ask.context_through_sequence == cutoff
        assert job is not None and job.status == "completed" and (job.attempts == 2)
        assert reservation is not None and reservation.status == "settled"
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.action == "question")
            )
            == 1
        )


def test_live_ask_final_job_failure_does_not_terminalize_a_colliding_run(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    cutoff = int(_append(client, session_id).json()["last_event_sequence"])
    _database_jobs(monkeypatch)
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )

    def fail(*_args: Any, **_kwargs: Any) -> InterviewAgentTurn:
        raise RuntimeError("provider-secret-that-must-not-leak")

    monkeypatch.setattr(livemod, "run_interview_agent", fail)
    accepted = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={
            "client_request_id": "terminal-failure-0001",
            "question": "Will this fail safely?",
            "context_through_sequence": cutoff,
        },
    )
    assert accepted.status_code == 202
    with db_session() as session:
        ask = session.scalar(select(LiveCompanionAskRow))
        assert ask is not None
        unrelated_run = Run(
            id=ask.id, org_id=ask.org_id, question="Unrelated research run", status="running"
        )
        session.add(unrelated_run)
        session.flush()
        colliding_run_id = unrelated_run.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    for attempt in range(3):
        assert worker.run_once()
        if attempt < 2:
            with db_session() as session:
                job = session.scalar(
                    select(BackgroundJobRow).where(
                        BackgroundJobRow.task == "_execute_live_companion_ask"
                    )
                )
                assert job is not None and job.status == "queued"
                job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    receipt = client.get(f"/interviews/live/sessions/{session_id}/asks").json()[0]
    assert receipt["status"] == "failed"
    assert receipt["code"] == "answer_unavailable"
    assert "provider-secret" not in str(receipt)
    with db_session() as session:
        job = session.scalar(
            select(BackgroundJobRow).where(BackgroundJobRow.task == "_execute_live_companion_ask")
        )
        reservation = session.scalar(select(CapacityReservationRow))
        colliding_run = session.get(Run, colliding_run_id)
        assert job is not None and job.status == "failed" and (job.attempts == 3)
        assert reservation is not None and reservation.status == "failed"
        assert reservation.remaining_credits == 0
        assert session.scalar(select(func.count(CreditEventRow.id))) == 0
        assert colliding_run is not None and colliding_run.status == "running"
        assert colliding_run.finished_at is None
        assert (
            session.scalar(
                select(func.count(RunEvent.id)).where(
                    RunEvent.run_id == colliding_run_id, RunEvent.event == "run_failed"
                )
            )
            == 0
        )


def test_deleting_session_cancels_pending_live_ask_job_and_reservation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    cutoff = int(_append(client, session_id).json()["last_event_sequence"])
    _database_jobs(monkeypatch)
    accepted = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={
            "client_request_id": "delete-pending-0001",
            "question": "Will this be cancelled?",
            "context_through_sequence": cutoff,
        },
    )
    assert accepted.status_code == 202
    assert client.post(f"/interviews/live/sessions/{session_id}/cancel").status_code == 200
    deleted = client.delete(f"/interviews/live/sessions/{session_id}")
    assert deleted.status_code == 200
    with db_session() as session:
        job = session.scalar(
            select(BackgroundJobRow).where(BackgroundJobRow.task == "_execute_live_companion_ask")
        )
        reservation = session.scalar(select(CapacityReservationRow))
        assert job is not None and job.status == "cancelled"
        assert reservation is not None and reservation.status == "cancelled"
        assert reservation.remaining_credits == 0
        assert session.scalar(select(func.count(LiveCompanionAskRow.id))) == 0
        assert session.scalar(select(func.count(LiveCompanionSessionRow.id))) == 0


def test_live_ask_enqueue_failure_rolls_back_receipt_and_reservation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _token = _client(settings)
    _scholar(client)
    session_id = _create_session(client)["id"]
    cutoff = int(_append(client, session_id).json()["last_event_sequence"])
    monkeypatch.setenv("SIX_JOBS_BACKEND", "unsupported")
    get_settings.cache_clear()
    response = client.post(
        f"/interviews/live/sessions/{session_id}/ask",
        json={
            "client_request_id": "queue-failure-0001",
            "question": "Will acceptance roll back?",
            "context_through_sequence": cutoff,
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ask_queue_unavailable"
    with db_session() as session:
        assert session.scalar(select(func.count(LiveCompanionAskRow.id))) == 0
        assert session.scalar(select(func.count(CapacityReservationRow.id))) == 0
        assert session.scalar(select(func.count(BackgroundJobRow.id))) == 0
