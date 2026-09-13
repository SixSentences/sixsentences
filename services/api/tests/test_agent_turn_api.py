"""API contracts for durable specialist turn status, stop, and replay."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from sixsentences_server.agent.ledger import (
    agent_turn_request_hash,
    append_agent_event,
    claim_agent_turn,
    finish_agent_turn,
    reserve_agent_turn,
)
from sixsentences_server.api.app import _AGENT_TURN_CANCEL_EVENTS, create_app
from sixsentences_server.core.db import (
    AgentEventRow,
    AgentTurnRow,
    CreditEventRow,
    DatasetMessageRow,
    InterviewMessageRow,
    InterviewRow,
    Org,
    ResearchDatasetRow,
    SurveyMessageRow,
    SurveyRow,
    User,
    VoiceStudyMessageRow,
    VoiceStudyRow,
    WriterCollaboratorRow,
    WriterDocumentRow,
    WriterMessageRow,
    db_session,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus


def _register(client: TestClient, email: str, org: str) -> str:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def _authed(app: FastAPI, *, email: str, org: str) -> TestClient:
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {_register(client, email, org)}"
    return client


def _user_identity(client: TestClient, email: str) -> tuple[int, int]:
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    return (org_id, user_id)


def _add_member(app: FastAPI, owner: TestClient, email: str) -> TestClient:
    org_id = int(owner.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"
    response = owner.post(
        "/orgs/current/users", json={"email": email, "password": "StrongPass123!", "role": "member"}
    )
    assert response.status_code == 201, response.text
    member = TestClient(app)
    login = member.post("/auth/login", json={"email": email, "password": "StrongPass123!"})
    assert login.status_code == 200, login.text
    member.headers["Authorization"] = f"Bearer {login.json()['token']}"
    return member


def test_writer_stream_authorizes_editor_before_persisting_turn_or_prompt(
    corpus: DuckDBCorpus,
) -> None:
    app = create_app()
    owner = _authed(app, email="writer-stream-owner@example.test", org="Writer Stream ACL")
    viewer_email = "writer-stream-viewer@example.test"
    viewer = _add_member(app, owner, viewer_email)
    _org_id, viewer_id = _user_identity(viewer, viewer_email)
    document = owner.post(
        "/writer", json={"title": "Private manuscript", "template": "blank"}
    ).json()
    document_id = str(document["public_id"])
    granted = owner.put(
        f"/writer/{document_id}/collaborators", json={"user_id": viewer_id, "role": "viewer"}
    )
    assert granted.status_code == 200, granted.text
    denied_turn_id = "writer_viewer_denied_1234"
    denied = viewer.post(
        f"/writer/{document_id}/chat/stream",
        json={
            "turn_id": denied_turn_id,
            "message": "Change the manuscript",
            "model": "auto",
            "active_path": "main.tex",
        },
    )
    assert denied.status_code == 403
    assert "editor manuscript access required" in denied.json()["detail"]
    missing_turn_id = "writer_missing_denied_1234"
    missing = viewer.post(
        "/writer/not-a-document/chat/stream",
        json={
            "turn_id": missing_turn_id,
            "message": "Change the manuscript",
            "model": "auto",
            "active_path": "main.tex",
        },
    )
    assert missing.status_code == 404
    with db_session() as session:
        turns = session.scalars(
            select(AgentTurnRow).where(AgentTurnRow.turn_id.in_([denied_turn_id, missing_turn_id]))
        ).all()
        writer = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == document_id)
        )
        assert writer is not None
        prompts = session.scalars(
            select(WriterMessageRow).where(WriterMessageRow.document_id == writer.id)
        ).all()
    assert turns == []
    assert prompts == []


def test_active_agent_turn_discovery_is_owner_scoped_for_every_specialist_kind(
    corpus: DuckDBCorpus,
) -> None:
    app = create_app()
    owner_email = "agent-active-owner@example.test"
    owner = _authed(app, email=owner_email, org="Agent Active Owner")
    member = _add_member(app, owner, "agent-active-member@example.test")
    org_id, user_id = _user_identity(owner, owner_email)
    resources = {
        "manuscript": "writer-active-resource",
        "interview": "interview-active-resource",
        "interview-study": "study-active-resource",
        "survey": "survey-active-resource",
        "dataset": "dataset-active-resource",
    }
    reservations = []
    with db_session() as session:
        for index, (kind, resource_id) in enumerate(resources.items(), start=1):
            reservation = reserve_agent_turn(
                session,
                org_id=org_id,
                user_id=user_id,
                resource_kind=kind,
                resource_id=resource_id,
                turn_id=f"active_turn_{index:04d}",
                request_hash=agent_turn_request_hash({"prompt": f"Work on {kind}"}),
            )
            claim_agent_turn(session, reservation.row_id)
            append_agent_event(
                session,
                reservation.row_id,
                event="turn.started",
                payload={"label": "Agent turn started"},
            )
            reservations.append(reservation)
    for (kind, resource_id), reservation in zip(resources.items(), reservations, strict=True):
        response = owner.get(
            "/agent/turns/active", params={"resource_kind": kind, "resource_id": resource_id}
        )
        assert response.status_code == 200
        assert response.json() == {
            "turn_id": reservation.turn_id,
            "resource_kind": kind,
            "resource_id": resource_id,
            "status": "running",
            "last_event_id": 1,
            "result": None,
            "error_message": None,
            "created_at": response.json()["created_at"],
            "started_at": response.json()["started_at"],
            "finished_at": None,
        }
        hidden = member.get(
            "/agent/turns/active", params={"resource_kind": kind, "resource_id": resource_id}
        )
        assert hidden.status_code == 200
        assert hidden.json() is None
    with db_session() as session:
        for reservation in reservations:
            finish_agent_turn(
                session, reservation.row_id, status="completed", result={"answer": "Done"}
            )
    for kind, resource_id in resources.items():
        response = owner.get(
            "/agent/turns/active", params={"resource_kind": kind, "resource_id": resource_id}
        )
        assert response.status_code == 200
        assert response.json() is None
        latest = owner.get(
            "/agent/turns/latest", params={"resource_kind": kind, "resource_id": resource_id}
        )
        assert latest.status_code == 200
        assert latest.json()["status"] == "completed"
        assert latest.json()["resource_kind"] == kind
        assert latest.json()["resource_id"] == resource_id


def test_agent_turn_status_stop_and_replay_are_owner_scoped(corpus: DuckDBCorpus) -> None:
    app = create_app()
    owner_email = "agent-turn-owner@example.test"
    owner = _authed(app, email=owner_email, org="Agent Turn Owner")
    member = _add_member(app, owner, "agent-turn-member@example.test")
    outsider = _authed(app, email="agent-turn-outsider@example.test", org="Other Agent Turn Org")
    org_id, user_id = _user_identity(owner, owner_email)
    turn_id = "dataset_stop_1234"
    request_payload = {"question": "Inspect durable cancellation", "model": "auto"}
    with db_session() as session:
        dataset = ResearchDatasetRow(
            org_id=org_id, name="Private durable dataset", filename="private.csv"
        )
        session.add(dataset)
        session.flush()
        resource_id = dataset.public_id
        reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="dataset",
            resource_id=resource_id,
            turn_id=turn_id,
            request_hash=agent_turn_request_hash(request_payload),
        )
    queued = owner.get(f"/agent/turns/{turn_id}")
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert member.get(f"/agent/turns/{turn_id}").status_code == 404
    assert member.post(f"/agent/turns/{turn_id}/stop").status_code == 404
    assert member.get(f"/agent/turns/{turn_id}/events/stream").status_code == 404
    assert outsider.get(f"/agent/turns/{turn_id}").status_code == 404
    member_replay = member.post(
        f"/datasets/{resource_id}/chat/stream", json={"turn_id": turn_id, **request_payload}
    )
    assert member_replay.status_code == 404
    assert member_replay.json()["detail"] == "agent turn not found"
    active_conflict = member.post(
        f"/datasets/{resource_id}/chat/stream",
        json={"turn_id": "dataset_member_1234", **request_payload},
    )
    assert active_conflict.status_code == 409
    assert active_conflict.json()["detail"] == {"message": "another agent turn is already running"}
    stopped = owner.post(f"/agent/turns/{turn_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json() == {"turn_id": turn_id, "status": "cancelled"}
    assert owner.post(f"/agent/turns/{turn_id}/stop").json()["status"] == "cancelled"
    status = owner.get(f"/agent/turns/{turn_id}").json()
    assert status["status"] == "cancelled"
    assert status["last_event_id"] == 1
    assert (
        owner.get(
            "/agent/turns/active", params={"resource_kind": "dataset", "resource_id": resource_id}
        ).json()
        is None
    )
    latest = owner.get(
        "/agent/turns/latest", params={"resource_kind": "dataset", "resource_id": resource_id}
    )
    assert latest.status_code == 200
    assert latest.json()["turn_id"] == turn_id
    assert latest.json()["status"] == "cancelled"
    replay = owner.post(
        f"/datasets/{resource_id}/chat/stream", json={"turn_id": turn_id, **request_payload}
    )
    assert replay.status_code == 200
    assert replay.headers["x-agent-turn-id"] == turn_id
    assert "id: 1\nevent: turn.cancelled" in replay.text
    bodyless_replay = owner.get(f"/agent/turns/{turn_id}/events/stream")
    assert bodyless_replay.status_code == 200
    assert bodyless_replay.headers["x-agent-turn-id"] == turn_id
    assert "id: 1\nevent: turn.cancelled" in bodyless_replay.text
    assert (org_id, turn_id) not in _AGENT_TURN_CANCEL_EVENTS
    with db_session() as session:
        row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.turn_id == turn_id))
        assert row is not None
        event_count = session.scalar(
            select(func.count(AgentEventRow.id)).where(AgentEventRow.turn_id == row.id)
        )
        assert event_count == 1


def test_agent_turn_reconnect_replays_only_events_after_valid_cursor(corpus: DuckDBCorpus) -> None:
    app = create_app()
    email = "agent-turn-replay@example.test"
    client = _authed(app, email=email, org="Agent Turn Replay")
    org_id, user_id = _user_identity(client, email)
    turn_id = "dataset_replay_1234"
    request_payload = {"question": "Inspect durable replay", "model": "auto"}
    with db_session() as session:
        dataset = ResearchDatasetRow(org_id=org_id, name="Replay dataset", filename="replay.csv")
        session.add(dataset)
        session.flush()
        resource_id = dataset.public_id
        reservation = reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="dataset",
            resource_id=resource_id,
            turn_id=turn_id,
            request_hash=agent_turn_request_hash(request_payload),
        )
        claim_agent_turn(session, reservation.row_id)
        append_agent_event(
            session,
            reservation.row_id,
            event="turn.started",
            payload={"label": "Agent turn started"},
        )
        append_agent_event(
            session,
            reservation.row_id,
            event="tool.completed",
            payload={"tool": "dataset.inspect", "output": {"rows": 4}},
        )
        finish_agent_turn(
            session,
            reservation.row_id,
            status="completed",
            result={"answer": "Four rows inspected."},
        )
        append_agent_event(
            session,
            reservation.row_id,
            event="turn.completed",
            payload={"result": {"answer": "Four rows inspected."}},
        )
    replay = client.post(
        f"/datasets/{resource_id}/chat/stream",
        headers={"Last-Event-ID": "1"},
        json={"turn_id": turn_id, **request_payload},
    )
    assert replay.status_code == 200
    assert "id: 1\n" not in replay.text
    assert "id: 2\nevent: tool.completed" in replay.text
    assert "id: 3\nevent: turn.completed" in replay.text
    invalid_future_cursor = client.post(
        f"/datasets/{resource_id}/chat/stream",
        headers={"Last-Event-ID": "999999"},
        json={"turn_id": turn_id, **request_payload},
    )
    assert invalid_future_cursor.status_code == 200
    assert "id: 1\nevent: turn.started" in invalid_future_cursor.text
    assert "id: 2\nevent: tool.completed" in invalid_future_cursor.text
    assert "id: 3\nevent: turn.completed" in invalid_future_cursor.text
    bodyless_cursor_replay = client.get(
        f"/agent/turns/{turn_id}/events/stream", headers={"Last-Event-ID": "1"}
    )
    assert bodyless_cursor_replay.status_code == 200
    assert "id: 1\n" not in bodyless_cursor_replay.text
    assert "id: 2\nevent: tool.completed" in bodyless_cursor_replay.text
    assert "id: 3\nevent: turn.completed" in bodyless_cursor_replay.text
    assert (org_id, turn_id) not in _AGENT_TURN_CANCEL_EVENTS


def test_orphaned_running_turn_is_failed_and_replayable_after_reload(corpus: DuckDBCorpus) -> None:
    app = create_app()
    email = "agent-turn-orphan@example.test"
    client = _authed(app, email=email, org="Agent Turn Orphan")
    org_id, user_id = _user_identity(client, email)
    turn_id = "orphaned_turn_1234"
    resource_id = "orphaned-writer-resource"
    with db_session() as session:
        reservation = reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="manuscript",
            resource_id=resource_id,
            turn_id=turn_id,
            request_hash=agent_turn_request_hash({"message": "Continue writing"}),
        )
        claim_agent_turn(session, reservation.row_id)
        append_agent_event(
            session,
            reservation.row_id,
            event="turn.started",
            payload={"label": "Agent turn started"},
        )
        row = session.get(AgentTurnRow, reservation.row_id)
        assert row is not None
        row.heartbeat_at = row.created_at - timedelta(hours=1)
    active = client.get(
        "/agent/turns/active", params={"resource_kind": "manuscript", "resource_id": resource_id}
    )
    assert active.status_code == 200
    assert active.json() is None
    latest = client.get(
        "/agent/turns/latest", params={"resource_kind": "manuscript", "resource_id": resource_id}
    )
    assert latest.status_code == 200
    assert latest.json()["turn_id"] == turn_id
    assert latest.json()["status"] == "failed"
    assert latest.json()["error_message"]
    replay = client.get(f"/agent/turns/{turn_id}/events/stream")
    assert replay.status_code == 200
    assert "id: 1\nevent: turn.started" in replay.text
    assert "id: 2\nevent: turn.failed" in replay.text


def test_stale_turn_takeover_keeps_predecessor_terminal_replayable(corpus: DuckDBCorpus) -> None:
    app = create_app()
    email = "agent-turn-takeover@example.test"
    client = _authed(app, email=email, org="Agent Turn Takeover")
    org_id, user_id = _user_identity(client, email)
    resource_id = "takeover-dataset-resource"
    stale_turn_id = "takeover_stale_turn_1234"
    fresh_turn_id = "takeover_fresh_turn_1234"
    with db_session() as session:
        stale = reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="dataset",
            resource_id=resource_id,
            turn_id=stale_turn_id,
            request_hash=agent_turn_request_hash({"question": "First attempt"}),
        )
        claim_agent_turn(session, stale.row_id)
        append_agent_event(
            session, stale.row_id, event="turn.started", payload={"label": "First attempt started"}
        )
        stale_row = session.get(AgentTurnRow, stale.row_id)
        assert stale_row is not None
        stale_row.heartbeat_at = stale_row.created_at - timedelta(hours=1)
    with db_session() as session:
        fresh = reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="dataset",
            resource_id=resource_id,
            turn_id=fresh_turn_id,
            request_hash=agent_turn_request_hash({"question": "Second attempt"}),
        )
        claim_agent_turn(session, fresh.row_id)
    replay = client.get(f"/agent/turns/{stale_turn_id}/events/stream")
    assert replay.status_code == 200
    assert "id: 1\nevent: turn.started" in replay.text
    assert "id: 2\nevent: turn.failed" in replay.text
    assert replay.text.count("event: turn.failed") == 1
    active = client.get(
        "/agent/turns/active", params={"resource_kind": "dataset", "resource_id": resource_id}
    )
    assert active.status_code == 200
    assert active.json()["turn_id"] == fresh_turn_id


def test_repeated_stop_does_not_duplicate_terminal_event_after_first_page(
    corpus: DuckDBCorpus,
) -> None:
    app = create_app()
    email = "agent-turn-pagination@example.test"
    client = _authed(app, email=email, org="Agent Turn Pagination")
    org_id, user_id = _user_identity(client, email)
    turn_id = "dataset_stop_page_1234"
    with db_session() as session:
        reservation = reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="dataset",
            resource_id="dataset-stop-page",
            turn_id=turn_id,
            request_hash=agent_turn_request_hash({"question": "Stop queued work", "model": "auto"}),
        )
        for position in range(1, 201):
            append_agent_event(
                session, reservation.row_id, event="queue.updated", payload={"position": position}
            )
    assert client.post(f"/agent/turns/{turn_id}/stop").json()["status"] == "cancelled"
    assert client.post(f"/agent/turns/{turn_id}/stop").json()["status"] == "cancelled"
    with db_session() as session:
        row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.turn_id == turn_id))
        assert row is not None
        terminal_count = session.scalar(
            select(func.count(AgentEventRow.id)).where(
                AgentEventRow.turn_id == row.id, AgentEventRow.event == "turn.cancelled"
            )
        )
        assert row.event_count == 201
        assert terminal_count == 1


def test_terminal_receipts_survive_later_turns_and_stay_owner_scoped_for_every_kind(
    corpus: DuckDBCorpus,
) -> None:
    app = create_app()
    owner_email = "terminal-receipt-owner@example.test"
    member_email = "terminal-receipt-member@example.test"
    owner = _authed(app, email=owner_email, org="Terminal Receipt Owner")
    member = _add_member(app, owner, member_email)
    org_id, owner_id = _user_identity(owner, owner_email)
    _, member_id = _user_identity(member, member_email)
    expected_success_content = {
        "manuscript": "Existing manuscript answer",
        "dataset": "Existing dataset answer",
        "interview": "Existing interview answer",
        "interview-study": "Existing study answer",
        "survey": "Existing survey answer",
    }
    with db_session() as session:
        writer = WriterDocumentRow(org_id=org_id, title="Receipt manuscript", created_by=owner_id)
        dataset = ResearchDatasetRow(org_id=org_id, name="Receipt dataset", filename="receipt.csv")
        interview = InterviewRow(org_id=org_id, title="Receipt interview", status="ready")
        study = VoiceStudyRow(org_id=org_id, title="Receipt study")
        survey = SurveyRow(org_id=org_id, title="Receipt survey")
        session.add_all([writer, dataset, interview, study, survey])
        session.flush()
        session.add(
            WriterCollaboratorRow(
                org_id=org_id,
                document_id=writer.id,
                user_id=member_id,
                role="viewer",
                added_by=owner_id,
            )
        )
        successful_messages = [
            WriterMessageRow(
                org_id=org_id,
                document_id=writer.id,
                role="assistant",
                content="Existing manuscript answer",
                payload={"existing": True},
            ),
            DatasetMessageRow(
                org_id=org_id,
                dataset_id=dataset.id,
                role="assistant",
                content="Existing dataset answer",
                payload={"existing": True},
            ),
            InterviewMessageRow(
                org_id=org_id,
                interview_id=interview.id,
                role="assistant",
                content="Existing interview answer",
                payload={"existing": True},
            ),
            VoiceStudyMessageRow(
                org_id=org_id,
                study_id=study.id,
                role="assistant",
                content="Existing study answer",
                payload={"existing": True},
            ),
            SurveyMessageRow(
                org_id=org_id,
                survey_id=survey.id,
                role="assistant",
                content="Existing survey answer",
                payload={"existing": True},
            ),
        ]
        session.add_all(successful_messages)
        session.flush()
        resources = [
            (
                "manuscript",
                writer.public_id,
                f"/writer/{writer.public_id}/chat",
                WriterMessageRow,
                "document_id",
                writer.id,
            ),
            (
                "dataset",
                dataset.public_id,
                f"/datasets/{dataset.public_id}/chat",
                DatasetMessageRow,
                "dataset_id",
                dataset.id,
            ),
            (
                "interview",
                interview.public_id,
                f"/interviews/{interview.public_id}/chat",
                InterviewMessageRow,
                "interview_id",
                interview.id,
            ),
            (
                "interview-study",
                study.public_id,
                f"/voice/studies/{study.public_id}/chat",
                VoiceStudyMessageRow,
                "study_id",
                study.id,
            ),
            (
                "survey",
                survey.public_id,
                f"/surveys/{survey.public_id}/chat",
                SurveyMessageRow,
                "survey_id",
                survey.id,
            ),
        ]
        for index, (kind, resource_id, *_rest) in enumerate(resources, start=1):
            for status_index, status in enumerate(("failed", "cancelled"), start=1):
                turn_id = f"receipt_{index}_{status_index}_turn"
                reservation = reserve_agent_turn(
                    session,
                    org_id=org_id,
                    user_id=owner_id,
                    resource_kind=kind,
                    resource_id=resource_id,
                    turn_id=turn_id,
                    request_hash=agent_turn_request_hash({"question": f"Terminal {status} {kind}"}),
                )
                claim_agent_turn(session, reservation.row_id)
                append_agent_event(
                    session,
                    reservation.row_id,
                    event="turn.started",
                    payload={"label": "Agent turn started"},
                )
                append_agent_event(
                    session,
                    reservation.row_id,
                    event="tool.completed",
                    payload={
                        "tool": f"{kind}.inspect",
                        "label": "Inspected the workspace",
                        "token": "must-not-survive",
                    },
                )
                finish_agent_turn(
                    session,
                    reservation.row_id,
                    status=status,
                    error_message="provider detail must not become receipt text",
                )
                append_agent_event(
                    session, reservation.row_id, event=f"turn.{status}", payload={"status": status}
                )
            later = reserve_agent_turn(
                session,
                org_id=org_id,
                user_id=owner_id,
                resource_kind=kind,
                resource_id=resource_id,
                turn_id=f"receipt_{index}_later_turn",
                request_hash=agent_turn_request_hash({"question": f"Later successful {kind}"}),
            )
            claim_agent_turn(session, later.row_id)
            finish_agent_turn(
                session, later.row_id, status="completed", result={"answer": "Later success"}
            )
            append_agent_event(
                session,
                later.row_id,
                event="turn.completed",
                payload={"result": {"answer": "Later success"}},
                trusted_payload=True,
            )
        credit_count_before = session.scalar(select(func.count(CreditEventRow.id)))
    for kind, resource_id, route, *_rest in resources:
        latest = owner.get(
            "/agent/turns/latest", params={"resource_kind": kind, "resource_id": resource_id}
        )
        assert latest.status_code == 200
        assert latest.json()["status"] == "completed"
        assert [message["content"] for message in member.get(route).json()] == [
            expected_success_content[kind]
        ]
        first_history = owner.get(route)
        second_history = owner.get(route)
        assert first_history.status_code == 200
        assert second_history.status_code == 200
        assert first_history.json() == second_history.json()
        history = first_history.json()
        assert len(history) == 3
        successful = next(
            message for message in history if message["content"].startswith("Existing")
        )
        assert successful["payload"] == {"existing": True}
        receipts = [
            message for message in history if "agent_terminal_receipt" in message["payload"]
        ]
        assert {message["content"] for message in receipts} == {
            "This agent task was stopped.",
            "This agent task could not be completed.",
        }
        for receipt in receipts:
            payload = receipt["payload"]
            metadata = payload["agent_terminal_receipt"]
            assert payload["turn_id"] == metadata["turn_id"]
            assert payload["terminal_status"] == metadata["status"]
            assert metadata["owner_user_id"] == owner_id
            assert [event["event"] for event in payload["agent_events"]] == [
                "turn.started",
                "tool.completed",
                f"turn.{metadata['status']}",
            ]
            assert payload["agent_events"][1]["token"] == "[protected]"
        after_owner_backfill = member.get(route)
        assert len(after_owner_backfill.json()) == 1
        assert after_owner_backfill.json()[0]["payload"] == {"existing": True}
    with db_session() as session:
        credit_count_after = session.scalar(select(func.count(CreditEventRow.id)))
        assert credit_count_after == credit_count_before
        for _kind, _public_id, _route, message_model, foreign_key, numeric_id in resources:
            messages = session.scalars(
                select(message_model).where(
                    getattr(message_model, foreign_key) == numeric_id,
                    message_model.role == "assistant",
                )
            ).all()
            assert len(messages) == 3


def test_specialist_chat_clear_removes_only_messages_and_terminal_ledgers(
    corpus: DuckDBCorpus,
) -> None:
    app = create_app()
    owner_email = "specialist-clear-owner@example.test"
    owner = _authed(app, email=owner_email, org="Specialist Clear Owner")
    viewer_email = "specialist-clear-viewer@example.test"
    viewer = _add_member(app, owner, viewer_email)
    outsider = _authed(
        app, email="specialist-clear-outsider@example.test", org="Specialist Clear Outsider"
    )
    org_id, owner_id = _user_identity(owner, owner_email)
    _, viewer_id = _user_identity(viewer, viewer_email)
    with db_session() as session:
        writer = WriterDocumentRow(
            org_id=org_id,
            title="Clear manuscript",
            content="domain manuscript content",
            created_by=owner_id,
        )
        dataset = ResearchDatasetRow(
            org_id=org_id,
            name="Clear dataset",
            filename="clear.csv",
            description="domain dataset description",
        )
        interview = InterviewRow(
            org_id=org_id, title="Clear interview", status="ready", guide="domain interview guide"
        )
        study = VoiceStudyRow(
            org_id=org_id, title="Clear study", consent_text="domain study consent"
        )
        survey = SurveyRow(
            org_id=org_id, title="Clear survey", description="domain survey description"
        )
        session.add_all([writer, dataset, interview, study, survey])
        session.flush()
        session.add(
            WriterCollaboratorRow(
                org_id=org_id,
                document_id=writer.id,
                user_id=viewer_id,
                role="viewer",
                added_by=owner_id,
            )
        )
        resources = [
            (
                "manuscript",
                writer,
                WriterMessageRow,
                "document_id",
                f"/writer/{writer.public_id}/chat",
            ),
            (
                "dataset",
                dataset,
                DatasetMessageRow,
                "dataset_id",
                f"/datasets/{dataset.public_id}/chat",
            ),
            (
                "interview",
                interview,
                InterviewMessageRow,
                "interview_id",
                f"/interviews/{interview.public_id}/chat",
            ),
            (
                "interview-study",
                study,
                VoiceStudyMessageRow,
                "study_id",
                f"/voice/studies/{study.public_id}/chat",
            ),
            ("survey", survey, SurveyMessageRow, "survey_id", f"/surveys/{survey.public_id}/chat"),
        ]
        resource_ids: list[tuple[type[object], int]] = []
        for index, (kind, resource, message_model, foreign_key, _route) in enumerate(
            resources, start=1
        ):
            resource_ids.append((type(resource), resource.id))
            session.add_all(
                [
                    message_model(
                        org_id=org_id,
                        role="user",
                        content=f"Question {kind}",
                        payload={"turn_id": f"clear_{index}_turn"},
                        **{foreign_key: resource.id},
                    ),
                    message_model(
                        org_id=org_id,
                        role="assistant",
                        content=f"Answer {kind}",
                        payload={"turn_id": f"clear_{index}_turn"},
                        **{foreign_key: resource.id},
                    ),
                ]
            )
            reservation = reserve_agent_turn(
                session,
                org_id=org_id,
                user_id=owner_id,
                resource_kind=kind,
                resource_id=resource.public_id,
                turn_id=f"clear_{index}_turn",
                request_hash=agent_turn_request_hash({"question": f"Question {kind}"}),
            )
            claim_agent_turn(session, reservation.row_id)
            append_agent_event(
                session, reservation.row_id, event="turn.started", payload={"label": "Started"}
            )
            finish_agent_turn(
                session, reservation.row_id, status="completed", result={"answer": f"Answer {kind}"}
            )
            append_agent_event(
                session,
                reservation.row_id,
                event="turn.completed",
                payload={"result": {"answer": f"Answer {kind}"}},
                trusted_payload=True,
            )
    writer_route = resources[0][4]
    assert viewer.delete(writer_route).status_code == 403
    for _kind, _resource, _message_model, _foreign_key, route in resources:
        assert outsider.delete(route).status_code == 404
        cleared = owner.delete(route)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json() == {"ok": True, "messages_deleted": 2, "turns_deleted": 1}
        assert owner.get(route).json() == []
    with db_session() as session:
        for model, resource_id in resource_ids:
            assert session.get(model, resource_id) is not None
        assert session.scalar(select(func.count(AgentTurnRow.id))) == 0
        assert session.scalar(select(func.count(AgentEventRow.id))) == 0
        assert session.scalar(select(func.count(WriterMessageRow.id))) == 0
        assert session.scalar(select(func.count(DatasetMessageRow.id))) == 0
        assert session.scalar(select(func.count(InterviewMessageRow.id))) == 0
        assert session.scalar(select(func.count(VoiceStudyMessageRow.id))) == 0
        assert session.scalar(select(func.count(SurveyMessageRow.id))) == 0
        assert (
            session.get(WriterDocumentRow, resource_ids[0][1]).content
            == "domain manuscript content"
        )
        assert (
            session.get(ResearchDatasetRow, resource_ids[1][1]).description
            == "domain dataset description"
        )
        assert session.get(InterviewRow, resource_ids[2][1]).guide == "domain interview guide"
        assert session.get(VoiceStudyRow, resource_ids[3][1]).consent_text == "domain study consent"
        assert session.get(SurveyRow, resource_ids[4][1]).description == "domain survey description"


def test_specialist_chat_clear_refuses_an_active_turn(corpus: DuckDBCorpus) -> None:
    app = create_app()
    owner_email = "specialist-clear-active@example.test"
    owner = _authed(app, email=owner_email, org="Specialist Clear Active")
    org_id, owner_id = _user_identity(owner, owner_email)
    with db_session() as session:
        dataset = ResearchDatasetRow(org_id=org_id, name="Active dataset", filename="active.csv")
        session.add(dataset)
        session.flush()
        session.add(
            DatasetMessageRow(
                org_id=org_id,
                dataset_id=dataset.id,
                role="user",
                content="Keep this while active",
                payload={"turn_id": "clear_active_turn"},
            )
        )
        reservation = reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=owner_id,
            resource_kind="dataset",
            resource_id=dataset.public_id,
            turn_id="clear_active_turn",
            request_hash=agent_turn_request_hash({"question": "Keep this while active"}),
        )
        claim_agent_turn(session, reservation.row_id)
        route = f"/datasets/{dataset.public_id}/chat"
        row_id = reservation.row_id
    refused = owner.delete(route)
    assert refused.status_code == 409
    assert "still running" in refused.json()["detail"]
    assert len(owner.get(route).json()) == 1
    with db_session() as session:
        finish_agent_turn(session, row_id, status="cancelled", error_message="Stopped for reset")
        append_agent_event(session, row_id, event="turn.cancelled", payload={"status": "cancelled"})
    cleared = owner.delete(route)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["messages_deleted"] == 1
    assert cleared.json()["turns_deleted"] == 1


def test_dataset_turn_reservation_serializes_with_chat_clear(corpus: DuckDBCorpus) -> None:
    app = create_app()
    owner_email = "specialist-clear-race@example.test"
    owner = _authed(app, email=owner_email, org="Specialist Clear Race")
    org_id, owner_id = _user_identity(owner, owner_email)
    with db_session() as session:
        dataset = ResearchDatasetRow(org_id=org_id, name="Race dataset", filename="race.csv")
        session.add(dataset)
        session.flush()
        dataset_id = dataset.id
        public_id = dataset.public_id
    resource_locked = Event()
    release_reservation = Event()
    clear_started = Event()

    def reserve_while_holding_resource_lock() -> int:
        with db_session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            locked = session.scalar(
                select(ResearchDatasetRow)
                .where(ResearchDatasetRow.id == dataset_id)
                .with_for_update()
            )
            assert locked is not None
            reservation = reserve_agent_turn(
                session,
                org_id=org_id,
                user_id=owner_id,
                resource_kind="dataset",
                resource_id=public_id,
                turn_id="clear_race_turn",
                request_hash=agent_turn_request_hash({"question": "Race clear"}),
            )
            claim_agent_turn(session, reservation.row_id)
            resource_locked.set()
            assert release_reservation.wait(timeout=3)
            return reservation.row_id

    def clear_chat() -> object:
        clear_started.set()
        client = TestClient(app)
        client.headers.update(owner.headers)
        return client.delete(f"/datasets/{public_id}/chat")

    with ThreadPoolExecutor(max_workers=2) as pool:
        reserve_future = pool.submit(reserve_while_holding_resource_lock)
        assert resource_locked.wait(timeout=3)
        clear_future = pool.submit(clear_chat)
        assert clear_started.wait(timeout=3)
        time.sleep(0.05)
        assert not clear_future.done()
        release_reservation.set()
        row_id = reserve_future.result(timeout=3)
        refused = clear_future.result(timeout=3)
    assert refused.status_code == 409
    with db_session() as session:
        row = session.get(AgentTurnRow, row_id)
        assert row is not None
        assert row.status == "running"
        finish_agent_turn(
            session, row_id, status="cancelled", error_message="Stopped after serialization test"
        )
        append_agent_event(session, row_id, event="turn.cancelled", payload={"status": "cancelled"})
    assert owner.delete(f"/datasets/{public_id}/chat").status_code == 200
