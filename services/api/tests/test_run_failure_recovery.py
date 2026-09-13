"""Late worker failures must serialize with user cancellation, not overwrite it."""

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import ORMExecuteState, Session

from sixsentences_server.api.app import _recover_failed_run
from sixsentences_server.config import Settings
from sixsentences_server.core.db import Org, Run, RunEvent, db_session, init_db
from sixsentences_server.jobs import DatabaseWorker


@pytest.mark.parametrize("worker", ["recovery", "guard"])
@pytest.mark.parametrize("status", ["running", "cancelled", "completed", "failed"])
def test_failure_finalizers_lock_current_state_before_deciding(
    settings: Settings,
    worker: str,
    status: str,
) -> None:
    del settings
    init_db()
    with db_session() as session:
        org = Org(name="Synthetic cancellation race")
        session.add(org)
        session.flush()
        run = Run(
            org_id=org.id,
            question="Synthetic run",
            status=status,
            error="Existing failure" if status == "failed" else None,
        )
        session.add(run)
        session.flush()
        run_id = run.id

    reads: list[str] = []

    def inspect_current_run_read(state: ORMExecuteState) -> None:
        if not state.is_select:
            return
        statement = str(state.statement.compile(dialect=postgresql.dialect()))
        if "FROM runs" in statement:
            reads.append(statement)
            assert statement.rstrip().endswith("FOR UPDATE")
            assert state.execution_options.get("populate_existing") is True

    event.listen(Session, "do_orm_execute", inspect_current_run_read)
    try:
        if worker == "recovery":
            with db_session() as session:
                recovered = _recover_failed_run(session, run_id, "Synthetic worker failure")
                assert recovered is not None
        else:
            DatabaseWorker._terminalize_failed_run(run_id)
    finally:
        event.remove(Session, "do_orm_execute", inspect_current_run_read)

    assert len(reads) == 1
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == ("failed" if status == "running" else status)
        if status in {"cancelled", "completed"}:
            assert run.error is None
            assert session.scalar(select(RunEvent.id).where(RunEvent.run_id == run_id)) is None
        elif status == "failed":
            assert run.error == "Existing failure"
        else:
            assert run.error
