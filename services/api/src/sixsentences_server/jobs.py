"""Durable background job execution for the modular-monolith deployment.

Local development keeps FastAPI ``BackgroundTasks`` for zero-setup feedback.
Multi-worker deployments write jobs to PostgreSQL. Disjoint worker pools execute chat,
research, document, media, notification and LaTeX workloads independently.
Pool concurrency keeps the host busy under load while reserved lanes protect
interactive latency. The queue deliberately shares the application's durable,
database: PostgreSQL row locks coordinate replicas, and fenced leases
make expired-job recovery safe. The public scheduling interface remains
backend-independent so a dedicated broker can replace this queue when traffic
actually warrants another service.
"""

from __future__ import annotations

import importlib
import logging
import multiprocessing
import os
import signal
import socket
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import BackgroundTasks
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    BackgroundJobRow,
    CapacityReservationRow,
    Org,
    PaperEnrichmentRow,
    RepositoryAnalysisRow,
    Run,
    RunEvent,
    WorkerReplicaRow,
    db_session,
)
from sixsentences_server.core.entitlements import finish_ai_action
from sixsentences_server.core.models import StageName
from sixsentences_server.core.security_logging import install_sensitive_log_filter
from sixsentences_server.core.state import is_terminal, mark_failed

_LOG = logging.getLogger(__name__)
_TASK_MODULE = "sixsentences_server.api.app"
_ALLOWED_TASKS = frozenset(
    {
        "_acquire_documents",
        "_execute",
        "_execute_ask",
        "_execute_document_translation",
        "_execute_extraction",
        "_execute_figure",
        "_execute_interview",
        "_execute_interview_analysis",
        "_execute_live_companion_ask",
        "_execute_project_brainstorm_synthesis",
        "_execute_repository_analysis",
        "_execute_paper_enrichment",
        "_execute_writer_compile",
        "_notify_voice_session",
        "_resume",
        "_resume_paused",
    }
)
_CHAT_TASKS = frozenset(
    {
        "_execute_ask",
        "_execute_live_companion_ask",
        "_execute_project_brainstorm_synthesis",
    }
)
_LIVE_ASK_TASKS = frozenset(
    {"_execute_live_companion_ask", "_execute_project_brainstorm_synthesis"}
)
_RESEARCH_TASKS = frozenset(
    {
        "_execute",
        "_resume",
        "_resume_paused",
    }
)
_DOCUMENT_TASKS = frozenset(
    {
        "_acquire_documents",
        "_execute_document_translation",
        "_execute_extraction",
        "_execute_paper_enrichment",
        "_execute_repository_analysis",
    }
)
_MEDIA_TASKS = frozenset(
    {
        "_execute_figure",
        "_execute_interview",
        "_execute_interview_analysis",
    }
)
_COMPILE_TASKS = frozenset({"_execute_writer_compile"})
_NOTIFICATION_TASKS = frozenset({"_notify_voice_session"})
_RUN_SCOPED_TASKS = (
    (_CHAT_TASKS - _LIVE_ASK_TASKS)
    | _RESEARCH_TASKS
    | (
        _DOCUMENT_TASKS
        - frozenset(
            {
                "_execute_document_translation",
                "_execute_paper_enrichment",
                "_execute_repository_analysis",
            }
        )
    )
)
PRODUCTION_LANES = (
    "chat",
    "research",
    "documents",
    "compile",
    "media",
    "notifications",
)
_WORKER_LANES = {
    "all": _ALLOWED_TASKS,
    "chat": _CHAT_TASKS,
    "research": _RESEARCH_TASKS,
    "documents": _DOCUMENT_TASKS,
    "compile": _COMPILE_TASKS,
    "media": _MEDIA_TASKS,
    "notifications": _NOTIFICATION_TASKS,
}
_PRODUCTION_TASKS = frozenset().union(*(_WORKER_LANES[name] for name in PRODUCTION_LANES))
if _PRODUCTION_TASKS != _ALLOWED_TASKS or sum(
    len(_WORKER_LANES[name]) for name in PRODUCTION_LANES
) != len(_ALLOWED_TASKS):
    raise RuntimeError("every registered background task must belong to exactly one worker lane")
_RETRY_DELAYS_SECONDS = (10, 60, 300, 900)
_ACTIVE_JOB_STATES = ("queued", "running")
_GENERIC_BACKGROUND_FAILURE = (
    "Something went wrong while running this task. Please try again; if it "
    "keeps failing, contact support."
)


class JobConfigurationError(RuntimeError):
    """The configured job backend cannot safely accept work."""


class JobExecutionError(RuntimeError):
    """A worker subprocess exited without completing its task."""


class JobTimeoutError(TimeoutError):
    """A task exceeded its configured hard execution limit."""


class _StoppableProcess(Protocol):
    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def worker_lane_tasks(lane: str) -> frozenset[str]:
    """Return the registered task names for one public worker lane."""

    try:
        return _WORKER_LANES[lane]
    except KeyError as exc:
        allowed = ", ".join(sorted(_WORKER_LANES))
        raise JobConfigurationError(
            f"unsupported worker lane {lane!r}; expected one of: {allowed}"
        ) from exc


def task_lane(task_name: str) -> str:
    """Resolve one registered task to its disjoint production lane."""

    for lane in PRODUCTION_LANES:
        if task_name in _WORKER_LANES[lane]:
            return lane
    raise JobConfigurationError(f"background task {task_name!r} is not registered")


def _task_name(task: Callable[..., Any]) -> str:
    name = task.__name__
    if task.__module__ != _TASK_MODULE or name not in _ALLOWED_TASKS:
        raise JobConfigurationError(f"background task {task.__module__}:{name} is not registered")
    return name


def enqueue_job(
    background: BackgroundTasks,
    task: Callable[..., Any],
    *args: Any,
    session: Session | None = None,
    timeout_seconds: int = 21_600,
    max_attempts: int | None = None,
    queue_org_id: int | None = None,
    queue_priority: int = 0,
    **kwargs: Any,
) -> int | None:
    """Schedule one registered task.

    When ``session`` is supplied, the job row participates in the caller's
    transaction. This is used when a run and its job must become visible
    atomically. Inline mode preserves the current TestClient/local behavior.
    """

    settings = get_settings()
    if settings.jobs_backend == "inline":
        background.add_task(task, *args, **kwargs)
        return None
    if settings.jobs_backend != "database":
        raise JobConfigurationError(f"unsupported SIX_JOBS_BACKEND={settings.jobs_backend!r}")
    name = _task_name(task)
    row = BackgroundJobRow(
        org_id=queue_org_id,
        task=name,
        lane=task_lane(name),
        priority=queue_priority,
        args=list(args),
        kwargs=dict(kwargs),
        max_attempts=max(1, max_attempts or settings.jobs_max_attempts),
        timeout_seconds=max(30, timeout_seconds),
    )
    if session is not None:
        session.add(row)
        session.flush()
        return row.id
    with db_session() as job_session:
        job_session.add(row)
        job_session.flush()
        return row.id


def cancel_run_jobs(session: Session, run_id: int) -> int:
    """Cancel every queued or running job owned by one run.

    Clearing the fenced lease makes a live worker terminate its subprocess on
    the next poll. Quick Answers use the chat lane but are still
    run-scoped; omitting them used to leave the agent and its tool calls alive
    after the UI already displayed ``Cancelled``.
    """

    now = datetime.now(UTC)
    candidates = session.scalars(
        select(BackgroundJobRow).where(
            BackgroundJobRow.status.in_(_ACTIVE_JOB_STATES),
            BackgroundJobRow.task.in_(_RUN_SCOPED_TASKS),
        )
    ).all()
    cancelled = 0
    for job in candidates:
        if not job.args or str(job.args[0]) != str(run_id):
            continue
        job.status = "cancelled"
        job.finished_at = now
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.last_error = ""
        job.updated_at = now
        cancelled += 1
    return cancelled


def cancel_live_ask_jobs(session: Session, ask_ids: list[int]) -> int:
    """Cancel queued/running Live Ask work before its owning rows are deleted."""

    targets = {int(value) for value in ask_ids}
    if not targets:
        return 0
    now = datetime.now(UTC)
    cancelled = 0
    jobs = session.scalars(
        select(BackgroundJobRow).where(
            BackgroundJobRow.task == "_execute_live_companion_ask",
            BackgroundJobRow.status.in_(_ACTIVE_JOB_STATES),
        )
    ).all()
    for job in jobs:
        try:
            ask_id = int((job.args or [None])[0])
        except (TypeError, ValueError, IndexError):
            continue
        if ask_id not in targets:
            continue
        job.status = "cancelled"
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        cancelled += 1
    return cancelled


def cancel_project_brainstorm_jobs(
    session: Session,
    synthesis_ids: list[int],
) -> int:
    """Fence project synthesis workers before private receipts change scope."""

    targets = {int(value) for value in synthesis_ids}
    if not targets:
        return 0
    now = datetime.now(UTC)
    cancelled = 0
    jobs = session.scalars(
        select(BackgroundJobRow).where(
            BackgroundJobRow.task == "_execute_project_brainstorm_synthesis",
            BackgroundJobRow.status.in_(_ACTIVE_JOB_STATES),
        )
    ).all()
    for job in jobs:
        try:
            synthesis_id = int((job.args or [None])[0])
        except (TypeError, ValueError, IndexError):
            continue
        if synthesis_id not in targets:
            continue
        job.status = "cancelled"
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        cancelled += 1
    return cancelled


def cancel_paper_enrichment_jobs(session: Session, enrichment_ids: list[int]) -> int:
    """Fence active provider lookups before their private receipts disappear."""

    targets = {int(value) for value in enrichment_ids}
    if not targets:
        return 0
    now = datetime.now(UTC)
    cancelled = 0
    jobs = session.scalars(
        select(BackgroundJobRow)
        .where(
            BackgroundJobRow.task == "_execute_paper_enrichment",
            BackgroundJobRow.status.in_(_ACTIVE_JOB_STATES),
        )
        .with_for_update()
    ).all()
    for job in jobs:
        try:
            enrichment_id = int((job.args or [None])[0])
        except (TypeError, ValueError, IndexError):
            continue
        if enrichment_id not in targets:
            continue
        job.status = "cancelled"
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        cancelled += 1
    return cancelled


def cancel_repository_analysis_jobs(session: Session, analysis_ids: list[int]) -> int:
    """Fence active repository workers before their private receipt changes."""

    targets = {int(value) for value in analysis_ids}
    if not targets:
        return 0
    now = datetime.now(UTC)
    cancelled = 0
    jobs = session.scalars(
        select(BackgroundJobRow)
        .where(
            BackgroundJobRow.task == "_execute_repository_analysis",
            BackgroundJobRow.status.in_(_ACTIVE_JOB_STATES),
        )
        .with_for_update()
    ).all()
    for job in jobs:
        try:
            analysis_id = int((job.args or [None])[0])
        except (TypeError, ValueError, IndexError):
            continue
        if analysis_id not in targets:
            continue
        job.status = "cancelled"
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.last_error = ""
        cancelled += 1
    return cancelled


def cancel_figure_jobs(session: Session, figure_ids: list[int]) -> int:
    """Fence queued/running figure workers before their grounding disappears."""

    targets = {int(value) for value in figure_ids}
    if not targets:
        return 0
    now = datetime.now(UTC)
    cancelled = 0
    jobs = session.scalars(
        select(BackgroundJobRow)
        .where(
            BackgroundJobRow.task == "_execute_figure",
            BackgroundJobRow.status.in_(_ACTIVE_JOB_STATES),
        )
        .with_for_update()
    ).all()
    for job in jobs:
        try:
            figure_id = int((job.args or [None])[0])
        except (TypeError, ValueError, IndexError):
            continue
        if figure_id not in targets:
            continue
        job.status = "cancelled"
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.last_error = ""
        cancelled += 1
    return cancelled


def _terminalize_paper_enrichment_receipt(
    enrichment_id: int,
    *,
    now: datetime,
) -> None:
    """Best-effort product finalizer after the queue row committed terminal.

    This intentionally runs in its own transaction. User cancellation/deletion
    locks Receipt -> Job, whereas the queue only commits Job and then touches
    Receipt conditionally, so the two paths cannot deadlock or revive a cancel.
    """

    try:
        with db_session() as session:
            receipt = session.scalar(
                select(PaperEnrichmentRow)
                .where(PaperEnrichmentRow.id == enrichment_id)
                .with_for_update()
            )
            if receipt is not None and receipt.status in {"queued", "running"}:
                receipt.status = "failed"
                receipt.error_code = "paper_enrichment_worker_failed"
                receipt.error_message = _GENERIC_BACKGROUND_FAILURE
                receipt.finished_at = now
                receipt.updated_at = now
    except SQLAlchemyError:
        _LOG.exception(
            "could not terminalize paper-enrichment receipt %s",
            enrichment_id,
        )


def _terminalize_project_brainstorm_receipt(synthesis_id: int) -> None:
    """Conditionally fail a receipt after its queue row committed terminal."""

    try:
        with db_session() as session:
            from sixsentences_server.interviews.live import (
                terminalize_project_brainstorm_synthesis,
            )

            terminalize_project_brainstorm_synthesis(session, synthesis_id)
    except SQLAlchemyError:
        _LOG.exception(
            "could not terminalize project-brainstorm receipt %s",
            synthesis_id,
        )


def terminalize_extraction_action(
    run_id: int, action_id: str, *, expected_org_id: int | None = None
) -> None:
    """Release only a failed extraction's unused hold, not its completed parent."""
    if not action_id:
        return
    try:
        with db_session() as session:
            run = session.get(Run, run_id)
            if run is None or (expected_org_id is not None and run.org_id != expected_org_id):
                return
            session.scalar(select(Org.id).where(Org.id == run.org_id).with_for_update())
            action = session.scalar(
                select(CapacityReservationRow)
                .where(
                    CapacityReservationRow.action_id == action_id,
                    CapacityReservationRow.org_id == run.org_id,
                    CapacityReservationRow.run_id == run.id,
                    CapacityReservationRow.resource_type == "run_extraction",
                    CapacityReservationRow.resource_id == str(run.id),
                    CapacityReservationRow.action == "extraction",
                    CapacityReservationRow.status == "active",
                )
                .with_for_update()
            )
            if action is None:
                return
            finish_ai_action(session, action_id, status="failed")
            session.add(
                RunEvent(
                    org_id=run.org_id,
                    run_id=run.id,
                    stage=StageName.REPORT.value,
                    event="extraction_failed",
                    payload={"control": "worker_guard", "action_id": action_id},
                )
            )
    except Exception:
        _LOG.exception("could not terminalize failed extraction for run %s", run_id)


def _extraction_job_scope(job: BackgroundJobRow) -> tuple[int, str, int | None] | None:
    """Read the backward-compatible extraction action from durable job arguments."""
    args = job.args or []
    if len(args) < 3 or not isinstance(args[2], str) or not args[2]:
        return None
    try:
        return int(args[0]), args[2], job.org_id
    except (TypeError, ValueError):
        _LOG.error("extraction job %s has an invalid run id", job.id)
        return None


def _terminalize_repository_analysis(analysis_id: int, *, now: datetime) -> None:
    """Fail an active private analysis after its final queue attempt ends."""

    try:
        with db_session() as session:
            row = session.scalar(
                select(RepositoryAnalysisRow)
                .where(RepositoryAnalysisRow.id == analysis_id)
                .with_for_update()
            )
            if row is None or row.status not in {"queued", "fetching", "analyzing"}:
                return
            row.status = "error"
            row.error_code = "repository_analysis_worker_failed"
            row.error = _GENERIC_BACKGROUND_FAILURE
            row.finished_at = now
            row.updated_at = now
            finish_ai_action(session, row.action_id, status="failed")
    except SQLAlchemyError:
        _LOG.exception("could not terminalize repository analysis %s", analysis_id)


def _resolve_task(name: str) -> Callable[..., Any]:
    if name not in _ALLOWED_TASKS:
        raise JobConfigurationError(f"background task {name!r} is not registered")
    module = importlib.import_module(_TASK_MODULE)
    task = getattr(module, name, None)
    if not callable(task):
        raise JobConfigurationError(f"background task {name!r} is unavailable")
    return cast(Callable[..., Any], task)


def _run_task_process(name: str, args: list[Any], kwargs: dict[str, Any]) -> None:
    """Fresh process boundary so a wedged provider call cannot block the queue."""

    task = _resolve_task(name)
    task(*args, **kwargs)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class DatabaseWorker:
    """One durable task-lane worker with retry scheduling and recovery."""

    def __init__(
        self,
        *,
        run_in_subprocess: bool = True,
        lane: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        settings = get_settings()
        if settings.jobs_backend != "database":
            raise JobConfigurationError("six-worker requires SIX_JOBS_BACKEND=database")
        self.lane = (lane or settings.jobs_worker_lane).strip().lower()
        self.allowed_tasks = worker_lane_tasks(self.lane)
        self.poll_seconds = max(0.1, settings.jobs_poll_seconds)
        self.retention_days = max(1, settings.jobs_retention_days)
        self.lease_seconds = max(30, settings.jobs_lease_seconds)
        self.drain_seconds = max(0, settings.jobs_drain_seconds)
        self.fair_scheduling = settings.jobs_fair_scheduling
        self.hostname = socket.gethostname()
        self.worker_id = f"{self.hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        self.release_revision = settings.release_git_revision[:64]
        self.concurrency = max(
            1,
            concurrency if concurrency is not None else settings.jobs_worker_concurrency,
        )
        heartbeat_name = (
            "job-worker-heartbeat" if self.lane == "all" else f"job-worker-{self.lane}-heartbeat"
        )
        heartbeat_dir = settings.jobs_heartbeat_dir or settings.data_dir
        self.heartbeat_path = heartbeat_dir / heartbeat_name
        self.run_in_subprocess = run_in_subprocess
        self._stopping = threading.Event()
        self._draining = threading.Event()
        self._force_stopping = threading.Event()
        self._active_claims: dict[int, str] = {}
        self._active_claims_lock = threading.Lock()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="six-job-heartbeat",
            daemon=True,
        )
        self._worker_threads: list[threading.Thread] = []

    def stop(self, *_args: Any) -> None:
        """Begin a graceful drain; a repeated signal forces a hand-off."""

        if self._draining.is_set():
            _LOG.warning("forcing worker shutdown; lane=%s", self.lane)
            self._force_stopping.set()
            return
        _LOG.info("draining worker; lane=%s active=%s", self.lane, self.active_slots)
        self._draining.set()

    @property
    def active_slots(self) -> int:
        """Return the number of locally owned active claims."""

        with self._active_claims_lock:
            return len(self._active_claims)

    def _record_replica_heartbeat(
        self,
        *,
        draining: bool | None = None,
        stopped: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        with db_session() as session:
            row = session.get(WorkerReplicaRow, self.worker_id)
            if row is None:
                row = WorkerReplicaRow(
                    worker_id=self.worker_id,
                    lane=self.lane,
                    hostname=self.hostname,
                    pid=os.getpid(),
                    release_revision=self.release_revision,
                    concurrency=self.concurrency,
                    started_at=now,
                )
                session.add(row)
            row.lane = self.lane
            row.release_revision = self.release_revision
            row.concurrency = self.concurrency
            row.active_slots = self.active_slots
            row.draining = self._draining.is_set() if draining is None else draining
            row.heartbeat_at = now
            row.stopped_at = now if stopped else None

    def _requeue_owned_claims(self) -> int:
        """Fence and requeue this replica's active work during forced shutdown."""

        now = datetime.now(UTC)
        with db_session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(BackgroundJobRow)
                    .where(
                        BackgroundJobRow.status == "running",
                        BackgroundJobRow.lease_owner == self.worker_id,
                    )
                    .values(
                        status="queued",
                        available_at=now,
                        started_at=None,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        last_error="Worker drained before the task completed.",
                        updated_at=now,
                    )
                ),
            )
            return int(result.rowcount or 0)

    def _heartbeat_loop(self) -> None:
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        while not self._stopping.is_set() and (
            not self._draining.is_set() or self.active_slots > 0
        ):
            try:
                self._renew_active_leases()
                self._record_replica_heartbeat()
                # The file is a container-local health receipt, so update it
                # only after the authoritative database heartbeat succeeds.
                self.heartbeat_path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
            except SQLAlchemyError:
                # Transient lock/connection failures are retried on the next
                # heartbeat. The old implementation let this daemon thread die,
                # which made healthy long-running work look abandoned.
                _LOG.warning(
                    "could not renew active job leases; retrying without "
                    "relinquishing the local claims",
                    exc_info=True,
                )
            except OSError:
                # A missing health-file receipt must fail the container health
                # check, but it must not prevent database lease renewal.
                _LOG.warning("could not write the worker heartbeat file", exc_info=True)
            self._stopping.wait(15)

    def recover_interrupted(self) -> int:
        """Requeue only claims whose lease expired.

        Rows without a token are legacy claims from the pre-lease queue. They
        can only exist across the first deployment of this migration, when the
        old worker process has already stopped.
        """

        now = datetime.now(UTC)
        exhausted_enrichment_ids: list[int] = []
        exhausted_project_brainstorm_ids: list[int] = []
        exhausted_repository_analysis_ids: list[int] = []
        exhausted_extractions: list[tuple[int, str, int | None]] = []
        exhausted_count = 0
        with db_session() as session:
            exhausted = session.scalars(
                select(BackgroundJobRow)
                .where(
                    BackgroundJobRow.status == "running",
                    BackgroundJobRow.task.in_(
                        (
                            "_execute_paper_enrichment",
                            "_execute_project_brainstorm_synthesis",
                            "_execute_repository_analysis",
                            "_execute_extraction",
                        )
                    ),
                    BackgroundJobRow.task.in_(self.allowed_tasks),
                    (
                        (BackgroundJobRow.lease_expires_at <= now)
                        | (BackgroundJobRow.lease_token.is_(None))
                    ),
                    BackgroundJobRow.attempts >= BackgroundJobRow.max_attempts,
                )
                .with_for_update()
            ).all()
            for job in exhausted:
                job.status = "failed"
                job.finished_at = now
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                job.last_error = "Worker lease expired after the final allowed attempt."
                job.updated_at = now
                exhausted_count += 1
                if job.task == "_execute_extraction":
                    scope = _extraction_job_scope(job)
                    if scope is not None:
                        exhausted_extractions.append(scope)
                if job.args and job.task == "_execute_paper_enrichment":
                    try:
                        exhausted_enrichment_ids.append(int(job.args[0]))
                    except (TypeError, ValueError):
                        _LOG.error(
                            "paper-enrichment job %s has an invalid receipt id",
                            job.id,
                        )
                if job.args and job.task == "_execute_project_brainstorm_synthesis":
                    try:
                        exhausted_project_brainstorm_ids.append(int(job.args[0]))
                    except (TypeError, ValueError):
                        _LOG.error(
                            "project-brainstorm job %s has an invalid synthesis id",
                            job.id,
                        )
                if job.args and job.task == "_execute_repository_analysis":
                    try:
                        exhausted_repository_analysis_ids.append(int(job.args[0]))
                    except (TypeError, ValueError):
                        _LOG.error(
                            "repository-analysis job %s has an invalid analysis id",
                            job.id,
                        )
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(BackgroundJobRow)
                    .where(
                        BackgroundJobRow.status == "running",
                        BackgroundJobRow.task.in_(self.allowed_tasks),
                        (
                            (BackgroundJobRow.lease_expires_at <= now)
                            | (BackgroundJobRow.lease_token.is_(None))
                        ),
                    )
                    .values(
                        status="queued",
                        available_at=now,
                        started_at=None,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        last_error="Worker lease expired before the task completed.",
                        updated_at=now,
                    )
                ),
            )
            recovered_count = int(result.rowcount or 0) + exhausted_count
        for enrichment_id in exhausted_enrichment_ids:
            _terminalize_paper_enrichment_receipt(enrichment_id, now=now)
        for synthesis_id in exhausted_project_brainstorm_ids:
            _terminalize_project_brainstorm_receipt(synthesis_id)
        for analysis_id in exhausted_repository_analysis_ids:
            _terminalize_repository_analysis(analysis_id, now=now)
        for run_id, action_id, org_id in exhausted_extractions:
            terminalize_extraction_action(run_id, action_id, expected_org_id=org_id)
        return recovered_count

    def cleanup(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        with db_session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    delete(BackgroundJobRow).where(
                        BackgroundJobRow.status.in_(("completed", "failed")),
                        BackgroundJobRow.finished_at.is_not(None),
                        BackgroundJobRow.finished_at < cutoff,
                    )
                ),
            )
            session.execute(
                delete(WorkerReplicaRow).where(
                    WorkerReplicaRow.heartbeat_at < cutoff,
                )
            )
            return int(result.rowcount or 0)

    def _claim(self) -> BackgroundJobRow | None:
        if self._draining.is_set() or self._force_stopping.is_set():
            return None
        # PostgreSQL exits on the first pass because ``SKIP LOCKED`` prevents
        # replicas from observing the same candidate. SQLite cannot lock a row
        # during SELECT, so concurrent local workers may lose the conditional
        # UPDATE and must select the next queued row instead of going idle.
        for _claim_attempt in range(32):
            now = datetime.now(UTC)
            lease_token = uuid.uuid4().hex
            lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            with db_session() as session:
                order_columns: list[Any] = [BackgroundJobRow.priority.desc()]
                if self.fair_scheduling:
                    running_job = aliased(BackgroundJobRow)
                    org_lane_load = (
                        select(func.count(running_job.id))
                        .where(
                            running_job.status == "running",
                            running_job.org_id == BackgroundJobRow.org_id,
                            running_job.task.in_(self.allowed_tasks),
                        )
                        .correlate(BackgroundJobRow)
                        .scalar_subquery()
                    )
                    order_columns.append(
                        case(
                            (BackgroundJobRow.org_id.is_(None), 0),
                            else_=org_lane_load,
                        )
                    )
                order_columns.extend([BackgroundJobRow.available_at, BackgroundJobRow.id])
                candidate_query = (
                    select(BackgroundJobRow)
                    .where(
                        BackgroundJobRow.status == "queued",
                        BackgroundJobRow.available_at <= now,
                        BackgroundJobRow.task.in_(self.allowed_tasks),
                    )
                    .order_by(*order_columns)
                    .limit(1)
                )
                # PostgreSQL locks the selected row and lets other replicas
                # skip it. The conditional UPDATE is the compare-and-swap
                # fallback for SQLite local development.
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    candidate_query = candidate_query.with_for_update(skip_locked=True)
                candidate = session.scalar(candidate_query)
                if candidate is None:
                    return None
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(BackgroundJobRow)
                        .where(
                            BackgroundJobRow.id == candidate.id,
                            BackgroundJobRow.status == "queued",
                        )
                        .values(
                            status="running",
                            attempts=BackgroundJobRow.attempts + 1,
                            started_at=now,
                            last_error="",
                            lane=task_lane(candidate.task),
                            lease_owner=self.worker_id,
                            lease_token=lease_token,
                            lease_expires_at=lease_expires_at,
                            heartbeat_at=now,
                            updated_at=now,
                        )
                    ),
                )
                if result.rowcount:
                    session.flush()
                    return session.get(BackgroundJobRow, candidate.id)
        return None

    def _renew_active_leases(self) -> int:
        with self._active_claims_lock:
            claims = list(self._active_claims.items())
        if not claims:
            return 0
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        renewed = 0
        with db_session() as session:
            for job_id, lease_token in claims:
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(BackgroundJobRow)
                        .where(
                            BackgroundJobRow.id == job_id,
                            BackgroundJobRow.status == "running",
                            BackgroundJobRow.lease_owner == self.worker_id,
                            BackgroundJobRow.lease_token == lease_token,
                        )
                        .values(
                            heartbeat_at=now,
                            lease_expires_at=lease_expires_at,
                            updated_at=now,
                        )
                    ),
                )
                renewed += int(result.rowcount or 0)
        return renewed

    def _complete(self, job_id: int, lease_token: str) -> bool:
        now = datetime.now(UTC)
        with db_session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(BackgroundJobRow)
                    .where(
                        BackgroundJobRow.id == job_id,
                        BackgroundJobRow.status == "running",
                        BackgroundJobRow.lease_owner == self.worker_id,
                        BackgroundJobRow.lease_token == lease_token,
                    )
                    .values(
                        status="completed",
                        finished_at=now,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        updated_at=now,
                        last_error="",
                    )
                ),
            )
            return bool(result.rowcount)

    def _claim_is_active(self, job_id: int, lease_token: str) -> bool:
        """Check whether this worker still owns a live fenced claim."""

        try:
            with db_session() as session:
                return (
                    session.scalar(
                        select(BackgroundJobRow.id).where(
                            BackgroundJobRow.id == job_id,
                            BackgroundJobRow.status == "running",
                            BackgroundJobRow.lease_owner == self.worker_id,
                            BackgroundJobRow.lease_token == lease_token,
                        )
                    )
                    is not None
                )
        except SQLAlchemyError:
            # A transient database failure must not kill healthy work. Lease
            # expiry and the next successful poll remain authoritative.
            _LOG.warning("could not verify background job claim %s", job_id, exc_info=True)
            return True

    @staticmethod
    def _terminate_process(process: _StoppableProcess) -> None:
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)

    def _retry_or_fail(self, job_id: int, lease_token: str, error: Exception) -> bool:
        now = datetime.now(UTC)
        failed_run_id: int | None = None
        failed_enrichment_id: int | None = None
        failed_project_brainstorm_id: int | None = None
        failed_repository_analysis_id: int | None = None
        failed_extraction: tuple[int, str, int | None] | None = None
        with db_session() as session:
            job = session.scalar(
                select(BackgroundJobRow)
                .where(
                    BackgroundJobRow.id == job_id,
                    BackgroundJobRow.status == "running",
                    BackgroundJobRow.lease_owner == self.worker_id,
                    BackgroundJobRow.lease_token == lease_token,
                )
                .with_for_update()
            )
            if job is None:
                return False
            # Provider responses and user content must not be copied into the
            # operational queue. The full traceback stays in container logs.
            job.last_error = f"{type(error).__name__}; see worker logs"
            job.updated_at = now
            job.lease_owner = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            if job.attempts >= job.max_attempts:
                if job.task == "_execute_live_companion_ask" and job.args:
                    try:
                        from sixsentences_server.interviews.live import (
                            terminalize_live_companion_ask,
                        )

                        # Keep the global lock order Session -> Ask -> Job.
                        # The dirty queue row must not autoflush before the
                        # helper acquires the owning live-session fence.
                        with session.no_autoflush:
                            terminalize_live_companion_ask(session, int(job.args[0]))
                    except (TypeError, ValueError):
                        _LOG.error(
                            "live-ask background job %s has an invalid ask id",
                            job.id,
                        )
                if job.task == "_execute_project_brainstorm_synthesis" and job.args:
                    try:
                        failed_project_brainstorm_id = int(job.args[0])
                    except (TypeError, ValueError):
                        _LOG.error(
                            "project-brainstorm job %s has an invalid synthesis id",
                            job.id,
                        )
                if job.task == "_execute_repository_analysis" and job.args:
                    try:
                        failed_repository_analysis_id = int(job.args[0])
                    except (TypeError, ValueError):
                        _LOG.error(
                            "repository-analysis job %s has an invalid analysis id",
                            job.id,
                        )
                job.status = "failed"
                job.finished_at = now
                if job.task == "_execute_paper_enrichment" and job.args:
                    try:
                        failed_enrichment_id = int(job.args[0])
                    except (TypeError, ValueError):
                        _LOG.error(
                            "paper-enrichment job %s has an invalid receipt id",
                            job.id,
                        )
                if job.task == "_execute_extraction":
                    failed_extraction = _extraction_job_scope(job)
                elif job.task in _RUN_SCOPED_TASKS and job.args:
                    try:
                        failed_run_id = int(job.args[0])
                    except (TypeError, ValueError):
                        _LOG.error(
                            "run-scoped background job %s has an invalid run id",
                            job.id,
                        )
            else:
                delay_index = min(
                    max(job.attempts - 1, 0),
                    len(_RETRY_DELAYS_SECONDS) - 1,
                )
                job.status = "queued"
                job.available_at = now + timedelta(seconds=_RETRY_DELAYS_SECONDS[delay_index])
                job.started_at = None
        # Keep the queue job and product receipt in separate transactions.
        # Cancel/delete owns Receipt -> Job; terminalization owns Job, commits,
        # then conditionally touches Receipt. This prevents an inverted row-lock
        # order while ensuring a concurrent user cancellation always wins.
        if failed_enrichment_id is not None:
            _terminalize_paper_enrichment_receipt(failed_enrichment_id, now=now)
        if failed_project_brainstorm_id is not None:
            _terminalize_project_brainstorm_receipt(failed_project_brainstorm_id)
        if failed_repository_analysis_id is not None:
            _terminalize_repository_analysis(failed_repository_analysis_id, now=now)
        if failed_extraction is not None:
            run_id, action_id, org_id = failed_extraction
            terminalize_extraction_action(run_id, action_id, expected_org_id=org_id)
        if failed_run_id is not None:
            self._terminalize_failed_run(failed_run_id)
        return True

    @staticmethod
    def _terminalize_failed_run(run_id: int) -> None:
        """Last-resort guard for a subprocess that never finalized its run."""

        try:
            with db_session() as session:
                # Serialize the last-resort failure with the API's cancel
                # transaction; a stale "running" read must not overwrite it.
                run = session.scalar(
                    select(Run)
                    .where(Run.id == run_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if run is None or is_terminal(run.status):
                    return
                mark_failed(run, _GENERIC_BACKGROUND_FAILURE)
                action_id = str((run.config or {}).get("cost_action_id") or "")
                if action_id:
                    finish_ai_action(session, action_id, status="failed")
                session.add(
                    RunEvent(
                        org_id=run.org_id,
                        run_id=run.id,
                        stage=StageName.REPORT.value,
                        event="run_failed",
                        payload={"control": "worker_guard"},
                    )
                )
        except Exception:
            # The queue row has already been committed as failed in a separate
            # transaction. A recovery fault is operator-visible without
            # turning the durable queue entry back into a retry loop.
            _LOG.exception("could not terminalize failed run %s", run_id)

    def run_once(self) -> bool:
        job = self._claim()
        if job is None:
            return False
        job_id = job.id
        lease_token = job.lease_token
        if lease_token is None:  # defensive: every new claim must be fenced
            raise JobExecutionError(f"background job {job_id} has no lease token")
        with self._active_claims_lock:
            self._active_claims[job_id] = lease_token
        try:
            args = list(job.args or [])
            kwargs = dict(job.kwargs or {})
            if self.run_in_subprocess:
                process = multiprocessing.get_context("spawn").Process(
                    target=_run_task_process,
                    args=(job.task, args, kwargs),
                    name=f"six-job-{job_id}",
                )
                process.start()
                deadline = time.monotonic() + job.timeout_seconds
                while process.is_alive():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._terminate_process(process)
                        raise JobTimeoutError(
                            f"background job exceeded {job.timeout_seconds} seconds"
                        )
                    # Cancellation fences the database lease. Poll often
                    # enough that an interactive tool call cannot keep running
                    # visibly after the user has stopped the run.
                    process.join(timeout=min(0.25, remaining))
                    if process.is_alive() and not self._claim_is_active(job_id, lease_token):
                        self._terminate_process(process)
                        return True
                if process.exitcode != 0:
                    raise JobExecutionError(
                        f"background job process exited with code {process.exitcode}"
                    )
            else:
                task = _resolve_task(job.task)
                task(*args, **kwargs)
        except Exception as exc:
            _LOG.exception("background job %s (%s) failed", job_id, job.task)
            if not self._retry_or_fail(job_id, lease_token, exc):
                _LOG.warning(
                    "discarded retry result for job %s because its lease was lost",
                    job_id,
                )
        else:
            if not self._complete(job_id, lease_token):
                _LOG.warning(
                    "discarded completion for job %s because its lease was lost",
                    job_id,
                )
        finally:
            with self._active_claims_lock:
                self._active_claims.pop(job_id, None)
        return True

    def _work_loop(self) -> None:
        while not self._draining.is_set() and not self._force_stopping.is_set():
            if not self.run_once():
                self._draining.wait(self.poll_seconds)

    def run_forever(self) -> None:
        recovered = self.recover_interrupted()
        cleaned = self.cleanup()
        _LOG.info(
            "background worker ready; lane=%s concurrency=%s recovered=%s cleaned=%s",
            self.lane,
            self.concurrency,
            recovered,
            cleaned,
        )
        self._record_replica_heartbeat(draining=False)
        self._heartbeat_thread.start()
        self._worker_threads = [
            threading.Thread(
                target=self._work_loop,
                name=f"six-job-{self.lane}-{slot + 1}",
                daemon=True,
            )
            for slot in range(self.concurrency)
        ]
        for thread in self._worker_threads:
            thread.start()
        last_cleanup = time.monotonic()
        last_recovery = time.monotonic()
        failed_threads: tuple[str, ...] = ()
        while not self._draining.is_set() and not self._force_stopping.is_set():
            dead = [thread.name for thread in self._worker_threads if not thread.is_alive()]
            if not self._heartbeat_thread.is_alive():
                dead.append(self._heartbeat_thread.name)
            if dead:
                failed_threads = tuple(sorted(dead))
                _LOG.error("worker thread stopped unexpectedly: %s", ", ".join(failed_threads))
                self._force_stopping.set()
                break
            if time.monotonic() - last_cleanup >= 3600:
                self.cleanup()
                last_cleanup = time.monotonic()
            if time.monotonic() - last_recovery >= max(15, self.lease_seconds // 2):
                recovered = self.recover_interrupted()
                if recovered:
                    _LOG.warning(
                        "requeued %s expired job lease(s) in lane %s",
                        recovered,
                        self.lane,
                    )
                last_recovery = time.monotonic()
            self._draining.wait(1)
        drain_deadline = time.monotonic() + self.drain_seconds
        self._record_replica_heartbeat(draining=True)
        while self.active_slots and not self._force_stopping.is_set():
            if time.monotonic() >= drain_deadline:
                _LOG.warning(
                    "worker drain deadline reached; lane=%s active=%s",
                    self.lane,
                    self.active_slots,
                )
                self._force_stopping.set()
                break
            time.sleep(0.1)
        if self._force_stopping.is_set() and self.active_slots:
            requeued = self._requeue_owned_claims()
            _LOG.warning(
                "requeued %s active job(s) during forced worker shutdown; lane=%s",
                requeued,
                self.lane,
            )
        self._stopping.set()
        for thread in self._worker_threads:
            thread.join(timeout=7)
        self._heartbeat_thread.join(timeout=2)
        try:
            self._record_replica_heartbeat(draining=True, stopped=True)
        except SQLAlchemyError:
            _LOG.warning("could not mark worker replica stopped", exc_info=True)
        if failed_threads:
            raise JobConfigurationError(
                f"worker thread stopped unexpectedly: {', '.join(failed_threads)}"
            )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    install_sensitive_log_filter()
    worker = DatabaseWorker()
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run_forever()


def worker_heartbeat_age_seconds(lane: str = "research") -> float | None:
    name = "job-worker-heartbeat" if lane == "all" else f"job-worker-{lane}-heartbeat"
    settings = get_settings()
    path = (settings.jobs_heartbeat_dir or Path(settings.data_dir)) / name
    try:
        written = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return max(0.0, (datetime.now(UTC) - _as_utc(written)).total_seconds())
