"""Protocol synthesis: research question -> formal review protocol.

Two strategies:
- LLM (strong routed model) when a pool is configured: proposes criteria and
  a boolean query, validated against the parser before acceptance.
- Heuristic fallback so the pipeline runs keyless (dev, tests, CI).

The pipeline stops at the Protocol Gate after this stage in interactive use;
the skeleton auto-approves and records that in the audit log.
"""

import json
import re
from typing import Any

from sixsentences_server.core.models import ReviewProtocol
from sixsentences_server.llm.base import BudgetExceededError, LLMConfigError, TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.querylang.parser import QueryParseError, parse_query

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "between",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "their",
    "there",
    "to",
    "und",
    "was",
    "we",
    "what",
    "when",
    "which",
    "with",
    "der",
    "die",
    "das",
    "effect",
    "effects",
    "impact",
    "role",
    "study",
    "studies",
    "research",
    "based",
    "using",
    "use",
    "used",
    "new",
    "into",
    "onto",
    "via",
    "through",
    "towards",
    "toward",
    "about",
    # command verbs and german question filler — the asking, not the topic
    "suche",
    "such",
    "suchen",
    "erstelle",
    "erstell",
    "mach",
    "mache",
    "zeige",
    "zeig",
    "gib",
    "vergleich",
    "vergleiche",
    "vergleichen",
    "bitte",
    "mir",
    "mal",
    "dann",
    "dazu",
    "grafik",
    "diagramm",
    "aktuell",
    "aktuelle",
    "aktuellen",
    "entwicklung",
    "entwicklungen",
    "bereich",
    # meta-words of trend questions — they describe the asking, not the topic
    "currently",
    "current",
    "happening",
    "state",
    "development",
    "developments",
    "trend",
    "trends",
    "recent",
    "recently",
    "latest",
    "overview",
    "field",
    "area",
}

_SYNTH_SYSTEM = (
    "You are a systematic-review methodologist. Given a research question, produce a "
    "review protocol as strict JSON with keys: inclusion_criteria (list of strings), "
    "exclusion_criteria (list of strings), eligibility_dimensions (short labels such as "
    "population, intervention, task, outcome, empirical_design), comparison_required "
    "(boolean), comparison_note (one sentence explaining whether comparison is an "
    "eligibility gate or a synthesis subgroup), query_string (a professional boolean search "
    "string a researcher could paste verbatim into Scopus, Web of Science or IEEE "
    "Xplore — it will be published in the review's search appendix). "
    "Eligibility rules: translate the population, setting, intervention/exposure and reported "
    "outcomes into independent criteria. A question asking how groups, settings or approaches "
    "differ does NOT automatically require every included study to contain a direct head-to-head "
    "comparison; individual-arm evidence may still answer the synthesis. Make a direct comparison "
    "mandatory only when the question explicitly restricts eligibility to comparative studies. "
    "Criteria must describe observable study properties and must never infer absence from an "
    "abstract that simply does not mention a detail. "
    "Query rules — precision AND portability both matter: "
    "(1) Exactly 2 or 3 AND-joined concept blocks, each wrapped in parentheses, never "
    "more. Every block must name a real TOPIC of the question; never build a block out "
    "of generic verbs or study-type words (generation, evaluation, analysis, approach, "
    "method, using) — those match everything and only add noise. Fold quality/outcome "
    "aspects into the criteria lists instead of the query. "
    "(2) Inside a block, OR together 3 to 7 strong names for the concept: the "
    "canonical term, its standard abbreviation, established synonyms, and the defining "
    "tools or standards of the field. Cover the formal literature term AND the "
    "practitioner term when they differ. Skip morphological variants (search engines "
    "stem: generate/generation are the same term) and skip marginal synonyms. "
    "(3) Use double-quoted phrases for every multiword term. Plain AND/OR and "
    "parentheses only: no NOT, no wildcards, no field tags — the string must run "
    "unchanged on every major database. "
    "(4) Keep the whole query under about 320 characters. "
    "Respond with JSON only, no prose."
)

# structural budget the LLM draft must meet; a bloated query hurts precision
_MAX_AND_GROUPS = 3
_MAX_QUERY_CHARS = 420


def _heuristic_query(question: str) -> str:
    """AND the question's most distinctive content terms.

    Acronyms (IaC, LLMs, GPT) outrank everything: they name the topic. Related
    word forms are deduplicated by stem (generation/generated), and the query
    stays at three AND terms — the recall-safe end of specificity. Sorting by
    raw length alone previously produced verb salads like
    "generation AND using AND evaluation AND generated".
    """
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", question)
    candidates: list[tuple[str, bool]] = []  # (lowercase term, is_acronym)
    seen_stems: set[str] = set()
    for token in raw_tokens:
        lower = token.lower()
        if lower in _STOPWORDS:
            continue
        stem = lower[:6]
        if stem in seen_stems:
            continue
        seen_stems.add(stem)
        # mixed/upper case beyond the first letter marks an acronym, not
        # sentence- or title-case (IaC, LLMs, RAG — but not "Code")
        candidates.append((lower, any(ch.isupper() for ch in token[1:])))
    if not candidates:
        return question.split()[0].lower() if question.split() else "research"
    order = [term for term, _ in candidates]
    top = sorted(candidates, key=lambda item: (not item[1], -len(item[0])))[:3]
    return " AND ".join(sorted((term for term, _ in top), key=order.index))


def synthesize_protocol(question: str, pool: LLMPool | None = None) -> ReviewProtocol:
    if pool is not None and pool.has_strong():
        try:
            return _synthesize_llm(question, pool)
        except (
            ValueError,
            KeyError,
            QueryParseError,
            LLMConfigError,
            BudgetExceededError,
            ProviderError,
        ):
            # fall back to the (LLM-free) heuristic; a budget too small to even
            # synthesise pauses honestly into heuristic mode rather than failing
            pass
    return ReviewProtocol(
        question=question,
        inclusion_criteria=["addresses the research question directly"],
        exclusion_criteria=["not a primary study or survey relevant to the question"],
        query_string=_heuristic_query(question),
        comparison_required=False,
        comparison_note=(
            "Comparative evidence is analysed as a subgroup unless the approved "
            "protocol explicitly requires a direct comparison."
        ),
        eligibility_dimensions=["topic", "population", "outcome", "empirical_design"],
        synthesized_by="heuristic",
    )


def query_shape_ok(query_string: str) -> bool:
    """Cheap structural check that a draft query stays compact and on-topic."""
    if len(query_string) > _MAX_QUERY_CHARS:
        return False
    # count top-level-ish AND groups; quoted phrases may contain "and" so only
    # count the uppercase operator with spaces around it
    return query_string.count(" AND ") < _MAX_AND_GROUPS


def _synthesize_llm(question: str, pool: LLMPool) -> ReviewProtocol:
    prompt = f"Research question: {question}"
    for attempt in range(2):
        for ref in pool.synthesis_refs():
            try:
                response = pool.complete(
                    TaskType.PROTOCOL_SYNTHESIS,
                    system=_SYNTH_SYSTEM,
                    prompt=prompt,
                    max_tokens=1500,
                    ref=ref,
                )
            except ProviderError:
                pool.mark_unavailable(ref)
                continue
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw.removeprefix("json").strip()
            try:
                data: dict[str, Any] = json.loads(raw)
                query_string = str(data["query_string"])
                parse_query(query_string)
            except (ValueError, KeyError, QueryParseError):
                continue
            if query_shape_ok(query_string) or attempt == 1:
                return ReviewProtocol(
                    question=question,
                    inclusion_criteria=[
                        str(criterion) for criterion in data.get("inclusion_criteria", [])
                    ],
                    exclusion_criteria=[
                        str(criterion) for criterion in data.get("exclusion_criteria", [])
                    ],
                    query_string=query_string,
                    comparison_required=bool(data.get("comparison_required", False)),
                    comparison_note=str(data.get("comparison_note") or ""),
                    eligibility_dimensions=[
                        str(dimension) for dimension in data.get("eligibility_dimensions", [])
                    ][:12],
                    synthesized_by=f"{response.provider}:{response.model}",
                )
            # one corrective pass: an over-long query costs precision downstream
            prompt = (
                f"Research question: {question}\n\n"
                f"Your previous query was too long or had too many AND groups:\n"
                f"{query_string}\n\n"
                "Produce the protocol again. Compress the query to at most 3 AND "
                "blocks with at most 7 OR terms each, under 320 characters."
            )
            break
    raise ProviderError("no protocol synthesis route returned a valid compact protocol")
