"""Bounded result sets and grounded post-run chat context."""

from sqlalchemy import func, select

from sixsentences_server.chat.service import answer_question
from sixsentences_server.core.db import (
    ExtractionRow,
    Run,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.reporting.exports import works_for_run


def _add_work(session, run: Run, work: WorkRecord) -> None:  # type: ignore[no-untyped-def]
    session.add(
        WorkRow(
            id=work.id,
            doi=work.doi,
            title=work.title,
            year=work.year,
            payload=work.model_dump(mode="json"),
        )
    )
    session.add(
        SourceRecordRow(
            org_id=run.org_id,
            run_id=run.id,
            work_id=work.id,
            source="test",
            corpus_version="fixture",
        )
    )


def test_paper_limit_keeps_full_identification_ledger_but_bounds_results(settings) -> None:
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        run = Run(
            org_id=org.id,
            question="transformer screening",
            status="completed",
            config={"paper_limit": 2, "paper_selection_ids": ["W2", "W1"]},
        )
        session.add(run)
        session.flush()
        for work in (
            WorkRecord(
                id="W1",
                title="Transformer screening benchmark",
                abstract="Transformer screening benchmark evidence.",
                cited_by_count=100,
            ),
            WorkRecord(
                id="W2",
                title="Automated screening with language models",
                abstract="Language models automate evidence screening.",
                cited_by_count=50,
            ),
            WorkRecord(
                id="W3",
                title="Unrelated database systems",
                abstract="Storage engines and transaction processing.",
                cited_by_count=5000,
            ),
        ):
            _add_work(session, run, work)
        session.flush()

        selected = works_for_run(session, run.id, org_id=org.id)
        identified = session.scalar(
            select(func.count(func.distinct(SourceRecordRow.work_id))).where(
                SourceRecordRow.run_id == run.id
            )
        )

        assert identified == 3
        assert [work.id for work in selected] == ["W2", "W1"]


def test_finalized_empty_output_does_not_fall_back_to_unscreened_papers(settings) -> None:
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        run = Run(
            org_id=org.id,
            question="no eligible evidence",
            status="completed",
            config={
                "paper_limit": 10,
                "paper_selection_ids": [],
                "paper_selection_finalized": True,
            },
        )
        session.add(run)
        session.flush()
        _add_work(
            session,
            run,
            WorkRecord(id="W1", title="Identified but excluded"),
        )
        session.flush()

        assert works_for_run(session, run.id, org_id=org.id) == []


def test_post_run_chat_receives_screening_and_extraction_evidence(settings) -> None:
    init_db()
    prompts: list[str] = []

    def handler(model: str, prompt: str) -> str:
        prompts.append(prompt)
        return "The included study reports a 0.91 F1 score [W1]."

    with db_session() as session:
        org = get_default_org(session)
        run = Run(
            org_id=org.id,
            question="How accurate is automated screening?",
            status="completed",
            config={"paper_limit": 100},
            prisma={"records_identified": 1, "records_screened": 1, "included": 1},
        )
        session.add(run)
        session.flush()
        _add_work(
            session,
            run,
            WorkRecord(
                id="W1",
                title="Automated screening benchmark",
                abstract="The model was evaluated on a held-out review set.",
                cited_by_count=25,
            ),
        )
        session.add(
            ScreeningDecisionRow(
                org_id=org.id,
                run_id=run.id,
                work_id="W1",
                reviewer="model:test",
                verdict="include",
                reason="Directly evaluates automated title and abstract screening.",
                quote="The system was evaluated on a held-out review set.",
            )
        )
        session.add(
            ExtractionRow(
                org_id=org.id,
                run_id=run.id,
                work_id="W1",
                status="done",
                model="test",
                payload={
                    "F1": {
                        "value": "0.91",
                        "quote": "The macro F1 score was 0.91.",
                        "page": 7,
                        "verified": True,
                    }
                },
            )
        )
        session.flush()

        answer = answer_question(
            session,
            run,
            mock_pool(handler),
            "What performance did the included study report?",
            allow_tools=False,
            verify=False,
        )

        assert answer.citations[0].id == "W1"
        chat_prompt = next(prompt for prompt in prompts if "Run evidence:" in prompt)
        assert "screening=include" in chat_prompt
        assert "F1=0.91 (page 7)" in chat_prompt
        assert "PRISMA counts" in chat_prompt
