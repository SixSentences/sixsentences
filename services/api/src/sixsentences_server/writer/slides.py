"""Manuscript-to-slides drafting: a complete beamer deck from the paper.

The model restructures, never invents: numbers come from the manuscript,
citations only from the provided keys. The result is validated to be a
complete beamer document before it becomes a writer document.
"""

from __future__ import annotations

import re

from sixsentences_server.llm.base import TaskType
from sixsentences_server.llm.pool import LLMPool

_SLIDES_SYSTEM = (
    "You turn a LaTeX manuscript into a presentation deck. Return ONLY the "
    "LaTeX source of one complete beamer document (starting at "
    "\\documentclass, ending at \\end{document}), with no prose around it. "
    "Rules: \\documentclass[aspectratio=169]{beamer} with "
    "\\usetheme{metropolis}, booktabs and [numbers]{natbib}; at most 14 "
    "frames; the sections follow the manuscript's argument (motivation, "
    "method, results, takeaway); exact numbers only, copied from the "
    "manuscript; cite with \\citep{key} using ONLY the provided keys; end "
    "with a references frame using \\bibliography{references}; one standout "
    "frame with the single-sentence takeaway. No em dashes, no markdown, no "
    "placeholder text."
)


class SlidesDraftError(Exception):
    """The model returned something that is not a complete beamer deck."""


def draft_slides(
    pool: LLMPool,
    *,
    title: str,
    manuscript: str,
    cite_keys: list[str],
    figures: list[str],
) -> str:
    """One compile-ready deck source, validated before it is stored."""
    keys = ", ".join(cite_keys[:80]) or "(no citations available)"
    figure_list = ", ".join(figures[:20]) or "(no figures available)"
    prompt = (
        f"Manuscript title: {title}\n"
        f"Citation keys you may use: {keys}\n"
        f"Figure files available for \\includegraphics: {figure_list}\n\n"
        f"Manuscript source:\n{manuscript[:60_000]}"
    )
    response = pool.complete(TaskType.CHAT, system=_SLIDES_SYSTEM, prompt=prompt, max_tokens=6000)
    text = response.text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start = text.find("\\documentclass")
    end = text.rfind("\\end{document}")
    if start < 0 or end < 0:
        raise SlidesDraftError("the model returned no complete beamer document")
    deck = text[start : end + len("\\end{document}")] + "\n"
    if "\\begin{frame}" not in deck:
        raise SlidesDraftError("the model returned no frames")
    return deck
