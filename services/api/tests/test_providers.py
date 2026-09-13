"""Provider registry, OpenAI-compatible client, and routing config."""

import json
import threading
import time

import httpx
import pytest

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.llm.base import (
    PRICES_PER_MTOK,
    BudgetExceededError,
    BudgetGovernor,
    LLMCancelledError,
    LLMConfigError,
    LLMResponse,
    ModelRef,
    TaskType,
    cache_read_price_of,
    cost_usd,
    openrouter_price_cap_of,
    openrouter_request_fee_policy_of,
    openrouter_request_price_cap_of,
    price_ceiling_of,
    price_of,
    projected_cost_usd,
    settled_cost_source,
    settled_cost_usd,
)
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.providers import (
    PROVIDERS,
    OpenAICompatClient,
    ProviderError,
    available_specs,
    build_client,
    default_screening_refs,
    default_strong_ref,
)

ALL_KEY_ENVS = [spec.key_env for spec in PROVIDERS.values()]


def test_openrouter_base_url_is_centralized_and_https_only() -> None:
    settings = Settings(openrouter_base_url="https://gateway.example/v1/")
    assert settings.openrouter_base_url == "https://gateway.example/v1"
    assert settings.openrouter_endpoint("chat/completions") == (
        "https://gateway.example/v1/chat/completions"
    )
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        Settings(openrouter_base_url="http://gateway.example/v1")
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        Settings(openrouter_base_url="https://user:secret@gateway.example/v1")


@pytest.fixture()
def no_keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for env in ALL_KEY_ENVS:
        monkeypatch.setenv(env, "")
    get_settings.cache_clear()
    return monkeypatch


def _compat_client(
    handler,  # type: ignore[no-untyped-def]
    *,
    provider: str = "deepseek",
    sleeps: list[float] | None = None,
) -> OpenAICompatClient:
    return OpenAICompatClient(
        provider=provider,
        base_url="https://api.deepseek.com/v1",
        api_key="k",
        http=httpx.Client(
            base_url="https://api.deepseek.com/v1",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer k"},
        ),
        max_retries=1,
        sleeper=(sleeps.append if sleeps is not None else lambda _delay: None),
        jitter=lambda delay: delay,
    )


class _InterruptedStream(httpx.SyncByteStream):
    def __init__(self, request: httpx.Request, first_chunk: bytes) -> None:
        self.request = request
        self.first_chunk = first_chunk

    def __iter__(self):  # type: ignore[no-untyped-def]
        yield self.first_chunk
        raise httpx.ReadError("connection reset", request=self.request)


class _BlockingStream(httpx.SyncByteStream):
    """A stream that only returns after another thread closes the response."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.closed = threading.Event()

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.started.set()
        self.closed.wait(timeout=5.0)
        if False:  # pragma: no cover - keeps this method a byte iterator
            yield b""

    def close(self) -> None:
        self.closed.set()


def test_openai_compat_request_and_usage_mapping() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"verdict":"include"}'}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "cost": 0.0042,
                },
            },
        )

    client = _compat_client(handler, provider="openrouter")
    response = client.complete("openai/gpt-5.6-terra-pro", system="sys", prompt="hello")
    assert response.text == '{"verdict":"include"}'
    assert response.provider == "openrouter"
    assert response.input_tokens == 100 and response.output_tokens == 20
    assert response.cached_input_tokens == 40
    assert response.cost_usd == pytest.approx(0.0042)
    assert response.cost_source == "provider"

    request = seen[0]
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"] == "Bearer k"
    assert request.headers["x-openrouter-cache"] == "false"
    import json as _json

    body = _json.loads(request.content)
    assert body["model"] == "openai/gpt-5.6-terra-pro"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["provider"]["data_collection"] == "deny"
    assert body["provider"]["zdr"] is True
    input_price, output_price = openrouter_price_cap_of("openai/gpt-5.6-terra-pro")
    assert body["provider"]["max_price"] == {
        "prompt": input_price,
        "completion": output_price,
        "request": 0.0,
    }


def test_openrouter_sonar_completion_caps_the_reserved_flat_request_fee() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Grounded answer."}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            },
        )

    response = _compat_client(handler, provider="openrouter").complete(
        "perplexity/sonar-reasoning-pro",
        system="s",
        prompt="p",
    )

    assert response.text == "Grounded answer."
    provider = bodies[0]["provider"]
    assert isinstance(provider, dict)
    assert provider["max_price"] == {
        "prompt": 2.2,
        "completion": 8.8,
        "request": 0.014,
    }


def test_openai_compat_cancelled_before_request_never_calls_provider() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "late"}}]})

    with pytest.raises(LLMCancelledError, match="cancelled"):
        _compat_client(handler).complete(
            "deepseek-v4-flash",
            system="s",
            prompt="p",
            cancel_check=lambda: True,
        )

    assert attempts["n"] == 0


def test_openrouter_pro_ceiling_allows_privacy_compatible_endpoints() -> None:
    """The route ceiling must not assume a disallowed collecting provider."""

    input_price, output_price = price_of("deepseek/deepseek-v4-pro")

    assert (input_price, output_price) == (1.60, 3.20)


def test_catalog_applies_long_context_tiers_and_conservative_route_ceilings() -> None:
    assert price_of("openai/gpt-5.6-terra-pro", 271_999) == (2.0, 12.0)
    assert price_of("openai/gpt-5.6-terra-pro", 272_000) == (4.0, 18.0)
    assert price_ceiling_of("openai/gpt-5.6-terra-pro") == (4.0, 18.0)
    assert cache_read_price_of("openai/gpt-5.6-terra-pro", 271_999) == 0.20
    assert cache_read_price_of("openai/gpt-5.6-terra-pro", 272_000) == 0.40

    assert price_of("openai/gpt-5.6-luna-pro", 271_999) == (0.20, 1.20)
    assert price_of("openai/gpt-5.6-luna-pro", 272_000) == (0.40, 1.80)
    assert price_ceiling_of("openai/gpt-5.6-luna-pro") == (0.40, 1.80)
    assert cache_read_price_of("openai/gpt-5.6-luna-pro", 272_000) == 0.04
    # Verified million-token ZDR route: DeepInfra at $0.20/$0.80 per MTok.
    assert price_of("meta-llama/llama-4-maverick") == (0.20, 0.80)
    assert openrouter_price_cap_of("meta-llama/llama-4-maverick") == (0.22, 0.88)
    assert price_of("qwen/qwen3.7-flash", 32_000) == (0.10, 0.40)
    assert price_of("qwen/qwen3.7-flash", 256_000) == (0.20, 0.80)
    assert price_of("nvidia/nemotron-3-super-120b-a12b", 262_145) == (0.085, 0.40)
    assert price_ceiling_of("nvidia/nemotron-3-super-120b-a12b") == (0.085, 0.40)
    assert price_of("gemini-pro-latest", 200_001) == (4.0, 18.0)
    assert cache_read_price_of("qwen-plus", 256_000) == pytest.approx(0.08)
    assert cache_read_price_of("qwen-plus", 256_001) == pytest.approx(0.24)


def test_deepseek_peak_prices_and_cache_hits_drive_catalog_accounting() -> None:
    # Direct DeepSeek has a time-dependent half-price window. The pre-call
    # ledger deliberately uses the official peak tariff at every hour so a
    # request crossing into peak time cannot be admitted against a lower rate.
    assert price_of("deepseek-v4-flash") == (0.44, 1.32)
    assert price_of("deepseek-v4-pro") == (1.32, 3.96)
    assert "deepseek-chat" not in PRICES_PER_MTOK
    assert "deepseek-reasoner" not in PRICES_PER_MTOK
    assert "kimi-k2-turbo-preview" not in PRICES_PER_MTOK
    assert cache_read_price_of("deepseek-v4-flash") == pytest.approx(0.014)
    assert cache_read_price_of("deepseek-v4-pro") == pytest.approx(0.044)
    # OpenRouter annotations follow the checked accounting route, while actual
    # routed calls settle from usage.cost or the uncached route-cap fallback.
    assert cache_read_price_of("deepseek/deepseek-v4-flash") == pytest.approx(0.018)
    assert cache_read_price_of("deepseek/deepseek-v4-pro") == pytest.approx(0.33)

    billed = cost_usd(
        "deepseek-v4-flash",
        1_000_000,
        1_000_000,
        cached_input_tokens=250_000,
    )
    assert billed == pytest.approx(1.6535)


def test_openrouter_preflight_uses_routing_cap_and_sonar_reserves() -> None:
    assert openrouter_price_cap_of("deepseek/deepseek-v4-flash") == (0.099, 0.198)
    assert openrouter_price_cap_of("perplexity/sonar") == (1.1, 1.1)
    assert openrouter_request_price_cap_of("perplexity/sonar") == pytest.approx(0.006)

    # Nemotron is capped to its currently operational 262k ZDR endpoint.
    projected = projected_cost_usd(
        "nvidia/nemotron-3-super-120b-a12b",
        100_000,
        1_000,
        provider="openrouter",
    )
    assert projected == pytest.approx(0.00979)
    assert projected > cost_usd("nvidia/nemotron-3-super-120b-a12b", 100_000, 1_000)

    sonar_projection = projected_cost_usd(
        "perplexity/sonar-reasoning-pro",
        1_000,
        1_000,
        provider="openrouter",
    )
    sonar_policy = openrouter_request_fee_policy_of("perplexity/sonar-reasoning-pro")
    assert sonar_policy is not None
    assert sonar_policy.reserve_usd == sonar_policy.max_price_usd == 0.014
    assert openrouter_request_price_cap_of("perplexity/sonar-reasoning-pro") == pytest.approx(0.014)
    assert sonar_projection == pytest.approx(0.025)
    assert sonar_projection == pytest.approx((2.2 + 8.8) / 1_000 + 0.014)
    BudgetGovernor(limit_usd=sonar_projection).check(sonar_projection)
    with pytest.raises(BudgetExceededError, match="budget exhausted"):
        BudgetGovernor(limit_usd=sonar_projection - 0.000001).check(sonar_projection)

    # Routes without a contractually bounded flat request fee retain token
    # caps only. Deep Research's example-based reserve is not misrepresented
    # as a provider-side fee ceiling.
    assert openrouter_request_price_cap_of("deepseek/deepseek-v4-flash") is None
    assert openrouter_request_price_cap_of("perplexity/sonar-deep-research") is None
    assert projected_cost_usd(
        "perplexity/sonar-deep-research",
        1_000,
        1_000,
        provider="openrouter",
    ) == pytest.approx(1.331)
    assert projected_cost_usd(
        "deepseek-v4-flash",
        1_000,
        1_000,
        provider="deepseek",
    ) == pytest.approx(cost_usd("deepseek-v4-flash", 1_000, 1_000))


def test_pool_rejects_sonar_deep_research_before_exceeding_budget() -> None:
    class UnexpectedClient:
        calls = 0

        def complete(self, model: str, **kwargs: object) -> LLMResponse:
            self.calls += 1
            raise AssertionError("provider must not be called")

        def complete_stream(self, model: str, **kwargs: object) -> LLMResponse:
            return self.complete(model, **kwargs)

    client = UnexpectedClient()
    pool = LLMPool(
        clients={"openrouter": client},  # type: ignore[dict-item]
        routing=RoutingConfig(
            synthesis=ModelRef("openrouter", "perplexity/sonar-deep-research"),
            adjudication=None,
            screening=[],
        ),
        budget=BudgetGovernor(limit_usd=1.0),
        content_scope="public",
    )

    with pytest.raises(BudgetExceededError, match="budget exhausted"):
        pool.complete(
            TaskType.CHAT,
            system="system",
            prompt="question",
            max_tokens=1,
        )

    assert client.calls == 0


def test_direct_cache_usage_is_retained_for_budget_accounting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                    "prompt_cache_hit_tokens": 250_000,
                },
            },
        )

    response = _compat_client(handler).complete("deepseek-v4-flash", system="s", prompt="p")
    budget = BudgetGovernor(limit_usd=10.0)
    budget.record(TaskType.CHAT, response)

    assert response.cached_input_tokens == 250_000
    assert budget.spent_usd == pytest.approx(1.6535)


def test_openrouter_missing_provider_cost_settles_at_routing_cap() -> None:
    response = LLMResponse(
        text="ok",
        model="deepseek/deepseek-v4-flash",
        provider="openrouter",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    base_catalog_cost = cost_usd(
        response.model,
        response.input_tokens,
        response.output_tokens,
    )
    expected_cap_cost = projected_cost_usd(
        response.model,
        response.input_tokens,
        response.output_tokens,
        provider="openrouter",
    )

    budget = BudgetGovernor(limit_usd=1.0)
    budget.record(TaskType.CHAT, response)

    assert expected_cap_cost > base_catalog_cost
    assert settled_cost_usd(response) == pytest.approx(expected_cap_cost)
    assert settled_cost_source(response) == "catalog_cap"
    assert budget.spent_usd == pytest.approx(expected_cap_cost)


def test_pool_uses_same_openrouter_routing_cap_fallback_for_usage() -> None:
    class MissingCostClient:
        def complete(self, model: str, **_kwargs: object) -> LLMResponse:
            return LLMResponse(
                text="ok",
                model=model,
                provider="openrouter",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            )

        def complete_stream(self, model: str, **kwargs: object) -> LLMResponse:
            return self.complete(model, **kwargs)

    model = "deepseek/deepseek-v4-flash"
    budget = BudgetGovernor(limit_usd=1.0)
    pool = LLMPool(
        clients={"openrouter": MissingCostClient()},  # type: ignore[dict-item]
        routing=RoutingConfig(
            synthesis=ModelRef("openrouter", model),
            adjudication=None,
            screening=[],
        ),
        budget=budget,
        content_scope="public",
    )

    pool.complete(TaskType.CHAT, system="system", prompt="question")

    expected_cap_cost = projected_cost_usd(
        model,
        1_000_000,
        1_000_000,
        provider="openrouter",
    )
    assert budget.spent_usd == pytest.approx(expected_cap_cost)
    assert pool.usage[0].cost_usd == pytest.approx(expected_cap_cost)
    assert pool.usage[0].cost_source == "catalog_cap"


def test_openai_compat_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    client = _compat_client(handler)
    assert client.complete("deepseek-v4-flash", system="s", prompt="p").text == "ok"
    assert attempts["n"] == 2


def test_openai_compat_retries_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    with pytest.raises(ProviderError, match="retries exhausted"):
        _compat_client(handler).complete("deepseek-v4-flash", system="s", prompt="p")


def test_openai_compat_never_exposes_provider_error_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_body = "private prompt and document excerpt"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=private_body)

    with pytest.raises(ProviderError) as caught:
        _compat_client(handler).complete("deepseek-v4-flash", system="s", prompt="p")

    assert private_body not in str(caught.value)
    assert private_body not in caplog.text


def test_openai_compat_honors_bounded_retry_after() -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, text="rate limited", headers={"Retry-After": "20"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    response = _compat_client(handler, sleeps=sleeps).complete(
        "deepseek-v4-flash", system="s", prompt="p"
    )

    assert response.text == "ok"
    assert attempts["n"] == 2
    assert sleeps == [8.0]


def test_openai_compat_retries_malformed_success_response() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(200, text="not-json")
        return httpx.Response(200, json={"choices": [{"message": {"content": "recovered"}}]})

    response = _compat_client(handler).complete("deepseek-v4-flash", system="s", prompt="p")

    assert response.text == "recovered"
    assert attempts["n"] == 2


def test_openai_compat_fails_over_instead_of_repeating_a_read_timeout() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ReadTimeout("provider stalled", request=request)

    with pytest.raises(ProviderError, match="retries exhausted"):
        _compat_client(handler).complete("deepseek-v4-flash", system="s", prompt="p")

    assert attempts["n"] == 1


def test_openai_compat_does_not_retry_permanent_422() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(422, text="invalid request")

    with pytest.raises(ProviderError, match="422.*permanent"):
        _compat_client(handler).complete("deepseek-v4-flash", system="s", prompt="p")
    assert attempts["n"] == 1


def test_openrouter_policy_404_fails_without_retrying() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(404, text="no endpoints match data policy")

    with pytest.raises(ProviderError, match="404.*permanent"):
        _compat_client(handler, provider="openrouter").complete(
            "deepseek/deepseek-v4-pro",
            system="s",
            prompt="p",
        )
    assert attempts["n"] == 1


def test_openrouter_json_completion_requires_format_support_without_plugins() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    response = _compat_client(handler, provider="openrouter").complete(
        "deepseek/deepseek-v4-flash",
        system="Return JSON.",
        prompt="Answer.",
        json_response=True,
    )

    assert response.text == '{"answer":"ok"}'
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "plugins" not in bodies[0]
    provider = bodies[0]["provider"]
    assert isinstance(provider, dict)
    assert provider["require_parameters"] is True


def test_structured_completion_retries_invalid_json_then_returns_valid_object() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        content = '{"answer":' if len(bodies) == 1 else '{"answer":"verified"}'
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    response = _compat_client(handler, provider="openrouter").complete(
        "deepseek/deepseek-v4-flash",
        system="Return JSON.",
        prompt="Answer.",
        json_response=True,
    )

    assert response.text == '{"answer":"verified"}'
    assert len(bodies) == 2
    assert all("plugins" not in body for body in bodies)


def test_structured_completion_fails_closed_after_invalid_json_retries() -> None:
    attempts = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer":'}}]},
        )

    with pytest.raises(ProviderError, match="malformed structured completion"):
        _compat_client(handler, provider="openrouter").complete(
            "deepseek/deepseek-v4-flash",
            system="Return JSON.",
            prompt="Answer.",
            json_response=True,
        )

    assert attempts["count"] == 2


def test_openrouter_mandatory_reasoning_model_uses_low_non_streaming() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = OpenAICompatClient(
        "openrouter",
        "https://example.invalid",
        "test",
        http=httpx.Client(
            base_url="https://example.invalid",
            transport=httpx.MockTransport(handler),
        ),
        extra_body={"reasoning": {"effort": "none"}},
    )
    response = client.complete(
        "google/gemini-3.7-flash",
        system="s",
        prompt="p",
    )

    assert response.text == "ok"
    assert bodies[0]["reasoning"] == {"effort": "low", "exclude": True}


def test_pool_falls_back_to_a_second_model_after_provider_failure() -> None:
    class FailingThenHealthyClient:
        def __init__(self) -> None:
            self.models: list[str] = []

        def complete(self, model: str, **kwargs: object) -> LLMResponse:
            self.models.append(model)
            if model == "strong-model":
                raise ProviderError("temporary gateway failure")
            return LLMResponse(
                text="recovered",
                model=model,
                provider="mock",
                input_tokens=4,
                output_tokens=2,
            )

        def complete_stream(self, model: str, **kwargs: object) -> LLMResponse:
            return self.complete(model, **kwargs)

    client = FailingThenHealthyClient()
    pool = LLMPool(
        clients={"mock": client},  # type: ignore[dict-item]
        routing=RoutingConfig(
            synthesis=ModelRef("mock", "strong-model"),
            adjudication=ModelRef("mock", "fallback-model"),
            screening=[ModelRef("mock", "fallback-model")],
        ),
        budget=BudgetGovernor(limit_usd=1.0),
    )

    response = pool.complete(TaskType.CHAT, system="system", prompt="question")

    assert response.text == "recovered"
    assert client.models == ["strong-model", "fallback-model"]


def test_pool_propagates_cancellation_without_fallback_or_usage() -> None:
    class CancellingClient:
        def __init__(self) -> None:
            self.models: list[str] = []
            self.received_cancel_check = False

        def complete(
            self,
            model: str,
            *,
            cancel_check=None,  # type: ignore[no-untyped-def]
            **kwargs: object,
        ) -> LLMResponse:
            self.models.append(model)
            self.received_cancel_check = cancel_check is not None
            raise LLMCancelledError("LLM request cancelled")

        def complete_stream(self, model: str, **kwargs: object) -> LLMResponse:
            return self.complete(model, **kwargs)

    client = CancellingClient()
    budget = BudgetGovernor(limit_usd=1.0)
    pool = LLMPool(
        clients={"mock": client},  # type: ignore[dict-item]
        routing=RoutingConfig(
            synthesis=ModelRef("mock", "strong-model"),
            adjudication=ModelRef("mock", "fallback-model"),
            screening=[ModelRef("mock", "fallback-model")],
        ),
        budget=budget,
    )
    pool.cancel_check = lambda: False

    with pytest.raises(LLMCancelledError, match="cancelled"):
        pool.complete(
            TaskType.CHAT,
            system="system",
            prompt="question",
        )

    assert client.models == ["strong-model"]
    assert client.received_cancel_check is True
    assert budget.calls == 0
    assert pool.usage == []


def test_pool_records_billed_response_before_post_request_cancellation() -> None:
    request_started = threading.Event()
    release_response = threading.Event()
    cancelled = threading.Event()

    class BlockingBilledClient:
        def complete(self, model: str, **_kwargs: object) -> LLMResponse:
            request_started.set()
            assert release_response.wait(timeout=2)
            return LLMResponse(
                text="completed but cancelled",
                model=model,
                provider="mock",
                input_tokens=12,
                output_tokens=4,
                cost_usd=0.002,
                cost_source="provider",
            )

        def complete_stream(self, model: str, **kwargs: object) -> LLMResponse:
            return self.complete(model, **kwargs)

    budget = BudgetGovernor(limit_usd=1.0)
    pool = LLMPool(
        clients={"mock": BlockingBilledClient()},  # type: ignore[dict-item]
        routing=RoutingConfig(
            synthesis=ModelRef("mock", "billed-model"),
            adjudication=None,
            screening=[],
        ),
        budget=budget,
    )
    recorded = []
    pool.on_usage = recorded.append
    pool.cancel_check = cancelled.is_set
    errors: list[BaseException] = []

    def run_completion() -> None:
        try:
            pool.complete(TaskType.CHAT, system="system", prompt="question")
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    worker = threading.Thread(target=run_completion, daemon=True)
    worker.start()
    assert request_started.wait(timeout=2)
    cancelled.set()
    release_response.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], LLMCancelledError)
    assert budget.calls == 1
    assert budget.spent_usd == pytest.approx(0.002)
    assert len(pool.usage) == 1
    assert recorded == pool.usage


def test_provider_retry_is_settled_only_once_after_final_success() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, text="temporary gateway failure")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "recovered"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    budget = BudgetGovernor(limit_usd=1.0)
    pool = LLMPool(
        clients={"deepseek": _compat_client(handler)},
        routing=RoutingConfig(
            synthesis=ModelRef("deepseek", "mock-retry-model"),
            adjudication=None,
            screening=[],
        ),
        budget=budget,
        content_scope="public",
    )
    recorded = []
    pool.on_usage = recorded.append

    response = pool.complete(TaskType.CHAT, system="system", prompt="question")

    assert response.text == "recovered"
    assert attempts["n"] == 2
    assert budget.calls == 1
    assert len(pool.usage) == 1
    assert recorded == pool.usage


def test_openai_compat_streams_visible_text_and_keeps_reasoning_private() -> None:
    requests: list[httpx.Request] = []
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        bodies.append(json.loads(request.content))
        frames = [
            {"choices": [{"delta": {"content": "<think>private"}}]},
            {"choices": [{"delta": {"content": " chain</think>Public "}}]},
            {"choices": [{"delta": {"content": "answer."}}]},
            {
                "choices": [],
                "model": "routed-model",
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.001},
            },
        ]
        body = "".join(f"data: {__import__('json').dumps(frame)}\n\n" for frame in frames)
        return httpx.Response(200, text=body)

    visible: list[str] = []
    response = _compat_client(handler, provider="openrouter").complete_stream(
        "deepseek-v4-flash",
        system="s",
        prompt="p",
        on_delta=visible.append,
    )

    assert "".join(visible) == "Public answer."
    assert response.text == "Public answer."
    assert response.model == "routed-model"
    assert response.input_tokens == 12
    assert response.output_tokens == 4
    assert response.cost_usd == pytest.approx(0.001)
    assert requests[0].headers["x-openrouter-cache"] == "false"
    provider = bodies[0]["provider"]
    assert isinstance(provider, dict)
    assert provider["data_collection"] == "deny"
    assert provider["zdr"] is True
    max_price = provider["max_price"]
    assert isinstance(max_price, dict)
    assert max_price["request"] == 0.0


def test_openrouter_sonar_stream_caps_the_reserved_flat_request_fee() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        frames = [
            {"choices": [{"delta": {"content": "Grounded answer."}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            },
        ]
        body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
        return httpx.Response(200, text=f"{body}data: [DONE]\n\n")

    visible: list[str] = []
    response = _compat_client(handler, provider="openrouter").complete_stream(
        "perplexity/sonar-reasoning-pro",
        system="s",
        prompt="p",
        on_delta=visible.append,
    )

    assert response.text == "Grounded answer."
    assert visible == ["Grounded answer."]
    provider = bodies[0]["provider"]
    assert isinstance(provider, dict)
    assert provider["max_price"] == {
        "prompt": 2.2,
        "completion": 8.8,
        "request": 0.014,
    }


def test_openai_compat_cancel_closes_a_blocked_stream_promptly() -> None:
    stream = _BlockingStream()
    cancelled = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    def trigger_cancel() -> None:
        assert stream.started.wait(timeout=1.0)
        cancelled.set()

    trigger = threading.Thread(target=trigger_cancel, daemon=True)
    trigger.start()
    started = time.perf_counter()
    with pytest.raises(LLMCancelledError, match="cancelled"):
        _compat_client(handler).complete_stream(
            "deepseek-v4-flash",
            system="s",
            prompt="p",
            on_delta=lambda _delta: None,
            cancel_check=cancelled.is_set,
        )
    elapsed = time.perf_counter() - started
    trigger.join(timeout=1.0)

    assert stream.closed.is_set()
    assert elapsed < 1.0


def test_openai_compat_cancel_after_delta_never_retries() -> None:
    attempts = {"n": 0}
    cancelled = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        frames = [
            {"choices": [{"delta": {"content": "First"}}]},
            {"choices": [{"delta": {"content": " second"}}]},
        ]
        body = "".join(f"data: {__import__('json').dumps(frame)}\n\n" for frame in frames)
        return httpx.Response(200, text=body)

    def on_delta(_delta: str) -> None:
        cancelled["value"] = True

    with pytest.raises(LLMCancelledError, match="cancelled"):
        _compat_client(handler).complete_stream(
            "deepseek-v4-flash",
            system="s",
            prompt="p",
            on_delta=on_delta,
            cancel_check=lambda: cancelled["value"],
        )

    assert attempts["n"] == 1


def test_openrouter_streams_only_visible_reasoning_details() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        frames = [
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_details": [
                                {"type": "reasoning.text", "text": "Compare the sources. "},
                                {"type": "reasoning.encrypted", "data": "private"},
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_details": [
                                {"type": "reasoning.summary", "summary": "Resolve the conflict."}
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {"content": "Grounded answer."}}]},
        ]
        body = "".join(f"data: {__import__('json').dumps(frame)}\n\n" for frame in frames)
        return httpx.Response(200, text=body)

    reasoning: list[str] = []
    response = _compat_client(handler, provider="openrouter").complete_stream(
        "deepseek/deepseek-v4-flash",
        system="s",
        prompt="p",
        on_delta=lambda _delta: None,
        on_reasoning=reasoning.append,
    )

    assert "".join(reasoning) == "source_comparison\nconflict_check\n"
    assert response.reasoning == "source_comparison\nconflict_check"
    assert "Compare the sources" not in response.reasoning
    assert "private" not in response.reasoning
    request_body = __import__("json").loads(seen[0].content)
    assert request_body["reasoning"] == {"effort": "low", "exclude": False}


def test_openai_compat_retries_empty_stream_before_showing_output() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(200, text="data: [DONE]\n\n")
        frame = {"choices": [{"delta": {"content": "Recovered answer."}}]}
        return httpx.Response(
            200,
            text=f"data: {__import__('json').dumps(frame)}\n\ndata: [DONE]\n\n",
        )

    visible: list[str] = []
    response = _compat_client(handler).complete_stream(
        "deepseek-v4-flash", system="s", prompt="p", on_delta=visible.append
    )

    assert response.text == "Recovered answer."
    assert visible == ["Recovered answer."]
    assert attempts["n"] == 2


def test_openai_compat_does_not_retry_after_visible_stream_output() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        frame = {"choices": [{"delta": {"content": "Partial answer"}}]}
        chunk = f"data: {__import__('json').dumps(frame)}\n\n".encode()
        return httpx.Response(200, stream=_InterruptedStream(request, chunk))

    visible: list[str] = []
    with pytest.raises(ProviderError, match="stream interrupted after output began"):
        _compat_client(handler).complete_stream(
            "deepseek-v4-flash", system="s", prompt="p", on_delta=visible.append
        )

    assert visible == ["Partial answer"]
    assert attempts["n"] == 1


def test_openai_compat_retries_interrupted_private_reasoning_prefix() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            frame = {"choices": [{"delta": {"content": "<think>private"}}]}
            chunk = f"data: {__import__('json').dumps(frame)}\n\n".encode()
            return httpx.Response(200, stream=_InterruptedStream(request, chunk))
        frame = {"choices": [{"delta": {"content": "Recovered answer"}}]}
        return httpx.Response(
            200,
            text=f"data: {__import__('json').dumps(frame)}\n\ndata: [DONE]\n\n",
        )

    visible: list[str] = []
    response = _compat_client(handler).complete_stream(
        "deepseek-v4-flash", system="s", prompt="p", on_delta=visible.append
    )

    assert response.text == "Recovered answer"
    assert visible == ["Recovered answer"]
    assert attempts["n"] == 2


def test_production_provider_client_has_bounded_retries(
    no_keys: pytest.MonkeyPatch,
) -> None:
    no_keys.setenv("SIX_OPENROUTER_API_KEY", "gateway")
    client = build_client(PROVIDERS["openrouter"])

    assert isinstance(client, OpenAICompatClient)
    assert client.max_retries == 2


def test_openrouter_client_uses_the_deployment_wide_base_url(
    no_keys: pytest.MonkeyPatch,
) -> None:
    no_keys.setenv("SIX_OPENROUTER_API_KEY", "gateway")
    no_keys.setenv("SIX_OPENROUTER_BASE_URL", "https://contractual-route.example/v1")
    get_settings.cache_clear()
    try:
        client = build_client(PROVIDERS["openrouter"])
        assert isinstance(client, OpenAICompatClient)
        assert str(client.http.base_url) == "https://contractual-route.example/v1/"
    finally:
        get_settings.cache_clear()


def test_direct_gemini_requires_data_processing_confirmation(
    no_keys: pytest.MonkeyPatch,
) -> None:
    assert Settings(gemini_api_key="key").gemini_enabled is False
    assert (
        Settings(
            gemini_api_key="key",
            gemini_data_processing_confirmed=True,
        ).public_gemini_live_enabled
        is False
    )
    assert (
        Settings(
            gemini_api_key="key",
            gemini_data_processing_confirmed=True,
        ).gemini_egress_api_key
        == "key"
    )
    assert (
        Settings(
            gemini_api_key="key",
            gemini_data_processing_confirmed=True,
            public_spoken_interviews_enabled=True,
        ).public_gemini_live_enabled
        is True
    )

    no_keys.setenv("SIX_GEMINI_API_KEY", "key-alone-must-not-enable-egress")
    no_keys.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "0")
    get_settings.cache_clear()
    try:
        assert PROVIDERS["gemini"].available() is False
        with pytest.raises(LLMConfigError, match="DATA_PROCESSING_CONFIRMED"):
            build_client(PROVIDERS["gemini"])
        with pytest.raises(LLMConfigError, match="DATA_PROCESSING_CONFIRMED"):
            LLMPool.from_environment(
                BudgetGovernor(limit_usd=1.0),
                routing_json='{"screening": ["gemini:gemini-3.5-flash"]}',
            )

        no_keys.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
        get_settings.cache_clear()
        assert PROVIDERS["gemini"].available() is True
        client = build_client(PROVIDERS["gemini"])
        assert isinstance(client, GeminiClient)
    finally:
        get_settings.cache_clear()


def test_available_specs_follow_env(no_keys: pytest.MonkeyPatch) -> None:
    assert available_specs() == []
    no_keys.setenv("DEEPSEEK_API_KEY", "x")
    no_keys.setenv("MISTRAL_API_KEY", "y")
    assert available_specs() == []  # legacy keys never become automatic routing


def test_openrouter_exclusively_wins_public_routing(no_keys: pytest.MonkeyPatch) -> None:
    no_keys.setenv("DEEPSEEK_API_KEY", "legacy")
    no_keys.setenv("ANTHROPIC_API_KEY", "legacy")
    no_keys.setenv("SIX_OPENROUTER_API_KEY", "gateway")
    specs = available_specs(content_scope="public")
    assert [spec.name for spec in specs] == ["openrouter"]
    assert default_strong_ref(specs) == ModelRef("openrouter", "deepseek/deepseek-v4-pro")
    assert specs[0].extra_body == {"reasoning": {"effort": "none"}}


def test_explicit_public_routing_uses_only_openrouter(no_keys: pytest.MonkeyPatch) -> None:
    no_keys.setenv("DEEPSEEK_API_KEY", "x")
    no_keys.setenv("SIX_GEMINI_API_KEY", "y")
    no_keys.setenv("ANTHROPIC_API_KEY", "z")
    no_keys.setenv("SIX_OPENROUTER_API_KEY", "gateway")
    specs = available_specs(content_scope="public")
    refs = default_screening_refs(specs)
    assert refs == [
        ModelRef("openrouter", "deepseek/deepseek-v4-flash"),
        ModelRef("openrouter", "nvidia/nemotron-3-super-120b-a12b"),
    ]
    strong = default_strong_ref(specs)
    assert strong == ModelRef("openrouter", "deepseek/deepseek-v4-pro")
    routing = RoutingConfig.defaults(content_scope="public")
    assert routing.synthesis == strong
    assert routing.adjudication == ModelRef("openrouter", "deepseek/deepseek-v4-flash")
    assert routing.screening == refs


def test_routing_config_from_json() -> None:
    routing = RoutingConfig.from_json(
        '{"synthesis": "anthropic:claude-opus-4-8",'
        ' "screening": ["deepseek:deepseek-v4-flash", "kimi:kimi-k2.6"]}'
    )
    assert routing.synthesis == ModelRef("anthropic", "claude-opus-4-8")
    assert routing.adjudication is None
    assert [str(r) for r in routing.screening] == [
        "deepseek:deepseek-v4-flash",
        "kimi:kimi-k2.6",
    ]


def test_pool_rejects_routed_provider_without_key(no_keys: pytest.MonkeyPatch) -> None:
    with pytest.raises(LLMConfigError, match="DEEPSEEK_API_KEY"):
        LLMPool.from_environment(
            BudgetGovernor(limit_usd=1.0),
            routing_json='{"screening": ["deepseek:deepseek-v4-flash"]}',
            content_scope="public",
        )


def test_think_blocks_are_stripped_from_content() -> None:
    # MiniMax M-series prepends its reasoning inside `content`
    from sixsentences_server.llm.providers import _strip_think

    raw = '<think>\nweighing the criteria...\n</think>\n\n{"verdict": "include"}'
    assert _strip_think(raw) == '{"verdict": "include"}'
    assert _strip_think("plain answer") == "plain answer"
    assert (
        _strip_think("private plan without opening tag</think>Visible answer") == "Visible answer"
    )
    assert _strip_think("Visible answer<think>unfinished private plan") == "Visible answer"


def test_stream_hides_orphan_reasoning_prefix_before_visible_answer() -> None:
    frames = [
        {"choices": [{"delta": {"content": "private plan without opening tag"}}]},
        {"choices": [{"delta": {"content": "</think>Visible answer"}}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = "\n\n".join(f"data: {json.dumps(frame)}" for frame in frames)
        return httpx.Response(200, text=f"{body}\n\ndata: [DONE]\n\n")

    visible: list[str] = []
    response = _compat_client(handler).complete_stream(
        "deepseek-v4-flash", system="s", prompt="p", on_delta=visible.append
    )

    assert response.text == "Visible answer"
    assert visible == ["Visible answer"]
