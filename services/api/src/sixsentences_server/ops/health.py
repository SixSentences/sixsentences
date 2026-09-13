"""Credential-free health signals for a self-hosted community deployment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.db import BackgroundJobRow, WorkerReplicaRow, db_session
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.ops.status import storage_status


def collect_operator_health(settings: Settings | None = None) -> dict[str, Any]:
    """Return local DB, queue, worker, corpus and storage health without secrets."""
    active = settings or get_settings()
    now = datetime.now(UTC)
    with db_session() as session:
        session.execute(text("SELECT 1"))
        jobs = {
            status: int(count)
            for status, count in session.execute(
                select(BackgroundJobRow.status, func.count(BackgroundJobRow.id)).group_by(
                    BackgroundJobRow.status
                )
            ).all()
        }
        oldest_queued = session.scalar(
            select(func.min(BackgroundJobRow.created_at)).where(BackgroundJobRow.status == "queued")
        )
        live_workers = int(
            session.scalar(
                select(func.count(WorkerReplicaRow.worker_id)).where(
                    WorkerReplicaRow.stopped_at.is_(None),
                    WorkerReplicaRow.heartbeat_at
                    >= now - timedelta(seconds=max(120, active.jobs_replica_alert_seconds)),
                )
            )
            or 0
        )
    queued_age_seconds: float | None = None
    if oldest_queued is not None:
        if oldest_queued.tzinfo is None:
            oldest_queued = oldest_queued.replace(tzinfo=UTC)
        queued_age_seconds = max(0.0, (now - oldest_queued).total_seconds())
    queue_delayed = bool(
        queued_age_seconds is not None and queued_age_seconds > active.jobs_queue_warning_seconds
    )
    disk = storage_status(active.data_dir, active.storage_reserve_bytes)
    corpus = DuckDBCorpus(active.corpus_dir)
    worker_ok = active.jobs_backend == "inline" or live_workers > 0
    status = "ok" if disk["status"] == "ok" and worker_ok and not queue_delayed else "degraded"
    return {
        "status": status,
        "database": "ok",
        "storage": disk,
        "corpus": {"synced": corpus.exists()},
        "queue": {
            "backend": active.jobs_backend,
            "counts": jobs,
            "oldest_queued_age_seconds": queued_age_seconds,
            "delayed": queue_delayed,
        },
        "workers": {"live": live_workers, "required": active.jobs_backend != "inline"},
    }
