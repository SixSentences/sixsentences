"""Provider registry with separate private Gemini and public-only routing.

Private workspace tasks exclusively use the native, paid Gemini adapter.
OpenRouter remains available only to explicitly public scoped callers. Legacy
direct adapters are retained for controlled public migrations and offline tests,
not as private fallbacks. Gemini requires paid-service/DPA confirmation as well
as its API key.
"""

import json
import logging
import os
import random
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import httpx

from sixsentences_server.config import DEFAULT_OPENROUTER_BASE_URL, get_settings
from sixsentences_server.llm.base import (
    LLMCancelledError,
    LLMClient,
    LLMConfigError,
    LLMResponse,
    ModelRef,
    openrouter_price_cap_of,
    openrouter_request_price_cap_of,
)
from sixsentences_server.llm.privacy import (
    PRIVATE_DEFAULT_MODEL,
    PRIVATE_PREMIUM_MODEL,
    PRIVATE_SCREENING_MODELS,
    ContentScope,
)

_LOG = logging.getLogger(__name__)


def _openrouter_max_price(model: str) -> dict[str, float]:
    """Build the shared token and optional fixed-request routing ceiling."""
    input_price, output_price = openrouter_price_cap_of(model)
    max_price = {
        "prompt": input_price,
        "completion": output_price,
        # Most chat models have no fixed provider fee. Pinning that assumption
        # to zero prevents a newly introduced per-request surcharge from
        # bypassing token-based pre-call budgeting.
        "request": 0.0,
    }
    request_price = openrouter_request_price_cap_of(model)
    if request_price is not None:
        max_price["request"] = request_price
    return max_price


class ProviderSpec:
    def __init__(
        self,
        name: str,
        *,
        kind: str,  # "openai-compat" | "anthropic"
        base_url: str,
        key_env: str,
        cheap: str,  # default screening model
        strong: str,  # default adjudication/synthesis model
        extra_body: dict[str, Any] | None = None,  # provider-specific request fields
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.base_url = base_url
        self.key_env = key_env
        self.cheap = cheap
        self.strong = strong
        self.extra_body = extra_body or {}
        self.extra_headers = extra_headers or {}

    def available(self) -> bool:
        if self.name == "gemini":
            return get_settings().gemini_enabled
        return bool(os.environ.get(self.key_env))


PROVIDERS: dict[str, ProviderSpec] = {
    "openrouter": ProviderSpec(
        "openrouter",
        kind="openai-compat",
        base_url=DEFAULT_OPENROUTER_BASE_URL,
        key_env="SIX_OPENROUTER_API_KEY",
        # Bulk decisions, disagreement adjudication and full-text eligibility
        # stay on the economical ZDR-capable DeepSeek route. ``strong``
        # remains available for bounded synthesis work, but never fans out
        # once per candidate record.
        cheap="deepseek/deepseek-v4-flash",
        strong="deepseek/deepseek-v4-pro",
        # The product's router and screening calls need short deterministic
        # answers, not paid hidden deliberation that can consume the whole
        # completion budget.
        extra_body={"reasoning": {"effort": "none"}},
        extra_headers={
            "X-Title": "SixSentences_",
        },
    ),
    "anthropic": ProviderSpec(
        "anthropic",
        kind="anthropic",
        base_url="",
        key_env="ANTHROPIC_API_KEY",
        cheap="claude-haiku-4-5",
        strong="claude-opus-4-8",
    ),
    "openai": ProviderSpec(
        "openai",
        kind="openai-compat",
        base_url="https://api.openai.com/v1",
        key_env="OPENAI_API_KEY",
        cheap="gpt-5-mini",
        strong="gpt-5",
    ),
    "gemini": ProviderSpec(
        "gemini",
        kind="gemini-native",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        key_env="SIX_GEMINI_API_KEY",
        cheap=PRIVATE_DEFAULT_MODEL,
        strong=PRIVATE_PREMIUM_MODEL,
    ),
    "deepseek": ProviderSpec(
        "deepseek",
        kind="openai-compat",
        base_url="https://api.deepseek.com/v1",
        key_env="DEEPSEEK_API_KEY",
        # V4 lineup; legacy deepseek-chat/deepseek-reasoner deprecate 2026-07-24
        cheap="deepseek-v4-flash",
        strong="deepseek-v4-pro",
        # V4 defaults thinking ON (CoT goes to reasoning_content); we want direct
        # answers/structured JSON, so disable it for deterministic, cheaper calls.
        extra_body={"thinking": {"type": "disabled"}},
    ),
    "qwen": ProviderSpec(
        "qwen",
        kind="openai-compat",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        key_env="DASHSCOPE_API_KEY",
        cheap="qwen-plus",
        strong="qwen-max",
    ),
    "kimi": ProviderSpec(
        "kimi",
        kind="openai-compat",
        base_url="https://api.moonshot.ai/v1",
        key_env="MOONSHOT_API_KEY",
        cheap="kimi-k2.6",
        strong="kimi-k2.6",
    ),
    "minimax": ProviderSpec(
        "minimax",
        kind="openai-compat",
        base_url="https://api.minimax.io/v1",
        key_env="MINIMAX_API_KEY",
        # Verified against GET /v1/models 2026-07: M3 is the flagship; the
        # M2.7 highspeed variant is the cheap screening reviewer. The M-series
        # prepends <think>…</think> inside `content`, stripped by the client.
        cheap="MiniMax-M2.7-highspeed",
        strong="MiniMax-M3",
    ),
    "mistral": ProviderSpec(
        "mistral",
        kind="openai-compat",
        base_url="https://api.mistral.ai/v1",
        key_env="MISTRAL_API_KEY",
        cheap="mistral-small-latest",
        strong="mistral-large-latest",
    ),
}


_PROVIDER_DIAGNOSTIC_CODES = frozenset(
    {
        "provider_error",
        "permanent_http",
        "transient_http",
        "retry_exhausted",
        "routes_exhausted",
        "transport_error",
        "read_timeout",
        "connect_timeout",
        "write_timeout",
        "pool_timeout",
        "stream_error",
        "stream_interrupted",
        "unexpected_response_shape",
        "empty_response",
        "invalid_structured_response",
    }
)


class ProviderError(RuntimeError):
    """Provider failure with content-free, non-authoritative diagnostic facts.

    Existing callers may still supply only a message. Typed diagnostic codes
    may distinguish an invalid screening object from a failed provider route;
    raw messages never authorize retries or derive diagnostic fields.
    """

    def __init__(
        self,
        message: str = "",
        *,
        diagnostic_code: str = "provider_error",
        http_status: int | None = None,
        incurred_response: LLMResponse | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic_code = (
            diagnostic_code
            if isinstance(diagnostic_code, str) and diagnostic_code in _PROVIDER_DIAGNOSTIC_CODES
            else "provider_error"
        )
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )
        # Some failures happen after billable provider egress. This is only
        # token/cost metadata, never a failed answer or private prompt.
        self.incurred_response = incurred_response


def provider_failure_diagnostics(error: BaseException | None) -> tuple[str, int | None]:
    """Read only allowlisted categories and numeric status, never error text."""
    if isinstance(error, ProviderError):
        code = error.diagnostic_code
        status = error.http_status
        return (
            code
            if isinstance(code, str) and code in _PROVIDER_DIAGNOSTIC_CODES
            else "provider_error",
            status if type(status) is int and 100 <= status <= 599 else None,
        )
    for error_type, code in (
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.TransportError, "transport_error"),
        (httpx.StreamError, "stream_error"),
    ):
        if isinstance(error, error_type):
            return code, None
    return "none" if error is None else "unknown_error", None


class _TransientProviderError(ProviderError):
    """A provider failure worth retrying (rate limits, 5xx, flaky gateways)."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        diagnostic_code: str = "provider_error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message, diagnostic_code=diagnostic_code, http_status=http_status)
        self.retry_after = retry_after


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise LLMCancelledError("LLM request cancelled")


def _cached_input_tokens(usage: dict[str, Any], input_tokens: int) -> int:
    """Normalize OpenAI-compatible prompt-cache usage fields."""
    candidates = [usage.get("prompt_cache_hit_tokens")]
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        candidates.append(details.get("cached_tokens"))
    parsed: list[int] = []
    for value in candidates:
        if value is None:
            continue
        try:
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    return min(max(parsed, default=0), max(0, input_tokens))


class _StreamCancellationWatcher:
    """Close one active response when cancellation arrives during a blocked read.

    Checking between SSE lines is not sufficient because a provider can stop
    sending data while ``iter_lines`` is blocked. A small daemon watcher closes
    only this response, which releases the read without shutting down the
    shared, thread-safe HTTP client.
    """

    def __init__(
        self,
        response: httpx.Response,
        cancel_check: Callable[[], bool] | None,
    ) -> None:
        self.response = response
        self.cancel_check = cancel_check
        self.cancelled = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_StreamCancellationWatcher":
        _raise_if_cancelled(self.cancel_check)
        if self.cancel_check is not None:
            self._thread = threading.Thread(
                target=self._watch,
                name="llm-stream-cancellation",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)

    def _watch(self) -> None:
        while not self._stop.wait(0.05):
            try:
                should_cancel = bool(self.cancel_check and self.cancel_check())
            except Exception:
                # The normal request thread remains responsible for surfacing
                # callback failures. A watcher must never close a response on
                # an inconclusive cancellation check.
                _LOG.exception("LLM cancellation check failed in stream watcher")
                continue
            if not should_cancel:
                continue
            self.cancelled.set()
            with suppress(Exception):
                self.response.close()
            # The main thread observes ``cancelled`` and raises the stable
            # cancellation exception even if the transport was closing.
            return

    def check(self) -> None:
        if self.cancelled.is_set():
            raise LLMCancelledError("LLM request cancelled")
        _raise_if_cancelled(self.cancel_check)


_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_ORPHAN_THINK_PREFIX = re.compile(r"^.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK_SUFFIX = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Drop reasoning blocks some models (MiniMax M-series) emit inside
    `content`. Downstream consumers want the answer, never the scratchpad —
    a leading think block would break every strict-JSON parse."""
    cleaned = _THINK_BLOCK.sub("", text)
    # Some OpenAI-compatible gateways expose the opening reasoning marker via
    # a dedicated field but leave the closing marker in ``content``. In that
    # shape everything before the first orphan ``</think>`` is still private
    # scratchpad and must be discarded, not merely have the tag removed.
    if "</think>" in cleaned.casefold():
        cleaned = _ORPHAN_THINK_PREFIX.sub("", cleaned, count=1)
    # Never surface a truncated private block after a provider interruption.
    cleaned = _UNCLOSED_THINK_SUFFIX.sub("", cleaned)
    return cleaned.replace("</think>", "").strip()


def _reasoning_phase(delta: dict[str, Any]) -> str:
    """Map provider reasoning to a safe, high-level progress phase.

    Provider reasoning can contain a private model scratchpad. It is useful as
    a progress signal, but must never be rendered or persisted verbatim. The
    returned identifiers are deliberately coarse and translated by the client.
    Encrypted or signed preservation blocks are ignored entirely.
    """
    details = delta.get("reasoning_details")
    fragments: list[str] = []
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            kind = str(detail.get("type") or "")
            value = detail.get("text") if kind == "reasoning.text" else None
            if kind == "reasoning.summary":
                value = detail.get("summary")
            if isinstance(value, str) and value and value != "[REDACTED]":
                fragments.append(value)
    raw = " ".join(fragments) if fragments else ""
    for key in ("reasoning", "reasoning_content"):
        value = delta.get(key)
        if isinstance(value, str) and value and value != "[REDACTED]":
            raw = f"{raw} {value}".strip()
    if not raw:
        return ""

    normalized = raw.casefold()
    if any(term in normalized for term in ("contradict", "conflict", "disagree")):
        return "conflict_check"
    if any(term in normalized for term in ("citation", "cite", "support", "verify claim")):
        return "citation_check"
    if any(term in normalized for term in ("source", "evidence", "paper", "study", "compare")):
        return "source_comparison"
    if any(term in normalized for term in ("query", "search", "retrieve", "tool", "browse")):
        return "research_plan"
    if any(term in normalized for term in ("structure", "answer", "explain", "summar")):
        return "answer_structure"
    return "evidence_analysis"


class _VisibleDeltaFilter:
    """Hold a possible leading think block until it can be discarded."""

    def __init__(self, on_delta: Callable[[str], None]) -> None:
        self.on_delta = on_delta
        self.pending = ""
        self.decided = False
        # Hold only the short leading prefix in which gateways have been seen
        # to place an orphan ``</think>``. Normal answers still stream after a
        # small prefix while private scratchpad never flashes in the UI.
        self.probe_chars = 128

    def feed(self, value: str) -> None:
        if not value:
            return
        if self.decided:
            self.on_delta(value)
            return
        self.pending += value
        stripped = self.pending.lstrip()
        orphan_end = stripped.casefold().find("</think>")
        if orphan_end >= 0:
            self.decided = True
            visible = stripped[orphan_end + len("</think>") :].lstrip()
            self.pending = ""
            if visible:
                self.on_delta(visible)
            return
        if stripped.startswith("<think>"):
            end = stripped.find("</think>")
            if end < 0:
                return
            self.decided = True
            visible = stripped[end + len("</think>") :].lstrip()
            self.pending = ""
            if visible:
                self.on_delta(visible)
            return
        if len(stripped) < len("<think>") and "<think>".startswith(stripped):
            return
        if len(stripped) < self.probe_chars:
            return
        self.decided = True
        visible = self.pending
        self.pending = ""
        if visible:
            self.on_delta(visible)

    def finish(self) -> None:
        """Release a short normal prefix, but never an unfinished think block."""
        if self.decided or not self.pending:
            return
        stripped = self.pending.lstrip()
        self.pending = ""
        if stripped.startswith("<think>") or "<think>".startswith(stripped):
            return
        self.decided = True
        self.on_delta(stripped)


class OpenAICompatClient(LLMClient):
    """Chat-completions client for any OpenAI-compatible endpoint."""

    def __init__(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        http: httpx.Client | None = None,
        max_retries: int = 0,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        self.max_retries = max_retries
        self._sleep = sleeper
        self._jitter = jitter or (lambda delay: random.uniform(delay * 0.8, delay * 1.2))
        self.extra_body = extra_body or {}
        # OpenRouter's response cache is independent from provider-level ZDR.
        # Send an explicit per-request opt-out so neither account nor preset
        # defaults can retain a response unexpectedly.
        self._request_headers = {"X-OpenRouter-Cache": "false"} if provider == "openrouter" else {}
        self.http = http or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
            headers={"Authorization": f"Bearer {api_key}", **(extra_headers or {})},
        )

    def complete(
        self,
        model: str,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        json_response: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        _raise_if_cancelled(cancel_check)
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            **self.extra_body,
        }
        if json_response:
            body["response_format"] = {"type": "json_object"}
        if self.provider == "openrouter":
            if model == "google/gemini-3.7-flash":
                # Gemini 3.7 Flash has mandatory reasoning on OpenRouter and
                # rejects the provider-wide economical ``none`` default.
                # Internal non-streaming steps do not expose deliberation, but
                # still need a supported effort so a pinned model never falls
                # through to another route after a preventable 4xx.
                body["reasoning"] = {"effort": "low", "exclude": True}
            max_price = _openrouter_max_price(model)
            body["provider"] = {
                "sort": "price",
                # Workspace content may only be routed to endpoints whose
                # published data policy excludes collection for training.
                "data_collection": "deny",
                # Fail closed if the selected endpoint is not Zero Data
                # Retention compliant, even when its general collection
                # declaration would otherwise satisfy the policy above.
                "zdr": True,
                # If catalog pricing moves beyond the safety margin, fail
                # closed instead of silently routing one high-volume task to
                # a materially more expensive endpoint.
                "max_price": max_price,
                **({"require_parameters": True} if json_response else {}),
            }
        response = self._post_with_retry(
            body,
            validate=lambda candidate: self._validate_completion_response(
                candidate,
                require_json=json_response,
            ),
            cancel_check=cancel_check,
        )
        _raise_if_cancelled(cancel_check)
        data = response.json()
        try:
            text = _strip_think(data["choices"][0]["message"]["content"] or "")
            usage = data.get("usage") or {}
        except (KeyError, IndexError) as exc:
            raise ProviderError(
                f"{self.provider}: unexpected response shape",
                diagnostic_code="unexpected_response_shape",
                http_status=response.status_code,
            ) from exc
        provider_cost = usage.get("cost") if self.provider == "openrouter" else None
        try:
            billed_cost = float(provider_cost) if provider_cost is not None else None
        except (TypeError, ValueError):
            billed_cost = None
        input_tokens = int(usage.get("prompt_tokens") or 0)
        return LLMResponse(
            text=text,
            model=str(data.get("model") or model),
            provider=self.provider,
            input_tokens=input_tokens,
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_input_tokens=_cached_input_tokens(usage, input_tokens),
            cost_usd=billed_cost,
            cost_source="provider" if billed_cost is not None else "catalog",
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
        cancel_check: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """Stream visible OpenAI-compatible deltas and assemble the response."""
        _raise_if_cancelled(cancel_check)
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            **self.extra_body,
        }
        if self.provider == "openrouter":
            if on_reasoning is not None:
                # Low effort keeps visible deliberation useful without turning
                # a normal answer into a disproportionately expensive run.
                body["reasoning"] = {"effort": "low", "exclude": False}
            elif model == "google/gemini-3.7-flash":
                # The model requires reasoning even when the caller does not
                # render reasoning deltas.
                body["reasoning"] = {"effort": "low", "exclude": True}
        if self.provider == "openrouter":
            body["provider"] = {
                "sort": "price",
                "data_collection": "deny",
                "zdr": True,
                "max_price": _openrouter_max_price(model),
            }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_check)
            visible_received = False
            parts: list[str] = []
            reasoning_parts: list[str] = []
            emitted_reasoning_phases: set[str] = set()
            usage: dict[str, Any] = {}
            response_model = model

            def emit_visible(delta: str) -> None:
                nonlocal visible_received
                _raise_if_cancelled(cancel_check)
                visible_received = True
                on_delta(delta)

            visible = _VisibleDeltaFilter(emit_visible)
            watcher: _StreamCancellationWatcher | None = None
            try:
                _raise_if_cancelled(cancel_check)
                with self.http.stream(
                    "POST",
                    "/chat/completions",
                    headers=self._request_headers,
                    json=body,
                ) as response:
                    if self._is_permanent_status(response.status_code):
                        response.read()
                        raise ProviderError(
                            f"{self.provider}: HTTP {response.status_code} (permanent)",
                            diagnostic_code="permanent_http",
                            http_status=response.status_code,
                        )
                    if response.status_code >= 400:
                        response.read()
                        raise _TransientProviderError(
                            f"{self.provider}: HTTP {response.status_code}",
                            retry_after=self._retry_after(response),
                            diagnostic_code="transient_http",
                            http_status=response.status_code,
                        )
                    watcher = _StreamCancellationWatcher(response, cancel_check)
                    with watcher:
                        for line in response.iter_lines():
                            watcher.check()
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if not raw or raw == "[DONE]":
                                continue
                            try:
                                event = json.loads(raw)
                            except ValueError:
                                continue
                            response_model = str(event.get("model") or response_model)
                            if event.get("usage"):
                                usage = event["usage"]
                            choices = event.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning_phase = _reasoning_phase(delta)
                            if reasoning_phase and reasoning_phase not in emitted_reasoning_phases:
                                watcher.check()
                                visible_received = True
                                emitted_reasoning_phases.add(reasoning_phase)
                                reasoning_parts.append(reasoning_phase)
                                if on_reasoning is not None:
                                    on_reasoning(f"{reasoning_phase}\n")
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                parts.append(content)
                                visible.feed(content)
                        watcher.check()
                visible.finish()
                _raise_if_cancelled(cancel_check)
                text = _strip_think("".join(parts))
                if not text:
                    raise _TransientProviderError(
                        f"{self.provider}: empty streamed completion",
                        diagnostic_code="empty_response",
                        http_status=response.status_code,
                    )
                provider_cost = usage.get("cost") if self.provider == "openrouter" else None
                try:
                    billed_cost = float(provider_cost) if provider_cost is not None else None
                except (TypeError, ValueError):
                    billed_cost = None
                input_tokens = int(usage.get("prompt_tokens") or 0)
                return LLMResponse(
                    text=text,
                    model=response_model,
                    provider=self.provider,
                    input_tokens=input_tokens,
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    cached_input_tokens=_cached_input_tokens(usage, input_tokens),
                    cost_usd=billed_cost,
                    cost_source="provider" if billed_cost is not None else "catalog",
                    reasoning="\n".join(reasoning_parts) or None,
                )
            except LLMCancelledError:
                raise
            except (
                _TransientProviderError,
                httpx.TransportError,
                httpx.StreamError,
            ) as exc:
                if watcher is not None and watcher.cancelled.is_set():
                    raise LLMCancelledError("LLM request cancelled") from None
                _raise_if_cancelled(cancel_check)
                # Release a buffered normal prefix before deciding whether a
                # retry is safe. ``finish`` deliberately keeps private
                # ``<think>`` prefixes hidden, so only genuinely visible
                # output turns the interruption into a fail-fast error.
                visible.finish()
                last_error = exc
                if visible_received:
                    raise ProviderError(
                        f"{self.provider}: stream interrupted after output began",
                        diagnostic_code="stream_interrupted",
                        http_status=provider_failure_diagnostics(exc)[1],
                    ) from exc
                if self._can_retry(attempt, exc):
                    self._sleep_before_retry(attempt, exc, cancel_check=cancel_check)
                    continue
                break
            except ProviderError:
                raise
        raise ProviderError(
            f"{self.provider}: retries exhausted: {last_error}",
            diagnostic_code="retry_exhausted",
            http_status=provider_failure_diagnostics(last_error)[1],
        ) from last_error

    # Statuses that signal a permanent problem with the request or the key:
    # retrying only burns time and budget, so these fail fast. Direct MiniMax
    # has previously used 404 as a burst-limit response, but OpenRouter uses it
    # for deterministic routing/policy failures; only the latter fails fast.
    def _is_permanent_status(self, status_code: int) -> bool:
        if status_code == 404 and self.provider != "openrouter":
            return False
        return 400 <= status_code < 500 and status_code not in {408, 409, 425, 429}

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after", "").strip()
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None

    def _sleep_before_retry(
        self,
        attempt: int,
        error: Exception,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        retry_after = error.retry_after if isinstance(error, _TransientProviderError) else None
        if retry_after is not None:
            delay = min(retry_after, 8.0)
        else:
            delay = min(self._jitter(1.5 * (2**attempt)), 8.0)
        _LOG.warning(
            "provider request retry scheduled; provider=%s attempt=%s delay_seconds=%.3f reason=%s",
            self.provider,
            attempt + 1,
            delay,
            type(error).__name__,
        )
        delay = max(0.0, delay)
        if cancel_check is None:
            self._sleep(delay)
            return
        remaining = delay
        while remaining > 0:
            _raise_if_cancelled(cancel_check)
            interval = min(0.05, remaining)
            self._sleep(interval)
            remaining -= interval
        _raise_if_cancelled(cancel_check)

    def _can_retry(self, attempt: int, error: Exception) -> bool:
        if attempt >= self.max_retries:
            return False
        # A read timeout already consumed the full inactivity window. Trying
        # another routed model is faster than waiting on the same stalled
        # generation two more times.
        if isinstance(error, httpx.ReadTimeout):
            return False
        # Connection/write faults happen before a useful response exists, but
        # one retry is enough before the pool moves to its fallback route.
        if isinstance(error, (httpx.ConnectTimeout, httpx.WriteTimeout)):
            return attempt == 0
        return True

    def _validate_completion_response(
        self,
        response: httpx.Response,
        *,
        require_json: bool = False,
    ) -> None:
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _TransientProviderError(
                f"{self.provider}: malformed completion response",
                diagnostic_code="unexpected_response_shape",
                http_status=response.status_code,
            ) from exc
        if not isinstance(content, str) or not _strip_think(content):
            raise _TransientProviderError(
                f"{self.provider}: empty completion response",
                diagnostic_code="empty_response",
                http_status=response.status_code,
            )
        if require_json:
            try:
                structured = json.loads(_strip_think(content))
            except (TypeError, ValueError) as exc:
                raise _TransientProviderError(
                    f"{self.provider}: malformed structured completion",
                    diagnostic_code="invalid_structured_response",
                    http_status=response.status_code,
                ) from exc
            if not isinstance(structured, dict):
                raise _TransientProviderError(
                    f"{self.provider}: structured completion is not an object",
                    diagnostic_code="invalid_structured_response",
                    http_status=response.status_code,
                )

    def _post_with_retry(
        self,
        body: dict[str, object],
        *,
        validate: Callable[[httpx.Response], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_check)
            try:
                response = self.http.post(
                    "/chat/completions",
                    headers=self._request_headers,
                    json=body,
                )
                _raise_if_cancelled(cancel_check)
                if self._is_permanent_status(response.status_code):
                    raise ProviderError(
                        f"{self.provider}: HTTP {response.status_code} (permanent)",
                        diagnostic_code="permanent_http",
                        http_status=response.status_code,
                    ) from None
                if response.status_code >= 400:
                    raise _TransientProviderError(
                        f"{self.provider}: HTTP {response.status_code}",
                        retry_after=self._retry_after(response),
                        diagnostic_code="transient_http",
                        http_status=response.status_code,
                    )
                if validate is not None:
                    validate(response)
                _raise_if_cancelled(cancel_check)
                return response
            except LLMCancelledError:
                raise
            except (_TransientProviderError, httpx.TransportError) as exc:
                _raise_if_cancelled(cancel_check)
                last_error = exc
                if self._can_retry(attempt, exc):
                    self._sleep_before_retry(attempt, exc, cancel_check=cancel_check)
                    continue
                break
        raise ProviderError(
            f"{self.provider}: retries exhausted: {last_error}",
            diagnostic_code="retry_exhausted",
            http_status=provider_failure_diagnostics(last_error)[1],
        ) from last_error


def build_client(spec: ProviderSpec) -> LLMClient:
    if spec.kind == "anthropic":
        from sixsentences_server.llm.anthropic_client import AnthropicClient

        return AnthropicClient()
    if spec.name == "gemini":
        from sixsentences_server.llm.gemini_client import GeminiClient

        api_key = get_settings().gemini_egress_api_key
        if not api_key:
            raise LLMConfigError(
                "direct Gemini requires SIX_GEMINI_API_KEY and "
                "SIX_GEMINI_DATA_PROCESSING_CONFIRMED=1"
            )
        return GeminiClient(api_key=api_key, max_retries=2)
    else:
        api_key = os.environ.get(spec.key_env, "")
    base_url = get_settings().openrouter_base_url if spec.name == "openrouter" else spec.base_url
    return OpenAICompatClient(
        provider=spec.name,
        base_url=base_url,
        api_key=api_key,
        max_retries=2,
        extra_body=spec.extra_body,
        extra_headers=spec.extra_headers,
    )


def available_specs(*, content_scope: ContentScope = "private") -> list[ProviderSpec]:
    """Providers used for automatic routing.

    Private is the safe default and requires paid direct Gemini. Public scope
    is an explicit server-side boundary, not inferred from research topics.
    Neither route silently falls back to the other if credentials are absent.
    """
    if content_scope == "private":
        gemini = PROVIDERS["gemini"]
        return [gemini] if gemini.available() else []
    if content_scope != "public":
        raise LLMConfigError("Unknown AI content scope")
    openrouter = PROVIDERS["openrouter"]
    if openrouter.available():
        return [openrouter]
    return []


_OPENROUTER_SCREENING_MODELS = (
    "deepseek/deepseek-v4-flash",
    "nvidia/nemotron-3-super-120b-a12b",
)


def default_screening_refs(specs: list[ProviderSpec], max_models: int = 5) -> list[ModelRef]:
    """Return distinct screening models without claiming cross-vendor review.

    OpenRouter is one gateway rather than one reviewer. Treating it as a
    single provider previously left the default route with one model, so the
    recall guard downgraded every otherwise valid exclusion to ``unsure``.
    Two inexpensive model families keep the no-single-model-exclusion policy
    meaningful without routing high-volume screening through frontier models.
    An explicit migration/test routing override still uses one cheap model per
    named adapter; it is not part of automatic production routing.
    """
    refs: list[ModelRef] = []
    for spec in specs:
        if spec.name == "openrouter":
            refs.extend(ModelRef(spec.name, model) for model in _OPENROUTER_SCREENING_MODELS)
        elif spec.name == "gemini":
            # Internal, distinct Lite models; a premium chat choice never fans
            # out per paper. Their errors are not cross-vendor independent.
            refs.extend(ModelRef(spec.name, model) for model in PRIVATE_SCREENING_MODELS)
        else:
            refs.append(ModelRef(spec.name, spec.cheap))
    return refs[:max_models]


def default_strong_ref(specs: list[ProviderSpec]) -> ModelRef | None:
    if not specs:
        return None
    # Preserve the historical preference only for an explicit migration/test
    # adapter list; private automatic production uses direct Gemini above.
    for spec in specs:
        if spec.name == "anthropic":
            return ModelRef(spec.name, spec.strong)
    return ModelRef(specs[0].name, specs[0].strong)
