"""OpenAlex connector.

Two roles:
1. Slice ingestion for the MicroCorpus (iter_works with a filter).
2. Live freshness layer at run time (search with a compiled boolean string).

Etiquette/auth: sends `mailto` on every request; sends `api_key` when
configured (required by OpenAlex since Feb 2026). Cursor paging per docs.
"""

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from sixsentences_server.core.models import WorkRecord

BASE_URL = "https://api.openalex.org"
PER_PAGE = 200
_MAX_RETRIES = 4  # a 50k ingest is ~250 pages; a single slow page must not kill it
_TRANSIENT_STATUS = (429, 500, 502, 503, 504)
_EXACT_LOOKUP_TIMEOUT_SECONDS = 8.0
# The three id shapes callers actually resolve: a DOI, an arXiv id, or a bare
# OpenAlex id. Everything else is refused before it reaches the request path.
_EXTERNAL_ID = re.compile(
    r"doi:10\.\d{4,9}/[A-Za-z0-9._:;()\[\]<>+*/-]{1,180}"
    r"|arxiv:[A-Za-z0-9./-]{1,40}"
    r"|[A-Za-z]\d{2,18}"
)
_MAX_EXACT_LOOKUP_BYTES = 1_000_000

WORK_FIELDS = (
    "id,doi,title,abstract_inverted_index,publication_year,publication_date,biblio,language,primary_location,"
    "authorships,cited_by_count,is_retracted,referenced_works,type,"
    "open_access,best_oa_location,locations,ids"
)

# arXiv ids: new scheme (2301.12345[v2]) or legacy (hep-th/9901001)
_ARXIV_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})",
    re.IGNORECASE,
)


class OpenAlexError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAlexSearchPage:
    """One durable unit of a cursor-paged live search.

    Exposing pages lets long-running review jobs report progress, evaluate
    saturation and react to cancellation without waiting for the entire
    provider result cap to be downloaded first.
    """

    records: list[WorkRecord]
    page_number: int
    fetched: int
    provider_total: int | None
    next_cursor: str
    has_more: bool


_WILDCARDS = re.compile(r"[*?]")


def sanitize_search_text(text: str) -> str:
    """Make free text safe for the stemmed ``search`` parameter.

    OpenAlex rejects wildcards (* and ?) in stemmed search with HTTP 400, and
    natural-language questions routinely end in a question mark.
    """
    return _WILDCARDS.sub(" ", text).strip()


def _invert_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """Reconstruct abstract text from OpenAlex's inverted index."""
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for token, indexes in inverted.items():
        positions.extend((i, token) for i in indexes)
    positions.sort()
    return " ".join(token for _, token in positions)


def _extract_arxiv_id(*urls: str | None) -> str | None:
    for url in urls:
        if url and (match := _ARXIV_RE.search(url)):
            return match.group(1)
    return None


def _parse_oa(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten OpenAlex open-access metadata into the WorkRecord OA fields.

    OpenAlex ingests Unpaywall, so best_oa_location IS the Unpaywall best link —
    this makes full-text discovery corpus-first: no separate Unpaywall call.
    """
    oa = data.get("open_access") or {}
    best = data.get("best_oa_location") or {}
    ids = data.get("ids") or {}
    locations = data.get("locations") or []
    pdf_url = best.get("pdf_url")
    landing = best.get("landing_page_url")
    # Lever A: if the best OA location has no direct PDF, take one from any other
    # OA location; keep a landing page around for citation_pdf_url resolution.
    if not pdf_url:
        for loc in locations:
            if loc.get("is_oa") and loc.get("pdf_url"):
                pdf_url = loc["pdf_url"]
                landing = landing or loc.get("landing_page_url")
                break
    if not landing:
        for loc in locations:
            if loc.get("is_oa") and loc.get("landing_page_url"):
                landing = loc["landing_page_url"]
                break
    urls: list[str | None] = [pdf_url, landing]
    for loc in locations:
        urls.append(loc.get("pdf_url"))
        urls.append(loc.get("landing_page_url"))
    pmcid = ids.get("pmcid")
    if pmcid:
        pmcid = str(pmcid).rsplit("/", 1)[-1]  # URL form -> bare PMCxxxxxxx
    pmid = ids.get("pmid")
    if pmid:
        pmid = str(pmid).rsplit("/", 1)[-1]
    oa_locations: list[dict[str, str | None]] = []
    for loc in locations:
        if not loc.get("is_oa"):
            continue
        location = {
            "pdf_url": loc.get("pdf_url"),
            "landing_page_url": loc.get("landing_page_url"),
            "license": loc.get("license"),
            "version": loc.get("version"),
        }
        if location not in oa_locations:
            oa_locations.append(location)
    return {
        "oa_status": oa.get("oa_status"),
        "oa_url": oa.get("oa_url") or landing,
        "pdf_url": pdf_url,
        "oa_landing_url": landing,
        "oa_license": best.get("license"),
        "oa_version": best.get("version"),
        "arxiv_id": _extract_arxiv_id(*urls),
        "pmcid": pmcid,
        "pmid": pmid,
        "oa_locations": oa_locations,
    }


def _parse_work(data: dict[str, Any]) -> WorkRecord:
    venue = None
    location = data.get("primary_location") or {}
    if location.get("source"):
        venue = location["source"].get("display_name")
    source = location.get("source") or {}
    biblio = data.get("biblio") or {}
    first_page = biblio.get("first_page")
    last_page = biblio.get("last_page")
    pages = (
        f"{first_page}-{last_page}"
        if first_page and last_page and first_page != last_page
        else first_page or last_page
    )
    issn_values = source.get("issn") or []
    issn = source.get("issn_l") or (issn_values[0] if issn_values else None)
    authors = [
        a["author"]["display_name"]
        for a in data.get("authorships") or []
        if a.get("author", {}).get("display_name")
    ]
    doi = data.get("doi")
    if doi:
        doi = doi.removeprefix("https://doi.org/")
    oa = _parse_oa(data)
    return WorkRecord(
        id=(data.get("id") or "").removeprefix("https://openalex.org/"),
        doi=doi,
        title=data.get("title") or "",
        abstract=_invert_abstract(data.get("abstract_inverted_index")),
        year=data.get("publication_year"),
        venue=venue,
        authors=authors[:20],
        cited_by_count=data.get("cited_by_count") or 0,
        is_retracted=bool(data.get("is_retracted")),
        referenced_works=[
            str(ref).removeprefix("https://openalex.org/")
            for ref in data.get("referenced_works") or []
        ],
        work_type=data.get("type"),
        oa_status=oa["oa_status"],
        oa_url=oa["oa_url"],
        pdf_url=oa["pdf_url"],
        oa_landing_url=oa["oa_landing_url"],
        oa_license=oa["oa_license"],
        oa_version=oa["oa_version"],
        arxiv_id=oa["arxiv_id"],
        pmid=oa["pmid"],
        pmcid=oa["pmcid"],
        publication_date=data.get("publication_date"),
        volume=biblio.get("volume"),
        issue=biblio.get("issue"),
        pages=pages,
        publisher=source.get("host_organization_name"),
        language=data.get("language"),
        issn=issn,
        oa_locations=oa["oa_locations"],
    )


class OpenAlexClient:
    def __init__(
        self,
        *,
        mailto: str = "",
        api_key: str = "",
        http: httpx.Client | None = None,
    ) -> None:
        self.mailto = mailto
        self.api_key = api_key
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=60)

    def _params(self, **extra: str | int) -> dict[str, str | int]:
        params: dict[str, str | int] = dict(extra)
        if self.mailto:
            params["mailto"] = self.mailto
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _get_works(self, params: dict[str, str | int]) -> dict:  # type: ignore[type-arg]
        """GET /works with retry+backoff on timeouts and transient 5xx/429.

        Auth failures (401/403) fail fast — retrying won't help.
        """
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self.http.get("/works", params=params)
                if response.status_code in (401, 403):
                    raise OpenAlexError(
                        "OpenAlex rejected the request (API key required since Feb 2026). "
                        "Set SIX_OPENALEX_API_KEY, or retry later if you relied on the "
                        "transition grace period."
                    )
                if response.status_code in _TRANSIENT_STATUS:
                    last_error = OpenAlexError(f"HTTP {response.status_code}")
                else:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise OpenAlexError(
                            f"OpenAlex rejected the request with HTTP {response.status_code}"
                        ) from exc
                    return response.json()  # type: ignore[no-any-return]
            except httpx.TransportError as exc:  # timeouts, connection resets, ...
                last_error = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(min(2**attempt, 10))
        raise OpenAlexError(f"OpenAlex request failed after {_MAX_RETRIES} attempts: {last_error}")

    def iter_works(
        self,
        oa_filter: str,
        *,
        limit: int,
        sort: str = "cited_by_count:desc",
    ) -> Iterator[WorkRecord]:
        """Cursor-paged iteration over /works for corpus ingestion."""
        cursor = "*"
        fetched = 0
        while fetched < limit and cursor:
            data = self._get_works(
                self._params(
                    filter=oa_filter,
                    select=WORK_FIELDS,
                    sort=sort,
                    per_page=min(PER_PAGE, limit - fetched),
                    cursor=cursor,
                )
            )
            for item in data.get("results", []):
                yield _parse_work(item)
                fetched += 1
                if fetched >= limit:
                    return
            cursor = (data.get("meta") or {}).get("next_cursor") or ""

    def search(
        self,
        query_string: str,
        *,
        limit: int = 200,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[WorkRecord]:
        """Live boolean search (freshness layer), optionally windowed by year."""
        results: list[WorkRecord] = []
        for page in self.iter_search_pages(
            query_string,
            limit=limit,
            year_from=year_from,
            year_to=year_to,
        ):
            results.extend(page.records)
        return results

    def iter_search_pages(
        self,
        query_string: str,
        *,
        limit: int = 200,
        year_from: int | None = None,
        year_to: int | None = None,
        per_page: int = PER_PAGE,
    ) -> Iterator[OpenAlexSearchPage]:
        """Yield live-search results one cursor page at a time.

        ``limit`` is a hard client-side safety ceiling. ``has_more`` remains
        true when that ceiling is reached while OpenAlex still advertises a
        cursor, allowing callers to report an honest truncation instead of
        confusing a configured cap with provider exhaustion.
        """
        safe_query = sanitize_search_text(query_string)
        if not safe_query or limit <= 0:
            return
        filters = []
        if year_from is not None:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to is not None:
            filters.append(f"to_publication_date:{year_to}-12-31")
        page_size = max(1, min(PER_PAGE, per_page, limit))
        cursor = "*"
        fetched = 0
        page_number = 0
        while fetched < limit and cursor:
            extra: dict[str, str | int] = {
                "search": safe_query,
                "select": WORK_FIELDS,
                "per_page": min(page_size, limit - fetched),
                "cursor": cursor,
            }
            if filters:
                extra["filter"] = ",".join(filters)
            data = self._get_works(self._params(**extra))
            items = data.get("results", [])
            if not items:
                break
            remaining = limit - fetched
            records = [_parse_work(item) for item in items[:remaining]]
            fetched += len(records)
            page_number += 1
            meta = data.get("meta") or {}
            next_cursor = meta.get("next_cursor") or ""
            provider_count = meta.get("count")
            provider_total = provider_count if isinstance(provider_count, int) else None
            has_more = bool(next_cursor) and (provider_total is None or fetched < provider_total)
            yield OpenAlexSearchPage(
                records=records,
                page_number=page_number,
                fetched=fetched,
                provider_total=provider_total,
                next_cursor=next_cursor,
                has_more=has_more,
            )
            cursor = next_cursor

    def get_work(self, external_id: str) -> WorkRecord | None:
        """Resolve one work by an external id ("doi:10.x/y" or a bare OpenAlex
        id; arXiv papers resolve via their DataCite DOI 10.48550/arXiv.<id>).
        Returns None when OpenAlex does not know it."""
        # The id is read out of an uploaded PDF's text, so it is attacker-shaped
        # input pasted into the request path: "//host/x" is a protocol-relative
        # URL that would retarget the whole request, "../" walks out of /works/,
        # and "?"/"#" rewrite the query the caller built. Refuse anything that is
        # not a plain DOI or OpenAlex id rather than encoding around it.
        if ".." in external_id:
            return None
        if not _EXTERNAL_ID.fullmatch(external_id):
            return None
        try:
            with self.http.stream(
                "GET",
                f"/works/{external_id}",
                params=self._params(),
                timeout=httpx.Timeout(_EXACT_LOOKUP_TIMEOUT_SECONDS),
            ) as response:
                if response.status_code != 200:
                    return None
                length = response.headers.get("content-length")
                if length and int(length) > _MAX_EXACT_LOOKUP_BYTES:
                    return None
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_EXACT_LOOKUP_BYTES:
                        return None
        except (httpx.HTTPError, ValueError):
            return None
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                return None
            return _parse_work(data)
        except (ValueError, KeyError, AttributeError):
            return None

    def related(self, work_id: str, *, direction: str, limit: int = 8) -> list[WorkRecord]:
        """Walk the citation graph one hop from a work.

        direction "cites": works that cite it (its impact, newest research
        building on it). direction "cited_by": works it cites (its
        foundations / reference list). Highest-cited first.
        """
        oa_filter = f"cites:{work_id}" if direction == "cites" else f"cited_by:{work_id}"
        data = self._get_works(
            self._params(
                filter=oa_filter,
                select=WORK_FIELDS,
                sort="cited_by_count:desc",
                per_page=min(PER_PAGE, limit),
            )
        )
        return [_parse_work(item) for item in data.get("results", [])][:limit]

    def author_works(self, name: str, *, limit: int = 8) -> list[WorkRecord]:
        """The best-known author matching `name`, with their top-cited works.
        Empty when no author matches."""
        try:
            response = self.http.get(
                "/authors",
                params=self._params(search=name, per_page=1),
            )
            response.raise_for_status()
            authors = response.json().get("results", [])
        except (httpx.HTTPError, ValueError):
            return []
        if not authors:
            return []
        author_id = str(authors[0].get("id", "")).rsplit("/", 1)[-1]
        if not author_id:
            return []
        data = self._get_works(
            self._params(
                filter=f"author.id:{author_id}",
                select=WORK_FIELDS,
                sort="cited_by_count:desc",
                per_page=min(PER_PAGE, limit),
            )
        )
        return [_parse_work(item) for item in data.get("results", [])][:limit]
