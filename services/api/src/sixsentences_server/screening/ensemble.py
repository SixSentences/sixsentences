"""Distinct-model ensemble screening with adjudication.

Decision rules (see docs/QUALITY.md layer 3; grounded in JAMIA 2025):
- the ensemble is the RECALL stage: any INCLUDE vote keeps a work alive
- include/exclude conflicts and unsure-majorities go to a strong ADJUDICATOR
- unanimous EXCLUDE requires >= 2 distinct model reviewers; a single model may
  never exclude on its own (single-vote excludes degrade to UNSURE)
- reviewer disagreement is measured and logged (live quality signal)
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import combinations

from sixsentences_server.core.models import (
    ReviewProtocol,
    ScreeningDecision,
    Verdict,
    WorkRecord,
)
from sixsentences_server.llm.base import ModelRef, TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.screening.quote import verify_quote
from sixsentences_server.screening.reviewer import (
    UNVALIDATED_REVIEWER_REASON,
    parse_verdict,
    screen_single,
    screening_prompt,
)

logger = logging.getLogger(__name__)

ADJUDICATE_SYSTEM = (
    "You are the senior adjudicator in a systematic review. Distinct model reviewers "
    "disagreed about this record. Decide conservatively: exclude only when the record "
    "clearly fails the criteria; when in genuine doubt, keep it for full-text review "
    '(include). Respond with strict JSON: {"verdict": "include"|"exclude"|"unsure", '
    '"reason": "<one sentence weighing the reviewer votes>", '
    '"quote": "<a verbatim span from the title/abstract supporting the decision, or empty>"}'
)


@dataclass
class EnsembleOutcome:
    work_id: str
    votes: list[ScreeningDecision]
    final: ScreeningDecision
    adjudicated: bool


def _finalize(
    work_id: str,
    votes: list[ScreeningDecision],
    final: ScreeningDecision,
    *,
    adjudicated: bool,
) -> EnsembleOutcome:
    """Apply the evidence guard and attach calibrated reviewer agreement."""
    if final.verdict is Verdict.EXCLUDE and final.quote is None:
        final.verdict = Verdict.UNSURE
        final.reason = (
            "The proposed exclusion was not supported by a verified title or abstract "
            "passage; the record advances to full-text or human review."
        )
    if votes:
        agree = sum(1 for v in votes if v.verdict is final.verdict)
        final.confidence = round(agree / len(votes), 4)
    return EnsembleOutcome(work_id, votes, final, adjudicated)


def include_vote_counts(outcomes: list[EnsembleOutcome]) -> dict[str, int]:
    """Per work, how many reviewers voted INCLUDE — the capture frequencies for
    the screening-recall estimator (works with zero include votes are the
    'unseen' includes Chao2 estimates)."""
    counts: dict[str, int] = {}
    for outcome in outcomes:
        n = sum(1 for v in outcome.votes if v.verdict is Verdict.INCLUDE)
        if n:
            counts[outcome.work_id] = n
    return counts


def _pairwise_agreement(outcomes: list[EnsembleOutcome]) -> float | None:
    """Share of reviewer pairs (per work) that agree — a cheap live κ proxy."""
    pairs = agree = 0
    for outcome in outcomes:
        for a, b in combinations(outcome.votes, 2):
            pairs += 1
            agree += a.verdict is b.verdict
    return agree / pairs if pairs else None


def screen_ensemble(
    work: WorkRecord,
    protocol: ReviewProtocol,
    pool: LLMPool,
    refs: list[ModelRef],
) -> EnsembleOutcome:
    # Repeated references do not create distinct model reviewers. Preserve the
    # configured order while allowing only one vote per provider/model pair.
    refs = list(dict.fromkeys(refs))

    # One flaky vendor must degrade the ensemble to fewer reviewers, never
    # kill the whole run: a failed reviewer simply casts no vote.
    def vote(ref: ModelRef) -> ScreeningDecision | None:
        try:
            return screen_single(work, protocol, pool, ref)
        except ProviderError:
            pool.mark_unavailable(ref)
            logger.warning(
                "screening reviewer failed on %s; vote skipped",
                work.id,
                exc_info=True,
            )
            return None

    with ThreadPoolExecutor(max_workers=max(len(refs), 1)) as executor:
        votes = [v for v in executor.map(vote, refs) if v is not None]
    if not votes:
        raise ProviderError(f"all screening reviewers failed for work {work.id}")

    includes = [v for v in votes if v.verdict is Verdict.INCLUDE]
    excludes = [v for v in votes if v.verdict is Verdict.EXCLUDE]

    if includes and excludes:
        final = _adjudicate(work, protocol, pool, votes)
        return _finalize(work.id, votes, final, adjudicated=True)

    if includes:  # OR rule: any include keeps the work (recall stage)
        final = ScreeningDecision(
            work_id=work.id,
            reviewer="ensemble-or",
            verdict=Verdict.INCLUDE,
            reason=f"{len(includes)}/{len(votes)} reviewers voted include: " + includes[0].reason,
            quote=next((v.quote for v in includes if v.quote), None),
        )
        return _finalize(work.id, votes, final, adjudicated=False)

    if excludes and len(votes) >= 2 and len(excludes) == len(votes):
        final = ScreeningDecision(
            work_id=work.id,
            reviewer="ensemble-unanimous",
            verdict=Verdict.EXCLUDE,
            reason=f"unanimous exclude by {len(votes)} distinct model reviewers: "
            + excludes[0].reason,
            quote=next((v.quote for v in excludes if v.quote), None),
        )
        return _finalize(work.id, votes, final, adjudicated=False)

    if excludes and len(votes) == 1:
        final = ScreeningDecision(
            work_id=work.id,
            reviewer="ensemble-guard",
            verdict=Verdict.UNSURE,
            reason="single-model exclude downgraded to unsure (policy: no exclusion "
            "on one model's vote): " + excludes[0].reason,
            quote=excludes[0].quote,
        )
        return _finalize(work.id, votes, final, adjudicated=False)

    # mixed unsure/exclude or all unsure -> adjudicate if possible, else unsure
    if excludes and pool.has_strong():
        final = _adjudicate(work, protocol, pool, votes)
        return _finalize(work.id, votes, final, adjudicated=True)

    final = ScreeningDecision(
        work_id=work.id,
        reviewer="ensemble",
        verdict=Verdict.UNSURE,
        reason="no reviewer committed to a verdict",
    )
    return _finalize(work.id, votes, final, adjudicated=False)


def _adjudicate(
    work: WorkRecord,
    protocol: ReviewProtocol,
    pool: LLMPool,
    votes: list[ScreeningDecision],
) -> ScreeningDecision:
    vote_lines = "\n".join(f"- {v.reviewer}: {v.verdict.value} — {v.reason}" for v in votes)
    prompt = screening_prompt(work, protocol) + (
        f"\n\nDistinct model reviewer votes:\n{vote_lines}"
    )
    response = None
    for ref in pool.precision_refs():
        try:
            response = pool.complete(
                TaskType.ADJUDICATION,
                system=ADJUDICATE_SYSTEM,
                prompt=prompt,
                max_tokens=300,
                json_response=True,
                ref=ref,
            )
            break
        except ProviderError as exc:
            if exc.diagnostic_code == "invalid_structured_response":
                return ScreeningDecision(
                    work_id=work.id,
                    reviewer=f"adjudicator:{ref}",
                    verdict=Verdict.UNSURE,
                    reason=UNVALIDATED_REVIEWER_REASON,
                )
            logger.warning(
                "adjudication route failed on %s; trying fallback",
                work.id,
                exc_info=True,
            )
    if response is None:
        # Recall is the governing objective of title/abstract screening. If
        # every precision route is unavailable, retain the record for the
        # full-text/human pass instead of failing the run or silently excluding
        # potentially relevant evidence.
        return ScreeningDecision(
            work_id=work.id,
            reviewer="adjudicator:fallback",
            verdict=Verdict.INCLUDE,
            reason=(
                "adjudication temporarily unavailable; conservatively retained "
                "for full-text or human review"
            ),
            quote=next((vote.quote for vote in votes if vote.quote), None),
        )
    verdict, reason, raw_quote = parse_verdict(response.text)
    quote, _ = verify_quote(raw_quote, f"{work.title}. {work.abstract or ''}")
    return ScreeningDecision(
        work_id=work.id,
        reviewer=f"adjudicator:{response.provider}:{response.model}",
        verdict=verdict,
        reason=reason,
        quote=quote,
    )


def ensemble_agreement(outcomes: list[EnsembleOutcome]) -> float | None:
    return _pairwise_agreement(outcomes)
