"""Source matching for quotes attached to screening decisions.

This module checks whether model-provided text occurs in a work's title or
abstract. Matching is tolerant of case, whitespace, and narrowly bounded
spelling variation. Internal punctuation and symbols must match literally. A
source match does not establish that the quote supports a decision; callers and
human reviewers remain responsible for interpreting it.

Returns the quote to store (None when it fails to match) and a match status.
"""

import unicodedata
from difflib import SequenceMatcher

from sixsentences.core.text import is_lexical_token, structural_tokens

MIN_QUOTE_LEN = 8  # shorter spans match by chance and are not real evidence
FUZZY_THRESHOLD = 0.9
_NEGATIONS = frozenset(
    {
        "cannot",
        "no",
        "nobody",
        "none",
        "noone",
        "not",
        "nothing",
        "nowhere",
        "never",
        "neither",
        "nor",
        "without",
    }
)


def _source_windows(
    source: str,
    size: int,
) -> list[tuple[tuple[str, ...], int, tuple[str, ...]]]:
    source_tokens = structural_tokens(source)
    return [
        (source_tokens, index, source_tokens[index : index + size])
        for index in range(len(source_tokens) - size + 1)
    ]


def _is_clause_boundary(source_tokens: tuple[str, ...], index: int) -> bool:
    token = source_tokens[index]
    if token in {"!", "?", ";"}:
        return True
    if token not in {".", ":"}:
        return False
    previous_is_number = index > 0 and source_tokens[index - 1].isnumeric()
    next_is_number = index + 1 < len(source_tokens) and source_tokens[index + 1].isnumeric()
    return not (previous_is_number and next_is_number)


def _omits_preceding_negation(source_tokens: tuple[str, ...], index: int) -> bool:
    for preceding_index in range(index - 1, -1, -1):
        if _is_clause_boundary(source_tokens, preceding_index):
            return False
        token = source_tokens[preceding_index]
        compact_contraction = (
            len(token) >= 3
            and token[-1] == "t"
            and token[-3] == "n"
            and _is_apostrophe_character(token[-2])
        )
        if token in _NEGATIONS or compact_contraction:
            return True
        if (
            token == "t"
            and preceding_index >= 2
            and len(source_tokens[preceding_index - 1]) == 1
            and _is_apostrophe_character(source_tokens[preceding_index - 1])
            and source_tokens[preceding_index - 2].endswith("n")
        ):
            return True
    return False


def _is_apostrophe_character(character: str) -> bool:
    unicode_name = unicodedata.name(character, "")
    return ("SINGLE" in unicode_name and "QUOTATION MARK" in unicode_name) or any(
        marker in unicode_name
        for marker in (
            "ACUTE ACCENT",
            "APOSTROPHE",
            "GRAVE ACCENT",
            "PRIME",
            "TURNED COMMA",
        )
    )


def _contains_numeric_character(token: str) -> bool:
    return any(character.isnumeric() for character in token)


def _window_preserves_structure(
    source_tokens: tuple[str, ...],
    index: int,
    size: int,
) -> bool:
    if index > 0 and not is_lexical_token(source_tokens[index - 1]):
        return False
    end = index + size
    return end >= len(source_tokens) or is_lexical_token(source_tokens[end])


def _minor_token_variant(left: str, right: str) -> bool:
    if left == right:
        return True
    if _contains_numeric_character(left) or _contains_numeric_character(right):
        return False
    if not is_lexical_token(left) or not is_lexical_token(right):
        return False
    return (
        left[:1] == right[:1]
        and left[-1:] == right[-1:]
        and abs(len(left) - len(right)) <= 1
        and SequenceMatcher(None, left, right).ratio() >= FUZZY_THRESHOLD
    )


def verify_quote(quote: str, source: str) -> tuple[str | None, str]:
    """Match a model-provided quote against the source text.

    status is one of: "exact", "fuzzy", "unverified", "empty".
    The returned quote is None unless it verifies (exact or fuzzy).
    """
    cleaned = quote.strip()
    quote_tokens = structural_tokens(cleaned)
    if sum(len(token) for token in quote_tokens) < MIN_QUOTE_LEN:
        return None, "empty"
    windows = _source_windows(source, len(quote_tokens))
    for source_words, index, window in windows:
        if (
            window == quote_tokens
            and _window_preserves_structure(source_words, index, len(quote_tokens))
            and not _omits_preceding_negation(source_words, index)
        ):
            return cleaned, "exact"

    quote_negations = [word for word in quote_tokens if word in _NEGATIONS]
    quote_numbers = [word for word in quote_tokens if _contains_numeric_character(word)]
    for source_words, index, window in windows:
        if not _window_preserves_structure(source_words, index, len(quote_tokens)):
            continue
        if _omits_preceding_negation(source_words, index):
            continue
        if quote_negations != [word for word in window if word in _NEGATIONS]:
            continue
        if quote_numbers != [word for word in window if _contains_numeric_character(word)]:
            continue
        if all(
            _minor_token_variant(left, right)
            for left, right in zip(quote_tokens, window, strict=True)
        ):
            return cleaned, "fuzzy"
    return None, "unverified"
