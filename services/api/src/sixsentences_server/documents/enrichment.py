"""Strong-identity metadata enrichment for one Library paper.

The enrichment path intentionally does not perform a title search.  A provider
response must round-trip the DOI, arXiv, PMID, PMCID or OpenAlex identifier that
was already attached to the exact Library component before any field can be
proposed.  OpenAlex is the existing scholarly metadata provider and includes
Unpaywall's public open-access locations; this module never downloads a paper
or attempts to cross a paywall.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from sixsentences_server.connectors.openalex import OpenAlexClient
from sixsentences_server.core.models import WorkRecord


class PaperEnrichmentError(RuntimeError):
    """A safe, user-actionable enrichment failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StrongPaperIdentity:
    """A stable provider identifier already present on the Library paper."""

    kind: str
    value: str
    provider_id: str


_FIELD_TEXT_LIMITS = {
    "title": 500,
    "abstract": 20_000,
    "published_at": 40,
    "container_title": 500,
    "volume": 100,
    "issue": 100,
    "pages": 100,
    "publisher": 300,
    "language": 80,
    "license": 200,
    "issn": 100,
    "arxiv_id": 80,
    "item_type": 80,
    "pmid": 80,
    "pmcid": 80,
}
_URL_FIELDS = {"source_url", "canonical_url", "pdf_url"}
_MAX_PROVIDER_RESPONSE_BYTES = 1_000_000
_PLACEHOLDER_TITLES = {
    "captured paper",
    "captured paper citation",
    "untitled",
    "untitled paper",
    "untitled source",
    "uploaded document",
}


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _safe_public_url(value: object) -> str:
    raw = str(value or "").strip()[:4000]
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").rstrip(".").casefold()
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return ""
    except ValueError:
        return ""
    return raw


def _bounded_candidate(field: str, value: Any) -> Any:
    if field == "authors":
        authors: list[str] = []
        seen: set[str] = set()
        for raw_author in list(value or [])[:100]:
            author = _bounded_text(raw_author, 300)
            if author and author.casefold() not in seen:
                authors.append(author)
                seen.add(author.casefold())
        return authors
    if field in _URL_FIELDS:
        return _safe_public_url(value)
    if field == "doi":
        return normalize_doi(value)
    return _bounded_text(value, _FIELD_TEXT_LIMITS.get(field, 500))


def metadata_value_is_missing(field: str, value: Any) -> bool:
    """Treat known system placeholders as missing, never real user content."""

    if value in (None, "", []):
        return True
    if field != "title" or not isinstance(value, str):
        return False
    normalized = " ".join(value.split()).strip().casefold()
    return normalized in _PLACEHOLDER_TITLES or re.fullmatch(r"w\d{6,}", normalized) is not None


class CrossrefClient:
    """One exact-DOI call to Crossref's official public REST API."""

    def __init__(
        self,
        *,
        mailto: str = "",
        http: httpx.Client | None = None,
    ) -> None:
        self.mailto = mailto
        self.http = http or httpx.Client(
            base_url="https://api.crossref.org",
            timeout=httpx.Timeout(8.0),
            headers={
                "User-Agent": ("SixSentences/1.0" + (f" (mailto:{mailto})" if mailto else ""))
            },
        )

    def get_work(self, doi: str) -> dict[str, Any] | None:
        normalized = normalize_doi(doi)
        if not normalized:
            return None
        try:
            with self.http.stream(
                "GET",
                f"/works/{quote(normalized, safe='')}",
            ) as response:
                if response.status_code != 200:
                    return None
                length = response.headers.get("content-length")
                if length and int(length) > _MAX_PROVIDER_RESPONSE_BYTES:
                    return None
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_PROVIDER_RESPONSE_BYTES:
                        return None
        except (httpx.HTTPError, ValueError):
            return None
        try:
            message = json.loads(payload).get("message") or {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(message, dict) or normalize_doi(message.get("DOI")) != normalized:
            return None
        return message


def normalize_doi(value: object) -> str:
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"^doi\s*:\s*", "", normalized)
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = normalized.rstrip(".,;:")
    if re.fullmatch(r"10\.\d{4,9}/\S+", normalized) is None:
        return ""
    arxiv = re.fullmatch(r"10\.48550/arxiv\.([^\s]+)", normalized, re.I)
    if arxiv:
        return "10.48550/arxiv." + re.sub(r"v\d+$", "", arxiv.group(1), flags=re.I).lower()
    return normalized


def normalize_arxiv(value: object) -> str:
    normalized = str(value or "").strip()
    normalized = re.sub(
        r"^(?:arxiv\s*:\s*|https?://arxiv\.org/(?:abs|pdf)/)",
        "",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"\.pdf$", "", normalized, flags=re.I)
    normalized = re.sub(r"v\d+$", "", normalized, flags=re.I)
    if (
        re.fullmatch(
            r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})",
            normalized,
            re.I,
        )
        is None
    ):
        return ""
    return normalized.lower()


def normalize_pmid(value: object) -> str:
    normalized = re.sub(r"^(?:pmid\s*:\s*)", "", str(value or "").strip(), flags=re.I)
    return normalized if re.fullmatch(r"\d{1,12}", normalized) else ""


def normalize_pmcid(value: object) -> str:
    normalized = re.sub(r"^(?:pmcid\s*:\s*)", "", str(value or "").strip(), flags=re.I)
    normalized = normalized.upper()
    if re.fullmatch(r"PMC\d{1,12}", normalized):
        return normalized
    if re.fullmatch(r"\d{1,12}", normalized):
        return f"PMC{normalized}"
    return ""


def strong_identity(
    metadata: Mapping[str, Any],
    *,
    work_id: str,
) -> StrongPaperIdentity | None:
    """Choose the strongest available id, never a title or author heuristic."""

    doi = normalize_doi(metadata.get("doi"))
    if doi:
        return StrongPaperIdentity("doi", doi, f"doi:{doi}")
    arxiv_id = normalize_arxiv(metadata.get("arxiv_id"))
    if arxiv_id:
        return StrongPaperIdentity("arxiv", arxiv_id, f"doi:10.48550/arXiv.{arxiv_id}")
    pmid = normalize_pmid(metadata.get("pmid"))
    if pmid:
        return StrongPaperIdentity("pmid", pmid, f"pmid:{pmid}")
    pmcid = normalize_pmcid(metadata.get("pmcid"))
    if pmcid:
        return StrongPaperIdentity("pmcid", pmcid, f"pmcid:{pmcid}")
    if re.fullmatch(r"W[1-9]\d+", work_id):
        return StrongPaperIdentity("openalex", work_id, work_id)
    return None


def _identity_matches(identity: StrongPaperIdentity, work: WorkRecord) -> bool:
    if identity.kind == "doi":
        return normalize_doi(work.doi) == identity.value
    if identity.kind == "arxiv":
        return (
            normalize_arxiv(work.arxiv_id) == identity.value
            or normalize_doi(work.doi) == f"10.48550/arxiv.{identity.value}"
        )
    if identity.kind == "pmid":
        return normalize_pmid(work.pmid) == identity.value
    if identity.kind == "pmcid":
        return normalize_pmcid(work.pmcid) == identity.value
    return work.id.casefold() == identity.value.casefold()


def _canonical_url(identity: StrongPaperIdentity, work: WorkRecord) -> str:
    if identity.kind == "doi":
        return f"https://doi.org/{identity.value}"
    if identity.kind == "arxiv":
        return f"https://arxiv.org/abs/{identity.value}"
    if identity.kind == "pmid":
        return f"https://pubmed.ncbi.nlm.nih.gov/{identity.value}/"
    if identity.kind == "pmcid":
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{identity.value}/"
    return f"https://openalex.org/{work.id}"


def _candidate_metadata(
    identity: StrongPaperIdentity,
    work: WorkRecord,
) -> dict[str, Any]:
    canonical = _canonical_url(identity, work)
    values: dict[str, Any] = {
        "title": work.title or None,
        "authors": list(work.authors or []),
        "abstract": work.abstract,
        "published_at": work.publication_date or (str(work.year) if work.year else None),
        "doi": normalize_doi(work.doi) or None,
        "source_url": work.oa_landing_url or work.oa_url or canonical,
        "canonical_url": canonical,
        "pdf_url": work.pdf_url if work.oa_status != "closed" else None,
        "container_title": work.venue,
        "volume": work.volume,
        "issue": work.issue,
        "pages": work.pages,
        "publisher": work.publisher,
        "language": work.language,
        "license": work.oa_license,
        "issn": work.issn,
        "arxiv_id": normalize_arxiv(work.arxiv_id) or None,
        "item_type": work.work_type,
        "pmid": normalize_pmid(work.pmid) or None,
        "pmcid": normalize_pmcid(work.pmcid) or None,
    }
    bounded = {field: _bounded_candidate(field, value) for field, value in values.items()}
    return {field: value for field, value in bounded.items() if value not in (None, "", [])}


def _crossref_date(message: Mapping[str, Any]) -> str | None:
    for key in ("published-print", "published-online", "issued", "created"):
        value = message.get(key)
        if not isinstance(value, Mapping):
            continue
        parts = value.get("date-parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        numbers = [int(item) for item in parts[0][:3] if isinstance(item, int)]
        if numbers:
            return "-".join([str(numbers[0]), *[f"{number:02d}" for number in numbers[1:]]])
    return None


def _crossref_candidates(message: Mapping[str, Any]) -> dict[str, Any]:
    def first_list(field: str) -> str | None:
        value = message.get(field)
        return str(value[0]) if isinstance(value, list) and value else None

    authors: list[str] = []
    for raw_author in list(message.get("author") or [])[:100]:
        if not isinstance(raw_author, Mapping):
            continue
        name = " ".join(
            str(raw_author.get(part) or "").strip() for part in ("given", "family")
        ).strip()
        if name:
            authors.append(name)
    abstract = message.get("abstract")
    if abstract:
        abstract = unescape(re.sub(r"<[^>]+>", " ", str(abstract)))
    license_items = message.get("license") or []
    license_url = None
    if isinstance(license_items, list) and license_items:
        first_license = license_items[0]
        if isinstance(first_license, Mapping):
            license_url = first_license.get("URL")
    values: dict[str, Any] = {
        "title": first_list("title"),
        "subtitle": first_list("subtitle"),
        "authors": authors,
        "abstract": abstract,
        "published_at": _crossref_date(message),
        "doi": normalize_doi(message.get("DOI")),
        "source_url": message.get("URL"),
        "canonical_url": (
            f"https://doi.org/{normalize_doi(message.get('DOI'))}"
            if normalize_doi(message.get("DOI"))
            else None
        ),
        "container_title": first_list("container-title"),
        "volume": message.get("volume"),
        "issue": message.get("issue"),
        "pages": message.get("page") or message.get("article-number"),
        "publisher": message.get("publisher"),
        "language": message.get("language"),
        "license": license_url,
        "issn": first_list("ISSN"),
        "item_type": message.get("type"),
    }
    bounded = {field: _bounded_candidate(field, value) for field, value in values.items()}
    return {field: value for field, value in bounded.items() if value not in (None, "", [])}


def discover_missing_metadata(
    *,
    client: OpenAlexClient,
    crossref_client: CrossrefClient | None = None,
    identity: StrongPaperIdentity,
    current_metadata: Mapping[str, Any],
    protected_fields: set[str],
    max_suggestions: int = 32,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve and return bounded, source-attributed fill-only proposals."""

    provider_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if identity.kind == "doi" and crossref_client is not None:
        crossref = crossref_client.get_work(identity.value)
        if crossref is not None:
            crossref_source = {
                "provider": "crossref",
                "url": f"https://api.crossref.org/works/{quote(identity.value, safe='')}",
                "matched_identifier": {
                    "kind": identity.kind,
                    "value": identity.value[:500],
                },
            }
            provider_candidates.append((_crossref_candidates(crossref), crossref_source))
    work = client.get_work(identity.provider_id)
    if work is not None and not _identity_matches(identity, work):
        raise PaperEnrichmentError(
            "paper_enrichment_identity_mismatch",
            "The metadata provider returned a different paper, so nothing was proposed.",
        )
    if work is not None:
        safe_work_id = re.fullmatch(r"W[1-9]\d+", work.id)
        if safe_work_id is None:
            raise PaperEnrichmentError(
                "paper_enrichment_identity_mismatch",
                "The metadata provider returned an invalid paper identifier.",
            )
        source = {
            "provider": "openalex",
            "url": f"https://openalex.org/{work.id}",
            "matched_identifier": {
                "kind": identity.kind,
                "value": identity.value[:500],
            },
        }
        provider_candidates.append((_candidate_metadata(identity, work), source))
    if not provider_candidates:
        raise PaperEnrichmentError(
            "paper_enrichment_not_accessible",
            "No publicly accessible metadata record was found for this identifier.",
        )
    suggestions: list[dict[str, Any]] = []
    proposed_fields: set[str] = set()
    for candidates, source in provider_candidates:
        for field, value in candidates.items():
            if (
                field in proposed_fields
                or field in protected_fields
                or not metadata_value_is_missing(field, current_metadata.get(field))
            ):
                continue
            suggestions.append(
                {
                    "field": field,
                    "current": None,
                    "value": value,
                    "confidence": 0.99,
                    "source": source,
                }
            )
            proposed_fields.add(field)
            if len(suggestions) >= max_suggestions:
                break
        if len(suggestions) >= max_suggestions:
            break
    sources = [source for _, source in provider_candidates[:2]]
    return suggestions, sources
