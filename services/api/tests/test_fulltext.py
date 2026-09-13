"""Full-text screening: the second SLR pass (eligibility on the parsed full text)."""

from pathlib import Path

import pytest
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
from sixsentences_server.llm.base import TaskType
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.pipeline.run import execute_run
from sixsentences_server.screening.fulltext import screen_full_text

_PROTO = ReviewProtocol(
    question="Do LLMs generate correct IaC?",
    inclusion_criteria=["empirically evaluates LLM-generated infrastructure as code"],
    exclusion_criteria=["not about infrastructure as code"],
    query_string="x",
)
_WORK = WorkRecord(id="W1", title="LLMs generate Terraform")
_FULL = (
    "This study evaluates LLM-generated Terraform against a reference suite of 100 modules "
    "with a rigorous methodology and reports strong accuracy results across providers."
)


# --- screen_full_text unit ----------------------------------------------


@pytest.mark.parametrize("reply", ["[]", "null", "42", "true", '"exclude"'])
def test_non_object_fulltext_response_is_unsure_with_usage_preserved(reply: str) -> None:
    pool = mock_pool(lambda model, prompt: reply)

    out = screen_full_text(_WORK, _PROTO, _FULL, pool)

    assert out.verdict is Verdict.UNSURE
    assert out.reason == (
        "The reviewer response could not be validated; the record requires further review."
    )
    assert out.quote is None
    assert not out.quote_verified
    assert pool.budget.calls == 2
    assert len(pool.usage) == 2
    assert all(usage.task == TaskType.FULL_TEXT_SCREENING.value for usage in pool.usage)


def test_include_carries_a_quote_verified_against_full_text() -> None:
    pool = mock_pool(
        lambda m, p: (
            '{"verdict":"include","reason":"empirically evaluates LLM IaC",'
            '"quote":"evaluates LLM-generated Terraform against a reference suite"}'
        )
    )
    out = screen_full_text(_WORK, _PROTO, _FULL, pool)
    assert out.verdict is Verdict.INCLUDE
    assert out.quote_verified and out.quote  # a real span of the FULL TEXT
    assert out.reviewer == "fulltext:mock:mock-strong"


def test_exclude_records_a_reason() -> None:
    reply = (
        '{"verdict":"exclude","reason":"the study evaluates no infrastructure as code",'
        '"quote":"reports strong accuracy results across providers"}'
    )
    out = screen_full_text(_WORK, _PROTO, _FULL, mock_pool(lambda m, p: reply))
    assert out.verdict is Verdict.EXCLUDE and "infrastructure" in out.reason


def test_structured_criteria_drive_the_verdict_deterministically() -> None:
    reply = (
        '{"criteria":['
        '{"index":0,"status":"yes","reason":"empirical evaluation",'
        '"quote":"evaluates LLM-generated Terraform against a reference suite"},'
        '{"index":1,"status":"yes","reason":"exclusion does not apply","quote":""}'
        "]}"
    )
    protocol = ReviewProtocol(
        question=_PROTO.question,
        inclusion_criteria=_PROTO.inclusion_criteria,
        exclusion_criteria=["not about infrastructure as code"],
        query_string="x",
    )

    out = screen_full_text(_WORK, protocol, _FULL, mock_pool(lambda m, p: reply))

    assert out.verdict is Verdict.INCLUDE
    assert len(out.criteria) == 2
    assert all(item.status == "yes" for item in out.criteria)


def test_literal_exclusion_applicability_maps_to_eligibility_failure() -> None:
    full_text = _FULL + " This is an editorial without an empirical evaluation."
    reply = (
        '{"criteria":['
        '{"index":0,"applies":"yes","reason":"empirical topic is present",'
        '"quote":"evaluates LLM-generated Terraform against a reference suite"},'
        '{"index":1,"applies":"yes","reason":"the exclusion condition applies",'
        '"quote":"This is an editorial without an empirical evaluation"}'
        "]}"
    )
    protocol = ReviewProtocol(
        question=_PROTO.question,
        inclusion_criteria=_PROTO.inclusion_criteria,
        exclusion_criteria=["editorials without an empirical evaluation"],
        query_string="x",
    )

    out = screen_full_text(_WORK, protocol, full_text, mock_pool(lambda m, p: reply))

    assert out.verdict is Verdict.EXCLUDE
    assert out.criteria[1].status == "no"
    assert out.criteria[1].quote_verified is True


def test_literal_non_applicable_exclusion_maps_to_eligibility_pass() -> None:
    reply = (
        '{"criteria":['
        '{"index":0,"applies":"yes","reason":"empirical evaluation",'
        '"quote":"evaluates LLM-generated Terraform against a reference suite"},'
        '{"index":1,"applies":"no","reason":"the study is about IaC","quote":""}'
        "]}"
    )

    out = screen_full_text(_WORK, _PROTO, _FULL, mock_pool(lambda m, p: reply))

    assert out.verdict is Verdict.INCLUDE
    assert out.criteria[1].status == "yes"


def test_structured_inclusion_without_support_becomes_unsure() -> None:
    reply = (
        '{"criteria":[{"index":0,"status":"yes","reason":"claimed",'
        '"quote":"a passage that does not exist"}]}'
    )

    out = screen_full_text(_WORK, _PROTO, _FULL, mock_pool(lambda m, p: reply))

    assert out.verdict is Verdict.UNSURE
    assert out.criteria[0].status == "unclear"


def test_unverified_exclusion_becomes_unsure() -> None:
    reply = '{"verdict":"exclude","reason":"the required comparison is not reported","quote":""}'
    out = screen_full_text(_WORK, _PROTO, _FULL, mock_pool(lambda m, p: reply))

    assert out.verdict is Verdict.UNSURE
    assert out.quote is None
    assert "not supported" in out.reason


def test_fabricated_quote_downgrades_definitive_verdict() -> None:
    pool = mock_pool(
        lambda m, p: (
            '{"verdict":"include","reason":"r",'
            '"quote":"quantum entanglement in superconducting qubits at millikelvin"}'
        )
    )
    out = screen_full_text(_WORK, _PROTO, _FULL, pool)
    assert out.verdict is Verdict.UNSURE
    assert out.quote is None and out.quote_verified is False  # invented quote not stored


def test_malformed_response_is_unsure_never_silent_exclude() -> None:
    out = screen_full_text(_WORK, _PROTO, _FULL, mock_pool(lambda m, p: "not json at all"))
    assert out.verdict is Verdict.UNSURE


def test_full_text_is_read_not_just_the_abstract() -> None:
    seen: list[str] = []

    def handler(model: str, prompt: str) -> str:
        seen.append(prompt)
        return '{"verdict":"unsure","reason":"x","quote":""}'

    screen_full_text(_WORK, _PROTO, _FULL, mock_pool(handler))
    assert "reference suite of 100 modules" in seen[-1]  # the body reached the model


def test_full_text_window_reaches_late_eligibility_sections() -> None:
    seen: list[str] = []
    long_text = (
        "Front matter.\n"
        + ("Background context. " * 200)
        + "\nMETHODS\nLate method eligibility detail.\n"
        + ("Method filler. " * 100)
        + "\nRESULTS\nLate result eligibility detail.\n"
        + ("Result filler. " * 100)
        + "\nCONCLUSION\nLate conclusion eligibility detail."
    )

    def handler(model: str, prompt: str) -> str:
        seen.append(prompt)
        return '{"verdict":"unsure","reason":"x","quote":""}'

    screen_full_text(_WORK, _PROTO, long_text, mock_pool(handler), text_budget=900)

    assert "Late method eligibility detail" in seen[-1]
    assert "Late result eligibility detail" in seen[-1]
    assert "Late conclusion eligibility detail" in seen[-1]


def test_full_text_uses_screening_fallback_when_strong_route_fails() -> None:
    def handler(model: str, prompt: str) -> str:
        if model == "mock-strong":
            raise ProviderError("strong route unavailable")
        return (
            '{"verdict":"include","reason":"eligible",'
            '"quote":"evaluates LLM-generated Terraform against a reference suite"}'
        )

    out = screen_full_text(_WORK, _PROTO, _FULL, mock_pool(handler))

    assert out.verdict is Verdict.INCLUDE
    assert out.reviewer == "fulltext:mock:mock-cheap-0"


def test_full_text_becomes_unsure_when_every_route_fails() -> None:
    def handler(model: str, prompt: str) -> str:
        raise ProviderError("route unavailable")

    out = screen_full_text(_WORK, _PROTO, _FULL, mock_pool(handler))

    assert out.verdict is Verdict.UNSURE
    assert out.reviewer == "fulltext:fallback"


def test_one_available_reviewer_cannot_exclude_a_study() -> None:
    reply = (
        '{"verdict":"exclude","reason":"the required comparison is absent",'
        '"quote":"reports strong accuracy results across providers"}'
    )
    out = screen_full_text(
        _WORK,
        _PROTO,
        _FULL,
        mock_pool(lambda model, prompt: reply, screening_models=0),
    )

    assert out.verdict is Verdict.UNSURE
    assert out.reviewer == "fulltext:single-reviewer"
    assert "second agreeing reviewer" in out.reason


# --- pipeline integration ------------------------------------------------


def _make_run(session: Session, question: str) -> Run:
    org = get_default_org(session)
    project = ProjectRow(org_id=org.id, name="ft")
    session.add(project)
    session.flush()
    run = Run(org_id=org.id, project_id=project.id, question=question, status="pending")
    session.add(run)
    session.flush()
    return run


class _FakeAcquirer:
    """Reports every work retrieved + parsed, pointing at one pre-stored text."""

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


def test_pipeline_full_text_screening_completes_prisma(
    corpus: DuckDBCorpus, tmp_path: Path
) -> None:
    init_db()
    store = LocalDocumentStore(tmp_path)
    store.put_text("ck", _FULL)  # the parsed text the fake acquirer points at

    def handler(model: str, prompt: str) -> str:
        if "Existing queries" in prompt:
            return '{"queries": []}'
        if "Full text of" in prompt:  # the full-text reviewer (strong model)
            return (
                '{"verdict":"include","reason":"evaluates LLM IaC",'
                '"quote":"evaluates LLM-generated Terraform against a reference suite"}'
            )
        if "Distinct model reviewer votes" in prompt:
            return '{"verdict":"include","reason":"adjudicated"}'
        if model.startswith("mock-cheap"):
            return '{"verdict":"include","reason":"on topic"}'
        return '{"inclusion_criteria":["x"],"exclusion_criteria":[],"query_string":"learning"}'

    pool = mock_pool(handler, screening_models=2)
    with db_session() as session:
        run = _make_run(session, "llm iac")
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
        assert result.fulltext is not None
        assert result.fulltext.assessed > 0
        assert result.fulltext.included == result.prisma.studies_included
        assert result.prisma.reports_assessed_for_eligibility == result.fulltext.assessed
        assert result.fulltext.quotes_verified == result.fulltext.assessed  # each has a real quote

        # full-text decisions persist distinctly from title/abstract ones
        ft_decisions = session.scalars(
            select(ScreeningDecisionRow).where(
                ScreeningDecisionRow.run_id == run.id,
                ScreeningDecisionRow.reviewer.like("fulltext:%"),
            )
        ).all()
        assert len(ft_decisions) == result.fulltext.assessed
        events = [e.event for e in session.scalars(select(RunEvent)).all()]
        assert "fulltext_screening_done" in events


def test_pipeline_retains_unsure_full_texts_and_exposes_quality_warning(
    corpus: DuckDBCorpus, tmp_path: Path
) -> None:
    init_db()
    store = LocalDocumentStore(tmp_path)
    store.put_text("ck", _FULL)

    def handler(model: str, prompt: str) -> str:
        if "Existing queries" in prompt:
            return '{"queries": []}'
        if "Full text of" in prompt:
            return '{"verdict":"unsure","reason":"human review required","quote":""}'
        if "Distinct model reviewer votes" in prompt:
            return '{"verdict":"include","reason":"adjudicated"}'
        if model.startswith("mock-cheap"):
            return '{"verdict":"include","reason":"on topic"}'
        return '{"inclusion_criteria":["x"],"exclusion_criteria":[],"query_string":"learning"}'

    with db_session() as session:
        run = _make_run(session, "llm iac")
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=mock_pool(handler, screening_models=2),
            query_override="learning",
            screen=True,
            paper_limit=1,
            acquire=True,
            full_text_screen=True,
            acquirer=_FakeAcquirer(),
            document_store=store,
        )

        assert result.fulltext is not None
        assert result.fulltext.unsure == result.fulltext.assessed > 0
        assert result.prisma.reports_excluded_fulltext == 0
        assert len(run.config["paper_selection_ids"]) == 1
        pending_warning = next(
            warning
            for warning in result.quality_warnings
            if warning["code"] == "fulltext_review_pending"
        )
        assert pending_warning["severity"] == "warning"
        assert str(result.fulltext.unsure) in pending_warning["detail"]
        assert "retained as unsure" in pending_warning["detail"]
        assert run.config["quality_warnings"] == result.quality_warnings
