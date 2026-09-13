"""Shared fail-closed responses for structured research agents."""


def structured_response_failure(language: str) -> str:
    """Return a safe user-facing failure without exposing model internals."""

    if language.casefold().startswith("de"):
        return (
            "Das hat gerade nicht geklappt. Ihr Workspace wurde nicht verändert. "
            "Versuchen Sie es bitte erneut oder markieren Sie bei einer Änderung "
            "die betreffende Stelle."
        )
    return (
        "That didn't work just now. Your workspace was not changed. Please try "
        "again, or select the exact passage you want changed."
    )
