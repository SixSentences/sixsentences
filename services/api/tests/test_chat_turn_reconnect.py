"""Reconnect contracts for durable Quick Answer chat turns."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.api.app import (
    _ensure_chat_turn_terminal_receipt,
    _heartbeat_chat_turn,
    create_app,
)
from sixsentences_server.chat.ledger import ensure_chat_turn_terminal_event
from sixsentences_server.core.db import (
    ChatMessageRow,
    ChatTurnEventRow,
    ChatTurnRow,
    Org,
    Run,
    User,
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


def _create_run(client: TestClient) -> tuple[str, int]:
    project = client.post("/projects", json={"name": "Reconnect"}).json()
    run = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()
    return (str(run["public_id"]), int(run["id"]))


def _identity(client: TestClient, email: str) -> tuple[int, int]:
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    return (org_id, int(user_id))


def _sse_event_frames(text: str) -> list[tuple[int, str, dict[str, Any], str]]:
    """Return exact event blocks plus their parsed cursor metadata."""
    frames: list[tuple[int, str, dict[str, Any], str]] = []
    for raw_block in text.split("\n\n"):
        lines = raw_block.splitlines()
        id_line = next((line for line in lines if line.startswith("id: ")), "")
        event_line = next((line for line in lines if line.startswith("event: ")), "")
        data_line = next((line for line in lines if line.startswith("data: ")), "")
        if not id_line or not event_line or (not data_line):
            continue
        sequence = int(id_line.removeprefix("id: "))
        event = event_line.removeprefix("event: ")
        payload = json.loads(data_line.removeprefix("data: "))
        assert isinstance(payload, dict)
        frames.append((sequence, event, payload, raw_block))
    return frames


def test_failed_turn_logs_only_fixed_reason_and_exception_types(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from sixsentences_server.chat.service import ChatError
    from sixsentences_server.llm.base import LLMConfigError
    from sixsentences_server.llm.mock import mock_pool

    marker = "SYNTHETIC_PROVIDER_DETAIL_MUST_STAY_PRIVATE"
    turn_id = "synthetic_failure_diagnostic_0001"

    def unavailable(session: Session, run: Run, _pool: Any, question: str, **kwargs: Any) -> None:
        session.add(
            ChatMessageRow(
                org_id=run.org_id,
                run_id=run.id,
                role="user",
                content=question,
                citations=[],
                payload={"turn_id": turn_id},
            )
        )
        session.commit()
        kwargs["event_sink"]("answer.started", {"phase": "writing"})
        raise ChatError(marker) from LLMConfigError(marker + "_CAUSE")

    monkeypatch.setattr("sixsentences_server.api.app.answer_question", unavailable)
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda _settings: mock_pool())
    client = _authed(create_app(), email="chat-diagnostic@example.test", org="Diagnostics")
    public_id, run_id = _create_run(client)
    response = client.post(
        f"/runs/{public_id}/chat/stream",
        json={"turn_id": turn_id, "question": "Explain the saved evidence."},
    )
    assert response.status_code == 200
    assert "turn.failed" in response.text
    assert marker not in response.text
    records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("streamed chat turn failed reason=")
    ]
    assert len(records) == 1
    diagnostic = records[0]
    assert f"run_id={run_id} turn_id={turn_id}" in diagnostic.getMessage()
    assert "error_class=ChatError cause_class=LLMConfigError" in diagnostic.getMessage()
    assert marker not in diagnostic.getMessage()
    assert diagnostic.exc_info is None
    assert diagnostic.stack_info is None


def test_chat_turn_discovery_status_and_follow_are_owner_scoped(corpus: DuckDBCorpus) -> None:
    app = create_app()
    email = "chat-reconnect-owner@example.test"
    owner = _authed(app, email=email, org="Chat Reconnect")
    member = _add_member(app, owner, "chat-reconnect-member@example.test")
    public_id, run_id = _create_run(owner)
    org_id, user_id = _identity(owner, email)
    now = datetime.now(UTC)
    turn_id = "chat_reconnect_owner_0001"
    with db_session() as session:
        session.add(
            ChatTurnRow(
                turn_id=turn_id,
                org_id=org_id,
                run_id=run_id,
                user_id=user_id,
                question="private prompt",
                request_hash="private-request-hash",
                model="private-model",
                status="running",
                active_slot=1,
                action_id="chat_reconnect_owner_action_0001",
                created_at=now,
                updated_at=now,
                started_at=now,
            )
        )
    active = owner.get(f"/runs/{public_id}/chat/turns/active")
    assert active.status_code == 200
    assert active.json()["turn_id"] == turn_id
    assert active.json()["status"] == "running"
    assert {
        "question",
        "request_hash",
        "model",
        "action_id",
        "run_id",
        "user_id",
        "org_id",
    }.isdisjoint(active.json())
    assert owner.get(f"/runs/{public_id}/chat/turns/{turn_id}").status_code == 200
    assert member.get(f"/runs/{public_id}/chat/turns/active").json() is None
    assert member.get(f"/runs/{public_id}/chat/turns/latest").json() is None
    assert member.get(f"/runs/{public_id}/chat/turns/{turn_id}").status_code == 404
    assert member.get(f"/runs/{public_id}/chat/turns/{turn_id}/events/stream").status_code == 404
    assert member.post(f"/runs/{public_id}/chat/turns/{turn_id}/stop").status_code == 404
    with db_session() as session:
        row = session.scalar(select(ChatTurnRow).where(ChatTurnRow.turn_id == turn_id))
        assert row is not None
        row.status = "completed"
        row.active_slot = None
        row.answer = {
            "answer": "Recovered answer",
            "citations": [],
            "sources_considered": 0,
            "claims_checked": 0,
            "claims_supported": 0,
            "claims_flagged": 0,
            "claim_checks": [],
        }
        row.finished_at = datetime.now(UTC)
    followed = owner.get(f"/runs/{public_id}/chat/turns/{turn_id}/events/stream")
    assert followed.status_code == 200
    assert followed.headers["x-chat-turn-id"] == turn_id
    assert "event: turn.completed" in followed.text
    assert "private prompt" not in followed.text
    assert "private-request-hash" not in followed.text


def test_stale_chat_worker_is_terminalized_with_one_neutral_receipt(corpus: DuckDBCorpus) -> None:
    app = create_app()
    email = "chat-stale-owner@example.test"
    client = _authed(app, email=email, org="Chat Stale")
    public_id, run_id = _create_run(client)
    org_id, user_id = _identity(client, email)
    turn_id = "chat_stale_worker_0001"
    stale = datetime.now(UTC) - timedelta(minutes=4)
    with db_session() as session:
        session.add(
            ChatTurnRow(
                turn_id=turn_id,
                org_id=org_id,
                run_id=run_id,
                user_id=user_id,
                question="Accepted request",
                request_hash="bound-hash",
                model="sixsentences-router",
                status="running",
                active_slot=1,
                action_id="chat_stale_worker_action_0001",
                created_at=stale,
                updated_at=stale,
                started_at=stale,
            )
        )
        session.add(
            ChatMessageRow(
                org_id=org_id,
                run_id=run_id,
                role="user",
                content="Accepted request",
                citations=[],
                payload={"turn_id": turn_id},
            )
        )
        session.add(
            ChatMessageRow(
                org_id=org_id,
                run_id=run_id,
                role="tool",
                content="Read the evidence",
                citations=[],
                payload={"turn_id": turn_id, "kind": "agent_work"},
            )
        )
    expired = client.get(f"/runs/{public_id}/chat/turns/active")
    assert expired.status_code == 200
    assert expired.json()["status"] == "failed"
    assert expired.json()["error_message"]
    history = client.get(f"/runs/{public_id}/chat").json()
    receipts = [
        message
        for message in history
        if message["role"] == "assistant"
        and (message.get("payload") or {}).get("turn_id") == turn_id
    ]
    assert len(receipts) == 1
    assert receipts[0]["payload"] == {
        "turn_id": turn_id,
        "kind": "turn_receipt",
        "status": "failed",
    }
    assert client.get(f"/runs/{public_id}/chat/turns/latest").json()["status"] == "failed"
    assert client.get(f"/runs/{public_id}/chat/turns/{turn_id}").json()["status"] == "failed"
    followed = client.get(f"/runs/{public_id}/chat/turns/{turn_id}/events/stream")
    stale_frames = _sse_event_frames(followed.text)
    assert [frame[1] for frame in stale_frames] == ["turn.failed"]
    assert stale_frames[0][2]["message"] == expired.json()["error_message"]
    history = client.get(f"/runs/{public_id}/chat").json()
    assert (
        sum(
            message["role"] == "assistant"
            and (message.get("payload") or {}).get("turn_id") == turn_id
            for message in history
        )
        == 1
    )
    with db_session() as session:
        row = session.scalar(select(ChatTurnRow).where(ChatTurnRow.turn_id == turn_id))
        assert row is not None
        assert row.active_slot is None
        terminal_events = session.scalars(
            select(ChatTurnEventRow).where(
                ChatTurnEventRow.turn_id == row.id, ChatTurnEventRow.event == "turn.failed"
            )
        ).all()
        assert len(terminal_events) == 1


def test_independent_chat_heartbeat_protects_a_long_running_turn(corpus: DuckDBCorpus) -> None:
    app = create_app()
    email = "chat-heartbeat-owner@example.test"
    client = _authed(app, email=email, org="Chat Heartbeat")
    public_id, run_id = _create_run(client)
    org_id, user_id = _identity(client, email)
    turn_id = "chat_heartbeat_worker_0001"
    stale = datetime.now(UTC) - timedelta(minutes=4)
    with db_session() as session:
        row = ChatTurnRow(
            turn_id=turn_id,
            org_id=org_id,
            run_id=run_id,
            user_id=user_id,
            question="Long call",
            request_hash="heartbeat-hash",
            model="sixsentences-router",
            status="running",
            active_slot=1,
            action_id="chat_heartbeat_worker_action_0001",
            created_at=stale,
            updated_at=stale,
            started_at=stale,
        )
        session.add(row)
        session.flush()
        row_id = row.id
    assert _heartbeat_chat_turn(row_id) is True
    active = client.get(f"/runs/{public_id}/chat/turns/active")
    assert active.status_code == 200
    assert active.json()["status"] == "running"


def test_parallel_terminal_recovery_writes_one_assistant_receipt(corpus: DuckDBCorpus) -> None:
    app = create_app()
    email = "chat-receipt-race@example.test"
    client = _authed(app, email=email, org="Chat Receipt Race")
    _, run_id = _create_run(client)
    org_id, user_id = _identity(client, email)
    turn_id = "chat_receipt_race_0001"
    now = datetime.now(UTC)
    with db_session() as session:
        turn = ChatTurnRow(
            turn_id=turn_id,
            org_id=org_id,
            run_id=run_id,
            user_id=user_id,
            question="Accepted request",
            request_hash="race-hash",
            model="sixsentences-router",
            status="failed",
            active_slot=None,
            action_id="chat_receipt_race_action_0001",
            error_message="Worker stopped",
            created_at=now,
            updated_at=now,
            started_at=now,
            finished_at=now,
        )
        session.add(turn)
        session.add(
            ChatMessageRow(
                org_id=org_id,
                run_id=run_id,
                role="user",
                content="Accepted request",
                citations=[],
                payload={"turn_id": turn_id},
            )
        )
        session.flush()
        row_id = turn.id
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def ensure_receipt() -> None:
        try:
            barrier.wait(timeout=2)
            _ensure_chat_turn_terminal_receipt(row_id)
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=ensure_receipt) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=3)
    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    with db_session() as session:
        receipts = session.scalars(
            select(ChatMessageRow).where(
                ChatMessageRow.run_id == run_id,
                ChatMessageRow.role == "assistant",
                ChatMessageRow.payload["turn_id"].as_string() == turn_id,
            )
        ).all()
    assert len(receipts) == 1


def test_parallel_terminal_event_recovery_allocates_one_final_sequence(
    corpus: DuckDBCorpus,
) -> None:
    app = create_app()
    email = "chat-event-race@example.test"
    client = _authed(app, email=email, org="Chat Event Race")
    _, run_id = _create_run(client)
    org_id, user_id = _identity(client, email)
    now = datetime.now(UTC)
    with db_session() as session:
        turn = ChatTurnRow(
            turn_id="chat_event_race_0001",
            org_id=org_id,
            run_id=run_id,
            user_id=user_id,
            question="Accepted request",
            request_hash="event-race-hash",
            model="sixsentences-router",
            status="failed",
            active_slot=None,
            action_id="chat_event_race_action_0001",
            error_message="Worker stopped",
            created_at=now,
            updated_at=now,
            started_at=now,
            finished_at=now,
        )
        session.add(turn)
        session.flush()
        row_id = turn.id
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def ensure_event() -> None:
        try:
            barrier.wait(timeout=2)
            with db_session() as session:
                row = session.scalar(
                    select(ChatTurnRow).where(ChatTurnRow.id == row_id).with_for_update()
                )
                assert row is not None
                ensure_chat_turn_terminal_event(session, row)
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=ensure_event) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=3)
    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    with db_session() as session:
        row = session.get(ChatTurnRow, row_id)
        events = session.scalars(
            select(ChatTurnEventRow).where(ChatTurnEventRow.turn_id == row_id)
        ).all()
    assert row is not None
    assert row.event_count == 1
    assert row.terminal_event_sequence == 1
    assert [(event.sequence, event.event) for event in events] == [(1, "turn.failed")]


def test_remounted_client_sees_progress_and_terminal_answer_without_resubmitting(
    corpus: DuckDBCorpus, monkeypatch
) -> None:
    from sixsentences_server.llm.mock import mock_pool

    provider_started = threading.Event()
    release_provider = threading.Event()

    def handler(model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return '{"action":"answer"}'
        if "Sources (cite by id" in prompt:
            provider_started.set()
            assert release_provider.wait(timeout=5)
            return "The remounted conversation receives its durable answer [W1]."
        if "Existing queries" in prompt:
            return '{"queries":[]}'
        return '{"inclusion_criteria":[],"exclusion_criteria":[],"query_string":"transformer"}'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    app = create_app()
    client = _authed(app, email="chat-remount@example.test", org="Chat Remount")
    worker = TestClient(app)
    worker.headers["Authorization"] = client.headers["Authorization"]
    public_id, _ = _create_run(client)
    turn_id = "chat_remount_worker_0001"
    result: dict[str, object] = {}

    def run_original_request() -> None:
        result["response"] = worker.post(
            f"/runs/{public_id}/chat/stream",
            json={"turn_id": turn_id, "question": "Explain the evidence."},
        )

    thread = threading.Thread(target=run_original_request)
    thread.start()
    try:
        assert provider_started.wait(timeout=5)
        active = client.get(f"/runs/{public_id}/chat/turns/active")
        assert active.status_code == 200
        assert active.json()["turn_id"] == turn_id
        history = client.get(f"/runs/{public_id}/chat").json()
        assert any(
            message["role"] == "user" and (message.get("payload") or {}).get("turn_id") == turn_id
            for message in history
        )
    finally:
        release_provider.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    latest = client.get(f"/runs/{public_id}/chat/turns/latest")
    assert latest.status_code == 200
    assert latest.json()["status"] == "completed"
    history = client.get(f"/runs/{public_id}/chat").json()
    answers = [
        message
        for message in history
        if message["role"] == "assistant"
        and (message.get("payload") or {}).get("turn_id") == turn_id
    ]
    assert len(answers) == 1
    assert "durable answer" in answers[0]["content"]


def test_chat_event_ledger_replays_exact_suffix_then_follows_without_rerun(
    corpus: DuckDBCorpus, monkeypatch
) -> None:
    """A remount gets byte-identical public frames and joins the live tail."""
    from sixsentences_server.chat.service import ChatAnswer
    from sixsentences_server.llm.mock import mock_pool

    partial_published = threading.Event()
    release_answer = threading.Event()
    answer_runs = 0
    answer_runs_lock = threading.Lock()
    turn_id = "chat_event_ledger_replay_0001"

    def fake_answer(
        session: Session, run: Run, _pool: Any, question: str, **kwargs: Any
    ) -> ChatAnswer:
        nonlocal answer_runs
        with answer_runs_lock:
            answer_runs += 1
        event_sink = kwargs["event_sink"]
        delta_sink = kwargs["delta_sink"]
        stream_reset_sink = kwargs["stream_reset_sink"]
        session.add(
            ChatMessageRow(
                org_id=run.org_id,
                run_id=run.id,
                role="user",
                content=question,
                citations=[],
                payload={"turn_id": turn_id},
            )
        )
        session.commit()
        event_sink(
            "tool.started",
            {
                "tool": "read_paper",
                "query": "W1",
                "label": "Reading the selected paper",
                "api_key": "must-never-leave-the-server",
            },
        )
        event_sink("activity", {"phase": "reading", "label": "Checking the relevant passage"})
        event_sink("reasoning.delta", {"reasoning": "Comparing the visible evidence."})
        delta_sink("This first visible answer passage is durable. ")
        stream_reset_sink()
        delta_sink("This replacement answer passage is durable. ")
        partial_published.set()
        assert release_answer.wait(timeout=5)
        event_sink(
            "tool.completed",
            {
                "tool": "read_paper",
                "query": "W1",
                "status": "completed",
                "label": "Paper passage checked",
            },
        )
        delta_sink("The final continuation is durable as well.")
        session.add(
            ChatMessageRow(
                org_id=run.org_id,
                run_id=run.id,
                role="assistant",
                content="This replacement answer passage is durable. The final continuation is durable as well.",
                citations=[],
                payload={"turn_id": turn_id},
            )
        )
        session.flush()
        event_sink(
            "answer.completed",
            {
                "label": "Answer grounded and ready",
                "sources": 1,
                "claims_checked": 0,
                "claims_flagged": 0,
            },
        )
        return ChatAnswer(
            answer="This replacement answer passage is durable. The final continuation is durable as well.",
            citations=[],
            sources_considered=1,
            tools_used=["read_paper"],
        )

    monkeypatch.setattr("sixsentences_server.api.app.answer_question", fake_answer)
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(lambda model, prompt: "{}"),
    )
    app = create_app()
    owner = _authed(app, email="chat-ledger@example.test", org="Chat Ledger")
    original_client = TestClient(app)
    original_client.headers["Authorization"] = owner.headers["Authorization"]
    reconnect_client = TestClient(app)
    reconnect_client.headers["Authorization"] = owner.headers["Authorization"]
    public_id, _ = _create_run(owner)
    responses: dict[str, Any] = {}
    errors: list[BaseException] = []

    def request_original() -> None:
        try:
            responses["original"] = original_client.post(
                f"/runs/{public_id}/chat/stream",
                json={"turn_id": turn_id, "question": "Explain the paper."},
            )
        except BaseException as exc:
            errors.append(exc)

    original_thread = threading.Thread(target=request_original)
    original_thread.start()
    assert partial_published.wait(timeout=5)
    with db_session() as session:
        turn = session.scalar(select(ChatTurnRow).where(ChatTurnRow.turn_id == turn_id))
        assert turn is not None
        durable_before_disconnect = session.scalars(
            select(ChatTurnEventRow)
            .where(ChatTurnEventRow.turn_id == turn.id)
            .order_by(ChatTurnEventRow.sequence)
        ).all()
    names_before_disconnect = [event.event for event in durable_before_disconnect]
    assert "tool.started" in names_before_disconnect
    assert "activity" in names_before_disconnect
    assert "reasoning.delta" in names_before_disconnect
    assert "answer.delta" in names_before_disconnect
    assert "answer.reset" in names_before_disconnect
    cursor = int(durable_before_disconnect[0].sequence)

    def request_reconnect() -> None:
        try:
            responses["reconnect"] = reconnect_client.get(
                f"/runs/{public_id}/chat/turns/{turn_id}/events/stream",
                headers={"Last-Event-ID": str(cursor)},
            )
        except BaseException as exc:
            errors.append(exc)

    reconnect_thread = threading.Thread(target=request_reconnect)
    reconnect_thread.start()
    release_answer.set()
    original_thread.join(timeout=8)
    reconnect_thread.join(timeout=8)
    assert not errors
    assert not original_thread.is_alive()
    assert not reconnect_thread.is_alive()
    assert answer_runs == 1
    original = responses["original"]
    reconnect = responses["reconnect"]
    assert original.status_code == 200
    assert reconnect.status_code == 200
    assert reconnect.headers["x-chat-turn-id"] == turn_id
    original_frames = _sse_event_frames(original.text)
    reconnect_frames = _sse_event_frames(reconnect.text)
    expected_suffix = [frame for frame in original_frames if frame[0] > cursor]
    assert [frame[3] for frame in reconnect_frames] == [frame[3] for frame in expected_suffix]
    assert [frame[0] for frame in reconnect_frames] == list(
        range(cursor + 1, reconnect_frames[-1][0] + 1)
    )
    assert reconnect_frames[-1][1] == "turn.completed"
    assert reconnect_frames[-1][2]["answer"]["answer"].endswith("durable as well.")
    started_payload = next(frame[2] for frame in reconnect_frames if frame[1] == "tool.started")
    assert started_payload["api_key"] == "[protected]"
    assert "must-never-leave-the-server" not in original.text
    status = owner.get(f"/runs/{public_id}/chat/turns/{turn_id}").json()
    assert status["answer"] == reconnect_frames[-1][2]["answer"]
    assert status["last_event_id"] == reconnect_frames[-1][0]
    acknowledged = owner.get(
        f"/runs/{public_id}/chat/turns/{turn_id}/events/stream",
        headers={"Last-Event-ID": str(reconnect_frames[-1][0])},
    )
    assert acknowledged.status_code == 200
    assert _sse_event_frames(acknowledged.text) == []
    invalid_cursor = owner.get(
        f"/runs/{public_id}/chat/turns/{turn_id}/events/stream",
        headers={"Last-Event-ID": "999999999"},
    )
    assert [frame[3] for frame in _sse_event_frames(invalid_cursor.text)] == [
        frame[3] for frame in original_frames
    ]
