"""Scheduled, content-free retention enforcement for operational records.

Research artifacts are user-owned workspace data and are not silently aged
out. This job removes only terminal queue payloads and stale authentication
records and disables expired participant links. Account/workspace deletion
remains the lifecycle boundary for
uploads, chats, manuscripts, interviews, surveys, annotations, figures, and
generated reports.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.db import (
    AuthToken,
    BackgroundJobRow,
    VoiceInviteRow,
    db_session,
)

TERMINAL_JOB_STATES = ("completed", "failed", "cancelled")


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamps to UTC for report serialization."""

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _count(session: Session, statement: Any) -> int:
    return int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def run_retention(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
    apply: bool,
) -> dict[str, Any]:
    """Evaluate or apply the technical retention policy in one transaction."""

    current = _as_utc(now or datetime.now(UTC))
    job_cutoff = current - timedelta(days=max(1, settings.jobs_retention_days))
    token_cutoff = current - timedelta(days=max(1, settings.auth_token_retention_days))
    stream_ticket_cutoff = current - timedelta(hours=max(1, settings.stream_ticket_retention_hours))

    terminal_jobs = select(BackgroundJobRow.id).where(
        BackgroundJobRow.status.in_(TERMINAL_JOB_STATES),
        BackgroundJobRow.finished_at.is_not(None),
        BackgroundJobRow.finished_at < job_cutoff,
    )
    stale_tokens = select(AuthToken.id).where(
        or_(
            and_(
                AuthToken.kind == "stream_ticket",
                AuthToken.expires_at.is_not(None),
                AuthToken.expires_at < stream_ticket_cutoff,
            ),
            and_(
                AuthToken.expires_at.is_not(None),
                AuthToken.expires_at < token_cutoff,
            ),
            and_(
                AuthToken.revoked_at.is_not(None),
                AuthToken.revoked_at < token_cutoff,
            ),
        )
    )
    expired_invites = select(VoiceInviteRow.id).where(
        VoiceInviteRow.active.is_(True),
        VoiceInviteRow.expires_at.is_not(None),
        VoiceInviteRow.expires_at <= current,
    )
    candidates = {
        "terminal_jobs": _count(session, terminal_jobs),
        "stale_auth_tokens": _count(session, stale_tokens),
        "expired_voice_invites": _count(session, expired_invites),
    }
    affected = {key: 0 for key in candidates}
    if apply:
        affected["terminal_jobs"] = int(
            cast(
                CursorResult[Any],
                session.execute(
                    delete(BackgroundJobRow).where(BackgroundJobRow.id.in_(terminal_jobs))
                ),
            ).rowcount
            or 0
        )
        affected["stale_auth_tokens"] = int(
            cast(
                CursorResult[Any],
                session.execute(delete(AuthToken).where(AuthToken.id.in_(stale_tokens))),
            ).rowcount
            or 0
        )
        affected["expired_voice_invites"] = int(
            cast(
                CursorResult[Any],
                session.execute(
                    update(VoiceInviteRow)
                    .where(VoiceInviteRow.id.in_(expired_invites))
                    .values(active=False)
                ),
            ).rowcount
            or 0
        )
    return {
        "format": 1,
        "ran_at": current.isoformat(),
        "mode": "apply" if apply else "dry_run",
        "policy": {
            "terminal_jobs_days": max(1, settings.jobs_retention_days),
            "expired_or_revoked_auth_tokens_days": max(1, settings.auth_token_retention_days),
            "spent_stream_tickets_hours": max(1, settings.stream_ticket_retention_hours),
            "expired_voice_invites": "disabled_on_expiry",
            "workspace_research_content": "until_user_or_workspace_deletion",
            "transcript_only_voice_audio": "never_persisted",
            "container_logs": "size_rotated",
            "system_journal": "maximum_30_days",
            "backups": "operator_defined_and_documented",
        },
        "cutoffs": {
            "terminal_jobs_before": job_cutoff.isoformat(),
            "auth_tokens_before": token_cutoff.isoformat(),
            "stream_tickets_before": stream_ticket_cutoff.isoformat(),
        },
        "candidates": candidates,
        "affected": affected,
        "contains_user_content": False,
        "passed": affected == candidates if apply else True,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enforce technical data retention")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    with db_session() as session:
        report = run_retention(
            session,
            settings=get_settings(),
            apply=args.apply,
        )
    if args.report is not None:
        _write_report(args.report, report)
    print(json.dumps(report, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
