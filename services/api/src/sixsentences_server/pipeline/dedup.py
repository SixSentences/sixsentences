"""Title-normalized deduplication (beyond exact DOI / OpenAlex-id).

The same paper is routinely re-indexed under different ids — an arXiv preprint,
its published version, and mirror records — often without a shared DOI, so
exact id/DOI dedup misses them (a live Terraform/IaC search returned the same
"IaC Generation with LLMs" paper three times). This collapses records whose
titles are identical after normalization (case, punctuation, hyphenation and
whitespace folded away — the variations that re-indexing introduces), keeping a
canonical record: a DOI beats none, more citations beat fewer, and a published
venue beats a preprint server. Also links preprint<->published as one record.

Deliberately conservative: very short/generic titles are never merged (they
could name distinct works), so a false merge that would drop a real study is
avoided at the cost of leaving a rare true duplicate.
"""

import re
from collections import defaultdict
from difflib import SequenceMatcher

from sixsentences_server.core.models import WorkRecord

_NONWORD = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_MIN_TITLE = 15  # shorter titles are too generic to dedup safely


def _normalize_title(title: str) -> str:
    return _WS.sub(" ", _NONWORD.sub(" ", (title or "").lower())).strip()


def _is_preprint(venue: str | None) -> bool:
    v = (venue or "").lower()
    return "arxiv" in v or "preprint" in v or "biorxiv" in v or "ssrn" in v


def _canonical(group: list[WorkRecord]) -> WorkRecord:
    # prefer: has a DOI, then more citations, then a published (non-preprint) venue
    return max(
        group,
        key=lambda w: (bool(w.doi), int(w.cited_by_count), not _is_preprint(w.venue)),
    )


def dedup_by_title(works: list[WorkRecord]) -> tuple[list[WorkRecord], int]:
    """Return (deduplicated works, number removed). Order of first appearance
    is preserved for the kept records."""
    groups: dict[str, list[WorkRecord]] = defaultdict(list)
    order: list[str] = []
    singletons: list[WorkRecord] = []
    for work in works:
        key = _normalize_title(work.title)
        if len(key) < _MIN_TITLE:
            singletons.append(work)
            continue
        if key not in groups:
            order.append(key)
        groups[key].append(work)

    kept: list[WorkRecord] = list(singletons)
    removed = 0
    for key in order:
        group = groups[key]
        if len(group) == 1:
            kept.append(group[0])
        else:
            kept.append(_canonical(group))
            removed += len(group) - 1
    return kept, removed


_DIGITS = re.compile(r"\d+")
_SAME_STUDY_SIMILARITY = 0.93


def _first_author_key(authors: list[str]) -> str:
    if not authors:
        return ""
    tokens = re.findall(r"[a-z]+", authors[0].lower())
    return tokens[-1] if tokens else ""


def _looks_preprint(work: WorkRecord) -> bool:
    return _is_preprint(work.venue) or (work.work_type or "") == "preprint"


def merge_same_study(
    works: list[WorkRecord],
) -> tuple[list[WorkRecord], list[tuple[WorkRecord, WorkRecord]]]:
    """Collapse multiple REPORTS of the same study — the arXiv preprint and
    its published article whose titles drifted apart, which exact-title dedup
    cannot see. PRISMA counts studies, not papers, so companion reports must
    fold into one canonical record.

    Extremely conservative on purpose: near-identical titles (>=0.93 after
    normalization), the same first author, publication years within two, and
    identical digit sequences (so "Part I" / "Part II" never merge) — and one
    side must look like a preprint, the classic companion pattern. A missed
    true companion is acceptable; silently dropping a distinct study is not.

    Returns (kept works, merged pairs as (dropped, kept_canonical))."""
    kept: list[WorkRecord] = []
    merged: list[tuple[WorkRecord, WorkRecord]] = []
    by_author: dict[str, list[int]] = {}  # first-author key -> indices in kept
    for work in works:
        author_key = _first_author_key(work.authors)
        norm = _normalize_title(work.title)
        target_index: int | None = None
        if author_key and len(norm) >= _MIN_TITLE:
            for index in by_author.get(author_key, []):
                other = kept[index]
                other_norm = _normalize_title(other.title)
                if _DIGITS.findall(norm) != _DIGITS.findall(other_norm):
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
                subtitle_drift = len(shorter) >= 25 and longer.startswith(shorter)
                close = SequenceMatcher(None, norm, other_norm).ratio() >= _SAME_STUDY_SIMILARITY
                if not (close or subtitle_drift):
                    continue
                target_index = index
                break
        if target_index is None:
            if author_key:
                by_author.setdefault(author_key, []).append(len(kept))
            kept.append(work)
            continue
        canonical = _canonical([kept[target_index], work])
        dropped = work if canonical is kept[target_index] else kept[target_index]
        kept[target_index] = canonical
        merged.append((dropped, canonical))
    return kept, merged
