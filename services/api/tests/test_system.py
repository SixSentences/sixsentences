"""System benchmark: drive the whole product end-to-end in two passes.

1. The SaaS surface over HTTP: accounts, plans/entitlements, a run's lifecycle,
   the audit log + SSE stream, methods, usage, exports, documents, tenancy.
2. The deep evidence pipeline with a mock LLM: title/abstract includes with
   verified quotes -> full-text acquisition -> full-text eligibility -> grounded
   chat with the NLI claim firewall — the whole chain wired together.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.acquisition.models import (
    AcquisitionResult,
    AcquisitionStatus,
    DocumentSource,
    LegalBasis,
    TextStatus,
)
from sixsentences_server.acquisition.store import LocalDocumentStore
from sixsentences_server.chat.service import answer_question
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.db import (
    Run,
    ScreeningDecisionRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.pipeline.run import execute_run

_FULL = "This study evaluates LLM-generated Terraform against a reference suite of 100 modules with a rigorous methodology and reports strong accuracy results across cloud providers."


def _authed(app: FastAPI, email: str, org: str) -> TestClient:
    client = TestClient(app)
    body = {"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"}
    resp = client.post("/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return client


def _handler(model: str, prompt: str) -> str:
    if "Existing queries" in prompt:
        return '{"queries": []}'
    if "Full text of" in prompt:
        return '{"verdict":"include","reason":"evaluates LLM IaC","quote":"evaluates LLM-generated Terraform against a reference suite"}'
    if "Distinct model reviewer votes" in prompt:
        return '{"verdict":"include","reason":"adjudicated"}'
    if "CLAIM:" in prompt:
        return '{"label":"supported","reason":"entailed by the source"}'
    if "Sources" in prompt:
        return "Active learning reduces screening effort [W2]."
    if model.startswith("mock-cheap"):
        return '{"verdict":"include","reason":"on topic","quote":"Active learning reduces screening effort"}'
    return '{"inclusion_criteria":[],"exclusion_criteria":[],"query_string":"learning"}'


class _FakeAcquirer:
    def acquire(self, work: WorkRecord) -> AcquisitionResult:
        return AcquisitionResult(
            work_id=work.id,
            status=AcquisitionStatus.RETRIEVED,
            source=DocumentSource.ARXIV,
            legal_basis=LegalBasis.OA_GREEN,
            text_status=TextStatus.PARSED,
            checksum="ck",
            byte_size=1,
        )


def _make_run(session: Session) -> Run:
    org = get_default_org(session)
    project = ProjectRow(org_id=org.id, name="deep")
    session.add(project)
    session.flush()
    run = Run(org_id=org.id, project_id=project.id, question="active learning", status="pending")
    session.add(run)
    session.flush()
    return run


def test_system_deep_evidence_pipeline(corpus: DuckDBCorpus, tmp_path: Path) -> None:
    init_db()
    store = LocalDocumentStore(tmp_path)
    store.put_text("ck", _FULL)
    pool = mock_pool(_handler, screening_models=2)
    with db_session() as session:
        run = _make_run(session)
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            query_override="learning",
            screen=True,
            acquire=True,
            full_text_screen=True,
            acquirer=_FakeAcquirer(),
            document_store=store,
        )
        assert result.prisma.included > 0
        assert any(d.quote for d in result.decisions)
        assert result.acquisition is not None
        assert result.acquisition.retrieved == result.prisma.included
        assert result.fulltext is not None
        assert result.fulltext.included == result.prisma.studies_included > 0
        assert result.fulltext.quotes_verified > 0
        ft_decisions = session.scalars(
            select(ScreeningDecisionRow).where(
                ScreeningDecisionRow.run_id == run.id,
                ScreeningDecisionRow.reviewer.like("fulltext:%"),
            )
        ).all()
        assert len(ft_decisions) == result.fulltext.assessed
        answer = answer_question(session, run, pool, "What reduces screening effort?")
        assert answer.citations and answer.citations[0].id == "W2"
        assert answer.claims_checked >= 1 and answer.claims_supported >= 1
