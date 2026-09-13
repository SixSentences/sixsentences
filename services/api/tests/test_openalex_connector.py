"""OpenAlex connector against recorded fixtures (httpx MockTransport)."""

import json
from collections.abc import Iterator

import httpx
import pytest

from sixsentences_server.connectors.openalex import (
    OpenAlexClient,
    OpenAlexError,
    _invert_abstract,
    _parse_work,
)

WORK_FIXTURE = {
    "id": "https://openalex.org/W123",
    "doi": "https://doi.org/10.5555/demo",
    "title": "Demo work",
    "abstract_inverted_index": {"Systematic": [0], "reviews": [1], "matter": [2]},
    "publication_year": 2024,
    "primary_location": {"source": {"display_name": "Demo Journal"}},
    "authorships": [{"author": {"display_name": "Jane Doe"}}],
    "cited_by_count": 42,
    "is_retracted": False,
}


def _client(handler: httpx.MockTransport) -> OpenAlexClient:
    return OpenAlexClient(
        mailto="test@example.org",
        api_key="test-key",
        http=httpx.Client(base_url="https://api.openalex.org", transport=handler),
    )


def test_invert_abstract() -> None:
    assert _invert_abstract({"b": [1], "a": [0]}) == "a b"
    assert _invert_abstract(None) is None


def test_parse_open_access_and_arxiv() -> None:
    work = _parse_work(
        dict(
            WORK_FIXTURE,
            open_access={"oa_status": "green", "oa_url": "https://arxiv.org/abs/2301.12345"},
            best_oa_location={
                "pdf_url": "https://arxiv.org/pdf/2301.12345",
                "landing_page_url": "https://arxiv.org/abs/2301.12345",
                "license": "cc-by",
                "version": "submittedVersion",
            },
            locations=[{"pdf_url": "https://arxiv.org/pdf/2301.12345"}],
            ids={"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7654321"},
        )
    )
    assert work.oa_status == "green"
    assert work.pdf_url == "https://arxiv.org/pdf/2301.12345"
    assert work.oa_license == "cc-by"
    assert work.oa_version == "submittedVersion"
    assert work.arxiv_id == "2301.12345"  # parsed from the location url
    assert work.pmcid == "PMC7654321"  # normalized from the ncbi url form


def test_parse_closed_work_has_no_oa_urls() -> None:
    work = _parse_work(dict(WORK_FIXTURE, open_access={"oa_status": "closed", "oa_url": None}))
    assert work.oa_status == "closed"
    assert work.pdf_url is None and work.arxiv_id is None


def test_parse_recovers_pdf_from_a_non_best_location() -> None:
    # best_oa_location has only a landing page; a secondary OA location has the PDF
    work = _parse_work(
        dict(
            WORK_FIXTURE,
            open_access={"oa_status": "hybrid", "oa_url": "https://pub.example/landing"},
            best_oa_location={
                "landing_page_url": "https://pub.example/landing",
                "pdf_url": None,
                "license": "cc-by",
                "version": "publishedVersion",
            },
            locations=[
                {"is_oa": True, "landing_page_url": "https://pub.example/landing", "pdf_url": None},
                {"is_oa": True, "pdf_url": "https://repo.example/paper.pdf"},
            ],
        )
    )
    assert work.pdf_url == "https://repo.example/paper.pdf"  # recovered from a non-best location
    assert work.oa_landing_url == "https://pub.example/landing"  # kept for citation_pdf_url


def test_search_parses_and_paginates() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page = request.url.params.get("cursor")
        if page == "*":
            payload = {"results": [WORK_FIXTURE], "meta": {"next_cursor": "c2"}}
        else:
            payload = {"results": [], "meta": {"next_cursor": None}}
        return httpx.Response(200, json=payload)

    client = _client(httpx.MockTransport(handler))
    works = client.search('"systematic review"', limit=10)

    assert len(works) == 1
    work = works[0]
    assert work.id == "W123"
    assert work.doi == "10.5555/demo"
    assert work.abstract == "Systematic reviews matter"
    assert work.venue == "Demo Journal"
    # auth etiquette on the wire
    first = calls[0]
    assert first.url.params["mailto"] == "test@example.org"
    assert first.url.params["api_key"] == "test-key"
    assert json.loads(json.dumps(first.url.params["search"])) == '"systematic review"'


def test_iter_search_pages_preserves_hard_cap_and_provider_state() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        results = [
            dict(WORK_FIXTURE, id=f"https://openalex.org/W{len(calls)}-{index}")
            for index in range(200)
        ]
        return httpx.Response(
            200,
            json={"results": results, "meta": {"next_cursor": "next", "count": 1_000}},
        )

    client = _client(httpx.MockTransport(handler))
    pages = list(client.iter_search_pages("systematic review", limit=250))

    assert [len(page.records) for page in pages] == [200, 50]
    assert [page.fetched for page in pages] == [200, 250]
    assert pages[-1].provider_total == 1_000
    assert pages[-1].has_more is True
    assert calls[-1].url.params["per_page"] == "50"


def test_search_sanitizes_question_marks_and_wildcards() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"results": [], "meta": {"next_cursor": None}})

    client = _client(httpx.MockTransport(handler))
    client.search("Which transformer* works best?", limit=10)

    assert calls[0].url.params["search"] == "Which transformer  works best"


def test_non_transient_http_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid search"})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(OpenAlexError, match="HTTP 400"):
        client.search("invalid")


def test_iter_works_respects_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        results = [dict(WORK_FIXTURE, id=f"https://openalex.org/W{i}") for i in range(200)]
        return httpx.Response(200, json={"results": results, "meta": {"next_cursor": "next"}})

    client = _client(httpx.MockTransport(handler))
    works = list(client.iter_works("primary_topic.field.id:fields/17", limit=250))
    assert len(works) == 250


def test_auth_error_is_actionable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "key required"})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(OpenAlexError, match="API key"):
        client.search("x")


def test_exact_work_lookup_has_a_hard_timeout_and_parses_one_record() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=WORK_FIXTURE)

    work = _client(httpx.MockTransport(handler)).get_work("doi:10.5555/demo")

    assert work is not None and work.id == "W123"
    assert requests[0].extensions["timeout"]["read"] == 8.0


def test_exact_work_lookup_rejects_an_oversize_response_before_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"Content-Length": "1000001"},
            content=b"{}",
        )

    assert _client(httpx.MockTransport(handler)).get_work("W123") is None


def test_exact_work_lookup_bounds_chunked_bodies_without_content_length() -> None:
    class OversizeStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"{" + b" " * 600_000
            yield b" " * 600_000 + b"}"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, stream=OversizeStream())

    assert _client(httpx.MockTransport(handler)).get_work("W123") is None
