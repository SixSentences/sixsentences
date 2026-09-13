"""Durable, tenant-bound controls across independent worker processes."""

import multiprocessing
import os
from multiprocessing.connection import Connection

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core import control
from sixsentences_server.core.db import BackgroundJobRow, Org, Run, RunEvent, db_session, init_db


def _poll_in_spawned_worker(
    database_url: str, run_id: int, org_id: int, sender: Connection
) -> None:
    """Import/read the ledger in a clean interpreter, like production jobs."""

    os.environ["SIX_DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        sender.send(control.poll(run_id, org_id=org_id))
    finally:
        sender.close()


def _spawned_signal(settings: Settings, run_id: int, org_id: int) -> str:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_poll_in_spawned_worker,
        args=(settings.resolved_database_url, run_id, org_id, sender),
    )
    process.start()
    sender.close()
    try:
        assert receiver.poll(15), "isolated control reader did not respond"
        value = receiver.recv()
        process.join(timeout=5)
        assert process.exitcode == 0
        assert isinstance(value, str)
        return value
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def _seed_run() -> tuple[int, int, int]:
    init_db()
    with db_session() as session:
        owner = Org(name="control-owner")
        other = Org(name="control-other")
        session.add_all([owner, other])
        session.flush()
        run = Run(org_id=owner.id, question="Synthetic run control", status="running")
        session.add(run)
        session.flush()
        return run.id, owner.id, other.id


def test_pause_and_resume_are_visible_in_fresh_worker_processes(settings: Settings) -> None:
    run_id, org_id, _ = _seed_run()
    assert _spawned_signal(settings, run_id, org_id) == ""
    with db_session() as session:
        control.request(run_id, control.PAUSE, org_id=org_id, session=session)
    assert _spawned_signal(settings, run_id, org_id) == control.PAUSE
    with db_session() as session:
        marker = control.clear(run_id, org_id=org_id, session=session)
        assert marker is not None
    assert _spawned_signal(settings, run_id, org_id) == ""


def test_commands_require_the_exact_tenant_and_preserve_other_runs(settings: Settings) -> None:
    del settings
    run_id, org_id, other_org_id = _seed_run()
    with db_session() as session, pytest.raises(LookupError, match="run not found"):
        control.request(run_id, control.PAUSE, org_id=other_org_id, session=session)
    assert control.poll(run_id, org_id=org_id) == ""
    assert control.poll(run_id, org_id=other_org_id) == control.CANCEL
    with db_session() as session:
        assert session.scalar(select(RunEvent.id).where(RunEvent.run_id == run_id)) is None


def test_cancel_is_not_cleared_or_replaced_by_a_later_pause(settings: Settings) -> None:
    del settings
    run_id, org_id, _ = _seed_run()
    with db_session() as session:
        cancelled_id = control.request(run_id, control.CANCEL, org_id=org_id, session=session)
        assert (
            control.request(
                run_id,
                control.PAUSE,
                org_id=org_id,
                session=session,
            )
            == cancelled_id
        )
        assert control.clear(run_id, org_id=org_id, session=session) is None
    assert control.poll(run_id, org_id=org_id) == control.CANCEL


def test_failed_control_transaction_is_not_acknowledged_by_workers(settings: Settings) -> None:
    del settings
    run_id, org_id, _ = _seed_run()
    with pytest.raises(RuntimeError, match="rollback fixture"), db_session() as session:
        control.request(run_id, control.PAUSE, org_id=org_id, session=session)
        raise RuntimeError("rollback fixture")
    assert control.poll(run_id, org_id=org_id) == ""


def test_pause_does_not_revoke_a_healthy_job_lease(settings: Settings) -> None:
    del settings
    run_id, org_id, _ = _seed_run()
    with db_session() as session:
        job = BackgroundJobRow(
            org_id=org_id,
            task="_execute",
            lane="research",
            args=[run_id],
            status="running",
            lease_owner="synthetic-worker",
            lease_token="synthetic-lease",
        )
        session.add(job)
        session.flush()
        job_id = job.id
        control.request(run_id, control.PAUSE, org_id=org_id, session=session)
    with db_session() as session:
        stored = session.get(BackgroundJobRow, job_id)
        assert stored is not None
        assert (stored.status, stored.lease_token) == ("running", "synthetic-lease")


def test_resume_clear_and_job_roll_back_together(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    del settings
    import sixsentences_server.api.app as app_module
    from sixsentences_server.jobs import enqueue_job

    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    run_id, org_id, _ = _seed_run()
    with db_session() as session:
        control.request(run_id, control.PAUSE, org_id=org_id, session=session)
    with pytest.raises(RuntimeError, match="rollback fixture"), db_session() as session:
        marker = control.clear(run_id, org_id=org_id, session=session)
        enqueue_job(
            BackgroundTasks(),
            app_module._resume_paused,
            run_id,
            control_request_id=marker,
            session=session,
            queue_org_id=org_id,
        )
        raise RuntimeError("rollback fixture")
    assert control.poll(run_id, org_id=org_id) == control.PAUSE
    with db_session() as session:
        assert session.scalar(select(BackgroundJobRow.id)) is None


def test_superseded_resume_cannot_consume_a_new_pause(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    del settings
    import sixsentences_server.api.app as app_module

    run_id, org_id, _ = _seed_run()
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.status = "paused"
        marker = control.clear(run_id, org_id=org_id, session=session)
        control.request(run_id, control.PAUSE, org_id=org_id, session=session)

    def forbidden_execute(*args: object, **kwargs: object) -> None:
        pytest.fail("a stale queued resume restarted a later pause")

    monkeypatch.setattr(app_module, "execute_run", forbidden_execute)
    app_module._resume_paused(run_id, control_request_id=marker)
    assert control.poll(run_id, org_id=org_id) == control.PAUSE
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None and run.status == "paused"
