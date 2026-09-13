"""Offline invariants for the reviewed direct Gemini provider-cost path."""

import json
from typing import Any

import httpx
import pytest

from sixsentences_server.config import Settings
from sixsentences_server.core.db import LLMCallRow, Org, ProviderCostRow, db_session, init_db
from sixsentences_server.core.entitlements import (
    action_ai_spend,
    action_budget,
    agent_tool_cost,
    ai_cost_reserved,
    attach_action_usage_sink,
    begin_ai_action,
    check_action_cost,
    finish_ai_action,
    monthly_ai_spend,
    question_cost,
    question_settlement_cost,
    record_provider_cost,
    search_cost,
)
from sixsentences_server.core.plans import capacity_profile
from sixsentences_server.llm.base import (
    BudgetExceededError,
    BudgetGovernor,
    LLMResponse,
    LLMUsage,
    ModelRef,
    TaskType,
    cache_read_price_of,
    cost_usd,
    price_of,
    settled_cost_usd,
)
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.gemini_pricing import (
    gemini_projected_cost_usd,
    gemini_usage_cost_usd,
    parse_gemini_usage,
    report_gemini_uncertain_cost,
)
from sixsentences_server.llm.models import resolve_chat_model
from sixsentences_server.llm.pool import LLMPool, RoutingConfig


@pytest.mark.parametrize(
    "tokens,input_rate,output_rate,cache_rate",
    [(199999, 2.0, 12.0, 0.2), (200000, 2.0, 12.0, 0.2), (200001, 4.0, 18.0, 0.4)],
)
def test_direct_pro_context_boundary_is_after_200k(
    tokens: int, input_rate: float, output_rate: float, cache_rate: float
) -> None:
    assert price_of("gemini-3.1-pro-preview", tokens) == (input_rate, output_rate)
    assert cache_read_price_of("gemini-3.1-pro-preview", tokens) == cache_rate


def test_direct_ids_use_standard_not_router_flex_or_unknown_fallback() -> None:
    assert price_of("gemini-3.5-flash") == (1.5, 9.0)
    assert cache_read_price_of("gemini-3.5-flash") == 0.15
    assert cost_usd("gemini-3.5-flash", 10000, 2000) == pytest.approx(0.033)
    assert cost_usd("gemini-3.1-pro-preview", 10000, 2000) == pytest.approx(0.044)


@pytest.mark.parametrize("plan", ["community", "community", "community"])
def test_private_premium_reservations_keep_fractional_price_ratio(plan: str) -> None:
    premium = resolve_chat_model("gemini-3.1-pro-preview", allow_locked=True)
    assert premium.multiplier == pytest.approx(4 / 3)
    profile = capacity_profile(plan)
    assert question_cost(premium.multiplier, plan=plan) == round(profile.question * 4 / 3)
    assert question_cost(premium.multiplier, plan=plan) < question_cost(3, plan=plan)
    assert agent_tool_cost(4, premium.multiplier, plan=plan) == max(
        1, round(profile.question * (4 / 3) * (4 * 0.18))
    )
    assert search_cost(premium.multiplier, plan=plan) == round(profile.search * 1.05)
    assert question_settlement_cost(
        premium.multiplier, plan=plan, output_chars=7200
    ) == question_cost(premium.multiplier, plan=plan, output_chars=7200) - question_cost(
        premium.multiplier, plan=plan
    )


def test_long_context_premium_admission_and_settlement_use_real_usd_not_menu_factor() -> None:
    model = "gemini-3.1-pro-preview"
    projected = gemini_projected_cost_usd(model, 250000, 10000)
    assert projected == pytest.approx(1.18)
    with pytest.raises(BudgetExceededError):
        BudgetGovernor(limit_usd=0.62).check(projected)
    budget = BudgetGovernor(limit_usd=projected)
    budget.check(projected)
    response = LLMResponse(
        text="done",
        model=model,
        provider="gemini",
        input_tokens=250000,
        output_tokens=2000,
        cached_input_tokens=200000,
        cost_source="gemini_usage",
    )
    budget.record(TaskType.CHAT, response)
    assert budget.spent_usd == pytest.approx(0.316)


def test_cached_prompt_selects_long_tier_before_discount_and_bills_thinking_once() -> None:
    metadata = {
        "promptTokenCount": 250000,
        "cachedContentTokenCount": 200000,
        "candidatesTokenCount": 1000,
        "thoughtsTokenCount": 3000,
        "totalTokenCount": 254000,
    }
    usage = parse_gemini_usage(metadata)
    assert usage is not None
    assert usage.input_tokens == 250000
    assert usage.output_tokens == 4000
    assert usage.cached_input_tokens == 200000
    cost = gemini_usage_cost_usd("gemini-3.1-pro-preview", metadata)
    assert cost is not None
    assert cost[0] == pytest.approx((50000 * 4 + 200000 * 0.4 + 4000 * 18) / 1000000.0)
    assert cost[1] == "gemini_usage"


def test_missing_thought_breakdown_uses_reported_total() -> None:
    usage = parse_gemini_usage(
        {"promptTokenCount": 100, "candidatesTokenCount": 20, "totalTokenCount": 170}
    )
    assert usage is not None
    assert usage.output_tokens == 70


@pytest.mark.parametrize(
    "tier", ["standard", "unspecified", None, "", "STANDARD", "SERVICE_TIER_UNSPECIFIED"]
)
def test_native_standard_tiers_preserve_actual_usage(tier: str | None) -> None:
    metadata = {
        "serviceTier": tier,
        "promptTokenCount": 100,
        "cachedContentTokenCount": 50,
        "candidatesTokenCount": 5,
        "thoughtsTokenCount": 2,
        "totalTokenCount": 107,
    }
    usage = parse_gemini_usage(metadata)
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens) == (100, 7, 50)
    measured = gemini_usage_cost_usd("gemini-3.5-flash", metadata)
    assert measured is not None
    assert measured == (
        cost_usd("gemini-3.5-flash", 100, 7, cached_input_tokens=50),
        "gemini_usage",
    )


@pytest.mark.parametrize(
    "tier", ["flex", "priority", "FLEX", "PRIORITY", "unknown", " standard ", 0, True, [], {}]
)
def test_nonstandard_or_malformed_tiers_cannot_use_standard_prices(tier: Any) -> None:
    metadata = {"serviceTier": tier, "promptTokenCount": 100, "totalTokenCount": 107}
    assert parse_gemini_usage(metadata) is None
    assert gemini_usage_cost_usd("gemini-3.5-flash", metadata) is None


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"promptTokenCount": 100},
        {"promptTokenCount": 0, "totalTokenCount": 0},
        {"promptTokenCount": True, "totalTokenCount": 1},
        {"promptTokenCount": -1, "candidatesTokenCount": 1},
        {"promptTokenCount": 1, "candidatesTokenCount": float("nan")},
        {"promptTokenCount": 1, "candidatesTokenCount": 1.2},
        {"promptTokenCount": "100", "candidatesTokenCount": 1},
        {"promptTokenCount": 100, "cachedContentTokenCount": 101, "totalTokenCount": 101},
        {"promptTokenCount": 100, "candidatesTokenCount": 5, "totalTokenCount": 104},
        {
            "promptTokenCount": 100,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 2,
            "totalTokenCount": 106,
        },
        {"promptTokenCount": 100, "totalTokenCount": 110, "toolUsePromptTokenCount": 1},
        {"promptTokenCount": 100, "totalTokenCount": 110, "serviceTier": "PRIORITY"},
    ],
)
def test_missing_or_invalid_usage_never_settles_as_zero(metadata: Any) -> None:
    assert parse_gemini_usage(metadata) is None
    assert gemini_usage_cost_usd("gemini-3.5-flash", metadata) is None


def test_blocked_output_can_still_record_observed_prompt_spend() -> None:
    billed = gemini_usage_cost_usd(
        "gemini-3.5-flash", {"promptTokenCount": 100, "totalTokenCount": 100}
    )
    assert billed is not None
    assert billed[0] == pytest.approx(0.00015)


@pytest.mark.parametrize(
    "model,image_rate", [("gemini-3-pro-image", 120.0), ("gemini-3.1-flash-image", 60.0)]
)
def test_image_usage_separates_image_output_from_text_and_thinking(
    model: str, image_rate: float
) -> None:
    billed = gemini_usage_cost_usd(
        model,
        {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 1220,
            "thoughtsTokenCount": 80,
            "totalTokenCount": 2300,
            "candidatesTokensDetails": [
                {"modality": "TEXT", "tokenCount": 100},
                {"modality": "IMAGE", "tokenCount": 1120},
            ],
        },
        output_image_count=1,
    )
    assert billed is not None
    assert billed[0] == pytest.approx(cost_usd(model, 1000, 180) + 1120 * image_rate / 1000000.0)
    assert billed[1] == "gemini_usage"


@pytest.mark.parametrize(
    "model,size,tokens,price",
    [
        ("gemini-3-pro-image", "1K", 1120, 0.1344),
        ("gemini-3-pro-image", "2K", 1120, 0.1344),
        ("gemini-3-pro-image", "4K", 2000, 0.24),
        ("gemini-3.1-flash-image", "0.5K", 747, 0.04482),
        ("gemini-3.1-flash-image", "1K", 1120, 0.0672),
        ("gemini-3.1-flash-image", "2K", 1680, 0.1008),
        ("gemini-3.1-flash-image", "4K", 2520, 0.1512),
    ],
)
def test_image_resolution_fallback_is_separately_labelled_and_counts_actual_images(
    model: str, size: str, tokens: int, price: float
) -> None:
    billed = gemini_usage_cost_usd(
        model,
        {"promptTokenCount": 1000, "candidatesTokenCount": tokens * 2 + 100},
        output_image_count=2,
        image_size=size,
    )
    assert billed is not None
    assert billed[0] == pytest.approx(cost_usd(model, 1000, 100) + 2 * price)
    assert billed[1] == "gemini_estimate"
    projected = gemini_projected_cost_usd(model, 1000, 1000, output_image_count=2, image_size=size)
    assert projected >= cost_usd(model, 1000, 1000) + 2 * price
    assert projected >= billed[0]


@pytest.mark.parametrize(
    "model,rate", [("gemini-3-pro-image", 120.0), ("gemini-3.1-flash-image", 60.0)]
)
def test_image_projection_covers_multiple_images_within_full_generation_cap(
    model: str, rate: float
) -> None:
    projected = gemini_projected_cost_usd(model, 1000, 8192, output_image_count=1)
    assert projected == pytest.approx(cost_usd(model, 1000, 0) + 8192 * rate / 1000000.0)


def test_unreviewed_model_and_unitemized_image_usage_require_reserve_fallback() -> None:
    metadata = {"promptTokenCount": 100, "candidatesTokenCount": 1200}
    assert gemini_usage_cost_usd("unknown-model", metadata) is None
    assert gemini_usage_cost_usd("gemini-3-pro-image", metadata) is None
    assert (
        gemini_usage_cost_usd("gemini-3-pro-image", metadata, output_image_count=1, image_size="8K")
        is None
    )
    assert gemini_usage_cost_usd("gemini-3.5-flash", metadata, output_image_count=1) is None
    with pytest.raises(ValueError, match="Unreviewed"):
        gemini_projected_cost_usd("unknown-model", 100, 100)


def test_reservation_is_uncached_but_settlement_releases_unused_thinking_allowance() -> None:
    model = "gemini-3.5-flash"
    reserve = gemini_projected_cost_usd(model, 10000, 6000)
    budget = BudgetGovernor(limit_usd=reserve)
    budget.check(reserve)
    with pytest.raises(BudgetExceededError):
        BudgetGovernor(limit_usd=reserve - 1e-06).check(reserve)
    response = LLMResponse(
        text="done",
        model=model,
        provider="gemini",
        input_tokens=1000,
        output_tokens=300,
        cached_input_tokens=500,
        cost_source="gemini_usage",
    )
    budget.record(TaskType.CHAT, response)
    assert budget.spent_usd == pytest.approx(0.003525)
    assert budget.spent_usd < reserve
    assert settled_cost_usd(response) == budget.spent_usd


def test_direct_gemini_text_and_multimedia_share_durable_action_and_monthly_ledgers(
    settings: Settings,
) -> None:
    init_db()
    with db_session() as session:
        org = Org(name="Synthetic Gemini accounting", plan="community")
        session.add(org)
        session.flush()
        action = begin_ai_action(
            session, org, action="question", resource_type="chat", resource_id="synthetic"
        )
        session.flush()
        initial_reserve = ai_cost_reserved(session, org.id)
        pool = LLMPool({}, RoutingConfig(), BudgetGovernor(limit_usd=action.cost_limit_usd))
        attach_action_usage_sink(
            session,
            pool,
            org_id=org.id,
            action_id=action.action_id,
            resource_type="chat",
            resource_id="synthetic",
        )
        text_cost = cost_usd("gemini-3.5-flash", 1000, 300, cached_input_tokens=500)
        assert pool.on_usage is not None
        pool.on_usage(
            LLMUsage(
                task="chat",
                provider="gemini",
                model="gemini-3.5-flash",
                input_tokens=1000,
                output_tokens=300,
                cached_input_tokens=500,
                cost_usd=text_cost,
                cost_source="gemini_usage",
                duration_ms=1,
            )
        )
        media_cost = gemini_usage_cost_usd(
            "gemini-3.5-flash",
            {
                "promptTokenCount": 2000,
                "candidatesTokenCount": 100,
                "thoughtsTokenCount": 50,
                "totalTokenCount": 2150,
            },
        )
        assert media_cost is not None
        record_provider_cost(
            session,
            org_id=org.id,
            action_id=action.action_id,
            resource_type="chat",
            resource_id="synthetic",
            task="vision",
            provider="gemini",
            model="gemini-3.5-flash",
            cost_usd=media_cost[0],
            cost_source=media_cost[1],
        )
        session.flush()
        total = text_cost + media_cost[0]
        assert action_ai_spend(session, action.action_id) == pytest.approx(total)
        assert monthly_ai_spend(session, org.id) == pytest.approx(total)
        assert action_budget(session, action.action_id).spent_usd == pytest.approx(total)
        assert ai_cost_reserved(session, org.id) == pytest.approx(initial_reserve - total)
        check_action_cost(session, action.action_id, action.cost_limit_usd - total)
        finish_ai_action(session, action.action_id)
        session.flush()
        assert ai_cost_reserved(session, org.id) == 0
        assert monthly_ai_spend(session, org.id) == pytest.approx(total)
        finish_ai_action(session, action.action_id)
        assert monthly_ai_spend(session, org.id) == pytest.approx(total)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ],
)
def test_native_standard_usage_reaches_durable_ledger_without_full_cap_charge(
    settings: Settings, stream: bool, model: str
) -> None:
    payload = {
        "candidates": [{"content": {"parts": [{"text": "A short answer."}]}}],
        "usageMetadata": {
            "serviceTier": "standard",
            "promptTokenCount": 100,
            "cachedContentTokenCount": 50,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 2,
            "totalTokenCount": 107,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["serviceTier"] == "standard"
        assert body["generationConfig"]["maxOutputTokens"] == 3200
        if stream:
            return httpx.Response(200, text=f"data: {json.dumps(payload)}\n\n")
        return httpx.Response(200, json=payload)

    init_db()
    with (
        db_session() as session,
        httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            transport=httpx.MockTransport(handler),
        ) as http,
    ):
        org = Org(name="Synthetic native Standard usage", plan="community")
        session.add(org)
        session.flush()
        action = begin_ai_action(
            session, org, action="question", resource_type="chat", resource_id="native-usage"
        )
        pool = LLMPool(
            {"gemini": GeminiClient(api_key="inert-test-key", http=http, max_retries=0)},
            RoutingConfig(synthesis=ModelRef("gemini", model)),
            BudgetGovernor(limit_usd=action.cost_limit_usd),
        )
        attach_action_usage_sink(
            session,
            pool,
            org_id=org.id,
            action_id=action.action_id,
            resource_type="chat",
            resource_id="native-usage",
        )
        deltas: list[str] = []
        response = pool.complete(
            TaskType.CHAT,
            system="Synthetic system",
            prompt="Synthetic short question",
            max_tokens=3200,
            on_delta=deltas.append if stream else None,
        )
        session.flush()
        expected = cost_usd(model, 100, 7, cached_input_tokens=50)
        row = session.query(LLMCallRow).filter_by(action_id=action.action_id).one()
        assert (row.input_tokens, row.output_tokens) == (100, 7)
        assert response.cached_input_tokens == 50
        assert row.cost_source == response.cost_source == "gemini_usage"
        assert row.cost_usd == pytest.approx(expected)
        assert pool.budget.spent_usd == pytest.approx(expected)
        assert monthly_ai_spend(session, org.id) == pytest.approx(expected)
        finish_ai_action(session, action.action_id)
        session.flush()
        assert ai_cost_reserved(session, org.id) == 0
        assert monthly_ai_spend(session, org.id) == pytest.approx(expected)


def test_native_cost_sources_fit_existing_postgres_ledger_columns() -> None:
    """SQLite accepts oversized VARCHAR values; assert the production bound too."""
    text = gemini_usage_cost_usd(
        "gemini-3.5-flash", {"promptTokenCount": 100, "candidatesTokenCount": 10}
    )
    image = gemini_usage_cost_usd(
        "gemini-3-pro-image",
        {"promptTokenCount": 100, "candidatesTokenCount": 1120},
        output_image_count=1,
        image_size="1K",
    )
    assert text is not None and image is not None
    sources = [text[1], image[1]]
    report_gemini_uncertain_cost(
        "gemini-3.5-flash",
        task="transcribe",
        reserved_cost_usd=0.1,
        on_cost=lambda task, model, cost, source: sources.append(source),
    )
    assert set(sources) == {"gemini_usage", "gemini_estimate", "uncertain"}
    for row in (LLMCallRow, ProviderCostRow):
        maximum = row.__table__.c.cost_source.type.length
        assert maximum == 16
        assert all(len(source) <= maximum for source in sources)
