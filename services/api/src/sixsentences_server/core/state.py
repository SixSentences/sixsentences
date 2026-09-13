"""Run state machine: explicit, validated status transitions.

Runs are long, resumable jobs (hours, with a human gate in the middle), so the
lifecycle is formalised: allowed transitions are declared once and every status
change goes through `set_status`, which refuses an illegal move (re-completing a
terminal run, resuming a completed one, ...). The executor is orthogonal —
today FastAPI BackgroundTasks, an arq worker on a real deployment in H1 — but
the state machine is identical either way, which is what makes swapping the
executor safe.
"""

from datetime import UTC, datetime

from sixsentences_server.core.db import Run
from sixsentences_server.core.models import RunStatus

_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.AWAITING_PROTOCOL_APPROVAL,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.PAUSED,
        RunStatus.CANCELLED,
    },
    RunStatus.AWAITING_PROTOCOL_APPROVAL: {
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.PAUSED: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.COMPLETED: set(),  # terminal
    # Failed remains terminal for polling and concurrency accounting, but an
    # explicit user retry may reopen it. The API only exposes this transition
    # for ask-mode runs, never for long systematic reviews.
    RunStatus.FAILED: {RunStatus.PENDING},
    RunStatus.CANCELLED: set(),  # terminal
}
_TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class InvalidTransition(RuntimeError):
    pass


def _as_status(value: str) -> RunStatus | None:
    try:
        return RunStatus(value)
    except ValueError:
        return None


def is_terminal(status: str) -> bool:
    return _as_status(status) in _TERMINAL


def set_status(run: Run, to: RunStatus) -> None:
    current = _as_status(run.status)
    if current == to:
        return  # idempotent
    if current is None or to not in _TRANSITIONS[current]:
        raise InvalidTransition(f"run {run.id}: {run.status!r} -> {to.value!r} not allowed")
    run.status = to.value


def mark_failed(run: Run, message: str) -> None:
    """Move a live run to FAILED without corrupting a terminal result.

    A stale retry can finish after the original worker has completed the run.
    In that case both the terminal status and the public error field are
    immutable: an operational retry failure must not produce the contradictory
    state ``completed`` plus ``error``.
    """
    if is_terminal(run.status):
        return
    set_status(run, RunStatus.FAILED)
    run.error = message
    run.finished_at = datetime.now(UTC)
