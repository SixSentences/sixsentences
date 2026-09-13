"""Run state machine: validated transitions."""

import pytest

from sixsentences_server.core.db import Run
from sixsentences_server.core.models import RunStatus
from sixsentences_server.core.state import InvalidTransition, is_terminal, mark_failed, set_status


def _run(status: str) -> Run:
    return Run(id=1, org_id=1, project_id=1, question="q", status=status)


def test_valid_lifecycle_including_the_gate() -> None:
    run = _run("pending")
    set_status(run, RunStatus.RUNNING)
    set_status(run, RunStatus.AWAITING_PROTOCOL_APPROVAL)
    set_status(run, RunStatus.RUNNING)  # resumed after approval
    set_status(run, RunStatus.COMPLETED)
    assert run.status == "completed"


def test_illegal_transition_is_rejected() -> None:
    run = _run("completed")  # terminal
    with pytest.raises(InvalidTransition):
        set_status(run, RunStatus.RUNNING)


def test_same_status_is_idempotent() -> None:
    run = _run("running")
    set_status(run, RunStatus.RUNNING)  # no raise, no change
    assert run.status == "running"


def test_mark_failed_from_a_live_run() -> None:
    run = _run("running")
    mark_failed(run, "boom")
    assert run.status == "failed" and run.error == "boom"
    assert run.finished_at is not None


def test_mark_failed_does_not_override_a_terminal_run() -> None:
    run = _run("completed")
    mark_failed(run, "late error")
    assert run.status == "completed"  # a completed run is not flipped to failed
    assert run.error is None  # nor is a stale operational error attached


def test_is_terminal() -> None:
    assert is_terminal("completed") and is_terminal("failed")
    assert not is_terminal("running") and not is_terminal("pending")


def test_pause_resume_cancel_transitions() -> None:
    run = _run("running")
    set_status(run, RunStatus.PAUSED)
    assert run.status == "paused"
    set_status(run, RunStatus.RUNNING)  # resume
    assert run.status == "running"
    set_status(run, RunStatus.CANCELLED)
    assert run.status == "cancelled" and is_terminal("cancelled")


def test_cannot_resume_a_cancelled_run() -> None:
    run = _run("cancelled")  # terminal
    with pytest.raises(InvalidTransition):
        set_status(run, RunStatus.RUNNING)


def test_failed_run_can_be_explicitly_retried_from_pending() -> None:
    run = _run("failed")
    set_status(run, RunStatus.PENDING)
    assert run.status == "pending"
