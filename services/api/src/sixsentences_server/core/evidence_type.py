"""Deterministic evidence-type constraints shared by research chat surfaces."""

from __future__ import annotations

import re

from sixsentences_server.core.models import WorkRecord

_PRIMARY_RESEARCH_WISH = re.compile(
    r"\b(?:"
    r"original(?:e|en|er|es)?\s*(?:papers?|studien|arbeiten)|"
    r"primary\s+(?:research\s+)?(?:papers?|stud(?:y|ies))|"
    r"prim(?:ä|ae)r(?:e|en|er|es)?\s*(?:papers?|studien|arbeiten|quellen)|"
    r"prim(?:ä|ae)r(?:arbeiten|quellen)|"
    r"empirical\s+(?:papers?|stud(?:y|ies))|"
    r"empirisch(?:e|en|er|es)?\s*(?:papers?|studien|arbeiten)|"
    r"(?:original|urspr(?:ü|ue)nglich)(?:e|en|er|es)?\s*"
    r"(?:method(?:en)?(?:papers?|arbeiten)|methodological\s+papers?)"
    r")\b",
    re.IGNORECASE,
)
_SECONDARY_RESEARCH_EXCLUSION = re.compile(
    r"\b(?:no|not|without|exclude|excluding|keine|keinen|keiner|ohne|"
    r"nicht\s+die|ausschlie(?:ß|ss)lich\s+keine)\s+"
    r"(?:reviews?|surveys?|systematic\s+reviews?|systematisch(?:e|en|er|es)?\s+"
    r"(?:reviews?|übersichten?)|meta[- ]analys(?:is|es|e|en)|"
    r"scoping\s+reviews?)\b",
    re.IGNORECASE,
)
_SECONDARY_RESEARCH_WISH = re.compile(
    r"\b(?:reviews?|surveys?|overview|systematic\s+reviews?|"
    r"systematisch(?:e|en|er|es)?\s+(?:reviews?|übersichten?)|"
    r"meta[- ]analys(?:is|es|e|en)|scoping\s+reviews?)\b",
    re.IGNORECASE,
)
_SECONDARY_SOURCE_TITLE = re.compile(
    r"\b(?:a\s+survey(?:\s+of|\s+on)?|survey\s+of|"
    r"systematic\s+(?:literature\s+)?review|scoping\s+review|"
    r"literature\s+review|review\s+of|meta[- ]analys(?:is|es)|"
    r"an?\s+overview\s+of)\b",
    re.IGNORECASE,
)
_FOUNDATIONAL_METHOD_WISH = re.compile(
    r"\b(?:"
    r"foundational\s+(?:method\s+)?(?:papers?|works?|stud(?:y|ies))|"
    r"original\s+method(?:ological)?\s+(?:papers?|works?|stud(?:y|ies))|"
    r"original(?:e|en|er|es)?\s+(?:papers?|artikel|arbeit(?:en)?)|"
    r"urspr(?:ü|ue)nglich(?:e|en|er|es)?\s+(?:method(?:en)?(?:paper|arbeit(?:en)?|studie)|"
    r"arbeit(?:en)?|paper)|"
    r"(?:die\s+)?(?:erste|ersten)\s+(?:method(?:en)?(?:paper|arbeit)|paper|arbeit(?:en)?)"
    r")\b",
    re.IGNORECASE,
)
_FOLLOW_ON_OR_EVALUATION_TITLE = re.compile(
    r"\b(?:"
    r"benchmark(?:ing)?|evaluation|evaluating|assessment|comparison|comparative|"
    r"survey|review|overview|application|applying|case\s+study|"
    r"improv(?:e|ed|ement|ing)|enhanc(?:e|ed|ement|ing)|extension|extended|"
    r"fusion|adapt(?:ation|ing)?|domain[- ]specific|medicine|medical"
    r")\b",
    re.IGNORECASE,
)


def primary_research_requested(question: str) -> bool:
    """Return whether the user explicitly asks for primary research only."""

    # Beginners commonly express the evidence constraint negatively ("keine
    # Surveys") rather than knowing the term primary research. That wording
    # is still an explicit primary-source contract. Check it before the broad
    # secondary-source detector, which necessarily also sees the word survey.
    if _SECONDARY_RESEARCH_EXCLUSION.search(question):
        return True
    return bool(_PRIMARY_RESEARCH_WISH.search(question)) and not bool(
        _SECONDARY_RESEARCH_WISH.search(question)
    )


def foundational_method_requested(question: str) -> bool:
    """Return whether the user asks specifically for the originating methods.

    This is stricter than asking for primary research. A later benchmark or an
    extension can be primary research while still being the wrong answer to a
    novice asking for the original methods that introduced a research line.
    """

    return bool(_FOUNDATIONAL_METHOD_WISH.search(question))


def follow_on_or_evaluation_like(work: WorkRecord) -> bool:
    """Conservatively flag titles that clearly describe later derivative work."""

    return secondary_source_like(work) or bool(_FOLLOW_ON_OR_EVALUATION_TITLE.search(work.title))


def secondary_source_like(work: WorkRecord) -> bool:
    """Conservatively identify clear reviews and surveys.

    The title rule deliberately avoids broad words such as ``systematic`` on
    their own because a systematic evaluation can still be primary research.
    """

    work_type = (work.work_type or "").strip().lower()
    if work_type in {"review", "meta-analysis", "systematic-review"}:
        return True
    return bool(_SECONDARY_SOURCE_TITLE.search(work.title))


def filter_primary_research(
    question: str,
    works: list[WorkRecord],
) -> tuple[list[WorkRecord], list[WorkRecord]]:
    """Apply an explicit primary-research constraint without hiding gaps.

    If no primary candidate remains, keep the original evidence set. The
    answer can then explain that the available material does not satisfy the
    requested evidence type instead of pretending that no evidence exists.
    """

    if not primary_research_requested(question):
        return works, []
    foundational_only = foundational_method_requested(question)
    primary = [
        work
        for work in works
        if not secondary_source_like(work)
        and (not foundational_only or not follow_on_or_evaluation_like(work))
    ]
    secondary = [work for work in works if work not in primary]
    return (primary, secondary) if primary else (works, [])
