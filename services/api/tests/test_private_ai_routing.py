"""Private-content routes cannot inherit historical OpenRouter preferences."""

import json

import httpx
import pytest

from sixsentences_server.config import get_settings
from sixsentences_server.core.structured_output import structured_recovery_pool
from sixsentences_server.llm.base import (
    BudgetGovernor,
    LLMCancelledError,
    LLMConfigError,
    ModelRef,
    TaskType,
    cost_usd,
)
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.mock import MockClient
from sixsentences_server.llm.models import (
    AUTO_ID,
    available_chat_models,
    pinned_ref,
    public_research_models,
    resolve_chat_model,
)
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.privacy import PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL
from sixsentences_server.llm.providers import PROVIDERS, ProviderError, available_specs


def _native_client(handler) -> GeminiClient:  # type: ignore[no-untyped-def]
    return GeminiClient(
        api_key="inert-test-key",
        max_retries=0,
        http=httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            transport=httpx.MockTransport(handler),
        ),
    )


@pytest.mark.parametrize(
    "old",
    [
        None,
        "auto",
        "sixsentences-router",
        "deepseek-v4-flash",
        "claude-sonnet-5",
        "google/gemini-3.5-flash",
    ],
)
def test_old_model_preferences_resolve_to_actual_direct_default(old: str | None) -> None:
    model = resolve_chat_model(old, allow_locked=True)
    assert model.id == AUTO_ID == PRIVATE_DEFAULT_MODEL
    assert pinned_ref(model) == ModelRef("gemini", PRIVATE_DEFAULT_MODEL)
    assert model.label == "Gemini 3.5 Flash"


def test_private_and_public_catalogs_remain_separate() -> None:
    assert {model.provider for model in available_chat_models()} == {"gemini"}
    assert len(available_chat_models()) == 2
    assert {model.provider for model in public_research_models()} == {"openrouter"}
    assert len(public_research_models()) == 13


def test_paid_gemini_is_required_even_when_openrouter_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for spec in PROVIDERS.values():
        monkeypatch.setenv(spec.key_env, "")
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "inert-public-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "0")
    get_settings.cache_clear()
    try:
        assert available_specs() == []
        assert [spec.name for spec in available_specs(content_scope="public")] == ["openrouter"]
        monkeypatch.setenv("SIX_GEMINI_API_KEY", "inert-private-key")
        monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
        get_settings.cache_clear()
        pool = LLMPool.from_environment(BudgetGovernor(limit_usd=1))
        assert set(pool.clients) == {"gemini"}
        assert pool.content_scope == "private"
        assert isinstance(pool.clients["gemini"], GeminiClient)
        with pytest.raises(LLMConfigError, match="Private workspace"):
            LLMPool.from_environment(
                BudgetGovernor(limit_usd=1),
                routing_json='{"synthesis":"openrouter:deepseek/deepseek-v4-flash"}',
            )
    finally:
        get_settings.cache_clear()


def test_private_pool_blocks_pinning_and_manually_injected_openrouter_dispatch() -> None:
    client = MockClient()
    pool = LLMPool(
        {"openrouter": client},
        RoutingConfig(
            synthesis=ModelRef("openrouter", "deepseek/deepseek-v4-flash"),
        ),
        BudgetGovernor(limit_usd=1),
    )
    with pytest.raises(LLMConfigError, match="Private workspace"):
        pool.complete(TaskType.CHAT, system="s", prompt="Private interview")
    with pytest.raises(LLMConfigError, match="Private workspace"):
        pool.pinned(ModelRef("openrouter", "deepseek/deepseek-v4-flash"))
    assert client.calls == []


def test_private_provider_failure_never_falls_back_to_openrouter() -> None:
    def fail(_model: str, _prompt: str) -> str:
        raise ProviderError("temporary Gemini outage")

    google, router = MockClient(fail), MockClient()
    pool = LLMPool(
        {"gemini": google, "openrouter": router},
        RoutingConfig(
            synthesis=ModelRef("gemini", PRIVATE_DEFAULT_MODEL),
            adjudication=ModelRef("openrouter", "deepseek/deepseek-v4-flash"),
        ),
        BudgetGovernor(limit_usd=1),
    )
    with pytest.raises(LLMConfigError, match="Private workspace"):
        pool.complete(TaskType.CHAT, system="s", prompt="Private manuscript")
    assert len(google.calls) == 1
    assert router.calls == []


def test_structured_recovery_stays_on_direct_gemini() -> None:
    pool = LLMPool(
        {"gemini": MockClient()},
        RoutingConfig(
            synthesis=ModelRef("gemini", PRIVATE_PREMIUM_MODEL),
        ),
        BudgetGovernor(limit_usd=1),
    )
    recovered = structured_recovery_pool(pool)
    assert recovered.routing.synthesis == ModelRef("gemini", PRIVATE_DEFAULT_MODEL)
    assert recovered.content_scope == "private"


def test_native_json_payload_usage_and_hidden_thoughts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(f"/{PRIVATE_DEFAULT_MODEL}:generateContent")
        body = json.loads(request.content)
        assert set(body) == {"serviceTier", "systemInstruction", "contents", "generationConfig"}
        assert body["serviceTier"] == "standard"
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["thinkingConfig"]["includeThoughts"] is False
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "private thought", "thought": True},
                                {"text": '{"ok":true}'},
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "serviceTier": "standard",
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 20,
                    "thoughtsTokenCount": 30,
                    "cachedContentTokenCount": 10,
                    "totalTokenCount": 150,
                },
            },
        )

    response = _native_client(handler).complete(
        PRIVATE_DEFAULT_MODEL,
        system="private system",
        prompt="private manuscript",
        json_response=True,
    )
    assert response.text == '{"ok":true}'
    assert (response.input_tokens, response.output_tokens, response.cached_input_tokens) == (
        100,
        50,
        10,
    )
    assert response.provider == "gemini"
    assert response.cost_source == "gemini_usage"


def test_native_stream_preserves_visible_deltas_and_final_usage_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":streamGenerateContent")
        assert json.loads(request.content)["serviceTier"] == "standard"
        events = [
            {"candidates": [{"content": {"parts": [{"text": "secret", "thought": True}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "Hello "}]}}]},
            {
                "candidates": [{"content": {"parts": [{"text": "world"}]}}],
                "usageMetadata": {
                    "serviceTier": "standard",
                    "promptTokenCount": 30,
                    "candidatesTokenCount": 5,
                    "thoughtsTokenCount": 12,
                    "totalTokenCount": 47,
                },
            },
        ]
        return httpx.Response(
            200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events)
        )

    visible: list[str] = []
    thoughts: list[str] = []
    response = _native_client(handler).complete_stream(
        PRIVATE_DEFAULT_MODEL,
        system="system",
        prompt="private",
        on_delta=visible.append,
        on_reasoning=thoughts.append,
    )
    assert visible == ["Hello ", "world"]
    assert thoughts == []
    assert response.text == "Hello world"
    assert response.input_tokens == 30
    assert response.output_tokens == 17
    assert response.cost_source == "gemini_usage"


def test_missing_usage_uses_conservative_reserve_not_zero() -> None:
    client = _native_client(
        lambda _: httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "answer"}]}}],
            },
        )
    )
    response = client.complete(PRIVATE_DEFAULT_MODEL, system="s", prompt="日本語", max_tokens=100)
    assert response.output_tokens == 100
    assert response.input_tokens >= len("s日本語".encode())
    assert response.cost_source == "gemini_estimate"


def test_native_rejects_unreviewed_model_before_transport() -> None:
    client = _native_client(lambda _: pytest.fail("must not send private content"))
    with pytest.raises(LLMConfigError):
        client.complete("gemini-flash-latest", system="s", prompt="private")


@pytest.mark.parametrize("failure", ["read_timeout", "write_timeout", "http_503", "invalid_json"])
def test_billable_native_failures_are_not_retried_or_lost(failure: str) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if failure == "read_timeout":
            raise httpx.ReadTimeout("private transport detail")
        if failure == "write_timeout":
            raise httpx.WriteTimeout("private transport detail")
        return httpx.Response(503 if failure == "http_503" else 200, text="invalid JSON")

    client = _native_client(handler)
    client.max_retries = 2
    budget = BudgetGovernor(limit_usd=1)
    pool = LLMPool(
        {"gemini": client},
        RoutingConfig(
            synthesis=ModelRef("gemini", PRIVATE_DEFAULT_MODEL),
        ),
        budget,
    )
    with pytest.raises(ProviderError):
        pool.complete(TaskType.CHAT, system="s", prompt="private", max_tokens=100)
    assert len(attempts) == budget.calls == len(pool.usage) == 1
    assert pool.usage[0].cost_source == "uncertain"
    payload = json.loads(attempts[0].content)
    sent_system = payload["systemInstruction"]["parts"][0]["text"]
    sent_prompt = payload["contents"][0]["parts"][0]["text"]
    input_bound = len(sent_system.encode()) + len(sent_prompt.encode()) + 256
    assert budget.spent_usd == pytest.approx(cost_usd(PRIVATE_DEFAULT_MODEL, input_bound, 100))


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_rejected_native_requests_do_not_consume_capacity(status: int) -> None:
    client = _native_client(lambda _: httpx.Response(status))
    budget = BudgetGovernor(limit_usd=1)
    pool = LLMPool(
        {"gemini": client},
        RoutingConfig(
            synthesis=ModelRef("gemini", PRIVATE_DEFAULT_MODEL),
        ),
        budget,
    )
    with pytest.raises(ProviderError):
        pool.complete(TaskType.CHAT, system="s", prompt="p")
    assert budget.spent_usd == budget.calls == 0
    assert pool.usage == []


def test_incomplete_native_json_still_records_observed_usage() -> None:
    client = _native_client(
        lambda _: httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": '{"not_complete":'}]}}],
                "usageMetadata": {
                    "promptTokenCount": 30,
                    "candidatesTokenCount": 8,
                    "thoughtsTokenCount": 12,
                    "totalTokenCount": 50,
                },
            },
        )
    )
    budget = BudgetGovernor(limit_usd=1)
    pool = LLMPool(
        {"gemini": client},
        RoutingConfig(
            synthesis=ModelRef("gemini", PRIVATE_DEFAULT_MODEL),
        ),
        budget,
    )
    with pytest.raises(ProviderError):
        pool.complete_json(TaskType.CHAT, system="s", prompt="p")
    assert len(pool.usage) == 1
    assert pool.usage[0].output_tokens == 20
    assert pool.usage[0].cost_source == "gemini_usage"


def test_native_stream_cancellation_settles_observed_usage_before_propagating() -> None:
    cancelled = []
    event = {
        "candidates": [{"content": {"parts": [{"text": "partial"}]}}],
        "usageMetadata": {
            "promptTokenCount": 30,
            "candidatesTokenCount": 8,
            "thoughtsTokenCount": 12,
            "totalTokenCount": 50,
        },
    }
    client = _native_client(lambda _: httpx.Response(200, text=f"data: {json.dumps(event)}\n\n"))
    budget = BudgetGovernor(limit_usd=1)
    pool = LLMPool(
        {"gemini": client},
        RoutingConfig(
            synthesis=ModelRef("gemini", PRIVATE_DEFAULT_MODEL),
        ),
        budget,
    )
    with pytest.raises(LLMCancelledError):
        pool.complete(
            TaskType.CHAT,
            system="s",
            prompt="p",
            on_delta=lambda _: cancelled.append(True),
            cancel_check=lambda: bool(cancelled),
        )
    assert len(pool.usage) == budget.calls == 1
    assert pool.usage[0].output_tokens == 20
