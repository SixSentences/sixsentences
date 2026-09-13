"""The user-facing model menu and its credit multipliers."""

from collections.abc import Callable

import pytest

from sixsentences_server.llm.base import (
    BudgetGovernor,
    LLMClient,
    LLMConfigError,
    LLMResponse,
    ModelRef,
    TaskType,
    openrouter_price_cap_of,
)
from sixsentences_server.llm.models import (
    AUTO_ID,
    CHAT_MODELS,
    PUBLIC_RESEARCH_MODELS,
    ChatModel,
    auto_reference_price,
    available_chat_models,
    blended_price,
    pinned_ref,
    resolve_chat_model,
)
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.privacy import PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL
from sixsentences_server.llm.providers import ProviderError

# Auditable assumptions from OpenRouter's official /api/v1/endpoints/zdr
# snapshot on 2026-08-31. Each tuple is
# (provider/tag, context length, maximum input price, maximum output price,
# native tool support). Prices include a selected endpoint's highest published
# context tier and are expressed in USD per million tokens.
_VERIFIED_ZDR_ENDPOINTS: dict[str, tuple[str, int, float, float, bool]] = {
    "deepseek/deepseek-v4-flash": (
        "provider=DeepInfra, tag=deepinfra/fp8",
        1_048_576,
        0.09,
        0.18,
        True,
    ),
    "deepseek/deepseek-v4-pro": (
        "provider=Novita, tag=novita/fp8",
        1_048_576,
        1.60,
        3.20,
        True,
    ),
    "openai/gpt-5.6-terra-pro": (
        "provider=Azure, tag=azure",
        1_050_000,
        4.0,
        18.0,
        True,
    ),
    "z-ai/glm-5.2": (
        "provider=SiliconFlow, tag=siliconflow/fp8",
        1_048_576,
        1.19,
        3.74,
        True,
    ),
    "mistralai/mistral-small-2603": (
        "provider=Mistral, tag=mistral/zdr",
        262_144,
        0.15,
        0.60,
        True,
    ),
    "meta-llama/llama-4-maverick": (
        "provider=DeepInfra, tag=deepinfra/base",
        1_048_576,
        0.20,
        0.80,
        False,
    ),
    "openai/gpt-5.6-luna-pro": (
        "provider=Azure, tag=azure",
        1_050_000,
        0.40,
        1.80,
        True,
    ),
    "nvidia/nemotron-3-super-120b-a12b": (
        "provider=DeepInfra, tag=deepinfra/bf16",
        262_144,
        0.085,
        0.40,
        True,
    ),
    "minimax/minimax-m3": (
        "provider=Parasail, tag=parasail/fp8",
        1_048_576,
        0.30,
        1.20,
        True,
    ),
    "google/gemini-3.1-flash-lite": (
        "provider=Google, tag=google-vertex/global",
        1_048_576,
        0.25,
        1.50,
        True,
    ),
    "moonshotai/kimi-k2.6": (
        "provider=DigitalOcean, tag=digitalocean",
        262_144,
        0.95,
        4.0,
        True,
    ),
    "anthropic/claude-sonnet-5": (
        "provider=Amazon Bedrock, tag=amazon-bedrock/global",
        1_000_000,
        2.0,
        10.0,
        True,
    ),
}


def test_private_multipliers_match_standard_price_without_premium_rounding() -> None:
    """Capacity estimates are proportional, not a flat premium surcharge.

    These are reservations rather than vendor bills: long-context tiers,
    caching and thinking settle through the independent token-cost governor.
    """
    baseline = auto_reference_price()
    for entry in CHAT_MODELS:
        if entry.id == AUTO_ID:
            continue
        ratio = blended_price(entry.model) / baseline
        assert entry.multiplier == pytest.approx(ratio)
    assert resolve_chat_model(PRIVATE_PREMIUM_MODEL, allow_locked=True).multiplier == pytest.approx(
        4 / 3,
    )


def test_menu_ids_are_unique_and_direct_gemini_is_the_1x_default() -> None:
    ids = [m.id for m in CHAT_MODELS]
    assert len(set(ids)) == len(ids)
    auto = resolve_chat_model(None)
    assert auto.id == AUTO_ID and auto.multiplier == 1
    assert pinned_ref(auto) == ModelRef("gemini", PRIVATE_DEFAULT_MODEL)


def test_locked_and_unknown_choices_fall_back_to_default() -> None:
    assert resolve_chat_model("claude-opus").id == AUTO_ID
    assert resolve_chat_model("claude-sonnet-5").id == AUTO_ID
    assert resolve_chat_model("does-not-exist").id == AUTO_ID
    assert len(available_chat_models()) == 2
    assert sum(not model.locked for model in available_chat_models()) == 1
    assert resolve_chat_model(PRIVATE_PREMIUM_MODEL).id == AUTO_ID
    assert resolve_chat_model(PRIVATE_PREMIUM_MODEL, allow_locked=True).id == PRIVATE_PREMIUM_MODEL
    assert resolve_chat_model("gpt-5.6-terra-pro", allow_locked=True).id == AUTO_ID
    assert resolve_chat_model("not-a-provider-model", allow_locked=True).id == AUTO_ID
    for unavailable in (
        "mistral-large-3",
        "mimo-v2.5-pro",
        "sonar-reasoning-pro",
        "gemini-3.7-flash",
    ):
        assert resolve_chat_model(unavailable, allow_locked=True).id == AUTO_ID


def _public_model(model_id: str) -> ChatModel:
    return next(entry for entry in PUBLIC_RESEARCH_MODELS if entry.id == model_id)


def test_retained_public_catalog_has_accurate_capabilities_and_is_not_a_private_menu() -> None:
    assert all(model.provider == "openrouter" for model in PUBLIC_RESEARCH_MODELS)
    assert {model.id for model in PUBLIC_RESEARCH_MODELS if not model.supports_tools} == {
        "llama-4-maverick",
    }
    assert PUBLIC_RESEARCH_MODELS[0].output_price < 1.0
    assert {model.impact for model in PUBLIC_RESEARCH_MODELS} == {
        "low",
        "medium",
        "high",
        "very_high",
    }
    assert {model.model for model in PUBLIC_RESEARCH_MODELS} == {
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "openai/gpt-5.6-terra-pro",
        "z-ai/glm-5.2",
        "mistralai/mistral-small-2603",
        "meta-llama/llama-4-maverick",
        "openai/gpt-5.6-luna-pro",
        "nvidia/nemotron-3-super-120b-a12b",
        "minimax/minimax-m3",
        "google/gemini-3.1-flash-lite",
        "moonshotai/kimi-k2.6",
        "anthropic/claude-sonnet-5",
    }
    assert all(model.cost_tier == model.impact for model in PUBLIC_RESEARCH_MODELS)
    assert all(model.id != "sonar-deep-research" for model in PUBLIC_RESEARCH_MODELS)
    assert resolve_chat_model("sonar-deep-research", allow_locked=True).id == AUTO_ID
    assert resolve_chat_model("sixsentences-router").reasoning_visible is False
    assert _public_model("sixsentences-router").reasoning_visible is True
    deepseek = _public_model("deepseek-v4-flash")
    assert deepseek.reasoning_visible is True
    assert (deepseek.input_price, deepseek.output_price, deepseek.multiplier) == (0.09, 0.18, 1)
    mistral = _public_model("mistral-small-4")
    assert (mistral.input_price, mistral.output_price, mistral.multiplier) == (0.15, 0.60, 3)
    maverick = _public_model("llama-4-maverick")
    # Verified DeepInfra ZDR route: the public context promise must fit and its
    # tariff must remain below the request's fail-closed provider price cap.
    verified_zdr_endpoint = {
        "context_length": 1_048_576,
        "input_price": 0.20,
        "output_price": 0.80,
    }
    assert maverick.context_length <= verified_zdr_endpoint["context_length"]
    assert (maverick.input_price, maverick.output_price) == (0.20, 0.80)
    input_cap, output_cap = openrouter_price_cap_of(maverick.model)
    assert verified_zdr_endpoint["input_price"] <= input_cap
    assert verified_zdr_endpoint["output_price"] <= output_cap


def test_every_retained_public_model_has_a_verified_eligible_zdr_endpoint() -> None:
    """Public-only catalog promises must fit a known fail-closed route."""

    assert set(_VERIFIED_ZDR_ENDPOINTS) == {entry.model for entry in PUBLIC_RESEARCH_MODELS}

    for entry in PUBLIC_RESEARCH_MODELS:
        endpoint, context_length, input_price, output_price, supports_tools = (
            _VERIFIED_ZDR_ENDPOINTS[entry.model]
        )
        input_cap, output_cap = openrouter_price_cap_of(entry.model)
        assert entry.context_length <= context_length, (
            f"{entry.id} advertises {entry.context_length} tokens, but its verified "
            f"ZDR endpoint {endpoint} only supports {context_length}"
        )
        assert input_price <= input_cap, (
            f"{entry.id} input cap {input_cap} excludes verified endpoint {endpoint} "
            f"at {input_price}"
        )
        assert output_price <= output_cap, (
            f"{entry.id} output cap {output_cap} excludes verified endpoint {endpoint} "
            f"at {output_price}"
        )
        assert entry.supports_tools is supports_tools


def test_pinning_overrides_only_bounded_synthesis_work() -> None:
    from sixsentences_server.llm.mock import MockClient

    screening = [ModelRef("mock", "mock-a"), ModelRef("mock", "mock-b")]
    pool = LLMPool(
        clients={"mock": MockClient()},
        routing=RoutingConfig(
            synthesis=ModelRef("mock", "mock-strong"),
            adjudication=ModelRef("mock", "mock-strong"),
            screening=screening,
        ),
        budget=BudgetGovernor(limit_usd=1.0),
    )
    pinned = pool.pinned(ModelRef("mock", "mock-user-choice"))
    assert pinned.routing.synthesis == ModelRef("mock", "mock-user-choice")
    assert pinned.routing.adjudication == ModelRef("mock", "mock-strong")
    assert pinned.routing.screening == screening
    assert pinned.usage is pool.usage  # one shared accounting ledger


def test_pinning_to_an_unconfigured_provider_raises() -> None:
    from sixsentences_server.llm.mock import MockClient

    pool = LLMPool(
        clients={"mock": MockClient()},
        routing=RoutingConfig(synthesis=ModelRef("mock", "m")),
        budget=BudgetGovernor(limit_usd=1.0),
    )
    with pytest.raises(LLMConfigError):
        pool.pinned(ModelRef("anthropic", "claude-opus-4-8"))


def test_interrupted_visible_stream_resets_before_model_fallback() -> None:
    class InterruptThenRecoverClient(LLMClient):
        def complete(
            self,
            model: str,
            *,
            system: str,
            prompt: str,
            max_tokens: int = 2048,
        ) -> LLMResponse:
            return LLMResponse(
                text="Recovered answer",
                provider="mock",
                model=model,
                input_tokens=2,
                output_tokens=2,
            )

        def complete_stream(
            self,
            model: str,
            *,
            system: str,
            prompt: str,
            max_tokens: int = 2048,
            on_delta: Callable[[str], None],
            on_reasoning: Callable[[str], None] | None = None,
        ) -> LLMResponse:
            if model == "selected":
                on_delta("Interrupted draft")
                raise ProviderError("mock: stream interrupted after output began")
            response = self.complete(
                model,
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
            )
            on_delta(response.text)
            return response

    pool = LLMPool(
        clients={"mock": InterruptThenRecoverClient()},
        routing=RoutingConfig(
            synthesis=ModelRef("mock", "selected"),
            adjudication=ModelRef("mock", "fallback"),
            screening=[ModelRef("mock", "fallback")],
        ),
        budget=BudgetGovernor(limit_usd=1.0),
    )
    visible: list[str] = []
    resets: list[bool] = []
    response = pool.complete(
        TaskType.CHAT,
        system="system",
        prompt="prompt",
        on_delta=visible.append,
        on_stream_reset=lambda: resets.append(True),
    )

    assert response.model == "fallback"
    assert visible == ["Interrupted draft", "Recovered answer"]
    assert resets == [True]
