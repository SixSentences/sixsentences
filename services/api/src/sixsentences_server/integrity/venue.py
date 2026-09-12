"""Venue integrity signal — list-driven, honest about the grey zone.

Venue quality is judged against curated LISTS, never a hardcoded verdict: a
whitelist of indexed journals (DOAJ) and a predatory/delisted list (e.g.
Cabells) that the institution configures. Single lists overlap poorly (1.9-4.2%
in the literature), so a venue that is on NO configured list is reported as
"unlisted" (grey zone) rather than judged either way. We deliberately ship no
built-in predatory names — that would risk libelling a real venue — so
"predatory" only ever appears when a user-provided list matches. Predatory is a
SIGNAL for review, never an auto-exclusion (QUALITY.md layer 4).

Lists load from `<data_dir>/venues_indexed.txt` and `venues_predatory.txt`
(one venue title per line).
"""

import re
from pathlib import Path

from pydantic import BaseModel

_WS = re.compile(r"\s+")
_NONWORD = re.compile(r"[^\w\s]")
_PREPRINT_MARKERS = (
    "arxiv",
    "preprint",
    "biorxiv",
    "medrxiv",
    "ssrn",
    "research square",
)


def normalize_venue(venue: str) -> str:
    return _WS.sub(" ", _NONWORD.sub(" ", venue.lower())).strip()


def is_preprint(venue: str | None) -> bool:
    v = (venue or "").lower()
    return any(marker in v for marker in _PREPRINT_MARKERS)


class VenueAssessment(BaseModel):
    venue: str | None
    status: str  # indexed | predatory | preprint | unlisted | unknown
    note: str


def load_venue_lists(data_dir: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return (indexed, predatory) normalized venue titles from the data dir."""

    def _load(name: str) -> frozenset[str]:
        path = data_dir / name
        if not path.exists():
            return frozenset()
        return frozenset(
            normalize_venue(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    return _load("venues_indexed.txt"), _load("venues_predatory.txt")


def assess_venue(
    venue: str | None,
    *,
    indexed: frozenset[str] = frozenset(),
    predatory: frozenset[str] = frozenset(),
) -> VenueAssessment:
    if not venue:
        return VenueAssessment(venue=venue, status="unknown", note="no venue on record")
    key = normalize_venue(venue)
    if key in predatory:
        return VenueAssessment(
            venue=venue, status="predatory", note="on the configured predatory list"
        )
    if is_preprint(venue):
        return VenueAssessment(
            venue=venue, status="preprint", note="preprint server (not peer reviewed)"
        )
    if key in indexed:
        return VenueAssessment(
            venue=venue, status="indexed", note="on the configured indexed (DOAJ) list"
        )
    return VenueAssessment(
        venue=venue,
        status="unlisted",
        note="not on any configured venue list (grey zone)",
    )


# Publication types that are not peer-reviewed research articles.
_NON_ARTICLE_TYPES = frozenset(
    {
        "preprint",
        "dataset",
        "peer-review",
        "editorial",
        "erratum",
        "grant",
        "letter",
        "paratext",
        "supplementary-materials",
        "reference-entry",
    }
)


def is_non_peer_reviewed(venue_status: str, work_type: str | None) -> bool:
    """A pragmatic peer-review screen for the ``peer_reviewed_only`` protocol flag.

    Drops detected preprints and clearly non-article types. Venue indexing is not
    fully known offline, so unknown ("unlisted") venues are KEPT — they may well
    be peer-reviewed; only clear non-articles and preprints are excluded.
    """
    if venue_status == "preprint":
        return True
    return (work_type or "").lower() in _NON_ARTICLE_TYPES
