"""Anthropic client adapter (native SDK).

Model behavior notes (API, mid-2026): claude-opus-4-8 and sonnet-5 get
adaptive thinking; claude-haiku-4-5 is a pre-4.6 surface and is called
without a thinking parameter (screening calls are deliberately fast/cheap).
Credentials resolve via the SDK (ANTHROPIC_API_KEY or `ant auth login`).
"""

from collections.abc import Callable

import anthropic

from sixsentences_server.llm.base import LLMCancelledError, LLMClient, LLMResponse

_ADAPTIVE_THINKING_MODELS = {"claude-opus-4-8", "claude-sonnet-5"}


class AnthropicClient(LLMClient):
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self.client = client or anthropic.Anthropic()

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
        if model in _ADAPTIVE_THINKING_MODELS:
            message = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
            )
        else:
            message = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        text = "".join(block.text for block in message.content if block.type == "text")
        return LLMResponse(
            text=text,
            model=model,
            provider="anthropic",
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
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
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        if model in _ADAPTIVE_THINKING_MODELS:
            manager = self.client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
            )
        else:
            manager = self.client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        chunks: list[str] = []
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        with manager as stream:
            for text in stream.text_stream:
                if cancel_check is not None and cancel_check():
                    raise LLMCancelledError("LLM request cancelled")
                chunks.append(text)
                on_delta(text)
            message = stream.get_final_message()
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        return LLMResponse(
            text="".join(chunks),
            model=model,
            provider="anthropic",
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
