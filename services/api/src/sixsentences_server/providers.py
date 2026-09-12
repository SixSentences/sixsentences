"""Deployment-owned AI and speech adapters with bounded egress."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from sixsentences_server.config import Settings


class ProviderUnavailable(RuntimeError):
    """Raised when an optional deployment adapter is disabled or malformed."""


class TextProvider(Protocol):
    """Minimal interface used by writing and brainstorming jobs."""

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str: ...


class SpeechProvider(Protocol):
    """Minimal turn-based transcription interface used by public voice sessions."""

    def transcribe(self, *, content: bytes, media_type: str) -> str: ...


@dataclass(frozen=True, slots=True)
class DisabledTextProvider:
    """Fail closed when no AI processor has been configured by the operator."""

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        del system, messages
        raise ProviderUnavailable("AI provider is disabled")


@dataclass(frozen=True, slots=True)
class DisabledSpeechProvider:
    """Fail closed when no speech processor has been configured by the operator."""

    def transcribe(self, *, content: bytes, media_type: str) -> str:
        del content, media_type
        raise ProviderUnavailable("voice provider is disabled")


class OpenAICompatibleTextProvider:
    """Small adapter for deployment-selected chat-completion endpoints."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.ai_base_url.rstrip("/")
        self.model = settings.ai_model
        self.max_output_tokens = settings.ai_max_output_tokens
        key = settings.ai_api_key.get_secret_value()
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.timeout = settings.ai_timeout_seconds

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": self.max_output_tokens,
            "temperature": 0.2,
        }
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions", headers=self.headers, json=payload
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable("AI provider request failed") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable("AI provider returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderUnavailable("AI provider returned an empty response")
        return content[:200_000]


class OpenAICompatibleSpeechProvider:
    """Turn-based adapter for deployment-selected transcription endpoints."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.voice_base_url.rstrip("/")
        self.model = settings.voice_model
        key = settings.voice_api_key.get_secret_value()
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.timeout = settings.voice_timeout_seconds

    def transcribe(self, *, content: bytes, media_type: str) -> str:
        extension = {
            "audio/wav": "wav",
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
        }[media_type]
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                response = client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=self.headers,
                    data={"model": self.model},
                    files={"file": (f"turn.{extension}", content, media_type)},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable("voice provider request failed") from exc
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderUnavailable("voice provider returned an invalid transcript")
        return text[:65_536]


def text_provider(settings: Settings) -> TextProvider:
    """Build the selected AI adapter without guessing credentials or endpoints."""

    if settings.ai_provider == "openai_compatible":
        return OpenAICompatibleTextProvider(settings)
    return DisabledTextProvider()


def speech_provider(settings: Settings) -> SpeechProvider:
    """Build the selected speech adapter without exposing its credential to clients."""

    if settings.voice_provider == "openai_compatible":
        return OpenAICompatibleSpeechProvider(settings)
    return DisabledSpeechProvider()
