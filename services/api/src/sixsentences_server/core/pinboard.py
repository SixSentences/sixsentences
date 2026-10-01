"""Bounded, creator-private notes with optimistic concurrency control.

Older stored JSON remains readable with size=1.0 and is not rewritten on read.
New writes/receipts contain normalized sizes. This is forward-only for API code:
pre-size readers reject that extra JSON field, so rollback needs a compatible
reader rather than destructive removal of users' saved sizes.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sixsentences_server.core.db import PersonalPinboardRow


class PinboardNote(BaseModel):
    """Plain text and layout only; no HTML, links, attachments or model context."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    id: str = Field(max_length=36)
    text: str = Field(max_length=2000)
    color: Literal["butter", "sage", "rose", "sky", "paper"]
    shape: Literal["note", "card", "circle"]
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    rotation: float = Field(ge=-12, le=12)
    size: float = Field(default=1.0, ge=0.6, le=2.4)

    @field_validator("id")
    @classmethod
    def canonical_id(cls, value: str) -> str:
        """Canonicalize UUIDs so alternate spellings cannot duplicate a note."""
        return str(UUID(value))


class PinboardState(BaseModel):
    """A complete desk plus its server-owned revision (zero means not saved yet)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    revision: int = Field(ge=0, le=2_147_483_647)
    notes: list[PinboardNote] = Field(max_length=24)

    @field_validator("notes")
    @classmethod
    def unique_ids(cls, notes: list[PinboardNote]) -> list[PinboardNote]:
        """Reject duplicate identities before any database write."""
        if len({note.id for note in notes}) != len(notes):
            raise ValueError("note IDs must be unique")
        return notes


class PinboardConflictError(Exception):
    """The client edited a stale revision; its draft must not overwrite it."""


def read_pinboard(session: Session, *, user_id: int, org_id: int) -> PinboardState:
    """Read only the authenticated creator's desk, including within a team."""
    row = session.scalar(
        select(PersonalPinboardRow).where(
            PersonalPinboardRow.user_id == user_id, PersonalPinboardRow.org_id == org_id
        )
    )
    if row is None:
        return PinboardState(revision=0, notes=[])
    return PinboardState.model_validate({"revision": row.revision, "notes": row.notes})


def write_pinboard(
    session: Session, *, user_id: int, org_id: int, state: PinboardState
) -> PinboardState:
    """Compare-and-swap a bounded desk, atomically including the first write.

    Existing sizes survive legacy clients that omit the field; only new notes
    default to 1.0. Explicit size values remain deliberate revision-bound edits.
    The API receipt and JSON storage always use the same normalized notes.
    """
    if state.revision == 2_147_483_647:
        raise PinboardConflictError
    normalized_notes = state.notes
    if state.revision and any("size" not in note.model_fields_set for note in state.notes):
        # Cached legacy clients omit size even after reading a resized note.
        # Preserve the value only from this creator's exact requested revision;
        # the later compare-and-swap still rejects a concurrent newer write.
        saved_notes = session.scalar(
            select(PersonalPinboardRow.notes).where(
                PersonalPinboardRow.user_id == user_id,
                PersonalPinboardRow.org_id == org_id,
                PersonalPinboardRow.revision == state.revision,
            )
        )
        if saved_notes is None:
            raise PinboardConflictError
        previous = PinboardState.model_validate({"revision": state.revision, "notes": saved_notes})
        sizes = {note.id: note.size for note in previous.notes}
        normalized_notes = [
            note.model_copy(update={"size": sizes.get(note.id, 1.0)})
            if "size" not in note.model_fields_set else note
            for note in state.notes
        ]
    notes = [note.model_dump(mode="json") for note in normalized_notes]
    next_revision = state.revision + 1
    if state.revision == 0:
        try:
            with session.begin_nested():
                session.add(
                    PersonalPinboardRow(
                        user_id=user_id, org_id=org_id, revision=next_revision, notes=notes
                    )
                )
                session.flush()
        except IntegrityError as exc:
            raise PinboardConflictError from exc
    else:
        saved = session.execute(
            update(PersonalPinboardRow)
            .where(
                PersonalPinboardRow.user_id == user_id,
                PersonalPinboardRow.org_id == org_id,
                PersonalPinboardRow.revision == state.revision,
            )
            .values(notes=notes, revision=next_revision)
            .returning(PersonalPinboardRow.revision)
        ).scalar_one_or_none()
        if saved is None:
            raise PinboardConflictError
    return PinboardState(revision=next_revision, notes=normalized_notes)
