"""Keyless routing, request and accounting guards for internal Lite reviewers."""

import json

import httpx
import pytest

from sixsentences_server.config import get_settings
from sixsentences_server.llm.base import (
    BudgetGovernor,
    LLMConfigError,
    ModelRef,
    TaskType,
    cost_usd,
    price_of,
)
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.gemini_pricing import gemini_usage_cost_usd
from sixsentences_server.llm.mock import MockClient
from sixsentences_server.llm.models import available_chat_models, resolve_chat_model
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.privacy import (
    PRIVATE_DEFAULT_MODEL,
    PRIVATE_PREMIUM_MODEL,
    PRIVATE_SCREENING_MODELS,
)


def test_internal_lite_reviewers_do_not_become_chat_choices() -> None:
    assert {model.id for model in available_chat_models()} == {
        PRIVATE_DEFAULT_MODEL,
        PRIVATE_PREMIUM_MODEL,
    }
    for internal in PRIVATE_SCREENING_MODELS:
        assert resolve_chat_model(internal, allow_locked=True).id == PRIVATE_DEFAULT_MODEL


def test_previous_lite_tariff_remains_historical_but_cannot_reenter_private_dispatch() -> None:
    old_ref = ModelRef("gemini", "gemini-2.5-flash-lite")
    assert price_of(old_ref.model) == (0.1, 0.4)
    assert resolve_chat_model(old_ref.model, allow_locked=True).id == PRIVATE_DEFAULT_MODEL
    client = MockClient()
    pool = LLMPool(
        {"gemini": client}, RoutingConfig(screening=[old_ref]), BudgetGovernor(limit_usd=1)
    )
    with pytest.raises(LLMConfigError, match="Private workspace"):
        pool.complete(TaskType.SCREENING, system="Synthetic reviewer", prompt="Synthetic record")
    assert client.calls == []


def test_default_private_screening_keeps_two_distinct_inexpensive_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "inert-test-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    try:
        routing = RoutingConfig.defaults()
    finally:
        get_settings.cache_clear()
    refs = [ModelRef("gemini", model) for model in PRIVATE_SCREENING_MODELS]
    assert routing.screening == refs
    assert routing.adjudication == refs[1]
    pool = LLMPool({"gemini": MockClient()}, routing, BudgetGovernor(limit_usd=1))
    for model in (PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL):
        pinned = pool.pinned(ModelRef("gemini", model))
        assert pinned.screening_refs() == refs
        assert pinned.precision_refs() == list(reversed(refs))
        assert pinned.synthesis_refs()[0] == ModelRef("gemini", model)
        for task in (TaskType.CHAT, TaskType.PROTOCOL_SYNTHESIS, TaskType.CLAIM_VERIFICATION):
            assert all(
                ref.model not in PRIVATE_SCREENING_MODELS
                for ref in pinned._completion_candidates(task, None)
            )


def test_lite_standard_costs_are_eighty_percent_below_previous_screening_pair() -> None:
    assert price_of(PRIVATE_SCREENING_MODELS[0]) == (0.25, 1.5)
    assert price_of(PRIVATE_SCREENING_MODELS[1]) == (0.3, 2.5)
    before = sum(
        cost_usd(model, 1000, 100) for model in (PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL)
    )
    after = sum(cost_usd(model, 1000, 100) for model in PRIVATE_SCREENING_MODELS)
    assert before * 1000 == pytest.approx(5.6)
    assert after * 1000 == pytest.approx(0.95)
    assert after / before < 0.2


@pytest.mark.parametrize(
    "model,thinking",
    [
        ("gemini-3.1-flash-lite", {"thinkingLevel": "minimal", "includeThoughts": False}),
        ("gemini-3.5-flash-lite", {"thinkingLevel": "minimal", "includeThoughts": False}),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
def test_native_lite_request_and_billable_thinking_stay_exact(
    model: str, thinking: dict[str, object], stream: bool
) -> None:
    metadata = {
        "serviceTier": "standard",
        "promptTokenCount": 1000,
        "cachedContentTokenCount": 200,
        "candidatesTokenCount": 100,
        "thoughtsTokenCount": 20,
        "totalTokenCount": 1120,
    }
    answer = '{"verdict":"include","reason":"Meets the criterion","quote":"test"}'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            f"/{model}:streamGenerateContent" if stream else f"/{model}:generateContent"
        )
        body = json.loads(request.content)
        assert body["serviceTier"] == "standard"
        assert body["generationConfig"]["thinkingConfig"] == thinking
        assert body["generationConfig"]["maxOutputTokens"] == 300
        assert "tools" not in body and "cachedContent" not in body
        if not stream:
            assert body["generationConfig"]["responseMimeType"] == "application/json"
        payload = {
            "candidates": [{"content": {"parts": [{"text": answer}]}}],
            "usageMetadata": metadata,
        }
        return (
            httpx.Response(200, text=f"data: {json.dumps(payload)}\n\n")
            if stream
            else httpx.Response(200, json=payload)
        )

    client = GeminiClient(
        api_key="inert-test-key",
        max_retries=0,
        http=httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            transport=httpx.MockTransport(handler),
        ),
    )
    pool = LLMPool(
        {"gemini": client},
        RoutingConfig(screening=[ModelRef("gemini", model)]),
        BudgetGovernor(limit_usd=0.01),
    )
    pool.complete(
        TaskType.SCREENING,
        system="s",
        prompt="test",
        max_tokens=300,
        json_response=True,
        on_delta=(lambda _: None) if stream else None,
    )
    assert pool.usage[0].output_tokens == 120
    assert pool.usage[0].cost_source == "gemini_usage"
    expected = cost_usd(model, 1000, 120, cached_input_tokens=200)
    assert pool.budget.spent_usd == pytest.approx(expected)
    assert gemini_usage_cost_usd(model, metadata) == (expected, "gemini_usage")
