"""Supported product languages and model-facing response instructions."""

import re

SUPPORTED_LANGUAGES = frozenset({"en", "de"})

_GERMAN_REQUEST = re.compile(
    r"\b(?:auf\s+deutsch|deutsch(?:e|en|er)?|ich|du|bitte|mach|oeffne|öffne|zeig|"
    r"fass|erklaer|erklär|nur|nicht|fuer|für|anfänger|zusammen|danach|vergiss|"
    r"suche|finde|papier|stellen|welche|vergleich(?:e|en|st|t)?|wurde[n]?|ohne)\b",
    re.IGNORECASE,
)
_ENGLISH_REQUEST = re.compile(
    r"\b(?:in\s+english|english|please|find|show|open|explain|summarize|only|"
    r"afterwards|forget|search|paper|passages|beginner|compare|were|without)\b",
    re.IGNORECASE,
)


def normalize_language(language: str | None) -> str:
    """Return a supported language code, defaulting safely to English."""
    return language if language in SUPPORTED_LANGUAGES else "en"


def infer_response_language(text: str, fallback: str | None = None) -> str:
    """Use the current request language without changing the saved UI locale.

    A saved English interface must not make a clearly German chat response
    switch to English. Short or genuinely mixed requests retain the explicit
    saved preference, while direct language instructions always win.
    """

    folded = text.casefold()
    if re.search(r"\b(?:auf\s+deutsch|antworte\s+deutsch)\b", folded):
        return "de"
    if re.search(r"\b(?:in\s+english|answer\s+in\s+english)\b", folded):
        return "en"
    if re.search(
        r"\b(?:schau|schaue|such|suche)\b.{0,60}\b(?:im\s+internet|online|im\s+web)\b",
        folded,
    ):
        return "de"
    german = len(_GERMAN_REQUEST.findall(text))
    english = len(_ENGLISH_REQUEST.findall(text))
    if german >= 2 and german > english:
        return "de"
    if english >= 2 and english > german:
        return "en"
    return normalize_language(fallback)


def response_language_instruction(language: str | None) -> str:
    """Build the system-prompt rule for the user's saved output language."""
    if normalize_language(language) == "de":
        return (
            "\n\nRESPONSE LANGUAGE\n"
            "Write all user-facing prose in natural, professional German. "
            "Use conventional, idiomatic wording. Do not invent unusual collective "
            "nouns or translate English product language literally. Preserve "
            "source titles, citation keys, code, LaTeX commands and proper names in "
            "their original form. Do not use em dashes or en dashes as "
            "punctuation. Use commas, colons, parentheses or full stops instead. "
            "Preserve hyphens required by source titles, established compound "
            "terms, URLs, code, identifiers, quotations and LaTeX syntax."
        )
    return (
        "\n\nRESPONSE LANGUAGE\n"
        "Write all user-facing prose in natural, professional English. "
        "Use conventional, idiomatic wording and avoid awkward literal translations. "
        "Preserve "
        "source titles, citation keys, code, LaTeX commands and proper names in "
        "their original form. Do not use em dashes or en dashes as punctuation. "
        "Use commas, colons, parentheses or full stops instead. Preserve hyphens "
        "required by source titles, established compound terms, URLs, code, "
        "identifiers, quotations and LaTeX syntax."
    )
