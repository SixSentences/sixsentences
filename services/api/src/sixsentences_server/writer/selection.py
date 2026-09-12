"""Bounded Writer selection normalization shared by API and assistant paths."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

WRITER_SELECTION_MIN_CHARACTERS = 8
WRITER_SELECTION_MAX_CHARACTERS = 12_000
WRITER_SELECTION_MAX_SEGMENTS = 32
WRITER_SELECTION_MAX_PAGE = 2_000
WRITER_SELECTION_MAX_PAGE_SPAN = 32

_PDF_LINE_END_HYPHEN = re.compile(
    r"([^\W\d_])-[\t ]*(?:\r?\n)+[\t ]*([^\W\d_])",
    re.UNICODE,
)
_PDF_CHARACTER_FOLD: dict[int, str] = {
    ord("\N{LEFT SINGLE QUOTATION MARK}"): "'",
    ord("\N{RIGHT SINGLE QUOTATION MARK}"): "'",
    ord("\N{LEFT DOUBLE QUOTATION MARK}"): '"',
    ord("\N{RIGHT DOUBLE QUOTATION MARK}"): '"',
    ord("\N{EN DASH}"): "-",
    ord("\N{EM DASH}"): "-",
}


def normalize_pdf_selection_text(value: str) -> str:
    """Return a matching copy without mutating the stored user selection.

    PDF text layers expose visual wrapping as copy text. Only a hyphen directly
    interrupted by a line ending is dehyphenated; ordinary in-line compounds
    remain unchanged. Whitespace is then collapsed for stable source matching.
    """

    folded = unicodedata.normalize("NFKC", value).translate(_PDF_CHARACTER_FOLD)
    without_soft_hyphens = folded.replace("\N{SOFT HYPHEN}", "")
    dehyphenated = _PDF_LINE_END_HYPHEN.sub(r"\1\2", without_soft_hyphens)
    return " ".join(dehyphenated.split())


def writer_selection_matching_quote(selection: Mapping[str, Any] | None) -> str:
    """Return the complete bounded quote used for matching and prompting."""

    quote = str((selection or {}).get("quote") or "")
    if (selection or {}).get("kind") == "pdf":
        return normalize_pdf_selection_text(quote)
    return quote.strip()


def pdf_selection_matches_text(quote: str, page_text: str) -> bool:
    """Return whether a selected PDF quote is present in extracted page text."""

    normalized_quote = normalize_pdf_selection_text(quote).casefold()
    normalized_page = normalize_pdf_selection_text(page_text).casefold()
    return bool(normalized_quote) and normalized_quote in normalized_page


def source_region_matches_pdf_quote(
    source: str,
    line: int,
    quote: str,
    *,
    before: int = 1_200,
    after: int = 3_600,
) -> bool:
    """Bind a SyncTeX line hint to distinctive words from the selected quote."""

    if line < 1:
        return False
    lines = source.splitlines(keepends=True)
    if line > max(1, len(lines)):
        return False
    offset = sum(len(value) for value in lines[: line - 1])
    window = source[max(0, offset - before) : min(len(source), offset + after)]
    normalized_quote = normalize_pdf_selection_text(quote).casefold()
    normalized_window = normalize_pdf_selection_text(window).casefold()
    if normalized_quote in normalized_window:
        return True
    quote_words = re.findall(
        r"[^\W_]{3,}",
        normalized_quote,
        re.UNICODE,
    )
    if len(quote_words) < 3:
        return False
    window_words = re.findall(
        r"[^\W_]{3,}",
        normalized_window,
        re.UNICODE,
    )
    blocks = [
        block
        for block in SequenceMatcher(
            None,
            quote_words,
            window_words,
            autojunk=False,
        ).get_matching_blocks()
        if block.size > 0
    ]
    if not blocks:
        return False
    matched = sum(block.size for block in blocks)
    coverage = matched / len(quote_words)
    source_span = blocks[-1].b + blocks[-1].size - blocks[0].b
    density = matched / max(source_span, 1)
    longest_ordered_run = max(block.size for block in blocks)

    def compact_anchor_present(anchor: list[str]) -> bool:
        for start, word in enumerate(window_words):
            if word != anchor[0]:
                continue
            cursor = start + 1
            for expected in anchor[1:]:
                while cursor < len(window_words) and window_words[cursor] != expected:
                    cursor += 1
                if cursor >= len(window_words):
                    break
                cursor += 1
            else:
                if cursor - start <= len(anchor) + 2:
                    return True
        return False

    anchor_size = min(4, len(quote_words))
    head_bound = compact_anchor_present(quote_words[:anchor_size])
    tail_bound = compact_anchor_present(quote_words[-anchor_size:])
    return (
        coverage >= 0.9
        and density >= 0.65
        and longest_ordered_run >= 4
        and head_bound
        and tail_bound
    )


def writer_pdf_page_label(selection: Mapping[str, Any]) -> str:
    """Render an honest single-page or page-range label for the model."""

    page = selection.get("page", "?")
    page_end = selection.get("page_end")
    if page_end and page_end != page:
        return f"pages {page}\u2013{page_end}"
    return f"page {page}"
