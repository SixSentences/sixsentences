"""Conservative identity verification for automatically acquired full texts.

Open-access metadata occasionally points at a neighbouring repository record or
an unrelated PDF. Screening such a document is worse than reporting it as not
retrieved. This module therefore requires a strong DOI or title/author match
before a sufficiently long extracted document is accepted.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from sixsentences_server.core.models import WorkRecord

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TITLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "using",
        "via",
        "with",
    }
)
_MIN_STRONG_TEXT = 1_000


class IdentityStatus(StrEnum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class IdentityCheck:
    status: IdentityStatus
    reason: str
    title_coverage: float
    detected_dois: tuple[str, ...] = ()


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(_TOKEN_RE.findall(value.lower()))


def _doi(value: str | None) -> str:
    if not value:
        return ""
    return value.lower().removeprefix("https://doi.org/").removeprefix("doi:").rstrip(".,;)")


def _title_tokens(value: str) -> list[str]:
    return [
        token
        for token in _normalize(value).split()
        if token not in _TITLE_STOPWORDS and len(token) > 2
    ]


def verify_document_identity(work: WorkRecord, extracted_text: str) -> IdentityCheck:
    """Verify that extracted text belongs to ``work``.

    Short or poorly extracted documents remain inconclusive instead of being
    rejected. A long document is rejected only when neither its DOI, title nor
    author line supports the expected identity.
    """

    front = extracted_text[:60_000]
    normalized_front = _normalize(front)
    expected_doi = _doi(work.doi)
    detected_dois = tuple(dict.fromkeys(_doi(match.group(0)) for match in _DOI_RE.finditer(front)))
    if expected_doi and expected_doi in detected_dois:
        return IdentityCheck(
            IdentityStatus.VERIFIED,
            "expected DOI found in the acquired document",
            1.0,
            detected_dois,
        )

    title_tokens = _title_tokens(work.title)
    present = sum(1 for token in title_tokens if token in normalized_front.split())
    title_coverage = present / len(title_tokens) if title_tokens else 0.0
    normalized_title = _normalize(work.title)
    if len(title_tokens) >= 4 and (normalized_title in normalized_front or title_coverage >= 0.72):
        return IdentityCheck(
            IdentityStatus.VERIFIED,
            "expected title matched the acquired document",
            title_coverage,
            detected_dois,
        )

    author_tokens = {
        token for author in work.authors[:8] for token in _title_tokens(author) if len(token) >= 4
    }
    author_coverage = (
        sum(1 for token in author_tokens if token in normalized_front.split()) / len(author_tokens)
        if author_tokens
        else 0.0
    )
    if len(title_tokens) >= 4 and title_coverage >= 0.55 and author_coverage >= 0.34:
        return IdentityCheck(
            IdentityStatus.VERIFIED,
            "expected title and author metadata matched the acquired document",
            title_coverage,
            detected_dois,
        )

    if len(extracted_text) < _MIN_STRONG_TEXT or len(title_tokens) < 4:
        return IdentityCheck(
            IdentityStatus.INCONCLUSIVE,
            "not enough extracted metadata to verify document identity",
            title_coverage,
            detected_dois,
        )

    mismatch_detail = (
        f"detected other DOI(s): {', '.join(detected_dois[:3])}"
        if detected_dois
        else "expected title was not found"
    )
    return IdentityCheck(
        IdentityStatus.MISMATCH,
        f"document identity mismatch ({mismatch_detail})",
        title_coverage,
        detected_dois,
    )
