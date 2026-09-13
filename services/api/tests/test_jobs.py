"""Durable single-VM job queue behavior."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.exc import OperationalError

import sixsentences_server.jobs as jobs
from sixsentences_server.api.app import _execute_ask
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.db import (
    BackgroundJobRow,
    BrainstormProjectDocumentRow,
    BrainstormProjectSynthesisRow,
    DocumentRow,
    Org,
    PaperEnrichmentRow,
    Project,
    Run,
    RunEvent,
    User,
    WorkerReplicaRow,
    WorkRow,
    db_session,
    init_db,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.ops.health import collect_operator_health


class _FakeProcess:
    """Controllable process double for worker fencing and timeout tests."""

    def __init__(self, on_start: Callable[[], None] | None = None) -> None:
        self._alive = True
        self._on_start = on_start
        self.exitcode: int | None = None
        self.terminated = False
        self.killed = False

    def start(self) -> None:
        if self._on_start is not None:
            self._on_start()

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.killed = True
        self._alive = False
        self.exitcode = -9


class _FakeProcessContext:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process

    def Process(self, **_kwargs: object) -> _FakeProcess:
        return self.process


def _enable_database_jobs(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    monkeypatch.setenv("SIX_JOBS_POLL_SECONDS", "0.1")
    get_settings.cache_clear()
    init_db()


def test_enqueue_persists_registered_job(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    background = BackgroundTasks()
    with db_session() as session:
        org = Org(name="queue-owner")
        session.add(org)
        session.flush()
        org_id = org.id
    job_id = jobs.enqueue_job(background, _execute_ask, 42, "auto", queue_org_id=org_id)
    assert job_id is not None
    assert background.tasks == []
    with db_session() as session:
        row = session.get(BackgroundJobRow, job_id)
        assert row is not None
        assert row.task == "_execute_ask"
        assert row.org_id == org_id
        assert row.lane == "chat"
        assert row.args == [42, "auto"]
        assert row.status == "queued"
        assert row.attempts == 0


def test_worker_retries_then_completes(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_database_jobs(settings, monkeypatch)
    calls = 0

    def flaky(_run_id: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("provider unavailable")

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: flaky)
    with db_session() as session:
        row = BackgroundJobRow(task="_execute_ask", args=[7], kwargs={}, max_attempts=3)
        session.add(row)
        session.flush()
        job_id = row.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    assert worker.run_once()
    with db_session() as session:
        row = session.get(BackgroundJobRow, job_id)
        assert row is not None
        assert row.status == "queued"
        assert row.attempts == 1
        assert row.last_error == "TimeoutError; see worker logs"
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert worker.run_once()
    with db_session() as session:
        row = session.get(BackgroundJobRow, job_id)
        assert row is not None
        assert row.status == "completed"
        assert row.attempts == 2
        assert row.finished_at is not None


def test_final_worker_failure_terminalizes_the_owned_run(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)

    def fail(_run_id: int) -> None:
        raise RuntimeError("worker process crashed")

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: fail)
    with db_session() as session:
        org = Org(name="failed-run-owner")
        session.add(org)
        session.flush()
        run = Run(org_id=org.id, question="question", status="running")
        session.add(run)
        session.flush()
        row = BackgroundJobRow(
            org_id=org.id, task="_execute_ask", args=[run.id], kwargs={}, max_attempts=1
        )
        session.add(row)
        session.flush()
        job_id = row.id
        run_id = run.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    assert worker.run_once()
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        run = session.get(Run, run_id)
        assert job is not None and job.status == "failed"
        assert run is not None and run.status == "failed"
        assert run.finished_at is not None
        assert "worker process crashed" not in (run.error or "")
        event = session.query(RunEvent).filter_by(run_id=run_id, event="run_failed").one()
        assert event.payload == {"control": "worker_guard"}


def test_execute_ask_recovers_a_failed_database_transaction(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_db()
    with db_session() as session:
        org = Org(name="ask-rollback-owner")
        session.add(org)
        session.flush()
        run = Run(org_id=org.id, question="question", status="pending")
        session.add(run)
        session.add(WorkRow(id="W-DUPLICATE", title="existing", payload={}))
        session.flush()
        run_id = run.id

    def violate_unique_constraint(session, _run, *, pool) -> None:
        del pool
        session.add(WorkRow(id="W-DUPLICATE", title="duplicate", payload={}))
        session.flush()

    monkeypatch.setattr("sixsentences_server.api.app.execute_ask", violate_unique_constraint)
    _execute_ask(run_id)
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.finished_at is not None
        assert "IntegrityError" not in (run.error or "")
        assert session.query(WorkRow).filter_by(id="W-DUPLICATE").count() == 1


def test_claim_clears_error_from_previous_attempt(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        session.add(
            BackgroundJobRow(
                task="_execute_ask",
                args=[7],
                kwargs={},
                attempts=1,
                last_error="JobTimeoutError; see worker logs",
            )
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    claimed = worker._claim()
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempts == 2
    assert claimed.last_error == ""
    assert claimed.lease_token is not None
    assert worker._complete(claimed.id, claimed.lease_token)


def test_worker_recovers_interrupted_jobs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        session.add(
            BackgroundJobRow(
                task="_execute_ask",
                args=[9],
                kwargs={},
                status="running",
                attempts=1,
                started_at=datetime.now(UTC),
            )
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    assert worker.recover_interrupted() == 1
    with db_session() as session:
        row = session.query(BackgroundJobRow).one()
        assert row.status == "queued"
        assert row.started_at is None
        assert "lease expired" in row.last_error


def test_worker_does_not_recover_another_replica_live_lease(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    now = datetime.now(UTC)
    with db_session() as session:
        session.add(
            BackgroundJobRow(
                task="_execute_ask",
                args=[9],
                kwargs={},
                status="running",
                attempts=1,
                started_at=now,
                lease_owner="other-worker",
                lease_token="other-claim",
                lease_expires_at=now + timedelta(minutes=5),
                heartbeat_at=now,
            )
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    assert worker.recover_interrupted() == 0
    with db_session() as session:
        row = session.query(BackgroundJobRow).one()
        assert row.status == "running"
        assert row.lease_owner == "other-worker"
        assert row.lease_token == "other-claim"


def test_expired_lease_is_reclaimed_and_fenced(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    now = datetime.now(UTC)
    with db_session() as session:
        session.add(
            BackgroundJobRow(
                task="_execute_ask",
                args=[9],
                kwargs={},
                status="running",
                attempts=1,
                started_at=now - timedelta(minutes=5),
                lease_owner="dead-worker",
                lease_token="expired-claim",
                lease_expires_at=now - timedelta(seconds=1),
                heartbeat_at=now - timedelta(minutes=2),
            )
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    assert worker.recover_interrupted() == 1
    claimed = worker._claim()
    assert claimed is not None
    assert claimed.lease_owner == worker.worker_id
    assert claimed.lease_token not in {None, "expired-claim"}
    assert not worker._complete(claimed.id, "expired-claim")
    assert claimed.lease_token is not None
    assert worker._complete(claimed.id, claimed.lease_token)
    with db_session() as session:
        row = session.query(BackgroundJobRow).one()
        assert row.status == "completed"
        assert row.attempts == 2
        assert row.lease_owner is None
        assert row.lease_token is None


def test_worker_renews_only_its_active_claims(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        session.add(BackgroundJobRow(task="_execute_ask", args=[9], kwargs={}))
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    claimed = worker._claim()
    assert claimed is not None
    assert claimed.lease_token is not None
    original_expiry = claimed.lease_expires_at
    with worker._active_claims_lock:
        worker._active_claims[claimed.id] = claimed.lease_token
    assert worker._renew_active_leases() == 1
    with db_session() as session:
        row = session.get(BackgroundJobRow, claimed.id)
        assert row is not None
        assert row.heartbeat_at is not None
        assert row.lease_expires_at is not None
        assert original_expiry is not None
        assert jobs._as_utc(row.lease_expires_at) >= jobs._as_utc(original_expiry)


def test_heartbeat_survives_a_transient_database_failure(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    calls = 0

    def flaky_renewal() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("UPDATE background_jobs", {}, Exception("locked"))
        worker.stop()
        return 0

    monkeypatch.setattr(worker, "_renew_active_leases", flaky_renewal)
    monkeypatch.setattr(worker._stopping, "wait", lambda _seconds: None)
    worker._heartbeat_loop()
    assert calls == 2


def test_health_receipt_requires_a_successful_database_heartbeat(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    worker = jobs.DatabaseWorker(run_in_subprocess=False)

    def failed_renewal() -> int:
        worker.stop()
        raise OperationalError("UPDATE background_jobs", {}, Exception("database unavailable"))

    monkeypatch.setattr(worker, "_renew_active_leases", failed_renewal)
    monkeypatch.setattr(worker._stopping, "wait", lambda _seconds: None)
    worker._heartbeat_loop()
    assert not worker.heartbeat_path.exists()


def test_worker_fails_closed_when_a_work_thread_stops(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    worker = jobs.DatabaseWorker(run_in_subprocess=False)
    monkeypatch.setattr(worker, "_work_loop", lambda: None)

    with pytest.raises(jobs.JobConfigurationError, match="worker thread stopped unexpectedly"):
        worker.run_forever()


def test_worker_records_replica_capacity_and_drain_state(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat", concurrency=3)
    worker._record_replica_heartbeat(draining=False)
    with db_session() as session:
        row = session.get(WorkerReplicaRow, worker.worker_id)
        assert row is not None
        assert row.lane == "chat"
        assert row.concurrency == 3
        assert row.active_slots == 0
        assert row.draining is False
        assert row.stopped_at is None
    worker.stop()
    worker._record_replica_heartbeat(stopped=True)
    with db_session() as session:
        row = session.get(WorkerReplicaRow, worker.worker_id)
        assert row is not None
        assert row.draining is True
        assert row.stopped_at is not None


def test_draining_worker_stops_claiming_new_jobs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        session.add(BackgroundJobRow(task="_execute_ask", args=[7], kwargs={}))
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    worker.stop()
    assert worker.run_once() is False
    with db_session() as session:
        assert session.query(BackgroundJobRow).one().status == "queued"


def test_graceful_drain_finishes_active_job_without_claiming_the_next(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def slow_task(_run_id: int) -> None:
        started.set()
        assert release.wait(timeout=3)

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: slow_task)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(task="_execute_ask", args=[1], kwargs={}),
                BackgroundJobRow(task="_execute_ask", args=[2], kwargs={}),
            ]
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    thread = threading.Thread(target=worker.run_forever)
    thread.start()
    assert started.wait(timeout=2)
    worker.stop()
    release.set()
    thread.join(timeout=4)
    assert not thread.is_alive()
    with db_session() as session:
        statuses = [
            row.status for row in session.query(BackgroundJobRow).order_by(BackgroundJobRow.id)
        ]
    assert statuses == ["completed", "queued"]


def test_forced_drain_requeues_owned_claim(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        session.add(BackgroundJobRow(task="_execute_ask", args=[1], kwargs={}))
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    claimed = worker._claim()
    assert claimed is not None and claimed.lease_token is not None
    with worker._active_claims_lock:
        worker._active_claims[claimed.id] = claimed.lease_token
    worker.stop()
    worker.stop()
    assert worker._force_stopping.is_set()
    assert worker._requeue_owned_claims() == 1
    with db_session() as session:
        row = session.get(BackgroundJobRow, claimed.id)
        assert row is not None
        assert row.status == "queued"
        assert row.lease_owner is None
        assert row.lease_token is None


def test_operator_health_aggregates_every_live_replica(
    settings: Settings, corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    del corpus
    _enable_database_jobs(settings, monkeypatch)
    now = datetime.now(UTC)
    with db_session() as session:
        for lane in jobs.PRODUCTION_LANES:
            session.add(
                WorkerReplicaRow(
                    worker_id=f"{lane}-one",
                    lane=lane,
                    hostname="test-host",
                    pid=42,
                    release_revision="test-release",
                    concurrency=2,
                    active_slots=1 if lane == "research" else 0,
                    heartbeat_at=now,
                )
            )
    health = collect_operator_health()
    assert health["status"] == "ok"
    assert health["workers"] == {
        "live": len(jobs.PRODUCTION_LANES),
        "required": True,
    }


def test_operator_health_does_not_hide_a_stale_replica(
    settings: Settings, corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    del corpus
    _enable_database_jobs(settings, monkeypatch)
    now = datetime.now(UTC)
    with db_session() as session:
        for lane in jobs.PRODUCTION_LANES:
            session.add(
                WorkerReplicaRow(
                    worker_id=f"{lane}-healthy",
                    lane=lane,
                    hostname="test-host",
                    pid=42,
                    release_revision="test-release",
                    concurrency=1,
                    heartbeat_at=now,
                )
            )
        session.add(
            WorkerReplicaRow(
                worker_id="chat-crashed",
                lane="chat",
                hostname="old-host",
                pid=41,
                release_revision="old-release",
                concurrency=1,
                heartbeat_at=now - timedelta(minutes=16),
            )
        )
    health = collect_operator_health()
    assert health["status"] == "ok"
    assert health["workers"]["live"] == len(jobs.PRODUCTION_LANES)


def test_compile_worker_only_claims_compile_jobs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    completed: list[int] = []

    def record(doc_id: int) -> None:
        completed.append(doc_id)

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: record)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(task="_execute", args=[1], kwargs={}),
                BackgroundJobRow(task="_execute_writer_compile", args=[2], kwargs={}),
            ]
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="compile")
    assert worker.run_once()
    assert completed == [2]
    with db_session() as session:
        rows = {row.task: row.status for row in session.query(BackgroundJobRow).all()}
        assert rows["_execute"] == "queued"
        assert rows["_execute_writer_compile"] == "completed"


def test_production_lanes_partition_every_registered_task() -> None:
    lanes = [jobs.worker_lane_tasks(lane) for lane in jobs.PRODUCTION_LANES]
    for index, tasks in enumerate(lanes):
        assert all(not tasks & other for other in lanes[index + 1 :])
    assert frozenset().union(*lanes) == jobs.worker_lane_tasks("all")
    assert jobs.task_lane("_execute") == "research"
    assert jobs.task_lane("_execute_ask") == "chat"
    assert jobs.task_lane("_execute_extraction") == "documents"
    assert jobs.task_lane("_execute_writer_compile") == "compile"
    assert jobs.task_lane("_execute_figure") == "media"
    assert jobs.task_lane("_notify_voice_session") == "notifications"


def test_chat_worker_does_not_claim_research_jobs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    completed: list[int] = []
    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: completed.append)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(task="_execute", args=[1], kwargs={}),
                BackgroundJobRow(task="_execute_ask", args=[2], kwargs={}),
            ]
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    assert worker.run_once()
    assert completed == [2]
    with db_session() as session:
        rows = {row.task: row.status for row in session.query(BackgroundJobRow).all()}
        assert rows["_execute"] == "queued"
        assert rows["_execute_ask"] == "completed"


def test_chat_lane_stays_available_during_long_research(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    research_started = threading.Event()
    release_research = threading.Event()
    chat_done = threading.Event()

    def resolve(name: str):
        if name == "_execute":

            def long_research(_run_id: int) -> None:
                research_started.set()
                assert release_research.wait(timeout=3)

            return long_research

        def quick_answer(_run_id: int) -> None:
            chat_done.set()

        return quick_answer

    monkeypatch.setattr(jobs, "_resolve_task", resolve)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(task="_execute", args=[1], kwargs={}),
                BackgroundJobRow(task="_execute_ask", args=[2], kwargs={}),
            ]
        )
    research = jobs.DatabaseWorker(run_in_subprocess=False, lane="research")
    chat = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    research_thread = threading.Thread(target=research.run_once)
    research_thread.start()
    assert research_started.wait(timeout=2)
    assert chat.run_once()
    assert chat_done.is_set()
    release_research.set()
    research_thread.join(timeout=3)
    assert not research_thread.is_alive()
    with db_session() as session:
        assert {row.status for row in session.query(BackgroundJobRow).all()} == {"completed"}


def test_lane_recovery_does_not_touch_another_live_worker(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(
                    task="_resume",
                    args=[1],
                    kwargs={},
                    status="running",
                    started_at=datetime.now(UTC),
                ),
                BackgroundJobRow(
                    task="_execute_writer_compile",
                    args=[2],
                    kwargs={},
                    status="running",
                    started_at=datetime.now(UTC),
                ),
            ]
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="compile")
    assert worker.recover_interrupted() == 1
    with db_session() as session:
        rows = {row.task: row.status for row in session.query(BackgroundJobRow).all()}
        assert rows["_resume"] == "running"
        assert rows["_execute_writer_compile"] == "queued"


def test_worker_pool_uses_configured_concurrency(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    both_started = threading.Barrier(2)
    completed = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def concurrent_task(_run_id: int) -> None:
        nonlocal calls
        both_started.wait(timeout=2)
        with calls_lock:
            calls += 1
            if calls == 2:
                completed.set()

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: concurrent_task)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(task="_execute", args=[1], kwargs={}),
                BackgroundJobRow(task="_execute", args=[2], kwargs={}),
            ]
        )
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="research", concurrency=2)
    thread = threading.Thread(target=worker.run_forever)
    thread.start()
    assert completed.wait(timeout=3)
    worker.stop()
    thread.join(timeout=3)
    assert not thread.is_alive()
    with db_session() as session:
        assert {row.status for row in session.query(BackgroundJobRow).all()} == {"completed"}


def test_two_worker_replicas_do_not_double_claim_jobs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    completed: list[int] = []
    completed_lock = threading.Lock()

    def record(run_id: int) -> None:
        with completed_lock:
            completed.append(run_id)

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: record)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(task="_execute", args=[1], kwargs={}),
                BackgroundJobRow(task="_execute", args=[2], kwargs={}),
            ]
        )
    workers = [jobs.DatabaseWorker(run_in_subprocess=False, lane="research") for _ in range(2)]
    threads = [threading.Thread(target=worker.run_once) for worker in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert sorted(completed) == [1, 2]
    with db_session() as session:
        rows = session.query(BackgroundJobRow).all()
        assert {row.status for row in rows} == {"completed"}
        assert {row.attempts for row in rows} == {1}


def test_claim_prefers_an_org_without_running_lane_work(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    now = datetime.now(UTC)
    with db_session() as session:
        busy_org = Org(name="busy")
        waiting_org = Org(name="waiting")
        session.add_all([busy_org, waiting_org])
        session.flush()
        session.add_all(
            [
                BackgroundJobRow(
                    org_id=busy_org.id,
                    task="_execute",
                    lane="research",
                    args=[1],
                    kwargs={},
                    status="running",
                    attempts=1,
                    started_at=now,
                    lease_owner="other-worker",
                    lease_token="live-busy-claim",
                    lease_expires_at=now + timedelta(minutes=5),
                    heartbeat_at=now,
                ),
                BackgroundJobRow(
                    org_id=busy_org.id, task="_execute", lane="research", args=[2], kwargs={}
                ),
                BackgroundJobRow(
                    org_id=waiting_org.id, task="_execute", lane="research", args=[3], kwargs={}
                ),
            ]
        )
        waiting_org_id = waiting_org.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="research")
    claimed = worker._claim()
    assert claimed is not None
    assert claimed.org_id == waiting_org_id


def test_worker_executes_registered_task_in_subprocess(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    background = BackgroundTasks()
    job_id = jobs.enqueue_job(background, _execute_ask, 999999, "auto")
    assert job_id is not None
    worker = jobs.DatabaseWorker()
    assert worker.run_once()
    with db_session() as session:
        row = session.get(BackgroundJobRow, job_id)
        assert row is not None
        assert row.status == "completed"


def test_worker_timeout_terminates_process_and_terminalizes_job(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        row = BackgroundJobRow(
            task="_execute_ask", args=[999999], kwargs={}, max_attempts=1, timeout_seconds=30
        )
        session.add(row)
        session.flush()
        job_id = row.id
    process = _FakeProcess()
    context = _FakeProcessContext(process)
    clock = iter((0.0, 31.0))
    monkeypatch.setattr(jobs.multiprocessing, "get_context", lambda _method: context)
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(clock))
    worker = jobs.DatabaseWorker()
    assert worker.run_once()
    assert process.terminated
    assert not process.killed
    with db_session() as session:
        row = session.get(BackgroundJobRow, job_id)
        assert row is not None
        assert row.status == "failed"
        assert row.finished_at is not None
        assert row.last_error == "JobTimeoutError; see worker logs"


def test_run_cancellation_fences_and_terminates_live_subprocess(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    run_id = 41
    with db_session() as session:
        row = BackgroundJobRow(task="_execute_ask", args=[run_id, "auto"], kwargs={})
        session.add(row)
        session.flush()
        job_id = row.id

    def cancel_claim() -> None:
        with db_session() as session:
            assert jobs.cancel_run_jobs(session, run_id) == 1

    process = _FakeProcess(on_start=cancel_claim)
    context = _FakeProcessContext(process)
    monkeypatch.setattr(jobs.multiprocessing, "get_context", lambda _method: context)
    worker = jobs.DatabaseWorker()
    assert worker.run_once()
    assert process.terminated
    with db_session() as session:
        row = session.get(BackgroundJobRow, job_id)
        assert row is not None
        assert row.status == "cancelled"
        assert row.lease_owner is None
        assert row.lease_token is None


def test_lost_lease_terminates_stale_process_without_overwriting_new_owner(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    with db_session() as session:
        row = BackgroundJobRow(task="_execute_ask", args=[77, "auto"], kwargs={})
        session.add(row)
        session.flush()
        job_id = row.id

    def replace_claim() -> None:
        with db_session() as session:
            row = session.get(BackgroundJobRow, job_id)
            assert row is not None
            row.lease_owner = "replacement-worker"
            row.lease_token = "replacement-token"
            row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)

    process = _FakeProcess(on_start=replace_claim)
    context = _FakeProcessContext(process)
    monkeypatch.setattr(jobs.multiprocessing, "get_context", lambda _method: context)
    worker = jobs.DatabaseWorker()
    assert worker.run_once()
    assert process.terminated
    with db_session() as session:
        row = session.get(BackgroundJobRow, job_id)
        assert row is not None
        assert row.status == "running"
        assert row.lease_owner == "replacement-worker"
        assert row.lease_token == "replacement-token"


def test_cancel_run_jobs_cancels_matching_research_and_quick_answer_work(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    now = datetime.now(UTC)
    with db_session() as session:
        session.add_all(
            [
                BackgroundJobRow(
                    task="_resume",
                    lane="research",
                    args=[41, {}, {}],
                    kwargs={},
                    status="running",
                    lease_owner="worker",
                    lease_token="cancel-target",
                    lease_expires_at=now + timedelta(minutes=1),
                ),
                BackgroundJobRow(task="_execute", lane="research", args=[42], kwargs={}),
                BackgroundJobRow(task="_execute_ask", lane="chat", args=[41, "auto"], kwargs={}),
            ]
        )
    with db_session() as session:
        assert jobs.cancel_run_jobs(session, 41) == 2
    with db_session() as session:
        rows = {row.task: row for row in session.query(BackgroundJobRow).all()}
        target = rows["_resume"]
        assert target.status == "cancelled"
        assert target.finished_at is not None
        assert target.lease_owner is None
        assert target.lease_token is None
        assert rows["_execute"].status == "queued"
        assert rows["_execute_ask"].status == "cancelled"
        assert rows["_execute_ask"].finished_at is not None


def _paper_enrichment_queue_fixture() -> tuple[int, int, int]:
    with db_session() as session:
        org = Org(name="paper-enrichment-queue")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="paper-enrichment-queue@example.org",
            password_hash="test",
            role="owner",
        )
        session.add(user)
        work = WorkRow(id="W987654", doi="10.5555/queue", title="Queue paper")
        session.add(work)
        session.flush()
        document = DocumentRow(
            org_id=org.id,
            work_id=work.id,
            status="not_retrieved",
            source="browser_capture",
            url="https://example.org/queue",
            text_status="not_retrieved",
        )
        session.add(document)
        session.flush()
        receipt = PaperEnrichmentRow(
            org_id=org.id,
            user_id=user.id,
            document_id=document.id,
            request_id="enrich:queue-guard",
            identity_kind="doi",
            identity_value="10.5555/queue",
            source_revision="sha256:" + "a" * 64,
            status="queued",
        )
        session.add(receipt)
        session.flush()
        return (org.id, document.id, receipt.id)


def test_paper_enrichment_queue_failure_terminalizes_only_its_receipt(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    org_id, _, receipt_id = _paper_enrichment_queue_fixture()

    def crash(_receipt_id: int) -> None:
        raise RuntimeError("provider process crashed")

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: crash)
    with db_session() as session:
        run = Run(org_id=org_id, question="unrelated", status="running")
        session.add(run)
        session.flush()
        assert run.id == receipt_id
        job = BackgroundJobRow(
            org_id=org_id,
            task="_execute_paper_enrichment",
            lane="documents",
            args=[receipt_id],
            kwargs={},
            max_attempts=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="documents")
    assert worker.run_once()
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        receipt = session.get(PaperEnrichmentRow, receipt_id)
        run = session.get(Run, receipt_id)
        assert job is not None and job.status == "failed"
        assert receipt is not None and receipt.status == "failed"
        assert receipt.error_code == "paper_enrichment_worker_failed"
        assert "provider process crashed" not in receipt.error_message
        assert run is not None and run.status == "running"


def test_paper_enrichment_user_cancel_wins_over_queue_failure_finalizer(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    org_id, _, receipt_id = _paper_enrichment_queue_fixture()

    def cancel_then_crash(_receipt_id: int) -> None:
        with db_session() as session:
            receipt = session.get(PaperEnrichmentRow, receipt_id)
            assert receipt is not None
            receipt.status = "cancelled"
            receipt.finished_at = datetime.now(UTC)
        raise RuntimeError("worker observed cancellation while failing")

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: cancel_then_crash)
    with db_session() as session:
        job = BackgroundJobRow(
            org_id=org_id,
            task="_execute_paper_enrichment",
            lane="documents",
            args=[receipt_id],
            kwargs={},
            max_attempts=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="documents")
    assert worker.run_once()
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        receipt = session.get(PaperEnrichmentRow, receipt_id)
        assert job is not None and job.status == "failed"
        assert receipt is not None and receipt.status == "cancelled"
        assert receipt.error_code == ""


def _project_brainstorm_queue_fixture() -> tuple[int, int]:
    with db_session() as session:
        org = Org(name="project-brainstorm-queue")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="project-brainstorm-queue@example.org",
            password_hash="test",
            role="owner",
        )
        project = Project(org_id=org.id, name="Queue project")
        session.add_all([user, project])
        session.flush()
        document = BrainstormProjectDocumentRow(
            org_id=org.id,
            user_id=user.id,
            project_id=project.id,
            revision=0,
            result={},
            source_sessions=[],
        )
        session.add(document)
        session.flush()
        receipt = BrainstormProjectSynthesisRow(
            org_id=org.id,
            user_id=user.id,
            project_id=project.id,
            document_id=document.id,
            client_request_id="project-queue-request",
            request_sha256="a" * 64,
            mode="selected",
            output_language="de",
            base_revision=0,
            context_snapshot={},
            action_id="project-queue-action",
            base_credits=1,
            status="pending",
            result={},
            error="",
            error_code="",
        )
        session.add(receipt)
        session.flush()
        return (org.id, receipt.id)


def test_project_brainstorm_queue_commits_job_before_receipt_finalizer(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    org_id, receipt_id = _project_brainstorm_queue_fixture()

    def crash(_receipt_id: int) -> None:
        raise RuntimeError("provider process crashed")

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: crash)
    with db_session() as session:
        job = BackgroundJobRow(
            org_id=org_id,
            task="_execute_project_brainstorm_synthesis",
            lane="chat",
            args=[receipt_id],
            kwargs={},
            max_attempts=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    observed_status: list[str] = []
    real_finalizer = jobs._terminalize_project_brainstorm_receipt

    def assert_job_committed(synthesis_id: int) -> None:
        with db_session() as session:
            job = session.get(BackgroundJobRow, job_id)
            assert job is not None
            observed_status.append(job.status)
        real_finalizer(synthesis_id)

    monkeypatch.setattr(jobs, "_terminalize_project_brainstorm_receipt", assert_job_committed)
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    assert worker.run_once()
    assert observed_status == ["failed"]
    with db_session() as session:
        receipt = session.get(BrainstormProjectSynthesisRow, receipt_id)
        assert receipt is not None and receipt.status == "failed"
        assert receipt.error_code == "project_brainstorm_unavailable"


def test_recovery_terminalizes_exhausted_project_brainstorm_without_requeue(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    org_id, receipt_id = _project_brainstorm_queue_fixture()
    now = datetime.now(UTC)
    with db_session() as session:
        job = BackgroundJobRow(
            org_id=org_id,
            task="_execute_project_brainstorm_synthesis",
            lane="chat",
            args=[receipt_id],
            kwargs={},
            status="running",
            attempts=3,
            max_attempts=3,
            lease_owner="dead-project-worker",
            lease_token="expired-project-claim",
            lease_expires_at=now - timedelta(seconds=1),
            heartbeat_at=now - timedelta(seconds=2),
        )
        session.add(job)
        session.flush()
        job_id = job.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    assert worker.recover_interrupted() == 1
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        receipt = session.get(BrainstormProjectSynthesisRow, receipt_id)
        assert job is not None and job.status == "failed"
        assert receipt is not None and receipt.status == "failed"
        assert receipt.error_code == "project_brainstorm_unavailable"


def test_recovery_terminalizes_an_exhausted_paper_enrichment_claim(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    org_id, _, receipt_id = _paper_enrichment_queue_fixture()
    now = datetime.now(UTC)
    with db_session() as session:
        job = BackgroundJobRow(
            org_id=org_id,
            task="_execute_paper_enrichment",
            lane="documents",
            args=[receipt_id],
            kwargs={},
            status="running",
            attempts=1,
            max_attempts=1,
            lease_owner="dead-worker",
            lease_token="expired-claim",
            lease_expires_at=now - timedelta(seconds=1),
            heartbeat_at=now - timedelta(seconds=2),
        )
        session.add(job)
        session.flush()
        job_id = job.id
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="documents")
    assert worker.recover_interrupted() == 1
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        receipt = session.get(PaperEnrichmentRow, receipt_id)
        assert job is not None and job.status == "failed"
        assert job.finished_at is not None and job.lease_token is None
        assert receipt is not None and receipt.status == "failed"
        assert receipt.error_code == "paper_enrichment_worker_failed"


def test_cancel_run_jobs_ignores_paper_receipt_id_collision(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_database_jobs(settings, monkeypatch)
    org_id, _, receipt_id = _paper_enrichment_queue_fixture()
    with db_session() as session:
        run = Run(org_id=org_id, question="same numeric id", status="running")
        session.add(run)
        session.flush()
        assert run.id == receipt_id
        job = BackgroundJobRow(
            org_id=org_id,
            task="_execute_paper_enrichment",
            lane="documents",
            args=[receipt_id],
            kwargs={},
        )
        session.add(job)
        session.flush()
        job_id = job.id
    with db_session() as session:
        assert jobs.cancel_run_jobs(session, receipt_id) == 0
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        assert job is not None and job.status == "queued"
