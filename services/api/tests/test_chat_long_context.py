"""Bounded long-thread context without new searches or invented summaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.chat import service
from sixsentences_server.config import Settings
from sixsentences_server.core.db import ChatMessageRow, Run, db_session, get_default_org, init_db
from sixsentences_server.core.models import WorkRecord


def _run(session: Any, question: str = "Explain Terraform state") -> Run:
    org = get_default_org(session)
    run = Run(org_id=org.id, question=question, status="completed")
    session.add(run)
    session.flush()
    return run


def _message(
    session: Any,
    run: Run,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        ChatMessageRow(
            org_id=run.org_id,
            run_id=run.id,
            role=role,
            content=content,
            payload=payload,
        )
    )


def test_history_retains_initial_goal_old_user_correction_and_exact_source(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    captured: dict[str, Any] = {}

    def capture(history: list[dict[str, str]], **kwargs: Any) -> str:
        captured["history"] = history
        captured.update(kwargs)
        return "captured"

    monkeypatch.setattr(service, "render_model_aware_context", capture)
    with db_session() as session:
        run = _run(session)
        for index in range(140):
            content = f"Follow-up instruction {index}"
            payload = None
            if index == 5:
                content = "Actually use only the Terraform state evidence, not Kubernetes."
                payload = {
                    "selection": {
                        "work_id": "W1234",
                        "page": 7,
                        "quote": "State binds resources.",
                    }
                }
            _message(session, run, "user", content, payload)
            _message(session, run, "assistant", f"Synthetic assistant answer {index}")
        other = _run(session, "Other conversation")
        _message(session, other, "user", "UNRELATED PRIVATE CONVERSATION")
        session.flush()

        pool = SimpleNamespace()
        assert (
            service._history(
                session,
                run.id,
                service.HISTORY_TURNS,
                pool=pool,
                current_request="Compare the W1234 state evidence again.",
            )
            == "captured"
        )

    history = captured["history"]
    assert history[0] == {"role": "user", "content": "Explain Terraform state"}
    assert len(history) == 261  # Initial goal + 140 users + the latest 120 assistants.
    assert len({(item["role"], item["content"]) for item in history}) == len(history)
    correction = next(item for item in history if item["content"].startswith("Actually"))
    assert '[W1234] on page 7: "State binds resources."' in correction["content"]
    assert history[-1]["content"] == "Synthetic assistant answer 139"
    assert not any("UNRELATED" in item["content"] for item in history)
    assert not any(item["content"] == "Synthetic assistant answer 0" for item in history)
    assert captured["pool"] is pool
    assert captured["current_request"] == "Compare the W1234 state evidence again."


def test_history_keeps_earliest_instructions_beyond_user_window_without_loading_all_answers(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    captured: list[dict[str, str]] = []

    def capture(history: list[dict[str, str]], **_kwargs: Any) -> str:
        captured.extend(history)
        return "captured"

    monkeypatch.setattr(service, "render_conversation_context", capture)
    with db_session() as session:
        run = _run(session)
        for index in range(300):
            _message(session, run, "user", f"Original instruction {index}")
            _message(session, run, "assistant", f"Original answer {index}")
        session.flush()
        service._history(session, run.id, service.HISTORY_TURNS)

    contents = [item["content"] for item in captured]
    assert all(f"Original instruction {index}" in contents for index in range(4))
    assert "Original instruction 30" not in contents
    assert "Original instruction 60" in contents
    assert "Original answer 179" not in contents
    assert "Original answer 180" in contents
    assert len(captured) == 365  # Initial goal + 244 users + 120 assistants.


def test_history_seeds_original_ask_without_a_persisted_initial_user_row(
    settings: Settings,
) -> None:
    init_db()
    with db_session() as session:
        run = _run(session, "Explain the immutable original Terraform question.")
        context = service._history(
            session, run.id, service.HISTORY_TURNS, current_request="Explain that further."
        )
        assert run.question in context


def test_latest_normal_topic_wins_after_over_one_hundred_acknowledgements(
    settings: Settings,
) -> None:
    init_db()
    with db_session() as session:
        run = _run(session)
        _message(session, run, "user", "Find 10 papers about Terraform since 2024")
        _message(session, run, "user", "What is Kubernetes?")
        for _ in range(130):
            _message(session, run, "user", "Okay")
            _message(session, run, "assistant", "Synthetic acknowledgement")
        session.flush()
        request = service._paper_discovery_request_context(session, run.id, "Search deeper")

    assert "What is Kubernetes?" in request
    assert "Terraform" not in request
    assert service._paper_discovery_topic(request) == "Kubernetes"
    assert service.paper_discovery_constraints(request).requested_count == 1


def test_latest_count_and_year_refinement_survives_deeper_search() -> None:
    current = "Search deeper"
    session = SimpleNamespace(
        scalars=lambda _query: SimpleNamespace(
            all=lambda: [
                "Okay",
                "Actually only 3 papers since 2020",
                "Make a table",
                "Find 10 papers about Terraform since 2024",
            ]
        ),
        get=lambda *_args: None,
    )
    request = service._paper_discovery_request_context(session, 7, current)
    constraints = service.paper_discovery_constraints(request)

    assert request.endswith("Current authoritative request: Search deeper")
    assert "Prior retrieval refinement: Actually only 3 papers since 2020" in request
    assert constraints.requested_count == 3
    assert constraints.minimum_year == 2020
    assert "refinement" not in service._paper_discovery_topic(request).casefold()
    overridden = service.paper_discovery_constraints(
        request.replace(
            "Current authoritative request: Search deeper",
            "Current authoritative request: Actually only one paper since 2025",
        )
    )
    assert overridden.requested_count == 1
    assert overridden.minimum_year == 2025


def test_current_named_topic_never_inherits_an_old_topic_or_constraints() -> None:
    session = SimpleNamespace(scalars=lambda _query: pytest.fail("history is unnecessary"))
    current = "Show me another paper about Kubernetes"
    assert service._paper_discovery_request_context(session, 7, current) == current


def test_progress_rows_do_not_evict_matching_receipts_or_the_last_opened_paper(
    settings: Settings,
) -> None:
    init_db()
    with db_session() as session:
        run = _run(session)
        _message(
            session,
            run,
            "tool",
            "Synthetic lookup",
            {
                "tool": "find_papers",
                "status": "completed",
                "turn_id": "prior-turn",
                "query": "Terraform state",
                "results": [{"id": "W1234", "title": "Terraform state"}],
            },
        )
        _message(
            session,
            run,
            "tool",
            "Synthetic reader",
            {
                "tool": "show_paper",
                "status": "completed",
                "turn_id": "prior-turn",
                "query": "W1234",
                "results": [{"id": "W1234", "title": "Terraform state"}],
            },
        )
        for index in range(120):
            _message(
                session,
                run,
                "tool",
                f"Synthetic progress {index}",
                {
                    "tool": "agent_update",
                    "kind": "agent_work",
                    "status": "completed",
                },
            )
        other = _run(session)
        _message(
            session,
            other,
            "tool",
            "Other reader",
            {
                "tool": "show_paper",
                "query": "W9999",
                "results": [{"id": "W9999", "title": "Terraform state"}],
            },
        )
        session.flush()

        receipts = service._prior_research_receipts(session, run.id, "Terraform")
        assert [step.tool for step in receipts] == ["find_papers", "show_paper"]
        assert service._latest_reader_work_id(session, run.id) == "W1234"
        assert service._prior_research_receipts(session, run.id, "Kubernetes") == []


def test_exact_source_reference_is_pinned_only_within_the_current_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preferred = WorkRecord(id="W10", title="Terraform overview")
    referenced = WorkRecord(id="W1234", title="State evidence")
    monkeypatch.setattr(service, "works_for_run", lambda *_args: [preferred, referenced])
    monkeypatch.setattr(
        service,
        "rank_works",
        lambda *_args, **_kwargs: [
            SimpleNamespace(work=preferred, score=100.0),
            SimpleNamespace(work=referenced, score=0.0),
        ],
    )

    assert service._context_works(None, 7, "Explain W1234 again", size=1) == [referenced]
    assert service._context_works(None, 7, "Explain W9999", size=1) == [preferred]


def test_exact_pubmed_reference_is_pinned_within_the_current_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preferred = WorkRecord(id="W10", title="Terraform overview")
    referenced = WorkRecord(
        id="pubmed:12345678",
        pmid="12345678",
        source="pubmed",
        title="Clinical state evidence",
    )
    monkeypatch.setattr(service, "works_for_run", lambda *_args: [preferred, referenced])
    monkeypatch.setattr(
        service,
        "rank_works",
        lambda *_args, **_kwargs: [
            SimpleNamespace(work=preferred, score=100.0),
            SimpleNamespace(work=referenced, score=0.0),
        ],
    )

    assert service._context_works(None, 7, "Explain PubMed:12345678 again", size=1) == [referenced]


def test_generic_web_followup_still_needs_an_explicit_public_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # More retained private conversation is never automatic web-search consent.
    monkeypatch.setattr(
        service,
        "formulate_search_query",
        lambda *_args, **_kwargs: pytest.fail(
            "a generic follow-up must not formulate a search from private history"
        ),
    )
    with pytest.raises(service.ChatError):
        service._confirmed_public_web_query("Schau auch mal noch im Internet nach", None, [])
