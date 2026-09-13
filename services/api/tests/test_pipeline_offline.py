"""End-to-end pipeline tests: fully offline, corpus-first, audited."""

import pytest
from sqlalchemy import select

from sixsentences_server.connectors.openalex import (
    OpenAlexClient,
    OpenAlexError,
    OpenAlexSearchPage,
)
from sixsentences_server.core import control
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.db import (
    Run,
    RunEvent,
    ScreeningDecisionRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.models import ReviewProtocol, Verdict, WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.base import PRICES_PER_MTOK, BudgetGovernor, ModelRef
from sixsentences_server.llm.mock import MockClient, mock_pool
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.pipeline.run import execute_run


def _make_run(session, question: str):  # type: ignore[no-untyped-def]
    org = get_default_org(session)
    project = ProjectRow(org_id=org.id, name="test")
    session.add(project)
    session.flush()
    run = Run(org_id=org.id, project_id=project.id, question=question, status="pending")
    session.add(run)
    session.flush()
    return run


def test_offline_run_prisma_consistency(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "transformer attention mechanisms")
        result = execute_run(session, run, corpus=corpus, query_override="transformer OR learning")

        prisma = result.prisma
        assert prisma.duplicates_removed == 0  # single query, no dupes
        assert prisma.retracted_flagged == 1  # W3 carries the corpus flag
        assert run.status == "completed"
        assert run.corpus_version == "micro-test-3"

        # every number must be reconstructable from run_events
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        names = [e.event for e in events]
        for expected in (
            "protocol_created",
            "queries_compiled",
            "corpus_search_done",
            "saturation_check",
            "dedup_done",
            "retraction_check_done",
            "ranking_done",
            "run_completed",
        ):
            assert expected in names
        dedup = next(e for e in events if e.event == "dedup_done")
        assert dedup.payload["identified"] == prisma.records_identified
        saturation = next(e for e in events if e.event == "saturation_check")
        assert saturation.payload["stopped_because"] == "expansion_disabled"

        # ranking: decomposed, ordered, and the retracted work is flagged not dropped
        assert result.ranked
        scores = [r.score for r in result.ranked]
        assert scores == sorted(scores, reverse=True)
        assert any(r.retracted for r in result.ranked)
        ranking = next(e for e in events if e.event == "ranking_done")
        assert ranking.payload["ranked"] >= len(result.ranked)  # total >= capped top view
        assert ranking.payload["top"][0]["signals"].keys() >= {"relevance", "impact", "recency"}

        # integrity: the retracted W3 is critical; W2 cites it -> zombie-citation warning
        integrity = next(e for e in events if e.event == "integrity_signals_done")
        assert integrity.payload["critical"] == 1
        assert integrity.payload["cites_retracted"] == 1
        assert any("W3" in report.cites_retracted for report in result.integrity_flags)

        # coverage: a single query has no overlap -> honestly undetermined
        assert result.coverage is not None
        assert result.coverage.method == "undetermined"
        assert any(e.event == "coverage_estimated" for e in events)


def test_query_override_keeps_synthesized_eligibility_contract(
    corpus: DuckDBCorpus,
) -> None:
    init_db()

    def handler(_model: str, prompt: str) -> str:
        if "Research question:" in prompt and "Existing queries:" not in prompt:
            return (
                '{"inclusion_criteria":["reports the target outcome"],'
                '"exclusion_criteria":["wrong population"],'
                '"eligibility_dimensions":["population","outcome"],'
                '"comparison_required":false,"comparison_note":"not required",'
                '"query_string":"generated"}'
            )
        if "Existing queries:" in prompt:
            return '{"queries":[]}'
        return '{"verdict":"include","reason":"eligible","quote":""}'

    with db_session() as session:
        run = _make_run(session, "target question")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=mock_pool(handler, screening_models=1),
            query_override="learning",
            screen=True,
        )

    assert result.protocol.query_string == "learning"
    assert result.protocol.inclusion_criteria == ["reports the target outcome"]
    assert result.protocol.exclusion_criteria == ["wrong population"]
    assert result.protocol.synthesized_by == "user"


def test_large_exhaustive_run_adds_audited_lexical_relaxation(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    corpus.write_meta(
        version="large-test",
        works=100_000,
        sources={"openalex": "fixture"},
    )
    query = '("active learner" OR "participant screening") AND (review OR classification)'

    with db_session() as session:
        run = _make_run(session, "active learning for review screening")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override=query,
            exhaustive=True,
        )
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()

    assert [item.work.id for item in result.ranked] == ["W2"]
    relaxed = next(event for event in events if event.event == "lexical_relaxation_done")
    assert relaxed.payload["new_unique"] == 1
    assert any(
        execution.source == "sixsentences-corpus/lexical-relaxation"
        for execution in result.search_executions
    )


def test_paper_limit_screens_all_then_persists_final_eligible_output(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "transformer attention mechanisms")
        run.config = {"paper_limit": 2}
        pool = mock_pool(
            lambda model, prompt: (
                '{"queries":[]}'
                if "Existing queries" in prompt
                else '{"verdict":"include","reason":"relevant","quote":""}'
            )
        )
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            query_override="transformer OR learning",
            screen=True,
            paper_limit=2,
        )

        selected_ids = run.config["paper_selection_ids"]
        assert len(result.decisions) > 2
        assert (
            selected_ids
            == [
                item.work.id
                for item in result.ranked
                if item.work.id in {decision.work_id for decision in result.decisions}
            ][:2]
        )
        assert len(selected_ids) == 2
        event = session.scalar(
            select(RunEvent).where(
                RunEvent.run_id == run.id,
                RunEvent.event == "output_set_finalized",
            )
        )
        assert event is not None
        assert event.payload["selected"] == 2
        assert event.payload["screened"] == len(result.decisions)
        assert event.payload["identified_unique"] >= 2


def test_expansion_loop_unions_results_and_saturates(corpus: DuckDBCorpus) -> None:
    init_db()

    def handler(model: str, prompt: str) -> str:
        if "Existing queries" in prompt:
            # one variant that matches new works, then saturation
            if '"screening"' in prompt or "screen" in prompt.lower():
                return '{"queries": []}'
            return '{"queries": ["screening OR survey"]}'
        return (
            '{"inclusion_criteria": ["x"], "exclusion_criteria": [], "query_string": "transformer"}'
        )

    pool = mock_pool(handler)
    with db_session() as session:
        run = _make_run(session, "attention models")
        result = execute_run(session, run, corpus=corpus, pool=pool, exhaustive=True)

        # base query found W1; the expansion variant added W2+W3
        assert len(result.queries_executed) == 2
        assert result.prisma.records_identified >= 3
        assert len(result.search_executions) == 2
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        rounds = [e for e in events if e.event == "expansion_round_done"]
        assert rounds and rounds[0].payload["new_unique_works"] == 2
        saturation = next(e for e in events if e.event == "saturation_check")
        assert saturation.payload["queries_executed"] == 2
        # two capture occasions -> a real Chao2 completeness estimate
        assert result.coverage is not None
        assert result.coverage.method == "chao2"
        assert result.coverage.occasions == 2


def test_live_retrieval_stops_after_relevance_saturates(
    corpus: DuckDBCorpus,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    init_db()
    yielded_pages: list[int] = []

    def pages(self, query_string, **kwargs):  # type: ignore[no-untyped-def]
        del self, query_string, kwargs
        page_titles = [
            "Transformer attention evaluation",
            "Transformer learning benchmark",
            "Unrelated dental procedure",
            "Unrelated marine ecology",
            "Transformer result that should not be fetched",
        ]
        for index, title in enumerate(page_titles, start=1):
            yielded_pages.append(index)
            yield OpenAlexSearchPage(
                records=[WorkRecord(id=f"WL{index}", title=title)],
                page_number=index,
                fetched=index,
                provider_total=20_000,
                next_cursor=f"c{index + 1}",
                has_more=True,
            )

    monkeypatch.setattr(OpenAlexClient, "iter_search_pages", pages)
    with db_session() as session:
        run = _make_run(session, "transformer attention mechanisms")
        execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            live=True,
        )
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        page_events = [event for event in events if event.event == "live_search_page"]
        done = next(event for event in events if event.event == "live_search_done")

        assert yielded_pages == [1, 2, 3, 4]
        assert len(page_events) == 4
        assert done.payload["stopped_because"] == "relevance_saturated"
        assert done.payload["truncated"] is False


def test_cancel_before_live_retrieval_avoids_provider_request(
    corpus: DuckDBCorpus,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    init_db()
    provider_called = False

    def pages(self, query_string, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal provider_called
        del self, query_string, kwargs
        provider_called = True
        yield OpenAlexSearchPage(
            records=[WorkRecord(id="WL1", title="Transformer")],
            page_number=1,
            fetched=1,
            provider_total=1,
            next_cursor="",
            has_more=False,
        )

    monkeypatch.setattr(OpenAlexClient, "iter_search_pages", pages)
    with db_session() as session:
        run = _make_run(session, "transformer")
        session.flush()
        control.request(run.id, control.CANCEL, org_id=run.org_id, session=session)
        try:
            execute_run(
                session,
                run,
                corpus=corpus,
                query_override="transformer",
                live=True,
            )
            events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
            control_event = next(event for event in events if event.event == "run_control")
            assert provider_called is False
            assert control_event.payload["checkpoint"] == "before_live_search"
            assert run.status == "cancelled"
        finally:
            control.clear(run.id, org_id=run.org_id, session=session)


def test_live_retrieval_reports_hard_cap_without_claiming_exhaustion(
    corpus: DuckDBCorpus,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    init_db()

    def pages(self, query_string, **kwargs):  # type: ignore[no-untyped-def]
        del self, query_string, kwargs
        for index in range(1, 3):
            yield OpenAlexSearchPage(
                records=[WorkRecord(id=f"WC{index}", title="Transformer attention")],
                page_number=index,
                fetched=index,
                provider_total=50_000,
                next_cursor=f"c{index + 1}",
                has_more=True,
            )

    monkeypatch.setattr(OpenAlexClient, "iter_search_pages", pages)
    with db_session() as session:
        run = _make_run(session, "transformer")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            live=True,
            live_limit=2,
        )
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        done = next(event for event in events if event.event == "live_search_done")

        assert done.payload["stopped_because"] == "hard_cap"
        assert done.payload["truncated"] is True
        assert any(warning["code"] == "live_results_capped" for warning in result.quality_warnings)


def test_live_provider_failure_keeps_partial_evidence_and_warns(
    corpus: DuckDBCorpus,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    init_db()

    def pages(self, query_string, **kwargs):  # type: ignore[no-untyped-def]
        del self, query_string, kwargs
        yield OpenAlexSearchPage(
            records=[WorkRecord(id="WPARTIAL", title="Transformer attention")],
            page_number=1,
            fetched=1,
            provider_total=2_000,
            next_cursor="c2",
            has_more=True,
        )
        raise OpenAlexError("simulated provider interruption")

    monkeypatch.setattr(OpenAlexClient, "iter_search_pages", pages)
    with db_session() as session:
        run = _make_run(session, "transformer")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            live=True,
        )
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        partial = next(event for event in events if event.event == "live_search_partial")
        done = next(event for event in events if event.event == "live_search_done")

        assert run.status == "completed"
        assert partial.payload["records_returned"] == 1
        assert done.payload["stopped_because"] == "provider_error"
        assert any(item.work.id == "WPARTIAL" for item in result.ranked)
        assert any(
            warning["code"] == "live_provider_interrupted" for warning in result.quality_warnings
        )


def test_live_provider_failure_before_first_page_is_not_silent(
    corpus: DuckDBCorpus,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    init_db()

    def pages(self, query_string, **kwargs):  # type: ignore[no-untyped-def]
        del self, query_string, kwargs
        raise OpenAlexError("simulated provider outage")
        yield  # pragma: no cover - keeps this a generator

    monkeypatch.setattr(OpenAlexClient, "iter_search_pages", pages)
    with db_session() as session:
        run = _make_run(session, "transformer")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer",
            live=True,
        )
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()

        assert run.status == "completed"
        assert any(event.event == "live_search_unavailable" for event in events)
        assert any(
            warning["code"] == "live_provider_interrupted" for warning in result.quality_warnings
        )


def test_screening_stub_never_excludes(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "anything")
        result = execute_run(session, run, corpus=corpus, query_override="learning", screen=True)
        assert result.decisions
        assert all(d.verdict is Verdict.UNSURE for d in result.decisions)
        assert result.prisma.records_excluded == 0


def test_screening_with_mock_ensemble(corpus: DuckDBCorpus) -> None:
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
        run = _make_run(session, "screening effort reduction via active learning")
        result = execute_run(session, run, corpus=corpus, pool=pool, screen=True)
        assert result.protocol.synthesized_by == "mock:mock-strong"
        assert result.prisma.included == result.prisma.records_screened > 0
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        done = next(e for e in events if e.event == "screening_done")
        assert done.payload["ensemble_size"] == 2
        assert done.payload["pairwise_agreement"] == 1.0

        # every ensemble decision carries a calibrated confidence
        assert all(d.confidence is not None for d in result.decisions)
        # capture-recapture recall certification is emitted and surfaced
        cert_ev = next(e for e in events if e.event == "screening_recall_certified")
        assert cert_ev.payload["reviewers"] == 2
        assert result.recall_certification is not None
        assert result.recall_certification.method == "chao2"


def test_perfect_gemini_model_agreement_does_not_certify_recall(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large synthetic same-vendor screen retains estimates, not a quality claim."""
    init_db()
    refs = [
        ModelRef("gemini", "gemini-3.1-flash-lite"),
        ModelRef("gemini", "gemini-3.5-flash-lite"),
    ]
    # This keyless test checks quality gating, not provider pricing or concurrent
    # top-up settlement. Explicitly give the mock responses zero synthetic cost.
    for ref in refs:
        monkeypatch.setitem(PRICES_PER_MTOK, ref.model, (0.0, 0.0))
    client = MockClient(
        lambda model, prompt: '{"verdict":"include","reason":"synthetic on-topic record"}'
    )
    pool = LLMPool(
        clients={"gemini": client},
        routing=RoutingConfig(screening=refs),
        budget=BudgetGovernor(limit_usd=10.0),
    )
    protocol = ReviewProtocol(
        question="Synthetic recall guard evaluation",
        query_string="sixqarecallguardnocorpusmatch",
        inclusion_criteria=["synthetic on-topic record"],
    )
    imported = [
        WorkRecord(
            id=f"synthetic-recall-{index}",
            title=f"Synthetic recall guard record {index}",
            abstract="This is a synthetic on-topic record for a keyless guard regression.",
        )
        for index in range(120)
    ]
    with db_session() as session:
        run = _make_run(session, protocol.question)
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            approved_protocol=protocol,
            imported=imported,
            screen=True,
            exhaustive=False,
        )

        assert run.status == "completed"
        assert result.prisma.records_screened == 120
        estimate = result.recall_certification
        assert estimate is not None
        assert estimate.method == "chao2"
        assert estimate.estimated_recall == estimate.ci_low == 1.0
        assert estimate.screened >= estimate.min_sample
        assert not estimate.certified
        assert "exploratory" in estimate.note
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        event = next(event for event in events if event.event == "screening_recall_certified")
        assert event.payload["certified"] is False
        assert event.payload["reviewers"] == 2
        assert any(
            warning["code"] == "screening_recall_not_certified"
            for warning in result.quality_warnings
        )


def test_budget_exhaustion_pauses_honestly(corpus: DuckDBCorpus) -> None:
    init_db()
    pool = mock_pool(
        lambda model, prompt: '{"verdict": "include", "reason": "r"}',
        budget=BudgetGovernor(limit_usd=0.0),  # trips on the first check
    )
    with db_session() as session:
        run = _make_run(session, "anything")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            query_override="learning",
            screen=True,
            exhaustive=True,
        )
        assert result.budget_paused is True
        assert result.prisma.records_screened == 0
        assert run.status == "paused"
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        names = [e.event for e in events]
        assert "budget_exhausted" in names
        assert "screening_paused" in names
        assert "screening_done" not in names
        saturation = next(e for e in events if e.event == "saturation_check")
        assert saturation.payload["stopped_because"] == "budget_exhausted"


def test_screening_provider_outage_pauses_instead_of_failing(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    pool = mock_pool(
        lambda model, prompt: (_ for _ in ()).throw(ProviderError("route unavailable"))
    )
    with db_session() as session:
        run = _make_run(session, "anything")
        execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            query_override="learning",
            screen=True,
            exhaustive=False,
        )

        assert run.status == "paused"
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        names = [event.event for event in events]
        assert "provider_unavailable" in names
        assert "screening_paused" in names
        assert "run_paused" in names
        assert "screening_done" not in names


def test_canary_recall_and_hit_calibration_are_reported(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "transformer")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="transformer OR learning",
            canary_ids=["W1", "W_absent"],
        )
        assert result.canary is not None
        assert result.canary.found == 1  # W1 is retrieved
        assert result.canary.missed == ["W_absent"]  # a provably missed must-hit
        assert result.hit_calibration is not None
        events = [
            e.event
            for e in session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        ]
        assert "query_calibration" in events and "canary_check" in events


def test_year_window_filters_retrieval_and_is_recorded(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "learning")
        result = execute_run(session, run, corpus=corpus, query_override="learning", year_from=2021)
        # "learning" matches W2 (2020) + W3 (2021); the window keeps only 2021+
        assert {r.work.id for r in result.ranked} == {"W3"}
        assert result.prisma.records_identified == 1
        # the window is documented in the PRISMA-S search appendix (limits)
        assert any("2021" in "; ".join(ex.limits) for ex in result.search_executions)


def test_peer_reviewed_filter_drops_preprints(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "learning")
        result = execute_run(
            session, run, corpus=corpus, query_override="learning", peer_reviewed_only=True
        )
        # "learning" matches W2 (article) + W3 (preprint); the filter drops W3
        assert {r.work.id for r in result.ranked} == {"W2"}
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        prf = next(e for e in events if e.event == "peer_review_filter")
        assert prf.payload["excluded"] == 1 and "W3" in prf.payload["excluded_ids"]


def test_cancel_signal_terminates_the_run(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "learning")
        session.flush()
        control.request(run.id, control.CANCEL, org_id=run.org_id, session=session)
        try:
            execute_run(session, run, corpus=corpus, query_override="learning", screen=True)
            assert run.status == "cancelled"  # stopped cooperatively, terminal
            events = [e.event for e in session.scalars(select(RunEvent)).all()]
            assert "run_cancelled" in events and "run_completed" not in events
        finally:
            control.clear(run.id, org_id=run.org_id, session=session)


def test_pause_signal_makes_the_run_resumable(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "learning")
        session.flush()
        control.request(run.id, control.PAUSE, org_id=run.org_id, session=session)
        try:
            execute_run(session, run, corpus=corpus, query_override="learning", screen=True)
            assert run.status == "paused"  # not terminal — can be resumed
        finally:
            control.clear(run.id, org_id=run.org_id, session=session)


@pytest.mark.parametrize("signal", [control.PAUSE, control.CANCEL])
def test_control_committed_at_final_checkpoint_beats_stale_worker_state(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, signal: str
) -> None:
    from sixsentences_server.core.models import StageName
    from sixsentences_server.pipeline.run import RunRecorder

    init_db()
    original_emit = RunRecorder.emit

    def emit_then_control(
        recorder: RunRecorder,
        stage: StageName,
        event: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        original_emit(recorder, stage, event, payload)
        if event == "search_executions":
            with db_session() as api_session:
                control.request(
                    recorder.run_id, signal, org_id=recorder.org_id, session=api_session
                )
                if signal == control.CANCEL:
                    persisted = api_session.get(Run, recorder.run_id)
                    assert persisted is not None
                    persisted.status = "cancelled"

    monkeypatch.setattr(RunRecorder, "emit", emit_then_control)
    with db_session() as session:
        run = _make_run(session, "learning")
        execute_run(session, run, corpus=corpus, query_override="learning")
        assert run.status == ("paused" if signal == control.PAUSE else "cancelled")
        assert control.poll(run.id, org_id=run.org_id) == signal
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        assert "run_completed" not in {item.event for item in events}


def test_screening_skips_already_decided_works_on_resume(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "learning")
        session.flush()
        # W2 was already screened in a prior (paused) session
        session.add(
            ScreeningDecisionRow(
                org_id=run.org_id,
                run_id=run.id,
                work_id="W2",
                reviewer="ensemble-or",
                verdict="include",
                reason="prior session",
            )
        )
        session.flush()
        result = execute_run(session, run, corpus=corpus, query_override="learning", screen=True)
        # W2 keeps its single prior decision (not re-screened); W3 is newly screened
        w2 = session.scalars(
            select(ScreeningDecisionRow).where(
                ScreeningDecisionRow.run_id == run.id, ScreeningDecisionRow.work_id == "W2"
            )
        ).all()
        assert len(w2) == 1  # no duplicate row
        assert result.prisma.records_screened == 2 and result.prisma.included >= 1


def test_budget_too_small_for_synthesis_degrades_to_heuristic(corpus: DuckDBCorpus) -> None:
    # a budget that trips on the very first call must fall back to the LLM-free
    # heuristic protocol (honest pause), not fail the whole run
    init_db()
    pool = mock_pool(lambda model, prompt: "{}", budget=BudgetGovernor(limit_usd=0.0))
    with db_session() as session:
        run = _make_run(session, "transformer attention mechanisms")
        result = execute_run(session, run, corpus=corpus, pool=pool)  # synthesis attempted
        assert run.status == "completed"  # not "failed"
        assert result.protocol.synthesized_by == "heuristic"
