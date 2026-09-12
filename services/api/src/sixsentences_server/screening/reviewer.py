"""Single-reviewer screening primitives (used by the ensemble).

Recall-first prompt contract: err toward INCLUDE when uncertain; a later
adjudication stage handles precision. A malformed model response degrades to
UNSURE, never to a silent exclusion.
"""

import json

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

SCREEN_SYSTEM = (
    "You are one of several distinct model reviewers screening titles/abstracts for a "
    "systematic review. Err on the side of INCLUDE when uncertain (recall matters most; "
    "a later adjudication stage handles precision). Respond with strict JSON: "
    '{"verdict": "include"|"exclude"|"unsure", "reason": "<one sentence>", '
    '"quote": "<a short span copied VERBATIM from the title/abstract that most supports '
    'your verdict; empty string if none applies — never paraphrase or invent>"}'
)
UNVALIDATED_REVIEWER_REASON = (
    "The reviewer response could not be validated; the record requires further review."
)


def _work_text(work: WorkRecord) -> str:
    return f"{work.title}. {work.abstract or ''}"


def screening_prompt(work: WorkRecord, protocol: ReviewProtocol) -> str:
    return (
        f"Research question: {protocol.question}\n"
        f"Inclusion criteria: {'; '.join(protocol.inclusion_criteria)}\n"
        f"Exclusion criteria: {'; '.join(protocol.exclusion_criteria)}\n\n"
        f"Title: {work.title}\n"
        f"Abstract: {work.abstract or '(no abstract)'}"
    )


def parse_verdict(text: str) -> tuple[Verdict, str, str]:
    """Parse a typed reviewer object; malformed responses never become evidence."""
    invalid_response = (
        Verdict.UNSURE,
        UNVALIDATED_REVIEWER_REASON,
        "",
    )
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except ValueError:
        return invalid_response
    if not isinstance(data, dict):
        return invalid_response
    verdict = data.get("verdict")
    reason = data.get("reason", "")
    quote = data.get("quote", "")
    if not isinstance(verdict, str) or not isinstance(reason, str) or not isinstance(quote, str):
        return invalid_response
    try:
        return Verdict(verdict), reason[:500], quote[:300]
    except ValueError:
        return invalid_response


def screen_stub(work: WorkRecord) -> ScreeningDecision:
    """Keyless placeholder: marks everything unsure, so no silent exclusions."""
    return ScreeningDecision(
        work_id=work.id,
        reviewer="stub",
        verdict=Verdict.UNSURE,
        reason="screening stub: no LLM provider configured",
    )


def screen_single(
    work: WorkRecord, protocol: ReviewProtocol, pool: LLMPool, ref: ModelRef
) -> ScreeningDecision:
    try:
        response = pool.complete(
            TaskType.SCREENING,
            ref=ref,
            system=SCREEN_SYSTEM,
            prompt=screening_prompt(work, protocol),
            max_tokens=300,
            json_response=True,
        )
    except ProviderError as exc:
        if exc.diagnostic_code != "invalid_structured_response":
            raise
        # The pool already recorded any incurred provider usage. Invalid JSON
        # is uncertainty about this record, not evidence supporting exclusion.
        return ScreeningDecision(
            work_id=work.id,
            reviewer=str(ref),
            verdict=Verdict.UNSURE,
            reason=UNVALIDATED_REVIEWER_REASON,
        )
    verdict, reason, raw_quote = parse_verdict(response.text)
    # only keep a quote that genuinely appears in the work (anti-hallucination)
    quote, _status = verify_quote(raw_quote, _work_text(work))
    return ScreeningDecision(
        work_id=work.id, reviewer=str(ref), verdict=verdict, reason=reason, quote=quote
    )
