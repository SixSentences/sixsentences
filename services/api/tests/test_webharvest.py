"""Web harvest: papers recovered from web results join the academic arm."""

from sqlalchemy import select

from sixsentences_server.connectors.webharvest import (
    HarvestResult,
    extract_scholarly_refs,
    harvest_works,
)
from sixsentences_server.connectors.websearch import WebSource
from sixsentences_server.core.db import (
    Project as ProjectRow,
)
from sixsentences_server.core.db import (
    Run,
    SourceRecordRow,
    WebSourceRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.pipeline.run import execute_run


def _src(url: str, domain: str = "example.org") -> WebSource:
    return WebSource(title="t", url=url, snippet="", domain=domain, score=0.5)


def test_sanitize_search_text_strips_wildcards() -> None:
    # OpenAlex 400s on * and ? in stemmed search; questions end in ? routinely
    from sixsentences_server.connectors.openalex import sanitize_search_text

    assert (
        sanitize_search_text("What is happening in IaC research?")
        == "What is happening in IaC research"
    )
    assert sanitize_search_text("transformer* attention?") == "transformer  attention"


def test_extract_scholarly_refs_finds_arxiv_and_dois() -> None:
    sources = [
        _src("https://arxiv.org/abs/2301.12345v2", "arxiv.org"),
        _src("https://doi.org/10.1145/3510003.3510101", "doi.org"),
        _src("https://dl.acm.org/doi/10.1145/3510003.3510101", "dl.acm.org"),  # same DOI
        _src("https://doi.org/10.48550/arXiv.2409.00001", "doi.org"),  # arXiv DOI
        _src("https://example.com/blog/iac-trends", "example.com"),
    ]
    refs = extract_scholarly_refs(sources)
    kinds = [(r.kind, r.value) for r in refs]
    assert ("arxiv", "2301.12345") in kinds  # version suffix stripped
    assert ("doi", "10.1145/3510003.3510101") in kinds
    assert ("arxiv", "2409.00001") in kinds  # 10.48550 resolves via arXiv id
    assert len(refs) == 3  # duplicate DOI and the blog do not count


def test_harvest_resolves_dedups_and_reports_unresolved() -> None:
    class _FakeOA:
        def get_work(self, external_id: str) -> WorkRecord | None:
            if external_id == "arxiv:2301.12345":
                return WorkRecord(id="W100", title="Paper A")
            if external_id == "doi:10.1145/3510003.3510101":
                return WorkRecord(id="W200", title="Paper B")
            return None

    sources = [
        _src("https://arxiv.org/abs/2301.12345v2", "arxiv.org"),
        _src("https://doi.org/10.1145/3510003.3510101", "doi.org"),
        _src("https://arxiv.org/abs/2409.99999", "arxiv.org"),  # unknown to OpenAlex
    ]
    result = harvest_works(sources, _FakeOA())  # type: ignore[arg-type]
    assert result.candidates == 3 and result.resolved == 2
    assert {w.id for w in result.works} == {"W100", "W200"}
    assert all(w.source == "websearch" for w in result.works)
    assert result.unresolved == ["arxiv:2409.99999"]
    assert "https://arxiv.org/abs/2301.12345v2" in result.resolved_urls


class _PaperWeb:
    """Fake web searcher whose results include one paper link and one blog."""

    def search(self, query: str, *, max_results: int = 10) -> list[WebSource]:
        return [
            _src("https://arxiv.org/abs/2301.12345", "arxiv.org"),
            _src("https://blog.example.com/post", "blog.example.com"),
        ]


def test_pipeline_moves_harvested_papers_into_academic_arm(
    corpus: DuckDBCorpus, monkeypatch
) -> None:
    harvested = WorkRecord(id="W900", title="Harvested paper", source="websearch")
    monkeypatch.setattr(
        "sixsentences_server.pipeline.run.harvest_works",
        lambda sources, client, **kw: HarvestResult(
            works=[harvested],
            candidates=1,
            resolved=1,
            unresolved=[],
            resolved_urls={"https://arxiv.org/abs/2301.12345"},
        ),
    )
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="w")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="terraform iac", status="pending")
        session.add(run)
        session.flush()
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="learning",
            web_search=True,
            web_searcher=_PaperWeb(),
        )
        # the paper entered the academic arm and is counted as "other methods"
        assert result.prisma.other_identified == 1
        records = session.scalars(
            select(SourceRecordRow).where(SourceRecordRow.run_id == run.id)
        ).all()
        assert any(r.work_id == "W900" and r.source == "websearch" for r in records)
        # the resolved paper is no longer listed as grey literature; the blog is
        grey = session.scalars(select(WebSourceRow).where(WebSourceRow.run_id == run.id)).all()
        assert [g.domain for g in grey] == ["blog.example.com"]
