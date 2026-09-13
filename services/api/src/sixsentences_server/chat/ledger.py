"""Append-only, replayable public event ledger for Quick Answer turns."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sixsentences_server.agent.events import (
    safe_event_value,
    safe_model_observation_value,
)
from sixsentences_server.core.db import ChatTurnEventRow, ChatTurnRow

CHAT_TURN_TERMINAL = frozenset({"cancelled", "completed", "failed"})
_EVENT_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,99}")


def append_chat_turn_event(
    session: Session,
    row_id: int,
    *,
    event: str,
    payload: Mapping[str, Any] | None = None,
    trusted_payload: bool = False,
) -> dict[str, Any]:
    """Append one public frame under the turn row's sequence lock."""

    row = session.scalar(
        select(ChatTurnRow)
        .where(ChatTurnRow.id == row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise RuntimeError("chat turn is no longer available")
    return append_chat_turn_event_for_row(
        session,
        row,
        event=event,
        payload=payload,
        trusted_payload=trusted_payload,
    )


def append_chat_turn_event_for_row(
    session: Session,
    row: ChatTurnRow,
    *,
    event: str,
    payload: Mapping[str, Any] | None = None,
    trusted_payload: bool = False,
) -> dict[str, Any]:
    """Append while the caller already owns the turn row's write lock."""

    event_name = str(event or "").strip()
    if _EVENT_NAME.fullmatch(event_name) is None:
        event_name = "activity"
    if row.status in CHAT_TURN_TERMINAL and event_name != f"turn.{row.status}":
        raise RuntimeError("cannot append progress after chat turn finished")
    terminal_event = event_name in {
        "turn.cancelled",
        "turn.completed",
        "turn.failed",
    }
    existing = chat_turn_event_for_name(session, row.id, event_name) if terminal_event else None
    if existing is not None:
        return dict(existing.payload or {})

    raw_payload = dict(payload or {})
    public_payload: Any = (
        safe_model_observation_value(raw_payload)
        if trusted_payload
        else safe_event_value(raw_payload)
    )
    clean_payload = public_payload if isinstance(public_payload, dict) else {}
    now = datetime.now(UTC)
    # SQLite ignores SELECT FOR UPDATE, while provider callbacks and reconnect
    # readers may use different sessions. Incrementing in SQL keeps allocation
    # monotonic across both SQLite and PostgreSQL without a process-local lock.
    if terminal_event:
        sequence = session.execute(
            update(ChatTurnRow)
            .where(
                ChatTurnRow.id == row.id,
                ChatTurnRow.terminal_event_sequence.is_(None),
            )
            .values(
                event_count=ChatTurnRow.event_count + 1,
                terminal_event_sequence=ChatTurnRow.event_count + 1,
                updated_at=now,
            )
            .returning(ChatTurnRow.terminal_event_sequence)
        ).scalar_one_or_none()
        if sequence is None:
            existing = chat_turn_event_for_name(session, row.id, event_name)
            if existing is not None:
                return dict(existing.payload or {})
            sequence = session.scalar(
                select(ChatTurnRow.terminal_event_sequence).where(ChatTurnRow.id == row.id)
            )
            if sequence is None:
                raise RuntimeError("chat turn terminal event could not be allocated")
    else:
        sequence = session.execute(
            update(ChatTurnRow)
            .where(ChatTurnRow.id == row.id)
            .values(
                event_count=ChatTurnRow.event_count + 1,
                updated_at=now,
            )
            .returning(ChatTurnRow.event_count)
        ).scalar_one()
    body = {
        **clean_payload,
        "id": sequence,
        "event": event_name,
        "turn_id": row.turn_id,
        "created_at": now.isoformat(),
    }
    session.add(
        ChatTurnEventRow(
            org_id=row.org_id,
            run_id=row.run_id,
            turn_id=row.id,
            sequence=sequence,
            event=event_name,
            payload=body,
            created_at=now,
        )
    )
    row.event_count = int(sequence)
    if terminal_event:
        row.terminal_event_sequence = int(sequence)
    row.updated_at = now
    return body


def chat_turn_events_after(
    session: Session,
    row_id: int,
    *,
    org_id: int,
    run_id: int,
    sequence: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read one bounded owner-resolved replay page after an SSE cursor."""

    rows: Sequence[ChatTurnEventRow] = session.scalars(
        select(ChatTurnEventRow)
        .where(
            ChatTurnEventRow.turn_id == row_id,
            ChatTurnEventRow.org_id == org_id,
            ChatTurnEventRow.run_id == run_id,
            ChatTurnEventRow.sequence > max(0, sequence),
        )
        .order_by(ChatTurnEventRow.sequence)
        .limit(max(1, min(limit, 500)))
    ).all()
    return [dict(row.payload or {}) for row in rows]


def chat_turn_event_for_name(
    session: Session,
    row_id: int,
    event: str,
) -> ChatTurnEventRow | None:
    """Return the first matching lifecycle frame for idempotent terminal writes."""

    return session.scalar(
        select(ChatTurnEventRow)
        .where(
            ChatTurnEventRow.turn_id == row_id,
            ChatTurnEventRow.event == event,
        )
        .order_by(ChatTurnEventRow.sequence)
        .limit(1)
    )


def ensure_chat_turn_terminal_event(
    session: Session,
    row: ChatTurnRow,
) -> dict[str, Any] | None:
    """Backfill exactly one final frame for a terminal or legacy turn."""

    if row.status not in CHAT_TURN_TERMINAL:
        return None
    event = f"turn.{row.status}"
    existing = chat_turn_event_for_name(session, row.id, event)
    if existing is not None:
        return dict(existing.payload or {})
    if row.status == "completed":
        payload: dict[str, Any] = {"answer": dict(row.answer or {})}
        trusted_payload = True
    elif row.status == "cancelled":
        payload = {"status": "cancelled"}
        trusted_payload = False
    else:
        payload = {"message": row.error_message or "This turn could not be completed."}
        trusted_payload = False
    return append_chat_turn_event_for_row(
        session,
        row,
        event=event,
        payload=payload,
        trusted_payload=trusted_payload,
    )
