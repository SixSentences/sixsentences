"""Durable turn control and replayable events for specialist agents."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.agent.events import safe_event_value
from sixsentences_server.core.db import (
    AgentEventRow,
    AgentTurnRow,
    CapacityReservationRow,
)

AGENT_TURN_TERMINAL = frozenset({"cancelled", "completed", "failed"})
AGENT_TURN_STALE_AFTER = timedelta(minutes=3)
_TURN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}")


class AgentTurnConflictError(ValueError):
    """The requested id is already bound to different turn input."""


class AgentTurnOwnershipError(LookupError):
    """The requested id belongs to a different user in the organization."""


class AgentTurnActiveError(RuntimeError):
    """A different turn currently owns the resource's active slot."""

    def __init__(self, turn_id: str | None) -> None:
        super().__init__("another agent turn is already running")
        self.turn_id = turn_id


class AgentTurnLeaseLostError(RuntimeError):
    """The current specialist worker no longer owns its durable turn lease."""


_CURRENT_AGENT_TURN_ROW_ID: ContextVar[int | None] = ContextVar(
    "sixsentences_agent_turn_row_id",
    default=None,
)


@dataclass(frozen=True)
class AgentTurnReservation:
    """Result of idempotently reserving one specialist turn."""

    row_id: int
    turn_id: str
    status: str
    created: bool


def agent_turn_action_id(org_id: int, turn_id: str, phase: str = "main") -> str:
    """Return one durable capacity action owned by a specialist-turn phase."""

    material = f"specialist:{org_id}:{turn_id}"
    if phase != "main":
        material = f"{material}:{phase}"
    return hashlib.sha256(material.encode()).hexdigest()


def _release_agent_turn_action(
    session: Session,
    row: AgentTurnRow,
    *,
    status: str,
    now: datetime,
) -> None:
    """Release an unfinished provider/credit hold with the turn lifecycle."""

    actions = session.scalars(
        select(CapacityReservationRow).where(
            CapacityReservationRow.action_id.in_(
                (
                    agent_turn_action_id(row.org_id, row.turn_id),
                    agent_turn_action_id(row.org_id, row.turn_id, "result_review"),
                )
            )
        )
    ).all()
    for action in actions:
        if action.status != "active":
            continue
        action.remaining_credits = 0
        action.status = status
        action.released_at = now


@contextmanager
def agent_turn_lease_scope(row_id: int) -> Iterator[None]:
    """Bind a specialist worker to the row that fences its durable writes."""

    token = _CURRENT_AGENT_TURN_ROW_ID.set(row_id)
    try:
        yield
    finally:
        _CURRENT_AGENT_TURN_ROW_ID.reset(token)


def current_agent_turn_row_id() -> int | None:
    """Return the specialist lease bound to the current worker, if any."""

    return _CURRENT_AGENT_TURN_ROW_ID.get()


def fence_agent_turn(session: Session) -> None:
    """Lock and verify the current worker's active lease before a commit.

    The row lock is retained by ``session`` until its transaction ends. A
    concurrent stale-turn takeover must therefore happen either before this
    check (and the worker is rejected) or after the guarded commit. Direct,
    non-streaming specialist calls have no bound lease and remain unchanged.
    """

    row_id = _CURRENT_AGENT_TURN_ROW_ID.get()
    if row_id is None:
        return
    row = session.scalar(
        select(AgentTurnRow)
        .where(AgentTurnRow.id == row_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if row is None or row.status != "running" or row.active_slot != 1:
        raise AgentTurnLeaseLostError("specialist agent turn lease was lost")


def agent_turn_request_hash(payload: Mapping[str, Any]) -> str:
    """Return the stable request identity used for idempotent retries."""

    canonical = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def reserve_agent_turn(
    session: Session,
    *,
    org_id: int,
    user_id: int,
    resource_kind: str,
    resource_id: str,
    turn_id: str,
    request_hash: str,
    stale_after: timedelta = AGENT_TURN_STALE_AFTER,
) -> AgentTurnReservation:
    """Reserve a resource slot or return the matching existing turn.

    A stale in-process worker cannot be resumed safely because provider state
    is not durable.  Its lease is therefore terminalized honestly and its slot
    released, allowing the user to start a fresh turn without manual cleanup.
    """

    if _TURN_ID_PATTERN.fullmatch(turn_id) is None:
        raise ValueError("invalid agent turn id")
    kind = resource_kind.strip()
    resolved_resource_id = resource_id.strip()
    if not kind or not resolved_resource_id:
        raise ValueError("agent turn resource is required")

    now = datetime.now(UTC)
    existing = session.scalar(
        select(AgentTurnRow)
        .where(
            AgentTurnRow.org_id == org_id,
            AgentTurnRow.turn_id == turn_id,
        )
        .with_for_update()
    )
    if existing is not None:
        # A client-stable turn id is also a user-owned idempotency key.  Check
        # ownership before request identity so another member cannot probe the
        # bound resource or payload through conflict responses.
        if existing.user_id != user_id:
            raise AgentTurnOwnershipError("agent turn not found")
        if (
            existing.resource_kind != kind
            or existing.resource_id != resolved_resource_id
            or existing.request_hash != request_hash
        ):
            raise AgentTurnConflictError("turn id is already bound to another request or resource")
        _expire_stale_turn(session, existing, now=now, stale_after=stale_after)
        return AgentTurnReservation(existing.id, existing.turn_id, existing.status, False)

    active = session.scalar(
        select(AgentTurnRow)
        .where(
            AgentTurnRow.org_id == org_id,
            AgentTurnRow.resource_kind == kind,
            AgentTurnRow.resource_id == resolved_resource_id,
            AgentTurnRow.active_slot == 1,
        )
        .with_for_update()
    )
    if active is not None:
        _expire_stale_turn(session, active, now=now, stale_after=stale_after)
        if active.active_slot == 1:
            visible_turn_id = active.turn_id if active.user_id == user_id else None
            raise AgentTurnActiveError(visible_turn_id)

    row = AgentTurnRow(
        turn_id=turn_id,
        org_id=org_id,
        user_id=user_id,
        resource_kind=kind,
        resource_id=resolved_resource_id,
        request_hash=request_hash,
        status="queued",
        active_slot=1,
        event_count=0,
        heartbeat_at=now,
    )
    session.add(row)
    session.flush()
    return AgentTurnReservation(row.id, row.turn_id, row.status, True)


def claim_agent_turn(session: Session, row_id: int) -> AgentTurnRow:
    """Claim a queued turn for exactly one worker."""

    row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.id == row_id).with_for_update())
    if row is None:
        raise RuntimeError("agent turn is no longer available")
    if row.status in {"cancel_requested", "cancelled"}:
        raise RuntimeError("agent turn was cancelled before start")
    if row.status != "queued":
        raise RuntimeError("agent turn was already claimed")
    now = datetime.now(UTC)
    row.status = "running"
    row.started_at = now
    row.heartbeat_at = now
    return row


def append_agent_event(
    session: Session,
    row_id: int,
    *,
    event: str,
    payload: Mapping[str, Any] | None = None,
    trusted_payload: bool = False,
) -> dict[str, Any]:
    """Append one monotonic event and refresh the turn heartbeat."""

    row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.id == row_id).with_for_update())
    if row is None:
        raise RuntimeError("agent turn is no longer available")
    if row.status in AGENT_TURN_TERMINAL and event != f"turn.{row.status}":
        raise RuntimeError("cannot append progress after agent turn finished")
    sequence = row.event_count + 1
    now = datetime.now(UTC)
    raw_payload = dict(payload or {})
    clean_payload = raw_payload if trusted_payload else safe_event_value(raw_payload)
    body = {
        **(clean_payload if isinstance(clean_payload, dict) else {}),
        "id": sequence,
        "event": event,
        "turn_id": row.turn_id,
        "kind": row.resource_kind,
        "created_at": now.isoformat(),
    }
    session.add(
        AgentEventRow(
            org_id=row.org_id,
            turn_id=row.id,
            sequence=sequence,
            event=event,
            payload=body,
            created_at=now,
        )
    )
    row.event_count = sequence
    row.heartbeat_at = now
    return body


def agent_events_after(
    session: Session,
    row_id: int,
    *,
    sequence: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read an ordered replay page after a client cursor."""

    rows: Sequence[AgentEventRow] = session.scalars(
        select(AgentEventRow)
        .where(
            AgentEventRow.turn_id == row_id,
            AgentEventRow.sequence > max(0, sequence),
        )
        .order_by(AgentEventRow.sequence)
        .limit(max(1, min(limit, 500)))
    ).all()
    return [dict(row.payload or {}) for row in rows]


def request_agent_turn_cancel(session: Session, row_id: int) -> str:
    """Persist a cooperative stop request and release queued turns immediately."""

    row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.id == row_id).with_for_update())
    if row is None:
        raise LookupError("agent turn not found")
    if row.status in AGENT_TURN_TERMINAL:
        return row.status
    now = datetime.now(UTC)
    row.cancel_requested_at = now
    if row.status == "queued":
        row.status = "cancelled"
        row.active_slot = None
        row.finished_at = now
        _release_agent_turn_action(session, row, status="cancelled", now=now)
    else:
        row.status = "cancel_requested"
    return row.status


def finish_agent_turn(
    session: Session,
    row_id: int,
    *,
    status: str,
    result: Mapping[str, Any] | None = None,
    error_message: str | None = None,
) -> str:
    """Persist a terminal state without overwriting a concurrent stop."""

    if status not in AGENT_TURN_TERMINAL:
        raise ValueError("agent turn status must be terminal")
    row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.id == row_id).with_for_update())
    if row is None:
        return "failed"
    if row.status in AGENT_TURN_TERMINAL:
        return row.status
    resolved = "cancelled" if row.status == "cancel_requested" else status
    row.status = resolved
    row.active_slot = None
    now = datetime.now(UTC)
    row.finished_at = now
    _release_agent_turn_action(session, row, status=resolved, now=now)
    if resolved == "completed":
        row.result = dict(result or {})
        row.error_message = None
    elif resolved == "failed":
        row.error_message = str(error_message or "")[:2_000]
    return resolved


def agent_turn_cancelled(session: Session, row_id: int) -> bool:
    """Return whether the worker has lost its active lease and must stop."""

    status = session.scalar(select(AgentTurnRow.status).where(AgentTurnRow.id == row_id))
    return status is None or status == "cancel_requested" or status in AGENT_TURN_TERMINAL


def heartbeat_agent_turn(session: Session, row_id: int) -> bool:
    """Refresh a live worker lease without adding a user-visible event."""

    row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.id == row_id).with_for_update())
    if row is None or row.status not in {"running", "cancel_requested"}:
        return False
    row.heartbeat_at = datetime.now(UTC)
    return True


def expire_stale_agent_turn(
    session: Session,
    row_id: int,
    *,
    stale_after: timedelta = AGENT_TURN_STALE_AFTER,
) -> bool:
    """Fail an orphaned active lease so reconnect streams can terminate."""

    row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.id == row_id).with_for_update())
    if row is None:
        return False
    previous = row.status
    _expire_stale_turn(session, row, now=datetime.now(UTC), stale_after=stale_after)
    return previous != row.status and row.status == "failed"


def _expire_stale_turn(
    session: Session,
    row: AgentTurnRow,
    *,
    now: datetime,
    stale_after: timedelta,
) -> None:
    if row.status not in {"queued", "running", "cancel_requested"}:
        return
    heartbeat = row.heartbeat_at or row.started_at or row.created_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    if now - heartbeat <= stale_after:
        return
    row.status = "failed"
    row.active_slot = None
    row.error_message = (
        "The previous agent worker stopped reporting progress. Start a new turn to continue."
    )
    row.finished_at = now
    _release_agent_turn_action(session, row, status="expired", now=now)
    # Slot takeover is also a terminal transition. Persist its final frame in
    # the same transaction so an already attached or later bodyless replay can
    # finish instead of waiting forever for an event no worker can emit.
    sequence = row.event_count + 1
    body = {
        "message": row.error_message,
        "id": sequence,
        "event": "turn.failed",
        "turn_id": row.turn_id,
        "kind": row.resource_kind,
        "created_at": now.isoformat(),
    }
    session.add(
        AgentEventRow(
            org_id=row.org_id,
            turn_id=row.id,
            sequence=sequence,
            event="turn.failed",
            payload=body,
            created_at=now,
        )
    )
    row.event_count = sequence
    row.heartbeat_at = now
