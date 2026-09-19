"""Offline integration tests for the optional native PubMed retrieval arm."""

from typing import Any

import pytest
from sqlalchemy import select

from sixsentences_server.config import Settings
from sixsentences_server.connectors.pubmed import PubMedError, PubMedSearchResult
from sixsentences_server.core import control
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.db import (
    Run,
    RunEvent,
    SourceRecordRow,
    WorkRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.models import ReviewProtocol, WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.pipeline.run import _persist_run_sources, execute_run


def _make_run(session: Any, question: str) -> Run:
    org = get_default_org(session)
    project = ProjectRow(org_id=org.id, name="pubmed-test")
    session.add(project)
    session.flush()
    run = Run(org_id=org.id, project_id=project.id, question=question, status="pending")
    session.add(run)
    session.flush()
    return run


class _FakePubMedClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search_with_metadata(
        self,
        query: str,
        *,
        limit: int,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> PubMedSearchResult:
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "year_from": year_from,
                "year_to": year_to,
            }
        )
        records = [
            WorkRecord(
                id="pubmed:111",
                doi="10.1/alpha",
                pmid="111",
                pmcid="PMC111",
                title="Attention is all you need: transformer architectures",
                year=2017,
                source="pubmed",
            ),
            WorkRecord(
                id="pubmed:222",
                doi="10.2/pubmed",
                pmid="222",
                title="Transformer interventions in biomedical screening",
                abstract="A controlled evaluation of screening outcomes.",
                year=2022,
                source="pubmed",
            ),
        ]
        return PubMedSearchResult(
            records=records,
            provider_total=len(records),
            pmids_returned=len(records),
            truncated=False,
        )


class _UnavailablePubMedClient:
    def search_with_metadata(self, query: str, **_: object) -> PubMedSearchResult:
        del query
        raise PubMedError("simulated provider outage")


class _StaticPubMedClient:
    def __init__(self, records: list[WorkRecord]) -> None:
        self.records = records

    def search_with_metadata(self, query: str, **_: object) -> PubMedSearchResult:
        del query
        return PubMedSearchResult(
            records=self.records,
            provider_total=len(self.records),
            pmids_returned=len(self.records),
            truncated=False,
        )


class _RejectedPubMedClient:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def search_with_metadata(self, query: str, **_: object) -> PubMedSearchResult:
        del query
        raise PubMedError("safe simulated rejection", status_code=self.status_code)


def test_pubmed_runtime_requires_explicit_enablement_and_contact() -> None:
    assert Settings(pubmed_enabled=False, pubmed_email="research@example.org").pubmed_ready is False
    assert Settings(pubmed_enabled=True, pubmed_email="").pubmed_ready is False
    assert Settings(pubmed_enabled=True, pubmed_email="not-an-email").pubmed_ready is False
    assert Settings(pubmed_enabled=True, pubmed_email="operator@localhost").pubmed_ready is False
    assert Settings(pubmed_enabled=True, pubmed_email="operator@example.org.").pubmed_ready is False
    assert Settings(pubmed_enabled=True, pubmed_email="research@example.org").pubmed_ready is True


def test_product_pubmed_rate_gate_uses_shared_conservative_database_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sixsentences_server.pipeline import run as run_module

    created: list[dict[str, object]] = []

    class Limiter:
        def allow(self, key: str) -> bool:
            assert key == "deployment"
            return True

    def limiter_factory(**kwargs: object) -> Limiter:
        created.append(kwargs)
        return Limiter()

    monkeypatch.setattr(run_module, "DatabaseRateLimiter", limiter_factory)

    assert (
        run_module._pubmed_request_rate_gate(
            api_key="",
            database_backed=False,
        )
        is None
    )
    unkeyed = run_module._pubmed_request_rate_gate(api_key="", database_backed=True)
    keyed = run_module._pubmed_request_rate_gate(api_key="secret", database_backed=True)
    assert unkeyed is not None and keyed is not None
    unkeyed.acquire()
    keyed.acquire()

    assert created == [
        {
            "scope": "pubmed-egress-unkeyed-v1",
            "max_hits": 1,
            "window_seconds": 0.5,
        },
        {
            "scope": "pubmed-egress-keyed-v1",
            "max_hits": 1,
            "window_seconds": 0.125,
        },
    ]


def test_pubmed_results_join_the_canonical_screening_path(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    client = _FakePubMedClient()
    with db_session() as session:
        run = _make_run(session, "transformer screening")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            pubmed=True,
            pubmed_limit=50,
            pubmed_client=client,  # type: ignore[arg-type]
            year_from=2010,
            year_to=2024,
            screen=True,
        )
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        sources = session.scalars(
            select(SourceRecordRow).where(SourceRecordRow.run_id == run.id)
        ).all()

    assert client.calls == [
        {
            "query": "transformer[tiab]",
            "limit": 50,
            "year_from": 2010,
            "year_to": 2024,
        }
    ]
    assert result.prisma.records_identified == 3
    assert result.prisma.duplicates_removed == 1
    assert {decision.work_id for decision in result.decisions} == {"W1", "pubmed:222"}
    execution = next(item for item in result.search_executions if item.source == "pubmed-live")
    assert execution.records_returned == 2
    effective_query = "(transformer[tiab]) AND 2010:2024[pdat]"
    assert execution.query_verbatim == effective_query
    done = next(event for event in events if event.event == "pubmed_search_done")
    assert done.payload["query_verbatim"] == effective_query
    assert done.payload["new_unique"] == 1
    assert done.payload["at_cap"] is False
    assert {(row.work_id, row.source, row.corpus_version) for row in sources} >= {
        ("W1", "pubmed-live", "eutils.ncbi.nlm.nih.gov"),
        ("pubmed:222", "pubmed-live", "eutils.ncbi.nlm.nih.gov"),
    }
    assert all(row.source != "pubmed" for row in sources)


def test_paused_resume_keeps_one_cumulative_retrieval_snapshot(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    first_record = WorkRecord(
        id="pubmed:401",
        pmid="401",
        title="First checkpointed biomedical study",
        year=2021,
        source="pubmed",
    )
    second_record = WorkRecord(
        id="pubmed:402",
        pmid="402",
        title="Second resumed biomedical study",
        year=2022,
        source="pubmed",
    )
    with db_session() as session:
        run = _make_run(session, "transformer screening")
        first = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            pubmed=True,
            pubmed_client=_StaticPubMedClient([first_record]),  # type: ignore[arg-type]
        )
        run.status = "paused"
        second = execute_run(
            session,
            run,
            corpus=corpus,
            approved_protocol=first.protocol,
            pubmed=True,
            pubmed_client=_StaticPubMedClient([second_record]),  # type: ignore[arg-type]
            resume_from_checkpoint=True,
        )
        source_ids = set(
            session.scalars(
                select(SourceRecordRow.work_id).where(SourceRecordRow.run_id == run.id)
            ).all()
        )
        coverage_events = session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run.id, RunEvent.event == "coverage_estimated")
            .order_by(RunEvent.id)
        ).all()

    assert {"pubmed:401", "pubmed:402"} <= source_ids
    assert second.prisma.records_identified - second.prisma.duplicates_removed == len(source_ids)
    assert sum(execution.source == "pubmed-live" for execution in second.search_executions) == 2
    assert coverage_events[-1].payload["scope"] == "resume_attempt"
    assert coverage_events[-1].payload["prior_queries_excluded"] == 1
    assert coverage_events[-1].payload["occasions"] == 1


def test_repeated_source_preserves_append_only_corpus_versions() -> None:
    init_db()
    record = WorkRecord(id="W-versioned", title="Versioned work")
    with db_session() as session:
        run = _make_run(session, "versioned provenance")
        _persist_run_sources(
            session,
            run,
            [record],
            source="sixsentences-corpus",
            corpus_version="snapshot-a",
        )
        _persist_run_sources(
            session,
            run,
            [record],
            source="sixsentences-corpus",
            corpus_version="snapshot-b",
        )
        _persist_run_sources(
            session,
            run,
            [record],
            source="sixsentences-corpus",
            corpus_version="snapshot-b",
        )
        rows = session.scalars(
            select(SourceRecordRow).where(
                SourceRecordRow.run_id == run.id,
                SourceRecordRow.work_id == record.id,
            )
        ).all()

    assert {(row.source, row.corpus_version) for row in rows} == {
        ("sixsentences-corpus", "snapshot-a"),
        ("sixsentences-corpus", "snapshot-b"),
    }
    assert len(rows) == 2


def test_resume_does_not_relabel_checkpoint_only_work_as_current_snapshot(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    checkpoint_only = WorkRecord(id="W-checkpoint-only", title="Unrelated checkpoint record")
    with db_session() as session:
        run = _make_run(session, "transformer screening")
        _persist_run_sources(
            session,
            run,
            [checkpoint_only],
            source="sixsentences-corpus",
            corpus_version="snapshot-a",
        )
        run.status = "paused"
        run.prisma = {"records_identified": 1}
        corpus.write_meta(
            version="snapshot-b",
            works=3,
            sources={"openalex": "fixture"},
        )

        execute_run(
            session,
            run,
            corpus=corpus,
            approved_protocol=ReviewProtocol(
                question=run.question,
                query_string="transformer",
            ),
            resume_from_checkpoint=True,
        )
        rows = session.scalars(
            select(SourceRecordRow).where(
                SourceRecordRow.run_id == run.id,
                SourceRecordRow.work_id == checkpoint_only.id,
            )
        ).all()

    assert [(row.source, row.corpus_version) for row in rows] == [
        ("sixsentences-corpus", "snapshot-a")
    ]


def test_pubmed_failure_is_audited_without_an_empty_success(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "transformer screening")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            pubmed=True,
            pubmed_client=_UnavailablePubMedClient(),  # type: ignore[arg-type]
        )
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()

    names = [event.event for event in events]
    assert "pubmed_search_unavailable" in names
    assert "pubmed_search_done" not in names
    failed_execution = next(
        item for item in result.search_executions if item.source == "pubmed-live"
    )
    assert failed_execution.status == "failed"
    assert failed_execution.failure_reason == "provider_temporarily_unavailable"
    assert failed_execution.records_returned == 0
    assert any(
        warning["code"] == "pubmed_provider_unavailable" for warning in result.quality_warnings
    )


def test_final_dedup_remaps_pubmed_provenance_without_ghost_source_rows(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    duplicate = WorkRecord(
        id="pubmed:333",
        pmid="333",
        title="Attention is all you need: transformer architectures",
        source="pubmed",
    )
    with db_session() as session:
        run = _make_run(session, "transformer screening")
        session.add(
            WorkRow(
                id="pubmed:333",
                title=duplicate.title,
                payload=duplicate.model_dump(mode="json"),
            )
        )
        session.flush()
        session.add(
            SourceRecordRow(
                org_id=run.org_id,
                run_id=run.id,
                work_id="pubmed:333",
                source="checkpointed-pubmed",
                corpus_version="prior-checkpoint",
            )
        )
        session.flush()
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            pubmed=True,
            pubmed_client=_StaticPubMedClient([duplicate]),  # type: ignore[arg-type]
            screen=True,
        )
        sources = session.scalars(
            select(SourceRecordRow).where(SourceRecordRow.run_id == run.id)
        ).all()

    assert {decision.work_id for decision in result.decisions} == {"W1"}
    assert {(row.work_id, row.source, row.corpus_version) for row in sources} == {
        ("W1", "sixsentences-corpus", "micro-test-3"),
        ("W1", "pubmed-live", "eutils.ncbi.nlm.nih.gov"),
        ("W1", "checkpointed-pubmed", "prior-checkpoint"),
    }
    assert all(row.work_id != "pubmed:333" for row in sources)


def test_pubmed_enrichment_updates_existing_work_non_destructively(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "transformer screening")
        session.add(
            WorkRow(
                id="W1",
                doi="10.1/alpha",
                title="Attention is all you need: transformer architectures",
                year=2017,
                payload={
                    "id": "W1",
                    "doi": "10.1/alpha",
                    "title": "Attention is all you need: transformer architectures",
                    "abstract": "Existing richer abstract must survive.",
                    "year": 2017,
                    "authors": ["Existing Author"],
                    "cited_by_count": 100_000,
                    "is_retracted": False,
                },
            )
        )
        session.flush()
        enriched = WorkRecord(
            id="pubmed:111",
            doi="10.1/alpha",
            pmid="111",
            pmcid="PMC111",
            title="Attention is all you need: transformer architectures",
            abstract="Provider abstract must not replace richer persisted metadata.",
            year=2017,
            authors=["Provider Author"],
            cited_by_count=5,
            is_retracted=True,
            source="pubmed",
        )
        execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            pubmed=True,
            pubmed_client=_StaticPubMedClient([enriched]),  # type: ignore[arg-type]
        )
        row = session.get(WorkRow, "W1")

    assert row is not None
    assert row.payload["abstract"] == "Existing richer abstract must survive."
    assert row.payload["authors"] == ["Existing Author"]
    assert row.payload["cited_by_count"] == 100_000
    assert row.payload["pmid"] == "111"
    assert row.payload["pmcid"] == "PMC111"
    assert row.payload["is_retracted"] is True


def test_pubmed_error_checkpoint_observes_a_concurrent_cancellation(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "transformer screening")
        run_id = run.id
        org_id = run.org_id

        class _CancellingClient:
            def search_with_metadata(self, query: str, **_: object) -> PubMedSearchResult:
                del query
                with db_session() as control_session:
                    control.request(
                        run_id,
                        control.CANCEL,
                        org_id=org_id,
                        session=control_session,
                    )
                raise PubMedError("simulated provider outage")

        try:
            execute_run(
                session,
                run,
                corpus=corpus,
                query_override="transformer",
                pubmed=True,
                pubmed_client=_CancellingClient(),  # type: ignore[arg-type]
            )
            events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        finally:
            control.clear(run.id, org_id=run.org_id, session=session)

    checkpoint = next(event for event in events if event.event == "run_control")
    assert checkpoint.payload == {
        "signal": "cancel",
        "checkpoint": "after_pubmed_search",
        "records_returned": 0,
    }
    assert run.status == "cancelled"


def test_pubmed_failure_classifies_permanent_and_transient_http_errors(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    observed: list[tuple[str, bool]] = []
    for status_code in (400, 429):
        with db_session() as session:
            run = _make_run(session, f"transformer screening {status_code}")
            execute_run(
                session,
                run,
                corpus=corpus,
                query_override="transformer",
                pubmed=True,
                pubmed_client=_RejectedPubMedClient(status_code),  # type: ignore[arg-type]
            )
            event = session.scalar(
                select(RunEvent).where(
                    RunEvent.run_id == run.id,
                    RunEvent.event == "pubmed_search_unavailable",
                )
            )
            assert event is not None
            observed.append((str(event.payload["reason"]), bool(event.payload["retryable"])))

    assert observed == [
        ("provider_request_rejected", False),
        ("provider_temporarily_unavailable", True),
    ]
