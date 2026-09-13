"""Query expansion loop: the concrete mechanics of exhaustiveness.

A single boolean query never captures a field's vocabulary. The expansion
loop asks a strong model for alternative formulations (synonyms, spelling
variants, abbreviations, adjacent terminology), validates each against the
query parser, executes every variant against the corpus, and keeps iterating
while new variants still surface unseen works. It stops on SATURATION
(novelty below threshold), not on a timer — runs are expected to take hours
on large corpora, and that is by design (docs/VISION.md, principle 8).

Every executed variant becomes its own PRISMA-S SearchExecution entry.
"""

import json

from sixsentences_server.core.models import ReviewProtocol
from sixsentences_server.llm.base import LLMConfigError, TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.querylang.parser import QueryParseError, parse_query

EXPAND_SYSTEM = (
    "You are a search strategist for systematic literature reviews. Given a research "
    "question and existing boolean queries, propose ADDITIONAL boolean queries that "
    "widen recall: synonyms, abbreviations, US/UK spelling variants, adjacent "
    "terminology, and alternative phrasings of the same concepts. Use AND/OR/NOT, "
    "parentheses and quoted phrases; group synonyms with OR. Keep every query COMPACT "
    "and precise: at most 3 AND groups, at most 5 OR terms per group, quoted phrases "
    "for multiword terms, and never a group of generic verbs or study-type words "
    "(evaluation, analysis, approach) — each variant should probe a DIFFERENT "
    "vocabulary, not restate the same one longer. At least one query MUST be BROAD — "
    "AND together only the 2 most central concepts so it still matches when narrower "
    "queries return nothing. Respond with strict JSON: "
    '{"queries": ["...", "..."]} — 3 to 5 queries, no prose.'
)

MAX_ROUNDS = 3
NOVELTY_THRESHOLD = 0.05  # stop when a round adds <5% new unique works
VARIANTS_PER_ROUND = 5


def propose_variants(
    protocol: ReviewProtocol,
    existing: list[str],
    sample_titles: list[str],
    pool: LLMPool,
) -> list[str]:
    prompt = (
        f"Research question: {protocol.question}\n"
        f"Inclusion criteria: {'; '.join(protocol.inclusion_criteria)}\n"
        f"Existing queries:\n" + "\n".join(f"- {q}" for q in existing)
    )
    if sample_titles:
        prompt += "\n\nTitles already retrieved (for vocabulary hints):\n" + "\n".join(
            f"- {t}" for t in sample_titles[:15]
        )
    response = None
    for ref in pool.synthesis_refs():
        try:
            response = pool.complete(
                TaskType.QUERY_EXPANSION,
                system=EXPAND_SYSTEM,
                prompt=prompt,
                max_tokens=1200,
                ref=ref,
            )
            break
        except (LLMConfigError, ProviderError):
            pool.mark_unavailable(ref)
    if response is None:
        return []
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
        candidates = [str(q) for q in data.get("queries", [])]
    except (ValueError, KeyError):
        return []

    validated: list[str] = []
    for candidate in candidates[:VARIANTS_PER_ROUND]:
        try:
            parse_query(candidate)
        except QueryParseError:
            continue
        if candidate not in existing and candidate not in validated:
            validated.append(candidate)
    return validated
