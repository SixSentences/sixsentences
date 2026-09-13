"""Full-text screening: the second SLR pass.

Works included at title/abstract whose full text was retrieved and parsed are
assessed against the eligibility criteria on the FULL TEXT — the precision pass
title/abstract screening cannot do. A strong model reads a window of the full
text and returns a verdict, a one-sentence reason naming the criterion, and a
verbatim quote that is verified against the FULL TEXT (not just the abstract),
so every inclusion/exclusion is evidence-backed in the paper itself
(principle 2). Exclusions carry their reason — the PRISMA "reports excluded, with
reasons" requirement. This never runs without a strong model; a malformed
response degrades to UNSURE (human review), never a silent exclusion.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from sixsentences_server.core.models import ReviewProtocol, Verdict, WorkRecord
from sixsentences_server.llm.base import TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.screening.quote import verify_quote
from sixsentences_server.screening.reviewer import parse_verdict

# Full text is long; a bounded, section-aware window keeps cost sane without
# silently limiting the reviewer to the introduction.
DEFAULT_TEXT_BUDGET = 24_000  # characters (~6k tokens)
logger = logging.getLogger(__name__)
_SECTION_HEADING = re.compile(
    r"(?im)^[ \t]*(?:\d+(?:\.\d+)*[.)]?[ \t]+)?"
    r"(?P<section>materials?[ \t]+and[ \t]+methods?|methods?|methodology|"
    r"results?|findings?|discussion|conclusions?)[ \t]*$"
)

FULLTEXT_SYSTEM = (
    "You are the senior reviewer assessing a study's FULL TEXT for eligibility in a "
    "systematic review (the second screening pass, after title/abstract). Decide against the "
    "inclusion/exclusion criteria using the full text provided. Treat comparative elements in "
    "the research question as outcomes to extract unless the protocol explicitly says that a "
    "direct comparison is mandatory. Exclude only when a VERBATIM passage positively proves "
    "that the study fails an exclusion criterion. Absence of a comparison, outcome, population "
    "or method from the provided text is not proof that it is absent from the study. If the "
    "provided text is insufficient to decide, answer unclear for that criterion. Review EVERY "
    "numbered criterion independently. Report whether the criterion AS WRITTEN applies to the "
    "study: applies=yes means the written condition is present, applies=no means the written "
    "condition is demonstrably absent, and applies=unclear means the supplied text cannot "
    "establish either answer. Inclusion and exclusion criteria use this same literal meaning. "
    "The application "
    "will deterministically convert an applicable inclusion criterion into an eligibility pass and "
    "an applicable exclusion criterion into an eligibility failure. Do not return a field named "
    "status and do not reverse the meaning for exclusion criteria. A direct comparison is optional "
    "unless the protocol says comparison_required=true. "
    "Respond with strict JSON only: "
    '{"criteria":[{"index":0,"applies":"yes"|"no"|"unclear","quote":"verbatim supporting '
    'passage or empty string","reason":"short criterion-specific reason"}]}. '
    "Never infer a writing task, population, outcome or comparison from a broader learning task."
)


class CriterionAssessment(BaseModel):
    index: int
    criterion: str
    kind: Literal["inclusion", "exclusion"]
    status: Literal["yes", "no", "unclear"]
    reason: str
    quote: str | None = None
    quote_verified: bool = False


@dataclass
class FullTextAssessment:
    work_id: str
    verdict: Verdict
    reason: str
    quote: str | None
    reviewer: str
    quote_verified: bool
    criteria: list[CriterionAssessment]


class FullTextSummary(BaseModel):
    """Aggregate of the full-text stage — feeds PRISMA + methods."""

    assessed: int = 0
    included: int = 0  # studies included (passed full-text eligibility)
    excluded: int = 0
    unsure: int = 0
    quotes_verified: int = 0
    budget_paused: bool = False
    exclusions: list[dict[str, str]] = Field(default_factory=list)  # {id, reason}, capped


def fulltext_prompt(
    work: WorkRecord, protocol: ReviewProtocol, full_text: str, *, budget: int
) -> str:
    window = _review_window(full_text, budget)
    truncated = "\n[... full text truncated for length ...]" if len(full_text) > budget else ""
    criteria = [
        {"index": index, "kind": "inclusion", "criterion": criterion}
        for index, criterion in enumerate(protocol.inclusion_criteria)
    ]
    criteria.extend(
        {
            "index": len(criteria) + offset,
            "kind": "exclusion",
            "criterion": criterion,
        }
        for offset, criterion in enumerate(protocol.exclusion_criteria)
    )
    return (
        f"Research question: {protocol.question}\n"
        f"Comparison required for eligibility: {str(protocol.comparison_required).lower()}\n"
        f"Comparison interpretation: {protocol.comparison_note or 'synthesis subgroup'}\n"
        f"Numbered criteria: {json.dumps(criteria, ensure_ascii=False)}\n\n"
        f'Full text of "{work.title}":\n{window}{truncated}'
    )


def _strip_json_fence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    return raw


def _structured_assessment(
    work: WorkRecord,
    protocol: ReviewProtocol,
    full_text: str,
    response_text: str,
    reviewer: str,
) -> FullTextAssessment | None:
    try:
        payload = json.loads(_strip_json_fence(response_text))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    raw_criteria = payload.get("criteria")
    if not isinstance(raw_criteria, list):
        return None

    specs = [("inclusion", criterion) for criterion in protocol.inclusion_criteria] + [
        ("exclusion", criterion) for criterion in protocol.exclusion_criteria
    ]
    by_index: dict[int, dict[str, Any]] = {}
    for item in raw_criteria:
        if not isinstance(item, dict):
            continue
        raw_index = item.get("index")
        if isinstance(raw_index, int):
            by_index[raw_index] = item
        elif isinstance(raw_index, str) and raw_index.isdigit():
            by_index[int(raw_index)] = item
    criteria: list[CriterionAssessment] = []
    for index, (kind, criterion) in enumerate(specs):
        item = by_index.get(index, {})
        raw_applies = item.get("applies")
        if raw_applies is not None:
            applies = str(raw_applies).lower()
            if applies not in {"yes", "no", "unclear"}:
                applies = "unclear"
            if applies == "unclear":
                status = "unclear"
            elif kind == "inclusion":
                status = "yes" if applies == "yes" else "no"
            else:
                status = "no" if applies == "yes" else "yes"
        else:
            # Backwards compatibility for immutable historical responses and
            # local fixtures. In the legacy contract ``status`` expressed the
            # eligibility result rather than whether the written criterion
            # applied. New provider prompts never request this ambiguous form.
            status = str(item.get("status") or "unclear").lower()
            if status not in {"yes", "no", "unclear"}:
                status = "unclear"
        raw_quote = str(item.get("quote") or "")[:500]
        quote, _quote_status = verify_quote(raw_quote, full_text)
        # A positive inclusion or any failing criterion needs direct passage
        # support. Non-applicability of an exclusion cannot be proven by an
        # absence quote and is therefore allowed without one.
        requires_quote = status == "no" or (kind == "inclusion" and status == "yes")
        if requires_quote and quote is None:
            status = "unclear"
        criteria.append(
            CriterionAssessment(
                index=index,
                criterion=criterion,
                kind=kind,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                reason=str(item.get("reason") or "")[:500],
                quote=quote,
                quote_verified=quote is not None,
            )
        )

    if not criteria:
        return None
    failed = [criterion for criterion in criteria if criterion.status == "no"]
    unclear = [criterion for criterion in criteria if criterion.status == "unclear"]
    if failed:
        verdict = Verdict.EXCLUDE
        reason = f"Fails criterion: {failed[0].criterion}"
        representative = failed[0]
    elif unclear:
        verdict = Verdict.UNSURE
        reason = (
            f"Eligibility remains unclear for {len(unclear)} criterion"
            f"{'' if len(unclear) == 1 else 's'}: "
            + "; ".join(item.criterion for item in unclear[:3])
        )
        representative = next((criterion for criterion in criteria if criterion.quote), unclear[0])
    else:
        verdict = Verdict.INCLUDE
        reason = "All approved eligibility criteria are supported."
        representative = next((criterion for criterion in criteria if criterion.quote), criteria[0])
    return FullTextAssessment(
        work_id=work.id,
        verdict=verdict,
        reason=reason,
        quote=representative.quote,
        reviewer=reviewer,
        quote_verified=representative.quote_verified,
        criteria=criteria,
    )


def _legacy_assessment(
    work: WorkRecord,
    full_text: str,
    response_text: str,
    reviewer: str,
) -> FullTextAssessment:
    verdict, reason, raw_quote = parse_verdict(response_text)
    quote, _status = verify_quote(raw_quote, full_text)
    if verdict is not Verdict.UNSURE and quote is None:
        verdict = Verdict.UNSURE
        reason = (
            "The proposed eligibility decision was not supported by a verified "
            "full-text passage; human review is required."
        )
    return FullTextAssessment(
        work_id=work.id,
        verdict=verdict,
        reason=reason,
        quote=quote,
        reviewer=reviewer,
        quote_verified=quote is not None,
        criteria=[],
    )


def _review_window(full_text: str, budget: int) -> str:
    """Sample front matter and eligibility-bearing sections within one budget."""
    if budget <= 0:
        return ""
    if len(full_text) <= budget:
        return full_text

    anchors = [0]
    seen_sections: set[str] = set()
    for match in _SECTION_HEADING.finditer(full_text):
        heading = match.group("section").lower()
        if "method" in heading:
            section = "methods"
        elif heading.startswith(("result", "finding")):
            section = "results"
        elif heading.startswith("discussion"):
            section = "discussion"
        else:
            section = "conclusion"
        if section not in seen_sections:
            seen_sections.add(section)
            anchors.append(match.start())
    anchors.append(max(0, len(full_text) - max(1, budget // 5)))
    anchors = list(dict.fromkeys(anchors))

    separator = "\n\n[...]\n\n"
    usable = max(1, budget - len(separator) * (len(anchors) - 1))
    per_section = max(1, usable // len(anchors))
    chunks = [full_text[start : start + per_section].strip() for start in anchors]
    window = separator.join(chunk for chunk in chunks if chunk)
    return window[:budget]


def screen_full_text(
    work: WorkRecord,
    protocol: ReviewProtocol,
    full_text: str,
    pool: LLMPool,
    *,
    text_budget: int = DEFAULT_TEXT_BUDGET,
) -> FullTextAssessment:
    prompt = fulltext_prompt(work, protocol, full_text, budget=text_budget)
    assessments: list[FullTextAssessment] = []
    for ref in pool.precision_refs():
        if len(assessments) >= 2:
            break
        try:
            response = pool.complete(
                TaskType.FULL_TEXT_SCREENING,
                system=FULLTEXT_SYSTEM,
                prompt=prompt,
                max_tokens=400,
                json_response=True,
                ref=ref,
            )
            reviewer = f"fulltext:{response.provider}:{response.model}"
            assessment = _structured_assessment(
                work, protocol, full_text, response.text, reviewer
            ) or _legacy_assessment(work, full_text, response.text, reviewer)
            assessments.append(assessment)
        except ProviderError as exc:
            if exc.diagnostic_code == "invalid_structured_response":
                # Native structured-output validation may reject before the
                # parser. Keep that already-accounted call as an unsure vote.
                assessments.append(_legacy_assessment(work, full_text, "", f"fulltext:{ref}"))
                continue
            pool.mark_unavailable(ref)
            logger.warning(
                "full-text route failed on %s; trying fallback",
                work.id,
                exc_info=True,
            )
    if not assessments:
        return FullTextAssessment(
            work_id=work.id,
            verdict=Verdict.UNSURE,
            reason=("full-text model routes temporarily unavailable; human review required"),
            quote=None,
            reviewer="fulltext:fallback",
            quote_verified=False,
            criteria=[],
        )
    first = assessments[0]
    if len(assessments) == 1 and first.verdict is Verdict.EXCLUDE:
        return FullTextAssessment(
            work_id=work.id,
            verdict=Verdict.UNSURE,
            reason=(
                "Only one full-text model reviewer was available; an exclusion "
                "requires a second agreeing reviewer or human confirmation."
            ),
            quote=first.quote,
            reviewer="fulltext:single-reviewer",
            quote_verified=first.quote_verified,
            criteria=first.criteria,
        )
    if len(assessments) > 1 and assessments[1].verdict is not first.verdict:
        return FullTextAssessment(
            work_id=work.id,
            verdict=Verdict.UNSURE,
            reason="Distinct full-text model reviewers disagreed; human adjudication is required.",
            quote=first.quote or assessments[1].quote,
            reviewer="fulltext:ensemble",
            quote_verified=bool(first.quote or assessments[1].quote),
            criteria=first.criteria,
        )
    return first
