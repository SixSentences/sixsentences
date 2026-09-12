"""Audit and erase account-less workspaces through the recovery-safe ledger."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.db import Org, User, db_session
from sixsentences_server.ops.erasure_ledger import append_event


@dataclass(frozen=True)
class OrphanWorkspacePurge:
    """Content-free result and deferred file cleanup for one purge batch."""

    report: dict[str, Any]
    artifacts: tuple[Any, ...]


def _cleanup_checkpoint_path(settings: Settings) -> Path:
    return settings.resolved_erasure_ledger_path.parent / "orphan-workspace-cleanup-pending.json"


def _artifact_payload(artifacts: Any) -> dict[str, Any]:
    return {
        "writer_ids": list(artifacts.writer_ids),
        "template_ids": list(artifacts.template_ids),
        "figure_ids": list(artifacts.figure_ids),
        "datasets": [list(dataset) for dataset in artifacts.datasets],
        "interview_ids": list(artifacts.interview_ids),
        "document_checksums": sorted(artifacts.document_checksums),
        "removable_checksums": list(artifacts.removable_checksums),
    }


def _write_cleanup_checkpoint(
    path: Path,
    *,
    org_ids: Sequence[int],
    artifacts: Sequence[Any],
) -> None:
    """Durably retain content-free cleanup identifiers across a process crash."""

    payload = {
        "format": 1,
        "kind": "orphan_workspace_cleanup_pending",
        "org_ids": list(org_ids),
        "artifacts": [_artifact_payload(item) for item in artifacts],
        "contains_user_content": False,
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise RuntimeError("short write while recording orphan cleanup checkpoint")
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


def _read_cleanup_checkpoint(path: Path) -> OrphanWorkspacePurge:
    from sixsentences_server.api.app import _WorkspaceErasureArtifacts  # noqa: PLC0415

    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("format") != 1
        or payload.get("kind") != "orphan_workspace_cleanup_pending"
        or payload.get("contains_user_content") is not False
        or not isinstance(payload.get("org_ids"), list)
        or not isinstance(payload.get("artifacts"), list)
    ):
        raise RuntimeError("invalid orphan cleanup checkpoint")
    org_ids = payload["org_ids"]
    if not all(type(org_id) is int and org_id > 0 for org_id in org_ids):
        raise RuntimeError("invalid workspace ids in orphan cleanup checkpoint")
    artifacts: list[Any] = []
    for item in payload["artifacts"]:
        if not isinstance(item, dict):
            raise RuntimeError("invalid artifacts in orphan cleanup checkpoint")
        try:
            artifacts.append(
                _WorkspaceErasureArtifacts(
                    writer_ids=tuple(int(value) for value in item["writer_ids"]),
                    template_ids=tuple(int(value) for value in item["template_ids"]),
                    figure_ids=tuple(int(value) for value in item["figure_ids"]),
                    datasets=tuple(
                        (int(dataset_id), str(filename))
                        for dataset_id, filename in item["datasets"]
                    ),
                    interview_ids=tuple(int(value) for value in item["interview_ids"]),
                    document_checksums=frozenset(
                        str(value) for value in item["document_checksums"]
                    ),
                    removable_checksums=tuple(str(value) for value in item["removable_checksums"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("invalid artifacts in orphan cleanup checkpoint") from exc
    return OrphanWorkspacePurge(
        report={
            "format": 1,
            "kind": "orphan_workspace_cleanup_retry",
            "requested_org_ids": org_ids,
            "contains_user_content": False,
            "passed": True,
        },
        artifacts=tuple(artifacts),
    )


def orphan_workspace_ids(session: Session) -> tuple[int, ...]:
    """Return stable IDs for workspaces that have no remaining account."""

    user_count = (
        select(func.count(User.id)).where(User.org_id == Org.id).correlate(Org).scalar_subquery()
    )
    return tuple(session.scalars(select(Org.id).where(user_count == 0).order_by(Org.id)).all())


def purge_orphan_workspaces(
    session: Session,
    *,
    settings: Settings,
    org_ids: Sequence[int],
) -> OrphanWorkspacePurge:
    """Journal and erase the exact requested orphan workspaces.

    The workspace row is locked and its account count is checked again before
    the durable journal write. This prevents an operator cleanup from racing a
    legitimate account assignment.
    """

    from sixsentences_server.api.app import _purge_workspace_database  # noqa: PLC0415

    requested = tuple(sorted(set(org_ids)))
    if not requested:
        raise ValueError("at least one workspace id is required")
    artifacts: list[Any] = []
    purged: list[int] = []
    already_absent: list[int] = []
    for org_id in requested:
        statement = select(Org).where(Org.id == org_id)
        if session.get_bind().dialect.name != "sqlite":
            statement = statement.with_for_update()
        org = session.scalar(statement)
        if org is None:
            already_absent.append(org_id)
            continue
        if session.scalar(select(User.id).where(User.org_id == org_id).limit(1)) is not None:
            raise RuntimeError(f"workspace {org_id} still has an account")
        append_event(
            settings,
            subject="orphan_workspace",
            reason="admin_request",
            email=f"orphan-workspace:{org_id}",
            org_id=org_id,
        )
        artifacts.append(_purge_workspace_database(session, org_id))
        purged.append(org_id)
    return OrphanWorkspacePurge(
        report={
            "format": 1,
            "kind": "orphan_workspace_cleanup",
            "completed_at": datetime.now(UTC).isoformat(),
            "requested_org_ids": list(requested),
            "purged_org_ids": purged,
            "already_absent_org_ids": already_absent,
            "contains_user_content": False,
            "passed": True,
        },
        artifacts=tuple(artifacts),
    )


def cleanup_purged_artifacts(result: OrphanWorkspacePurge, settings: Settings) -> None:
    """Remove workspace files only after the database transaction commits."""

    from sixsentences_server.api.app import _cleanup_workspace_erasure_artifacts  # noqa: PLC0415

    for artifacts in result.artifacts:
        _cleanup_workspace_erasure_artifacts(artifacts, settings)


def remaining_purged_artifact_count(
    result: OrphanWorkspacePurge,
    settings: Settings,
) -> int:
    """Count expected-to-be-deleted paths without exposing content or names."""

    remaining = 0
    for artifacts in result.artifacts:
        paths: list[Path] = []
        for writer_id in artifacts.writer_ids:
            paths.extend(
                (
                    settings.data_dir / "writer" / f"{writer_id}.pdf",
                    settings.data_dir / "writer" / f"{writer_id}.synctex.gz",
                    settings.data_dir / "writer" / str(writer_id),
                )
            )
        paths.extend(
            settings.data_dir / "writer_templates" / str(template_id)
            for template_id in artifacts.template_ids
        )
        for figure_id in artifacts.figure_ids:
            paths.extend(
                (
                    settings.data_dir / "figures" / f"{figure_id}.png",
                    settings.data_dir / "figures" / f"{figure_id}-input.png",
                )
            )
        for dataset_id, filename in artifacts.datasets:
            paths.extend(
                (
                    settings.data_dir / "datasets" / f"{dataset_id}-{Path(filename).name}",
                    settings.data_dir / "datasets" / "versions" / str(dataset_id),
                )
            )
        paths.extend(
            settings.data_dir / "interviews" / str(interview_id)
            for interview_id in artifacts.interview_ids
        )
        for checksum in artifacts.removable_checksums:
            paths.extend(
                (
                    settings.documents_dir / "blobs" / checksum[:2] / checksum,
                    settings.documents_dir / "text" / f"{checksum}.txt",
                )
            )
        remaining += sum(path.exists() for path in paths)
    return remaining


def finish_cleanup_checkpoint(
    result: OrphanWorkspacePurge,
    *,
    settings: Settings,
    checkpoint: Path,
) -> None:
    """Retry cleanup, prove it completed and remove the durable checkpoint."""

    requested = [int(value) for value in result.report["requested_org_ids"]]
    with db_session() as session:
        still_present = session.scalars(select(Org.id).where(Org.id.in_(requested))).all()
    if still_present:
        raise RuntimeError("cleanup checkpoint still references an active workspace")
    cleanup_purged_artifacts(result, settings)
    remaining = remaining_purged_artifact_count(result, settings)
    if remaining:
        raise RuntimeError(f"orphan workspace cleanup left {remaining} artifact paths")
    checkpoint.unlink()
    result.report["artifact_cleanup_verified"] = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit or erase account-less SixSentences workspaces"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("audit")
    purge = subcommands.add_parser("purge")
    purge.add_argument("--org-id", action="append", type=int, required=True)
    purge.add_argument("--confirm-production-purge", action="store_true")
    subcommands.add_parser("retry-cleanup")
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = get_settings()
    if args.command == "audit":
        with db_session() as session:
            org_ids = orphan_workspace_ids(session)
        report = {
            "format": 1,
            "kind": "orphan_workspace_audit",
            "audited_at": datetime.now(UTC).isoformat(),
            "orphan_org_ids": list(org_ids),
            "orphan_count": len(org_ids),
            "contains_user_content": False,
            "passed": len(org_ids) == 0,
        }
    elif args.command == "purge":
        if not args.confirm_production_purge:
            raise SystemExit("explicit production purge confirmation is required")
        checkpoint = _cleanup_checkpoint_path(settings)
        if checkpoint.exists():
            raise SystemExit("a pending orphan cleanup checkpoint must be retried first")
        with db_session() as session:
            result = purge_orphan_workspaces(
                session,
                settings=settings,
                org_ids=args.org_id,
            )
            _write_cleanup_checkpoint(
                checkpoint,
                org_ids=result.report["requested_org_ids"],
                artifacts=result.artifacts,
            )
        finish_cleanup_checkpoint(result, settings=settings, checkpoint=checkpoint)
        report = result.report
    else:
        checkpoint = _cleanup_checkpoint_path(settings)
        if not checkpoint.exists():
            raise SystemExit("no pending orphan cleanup checkpoint exists")
        result = _read_cleanup_checkpoint(checkpoint)
        finish_cleanup_checkpoint(result, settings=settings, checkpoint=checkpoint)
        report = result.report
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
