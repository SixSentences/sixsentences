"""Canonical interpretation of screening decisions across the product.

Title/abstract, full-text and human decisions are append-only. This module
collapses that audit log into an explicit evidence state so the pipeline,
results API, exports and closing synthesis cannot disagree about a paper.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import ScreeningDecisionRow
from sixsentences_server.core.models import WorkRecord


class EvidenceState(StrEnum):
    CONFIRMED_INCLUDE = "confirmed_include"
    PROVISIONAL_INCLUDE = "provisional_include"
    UNSURE = "unsure"
    EXCLUDE = "exclude"
    UNSCREENED = "unscreened"


def decision_stage(reviewer: str) -> str:
    if reviewer.startswith("human:"):
        return "human"
    if reviewer.startswith("fulltext:"):
        return "full_text"
    return "title_abstract"


def final_decisions(
    session: Session, run_id: int, org_id: int | None = None
) -> dict[str, ScreeningDecisionRow]:
    """Return the effective decision for every work in a run.

    Human decisions have highest precedence. Otherwise a full-text decision
    supersedes title/abstract, and the latest decision within the same stage
    wins. This stays correct when a resumed worker appends a late title record.
    """

    statement = (
        select(ScreeningDecisionRow)
        .where(ScreeningDecisionRow.run_id == run_id)
        .order_by(ScreeningDecisionRow.id)
    )
    if org_id is not None:
        statement = statement.where(ScreeningDecisionRow.org_id == org_id)
    rows = session.scalars(statement).all()
    priority = {"title_abstract": 1, "full_text": 2, "human": 3}
    final: dict[str, ScreeningDecisionRow] = {}
    for decision in rows:
        previous = final.get(decision.work_id)
        if (
            previous is None
            or priority[decision_stage(decision.reviewer)]
            >= priority[decision_stage(previous.reviewer)]
        ):
            final[decision.work_id] = decision
    return final


def evidence_state(decision: ScreeningDecisionRow | None) -> EvidenceState:
    if decision is None:
        return EvidenceState.UNSCREENED
    if decision.verdict == "exclude":
        return EvidenceState.EXCLUDE
    if decision.verdict == "unsure":
        return EvidenceState.UNSURE
    if decision.verdict == "include":
        if decision_stage(decision.reviewer) in {"full_text", "human"}:
            return EvidenceState.CONFIRMED_INCLUDE
        return EvidenceState.PROVISIONAL_INCLUDE
    return EvidenceState.UNSCREENED


def evidence_counts(
    decisions: dict[str, ScreeningDecisionRow], *, identified_total: int
) -> dict[str, int]:
    counts = {state.value: 0 for state in EvidenceState}
    for decision in decisions.values():
        counts[evidence_state(decision).value] += 1
    counts[EvidenceState.UNSCREENED.value] += max(0, identified_total - len(decisions))
    counts["retained"] = (
        counts[EvidenceState.CONFIRMED_INCLUDE.value]
        + counts[EvidenceState.PROVISIONAL_INCLUDE.value]
        + counts[EvidenceState.UNSURE.value]
    )
    return counts


def select_final_work_ids(
    ranked_works: list[WorkRecord],
    decisions: dict[str, ScreeningDecisionRow],
    *,
    limit: int = 0,
) -> list[str]:
    """Build the retained output without confusing access with eligibility.

    Confirmed inclusions come first, followed by provisional inclusions,
    unresolved records, and records that have not been screened. The incoming
    relevance order is stable within every evidence tier.
    """

    tier = {
        EvidenceState.CONFIRMED_INCLUDE: 0,
        EvidenceState.PROVISIONAL_INCLUDE: 1,
        EvidenceState.UNSURE: 2,
        EvidenceState.UNSCREENED: 3,
    }
    retained = [
        work
        for work in ranked_works
        if evidence_state(decisions.get(work.id)) is not EvidenceState.EXCLUDE
    ]
    retained.sort(key=lambda work: tier.get(evidence_state(decisions.get(work.id)), 4))
    selected = retained[:limit] if limit > 0 else retained
    return [work.id for work in selected]
