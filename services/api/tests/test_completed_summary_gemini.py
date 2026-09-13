"""Final-only run summaries through the native, metered Gemini caller."""

import json
from collections.abc import Callable, Iterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.chat.service import (
    _SUMMARY_MAX_TOKENS,
    SUMMARY_SYSTEM,
    _completed_summary_text,
    summarize_completed_run,
)
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    ChatMessageRow,
    LLMCallRow,
    Project,
    Run,
    ScreeningDecisionRow,
    WorkRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.entitlements import attach_action_usage_sink
from sixsentences_server.core.models import PrismaCounts, ReviewProtocol, WorkRecord
from sixsentences_server.llm.base import BudgetGovernor, LLMCancelledError, ModelRef, cost_usd
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.privacy import PRIVATE_DEFAULT_MODEL
from sixsentences_server.ranking.scorer import RankedWork, RankingSignals

_FINAL = (
    "The search is complete. One work has been confirmed [W1]. "
    "One title/abstract match remains provisional [W2], and one record awaits review. "
    "Review those records before drawing conclusions."
)
_PRISMA = PrismaCounts(
    records_identified=4,
    records_screened=4,
    records_excluded=1,
    records_unsure=1,
    included=2,
)
Context = tuple[Session, Run, list[RankedWork]]


@pytest.fixture
def summary_context(settings: Settings) -> Iterator[Context]:
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        project = Project(org_id=org.id, name="Synthetic completion summary")
        session.add(project)
        session.flush()
        run = Run(
            org_id=org.id,
            project_id=project.id,
            question="Find studies about transformers",
            status="completed",
        )
        session.add(run)
        session.flush()
        ranked = []
        for index, (reviewer, verdict) in enumerate(
            [
                ("human:test", "include"),
                ("ensemble", "include"),
                ("ensemble", "unsure"),
                ("ensemble", "exclude"),
            ],
            start=1,
        ):
            work = WorkRecord(id=f"W{index}", title=f"Synthetic research record {index}")
            session.add(WorkRow(id=work.id, title=work.title, payload=work.model_dump(mode="json")))
            session.flush()
            session.add(
                ScreeningDecisionRow(
                    org_id=org.id,
                    run_id=run.id,
                    work_id=work.id,
                    reviewer=reviewer,
                    verdict=verdict,
                    reason="Synthetic eligibility decision",
                )
            )
            ranked.append(
                RankedWork(
                    work=work,
                    score=1.0,
                    retracted=False,
                    explanation="Synthetic ranking",
                    signals=RankingSignals(relevance=1.0, impact=0.0, recency=0.0),
                )
            )
        session.flush()
        yield session, run, ranked


def _payload(text: str, *, thoughts: int = 400, finish: str = "STOP") -> dict[str, object]:
    return {
        "candidates": [
            {
                "finishReason": finish,
                "content": {
                    "parts": [
                        {"text": "PRIVATE_THOUGHT_NOT_FOR_THE_MESSAGE", "thought": True},
                        {"text": text},
                    ]
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 200,
            "candidatesTokenCount": 80,
            "thoughtsTokenCount": thoughts,
            "totalTokenCount": 280 + thoughts,
            "cachedContentTokenCount": 50,
        },
    }


def _pool(http: httpx.Client, *, limit: float = 1.0) -> LLMPool:
    return LLMPool(
        {"gemini": GeminiClient(api_key="inert-test-key", http=http, max_retries=0)},
        RoutingConfig(synthesis=ModelRef("gemini", PRIVATE_DEFAULT_MODEL)),
        BudgetGovernor(limit_usd=limit),
    )


def _summarize(
    context: Context,
    pool: LLMPool,
    *,
    prisma: PrismaCounts = _PRISMA,
) -> list[ChatMessageRow]:
    session, run, ranked = context
    attach_action_usage_sink(
        session,
        pool,
        org_id=run.org_id,
        action_id="synthetic-summary",
        resource_type="run",
        resource_id=run.id,
        run_id=run.id,
    )
    summarize_completed_run(
        session,
        run,
        pool,
        protocol=ReviewProtocol(question=run.question, query_string="q"),
        prisma=prisma,
        ranked=ranked,
    )
    return list(session.scalars(select(ChatMessageRow).where(ChatMessageRow.run_id == run.id)))


def _http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        transport=httpx.MockTransport(handler),
    )


def test_native_completed_summary_is_final_only_and_fully_metered(summary_context: Context) -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        observed.append(request.url.path)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["maxOutputTokens"] == 3072
        assert body["generationConfig"]["thinkingConfig"]["includeThoughts"] is False
        system = body["systemInstruction"]["parts"][0]["text"]
        prompt = body["contents"][0]["parts"][0]["text"]
        assert "drafting notes" in system
        assert "Confirmed full-text or human inclusions: 1" in prompt
        assert "Provisional title/abstract inclusions: 1" in prompt
        assert "[W2] PROVISIONAL, full-text confirmation pending" in prompt
        reserved = cost_usd(
            PRIVATE_DEFAULT_MODEL,
            len(system.encode()) + len(prompt.encode()) + 256,
            3072,
        )
        assert pool.budget.reserved_usd == pytest.approx(reserved)
        return httpx.Response(200, json=_payload(json.dumps({"summary": _FINAL, "complete": True})))

    with _http(handler) as http:
        pool = _pool(http)
        rows = _summarize(summary_context, pool)
        assert len(rows) == len(observed) == 1
        assert rows[0].content == _FINAL
        assert rows[0].citations == ["W1", "W2"]
        assert rows[0].payload == {"kind": "completion_summary"}
        expected = cost_usd(PRIVATE_DEFAULT_MODEL, 200, 480, cached_input_tokens=50)
        assert pool.budget.spent_usd == pytest.approx(expected)
        assert pool.budget.reserved_usd == 0
        session, run, _ = summary_context
        ledger = list(session.scalars(select(LLMCallRow).where(LLMCallRow.run_id == run.id)))
        assert len(ledger) == 1
        assert ledger[0].cost_usd == pytest.approx(expected)
        assert ledger[0].output_tokens == 480  # Native thoughts count once, never appear as prose.
        assert ledger[0].cost_source == "gemini_usage"


@pytest.mark.parametrize(
    "text",
    [
        "220 words? Let's check word count. \"The systematic search yielded zero confirmed studies",
        '{"summary":"A truncated final message',
        '{"summary":"Search complete."}',
        '{"summary":"Search complete.","complete":false}',
        '{"complete":true,"summary":"Search complete."}',
        '{"summary":null,"complete":true}',
        '{"summary":"220 words? Let\'s check word count. A draft.","complete":true}',
        '{"summary":"An incomplete sentence about the","complete":true}',
        '{"summary":"<tool_call>hidden instruction</tool_call>.","complete":true}',
        "[]",
    ],
)
def test_invalid_summary_is_not_published_or_retried_and_paid_usage_remains(
    summary_context: Context,
    text: str,
) -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        return httpx.Response(200, json=_payload(text, finish="MAX_TOKENS"))

    with _http(handler) as http:
        pool = _pool(http)
        rows = _summarize(summary_context, pool)
        assert len(rows) == len(observed) == pool.budget.calls == 1
        assert rows[0].payload == {"kind": "completion_summary", "summary_status": "unavailable"}
        assert "The written summary is not available yet." in rows[0].content
        assert "Records screened: 4. Confirmed inclusions: 1." in rows[0].content
        assert "Provisional title/abstract matches: 1. Awaiting review: 1." in rows[0].content
        assert "full-text or human confirmation" in rows[0].content
        assert "PRIVATE_THOUGHT" not in rows[0].content
        assert "220 words?" not in rows[0].content
        assert rows[0].citations == []
        assert pool.budget.reserved_usd == 0
        assert pool.budget.spent_usd == pytest.approx(
            cost_usd(PRIVATE_DEFAULT_MODEL, 200, 480, cached_input_tokens=50)
        )
        session, run, _ = summary_context
        ledger = list(session.scalars(select(LLMCallRow).where(LLMCallRow.run_id == run.id)))
        assert len(ledger) == 1


def test_summary_budget_rejection_keeps_results_without_provider_egress(
    summary_context: Context,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("the complete 3072-token allowance must be reserved before dispatch")

    with _http(handler) as http:
        pool = _pool(http, limit=0.001)
        session, run, _ = summary_context
        run.question = "Bitte finde Studien auf Deutsch"
        rows = _summarize(summary_context, pool)
        assert "Die ausformulierte Zusammenfassung ist noch nicht verfügbar." in rows[0].content
        assert "Vorläufige Treffer nach Titel-/Abstract-Prüfung: 1." in rows[0].content
        assert pool.budget.spent_usd == pool.budget.reserved_usd == pool.budget.calls == 0
        assert not list(session.scalars(select(LLMCallRow).where(LLMCallRow.run_id == run.id)))


def test_completed_summary_cancellation_does_not_publish_after_incurred_cost(
    summary_context: Context,
) -> None:
    cancelled = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal cancelled
        cancelled = True
        return httpx.Response(200, json=_payload(json.dumps({"summary": _FINAL, "complete": True})))

    with _http(handler) as http:
        pool = _pool(http)
        pool.cancel_check = lambda: cancelled
        with pytest.raises(LLMCancelledError):
            _summarize(summary_context, pool)
        assert pool.budget.calls == 1
        assert pool.budget.spent_usd > 0
        assert pool.budget.reserved_usd == 0
        session, run, _ = summary_context
        messages = session.scalars(select(ChatMessageRow).where(ChatMessageRow.run_id == run.id))
        assert not list(messages)


def test_near_cap_json_is_not_mislabelled_complete() -> None:
    text = json.dumps({"summary": _FINAL, "complete": True})
    assert _completed_summary_text(text, _SUMMARY_MAX_TOKENS - 4) is None
    assert _completed_summary_text(text, 500) == _FINAL
    cited_final = "A work is confirmed. [W1]"
    assert (
        _completed_summary_text(
            json.dumps({"summary": cited_final, "complete": True}),
            100,
        )
        == cited_final
    )
    assert "usually the query scope" not in SUMMARY_SYSTEM
    assert "Never guess why retrieval or confirmation is incomplete" in SUMMARY_SYSTEM


def test_native_near_limit_completion_falls_back_without_hiding_thinking_cost(
    summary_context: Context,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                json.dumps({"summary": _FINAL, "complete": True}),
                thoughts=2990,
                finish="MAX_TOKENS",
            ),
        )

    with _http(handler) as http:
        pool = _pool(http)
        rows = _summarize(summary_context, pool)
        assert (rows[0].payload or {})["summary_status"] == "unavailable"
        assert pool.budget.calls == 1
        assert pool.usage[0].output_tokens == 3070
        assert pool.budget.spent_usd == pytest.approx(
            cost_usd(PRIVATE_DEFAULT_MODEL, 200, 3070, cached_input_tokens=50)
        )
        assert pool.budget.reserved_usd == 0


def test_browser_five_provisional_five_excluded_two_unsure_counts_remain_explicit(
    summary_context: Context,
) -> None:
    session, run, ranked = summary_context
    initial = session.scalar(
        select(ScreeningDecisionRow).where(
            ScreeningDecisionRow.run_id == run.id,
            ScreeningDecisionRow.reviewer == "human:test",
        )
    )
    assert initial is not None
    initial.reviewer = "ensemble"
    for index in range(5, 13):
        work = WorkRecord(id=f"W{index}", title=f"Synthetic research record {index}")
        session.add(WorkRow(id=work.id, title=work.title, payload=work.model_dump(mode="json")))
        session.flush()
        session.add(
            ScreeningDecisionRow(
                org_id=run.org_id,
                run_id=run.id,
                work_id=work.id,
                reviewer="ensemble",
                verdict="include" if index <= 7 else "unsure" if index == 8 else "exclude",
                reason="Synthetic eligibility decision",
            )
        )
        ranked.append(
            RankedWork(
                work=work,
                score=1.0,
                retracted=False,
                explanation="Synthetic ranking",
                signals=RankingSignals(relevance=1.0, impact=0.0, recency=0.0),
            )
        )
    session.flush()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["contents"][0]["parts"][0]["text"]
        assert "Confirmed full-text or human inclusions: 0" in prompt
        assert "Provisional title/abstract inclusions: 5" in prompt
        assert "awaiting human review): 2" in prompt
        return httpx.Response(200, json=_payload('{"summary":"The systematic search yielded'))

    with _http(handler) as http:
        rows = _summarize(
            summary_context,
            _pool(http),
            prisma=PrismaCounts(
                records_identified=12,
                records_screened=12,
                records_excluded=5,
                records_unsure=2,
                included=5,
            ),
        )
    assert "Records screened: 12. Confirmed inclusions: 0." in rows[0].content
    assert "Provisional title/abstract matches: 5. Awaiting review: 2." in rows[0].content
    assert "does not mean that no relevant research exists" in rows[0].content
