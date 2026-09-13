"""Composite, explainable research-integrity assessment per work.

Combines three offline signals into one report with a severity and a
human-readable reason (QUALITY.md layer 4). Nothing here auto-excludes — a
report flags work for the audit trail and can inform ranking; exclusion stays a
screening/human decision.

- **retracted** (critical): the work itself is retracted (corpus flag or the
  Retraction Watch DOI feed).
- **cites_retracted** (warning): a "zombie citation" — the work cites a
  retracted paper that is itself in the retrieved set. Retracted work keeps
  getting cited uncritically; surfacing this protects the evidence base.
- **tortured_phrases** (warning): paper-mill / synthetic-text signal.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.integrity.tortured import TorturedScreen
from sixsentences_server.integrity.venue import assess_venue


class Severity(StrEnum):
    CLEAN = "clean"
    WARNING = "warning"
    CRITICAL = "critical"


class IntegrityReport(BaseModel):
    work_id: str
    severity: Severity = Severity.CLEAN
    is_retracted: bool = False
    cites_retracted: list[str] = Field(default_factory=list)
    tortured_phrases: list[str] = Field(default_factory=list)
    venue_status: str = "unknown"  # indexed | predatory | preprint | unlisted | unknown
    reason: str = "no integrity signals"

    @property
    def flagged(self) -> bool:
        return self.severity is not Severity.CLEAN


def assess_corpus(
    works: list[WorkRecord],
    *,
    retracted_dois: frozenset[str] = frozenset(),
    indexed_venues: frozenset[str] = frozenset(),
    predatory_venues: frozenset[str] = frozenset(),
    screen: TorturedScreen | None = None,
) -> dict[str, IntegrityReport]:
    """Assess every work; the zombie-citation guard is corpus-relative.

    A work is flagged `cites_retracted` only when the retracted work it cites is
    also in `works` — an actionable, in-set finding rather than a guess about
    the whole citation universe (honest incompleteness).
    """
    screen = screen or TorturedScreen()

    retracted_ids = {
        w.id for w in works if w.is_retracted or (w.doi or "").lower() in retracted_dois
    }

    reports: dict[str, IntegrityReport] = {}
    for work in works:
        retracted = work.id in retracted_ids
        zombies = sorted(retracted_ids & set(work.referenced_works) - {work.id})
        tortured = screen.scan(work.title, work.abstract)
        venue = assess_venue(work.venue, indexed=indexed_venues, predatory=predatory_venues)

        if retracted:
            severity = Severity.CRITICAL
        elif zombies or tortured or venue.status == "predatory":
            severity = Severity.WARNING
        else:
            severity = Severity.CLEAN

        reasons: list[str] = []
        if retracted:
            reasons.append("work is retracted")
        if zombies:
            reasons.append(f"cites {len(zombies)} retracted work(s) in the set")
        if tortured:
            reasons.append(f"{len(tortured)} tortured phrase(s): {', '.join(tortured[:3])}")
        if venue.status == "predatory":
            reasons.append("venue on the configured predatory list")

        reports[work.id] = IntegrityReport(
            work_id=work.id,
            severity=severity,
            is_retracted=retracted,
            cites_retracted=zombies,
            tortured_phrases=tortured,
            venue_status=venue.status,
            reason="; ".join(reasons) if reasons else "no integrity signals",
        )
    return reports
