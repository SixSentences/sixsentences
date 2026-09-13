"""Re-check a completed run against fresh integrity data (retraction delta)."""

from collections.abc import Iterable

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import Run, RunEvent
from sixsentences_server.reporting.exports import works_for_run


class RetractionDelta(BaseModel):
    checked: int  # included works re-checked
    retracted_now: list[str]  # included works currently retracted
    newly_retracted: list[str]  # retracted SINCE the review ran — the alert


def recheck_retractions(
    session: Session, run: Run, retracted_dois: Iterable[str]
) -> RetractionDelta:
    """Compare the run's included works against the current retraction set."""
    current = {d.lower() for d in retracted_dois}
    included = works_for_run(session, run.id, included_only=True, org_id=run.org_id)
    retracted_now = [w.id for w in included if w.is_retracted or (w.doi or "").lower() in current]
    # what the run flagged when it ran — anything retracted now but not then is new
    event = session.scalars(
        select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.event == "retraction_check_done")
    ).first()
    original = set(event.payload.get("flagged_ids", [])) if event else set()
    newly = [work_id for work_id in retracted_now if work_id not in original]
    return RetractionDelta(
        checked=len(included), retracted_now=retracted_now, newly_retracted=newly
    )
