"""Tamper-evident, content-free journal for post-snapshot account erasures.

The product database and user-file snapshots deliberately do not contain this
journal. Recovery restores the newest journal independently and replays every
entry before public traffic is re-enabled. Records contain internal numeric
identifiers and an HMAC of the normalized email address, never the address or
other user content itself.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sixsentences_server.config import Settings

ErasureSubject = Literal["workspace", "orphan_workspace", "member"]
_FORMAT = 1
_EMPTY_CHAIN = "0" * 64
_ALLOWED_REASONS = {
    "account_request",
    "admin_request",
    "unverified_signup_expired",
}


class ErasureLedgerError(RuntimeError):
    """The erasure journal is unavailable, malformed or unauthenticated."""


@dataclass(frozen=True)
class ErasureEvent:
    """One verified request that must remain effective after any restore."""

    event_id: str
    recorded_at: str
    subject: ErasureSubject
    reason: str
    org_id: int | None
    user_id: int | None
    email_digest: str
    previous_signature: str
    signature: str


def _key(settings: Settings) -> bytes:
    value = settings.erasure_ledger_hmac_key.encode("utf-8")
    if len(value) < 32:
        raise ErasureLedgerError("SIX_ERASURE_LEDGER_HMAC_KEY must contain at least 32 bytes")
    return hmac.new(value, b"sixsentences-erasure-ledger-v1", hashlib.sha256).digest()


def email_digest(email: str, settings: Settings) -> str:
    """Return a domain-separated, irreversible identity guard."""

    normalized = email.strip().casefold().encode("utf-8")
    return hmac.new(_key(settings), b"email\x00" + normalized, hashlib.sha256).hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _signed_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "signature"}


def _signature(event: dict[str, Any], key: bytes) -> str:
    return hmac.new(key, _canonical(_signed_payload(event)), hashlib.sha256).hexdigest()


def _parse_lines(raw: bytes, key: bytes) -> list[ErasureEvent]:
    events: list[ErasureEvent] = []
    event_ids: set[str] = set()
    previous = _EMPTY_CHAIN
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            raise ErasureLedgerError(f"blank line in erasure ledger at {line_number}")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ErasureLedgerError(
                f"invalid JSON in erasure ledger at line {line_number}"
            ) from exc
        if not isinstance(payload, dict):
            raise ErasureLedgerError(f"invalid record in erasure ledger at line {line_number}")
        required = {
            "format",
            "event_id",
            "recorded_at",
            "subject",
            "reason",
            "org_id",
            "user_id",
            "email_digest",
            "previous_signature",
            "signature",
        }
        if set(payload) != required or payload.get("format") != _FORMAT:
            raise ErasureLedgerError(f"unsupported record in erasure ledger at line {line_number}")
        if payload["subject"] not in {
            "workspace",
            "orphan_workspace",
            "member",
        }:
            raise ErasureLedgerError(f"invalid subject in erasure ledger at line {line_number}")
        if payload["reason"] not in _ALLOWED_REASONS:
            raise ErasureLedgerError(f"invalid reason in erasure ledger at line {line_number}")
        org_id = payload["org_id"]
        user_id = payload["user_id"]
        if org_id is not None and (type(org_id) is not int or org_id <= 0):
            raise ErasureLedgerError(f"invalid org id in erasure ledger at line {line_number}")
        if user_id is not None and (type(user_id) is not int or user_id <= 0):
            raise ErasureLedgerError(f"invalid user id in erasure ledger at line {line_number}")
        if payload["subject"] in {"workspace", "member"} and (org_id is None or user_id is None):
            raise ErasureLedgerError(f"account record lacks identifiers at line {line_number}")
        if payload["subject"] == "orphan_workspace" and (org_id is None or user_id is not None):
            raise ErasureLedgerError(
                f"orphan workspace record has invalid identifiers at line {line_number}"
            )
        recorded_at = payload["recorded_at"]
        if not isinstance(recorded_at, str):
            raise ErasureLedgerError(f"invalid timestamp in erasure ledger at line {line_number}")
        try:
            parsed_at = datetime.fromisoformat(recorded_at)
        except ValueError as exc:
            raise ErasureLedgerError(
                f"invalid timestamp in erasure ledger at line {line_number}"
            ) from exc
        if parsed_at.tzinfo is None:
            raise ErasureLedgerError(
                f"timestamp lacks timezone in erasure ledger at line {line_number}"
            )
        if payload["previous_signature"] != previous:
            raise ErasureLedgerError(
                f"broken signature chain in erasure ledger at line {line_number}"
            )
        expected = _signature(payload, key)
        if not hmac.compare_digest(str(payload["signature"]), expected):
            raise ErasureLedgerError(f"invalid signature in erasure ledger at line {line_number}")
        digest = str(payload["email_digest"])
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ErasureLedgerError(
                f"invalid email digest in erasure ledger at line {line_number}"
            )
        event_id = str(payload["event_id"])
        if len(event_id) != 32 or any(
            character not in "0123456789abcdef" for character in event_id
        ):
            raise ErasureLedgerError(f"invalid event id in erasure ledger at line {line_number}")
        if event_id in event_ids:
            raise ErasureLedgerError(f"duplicate event id in erasure ledger at line {line_number}")
        event_ids.add(event_id)
        events.append(
            ErasureEvent(
                event_id=event_id,
                recorded_at=recorded_at,
                subject=payload["subject"],
                reason=str(payload["reason"]),
                org_id=org_id,
                user_id=user_id,
                email_digest=digest,
                previous_signature=str(payload["previous_signature"]),
                signature=str(payload["signature"]),
            )
        )
        previous = str(payload["signature"])
    return events


def _open_ledger(path: Path) -> int:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    flags = os.O_CREAT | os.O_RDWR | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ErasureLedgerError(f"cannot open erasure ledger {path}") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ErasureLedgerError(f"erasure ledger is not a regular file: {path}")
    os.fchmod(descriptor, 0o600)
    return descriptor


def read_events(settings: Settings) -> list[ErasureEvent]:
    """Verify the full chain and return every event in append order."""

    path = settings.resolved_erasure_ledger_path
    if not path.exists():
        return []
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ErasureLedgerError(f"cannot open erasure ledger {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ErasureLedgerError(f"erasure ledger is not a regular file: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return _parse_lines(b"".join(chunks), _key(settings))
    finally:
        os.close(descriptor)


def append_event(
    settings: Settings,
    *,
    subject: ErasureSubject,
    reason: str,
    email: str,
    org_id: int | None = None,
    user_id: int | None = None,
) -> ErasureEvent:
    """Durably append one authenticated event before destructive DB writes."""

    if reason not in _ALLOWED_REASONS:
        raise ErasureLedgerError(f"unsupported erasure reason: {reason}")
    if subject == "workspace" and (org_id is None or user_id is None):
        raise ErasureLedgerError("workspace erasure requires org_id and user_id")
    if subject == "member" and (org_id is None or user_id is None):
        raise ErasureLedgerError("member erasure requires org_id and user_id")
    if subject == "orphan_workspace" and (org_id is None or user_id is not None):
        raise ErasureLedgerError(
            "orphan workspace erasure requires org_id and cannot reference a user"
        )

    key = _key(settings)
    path = settings.resolved_erasure_ledger_path
    descriptor = _open_ledger(path)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        current = _parse_lines(b"".join(chunks), key)
        payload: dict[str, Any] = {
            "format": _FORMAT,
            "event_id": secrets.token_hex(16),
            "recorded_at": datetime.now(UTC).isoformat(),
            "subject": subject,
            "reason": reason,
            "org_id": org_id,
            "user_id": user_id,
            "email_digest": email_digest(email, settings),
            "previous_signature": current[-1].signature if current else _EMPTY_CHAIN,
        }
        payload["signature"] = _signature(payload, key)
        encoded = _canonical(payload) + b"\n"
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ErasureLedgerError("short write while appending erasure event")
            remaining = remaining[written:]
        os.fsync(descriptor)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return (
            _parse_lines(encoded, key)[0]
            if not current
            else ErasureEvent(
                event_id=str(payload["event_id"]),
                recorded_at=str(payload["recorded_at"]),
                subject=subject,
                reason=reason,
                org_id=org_id,
                user_id=user_id,
                email_digest=str(payload["email_digest"]),
                previous_signature=str(payload["previous_signature"]),
                signature=str(payload["signature"]),
            )
        )
    finally:
        os.close(descriptor)
