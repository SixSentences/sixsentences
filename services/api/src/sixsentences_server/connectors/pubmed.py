"""Bounded PubMed retrieval through NCBI's E-utilities.

The connector deliberately implements only the reproducible retrieval path the
review pipeline needs: ESearch turns a PubMed query into PMIDs and EFetch loads
those records in bounded batches.  It follows NCBI's published E-utilities
guidance by sending ``tool`` and ``email`` (when configured), using ``api_key``
when available, limiting EFetch requests to 200 PMIDs and pacing requests
to the documented per-client rate.

Provider responses are untrusted input.  Request counts, response sizes,
timeouts and retries are bounded, XML failures are reported without echoing
queries or response bodies, and credentials never appear in connector errors.
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from sixsentences_server.core.models import WorkRecord

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_SEARCH_LIMIT = 200
MAX_SEARCH_RESULTS = 2_000
MAX_QUERY_CHARACTERS = 8_192
MAX_PMIDS_PER_FETCH = 200
MAX_RESPONSE_BYTES = 16_000_000
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_REQUEST_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 2.0
MAX_AUTHORS = 20

_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_PMID_PATTERN = re.compile(r"[1-9][0-9]{0,11}")
_TOOL_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_CONTACT_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")
_YEAR_PATTERN = re.compile(r"(?<!\d)(1[0-9]{3}|2[0-9]{3}|3000)(?!\d)")
_WHITESPACE = re.compile(r"\s+")
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


class PubMedError(RuntimeError):
    """A safe, credential-free PubMed connector failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _SharedLimiter(Protocol):
    """Minimal distributed limiter contract used by the request gate."""

    def allow(self, key: str) -> bool: ...


class PubMedRequestRateGate:
    """Block until one deployment-wide NCBI request slot is available.

    The backing limiter is supplied by the product runtime so the connector
    remains independently testable. Production uses the database-backed
    limiter, shared by every research worker and API replica.
    """

    def __init__(
        self,
        limiter: _SharedLimiter,
        *,
        window_seconds: float,
        unavailable_error: type[Exception],
        max_wait_seconds: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if window_seconds <= 0 or max_wait_seconds <= 0:
            raise ValueError("PubMed shared rate-gate bounds must be positive")
        self._limiter = limiter
        self._window_seconds = window_seconds
        self._unavailable_error = unavailable_error
        self._max_wait_seconds = max_wait_seconds
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock

    def acquire(self) -> None:
        """Consume one shared slot, failing closed when coordination breaks."""

        deadline = self._monotonic_clock() + self._max_wait_seconds
        while True:
            try:
                if self._limiter.allow("deployment"):
                    return
            except self._unavailable_error:
                raise PubMedError("PubMed request coordination is unavailable") from None
            wall_now = self._wall_clock()
            remaining = self._window_seconds - (wall_now % self._window_seconds)
            wait = max(0.001, remaining + 0.001)
            if self._monotonic_clock() + wait > deadline:
                raise PubMedError("PubMed request coordination exceeded the wait limit")
            self._sleep(wait)


@dataclass(frozen=True)
class PubMedSearchResult:
    """Normalized records and ESearch cardinality for one bounded search."""

    records: list[WorkRecord]
    provider_total: int
    pmids_returned: int
    truncated: bool


@dataclass(frozen=True)
class _PubMedIdSearchResult:
    pmids: list[str]
    provider_total: int
    truncated: bool


def _element_text(element: ET.Element | None) -> str:
    """Return normalized mixed XML content from one element."""

    if element is None:
        return ""
    return _WHITESPACE.sub(" ", "".join(element.itertext())).strip()


def _first_text(parent: ET.Element, *paths: str) -> str:
    for path in paths:
        value = _element_text(parent.find(path))
        if value:
            return value
    return ""


def _normalize_doi(value: str) -> str | None:
    normalized = _WHITESPACE.sub("", value).strip()
    normalized = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", normalized, flags=re.I)
    return normalized.casefold() or None


def _normalize_pmcid(value: str) -> str | None:
    normalized = value.strip().upper()
    if not normalized:
        return None
    if normalized.isdigit():
        normalized = f"PMC{normalized}"
    return normalized if re.fullmatch(r"PMC[0-9]+", normalized) else None


def _year_from_text(value: str) -> int | None:
    match = _YEAR_PATTERN.search(value)
    return int(match.group(1)) if match else None


def _date_from_elements(
    date_elements: Iterable[ET.Element | None],
    *,
    fallback_text: str = "",
) -> tuple[int | None, str | None]:
    for date in date_elements:
        if date is None:
            continue
        year_value = _first_text(date, "./Year")
        medline_date = _first_text(date, "./MedlineDate")
        year = _year_from_text(year_value or medline_date)
        if year is None:
            continue
        month_value = _first_text(date, "./Month")
        day_value = _first_text(date, "./Day")
        month: int | None = None
        if month_value.isdigit() and 1 <= int(month_value) <= 12:
            month = int(month_value)
        elif month_value:
            month = _MONTHS.get(month_value.casefold().rstrip("."))
        day = int(day_value) if day_value.isdigit() and 1 <= int(day_value) <= 31 else None
        if month is None:
            return year, str(year)
        if day is None:
            return year, f"{year:04d}-{month:02d}"
        return year, f"{year:04d}-{month:02d}-{day:02d}"

    fallback_year = _year_from_text(fallback_text)
    return fallback_year, str(fallback_year) if fallback_year is not None else None


def _publication_date(article: ET.Element) -> tuple[int | None, str | None]:
    date_elements = [
        article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate"),
        article.find("./MedlineCitation/Article/ArticleDate"),
    ]
    history = article.findall("./PubmedData/History/PubMedPubDate")
    date_elements.extend(
        date for date in history if date.attrib.get("PubStatus", "").casefold() == "pubmed"
    )
    fallback_text = _first_text(
        article,
        "./MedlineCitation/DateCompleted/Year",
        "./MedlineCitation/DateRevised/Year",
    )
    return _date_from_elements(date_elements, fallback_text=fallback_text)


def _book_publication_date(article: ET.Element) -> tuple[int | None, str | None]:
    date_elements = [
        article.find("./BookDocument/Book/PubDate"),
        article.find("./BookDocument/ContributionDate"),
    ]
    history = article.findall("./PubmedBookData/History/PubMedPubDate")
    date_elements.extend(
        date for date in history if date.attrib.get("PubStatus", "").casefold() == "pubmed"
    )
    fallback_text = _first_text(article, "./BookDocument/DateRevised/Year")
    return _date_from_elements(date_elements, fallback_text=fallback_text)


def _author_names(authors: Iterable[ET.Element]) -> list[str]:
    values: list[str] = []
    for author in authors:
        collective = _first_text(author, "./CollectiveName")
        if collective:
            values.append(collective)
            continue
        last_name = _first_text(author, "./LastName")
        fore_name = _first_text(author, "./ForeName", "./Initials")
        suffix = _first_text(author, "./Suffix")
        name = " ".join(part for part in (fore_name, last_name, suffix) if part)
        if name:
            values.append(name)
        if len(values) >= MAX_AUTHORS:
            break
    return values


def _authors(article: ET.Element) -> list[str]:
    return _author_names(article.findall("./MedlineCitation/Article/AuthorList/Author"))


def _book_authors(article: ET.Element) -> list[str]:
    document = article.find("./BookDocument")
    if document is None:
        return []
    author_elements: list[ET.Element] = []
    for author_list in document.findall("./AuthorList"):
        if author_list.attrib.get("Type", "authors").casefold() == "authors":
            author_elements.extend(author_list.findall("./Author"))
    if not author_elements:
        for author_list in document.findall("./Book/AuthorList"):
            if author_list.attrib.get("Type", "authors").casefold() == "authors":
                author_elements.extend(author_list.findall("./Author"))
    return _author_names(author_elements)


def _abstract_from_sections(sections: Iterable[ET.Element]) -> str | None:
    values: list[str] = []
    for section in sections:
        text = _element_text(section)
        if not text:
            continue
        label = _WHITESPACE.sub(" ", section.attrib.get("Label", "")).strip()
        if label and not text.casefold().startswith(f"{label.casefold()}:"):
            text = f"{label}: {text}"
        values.append(text)
    return " ".join(values) or None


def _abstract(article: ET.Element) -> str | None:
    sections = article.findall("./MedlineCitation/Article/Abstract/AbstractText")
    if not sections:
        sections = article.findall("./MedlineCitation/OtherAbstract/AbstractText")
    return _abstract_from_sections(sections)


def _book_abstract(article: ET.Element) -> str | None:
    return _abstract_from_sections(article.findall("./BookDocument/Abstract/AbstractText"))


def _article_ids(article: ET.Element) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for element in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        id_type = element.attrib.get("IdType", "").casefold()
        value = _element_text(element)
        if id_type and value and id_type not in identifiers:
            identifiers[id_type] = value
    return identifiers


def _book_article_ids(article: ET.Element) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    paths = (
        "./PubmedBookData/ArticleIdList/ArticleId",
        "./BookDocument/ArticleIdList/ArticleId",
    )
    for path in paths:
        for element in article.findall(path):
            id_type = element.attrib.get("IdType", "").casefold()
            value = _element_text(element)
            if id_type and value and id_type not in identifiers:
                identifiers[id_type] = value
    return identifiers


def _work_type_from_elements(
    publication_type_elements: Iterable[ET.Element],
) -> tuple[str | None, bool]:
    publication_types = [_element_text(element) for element in publication_type_elements]
    lowered = {value.casefold() for value in publication_types if value}
    is_retracted = "retracted publication" in lowered
    priority = (
        ("systematic review", "systematic-review"),
        ("meta-analysis", "meta-analysis"),
        ("review", "review"),
        ("book chapter", "book-chapter"),
        ("book", "book"),
        ("preprint", "preprint"),
        ("editorial", "editorial"),
        ("letter", "letter"),
        ("published erratum", "erratum"),
        ("journal article", "article"),
    )
    for pubmed_value, canonical_value in priority:
        if pubmed_value in lowered:
            return canonical_value, is_retracted
    if not publication_types:
        return None, is_retracted
    return publication_types[0].strip().casefold().replace(" ", "-"), is_retracted


def _work_type(article: ET.Element) -> tuple[str | None, bool]:
    return _work_type_from_elements(
        article.findall("./MedlineCitation/Article/PublicationTypeList/PublicationType")
    )


def _book_work_type(article: ET.Element) -> tuple[str | None, bool]:
    return _work_type_from_elements(article.findall("./BookDocument/PublicationType"))


def _pagination(parent: ET.Element, path: str) -> str | None:
    pagination = parent.find(path)
    if pagination is None:
        return None
    medline_pages = _first_text(pagination, "./MedlinePgn")
    if medline_pages:
        return medline_pages
    start_page = _first_text(pagination, "./StartPage")
    end_page = _first_text(pagination, "./EndPage")
    if start_page and end_page:
        return f"{start_page}-{end_page}"
    return start_page or end_page or None


def _parse_article(article: ET.Element) -> WorkRecord:
    identifiers = _article_ids(article)
    pmid = _first_text(article, "./MedlineCitation/PMID") or identifiers.get("pubmed", "")
    if not _PMID_PATTERN.fullmatch(pmid):
        raise PubMedError("PubMed EFetch returned a record without a valid PMID")

    doi = _normalize_doi(identifiers.get("doi", ""))
    if doi is None:
        for location in article.findall("./MedlineCitation/Article/ELocationID"):
            if location.attrib.get("EIdType", "").casefold() == "doi":
                doi = _normalize_doi(_element_text(location))
                if doi:
                    break
    pmcid = _normalize_pmcid(identifiers.get("pmc", "") or identifiers.get("pmcid", ""))
    year, publication_date = _publication_date(article)
    work_type, is_retracted = _work_type(article)
    venue = _first_text(
        article,
        "./MedlineCitation/Article/Journal/Title",
        "./MedlineCitation/MedlineJournalInfo/MedlineTA",
    )
    pages = _pagination(article, "./MedlineCitation/Article/Pagination")
    return WorkRecord(
        id=f"pubmed:{pmid}",
        doi=doi,
        title=_first_text(article, "./MedlineCitation/Article/ArticleTitle"),
        abstract=_abstract(article),
        year=year,
        venue=venue or None,
        authors=_authors(article),
        is_retracted=is_retracted,
        work_type=work_type,
        source="pubmed",
        pmid=pmid,
        pmcid=pmcid,
        publication_date=publication_date,
        volume=_first_text(
            article,
            "./MedlineCitation/Article/Journal/JournalIssue/Volume",
        )
        or None,
        issue=_first_text(
            article,
            "./MedlineCitation/Article/Journal/JournalIssue/Issue",
        )
        or None,
        pages=pages,
        language=_first_text(article, "./MedlineCitation/Article/Language").casefold() or None,
        issn=_first_text(article, "./MedlineCitation/Article/Journal/ISSN") or None,
    )


def _parse_book_article(article: ET.Element) -> WorkRecord:
    identifiers = _book_article_ids(article)
    pmid = _first_text(article, "./BookDocument/PMID") or identifiers.get("pubmed", "")
    if not _PMID_PATTERN.fullmatch(pmid):
        raise PubMedError("PubMed EFetch returned a record without a valid PMID")

    doi = _normalize_doi(identifiers.get("doi", ""))
    if doi is None:
        for location in article.findall("./BookDocument/Book/ELocationID"):
            if location.attrib.get("EIdType", "").casefold() == "doi":
                doi = _normalize_doi(_element_text(location))
                if doi:
                    break
    pmcid = _normalize_pmcid(identifiers.get("pmc", "") or identifiers.get("pmcid", ""))
    year, publication_date = _book_publication_date(article)
    work_type, is_retracted = _book_work_type(article)
    return WorkRecord(
        id=f"pubmed:{pmid}",
        doi=doi,
        title=_first_text(
            article,
            "./BookDocument/ArticleTitle",
            "./BookDocument/Book/BookTitle",
        ),
        abstract=_book_abstract(article),
        year=year,
        venue=_first_text(article, "./BookDocument/Book/BookTitle") or None,
        authors=_book_authors(article),
        is_retracted=is_retracted,
        work_type=work_type,
        source="pubmed",
        pmid=pmid,
        pmcid=pmcid,
        publication_date=publication_date,
        volume=_first_text(article, "./BookDocument/Book/Volume") or None,
        pages=_pagination(article, "./BookDocument/Pagination"),
        publisher=_first_text(
            article,
            "./BookDocument/Book/Publisher/PublisherName",
        )
        or None,
        language=_first_text(article, "./BookDocument/Language").casefold() or None,
    )


class PubMedClient:
    """Strict, bounded synchronous client for PubMed ESearch and EFetch."""

    def __init__(
        self,
        *,
        email: str = "",
        mailto: str | None = None,
        api_key: str = "",
        tool: str = "sixsentences",
        http: httpx.Client | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        request_rate_gate: PubMedRequestRateGate | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        normalized_tool = tool.strip()
        if email and mailto and email.strip() != mailto.strip():
            raise ValueError("PubMed email and mailto alias must match")
        normalized_email = (email or mailto or "").strip()
        normalized_api_key = api_key.strip()
        if not _TOOL_PATTERN.fullmatch(normalized_tool):
            raise ValueError("PubMed tool must be 1-64 characters without spaces")
        if normalized_email and _CONTACT_EMAIL_PATTERN.fullmatch(normalized_email) is None:
            raise ValueError("PubMed email must be a valid contact address")
        if any(character.isspace() for character in normalized_api_key):
            raise ValueError("PubMed API key must not contain spaces")
        if not 0 < timeout_seconds <= MAX_REQUEST_TIMEOUT_SECONDS:
            raise ValueError(
                f"PubMed timeout must be greater than zero and at most "
                f"{MAX_REQUEST_TIMEOUT_SECONDS:g} seconds"
            )
        self.email = normalized_email
        # ``mailto`` is kept as a read-only compatibility attribute; the
        # official E-utilities wire parameter is named ``email``.
        self.mailto = normalized_email
        self.api_key = normalized_api_key
        self.tool = normalized_tool
        self._owns_http = http is None
        self.http = http or httpx.Client()
        self._timeout = httpx.Timeout(timeout_seconds)
        self._request_rate_gate = request_rate_gate
        self._sleep = sleep
        self._clock = clock
        self._request_interval = 0.1 if self.api_key else 1.0 / 3.0
        self._last_request_started: float | None = None
        self._rate_lock = threading.Lock()

    def close(self) -> None:
        """Release the internally created HTTP pool.

        An injected client remains caller-owned, which keeps test transports
        and shared application clients usable after this connector closes.
        """

        if self._owns_http:
            self.http.close()

    def _common_params(self) -> dict[str, str]:
        params = {"db": "pubmed", "retmode": "xml", "tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _pace_request(self) -> None:
        """Keep one client within NCBI's 3/s or API-key 10/s policy."""

        with self._rate_lock:
            if self._request_rate_gate is not None:
                self._request_rate_gate.acquire()
            now = self._clock()
            if self._last_request_started is not None:
                wait = self._request_interval - (now - self._last_request_started)
                if wait > 0:
                    self._sleep(wait)
                    now = self._clock()
            self._last_request_started = now

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(MAX_RETRY_DELAY_SECONDS, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(MAX_RETRY_DELAY_SECONDS, 0.25 * float(1 << attempt))

    @staticmethod
    def _read_response(response: httpx.Response, operation: str) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_RESPONSE_BYTES:
                    raise PubMedError(f"PubMed {operation} response exceeded the size limit")
            except ValueError:
                pass
        payload = bytearray()
        for chunk in response.iter_bytes():
            payload.extend(chunk)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise PubMedError(f"PubMed {operation} response exceeded the size limit")
        return bytes(payload)

    def _request_xml(
        self,
        endpoint: str,
        *,
        params: dict[str, str | int],
        operation: str,
    ) -> ET.Element:
        url = f"{BASE_URL}/{endpoint}"
        last_retry_after: str | None = None
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            self._pace_request()
            try:
                # Always send E-utilities parameters in a form body. Besides
                # handling long queries, this keeps the research query,
                # operator contact and optional API key out of access-log,
                # proxy and tracing URLs.
                response_context = self.http.stream(
                    "POST",
                    url,
                    data=params,
                    timeout=self._timeout,
                )
                with response_context as response:
                    status_code = response.status_code
                    if status_code in _TRANSIENT_STATUS_CODES:
                        last_retry_after = response.headers.get("retry-after")
                    elif not 200 <= status_code < 300:
                        raise PubMedError(
                            f"PubMed {operation} rejected the request with HTTP {status_code}",
                            status_code=status_code,
                        )
                    else:
                        payload = self._read_response(response, operation)
                        try:
                            root = ET.fromstring(payload)
                        except ET.ParseError:
                            raise PubMedError(
                                f"PubMed {operation} returned malformed XML"
                            ) from None
                        if root.find(".//ERROR") is not None:
                            raise PubMedError(f"PubMed {operation} rejected the request")
                        return root
            except httpx.TransportError:
                last_retry_after = None
            if attempt < MAX_REQUEST_ATTEMPTS - 1:
                self._sleep(self._retry_delay(attempt, last_retry_after))
        raise PubMedError(
            f"PubMed {operation} failed after {MAX_REQUEST_ATTEMPTS} transient attempts"
        ) from None

    @staticmethod
    def _validate_search(query: str, limit: int) -> str:
        normalized = query.strip()
        if limit < 0:
            raise ValueError("PubMed search limit must not be negative")
        if limit > MAX_SEARCH_RESULTS:
            raise ValueError(f"PubMed search limit must not exceed {MAX_SEARCH_RESULTS}")
        if len(normalized) > MAX_QUERY_CHARACTERS:
            raise ValueError(f"PubMed query must not exceed {MAX_QUERY_CHARACTERS} characters")
        return normalized

    @staticmethod
    def effective_query(
        query: str,
        *,
        year_from: int | None,
        year_to: int | None,
    ) -> str:
        """Return the exact ESearch term, including the publication window."""

        normalized = query.strip()
        for value in (year_from, year_to):
            if value is not None and not 1000 <= value <= 3000:
                raise ValueError("PubMed publication years must be between 1000 and 3000")
        if year_from is not None and year_to is not None and year_from > year_to:
            raise ValueError("PubMed year_from must not exceed year_to")
        if year_from is None and year_to is None:
            return normalized
        lower = year_from if year_from is not None else 1000
        upper = year_to if year_to is not None else 3000
        return f"({normalized}) AND {lower}:{upper}[pdat]"

    def _search_pmids_with_metadata(
        self,
        query: str,
        *,
        limit: int,
        year_from: int | None,
        year_to: int | None,
    ) -> _PubMedIdSearchResult:
        normalized = self._validate_search(query, limit)
        if not normalized or limit == 0:
            return _PubMedIdSearchResult(pmids=[], provider_total=0, truncated=False)
        effective_query = self.effective_query(
            normalized,
            year_from=year_from,
            year_to=year_to,
        )
        if len(effective_query) > MAX_QUERY_CHARACTERS:
            raise ValueError(
                f"PubMed query with limits must not exceed {MAX_QUERY_CHARACTERS} characters"
            )
        params: dict[str, str | int] = {
            **self._common_params(),
            "term": effective_query,
            "retstart": 0,
            "retmax": limit,
            "sort": "relevance",
        }
        root = self._request_xml(
            "esearch.fcgi",
            params=params,
            operation="ESearch",
        )
        count_text = _first_text(root, "./Count")
        if not count_text.isdigit():
            raise PubMedError("PubMed ESearch returned an invalid result count")
        provider_total = int(count_text)
        pmids: list[str] = []
        seen: set[str] = set()
        for element in root.findall("./IdList/Id"):
            pmid = _element_text(element)
            if not _PMID_PATTERN.fullmatch(pmid):
                raise PubMedError("PubMed ESearch returned an invalid PMID")
            if pmid not in seen:
                seen.add(pmid)
                pmids.append(pmid)
            if len(pmids) >= limit:
                break
        return _PubMedIdSearchResult(
            pmids=pmids,
            provider_total=provider_total,
            truncated=provider_total > len(pmids),
        )

    def search_pmids(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[str]:
        """Return PMIDs in deterministic ESearch order within the hard cap."""

        result = self._search_pmids_with_metadata(
            query,
            limit=limit,
            year_from=year_from,
            year_to=year_to,
        )
        return result.pmids

    @staticmethod
    def _validated_pmids(pmids: Iterable[str]) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for raw_pmid in pmids:
            pmid = str(raw_pmid).strip()
            if not _PMID_PATTERN.fullmatch(pmid):
                raise ValueError("PubMed PMID values must be positive decimal identifiers")
            if pmid not in seen:
                seen.add(pmid)
                values.append(pmid)
            if len(values) > MAX_SEARCH_RESULTS:
                raise ValueError(f"PubMed fetch must not exceed {MAX_SEARCH_RESULTS} PMIDs")
        return values

    def fetch_records(self, pmids: Sequence[str] | Iterable[str]) -> list[WorkRecord]:
        """Fetch and normalize PMIDs in batches, preserving caller order."""

        ordered_pmids = self._validated_pmids(pmids)
        if not ordered_pmids:
            return []
        records_by_pmid: dict[str, WorkRecord] = {}
        for offset in range(0, len(ordered_pmids), MAX_PMIDS_PER_FETCH):
            batch = ordered_pmids[offset : offset + MAX_PMIDS_PER_FETCH]
            params: dict[str, str | int] = {
                **self._common_params(),
                "id": ",".join(batch),
                "rettype": "abstract",
            }
            root = self._request_xml(
                "efetch.fcgi",
                params=params,
                operation="EFetch",
            )
            batch_records: dict[str, WorkRecord] = {}
            for article in root:
                if article.tag == "PubmedArticle":
                    record = _parse_article(article)
                elif article.tag == "PubmedBookArticle":
                    record = _parse_book_article(article)
                else:
                    continue
                if record.pmid not in batch or record.pmid in batch_records:
                    raise PubMedError("PubMed EFetch returned an unexpected record set")
                batch_records[record.pmid] = record
            if set(batch_records) != set(batch):
                raise PubMedError("PubMed EFetch returned an incomplete record set")
            records_by_pmid.update(batch_records)
        return [records_by_pmid[pmid] for pmid in ordered_pmids if pmid in records_by_pmid]

    def search_with_metadata(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> PubMedSearchResult:
        """Search PubMed and return records plus provider count/truncation."""

        id_result = self._search_pmids_with_metadata(
            query,
            limit=limit,
            year_from=year_from,
            year_to=year_to,
        )
        records = self.fetch_records(id_result.pmids)
        return PubMedSearchResult(
            records=records,
            provider_total=id_result.provider_total,
            pmids_returned=len(id_result.pmids),
            truncated=id_result.truncated,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[WorkRecord]:
        """Search PubMed and return normalized records in ESearch order."""

        return self.search_with_metadata(
            query,
            limit=limit,
            year_from=year_from,
            year_to=year_to,
        ).records
