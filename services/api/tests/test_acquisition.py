"""Full-text acquisition: resolver (OA-first + structural tabu), store, fetch,
extract, and the run-level service."""

import base64
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.acquisition.arxiv import (
    ArxivClient,
    ArxivEntry,
    ArxivMatch,
    best_match,
    parse_atom,
)
from sixsentences_server.acquisition.extract import StdlibTextExtractor
from sixsentences_server.acquisition.fetch import HttpxFetcher
from sixsentences_server.acquisition.grobid import GrobidTextExtractor, tei_to_text
from sixsentences_server.acquisition.identity import IdentityStatus, verify_document_identity
from sixsentences_server.acquisition.landing import pdf_url_from_html
from sixsentences_server.acquisition.models import (
    OA_BASES,
    AcquisitionResult,
    AcquisitionStatus,
    DocumentSource,
    FetchedBlob,
    LegalBasis,
    TextStatus,
)
from sixsentences_server.acquisition.pdf import PdfTextExtractor
from sixsentences_server.acquisition.resolver import OpenAccessResolver
from sixsentences_server.acquisition.service import AcquisitionService, acquire_for_run
from sixsentences_server.acquisition.store import LocalDocumentStore
from sixsentences_server.core.db import (
    DocumentRow,
    Run,
    RunEvent,
    WorkRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.core.net import is_public_http_url
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.pipeline.run import execute_run

_JATS = b'<?xml version="1.0"?><article><front><article-meta><abstract><p>We evaluate LLM-generated Terraform against a reference suite.</p></abstract></article-meta></front><body><sec><p>The method scales to large infrastructure graphs.</p></sec></body></article>'


class FakeFetcher:
    """A DocumentFetcher that serves canned blobs by url — no network."""

    def __init__(self, blobs: dict[str, FetchedBlob]) -> None:
        self.blobs = blobs
        self.requested: list[str] = []

    def fetch(self, url: str) -> FetchedBlob | None:
        self.requested.append(url)
        return self.blobs.get(url)


def _work(**oa: object) -> WorkRecord:
    return WorkRecord(id="W1", title="A study", **oa)


def test_arxiv_work_plans_a_green_pdf_candidate() -> None:
    plan = OpenAccessResolver().plan(_work(oa_status="green", arxiv_id="2301.12345"))
    assert len(plan.candidates) == 1
    cand = plan.candidates[0]
    assert cand.source is DocumentSource.ARXIV
    assert cand.legal_basis is LegalBasis.OA_GREEN
    assert cand.url == "https://export.arxiv.org/pdf/2301.12345"
    assert plan.reason_if_empty is None


def test_gold_publisher_pdf_maps_to_gold_basis() -> None:
    plan = OpenAccessResolver().plan(
        _work(oa_status="gold", pdf_url="https://journal.example/article.pdf", oa_license="cc-by")
    )
    assert plan.candidates[0].source is DocumentSource.UNPAYWALL
    assert plan.candidates[0].legal_basis is LegalBasis.OA_GOLD
    assert plan.candidates[0].license == "cc-by"


def test_pmc_work_plans_parseable_xml_first() -> None:
    plan = OpenAccessResolver().plan(
        _work(oa_status="green", pmcid="PMC7654321", arxiv_id="2301.99999")
    )
    assert plan.candidates[0].source is DocumentSource.PMC
    assert plan.candidates[0].content_hint == "xml"
    assert "PMC7654321" in plan.candidates[0].url


def test_best_oa_location_that_is_arxiv_is_not_duplicated() -> None:
    plan = OpenAccessResolver().plan(
        _work(oa_status="green", arxiv_id="2301.12345", pdf_url="https://arxiv.org/pdf/2301.12345")
    )
    assert len(plan.candidates) == 1


def test_resolver_keeps_alternative_oa_locations() -> None:
    plan = OpenAccessResolver().plan(
        _work(
            oa_status="green",
            pdf_url="https://publisher.example/article.pdf",
            oa_locations=[
                {
                    "pdf_url": "https://repository.example/manuscript.pdf",
                    "landing_page_url": "https://repository.example/record/42",
                    "license": "cc-by",
                    "version": "acceptedVersion",
                }
            ],
        )
    )
    assert [candidate.url for candidate in plan.candidates] == [
        "https://publisher.example/article.pdf",
        "https://repository.example/manuscript.pdf",
        "https://repository.example/record/42",
    ]


def test_closed_work_yields_no_candidate_and_an_honest_reason() -> None:
    plan = OpenAccessResolver().plan(_work(oa_status="closed"))
    assert plan.candidates == []
    assert plan.reason_if_empty is not None
    assert "closed" in plan.reason_if_empty


def test_missing_oa_metadata_asks_for_a_resync() -> None:
    plan = OpenAccessResolver().plan(_work())
    assert plan.candidates == []
    assert "re-sync" in (plan.reason_if_empty or "")


def test_resolver_only_ever_emits_open_access_bases() -> None:
    """No input can make the resolver produce a paywalled / credential-replay
    candidate: there is no such code path. Every candidate it ever emits is
    open-access-grounded."""
    resolver = OpenAccessResolver()
    works = [
        _work(oa_status=status, arxiv_id="2301.00001", pmcid="PMC1", pdf_url="https://x/y.pdf")
        for status in ("gold", "hybrid", "bronze", "green", "diamond", "closed", None)
    ]
    for work in works:
        for cand in resolver.plan(work).candidates:
            assert cand.legal_basis in OA_BASES


def test_store_is_content_addressed_and_dedups(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)
    sha1, path1 = store.put(b"same bytes")
    sha2, path2 = store.put(b"same bytes")
    assert sha1 == sha2 and path1 == path2
    assert store.get(sha1) == b"same bytes"
    assert store.get("deadbeef") is None


def test_store_round_trips_extracted_text(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)
    sha, _ = store.put(b"pdf-bytes")
    store.put_text(sha, "the parsed full text")
    assert store.get_text(sha) == "the parsed full text"
    assert store.get_text("missing") is None


def _offline_guard(url: str) -> bool:
    return is_public_http_url(url, resolve=False)


def _fetcher(handler: httpx.MockTransport, **kw: int) -> HttpxFetcher:
    client = httpx.Client(transport=handler, follow_redirects=False)
    return HttpxFetcher(http=client, guard=_offline_guard, **kw)


def test_fetch_returns_blob_with_content_type() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"}
        )

    blob = _fetcher(httpx.MockTransport(handler)).fetch("https://arxiv.org/pdf/2301.1")
    assert blob is not None
    assert blob.content_type == "application/pdf"
    assert blob.byte_size == len(b"%PDF-1.7 ...")


def test_fetch_missing_returns_none() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    assert _fetcher(httpx.MockTransport(handler)).fetch("https://x/y.pdf") is None


def test_fetch_refuses_oversize() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "application/pdf"})

    assert _fetcher(httpx.MockTransport(handler), max_bytes=1000).fetch("https://x/big.pdf") is None


def test_fetch_refuses_internal_target_without_a_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"secret", headers={"content-type": "text/plain"})

    fetcher = _fetcher(httpx.MockTransport(handler))
    assert fetcher.fetch("http://169.254.169.254/latest/meta-data/iam/") is None
    assert calls == []


def test_extract_jats_xml_yields_full_text() -> None:
    out = StdlibTextExtractor().extract(_JATS, "application/xml")
    assert out.status is TextStatus.PARSED
    assert "Terraform" in out.text and "infrastructure graphs" in out.text


def test_extract_html_strips_tags_and_scripts() -> None:
    html = b"<html><head><style>.x{color:red}</style></head><body><p>The real body text of the paper spans several readable sentences.</p><script>evil()</script></body></html>"
    out = StdlibTextExtractor().extract(html, "text/html")
    assert out.status is TextStatus.PARSED
    assert "real body text of the paper" in out.text
    assert "evil" not in out.text and "color:red" not in out.text


def test_extract_pdf_is_stored_unparsed_not_faked() -> None:
    out = StdlibTextExtractor().extract(b"%PDF-1.7 binary...", "application/pdf")
    assert out.status is TextStatus.STORED_UNPARSED
    assert out.text == ""


def test_extract_short_text_is_empty_not_parsed() -> None:
    out = StdlibTextExtractor().extract(b"tiny", "text/plain")
    assert out.status is TextStatus.EMPTY


_REAL_PDF = base64.b64decode(
    "JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvUmVzb3VyY2VzIDw8IC9Gb250IDw8IC9GMSA0IDAgUiA+PiA+PiAvTWVkaWFCb3ggWzAgMCA2MTIgNzkyXSAvQ29udGVudHMgNSAwIFIgPj4KZW5kb2JqCjQgMCBvYmoKPDwgL1R5cGUgL0ZvbnQgL1N1YnR5cGUgL1R5cGUxIC9CYXNlRm9udCAvSGVsdmV0aWNhID4+CmVuZG9iago1IDAgb2JqCjw8IC9MZW5ndGggODcgPj4Kc3RyZWFtCkJUIC9GMSAxOCBUZiA3MiA3MDAgVGQgKFRlcnJhZm9ybSBmdWxsIHRleHQgZXh0cmFjdGVkIGJ5IHB5cGRmIHJlYWxseSB3b3JrcyBoZXJlKSBUaiBFVAplbmRzdHJlYW0KZW5kb2JqCnhyZWYKMCA2CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAwOSAwMDAwMCBuIAowMDAwMDAwMDU4IDAwMDAwIG4gCjAwMDAwMDAxMTUgMDAwMDAgbiAKMDAwMDAwMDI0MSAwMDAwMCBuIAowMDAwMDAwMzExIDAwMDAwIG4gCnRyYWlsZXIKPDwgL1NpemUgNiAvUm9vdCAxIDAgUiA+PgpzdGFydHhyZWYKNDQ4CiUlRU9G"
)


def test_pdf_extractor_parses_real_pdf_text() -> None:
    out = PdfTextExtractor().extract(_REAL_PDF, "application/pdf")
    assert out.status is TextStatus.PARSED
    assert "Terraform full text" in out.text


def test_pdf_extractor_malformed_pdf_stays_stored_unparsed() -> None:
    out = PdfTextExtractor().extract(b"%PDF-1.7 not really a pdf at all", "application/pdf")
    assert out.status is TextStatus.STORED_UNPARSED
    assert out.text == ""


def test_pdf_extractor_delegates_non_pdf_to_stdlib() -> None:
    out = PdfTextExtractor().extract(_JATS, "application/xml")
    assert out.status is TextStatus.PARSED and "Terraform" in out.text


def test_document_identity_rejects_an_unrelated_long_pdf() -> None:
    expected = WorkRecord(
        id="W1",
        doi="10.1016/j.system.2024.103225",
        title="Academic communication with AI powered language tools",
        authors=["Jane Researcher"],
    )
    wrong_text = (
        "Enhancing higher education through hybrid and flipped learning in nuclear engineering. doi:10.1016/j.nucengdes.2024.113028. "
        + "Reactor design course outcomes and student attendance. " * 80
    )
    check = verify_document_identity(expected, wrong_text)
    assert check.status is IdentityStatus.MISMATCH
    assert "10.1016/j.nucengdes.2024.113028" in check.detected_dois


def test_acquisition_tries_next_candidate_after_identity_mismatch(tmp_path: Path) -> None:
    work = WorkRecord(
        id="W1",
        doi="10.1000/correct",
        title="Generative artificial intelligence feedback for academic writing",
        authors=["Ada Researcher"],
        oa_status="green",
        pdf_url="https://publisher.example/wrong.pdf",
        oa_landing_url="https://repository.example/record",
    )
    wrong = (
        "Unrelated nuclear reactor optimisation. doi:10.9999/wrong. "
        + "Thermal hydraulics and reactor pressure analysis. " * 80
    ).encode()
    right = (
        "Generative artificial intelligence feedback for academic writing. Ada Researcher. doi:10.1000/correct. "
        + "This controlled study evaluates student writing outcomes. " * 80
    ).encode()
    landing = b'<meta name="citation_pdf_url" content="https://repository.example/correct.pdf">'
    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher(
            {
                "https://publisher.example/wrong.pdf": FetchedBlob(
                    wrong, "text/plain", "https://publisher.example/wrong.pdf"
                ),
                "https://repository.example/record": FetchedBlob(
                    landing, "text/html", "https://repository.example/record"
                ),
                "https://repository.example/correct.pdf": FetchedBlob(
                    right, "text/plain", "https://repository.example/correct.pdf"
                ),
            }
        ),
        extractor=StdlibTextExtractor(),
        store=LocalDocumentStore(tmp_path),
    )
    result = service.acquire(work)
    assert result.status is AcquisitionStatus.RETRIEVED
    assert result.url == "https://repository.example/correct.pdf"
    assert result.note and "identity verified" in result.note


_TEI = b'<?xml version="1.0"?><TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>LLMs for Terraform Generation</title></titleStmt></fileDesc><profileDesc><abstract><p>We evaluate LLM-generated Terraform on a benchmark suite.</p></abstract></profileDesc></teiHeader><text><body><div><head>Introduction</head><p>Infrastructure as code is widely adopted across cloud providers, and large language models are increasingly used to generate and evaluate such configurations for correctness, security, and maintainability across diverse real-world deployment scenarios today.</p></div></body></text></TEI>'


def _grobid(handler: httpx.MockTransport, fallback: object = None) -> GrobidTextExtractor:
    return GrobidTextExtractor(
        grobid_url="http://grobid:8070",
        fallback=fallback or PdfTextExtractor(),
        http=httpx.Client(transport=handler),
    )


def test_tei_to_text_extracts_title_abstract_body() -> None:
    text = tei_to_text(_TEI)
    assert "LLMs for Terraform Generation" in text
    assert "benchmark suite" in text
    assert "Infrastructure as code is widely adopted" in text


def test_grobid_extractor_uses_the_service() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_TEI)

    out = _grobid(httpx.MockTransport(handler)).extract(b"%PDF-1.7 binary", "application/pdf")
    assert out.status is TextStatus.PARSED
    assert "LLMs for Terraform Generation" in out.text


def test_grobid_down_falls_back_to_pypdf() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    out = _grobid(httpx.MockTransport(handler)).extract(_REAL_PDF, "application/pdf")
    assert out.status is TextStatus.PARSED
    assert "Terraform full text" in out.text


def test_grobid_delegates_non_pdf_to_fallback() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_TEI)

    out = _grobid(httpx.MockTransport(handler)).extract(_JATS, "application/xml")
    assert out.status is TextStatus.PARSED and "Terraform" in out.text


def _run_with_works(session: Session, works: list[WorkRecord]) -> Run:
    org = get_default_org(session)
    project = ProjectRow(org_id=org.id, name="acq")
    session.add(project)
    session.flush()
    run = Run(org_id=org.id, project_id=project.id, question="q", status="running")
    session.add(run)
    session.flush()
    for work in works:
        session.add(WorkRow(id=work.id, doi=work.doi, title=work.title, year=work.year))
    session.flush()
    return run


def test_acquire_for_run_writes_ledger_and_summary(settings: object, tmp_path: Path) -> None:
    init_db()
    store = LocalDocumentStore(tmp_path / "docs")
    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher(
            {
                "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML": FetchedBlob(
                    _JATS,
                    "application/xml",
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML",
                ),
                "https://export.arxiv.org/pdf/2301.00002": FetchedBlob(
                    b"%PDF-1.7 binary blob",
                    "application/pdf",
                    "https://export.arxiv.org/pdf/2301.00002",
                ),
            }
        ),
        extractor=StdlibTextExtractor(),
        store=store,
    )
    works = [
        WorkRecord(id="W_pmc", title="pmc paper", oa_status="green", pmcid="PMC1"),
        WorkRecord(id="W_arxiv", title="arxiv paper", oa_status="green", arxiv_id="2301.00002"),
        WorkRecord(id="W_closed", title="closed paper", oa_status="closed"),
    ]
    with db_session() as session:
        run = _run_with_works(session, works)
        progress: list[tuple[int, int, int, int]] = []
        summary = acquire_for_run(
            session,
            run,
            works,
            service=service,
            on_progress=lambda completed, total, current: progress.append(
                (completed, total, current.retrieved, current.not_retrieved)
            ),
        )
        assert summary.sought == 3
        assert summary.retrieved == 2 and summary.not_retrieved == 1
        assert summary.parsed == 1
        assert summary.stored_unparsed == 1
        assert summary.by_legal_basis == {"oa_green": 2}
        assert progress[0] == (0, 3, 0, 0)
        assert progress[-1] == (3, 3, 2, 1)
        rows = session.scalars(select(DocumentRow).where(DocumentRow.run_id == run.id)).all()
        assert len(rows) == 3
        assert all(row.project_id == run.project_id for row in rows)
        pmc = next(r for r in rows if r.work_id == "W_pmc")
        assert pmc.status == "retrieved" and pmc.legal_basis == "oa_green"
        assert pmc.text_status == "parsed" and pmc.checksum
        text = store.get_text(pmc.checksum or "")
        assert text is not None and "Terraform" in text
        closed = next(r for r in rows if r.work_id == "W_closed")
        assert closed.status == "not_retrieved"
        assert closed.reason and "closed" in closed.reason
        assert closed.checksum is None


def test_pdf_url_from_html_absolute_and_relative() -> None:
    absolute = (
        b'<html><head><meta name="citation_pdf_url" content="https://x.org/a.pdf"></head></html>'
    )
    assert pdf_url_from_html(absolute, "https://x.org/landing") == "https://x.org/a.pdf"
    relative = b'<meta name="citation_pdf_url" content="/files/b.pdf">'
    assert (
        pdf_url_from_html(relative, "https://pub.example/article/42")
        == "https://pub.example/files/b.pdf"
    )
    assert pdf_url_from_html(b"<html><body>no meta here</body></html>", "https://x.org") is None


def test_landing_candidate_resolves_to_pdf_via_citation_meta(tmp_path: Path) -> None:
    landing_html = b'<html><head><meta name="citation_pdf_url" content="https://pub.example/paper.pdf"></head><body>abstract page</body></html>'
    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher(
            {
                "https://pub.example/landing": FetchedBlob(
                    landing_html, "text/html", "https://pub.example/landing"
                ),
                "https://pub.example/paper.pdf": FetchedBlob(
                    b"%PDF-1.7 real full text", "application/pdf", "https://pub.example/paper.pdf"
                ),
            }
        ),
        extractor=StdlibTextExtractor(),
        store=LocalDocumentStore(tmp_path),
    )
    work = WorkRecord(
        id="W1", title="t", oa_status="hybrid", oa_landing_url="https://pub.example/landing"
    )
    result = service.acquire(work)
    assert result.status is AcquisitionStatus.RETRIEVED
    assert result.url == "https://pub.example/paper.pdf"
    assert result.content_type == "application/pdf"
    assert result.legal_basis is LegalBasis.OA_HYBRID


def test_landing_candidate_tries_an_alternate_pdf_after_identity_mismatch(tmp_path: Path) -> None:
    landing_html = b'<meta name="citation_pdf_url" content="https://pub.example/wrong.pdf"><a href="https://repo.example/right.pdf">Download accepted manuscript</a>'
    wrong = (
        "Distributed transaction recovery for storage engines. doi:10.9999/wrong. "
        + "Database replication and consistency. " * 80
    ).encode()
    right = (
        "Human oversight in automated literature reviews. doi:10.1000/right. "
        + "This study evaluates screening decisions and reviewer oversight. " * 80
    ).encode()
    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher(
            {
                "https://pub.example/landing": FetchedBlob(
                    landing_html, "text/html", "https://pub.example/landing"
                ),
                "https://pub.example/wrong.pdf": FetchedBlob(
                    wrong, "text/plain", "https://pub.example/wrong.pdf"
                ),
                "https://repo.example/right.pdf": FetchedBlob(
                    right, "text/plain", "https://repo.example/right.pdf"
                ),
            }
        ),
        extractor=StdlibTextExtractor(),
        store=LocalDocumentStore(tmp_path),
    )
    work = WorkRecord(
        id="W1",
        doi="10.1000/right",
        title="Human oversight in automated literature reviews",
        oa_status="green",
        oa_landing_url="https://pub.example/landing",
    )
    result = service.acquire(work)
    assert result.status is AcquisitionStatus.RETRIEVED
    assert result.url == "https://repo.example/right.pdf"


def test_landing_page_without_a_pdf_link_is_not_stored(tmp_path: Path) -> None:
    landing_html = b"<html><body>Just an abstract page, no PDF link, only navigation.</body></html>"
    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher(
            {"https://pub.example/landing": FetchedBlob(landing_html, "text/html", "u")}
        ),
        extractor=StdlibTextExtractor(),
        store=LocalDocumentStore(tmp_path),
    )
    work = WorkRecord(
        id="W1", title="t", oa_status="bronze", oa_landing_url="https://pub.example/landing"
    )
    result = service.acquire(work)
    assert result.status is AcquisitionStatus.NOT_RETRIEVED
    assert result.checksum is None


_ATOM_FEED = b'<?xml version="1.0"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n  <entry>\n    <id>http://arxiv.org/abs/2404.00227v2</id>\n    <title>A Survey of using Large Language Models for Generating Infrastructure as Code</title>\n    <published>2024-03-30T00:00:00Z</published>\n  </entry>\n</feed>'


class FakeArxiv:
    def __init__(self, match: ArxivMatch | None) -> None:
        self.match = match

    def find(self, title: str, year: int | None) -> ArxivMatch | None:
        return self.match


def test_best_match_accepts_same_paper_rejects_others() -> None:
    entries = [
        ArxivEntry(
            "2404.00227",
            "A Survey of using Large Language Models for Generating Infrastructure as Code",
            2024,
        ),
        ArxivEntry("1234.5678", "Deep reinforcement learning for robot locomotion", 2020),
    ]
    query = "A Survey of Using Large Language Models for Generating Infrastructure-as-Code"
    match = best_match(query, 2024, entries)
    assert match is not None and match.arxiv_id == "2404.00227" and (match.score >= 0.92)
    assert best_match("Quantum error correction thresholds", 2024, entries) is None


def test_best_match_rejects_year_mismatch() -> None:
    entries = [ArxivEntry("2404.00227", "A Survey of Large Language Models for IaC", 2016)]
    assert best_match("A Survey of Large Language Models for IaC", 2024, entries) is None


def test_parse_atom_extracts_id_title_year() -> None:
    entries = parse_atom(_ATOM_FEED)
    assert len(entries) == 1
    assert entries[0].arxiv_id == "2404.00227"
    assert entries[0].year == 2024


def test_arxiv_client_find_via_mock_transport() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_ATOM_FEED)

    client = ArxivClient(http=httpx.Client(transport=httpx.MockTransport(handler)))
    match = client.find(
        "A Survey of using Large Language Models for Generating Infrastructure as Code", 2024
    )
    assert match is not None and match.arxiv_id == "2404.00227"


def test_arxiv_client_resolves_exact_title_without_corrupt_year() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_ATOM_FEED)

    client = ArxivClient(http=httpx.Client(transport=httpx.MockTransport(handler)))
    entry = client.resolve_exact_title(
        "A Survey of using Large Language Models for Generating Infrastructure as Code"
    )
    assert entry is not None
    assert entry.arxiv_id == "2404.00227"
    assert entry.year == 2024


def test_arxiv_api_is_https() -> None:
    from sixsentences_server.acquisition.arxiv import ARXIV_API

    assert ARXIV_API.startswith("https://")
    assert ArxivClient().http.follow_redirects is True


def test_arxiv_fallback_recovers_a_closed_work(tmp_path: Path) -> None:
    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher(
            {
                "https://export.arxiv.org/pdf/2404.00227": FetchedBlob(
                    b"%PDF-1.7 real preprint",
                    "application/pdf",
                    "https://export.arxiv.org/pdf/2404.00227",
                )
            }
        ),
        extractor=StdlibTextExtractor(),
        store=LocalDocumentStore(tmp_path),
        arxiv=FakeArxiv(ArxivMatch("2404.00227", 0.98, "matched title")),
    )
    work = WorkRecord(id="W1", title="A Survey of LLMs for IaC", oa_status="closed")
    result = service.acquire(work)
    assert result.status is AcquisitionStatus.RETRIEVED
    assert result.source is DocumentSource.ARXIV
    assert result.legal_basis is LegalBasis.OA_GREEN
    assert result.note is not None and "fallback" in result.note


def test_arxiv_exact_title_fallback_ignores_a_corrupt_deposit_year(tmp_path: Path) -> None:

    class ExactArxiv(FakeArxiv):
        def resolve_exact_title(self, title: str) -> ArxivEntry:
            return ArxivEntry("1706.03762", title, 2017)

    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher(
            {
                "https://export.arxiv.org/pdf/1706.03762": FetchedBlob(
                    b"%PDF-1.7 attention paper",
                    "application/pdf",
                    "https://export.arxiv.org/pdf/1706.03762",
                )
            }
        ),
        extractor=StdlibTextExtractor(),
        store=LocalDocumentStore(tmp_path),
        arxiv=ExactArxiv(None),
    )
    work = WorkRecord(id="W1", title="Attention Is All You Need", year=2025, oa_status="closed")
    result = service.acquire(work)
    assert result.status is AcquisitionStatus.RETRIEVED
    assert result.source is DocumentSource.ARXIV
    assert result.url == "https://export.arxiv.org/pdf/1706.03762"


def test_arxiv_fallback_miss_stays_not_retrieved(tmp_path: Path) -> None:
    service = AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=FakeFetcher({}),
        extractor=StdlibTextExtractor(),
        store=LocalDocumentStore(tmp_path),
        arxiv=FakeArxiv(None),
    )
    work = WorkRecord(id="W1", title="A closed paper with no preprint", oa_status="closed")
    result = service.acquire(work)
    assert result.status is AcquisitionStatus.NOT_RETRIEVED


class _AlwaysRetrieve:
    """An Acquirer that reports every work retrieved — exercises the pipeline
    plumbing without depending on OA metadata in the fixture corpus."""

    def acquire(self, work: WorkRecord) -> AcquisitionResult:
        return AcquisitionResult(
            work_id=work.id,
            status=AcquisitionStatus.RETRIEVED,
            source=DocumentSource.ARXIV,
            legal_basis=LegalBasis.OA_GREEN,
            text_status=TextStatus.PARSED,
            checksum="deadbeef",
            byte_size=1,
        )


class _NeverRetrieve:
    """An Acquirer that keeps access failure separate from eligibility."""

    def acquire(self, work: WorkRecord) -> AcquisitionResult:
        return AcquisitionResult(
            work_id=work.id,
            status=AcquisitionStatus.NOT_RETRIEVED,
            text_status=TextStatus.NOT_RETRIEVED,
            reason="no open-access copy",
        )


def test_pipeline_acquisition_stage_feeds_prisma(corpus: DuckDBCorpus) -> None:
    init_db()

    def handler(model: str, prompt: str) -> str:
        if "Existing queries" in prompt:
            return '{"queries": []}'
        if "Distinct model reviewer votes" in prompt:
            return '{"verdict": "include", "reason": "adjudicated"}'
        if model.startswith("mock-cheap"):
            return '{"verdict": "include", "reason": "on topic"}'
        return '{"inclusion_criteria": ["x"], "exclusion_criteria": [], "query_string": "learning"}'

    pool = mock_pool(handler, screening_models=2)
    with db_session() as session:
        run = _run_with_works(session, [])
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            query_override="learning",
            screen=True,
            acquire=True,
            acquirer=_AlwaysRetrieve(),
        )
        included = result.prisma.included
        assert included > 0
        assert result.acquisition is not None
        assert result.acquisition.sought == included
        assert result.acquisition.retrieved == included
        assert result.prisma.reports_sought_for_retrieval == included
        assert result.prisma.reports_not_retrieved == 0
        events = [e.event for e in session.scalars(select(RunEvent)).all()]
        assert "acquisition_progress" in events
        assert "acquisition_done" in events
        docs = session.scalars(select(DocumentRow).where(DocumentRow.run_id == run.id)).all()
        assert len(docs) == included and all(d.status == "retrieved" for d in docs)


def test_final_paper_limit_is_applied_after_acquisition(corpus: DuckDBCorpus) -> None:
    init_db()

    def handler(model: str, prompt: str) -> str:
        if "Existing queries" in prompt:
            return '{"queries": []}'
        if "Distinct model reviewer votes" in prompt:
            return '{"verdict": "include", "reason": "adjudicated"}'
        if model.startswith("mock-cheap"):
            return '{"verdict": "include", "reason": "on topic"}'
        return '{"inclusion_criteria": ["x"], "exclusion_criteria": [], "query_string": "learning"}'

    with db_session() as session:
        run = _run_with_works(session, [])
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=mock_pool(handler, screening_models=2),
            query_override="learning",
            screen=True,
            paper_limit=1,
            acquire=True,
            acquirer=_AlwaysRetrieve(),
        )
        assert result.prisma.included > 1
        assert result.acquisition is not None
        assert result.acquisition.sought == result.prisma.included
        assert len(run.config["paper_selection_ids"]) == 1
        events = session.scalars(
            select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id)
        ).all()
        event_ids = {event.event: event.id for event in events}
        assert event_ids["acquisition_done"] < event_ids["output_set_finalized"]


def test_unavailable_full_texts_remain_in_final_candidate_set(
    corpus: DuckDBCorpus, tmp_path: Path
) -> None:
    init_db()

    def handler(model: str, prompt: str) -> str:
        if "Existing queries" in prompt:
            return '{"queries": []}'
        if "Distinct model reviewer votes" in prompt:
            return '{"verdict": "include", "reason": "adjudicated"}'
        if model.startswith("mock-cheap"):
            return '{"verdict": "include", "reason": "on topic"}'
        return '{"inclusion_criteria": ["x"], "exclusion_criteria": [], "query_string": "learning"}'

    with db_session() as session:
        run = _run_with_works(session, [])
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=mock_pool(handler, screening_models=2),
            query_override="learning",
            screen=True,
            paper_limit=2,
            acquire=True,
            full_text_screen=True,
            acquirer=_NeverRetrieve(),
            document_store=LocalDocumentStore(tmp_path),
        )
        assert result.acquisition is not None
        assert result.acquisition.not_retrieved == result.prisma.included
        assert result.fulltext is not None and result.fulltext.assessed == 0
        access_warning = next(
            warning
            for warning in result.quality_warnings
            if warning["code"] == "fulltext_access_gap"
        )
        assert access_warning["severity"] == "warning"
        assert str(result.acquisition.not_retrieved) in access_warning["detail"]
        assert "eligible candidates" in access_warning["detail"]
        selected_ids = run.config["paper_selection_ids"]
        assert len(selected_ids) == 2
        documents = session.scalars(
            select(DocumentRow).where(
                DocumentRow.run_id == run.id, DocumentRow.work_id.in_(selected_ids)
            )
        ).all()
        assert len(documents) == 2
        assert all(document.status == "not_retrieved" for document in documents)
        assert run.config["quality_warnings"] == result.quality_warnings


def test_title_abstract_unsure_records_advance_to_acquisition_and_output(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    with db_session() as session:
        run = _run_with_works(session, [])
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="learning",
            screen=True,
            paper_limit=2,
            acquire=True,
            acquirer=_NeverRetrieve(),
        )
        assert result.prisma.included == 0
        assert result.prisma.records_unsure > 0
        assert result.acquisition is not None
        assert result.acquisition.sought == result.prisma.records_unsure
        assert len(run.config["paper_selection_ids"]) == 2
