"""Native, stateless Gemini Paid text transport for private workspace content.

No Files, grounding, cachedContent, Interactions or provider fallback is used.
Thought parts are excluded from visible output but included in token accounting.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from sixsentences_server.llm.base import (
    LLMCancelledError,
    LLMClient,
    LLMResponse,
    ModelRef,
)
from sixsentences_server.llm.gemini_pricing import parse_gemini_usage
from sixsentences_server.llm.privacy import require_route_for_scope
from sixsentences_server.llm.providers import (
    ProviderError,
    _raise_if_cancelled,
    _StreamCancellationWatcher,
)


def _body(
    model: str,
    system: str,
    prompt: str,
    max_tokens: int,
    json_response: bool,
) -> dict[str, Any]:
    # Both current Lite reviewer generations support minimal thinking. Keep
    # their short structured responses bounded; reported thoughts remain billed.
    thinking: dict[str, Any] = {"thinkingLevel": "low", "includeThoughts": False}
    if model in {"gemini-3.1-flash-lite", "gemini-3.5-flash-lite"}:
        thinking = {"thinkingLevel": "minimal", "includeThoughts": False}
    config: dict[str, Any] = {
        "maxOutputTokens": max_tokens,
        "thinkingConfig": thinking,
    }
    if json_response:
        config["responseMimeType"] = "application/json"
    return {
        "serviceTier": "standard",
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": config,
    }


def _text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return ""
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return ""
    content = candidate.get("content") or {}
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")
    )


def _result(
    model: str,
    text: str,
    usage: dict[str, Any],
    *,
    input_estimate: int,
    output_cap: int,
) -> LLMResponse:
    # Native candidates and thoughts are distinct fields. Never add thought
    # tokens to totalTokenCount, which already includes them and the input.
    parsed = parse_gemini_usage(usage)
    return LLMResponse(
        text=text,
        model=model,
        provider="gemini",
        input_tokens=parsed.input_tokens if parsed else input_estimate,
        output_tokens=parsed.output_tokens if parsed else output_cap,
        cached_input_tokens=parsed.cached_input_tokens if parsed else 0,
        cost_source="gemini_usage" if parsed else "gemini_estimate",
    )


class _AccountedCancellation(LLMCancelledError):
    """Cancellation after provider dispatch, with content-free usage metadata."""

    def __init__(self, response: LLMResponse) -> None:
        super().__init__("LLM request cancelled")
        self.incurred_response = response


def _no_billable_egress(error: BaseException) -> bool:
    return isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)) or (
        isinstance(error, ProviderError)
        and error.http_status is not None
        and 400 <= error.http_status < 500
    )


def _incurred_result(
    model: str,
    usage: dict[str, Any],
    input_estimate: int,
    output_cap: int,
) -> LLMResponse:
    parsed = parse_gemini_usage(usage)
    return LLMResponse(
        text="",
        model=model,
        provider="gemini",
        input_tokens=parsed.input_tokens if parsed else input_estimate,
        output_tokens=parsed.output_tokens if parsed else output_cap,
        cached_input_tokens=parsed.cached_input_tokens if parsed else 0,
        cost_source="gemini_usage" if parsed else "uncertain",
    )


class GeminiClient(LLMClient):
    """Bounded native Gemini calls with content-free errors and cancellation."""

    def __init__(
        self,
        *,
        api_key: str,
        http: httpx.Client | None = None,
        max_retries: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = "gemini"
        self.max_retries = max_retries
        self._sleep = sleeper
        self.http = http or httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            headers={"x-goog-api-key": api_key},
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0),
        )

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise ProviderError(
                f"Gemini HTTP {response.status_code}",
                diagnostic_code=(
                    "transient_http"
                    if response.status_code in {429, 500, 502, 503, 504}
                    else "permanent_http"
                ),
                http_status=response.status_code,
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
        """Generate visible text or a complete JSON object through Google only."""
        require_route_for_scope(ModelRef("gemini", model), "private")
        body = _body(model, system, prompt, max_tokens, json_response)
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_check)
            usage: dict[str, Any] = {}
            try:
                response = self.http.post(f"models/{model}:generateContent", json=body)
                self._check_response(response)
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Invalid response")
                if isinstance(payload.get("usageMetadata"), dict):
                    usage = payload["usageMetadata"]
                text = _text(payload).strip()
                if not text:
                    raise ProviderError(
                        "Gemini returned no visible answer",
                        diagnostic_code="empty_response",
                    )
                if json_response:
                    try:
                        parsed = json.loads(text)
                    except ValueError as exc:
                        raise ProviderError(
                            "Gemini returned incomplete structured output",
                            diagnostic_code="invalid_structured_response",
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise ProviderError(
                            "Gemini returned no structured object",
                            diagnostic_code="invalid_structured_response",
                        )
                return _result(
                    model,
                    text,
                    usage,
                    input_estimate=len(system.encode()) + len(prompt.encode()) + 256,
                    output_cap=max_tokens,
                )
            except (ProviderError, httpx.TransportError, ValueError) as exc:
                if not _no_billable_egress(exc):
                    incurred = _incurred_result(
                        model,
                        usage,
                        len(system.encode()) + len(prompt.encode()) + 256,
                        max_tokens,
                    )
                    if cancel_check is not None and cancel_check():
                        raise _AccountedCancellation(incurred) from exc
                    raise ProviderError(
                        "Gemini request could not complete",
                        diagnostic_code=(
                            exc.diagnostic_code
                            if isinstance(exc, ProviderError)
                            else "transport_error"
                        ),
                        http_status=exc.http_status if isinstance(exc, ProviderError) else None,
                        incurred_response=incurred,
                    ) from exc
                _raise_if_cancelled(cancel_check)
                if (
                    isinstance(exc, ProviderError) and exc.http_status != 429
                ) or attempt == self.max_retries:
                    raise ProviderError(
                        "Gemini request could not complete",
                        diagnostic_code="permanent_http"
                        if isinstance(exc, ProviderError)
                        else "transport_error",
                        http_status=exc.http_status if isinstance(exc, ProviderError) else None,
                    ) from exc
            self._sleep(min(2.0, 0.25 * 2**attempt))
        raise ProviderError("Gemini request could not complete")

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
        """Stream candidate text, never thought parts; stop safely after visible output."""
        require_route_for_scope(ModelRef("gemini", model), "private")
        body = _body(model, system, prompt, max_tokens, False)
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_check)
            chunks: list[str] = []
            usage: dict[str, Any] = {}
            watcher: _StreamCancellationWatcher | None = None
            try:
                with self.http.stream(
                    "POST",
                    f"models/{model}:streamGenerateContent",
                    params={"alt": "sse"},
                    json=body,
                ) as response:
                    self._check_response(response)
                    watcher = _StreamCancellationWatcher(response, cancel_check)
                    with watcher:
                        for line in response.iter_lines():
                            watcher.check()
                            if not line.startswith("data:"):
                                continue
                            payload = json.loads(line[5:].strip())
                            if not isinstance(payload, dict):
                                raise ValueError("Invalid response")
                            if isinstance(payload.get("usageMetadata"), dict):
                                usage.update(payload["usageMetadata"])
                            if delta := _text(payload):
                                chunks.append(delta)
                                on_delta(delta)
                        watcher.check()
                if not chunks:
                    raise ProviderError(
                        "Gemini returned no visible answer",
                        diagnostic_code="empty_response",
                    )
                return _result(
                    model,
                    "".join(chunks),
                    usage,
                    input_estimate=len(system.encode()) + len(prompt.encode()) + 256,
                    output_cap=max_tokens,
                )
            except (
                ProviderError,
                LLMCancelledError,
                httpx.TransportError,
                httpx.StreamError,
                ValueError,
            ) as exc:
                cancelled = (
                    isinstance(exc, LLMCancelledError)
                    or (watcher is not None and watcher.cancelled.is_set())
                    or bool(cancel_check and cancel_check())
                )
                if not _no_billable_egress(exc):
                    incurred = _incurred_result(
                        model,
                        usage,
                        len(system.encode()) + len(prompt.encode()) + 256,
                        max_tokens,
                    )
                    if cancelled:
                        raise _AccountedCancellation(incurred) from exc
                    raise ProviderError(
                        "Gemini stream interrupted after output began"
                        if chunks
                        else "Gemini stream could not complete",
                        diagnostic_code="stream_interrupted" if chunks else "transport_error",
                        incurred_response=incurred,
                    ) from exc
                _raise_if_cancelled(cancel_check)
                if (
                    isinstance(exc, ProviderError) and exc.http_status != 429
                ) or attempt == self.max_retries:
                    raise ProviderError(
                        "Gemini stream could not complete",
                        diagnostic_code="retry_exhausted",
                        http_status=exc.http_status if isinstance(exc, ProviderError) else None,
                    ) from exc
            self._sleep(min(2.0, 0.25 * 2**attempt))
        raise ProviderError("Gemini stream could not complete")
