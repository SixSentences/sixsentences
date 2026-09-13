"""Harvest scholarly works out of grey-literature web search results.

Web search regularly surfaces actual papers (arXiv pages, DOI landing pages,
publisher links). Instead of leaving them as unstructured grey literature, this
module detects them, resolves them to canonical OpenAlex works (title,
abstract, authors — everything screening needs) and hands them to the pipeline
as a PRISMA 2020 "identification via other methods" arm: they are deduplicated
against the database arm and screened by the same ensemble.

Detection is deliberately conservative — only ids we can parse from the URL
itself (arXiv id or DOI), never guessed from titles. Resolution goes through
OpenAlex so every harvested work keeps a canonical W-id and stays citable in
chat and exports.
"""

import re
from dataclasses import dataclass, field

from sixsentences_server.connectors.openalex import OpenAlexClient
from sixsentences_server.connectors.websearch import WebSource
from sixsentences_server.core.models import WorkRecord

# arXiv ids: new scheme (2301.12345) or legacy (hep-th/9901001)
_ARXIV_URL = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})",
    re.IGNORECASE,
)
# a DOI embedded anywhere in a URL path (doi.org, dl.acm.org, link.springer, ...)
_DOI_URL = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>#?&]+)", re.IGNORECASE)
_VERSION_SUFFIX = re.compile(r"v\d+$")


@dataclass(frozen=True)
class ScholarlyRef:
    """One paper reference recovered from a web result."""

    kind: str  # "arxiv" | "doi"
    value: str
    source_url: str

    @property
    def external_id(self) -> str:
        return f"{self.kind}:{self.value}"


def extract_scholarly_refs(sources: list[WebSource]) -> list[ScholarlyRef]:
    """Paper ids parsed from web-result URLs, deduplicated, input order kept."""
    refs: list[ScholarlyRef] = []
    seen: set[str] = set()

    def add(kind: str, value: str, url: str) -> None:
        key = f"{kind}:{value.lower()}"
        if key not in seen:
            seen.add(key)
            refs.append(ScholarlyRef(kind=kind, value=value, source_url=url))

    for source in sources:
        url = source.url
        if match := _ARXIV_URL.search(url):
            add("arxiv", _VERSION_SUFFIX.sub("", match.group(1)), url)
            continue
        if match := _DOI_URL.search(url):
            doi = match.group(1).rstrip(".)/")
            # arXiv DOIs (10.48550/arXiv.xxxx) resolve better via the arxiv id
            if arxiv := re.match(r"10\.48550/arxiv\.(.+)", doi, re.IGNORECASE):
                add("arxiv", _VERSION_SUFFIX.sub("", arxiv.group(1)), url)
            else:
                add("doi", doi, url)
    return refs


@dataclass
class HarvestResult:
    works: list[WorkRecord]
    candidates: int = 0  # scholarly refs detected in the web results
    resolved: int = 0  # resolved to a canonical OpenAlex work
    unresolved: list[str] = field(default_factory=list)
    resolved_urls: set[str] = field(default_factory=set)  # web urls that became works


def harvest_works(
    sources: list[WebSource],
    client: OpenAlexClient,
    *,
    limit: int = 25,
) -> HarvestResult:
    """Resolve paper links found in web results to canonical works.

    Unresolvable refs are reported honestly instead of being fabricated —
    a work only enters the review if OpenAlex can confirm it exists.
    """
    refs = extract_scholarly_refs(sources)[:limit]
    result = HarvestResult(works=[], candidates=len(refs))
    seen_ids: set[str] = set()
    for ref in refs:
        record = client.get_work(ref.external_id)
        if record is None or not record.id or not record.title:
            result.unresolved.append(ref.external_id)
            continue
        if record.id in seen_ids:
            continue
        seen_ids.add(record.id)
        record.source = "websearch"
        result.works.append(record)
        result.resolved += 1
        result.resolved_urls.add(ref.source_url)
    return result
