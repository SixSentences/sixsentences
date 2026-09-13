"""arXiv fallback: recover a green-OA preprint when OpenAlex has no OA copy.

For the CS/ML beachhead, many works OpenAlex marks *closed* have an arXiv preprint
it never linked. This queries the arXiv API by title and accepts a match only
under a **conservative gate** (high title similarity + matching year), because
attaching the *wrong* paper's full text would poison the evidence. The result is
a green-OA copy, so principle 7 holds. Stdlib XML + httpx (existing deps).
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol
from xml.etree import ElementTree

import httpx

ARXIV_API = "https://export.arxiv.org/api/query"  # http 301-redirects; hit https directly
_ATOM = "{http://www.w3.org/2005/Atom}"
_TITLE_THRESHOLD = 0.92  # high on purpose: we want the SAME paper, not a similar one
_VERSION_RE = re.compile(r"v\d+$")
_NONWORD = re.compile(r"[^a-z0-9]+")


@dataclass
class ArxivEntry:
    arxiv_id: str
    title: str
    year: int | None


@dataclass
class ArxivMatch:
    arxiv_id: str
    score: float
    matched_title: str


class ArxivFinder(Protocol):
    def find(self, title: str, year: int | None) -> ArxivMatch | None: ...


def _norm(text: str) -> str:
    return _NONWORD.sub(" ", text.lower()).strip()


def best_match(
    title: str,
    year: int | None,
    entries: list[ArxivEntry],
    *,
    threshold: float = _TITLE_THRESHOLD,
) -> ArxivMatch | None:
    """Best arXiv entry for a work, or None if none clears the conservative gate."""
    target = _norm(title)
    if not target:
        return None
    best: ArxivMatch | None = None
    for entry in entries:
        score = SequenceMatcher(None, target, _norm(entry.title)).ratio()
        if score < threshold:
            continue
        if year is not None and entry.year is not None and abs(year - entry.year) > 1:
            continue  # a preprint precedes publication by at most ~a year
        if best is None or score > best.score:
            best = ArxivMatch(
                arxiv_id=entry.arxiv_id,
                score=round(score, 3),
                matched_title=entry.title,
            )
    return best


def _arxiv_id_from_url(id_url: str) -> str:
    return _VERSION_RE.sub("", id_url.rsplit("/abs/", 1)[-1])


def parse_atom(xml_bytes: bytes) -> list[ArxivEntry]:
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return []
    entries: list[ArxivEntry] = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        id_url = (entry.findtext(f"{_ATOM}id") or "").strip()
        published = entry.findtext(f"{_ATOM}published") or ""
        if not title or "/abs/" not in id_url:
            continue
        year = int(published[:4]) if published[:4].isdigit() else None
        entries.append(ArxivEntry(arxiv_id=_arxiv_id_from_url(id_url), title=title, year=year))
    return entries


class ArxivClient:
    def __init__(self, *, http: httpx.Client | None = None, max_results: int = 5) -> None:
        self.max_results = max_results
        self.http = http or httpx.Client(timeout=30, follow_redirects=True)

    def find(self, title: str, year: int | None) -> ArxivMatch | None:
        return best_match(title, year, self._search(title))

    def resolve_exact_title(self, title: str) -> ArxivEntry | None:
        """Resolve an exact title without trusting a possibly corrupt year.

        OpenAlex occasionally merges a highly cited historic preprint with a
        much newer repository deposit.  Acquisition normally keeps its strict
        year gate, but metadata reconciliation needs an independent title-only
        check so it can recover the original arXiv year and identifier.  The
        near-exact threshold deliberately rejects merely related papers.
        """
        target = _norm(title)
        if not target:
            return None
        candidates = [
            entry
            for entry in self._search(title)
            if SequenceMatcher(None, target, _norm(entry.title)).ratio() >= 0.985
        ]
        return min(candidates, key=lambda entry: entry.year or 9999, default=None)

    def _search(self, title: str) -> list[ArxivEntry]:
        query = _norm(title)  # arXiv search chokes on punctuation; the words suffice
        if not query:
            return []
        try:
            response = self.http.get(
                ARXIV_API,
                params={
                    "search_query": f'ti:"{query}"',
                    "max_results": self.max_results,
                },
            )
        except httpx.HTTPError:
            return []  # best-effort: a failed arXiv query just means no fallback
        if response.status_code != 200:
            return []
        return parse_atom(response.content)
