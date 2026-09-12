"""Metadata-only discovery must become bounded, exact-page evidence before synthesis."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.chat import service
from sixsentences_server.chat.service import ToolStep, _pending_public_web_read
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import Org, Run, db_session, init_db
from sixsentences_server.core.net import is_public_http_url
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool

QUERY = "Terraform plan apply official documentation HashiCorp"
PLAN = "https://developer.hashicorp.com/terraform/cli/commands/plan"
APPLY = "https://developer.hashicorp.com/terraform/cli/commands/apply"


def _search(*urls: str, query: str = QUERY, **extra: Any) -> ToolStep:
    return ToolStep(
        tool="web_search",
        query=query,
        results=[
            {
                "url": url,
                "title": url.rsplit("/", 1)[-1],
                "domain": "developer.hashicorp.com",
                "snippet": "",
                **extra,
            }
            for url in urls
        ],
    )


def test_hydration_is_bound_to_this_turn_exact_approved_query() -> None:
    steps = [_search(PLAN)]
    assert _pending_public_web_read(steps, None) is None
    assert _pending_public_web_read(steps, "private unrelated topic") is None
    assert _pending_public_web_read([], QUERY) is None
    failed = _search(PLAN).model_copy(update={"status": "failed"})
    assert _pending_public_web_read([failed], QUERY) is None
    decision = _pending_public_web_read(steps, QUERY)
    assert decision and decision["url"] == PLAN
    assert decision["tool"] == "read_webpage"


@pytest.mark.parametrize("field", ["snippet", "description", "excerpt"])
def test_hydration_does_not_automatically_refetch_hits_with_source_text(field: str) -> None:
    assert _pending_public_web_read([_search(PLAN, **{field: "A source passage."})], QUERY) is None


def test_hydration_reads_at_most_three_unique_safe_ranked_hits_without_retries() -> None:
    third = "https://developer.hashicorp.com/terraform/cli/run"
    fourth = "https://developer.hashicorp.com/terraform/language"
    steps = [_search("http://127.0.0.1/private", PLAN, PLAN, APPLY, third, fourth)]
    for url in (PLAN, APPLY, third):
        decision = _pending_public_web_read(steps, QUERY)
        assert decision and decision["url"] == url
        steps.append(
            ToolStep(
                tool="read_webpage",
                query=url,
                status="failed",
                results=[{"error": "The page could not be read"}],
            )
        )
    assert _pending_public_web_read(steps, QUERY) is None
    assert _pending_public_web_read([_search(PLAN, error="unavailable")], QUERY) is None


@pytest.mark.parametrize("legacy_tools", [False, True])
@pytest.mark.parametrize("read_failure", [False, True])
def test_answer_hydrates_exact_search_hits_through_existing_reader_before_synthesis(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, legacy_tools: bool, read_failure: bool
) -> None:
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-only-key")
    get_settings.cache_clear()
    if legacy_tools:
        monkeypatch.setattr(
            service, "_run_quick_answer_research_agent", lambda *a, **k: (set(), None)
        )
    dispatched: list[tuple[str, str]] = []
    fetched: list[str] = []
    premises: list[str] = []
    synthesis: list[str] = []
    original_execute = service._execute_tool

    def execute(tool: str, query: str, reason: str) -> tuple[ToolStep, list[Any]]:
        dispatched.append((tool, query))
        if tool == "web_search":
            assert query == QUERY
            return (_search(PLAN, APPLY), [])
        assert tool == "read_webpage" and query in {PLAN, APPLY}
        return original_execute(tool, query, reason)

    def fetch(_self: object, url: str) -> SimpleNamespace | None:
        fetched.append(url)
        if read_failure:
            return None
        text = (
            "Planning previews the proposed infrastructure changes."
            if url == PLAN
            else "Applying executes the proposed infrastructure changes."
        )
        return SimpleNamespace(
            final_url=url,
            content_type="text/html",
            content=f"<html><title>Terraform</title><main>{text}</main></html>".encode(),
        )

    def complete(_model: str, prompt: str) -> str:
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action":"answer"}'
            return json.dumps({"action": "tool", "tool": "web_search", "query": "PRIVATE_HISTORY"})
        if prompt.startswith("CLAIM:"):
            premises.append(prompt)
            evidence = prompt.split("EVIDENCE:\n", 1)[1]
            if "Planning" in prompt.split("EVIDENCE:", 1)[0]:
                assert "Planning previews" in evidence and "Applying executes" not in evidence
            else:
                assert "Applying executes" in evidence and "Planning previews" not in evidence
            return '{"label":"supported","reason":"The paired page establishes the claim."}'
        synthesis.append(prompt)
        assert "The title and URL do not establish factual claims" in prompt
        assert "could not be read or the reading budget ended" in prompt
        if read_failure:
            assert "Web pages opened and read in this conversation" not in prompt
            return "Die Seiten konnten nicht gelesen werden; ihre Inhalte sind nicht geprüft."
        assert "Planning previews" in prompt and "Applying executes" in prompt
        return f"Planning previews the proposed infrastructure changes [{service._web_citation_key(PLAN)}]. Applying executes the proposed infrastructure changes [{service._web_citation_key(APPLY)}]."

    monkeypatch.setattr(service, "_execute_tool", execute)
    monkeypatch.setattr(
        service, "is_public_http_url", lambda url, **_kwargs: is_public_http_url(url, resolve=False)
    )
    monkeypatch.setattr("sixsentences_server.acquisition.fetch.HttpxFetcher.fetch", fetch)
    init_db()
    with db_session() as session:
        org = Org(name="Synthetic public-source test", plan="community")
        session.add(org)
        session.flush()
        run = Run(
            org_id=org.id,
            question="Terraform plan and apply",
            status="completed",
            config={"mode": "ask"},
        )
        session.add(run)
        session.flush()
        answer = service.answer_question(
            session,
            run,
            mock_pool(complete),
            "Schau dazu bitte noch einmal im Internet nach und beantworte die Frage in zwei Sätzen.",
            web_search_public_data_confirmed=True,
            web_search_query=QUERY,
        )
    assert dispatched == [("web_search", QUERY), ("read_webpage", PLAN), ("read_webpage", APPLY)]
    assert fetched == [PLAN, APPLY]
    assert synthesis
    if read_failure:
        assert not premises
        assert "nicht geprüft" in answer.answer
    else:
        assert len(premises) == 2
        assert len(answer.claim_checks) == 2
        assert all(check.support == "supported" for check in answer.claim_checks)
    get_settings.cache_clear()


def test_runner_hydration_respects_existing_total_tool_limit(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatched: list[str] = []

    def execute(tool: str, query: str, reason: str) -> tuple[ToolStep, list[Any]]:
        assert tool == "read_webpage"
        dispatched.append(query)
        return (
            ToolStep(tool=tool, query=query, results=[{"url": query, "excerpt": "Source text"}]),
            [],
        )

    monkeypatch.setattr(service, "_execute_tool", execute)
    init_db()
    with db_session() as session:
        org = Org(name="Bounded page reads")
        session.add(org)
        session.flush()
        run = Run(org_id=org.id, question="Check the web", status="ready")
        session.add(run)
        session.flush()
        steps = [_search(PLAN, APPLY)]
        service._run_quick_answer_research_agent(
            session,
            run,
            mock_pool(lambda _m, _p: '{"action":"answer"}'),
            request="Check the web",
            history="PRIVATE_HISTORY",
            works=[],
            discovered_works=[],
            steps=steps,
            tools={"read_webpage": "Read public source"},
            base_tool_calls=1,
            hard_tool_limit=2,
            minimum_searches=1,
            expand_search_floor_after_first=False,
            approved_web_query=QUERY,
        )
    assert dispatched == [PLAN]
    assert len(steps) == 2
