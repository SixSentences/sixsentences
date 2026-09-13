"""Semantic sweep: LLM-paraphrased relevance searches over OpenAlex.

A boolean query only finds the words it contains. The sweep asks a strong
model for meaning-preserving paraphrases of the RESEARCH QUESTION (synonym
phrasings, adjacent terminology, spelled-out abbreviations) and runs each as
an OpenAlex relevance search — CC0 data, the index the run already uses, no
extra vendor. Every paraphrase is documented verbatim as its own PRISMA-S
execution, and every candidate faces the same screening as the boolean arms.
Without a strong model the sweep degrades honestly to one relevance search
over the original question — still a distinct arm from the boolean query.
"""

import json
import logging

from sixsentences_server.core.models import ReviewProtocol
from sixsentences_server.llm.base import TaskType
from sixsentences_server.llm.pool import LLMPool

logger = logging.getLogger(__name__)

_PARAPHRASE_SYSTEM = (
    "You reformulate a research question for literature search coverage. "
    "Produce meaning-preserving paraphrases that a different research "
    "community would use: synonym terminology, spelled-out abbreviations, "
    "adjacent phrasings. Keep the technical core intact; do not broaden the "
    'topic. Answer with STRICT JSON only: {"paraphrases": ["...", "..."]}.'
)


def propose_question_paraphrases(
    question: str,
    protocol: ReviewProtocol,
    pool: LLMPool | None,
    *,
    count: int = 4,
) -> list[str]:
    """Up to `count` distinct paraphrases, or [] when no strong model runs."""
    if pool is None or not pool.has_strong():
        return []
    criteria = "; ".join(protocol.inclusion_criteria[:6]) or "none stated"
    prompt = (
        f"Research question: {question}\n"
        f"Inclusion criteria (context, do not restate): {criteria}\n"
        f"Return up to {count} paraphrases."
    )
    try:
        response = pool.complete(
            TaskType.CHAT, system=_PARAPHRASE_SYSTEM, prompt=prompt, max_tokens=400
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        candidates = json.loads(raw).get("paraphrases") or []
    except Exception:  # degraded sweep beats a failed run
        logger.warning("paraphrase proposal failed; sweeping the question only")
        return []
    seen: set[str] = {question.strip().lower()}
    paraphrases: list[str] = []
    for candidate in candidates:
        text = str(candidate).strip()
        key = text.lower()
        if len(text) >= 10 and key not in seen:
            seen.add(key)
            paraphrases.append(text)
    return paraphrases[:count]
