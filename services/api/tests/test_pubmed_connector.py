"""Contract tests for the native PubMed E-utilities connector."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from sixsentences_server.connectors.pubmed import (
    MAX_QUERY_CHARACTERS,
    MAX_RESPONSE_BYTES,
    MAX_SEARCH_RESULTS,
    PubMedClient,
    PubMedError,
    PubMedRequestRateGate,
)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    email: str = "researcher@example.org",
    api_key: str = "test-api-key",
) -> PubMedClient:
    return PubMedClient(
        email=email,
        api_key=api_key,
        tool="sixsentences-tests",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )


def _esearch_xml(*pmids: str, count: int | None = None) -> bytes:
    total = len(pmids) if count is None else count
    ids = "".join(f"<Id>{pmid}</Id>" for pmid in pmids)
    return (
        f"<?xml version='1.0'?><eSearchResult><Count>{total}</Count>"
        f"<RetMax>{len(pmids)}</RetMax><IdList>{ids}</IdList></eSearchResult>"
    ).encode()


def _minimal_article(pmid: str, *, title: str | None = None) -> str:
    return (
        "<PubmedArticle><MedlineCitation>"
        f"<PMID>{pmid}</PMID><Article><ArticleTitle>{title or f'Article {pmid}'}</ArticleTitle>"
        "</Article></MedlineCitation><PubmedData><ArticleIdList>"
        f'<ArticleId IdType="pubmed">{pmid}</ArticleId>'
        "</ArticleIdList></PubmedData></PubmedArticle>"
    )


def _efetch_xml(*articles: str) -> bytes:
    return f"<?xml version='1.0'?><PubmedArticleSet>{''.join(articles)}</PubmedArticleSet>".encode()


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


def test_close_releases_only_an_internally_owned_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sixsentences_server.connectors import pubmed as pubmed_module

    class StubHttp:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    owned_http = StubHttp()
    monkeypatch.setattr(pubmed_module.httpx, "Client", lambda: owned_http)
    owned = PubMedClient(email="researcher@example.org")
    owned.close()
    assert owned_http.closed is True

    external_http = StubHttp()
    external = PubMedClient(
        email="researcher@example.org",
        http=external_http,  # type: ignore[arg-type]
    )
    external.close()
    assert external_http.closed is False


def test_search_normalizes_pubmed_xml_and_exposes_truncation_metadata() -> None:
    requests: list[httpx.Request] = []
    article = """
    <PubmedArticle>
      <MedlineCitation>
        <PMID Version="1">12345678</PMID>
        <Article>
          <Journal>
            <ISSN IssnType="Electronic">1234-5678</ISSN>
            <JournalIssue CitedMedium="Internet">
              <Volume>42</Volume><Issue>7</Issue>
              <PubDate><Year>2025</Year><Month>Sep</Month><Day>03</Day></PubDate>
            </JournalIssue>
            <Title>Journal of Reliable Retrieval</Title>
          </Journal>
          <ArticleTitle>Effects of <i>AI</i> systems</ArticleTitle>
          <Pagination><MedlinePgn>12-19</MedlinePgn></Pagination>
          <Abstract>
            <AbstractText Label="BACKGROUND">First section.</AbstractText>
            <AbstractText Label="METHODS">Second <i>section</i>.</AbstractText>
          </Abstract>
          <AuthorList>
            <Author><LastName>Doe</LastName><ForeName>Jane Q</ForeName></Author>
            <Author><CollectiveName>Retrieval Study Group</CollectiveName></Author>
          </AuthorList>
          <Language>ENG</Language>
          <PublicationTypeList>
            <PublicationType>Journal Article</PublicationType>
            <PublicationType>Systematic Review</PublicationType>
          </PublicationTypeList>
        </Article>
      </MedlineCitation>
      <PubmedData>
        <ArticleIdList>
          <ArticleId IdType="pubmed">12345678</ArticleId>
          <ArticleId IdType="doi">https://doi.org/10.1234/DEMO.5</ArticleId>
          <ArticleId IdType="pmc">PMC998877</ArticleId>
        </ArticleIdList>
      </PubmedData>
    </PubmedArticle>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/esearch.fcgi"):
            return httpx.Response(200, content=_esearch_xml("12345678", count=17))
        return httpx.Response(200, content=_efetch_xml(article))

    result = _client(handler).search_with_metadata(
        '"machine learning"[tiab]',
        limit=1,
        year_from=2020,
        year_to=2025,
    )

    assert result.provider_total == 17
    assert result.pmids_returned == 1
    assert result.truncated is True
    assert len(result.records) == 1
    work = result.records[0]
    assert work.id == "pubmed:12345678"
    assert work.source == "pubmed"
    assert work.pmid == "12345678"
    assert work.pmcid == "PMC998877"
    assert work.doi == "10.1234/demo.5"
    assert work.title == "Effects of AI systems"
    assert work.abstract == "BACKGROUND: First section. METHODS: Second section."
    assert work.year == 2025
    assert work.publication_date == "2025-09-03"
    assert work.venue == "Journal of Reliable Retrieval"
    assert work.authors == ["Jane Q Doe", "Retrieval Study Group"]
    assert work.volume == "42"
    assert work.issue == "7"
    assert work.pages == "12-19"
    assert work.language == "eng"
    assert work.issn == "1234-5678"
    assert work.work_type == "systematic-review"

    search_request, fetch_request = requests
    search_form = _form(search_request)
    fetch_form = _form(fetch_request)
    assert search_request.method == "POST"
    assert fetch_request.method == "POST"
    assert search_request.url.query == b""
    assert fetch_request.url.query == b""
    assert search_form["db"] == ["pubmed"]
    assert search_form["email"] == ["researcher@example.org"]
    assert search_form["api_key"] == ["test-api-key"]
    assert search_form["tool"] == ["sixsentences-tests"]
    assert search_form["term"][0].endswith("AND 2020:2025[pdat]")
    assert fetch_form["id"] == ["12345678"]
    assert fetch_form["rettype"] == ["abstract"]
    assert search_request.extensions["timeout"]["read"] == 15.0
    assert fetch_request.extensions["timeout"]["read"] == 15.0


def test_fetch_batches_at_200_and_restores_requested_order() -> None:
    fetch_batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.query == b""
        batch = _form(request)["id"][0].split(",")
        fetch_batches.append(batch)
        return httpx.Response(
            200,
            content=_efetch_xml(*(_minimal_article(pmid) for pmid in reversed(batch))),
        )

    requested = [str(index) for index in range(1, 202)]
    records = _client(handler).fetch_records([*requested, "3"])

    assert [len(batch) for batch in fetch_batches] == [200, 1]
    assert [record.pmid for record in records] == requested


def test_fetch_normalizes_mixed_article_and_book_article_sets() -> None:
    book_article = """
    <PubmedBookArticle>
      <BookDocument>
        <PMID Version="1">21433338</PMID>
        <ArticleIdList>
          <ArticleId IdType="bookaccession">NBK555555</ArticleId>
          <ArticleId IdType="doi">https://doi.org/10.1000/Book.Chapter</ArticleId>
        </ArticleIdList>
        <Book>
          <Publisher><PublisherName>National Academies Press</PublisherName></Publisher>
          <BookTitle>Methods in <i>Evidence Synthesis</i></BookTitle>
          <PubDate><Year>2024</Year><Month>Oct</Month><Day>05</Day></PubDate>
          <AuthorList Type="editors">
            <Author><LastName>Editor</LastName><ForeName>Excluded</ForeName></Author>
          </AuthorList>
          <Volume>3</Volume>
        </Book>
        <ArticleTitle>Reliable <i>multisource</i> retrieval</ArticleTitle>
        <Pagination><StartPage>15</StartPage><EndPage>27</EndPage></Pagination>
        <Language>ENG</Language>
        <AuthorList Type="authors">
          <Author><LastName>Ng</LastName><ForeName>Ada</ForeName></Author>
          <Author><CollectiveName>Evidence Methods Group</CollectiveName></Author>
        </AuthorList>
        <AuthorList Type="editors">
          <Author><LastName>Editor</LastName><ForeName>Also Excluded</ForeName></Author>
        </AuthorList>
        <PublicationType UI="D000000">Retracted Publication</PublicationType>
        <PublicationType UI="D016428">Book Chapter</PublicationType>
        <Abstract>
          <AbstractText Label="OVERVIEW">First section.</AbstractText>
          <AbstractText>Second <i>section</i>.</AbstractText>
        </Abstract>
      </BookDocument>
      <PubmedBookData>
        <PublicationStatus>ppublish</PublicationStatus>
        <ArticleIdList>
          <ArticleId IdType="pubmed">21433338</ArticleId>
          <ArticleId IdType="pmc">1234567</ArticleId>
        </ArticleIdList>
      </PubmedBookData>
    </PubmedBookArticle>
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_efetch_xml(book_article, _minimal_article("17247418")),
        )

    records = _client(handler).fetch_records(["17247418", "21433338"])

    assert [record.pmid for record in records] == ["17247418", "21433338"]
    work = records[1]
    assert work.id == "pubmed:21433338"
    assert work.source == "pubmed"
    assert work.pmcid == "PMC1234567"
    assert work.doi == "10.1000/book.chapter"
    assert work.title == "Reliable multisource retrieval"
    assert work.abstract == "OVERVIEW: First section. Second section."
    assert work.year == 2024
    assert work.publication_date == "2024-10-05"
    assert work.venue == "Methods in Evidence Synthesis"
    assert work.publisher == "National Academies Press"
    assert work.authors == ["Ada Ng", "Evidence Methods Group"]
    assert work.volume == "3"
    assert work.pages == "15-27"
    assert work.language == "eng"
    assert work.work_type == "book-chapter"
    assert work.is_retracted is True


@pytest.mark.parametrize(
    "articles",
    [
        (_minimal_article("111"),),
        (_minimal_article("111"), _minimal_article("222"), _minimal_article("333")),
        (_minimal_article("111"), _minimal_article("111"), _minimal_article("222")),
    ],
    ids=["missing", "additional", "duplicate"],
)
def test_fetch_rejects_incomplete_or_unexpected_record_sets(
    articles: tuple[str, ...],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_efetch_xml(*articles))

    with pytest.raises(PubMedError) as caught:
        _client(handler).fetch_records(["111", "222"])

    message = str(caught.value)
    assert "record set" in message
    assert "111" not in message
    assert "222" not in message
    assert "333" not in message


def test_doi_falls_back_to_elocation_and_medline_date_supplies_year() -> None:
    article = """
    <PubmedArticle>
      <MedlineCitation>
        <PMID>7654321</PMID>
        <Article>
          <Journal><JournalIssue><PubDate><MedlineDate>1998 Dec-1999 Jan</MedlineDate></PubDate>
          </JournalIssue><Title>Legacy Journal</Title></Journal>
          <ArticleTitle>Legacy article</ArticleTitle>
          <ELocationID EIdType="doi">doi: 10.9/Legacy</ELocationID>
          <AuthorList><Author><LastName>Smith</LastName><Initials>AB</Initials></Author></AuthorList>
          <PublicationTypeList>
            <PublicationType>Retracted Publication</PublicationType>
          </PublicationTypeList>
        </Article>
      </MedlineCitation>
    </PubmedArticle>
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_efetch_xml(article))

    work = _client(handler).fetch_records(["7654321"])[0]
    assert work.doi == "10.9/legacy"
    assert work.year == 1998
    assert work.publication_date == "1998"
    assert work.authors == ["AB Smith"]
    assert work.is_retracted is True


@pytest.mark.parametrize("query", ["short query", "x" * 301])
def test_esearch_always_uses_post_and_keeps_sensitive_parameters_out_of_url(
    query: str,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_esearch_xml())

    assert _client(handler).search_pmids(query, limit=10) == []

    request = seen[0]
    form = _form(request)
    assert request.method == "POST"
    assert request.url.query == b""
    assert form["term"] == [query]
    assert form["email"] == ["researcher@example.org"]
    assert form["api_key"] == ["test-api-key"]


def test_retries_only_transient_statuses_and_transport_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("provider timed out", request=request)
        if calls == 2:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, content=_esearch_xml("123"))

    assert _client(handler).search_pmids("safe query", limit=1) == ["123"]
    assert calls == 3


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_non_transient_http_errors_fail_fast_without_secret_leaks(status_code: int) -> None:
    calls = 0
    api_key = "super-secret-api-key"
    email = "private-person@example.org"
    query = "private-query-marker"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, text=f"leaked {api_key} {email} {query}")

    with pytest.raises(PubMedError) as caught:
        _client(handler, email=email, api_key=api_key).search_pmids(query)

    assert calls == 1
    assert caught.value.status_code == status_code
    message = str(caught.value)
    assert f"HTTP {status_code}" in message
    assert api_key not in message
    assert email not in message
    assert query not in message


def test_malformed_xml_is_terminal_and_body_is_not_exposed() -> None:
    calls = 0
    marker = "private-response-marker"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=f"<broken>{marker}".encode())

    with pytest.raises(PubMedError, match="malformed XML") as caught:
        _client(handler).search_pmids("query")

    assert calls == 1
    assert marker not in str(caught.value)


def test_provider_xml_error_is_terminal_and_not_echoed() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=b"<eSearchResult><ERROR>secret diagnostic</ERROR></eSearchResult>",
        )

    with pytest.raises(PubMedError, match="rejected the request") as caught:
        _client(handler).search_pmids("query")

    assert calls == 1
    assert "secret diagnostic" not in str(caught.value)


def test_response_size_and_input_limits_are_hard() -> None:
    calls = 0

    def oversized(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)},
            content=b"<eSearchResult />",
        )

    client = _client(oversized)
    with pytest.raises(PubMedError, match="size limit"):
        client.search_pmids("query")
    assert calls == 1

    with pytest.raises(ValueError, match="search limit"):
        client.search_pmids("query", limit=MAX_SEARCH_RESULTS + 1)
    with pytest.raises(ValueError, match="query"):
        client.search_pmids("x" * (MAX_QUERY_CHARACTERS + 1))
    with pytest.raises(ValueError, match="PMID"):
        client.fetch_records(["123", "not-an-id"])
    assert calls == 1


def test_empty_search_and_fetch_do_not_make_requests() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("empty input must not make a request")

    client = _client(handler)
    result = client.search_with_metadata("   ")
    assert result.records == []
    assert result.provider_total == 0
    assert result.truncated is False
    assert client.fetch_records([]) == []


def test_constructor_validates_ncbi_identity_and_timeout_without_echoing_values() -> None:
    with pytest.raises(ValueError, match="tool"):
        PubMedClient(tool="not a valid tool")
    with pytest.raises(ValueError, match="email"):
        PubMedClient(email="user name@example.org")
    with pytest.raises(ValueError, match="email"):
        PubMedClient(email="not-an-email")
    with pytest.raises(ValueError, match="email"):
        PubMedClient(email="research@localhost")
    with pytest.raises(ValueError, match="API key"):
        PubMedClient(api_key="secret key")
    with pytest.raises(ValueError, match="timeout"):
        PubMedClient(timeout_seconds=31)


def test_shared_rate_gate_waits_for_a_deployment_slot() -> None:
    class Limiter:
        def __init__(self) -> None:
            self.decisions = iter([False, True])
            self.keys: list[str] = []

        def allow(self, key: str) -> bool:
            self.keys.append(key)
            return next(self.decisions)

    limiter = Limiter()
    waits: list[float] = []
    gate = PubMedRequestRateGate(
        limiter,
        window_seconds=0.5,
        unavailable_error=RuntimeError,
        sleep=waits.append,
        wall_clock=lambda: 10.1,
        monotonic_clock=lambda: 0.0,
    )

    gate.acquire()

    assert limiter.keys == ["deployment", "deployment"]
    assert waits == [pytest.approx(0.401)]


def test_shared_rate_gate_fails_closed_without_leaking_limiter_details() -> None:
    class Unavailable(RuntimeError):
        pass

    class Limiter:
        def allow(self, key: str) -> bool:
            del key
            raise Unavailable("database-private-diagnostic")

    gate = PubMedRequestRateGate(
        Limiter(),
        window_seconds=0.5,
        unavailable_error=Unavailable,
    )

    with pytest.raises(PubMedError, match="coordination is unavailable") as caught:
        gate.acquire()

    assert "database-private-diagnostic" not in str(caught.value)
