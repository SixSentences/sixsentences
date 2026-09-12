"""Deterministic mock client/pool for tests and keyless dev runs."""

import time
from collections.abc import Callable

from sixsentences_server.llm.base import (
    BudgetGovernor,
    LLMCancelledError,
    LLMClient,
    LLMResponse,
    ModelRef,
)
from sixsentences_server.llm.pool import LLMPool, RoutingConfig

Handler = Callable[[str, str], str]  # (model, prompt) -> text


class MockClient(LLMClient):
    def __init__(
        self,
        handler: Handler | None = None,
        *,
        stream_delay_seconds: float = 0.0,
    ) -> None:
        self.handler = handler or (lambda model, prompt: f"[mock:{model}]")
        self.stream_delay_seconds = max(0.0, stream_delay_seconds)
        self.calls: list[tuple[str, str]] = []

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
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        self.calls.append((model, prompt))
        text = self.handler(model, prompt)
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        return LLMResponse(
            text=text,
            model=model,
            provider="mock",
            input_tokens=len(prompt) // 4,  # rough token proxy for usage tests
            output_tokens=len(text) // 4,
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
        response = self.complete(
            model,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            cancel_check=cancel_check,
        )
        for start in range(0, len(response.text), 24):
            if cancel_check is not None and cancel_check():
                raise LLMCancelledError("LLM request cancelled")
            on_delta(response.text[start : start + 24])
            if self.stream_delay_seconds:
                time.sleep(self.stream_delay_seconds)
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        return response


def mock_pool(
    handler: Handler | None = None,
    *,
    screening_models: int = 2,
    budget: BudgetGovernor | None = None,
    stream_delay_seconds: float = 0.0,
) -> LLMPool:
    """Pool with one mock provider, N screening 'models', strong routing set."""
    client = MockClient(handler, stream_delay_seconds=stream_delay_seconds)
    routing = RoutingConfig(
        synthesis=ModelRef("mock", "mock-strong"),
        adjudication=ModelRef("mock", "mock-strong"),
        screening=[ModelRef("mock", f"mock-cheap-{i}") for i in range(screening_models)],
    )
    return LLMPool(
        clients={"mock": client},
        routing=routing,
        budget=budget or BudgetGovernor(limit_usd=10.0),
    )
