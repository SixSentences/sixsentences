"""Loss-minimizing text tokenization for research-integrity comparisons."""

import unicodedata

_LEXICAL_CATEGORIES = frozenset({"L", "M", "N"})


def is_lexical_token(token: str) -> bool:
    """Return whether a token contains only Unicode letters, marks, or numbers."""

    return bool(token) and all(
        unicodedata.category(character)[0] in _LEXICAL_CATEGORIES for character in token
    )


def structural_tokens(text: str) -> tuple[str, ...]:
    """Case-fold text without discarding internal non-whitespace structure.

    Consecutive Unicode letters, combining marks, and numbers form lexical
    tokens. Every punctuation, symbol, or other non-whitespace code point is a
    literal token of its own. Whitespace only separates tokens. No placeholder
    words are injected, so literal input cannot collide with encoded symbols.
    """

    tokens: list[str] = []
    lexical: list[str] = []

    def flush_lexical() -> None:
        if lexical:
            tokens.append("".join(lexical))
            lexical.clear()

    for character in text.casefold():
        if character.isspace():
            flush_lexical()
        elif unicodedata.category(character)[0] in _LEXICAL_CATEGORIES:
            lexical.append(character)
        else:
            flush_lexical()
            tokens.append(character)
    flush_lexical()
    return tuple(tokens)
