"""Verbatim-quote verification for screening (product principle 2).

Every screening decision should carry a quote from the work that supports it —
but a model can fabricate or paraphrase a quote, which would reintroduce
hallucination. So a model-provided quote is only trusted if it genuinely occurs
in the work's title/abstract. Verification is tolerant of case, whitespace and
surrounding punctuation (models re-quote imperfectly) but not of invented text:
a near-verbatim span passes, a paraphrase or fabrication does not.

Returns the quote to store (None when it fails to verify) and a status, so the
pipeline can report how many decisions are evidence-backed vs. how often a model
tried to cite something that was not there.
"""

import re
from difflib import SequenceMatcher

_WS = re.compile(r"\s+")
_NONWORD = re.compile(r"[^\w\s]")
_EDGE = " \t\n\r\"'`.…,;:!?—–-()[]{}"
MIN_QUOTE_LEN = 8  # shorter spans match by chance and are not real evidence
FUZZY_THRESHOLD = 0.9  # longest common run must cover >=90% of the quote


def _normalize(text: str) -> str:
    # word-level: punctuation/hyphenation collapse to spaces so re-quoting
    # variations ("state-of-the-art" vs "state of the art") still verify, while
    # a different WORD sequence (a fabrication/paraphrase) does not.
    return _WS.sub(" ", _NONWORD.sub(" ", text.lower())).strip()


def verify_quote(quote: str, source: str) -> tuple[str | None, str]:
    """Verify a model-provided quote against the source text.

    status is one of: "exact", "fuzzy", "unverified", "empty".
    The returned quote is None unless it verifies (exact or fuzzy).
    """
    cleaned = quote.strip().strip(_EDGE)
    normalized = _normalize(cleaned)
    if len(normalized) < MIN_QUOTE_LEN:
        return None, "empty"
    source_norm = _normalize(source)
    if normalized in source_norm:
        return cleaned, "exact"
    match = SequenceMatcher(None, normalized, source_norm, autojunk=False).find_longest_match()
    if match.size / len(normalized) >= FUZZY_THRESHOLD:
        return cleaned, "fuzzy"
    return None, "unverified"
