"""Durable cooperative run control shared by API and worker processes.

Commands live in the existing tenant-scoped, append-only run-event ledger.
The API commits them with its state/queue changes before acknowledging the
request. Checkpoints use a fresh transaction: neither an ORM identity map nor
a process-local registry may hide a command issued by another API replica.
Pause remains cooperative; cancellation also fences the durable job lease.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import Run, RunEvent, db_session

PAUSE = "pause"
CANCEL = "cancel"
_REQUESTED = "run_control_requested"
_CLEARED = "run_control_cleared"


def lock_run(session: Session, run_id: int, *, org_id: int) -> Run | None:
    """Serialize control changes on the authorized run, refreshing stale state."""

    return session.scalar(
        select(Run)
        .where(Run.id == run_id, Run.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def latest(session: Session, run_id: int, *, org_id: int) -> RunEvent | None:
    """Read the last explicit command, not unrelated progress events."""

    return session.scalar(
        select(RunEvent)
        .where(
            RunEvent.run_id == run_id,
            RunEvent.org_id == org_id,
            RunEvent.stage == "control",
            RunEvent.event.in_((_REQUESTED, _CLEARED)),
        )
        .order_by(RunEvent.id.desc())
        .limit(1)
    )


def request(run_id: int, signal: str, *, org_id: int, session: Session) -> int:
    """Append a command inside the caller's transaction; cancel always wins."""

    if signal not in {PAUSE, CANCEL}:
        raise ValueError("unsupported run control signal")
    run = lock_run(session, run_id, org_id=org_id)
    if run is None:
        raise LookupError("run not found")
    previous = latest(session, run_id, org_id=org_id)
    if previous is not None and previous.payload.get("signal") == CANCEL:
        return previous.id
    if run.status == "cancelled":
        signal = CANCEL
    event = RunEvent(
        org_id=org_id,
        run_id=run_id,
        stage="control",
        event=_REQUESTED,
        payload={"signal": signal},
    )
    session.add(event)
    session.flush()
    return event.id


def poll(run_id: int, *, org_id: int) -> str:
    """Observe committed control from any process; deleted/cancelled runs stop."""

    with db_session() as session:
        status = session.scalar(select(Run.status).where(Run.id == run_id, Run.org_id == org_id))
        if status is None or status == "cancelled":
            return CANCEL
        event = latest(session, run_id, org_id=org_id)
        if event is None or event.event == _CLEARED:
            return ""
        signal = event.payload.get("signal")
        return signal if isinstance(signal, str) and signal in {PAUSE, CANCEL} else CANCEL


def clear(run_id: int, *, org_id: int, session: Session) -> int | None:
    """Record an explicit resume/retry without erasing the control audit trail.

    The returned event ID binds a queued resume to this exact request. A worker
    must never clear signals when it finishes: that could erase a newer pause.
    Cancelled runs and cancellation commands cannot be reopened here.
    """

    run = lock_run(session, run_id, org_id=org_id)
    if run is None:
        raise LookupError("run not found")
    previous = latest(session, run_id, org_id=org_id)
    if run.status == "cancelled" or (
        previous is not None and previous.payload.get("signal") == CANCEL
    ):
        return None
    event = RunEvent(
        org_id=org_id,
        run_id=run_id,
        stage="control",
        event=_CLEARED,
        payload={},
    )
    session.add(event)
    session.flush()
    return event.id
