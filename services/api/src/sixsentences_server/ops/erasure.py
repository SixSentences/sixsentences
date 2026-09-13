"""Verify and replay post-snapshot erasures before restored traffic resumes."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.db import Org, User, db_session
from sixsentences_server.ops.erasure_ledger import (
    ErasureEvent,
    email_digest,
    read_events,
)


def _matching_user(session: Session, event: ErasureEvent, settings: Settings) -> User | None:
    """Resolve an old-snapshot subject without trusting a potentially reused ID."""

    if event.user_id is None or event.org_id is None:
        raise RuntimeError("account erasure event is missing its internal identifiers")
    candidate = session.get(User, event.user_id)
    if (
        candidate is not None
        and candidate.org_id == event.org_id
        and email_digest(candidate.email, settings) == event.email_digest
    ):
        return candidate
    matches = [
        user
        for user in session.scalars(select(User).where(User.org_id == event.org_id)).all()
        if email_digest(user.email, settings) == event.email_digest
    ]
    if len(matches) > 1:
        raise RuntimeError(f"erasure event {event.event_id} matched multiple users")
    if matches:
        return matches[0]
    return None


def replay_erasure_events(
    session: Session,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Idempotently apply the authenticated journal to one isolated restore."""

    # Imported lazily to avoid loading the FastAPI surface when only verifying
    # a journal and to keep the ledger primitive independent of application DB
    # models. These helpers are the same tested erasure inventory used by the
    # authenticated account endpoint.
    from sixsentences_server.api.app import (  # noqa: PLC0415
        _detach_user_for_deletion,
        _purge_workspace_database,
    )

    events = read_events(settings)
    applied = {"workspace": 0, "orphan_workspace": 0, "member": 0}
    already_absent = {"workspace": 0, "orphan_workspace": 0, "member": 0}
    cleanup_checksums: set[str] = set()
    cleanup_artifacts: list[Any] = []
    last_signature = events[-1].signature if events else None

    for event in events:
        if event.subject not in applied:
            raise RuntimeError(f"unsupported community erasure subject: {event.subject}")

        if event.org_id is None:
            raise RuntimeError(f"erasure event {event.event_id} has no org id")
        org = session.get(Org, event.org_id)
        if org is None:
            already_absent[event.subject] += 1
            continue
        if event.subject == "orphan_workspace":
            expected_digest = email_digest(
                f"orphan-workspace:{event.org_id}",
                settings,
            )
            if not hmac.compare_digest(event.email_digest, expected_digest):
                raise RuntimeError(
                    f"erasure event {event.event_id} orphan identity guard did not match "
                    "restored data"
                )
            if session.scalar(select(User.id).where(User.org_id == event.org_id).limit(1)):
                raise RuntimeError(
                    f"erasure event {event.event_id} targets a workspace with an account"
                )
            artifacts = _purge_workspace_database(session, event.org_id)
            cleanup_artifacts.append(artifacts)
            applied["orphan_workspace"] += 1
            continue
        user = _matching_user(session, event, settings)
        if user is None:
            raise RuntimeError(
                f"erasure event {event.event_id} identity guard did not match restored data"
            )
        if event.subject == "member":
            removable_checksums = _detach_user_for_deletion(session, user.id)
            session.delete(user)
            session.flush()
            cleanup_checksums.update(removable_checksums)
            applied["member"] += 1
            continue

        artifacts = _purge_workspace_database(session, event.org_id)
        cleanup_artifacts.append(artifacts)
        applied["workspace"] += 1

    return {
        "format": 1,
        "kind": "erasure_replay",
        "completed_at": datetime.now(UTC).isoformat(),
        "verified_events": len(events),
        "applied": applied,
        "already_absent": already_absent,
        "last_signature": last_signature,
        "contains_user_content": False,
        "passed": True,
        "_cleanup_checksums": sorted(cleanup_checksums),
        "_cleanup_artifacts": cleanup_artifacts,
    }


def cleanup_replayed_erasure_artifacts(report: dict[str, Any], settings: Settings) -> None:
    """Remove replayed files only after the caller commits the DB erasure."""

    from sixsentences_server.api.app import (  # noqa: PLC0415
        _cleanup_document_checksums,
        _cleanup_workspace_erasure_artifacts,
    )

    _cleanup_document_checksums(report.pop("_cleanup_checksums", []), settings)
    for artifacts in report.pop("_cleanup_artifacts", []):
        _cleanup_workspace_erasure_artifacts(artifacts, settings)


def verify_erasure_events(settings: Settings) -> dict[str, Any]:
    events = read_events(settings)
    return {
        "format": 1,
        "kind": "erasure_ledger_verification",
        "verified_at": datetime.now(UTC).isoformat(),
        "verified_events": len(events),
        "last_signature": events[-1].signature if events else None,
        "contains_user_content": False,
        "passed": True,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o600,
    )
    try:
        encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise RuntimeError("short write while recording erasure evidence")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify or replay account erasures")
    subcommands = parser.add_subparsers(dest="command", required=True)
    verify = subcommands.add_parser("verify")
    verify.add_argument("--report", type=Path)
    replay = subcommands.add_parser("replay")
    replay.add_argument("--report", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = get_settings()
    if args.command == "verify":
        report = verify_erasure_events(settings)
    else:
        with db_session() as session:
            report = replay_erasure_events(session, settings=settings)
        cleanup_replayed_erasure_artifacts(report, settings)
    if args.report is not None:
        _write_report(args.report, report)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
