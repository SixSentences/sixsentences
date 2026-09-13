"""Provider-agnostic LLM layer.

Principles enforced here rather than per-callsite:
- every call is attributed to a TaskType (accounting + routing)
- a hard USD budget per run (BudgetGovernor), backed by durable action accounting
- clients are replaceable, but private hosted routing is restricted to paid
  Gemini; Flash/Pro agreement does not establish independent model errors
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import Protocol


class TaskType(StrEnum):
    PROTOCOL_SYNTHESIS = "protocol_synthesis"
    QUERY_EXPANSION = "query_expansion"
    SCREENING = "screening"
    FULL_TEXT_SCREENING = "full_text_screening"
    ADJUDICATION = "adjudication"
    CLAIM_VERIFICATION = "claim_verification"
    REPOSITORY_ANALYSIS = "repository_analysis"
    WEB_SEARCH = "web_search"
    CHAT = "chat"


@dataclass(frozen=True)
class ModelRef:
    """A concrete model at a concrete provider, e.g. deepseek:deepseek-v4-flash."""

    provider: str
    model: str

    @classmethod
    def parse(cls, spec: str) -> "ModelRef":
        provider, _, model = spec.partition(":")
        if not provider or not model:
            raise ValueError(f"model ref must be 'provider:model', got {spec!r}")
        return cls(provider=provider, model=model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    # A subset of input_tokens served from a provider prompt cache. Providers
    # expose this under several usage-field names; direct adapters retain it so
    # catalog accounting can apply the lower cache-read tariff.
    cached_input_tokens: int = 0
    # OpenRouter returns the amount actually billed. Direct adapters leave it
    # unset and the local catalog remains the conservative fallback.
    cost_usd: float | None = None
    cost_source: str = "catalog"
    # Provider-returned, user-visible reasoning. Encrypted/signature payloads
    # and hidden scratchpads are never exposed here.
    reasoning: str | None = None


@dataclass(frozen=True)
class LLMUsage:
    """One billable LLM call — the atomic unit of cost accounting and the
    'tool usage' surface a UI renders per run (docs/API_DESIGN_NOTES.md)."""

    task: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_source: str
    duration_ms: int
    cached_input_tokens: int = 0


class BudgetExceededError(RuntimeError):
    pass


class LLMConfigError(RuntimeError):
    pass


class LLMCancelledError(RuntimeError):
    """Raised when a caller explicitly cancels an in-flight LLM request.

    Cancellation is intentionally distinct from provider failures. Routing
    pools must propagate it immediately instead of trying another model and
    accidentally continuing work the user already stopped.
    """


# USD per million tokens (input, output). Conservative routing prices verified
# on 2026-09-03 — used for run accounting and the local budget ceiling.
# Unknown models fall back to a conservative estimate.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # Legacy public-only OpenRouter catalog, not the private model picker. These are the
    # selected route tariffs or conservative privacy-compatible ceilings in
    # USD per million tokens (verified 2026-09-03). Some economical entries
    # are explicit Flex endpoints rather than the standard provider tariff.
    # DeepSeek Pro uses a no-collection-compatible provider ceiling:
    # the cheapest headline endpoint may collect prompts and is removed by the
    # runtime policy, so pricing the model at the headline rate made every
    # privacy-compatible provider fail the max-price filter.
    "deepseek/deepseek-v4-flash": (0.09, 0.18),
    "qwen/qwen3.5-flash-02-23": (0.065, 0.26),
    "z-ai/glm-5.2": (1.19, 3.74),
    "deepseek/deepseek-v4-pro": (1.60, 3.20),
    "meta/muse-spark-1.2": (1.25, 4.25),
    "poolside/laguna-s-2.1": (0.09, 0.18),
    "openai/gpt-5.6-terra-pro": (2.0, 12.0),
    "mistralai/mistral-small-2603": (0.15, 0.60),
    "mistralai/mistral-large-2512": (0.50, 1.50),
    "qwen/qwen3.8-max": (2.0, 6.0),
    # Sonar also bills provider-side search and reasoning activity. OpenRouter
    # returns that complete billed amount and the action ledger stores it; the
    # token ceiling below is only the conservative pre-call routing guard.
    "perplexity/sonar-deep-research": (2.0, 8.0),
    "perplexity/sonar-reasoning-pro": (2.0, 8.0),
    "perplexity/sonar": (1.0, 1.0),
    # The cheaper $0.20/$0.696 ZDR route stops at 128k context. Use the
    # million-token ZDR endpoint's $0.20/$0.80 tariff so the 10% cap admits the
    # context window advertised by the product catalog.
    "meta-llama/llama-4-maverick": (0.20, 0.80),
    "openai/gpt-5.6-luna-pro": (0.20, 1.20),
    "qwen/qwen3.7-flash": (0.03, 0.13),
    "nvidia/nemotron-3-super-120b-a12b": (0.085, 0.40),
    "minimax/minimax-m3": (0.30, 1.20),
    "google/gemini-3.1-flash-lite": (0.25, 1.50),
    "google/gemini-3.5-flash": (1.50, 9.0),
    "google/gemini-3.7-flash": (0.375, 1.875),
    # Specialized multimedia routes use these exact OpenRouter model slugs.
    # Image-output spend is additionally bounded by the per-image request cap
    # because it is published as a separate SKU rather than completion text.
    "google/gemini-3.1-pro-preview": (2.0, 12.0),
    "google/gemini-3-pro-image": (2.0, 12.0),
    "google/gemini-3.1-flash-image": (0.50, 3.0),
    "moonshotai/kimi-k2.6": (0.95, 4.0),
    "anthropic/claude-sonnet-5": (2.0, 10.0),
    # anthropic
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # openai
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    # Direct Gemini Standard API tariffs verified 2026-09-08 at
    # https://ai.google.dev/gemini-api/docs/pricing. Output includes thinking.
    # These pinned IDs do not inherit OpenRouter Flex or endpoint prices.
    "gemini-3.5-flash": (1.50, 9.0),
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    # Image tokens have a separate tariff in llm/gemini_pricing.py; these are
    # only the text/thinking rates and must not price image output directly.
    "gemini-3-pro-image": (2.0, 12.0),
    "gemini-3.1-flash-image": (0.50, 3.0),
    # google (legacy aliases retained for historical ledgers)
    # The aliases currently resolve to Gemini 3.1 Pro Preview and Gemini 3.5
    # Flash respectively. Pro has a higher tariff above 200k input tokens.
    "gemini-pro-latest": (2.0, 12.0),
    "gemini-flash-latest": (1.50, 9.0),
    # DeepSeek V4 direct API peak tariff. Off-peak calls cost half, but budgets
    # and pre-call guards must remain safe when a run crosses a peak window.
    "deepseek-v4-flash": (0.44, 1.32),
    "deepseek-v4-pro": (1.32, 3.96),
    # alibaba qwen (dashscope)
    "qwen-max": (1.60, 6.40),
    "qwen-plus": (0.40, 1.20),
    # moonshot kimi
    "kimi-k2.6": (0.95, 4.0),
    # minimax — M3 flagship 2026-06; the M2.7 highspeed variant charges a
    # 2x speed premium over plain M2.7
    "MiniMax-M3": (0.30, 1.20),
    "MiniMax-M2.7": (0.30, 1.20),
    "MiniMax-M2.7-highspeed": (0.60, 2.40),
    "MiniMax-M2": (0.30, 1.20),
    # mistral
    "mistral-large-latest": (0.50, 1.50),
    "mistral-small-latest": (0.15, 0.60),
    # tests
    "mock": (0.0, 0.0),
}
_FALLBACK_PRICE = (5.0, 25.0)


@dataclass(frozen=True)
class PriceOverride:
    """A tariff that starts at ``min_input_tokens`` for one request."""

    min_input_tokens: int
    input_price: float
    output_price: float
    cache_read_price: float | None = None


# Prompt-cache reads are included in provider usage as input tokens. Applying
# these rates to the cached subset avoids systematically over-accounting direct
# calls while the pre-call guard still assumes an uncached request.
# OpenRouter entries are catalog accounting references, not provider pins.
# Completed OpenRouter calls settle from ``usage.cost`` and a missing-cost
# response falls back to the uncached route cap, so dynamic routing never relies
# on these cache annotations as a provider-cost guarantee.
CACHE_READ_PRICES_PER_MTOK: dict[str, float] = {
    # DeepInfra row checked in OpenRouter's public endpoint table on 2026-09-04.
    "deepseek/deepseek-v4-flash": 0.018,
    "deepseek/deepseek-v4-pro": 0.33,
    "z-ai/glm-5.2": 0.221,
    "meta/muse-spark-1.2": 0.15,
    "poolside/laguna-s-2.1": 0.009,
    "openai/gpt-5.6-terra-pro": 0.20,
    "mistralai/mistral-small-2603": 0.015,
    "mistralai/mistral-large-2512": 0.05,
    "qwen/qwen3.8-max": 0.25,
    "openai/gpt-5.6-luna-pro": 0.02,
    "qwen/qwen3.7-flash": 0.006,
    "minimax/minimax-m3": 0.06,
    "google/gemini-3.1-flash-lite": 0.025,
    "google/gemini-3.5-flash": 0.15,
    "google/gemini-3.7-flash": 0.0375,
    "google/gemini-3.1-pro-preview": 0.20,
    "google/gemini-3-pro-image": 0.20,
    "moonshotai/kimi-k2.6": 0.16,
    "anthropic/claude-sonnet-5": 0.20,
    "claude-opus-4-8": 0.50,
    "claude-sonnet-5": 0.20,
    "claude-haiku-4-5": 0.10,
    "gpt-5": 0.125,
    "gpt-5-mini": 0.025,
    "gemini-pro-latest": 0.20,
    "gemini-flash-latest": 0.15,
    "gemini-3.5-flash": 0.15,
    "gemini-3.1-pro-preview": 0.20,
    "gemini-2.5-flash-lite": 0.01,
    "gemini-3.1-flash-lite": 0.025,
    "gemini-3.5-flash-lite": 0.03,
    "deepseek-v4-flash": 0.014,
    "deepseek-v4-pro": 0.044,
    "qwen-max": 0.16,
    # DashScope implicit cache reads cost 20% of uncached input. The client
    # does not request the cheaper explicit-cache mode.
    "qwen-plus": 0.08,
    "kimi-k2.6": 0.16,
    "MiniMax-M3": 0.06,
    "MiniMax-M2.7": 0.06,
    "MiniMax-M2.7-highspeed": 0.06,
    "MiniMax-M2": 0.03,
    "mistral-large-latest": 0.05,
    "mistral-small-latest": 0.015,
}


# Providers publish a higher per-token rate once a single request crosses a
# context threshold. Entries are ordered so the last matching tier wins.
PRICE_OVERRIDES_PER_MTOK: dict[str, tuple[PriceOverride, ...]] = {
    # OpenRouter's live endpoint metadata applies this tier from 272,000
    # prompt tokens (inclusive), not only after that boundary.
    "openai/gpt-5.6-terra-pro": (PriceOverride(272_000, 4.0, 18.0, 0.40),),
    "openai/gpt-5.6-luna-pro": (PriceOverride(272_000, 0.40, 1.80, 0.04),),
    "qwen/qwen3.7-flash": (
        PriceOverride(32_000, 0.10, 0.40, 0.02),
        PriceOverride(256_000, 0.20, 0.80, 0.04),
    ),
    "gemini-pro-latest": (PriceOverride(200_001, 4.0, 18.0, 0.40),),
    "gemini-3.1-pro-preview": (PriceOverride(200_001, 4.0, 18.0, 0.40),),
    # OpenRouter advertises the higher tier from 200k prompt tokens for this
    # exact API slug. Specialized ten-minute transcription chunks remain well
    # below it, but the routing ceiling must still cover the published tier.
    "google/gemini-3.1-pro-preview": (PriceOverride(200_000, 4.0, 18.0, 0.40),),
    "qwen-plus": (PriceOverride(256_001, 1.20, 3.60, 0.24),),
    "MiniMax-M3": (PriceOverride(512_001, 0.60, 2.40, 0.12),),
}


def price_of(model: str, input_tokens: int = 0) -> tuple[float, float]:
    if model.startswith("mock"):  # test models are always free
        return (0.0, 0.0)
    price = PRICES_PER_MTOK.get(model, _FALLBACK_PRICE)
    for override in PRICE_OVERRIDES_PER_MTOK.get(model, ()):
        if input_tokens >= override.min_input_tokens:
            price = (override.input_price, override.output_price)
    return price


def cache_read_price_of(model: str, input_tokens: int = 0) -> float:
    """Return the cache-hit tariff, conservatively falling back to input."""
    cache_price = CACHE_READ_PRICES_PER_MTOK.get(model)
    for override in PRICE_OVERRIDES_PER_MTOK.get(model, ()):
        if input_tokens >= override.min_input_tokens and override.cache_read_price is not None:
            cache_price = override.cache_read_price
    return price_of(model, input_tokens)[0] if cache_price is None else cache_price


def price_ceiling_of(model: str) -> tuple[float, float]:
    """Return the maximum published tier for provider routing price caps."""
    input_price, output_price = price_of(model)
    for override in PRICE_OVERRIDES_PER_MTOK.get(model, ()):
        input_price = max(input_price, override.input_price)
        output_price = max(output_price, override.output_price)
    return input_price, output_price


OPENROUTER_PRICE_CAP_MULTIPLIER = 1.10


@dataclass(frozen=True)
class OpenRouterRequestFeePolicy:
    """Pre-call reserve and optional provider-side per-request price cap."""

    reserve_usd: float
    max_price_usd: float | None

    def __post_init__(self) -> None:
        if self.reserve_usd < 0:
            raise ValueError("OpenRouter request-fee reserve must be non-negative")
        if self.max_price_usd is not None and not (0 <= self.max_price_usd <= self.reserve_usd):
            raise ValueError(
                "OpenRouter request-fee cap must be non-negative and no greater "
                "than its pre-call reserve"
            )


# Sonar has billable work that is not represented by visible prompt and output
# token counts. This is the single source of truth shared by pre-call budget
# projection and OpenRouter's provider.max_price.request filter. Reasoning Pro's
# USD 0.014 value is the highest published search-context request fee, so the
# full admitted flat fee is reserved before egress. Deep Research has no fixed
# contractual maximum; its published high-effort example remains a conservative
# reserve only and deliberately has no provider request-price cap. It is not in
# the public model catalog. Completed calls are reconciled against OpenRouter's
# provider-reported exact usage cost.
OPENROUTER_REQUEST_FEE_POLICIES_USD: dict[str, OpenRouterRequestFeePolicy] = {
    # Sonar's low-context live search is currently USD 0.005/request. Reserve
    # and route-cap six tenths of a cent so a small tariff drift fails before
    # becoming material while keeping the published endpoint eligible.
    "perplexity/sonar": OpenRouterRequestFeePolicy(
        reserve_usd=0.006,
        max_price_usd=0.006,
    ),
    "perplexity/sonar-reasoning-pro": OpenRouterRequestFeePolicy(
        reserve_usd=0.014,
        max_price_usd=0.014,
    ),
    "perplexity/sonar-deep-research": OpenRouterRequestFeePolicy(
        reserve_usd=1.32,
        max_price_usd=None,
    ),
}


def openrouter_request_fee_policy_of(model: str) -> OpenRouterRequestFeePolicy | None:
    """Return the explicit fixed-fee policy for an OpenRouter model, if any."""
    return OPENROUTER_REQUEST_FEE_POLICIES_USD.get(model)


def openrouter_request_price_cap_of(model: str) -> float | None:
    """Return the provider-side per-request price cap, if contractually bounded."""
    policy = openrouter_request_fee_policy_of(model)
    return policy.max_price_usd if policy is not None else None


def openrouter_price_cap_of(model: str) -> tuple[float, float]:
    """Return the fail-closed OpenRouter routing cap in USD per million tokens."""
    input_price, output_price = price_ceiling_of(model)
    return (
        round(input_price * OPENROUTER_PRICE_CAP_MULTIPLIER, 6),
        round(output_price * OPENROUTER_PRICE_CAP_MULTIPLIER, 6),
    )


def projected_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    provider: str,
) -> float:
    """Conservatively project a call before the provider accepts it.

    OpenRouter may select any endpoint up to the request's routing cap, so a
    projection based only on the catalog's active tier can understate the
    accepted cost. Non-token Sonar fees are reserved separately. Actual spend
    continues to use the exact provider-reported amount after completion.
    """
    if provider != "openrouter":
        return cost_usd(model, input_tokens, output_tokens)

    input_price, output_price = openrouter_price_cap_of(model)
    token_cost = (
        max(0, input_tokens) * input_price + max(0, output_tokens) * output_price
    ) / 1_000_000
    request_fee_policy = openrouter_request_fee_policy_of(model)
    request_fee_reserve = request_fee_policy.reserve_usd if request_fee_policy is not None else 0.0
    return token_cost + request_fee_reserve


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    price_in, price_out = price_of(model, input_tokens)
    cached_tokens = min(max(0, cached_input_tokens), max(0, input_tokens))
    uncached_tokens = max(0, input_tokens) - cached_tokens
    cache_price = cache_read_price_of(model, input_tokens)
    return (
        uncached_tokens * price_in + cached_tokens * cache_price + max(0, output_tokens) * price_out
    ) / 1_000_000


def settled_cost_usd(response: LLMResponse) -> float:
    """Return provider-reported spend or the conservative catalog fallback.

    OpenRouter can select any endpoint accepted by the request's routing cap.
    If it omits ``usage.cost``, settling at the base catalog tariff could
    therefore undercount a successfully routed call. Direct-provider fallbacks
    retain cache-aware catalog accounting because they do not use that router.
    """
    if response.cost_usd is not None:
        return response.cost_usd
    if response.provider == "openrouter":
        return projected_cost_usd(
            response.model,
            response.input_tokens,
            response.output_tokens,
            provider=response.provider,
        )
    return cost_usd(
        response.model,
        response.input_tokens,
        response.output_tokens,
        cached_input_tokens=response.cached_input_tokens,
    )


def settled_cost_source(response: LLMResponse) -> str:
    """Label exact provider spend separately from conservative fallbacks."""
    if response.cost_usd is None and response.provider == "openrouter":
        return "catalog_cap"
    return response.cost_source


@dataclass
class BudgetGovernor:
    """Tracks spend across a run and refuses calls past the hard limit.

    Exhaustive runs are the product default and may take hours; the budget is
    the safety net, not the pacing mechanism. When it trips, the pipeline
    pauses honestly (audit event) instead of silently truncating.
    """

    limit_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    by_task: dict[str, float] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _reservations: dict[object, float] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def reserved_usd(self) -> float:
        """Return the projected spend held by provider calls still in flight."""
        with self._lock:
            return sum(self._reservations.values())

    def check(self, projected_cost_usd: float = 0.0) -> None:
        with self._lock:
            if (
                self.spent_usd >= self.limit_usd
                or self.spent_usd + self.reserved_usd + max(0.0, projected_cost_usd)
                > self.limit_usd
            ):
                raise BudgetExceededError(
                    f"LLM budget exhausted: spent ${self.spent_usd:.4f} of ${self.limit_usd:.2f}"
                )

    @contextmanager
    def reserve(self, projected_cost_usd: float) -> Iterator[object]:
        """Atomically hold a call's ceiling without locking its provider request.

        Pass the yielded token to ``record`` to replace the hold with observed
        spend atomically. Unsettled holds are released on every context exit,
        including provider rejection, cancellation and unexpected exceptions.
        """
        token = object()
        with self._lock:
            self.check(projected_cost_usd)
            self._reservations[token] = max(0.0, projected_cost_usd)
        try:
            yield token
        finally:
            with self._lock:
                self._reservations.pop(token, None)

    def record(
        self,
        task: TaskType,
        response: LLMResponse,
        *,
        reservation: object | None = None,
    ) -> None:
        """Record actual spend, even if a provider exceeded the reserved ceiling."""
        cost = settled_cost_usd(response)
        with self._lock:
            if reservation is not None:
                if reservation not in self._reservations:
                    raise ValueError("Budget reservation is not active")
                self._reservations.pop(reservation)
            self.spent_usd += cost
            self.calls += 1
            self.by_task[task.value] = self.by_task.get(task.value, 0.0) + cost


class LLMClient(Protocol):
    """Low-level client bound to one provider; called with an explicit model."""

    def complete(
        self,
        model: str,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        json_response: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> LLMResponse: ...

    def complete_stream(
        self,
        model: str,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        on_delta: Callable[[str], None],
        on_reasoning: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """Return the final response while forwarding visible text deltas."""
        ...
