"""Small text conventions for user-facing model output."""

import re

_DASHES = re.compile(r"\s*[–—]\s*")
_SPACES = re.compile(r" {2,}")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
# single-asterisk italic, conservatively: flush at both words, single line —
# a lone asterisk (footnote markers, multiplication) never matches
_MD_ITALIC = re.compile(r"\*(?!\s)([^*\n]{2,200}?)(?<!\s)\*")


def strip_dashes(text: str) -> str:
    """Apply the house plain-text style: no em/en dashes, no markdown
    bold/italics.

    Models are told the same, but they drift; this makes the rules mechanical.
    """
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    return _SPACES.sub(" ", _DASHES.sub(" - ", text))
