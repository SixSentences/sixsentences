"""Grounded chat over a run's results: citation-by-id, persistence, usage."""

import pytest
from sqlalchemy import select

from sixsentences_server.chat.service import (
    ChatError,
    _chat_message_payload,
    answer_question,
    chat_history,
    chat_turn_scope,
)
from sixsentences_server.core.db import LLMCallRow, Run, db_session, get_default_org, init_db
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.base import LLMConfigError
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.pipeline.run import execute_run


def _chat_handler(model: str, prompt: str) -> str:
    if "Sources" in prompt:  # the chat call
        return "Transformers rely on self-attention [W1]. An invented ref [W99999] is ignored."
    if "PRISMA counts:" in prompt:
        return '{"summary":"The search is complete. Review the saved results.","complete":true}'
    if "Existing queries" in prompt:
        return '{"queries": []}'
    return '{"inclusion_criteria": [], "exclusion_criteria": [], "query_string": "transformer"}'


def test_chat_turn_scope_is_authoritative_and_unscoped_payloads_stay_legacy() -> None:
    legacy = {"turn_id": "legacy-value", "kind": "tool"}

    assert _chat_message_payload(legacy) == legacy
    with chat_turn_scope("accepted-turn-0001"):
        assert _chat_message_payload(legacy) == {
            "turn_id": "accepted-turn-0001",
            "kind": "tool",
        }
    assert _chat_message_payload() is None


def test_chat_answers_are_grounded_and_cited(corpus: DuckDBCorpus) -> None:
    init_db()
    pool = mock_pool(_chat_handler)
    with db_session() as session:
        # execute_run needs the real corpus fixture, not None
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="t")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="transformers", status="pending")
        session.add(run)
        session.flush()
        execute_run(
            session, run, corpus=corpus, pool=pool, query_override="transformer OR learning"
        )

        answer = answer_question(session, run, pool, "How do transformers work?")
        assert "[W1]" in answer.answer
        # only real, in-context ids survive; the invented W99999 is filtered out
        assert [c.id for c in answer.citations] == ["W1"]
        assert answer.sources_considered >= 1

        # A completed run opens with the assistant's closing summary. The Q&A
        # turn keeps its visible agent plan/checkpoints between user and answer.
        history = chat_history(session, run.id)
        agent_rows = [
            message for message in history if (message.payload or {}).get("kind") == "agent_work"
        ]
        visible_roles = [
            message.role
            for message in history
            if (message.payload or {}).get("kind") != "agent_work"
        ]
        assert visible_roles == ["assistant", "user", "assistant"]
        assert agent_rows[0].payload["agent_event"]["event"] == "plan.created"
        assert agent_rows[-1].payload["agent_event"]["event"] == "checkpoint.completed"
        assert history[0].payload == {"kind": "completion_summary"}
        assert history[-1].citations == ["W1"]
        assert all("turn_id" not in (message.payload or {}) for message in history)

        # chat spend is attributed to the run
        calls = session.scalars(
            select(LLMCallRow).where(LLMCallRow.run_id == run.id, LLMCallRow.task == "chat")
        ).all()
        assert calls


def test_chat_requires_a_configured_model(corpus: DuckDBCorpus) -> None:
    init_db()
    pool = mock_pool(_chat_handler)
    pool.routing.synthesis = None
    pool.routing.adjudication = None  # no strong model at all
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="t")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="q", status="pending")
        session.add(run)
        session.flush()
        with pytest.raises(ChatError, match="not available right now"):
            answer_question(session, run, pool, "anything?")


def test_final_configuration_diagnostic_never_logs_exception_content(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    init_db()
    pool = mock_pool(_chat_handler)
    marker = "SYNTHETIC_CONFIGURATION_PRIVATE_DETAIL"

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise LLMConfigError(marker) from RuntimeError(marker + "_CAUSE")

    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="Diagnostic fixture")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="transformers", status="pending")
        session.add(run)
        session.flush()
        execute_run(
            session, run, corpus=corpus, pool=pool, query_override="transformer OR learning"
        )
        monkeypatch.setattr("sixsentences_server.chat.service.complete_public_answer", unavailable)
        with (
            chat_turn_scope("synthetic-diagnostic-turn"),
            pytest.raises(ChatError, match="not available right now"),
        ):
            answer_question(session, run, pool, "Explain self-attention.", allow_tools=False)
        records = [
            record
            for record in caplog.records
            if record.getMessage().startswith("chat synthesis failure ")
        ]
        assert len(records) == 1
        diagnostic = records[0]
        assert f"run_id={run.id} turn_id=synthetic-diagnostic-turn" in diagnostic.getMessage()
        assert "error_class=LLMConfigError cause_class=RuntimeError" in diagnostic.getMessage()
        assert marker not in diagnostic.getMessage()
        assert diagnostic.exc_info is None
        assert diagnostic.stack_info is None
