"""Durable specialist turn and event-ledger contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from sixsentences_server.agent.ledger import (
    AgentTurnActiveError,
    AgentTurnConflictError,
    AgentTurnOwnershipError,
    agent_events_after,
    agent_turn_cancelled,
    agent_turn_request_hash,
    append_agent_event,
    claim_agent_turn,
    finish_agent_turn,
    request_agent_turn_cancel,
    reserve_agent_turn,
)
from sixsentences_server.core.db import AgentTurnRow, Base, Org, User


@pytest.fixture
def ledger_session() -> Session:
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        org = Org(name="agent-ledger-test")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="agent-ledger@example.test",
            password_hash="unused",
        )
        session.add(user)
        session.commit()
        yield session
    engine.dispose()


def _identity(session: Session) -> tuple[int, int]:
    org = session.scalar(sa.select(Org).where(Org.name == "agent-ledger-test"))
    user = session.scalar(sa.select(User).where(User.email == "agent-ledger@example.test"))
    assert org is not None and user is not None
    return org.id, user.id


def test_turn_reservation_is_idempotent_and_resource_exclusive(
    ledger_session: Session,
) -> None:
    org_id, user_id = _identity(ledger_session)
    request_hash = agent_turn_request_hash({"message": "Improve the title"})
    first = reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="manuscript",
        resource_id="doc123456",
        turn_id="turn_12345678",
        request_hash=request_hash,
    )
    replay = reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="manuscript",
        resource_id="doc123456",
        turn_id="turn_12345678",
        request_hash=request_hash,
    )

    assert first.created
    assert not replay.created
    assert replay.row_id == first.row_id

    with pytest.raises(AgentTurnConflictError):
        reserve_agent_turn(
            ledger_session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="manuscript",
            resource_id="doc123456",
            turn_id="turn_12345678",
            request_hash=agent_turn_request_hash({"message": "Different"}),
        )

    with pytest.raises(AgentTurnActiveError) as exc_info:
        reserve_agent_turn(
            ledger_session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="manuscript",
            resource_id="doc123456",
            turn_id="turn_87654321",
            request_hash=agent_turn_request_hash({"message": "Another tab"}),
        )
    assert exc_info.value.turn_id == "turn_12345678"


def test_turn_reservation_is_owned_by_its_creator(ledger_session: Session) -> None:
    org_id, user_id = _identity(ledger_session)
    other_user = User(
        org_id=org_id,
        email="other-agent-ledger@example.test",
        password_hash="unused",
    )
    ledger_session.add(other_user)
    ledger_session.flush()
    request_hash = agent_turn_request_hash({"question": "Inspect data"})
    reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="dataset",
        resource_id="data123456",
        turn_id="turn_private12",
        request_hash=request_hash,
    )

    with pytest.raises(AgentTurnOwnershipError, match="not found"):
        reserve_agent_turn(
            ledger_session,
            org_id=org_id,
            user_id=other_user.id,
            resource_kind="dataset",
            resource_id="different-resource",
            turn_id="turn_private12",
            request_hash=agent_turn_request_hash({"question": "Different"}),
        )

    with pytest.raises(AgentTurnActiveError) as exc_info:
        reserve_agent_turn(
            ledger_session,
            org_id=org_id,
            user_id=other_user.id,
            resource_kind="dataset",
            resource_id="data123456",
            turn_id="turn_other123",
            request_hash=agent_turn_request_hash({"question": "Another tab"}),
        )
    assert exc_info.value.turn_id is None


def test_event_ledger_replays_monotonic_safe_events(ledger_session: Session) -> None:
    org_id, user_id = _identity(ledger_session)
    reservation = reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="dataset",
        resource_id="data123456",
        turn_id="turn_abcdefgh",
        request_hash=agent_turn_request_hash({"question": "Inspect data"}),
    )
    claim_agent_turn(ledger_session, reservation.row_id)
    first = append_agent_event(
        ledger_session,
        reservation.row_id,
        event="turn.started",
        payload={"api_key": "must-not-leak", "status": "running"},
    )
    second = append_agent_event(
        ledger_session,
        reservation.row_id,
        event="tool.completed",
        payload={"tool": "dataset.inspect", "output": {"rows": 4}},
    )
    ledger_session.commit()

    assert first["id"] == 1
    assert first["api_key"] == "[protected]"
    assert second["id"] == 2
    assert agent_events_after(ledger_session, reservation.row_id, sequence=1) == [second]


def test_cancel_wins_terminal_race_and_releases_resource_slot(
    ledger_session: Session,
) -> None:
    org_id, user_id = _identity(ledger_session)
    reservation = reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="survey",
        resource_id="survey1234",
        turn_id="turn_cancel12",
        request_hash=agent_turn_request_hash({"question": "Update"}),
    )
    claim_agent_turn(ledger_session, reservation.row_id)

    assert request_agent_turn_cancel(ledger_session, reservation.row_id) == ("cancel_requested")
    assert (
        finish_agent_turn(
            ledger_session,
            reservation.row_id,
            status="completed",
            result={"answer": "Too late"},
        )
        == "cancelled"
    )
    ledger_session.commit()

    replacement = reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="survey",
        resource_id="survey1234",
        turn_id="turn_recover1",
        request_hash=agent_turn_request_hash({"question": "Continue"}),
    )
    assert replacement.created


def test_stale_worker_is_failed_and_no_longer_blocks_resource(
    ledger_session: Session,
) -> None:
    org_id, user_id = _identity(ledger_session)
    reservation = reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="interview",
        resource_id="interview1",
        turn_id="turn_stale123",
        request_hash=agent_turn_request_hash({"question": "Summarize"}),
    )
    row = ledger_session.get(AgentTurnRow, reservation.row_id)
    assert row is not None
    row.status = "running"
    row.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    ledger_session.commit()

    replacement = reserve_agent_turn(
        ledger_session,
        org_id=org_id,
        user_id=user_id,
        resource_kind="interview",
        resource_id="interview1",
        turn_id="turn_fresh123",
        request_hash=agent_turn_request_hash({"question": "Continue"}),
        stale_after=timedelta(minutes=5),
    )

    assert replacement.created
    ledger_session.refresh(row)
    assert row.status == "failed"
    assert row.active_slot is None
    assert agent_turn_cancelled(ledger_session, reservation.row_id)

    terminal_events = agent_events_after(
        ledger_session,
        reservation.row_id,
    )
    assert [event["event"] for event in terminal_events] == ["turn.failed"]

    with pytest.raises(RuntimeError, match="cannot append progress"):
        append_agent_event(
            ledger_session,
            reservation.row_id,
            event="tool.completed",
            payload={"tool": "interview.summarize"},
        )
