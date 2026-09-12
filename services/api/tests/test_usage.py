"""LLM usage / cost accounting is persisted per run."""

from sqlalchemy import select

from sixsentences_server.core.db import LLMCallRow, Run, db_session, get_default_org, init_db
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.pipeline.run import execute_run
from sixsentences_server.reporting.usage import usage_for_run


def _make_run(session, question: str):  # type: ignore[no-untyped-def]
    org = get_default_org(session)
    project = ProjectRow(org_id=org.id, name="test")
    session.add(project)
    session.flush()
    run = Run(org_id=org.id, project_id=project.id, question=question, status="pending")
    session.add(run)
    session.flush()
    return run


def test_llm_calls_persisted_and_aggregated(corpus: DuckDBCorpus) -> None:
    init_db()

    def handler(model: str, prompt: str) -> str:
        if "Existing queries" in prompt:
            return '{"queries": []}'
        if model.startswith("mock-cheap"):
            return '{"verdict": "include", "reason": "on topic"}'
        return '{"inclusion_criteria": ["x"], "exclusion_criteria": [], "query_string": "learning"}'

    pool = mock_pool(handler, screening_models=2)
    with db_session() as session:
        run = _make_run(session, "active learning for screening")
        execute_run(session, run, corpus=corpus, pool=pool, screen=True)

        rows = session.scalars(select(LLMCallRow).where(LLMCallRow.run_id == run.id)).all()
        assert rows, "each LLM call must be persisted"
        assert all(r.org_id == run.org_id for r in rows)

        summary = usage_for_run(session, run.id)
        assert summary.total_calls == len(rows)
        assert summary.total_input_tokens > 0  # mock reports token proxies
        assert summary.total_cost_usd == 0.0  # mock models are free
        # synthesis and screening are distinct tasks in the breakdown
        assert "protocol_synthesis" in summary.by_task
        assert "screening" in summary.by_task
        assert "mock" in summary.by_provider


def test_usage_empty_for_run_without_llm(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        run = _make_run(session, "anything")
        execute_run(session, run, corpus=corpus, query_override="learning")  # no pool
        summary = usage_for_run(session, run.id)
        assert summary.total_calls == 0
        assert summary.total_cost_usd == 0.0
