"""Multi-signal ranking with a decomposed, never-black-box score.

Product principle: a score is always broken into named signals with weights,
so a caller can see why a work ranks where it does; ranking is never an opaque
number.

The signals here are deliberately offline and deterministic so the whole
pipeline stays corpus-first and testable without a network or a model:

- **relevance** — lexical coverage of the protocol vocabulary (question +
  inclusion criteria + query terms) by the work's title/abstract, title
  weighted higher. This is an honest offline proxy, not semantic similarity.
- **impact** — citation count normalized by age (citations per year, log-
  damped) then scaled across the candidate set, so a 2015 classic and a 2024
  breakout are compared fairly instead of by raw totals.
- **recency** — mild, bounded preference for newer work within the set.

Retracted works are penalized multiplicatively (kept in place, pushed down,
and flagged) rather than silently dropped — honest incompleteness.
"""

import math
import re
from datetime import UTC, datetime

from pydantic import BaseModel

from sixsentences.core.models import ReviewProtocol, WorkRecord
from sixsentences.querylang.ast import And, Node, Not, Or, Term
from sixsentences.querylang.parser import parse_query

_WORD = re.compile(r"[a-z0-9]+")
# Small English stoplist — enough to keep query/question vocabulary meaningful
# without pulling in a dependency. Domain terms are never in here.
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "we",
        "our",
        "using",
        "use",
        "used",
        "based",
        "via",
        "toward",
        "towards",
        "how",
        "do",
        "does",
        "what",
        "which",
        "when",
        "whom",
        "your",
        "you",
        "they",
        "them",
        "than",
        "then",
        "also",
        "can",
        "could",
        "may",
        "might",
    ]
)

# Relevance-first: this is a literature SEARCH, so topical match dominates;
# impact/recency are boosters, not primary. All three signals are normalized
# across the candidate set (below) so they are comparable.
DEFAULT_WEIGHTS: dict[str, float] = {"relevance": 0.6, "impact": 0.25, "recency": 0.15}
RETRACTION_PENALTY = 0.5  # multiplicative: a retracted work keeps 50% of its score


class RankingSignals(BaseModel):
    """Normalized components of one ranking score."""

    relevance: float
    impact: float
    recency: float


class RankedWork(BaseModel):
    """A work plus the decomposed reasoning behind its rank."""

    work: WorkRecord
    score: float
    signals: RankingSignals
    retracted: bool
    explanation: str


def _terms(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if len(t) > 2 and t not in _STOP}


def _query_vocabulary(protocol: ReviewProtocol) -> set[str]:
    """Clean natural-language vocabulary from the protocol for relevance.

    Only positive query branches contribute; terms beneath ``NOT`` are
    excluded rather than accidentally rewarded by relevance scoring.
    """
    vocabulary = _terms(" ".join([protocol.question, *protocol.inclusion_criteria]))
    query = parse_query(protocol.query_string)
    return vocabulary | _positive_ast_vocabulary(query)


def _positive_ast_vocabulary(node: Node, *, negated: bool = False) -> set[str]:
    match node:
        case Term(text=text):
            return set() if negated else _terms(text)
        case And(children=children) | Or(children=children):
            return set().union(
                *(_positive_ast_vocabulary(child, negated=negated) for child in children)
            )
        case Not(child=child):
            return _positive_ast_vocabulary(child, negated=not negated)
    raise TypeError(f"unknown node: {node!r}")


def _relevance(work: WorkRecord, vocabulary: set[str]) -> float:
    if not vocabulary:
        return 0.0
    title_hits = len(vocabulary & _terms(work.title))
    abstract_hits = len(vocabulary & _terms(work.abstract or ""))
    # Coverage of the protocol vocabulary, title weighted 2x abstract.
    covered = (2 * title_hits + abstract_hits) / (2 * len(vocabulary))
    return min(1.0, covered)


def protocol_relevance(work: WorkRecord, protocol: ReviewProtocol) -> float:
    """Return raw protocol-vocabulary coverage for retrieval diagnostics.

    Unlike the normalized ranking signal, this value is comparable across
    provider pages and can therefore identify when later live-search pages no
    longer contain meaningful topical matches.
    """
    return _relevance(work, _query_vocabulary(protocol))


def _raw_impact(work: WorkRecord, now_year: int) -> float:
    age = max(1, now_year - (work.year or now_year) + 1)
    return math.log1p(work.cited_by_count / age)


def _recency(work: WorkRecord, min_year: int, max_year: int) -> float:
    if work.year is None or max_year == min_year:
        return 0.5
    span = (work.year - min_year) / (max_year - min_year)
    return max(0.0, min(1.0, span))


def rank_works(
    works: list[WorkRecord],
    protocol: ReviewProtocol,
    *,
    retracted_ids: frozenset[str] = frozenset(),
    now_year: int | None = None,
    weights: dict[str, float] | None = None,
) -> list[RankedWork]:
    """Rank works by a weighted sum of normalized, named signals.

    Deterministic: identical inputs give identical order (ties broken by
    citation count then id). `now_year` is injectable so tests are stable.
    """
    work_ids = [work.id for work in works]
    if len(work_ids) != len(set(work_ids)):
        raise ValueError("work ids must be unique before ranking")
    weights = weights or DEFAULT_WEIGHTS
    now_year = now_year or datetime.now(UTC).year
    vocabulary = _query_vocabulary(protocol)

    # Normalize each signal across the candidate set so relevance is
    # discriminative (raw coverage fractions are compressed by a large
    # vocabulary, which otherwise lets impact/recency swamp topical match).
    raw_relevance = {w.id: _relevance(w, vocabulary) for w in works}
    max_relevance = max(raw_relevance.values(), default=0.0) or 1.0
    raw_impacts = {w.id: _raw_impact(w, now_year) for w in works}
    max_impact = max(raw_impacts.values(), default=0.0) or 1.0
    years = [w.year for w in works if w.year is not None]
    min_year, max_year = (min(years), max(years)) if years else (now_year, now_year)

    ranked: list[RankedWork] = []
    for work in works:
        signals = RankingSignals(
            relevance=round(raw_relevance[work.id] / max_relevance, 4),
            impact=round(raw_impacts[work.id] / max_impact, 4),
            recency=round(_recency(work, min_year, max_year), 4),
        )
        base = (
            weights["relevance"] * signals.relevance
            + weights["impact"] * signals.impact
            + weights["recency"] * signals.recency
        )
        retracted = work.id in retracted_ids or work.is_retracted
        score = base * RETRACTION_PENALTY if retracted else base
        explanation = (
            f"relevance {signals.relevance:.2f}*{weights['relevance']}, "
            f"impact {signals.impact:.2f}*{weights['impact']}, "
            f"recency {signals.recency:.2f}*{weights['recency']}"
            + (f", retracted penalty *{RETRACTION_PENALTY}" if retracted else "")
            + f" = {score:.3f}"
        )
        ranked.append(
            RankedWork(
                work=work,
                score=round(score, 6),
                signals=signals,
                retracted=retracted,
                explanation=explanation,
            )
        )

    ranked.sort(key=lambda r: (r.score, r.work.cited_by_count, r.work.id), reverse=True)
    return ranked
