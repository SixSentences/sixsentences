"""Identifier-only deduplication and non-destructive companion-report links.

Destructive deduplication requires the same syntactically valid canonical DOI.
Titles, author names, and publication years never authorize deletion because
different studies can share that metadata. Invalid or placeholder DOI values
remain separate records. The canonical record prefers more citations and a
published venue.

The companion-report helper is deliberately non-destructive. It can surface
weaker title/author/year similarities for caller-owned review while preserving
every input record. Title comparisons use structural Unicode tokens, so no
internal punctuation or symbol is discarded or replaced by a collision-prone
placeholder.
"""

import re
from collections import defaultdict
from difflib import SequenceMatcher

from sixsentences.core.models import WorkRecord
from sixsentences.core.text import is_lexical_token, structural_tokens

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:[ \t]*)", re.IGNORECASE)
_DOI_PATTERN = re.compile(r"10\.[0-9]{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE | re.ASCII)
_INVALID_DOI_VALUES = frozenset(
    {"", "-", "n/a", "na", "none", "null", "unknown", "unavailable", "not available", "missing"}
)
_INVALID_DOI_SUFFIXES = _INVALID_DOI_VALUES | {
    "dummy",
    "example",
    "fake",
    "invalid",
    "nil",
    "no-doi",
    "not-assigned",
    "notassigned",
    "pending",
    "placeholder",
    "tba",
    "tbd",
    "temporary",
    "test",
    "todo",
    "undefined",
}
_PLACEHOLDER_DOI_WORDS = frozenset(
    {
        "assigned",
        "available",
        "doi",
        "dummy",
        "example",
        "fake",
        "invalid",
        "missing",
        "na",
        "nil",
        "no",
        "none",
        "not",
        "null",
        "pending",
        "placeholder",
        "tba",
        "tbd",
        "temporary",
        "test",
        "todo",
        "undefined",
        "unknown",
        "unavailable",
    }
)
_MIN_TITLE = 15  # shorter titles are too generic to dedup safely


def canonicalize_doi(value: str | None) -> str | None:
    """Return a conservative canonical DOI, or ``None`` for unsafe identity values."""

    if value is None:
        return None
    candidate = value.strip()
    if any(character.isspace() and character not in {" ", "\t"} for character in candidate):
        return None
    if candidate.casefold() in _INVALID_DOI_VALUES:
        return None
    candidate = _DOI_PREFIX.sub("", candidate, count=1).casefold()
    if candidate in _INVALID_DOI_VALUES or _DOI_PATTERN.fullmatch(candidate) is None:
        return None
    prefix, _, suffix = candidate.partition("/")
    registrant = prefix.removeprefix("10.")
    placeholder_suffix = suffix.strip("-._;()/:")
    suffix_parts = re.findall(r"[a-z0-9]+", suffix, flags=re.ASCII)
    alphanumeric_suffix = "".join(suffix_parts)
    if (
        not alphanumeric_suffix
        or set(registrant) == {"0"}
        or set(alphanumeric_suffix) == {"0"}
        or suffix in _INVALID_DOI_SUFFIXES
        or placeholder_suffix in _INVALID_DOI_SUFFIXES
        or alphanumeric_suffix in _INVALID_DOI_SUFFIXES
        or (suffix_parts and all(part in _PLACEHOLDER_DOI_WORDS for part in suffix_parts))
    ):
        return None
    return candidate


def _title_tokens(title: str) -> tuple[str, ...]:
    return structural_tokens(title)


def _is_specific_title(title: str) -> bool:
    return (
        sum(len(token) for token in _title_tokens(title) if is_lexical_token(token)) >= _MIN_TITLE
    )


def _is_preprint(venue: str | None) -> bool:
    v = (venue or "").lower()
    return "arxiv" in v or "preprint" in v or "biorxiv" in v or "ssrn" in v


def _canonical(group: list[WorkRecord]) -> WorkRecord:
    # All records share a valid DOI; prefer richer, published metadata.
    return max(
        group,
        key=lambda w: (int(w.cited_by_count), not _is_preprint(w.venue)),
    )


def _first_author_identity(authors: list[str]) -> tuple[str, tuple[str, ...]] | None:
    if not authors:
        return None
    author = authors[0].casefold()
    if "," in author:
        family_text, _, given_text = author.partition(",")
        family_tokens = re.findall(r"[^\W\d_]+", family_text)
        given_tokens = re.findall(r"[^\W\d_]+", given_text)
    else:
        tokens = re.findall(r"[^\W\d_]+", author)
        family_tokens = tokens[-1:]
        given_tokens = tokens[:-1]
    if not family_tokens or not given_tokens:
        return None
    return " ".join(family_tokens), tuple(given_tokens)


def _authors_compatible_for_link(
    left: tuple[str, tuple[str, ...]],
    right: tuple[str, tuple[str, ...]],
) -> bool:
    left_family, left_given = left
    right_family, right_given = right
    if left_family != right_family or len(left_given) != len(right_given):
        return False
    for left_name, right_name in zip(left_given, right_given, strict=False):
        if len(left_name) == 1 or len(right_name) == 1:
            if left_name[0] != right_name[0]:
                return False
        elif left_name != right_name:
            return False
    return True


def _first_author_key(authors: list[str]) -> str:
    identity = _first_author_identity(authors)
    return identity[0] if identity is not None else ""


def dedup_by_doi(works: list[WorkRecord]) -> tuple[list[WorkRecord], int]:
    """Deduplicate only records sharing the same valid canonical DOI.

    Each DOI group occupies the position of its first record. Missing,
    placeholder, or malformed DOI values never become identity evidence.
    """

    groups: dict[str, list[WorkRecord]] = defaultdict(list)
    for work in works:
        doi = canonicalize_doi(work.doi)
        if doi is not None:
            groups[doi].append(work)

    kept: list[WorkRecord] = []
    emitted: set[str] = set()
    removed = 0
    for work in works:
        doi = canonicalize_doi(work.doi)
        if doi is None:
            kept.append(work)
            continue
        if doi in emitted:
            continue
        emitted.add(doi)
        group = groups[doi]
        if len(group) == 1:
            kept.append(group[0])
        else:
            kept.append(_canonical(group))
            removed += len(group) - 1
    return kept, removed


_DIGITS = re.compile(r"\d+")
_SAME_STUDY_SIMILARITY = 0.93


def _looks_preprint(work: WorkRecord) -> bool:
    return _is_preprint(work.venue) or (work.work_type or "") == "preprint"


def link_same_study_reports(
    works: list[WorkRecord],
) -> list[tuple[WorkRecord, WorkRecord]]:
    """Identify likely companion reports without discarding either report.

    Extremely conservative on purpose: near-identical structural title tokens
    (>=0.93), the same first author, publication years within two, and
    identical digit sequences (so "Part I" / "Part II" never merge) — and one
    side must look like a preprint, the classic companion pattern. PRISMA
    distinguishes records, reports, and studies, so this helper returns links
    while preserving every input report for caller-owned study grouping.

    Each returned pair is in input order: ``(earlier_report, later_report)``.
    """
    linked: list[tuple[WorkRecord, WorkRecord]] = []
    by_author: dict[str, list[WorkRecord]] = {}
    for work in works:
        author_key = _first_author_key(work.authors)
        norm = _title_tokens(work.title)
        if author_key and _is_specific_title(work.title):
            for other in by_author.get(author_key, []):
                work_author = _first_author_identity(work.authors)
                other_author = _first_author_identity(other.authors)
                if (
                    work_author is None
                    or other_author is None
                    or not _authors_compatible_for_link(work_author, other_author)
                ):
                    continue
                other_norm = _title_tokens(other.title)
                if _DIGITS.findall(" ".join(norm)) != _DIGITS.findall(" ".join(other_norm)):
                    continue
                if (
                    work.year is not None
                    and other.year is not None
                    and abs(work.year - other.year) > 2
                ):
                    continue
                if not (_looks_preprint(work) or _looks_preprint(other)):
                    continue
                shorter, longer = sorted((norm, other_norm), key=len)
                shorter_lexical_length = sum(
                    len(token) for token in shorter if is_lexical_token(token)
                )
                subtitle_drift = shorter_lexical_length >= 25 and longer[: len(shorter)] == shorter
                close = SequenceMatcher(None, norm, other_norm).ratio() >= _SAME_STUDY_SIMILARITY
                if not (close or subtitle_drift):
                    continue
                linked.append((other, work))
                break
        if author_key:
            by_author.setdefault(author_key, []).append(work)
    return linked
