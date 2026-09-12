"""Legacy provider-credential bounds retained behind the server relay."""

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from sixsentences_server.config import get_settings
from sixsentences_server.voice.service import (
    LIVE_AUDIO_INPUT_MINUTE_COST_USD,
    LIVE_AUDIO_OUTPUT_MINUTE_COST_USD,
    LIVE_BASE_AUDIO_MINUTE_COST_USD,
    LIVE_MAX_OUTPUT_TOKENS,
    LIVE_MINUTE_COST_USD,
    LIVE_SESSION_MAX_MINUTES,
    VoiceProviderError,
    affordable_live_minutes,
    mint_gemini_token,
)


class _Response:
    def __init__(self, status_code: int, payload: dict[str, str]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, str]:
        return self._payload


@pytest.fixture()
def confirmed_direct_gemini(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "secret")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_live_admission_reserve_is_above_the_published_base_audio_rate() -> None:
    assert LIVE_AUDIO_INPUT_MINUTE_COST_USD == 0.005
    assert LIVE_AUDIO_OUTPUT_MINUTE_COST_USD == 0.018
    assert pytest.approx(0.023) == LIVE_BASE_AUDIO_MINUTE_COST_USD
    assert LIVE_MINUTE_COST_USD >= 4 * LIVE_BASE_AUDIO_MINUTE_COST_USD


def test_live_minute_budget_keeps_an_exact_float_boundary() -> None:
    remaining = 3.0 - 2.85

    assert remaining < LIVE_MINUTE_COST_USD
    assert affordable_live_minutes(remaining) == 1
    assert affordable_live_minutes(LIVE_MINUTE_COST_USD * 60) == 60


def test_live_token_is_single_use_and_expires_with_authorized_session(
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> _Response:
        captured.update({"url": url, **kwargs})
        return _Response(200, {"name": "auth_tokens/bounded"})

    monkeypatch.setattr(httpx, "post", fake_post)
    before = datetime.now(UTC)
    token = mint_gemini_token(
        "secret",
        instructions="Ask one question at a time.",
        voice="Charon",
        language="en",
        patience_ms=1_800,
        session_seconds=90,
        timeout=4.0,
    )
    after = datetime.now(UTC)

    assert token == "auth_tokens/bounded"
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/auth_tokens"
    assert captured["headers"] == {"x-goog-api-key": "secret"}
    assert captured["timeout"] == 4.0
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["uses"] == 1
    assert payload["fieldMask"] == (
        "model,generationConfig,systemInstruction,tools,"
        "realtimeInputConfig,contextWindowCompression,"
        "inputAudioTranscription,outputAudioTranscription,"
        "proactivity,historyConfig"
    )
    setup = payload["bidiGenerateContentSetup"]
    assert setup["model"] == "models/gemini-3.1-flash-live-preview"
    assert setup["systemInstruction"] == {"parts": [{"text": "Ask one question at a time."}]}
    assert setup["generationConfig"]["speechConfig"] == {
        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Charon"}},
        "languageCode": "en-US",
    }
    assert setup["generationConfig"]["maxOutputTokens"] == LIVE_MAX_OUTPUT_TOKENS
    assert setup["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert setup["inputAudioTranscription"]["mode"] == "VERBATIM"
    assert setup["outputAudioTranscription"]["languageCodes"] == ["en-US"]
    assert setup["realtimeInputConfig"]["automaticActivityDetection"] == {
        "disabled": False,
        "prefixPaddingMs": 200,
        "silenceDurationMs": 1_800,
    }
    assert setup["contextWindowCompression"] == {
        "slidingWindow": {"targetTokens": "8000"},
        "triggerTokens": "25000",
    }
    assert "sessionResumption" not in setup
    expires = datetime.fromisoformat(str(payload["expireTime"]).replace("Z", "+00:00"))
    connect_until = datetime.fromisoformat(
        str(payload["newSessionExpireTime"]).replace("Z", "+00:00")
    )
    assert 89 <= (expires - before).total_seconds() <= 91
    assert 44 <= (connect_until - after).total_seconds() <= 46


def test_live_token_expiry_covers_session_beyond_one_socket_rotation(
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> _Response:
        captured.update(kwargs)
        return _Response(200, {"name": "auth_tokens/long-session"})

    monkeypatch.setattr(httpx, "post", fake_post)
    before = datetime.now(UTC)
    mint_gemini_token(
        "secret",
        instructions="Ask one question at a time.",
        voice="Kore",
        language="de",
        patience_ms=1_400,
        session_seconds=LIVE_SESSION_MAX_MINUTES * 60,
    )

    payload = captured["json"]
    assert isinstance(payload, dict)
    expires = datetime.fromisoformat(str(payload["expireTime"]).replace("Z", "+00:00"))
    assert (
        LIVE_SESSION_MAX_MINUTES * 60 - 1
        <= (expires - before).total_seconds()
        <= LIVE_SESSION_MAX_MINUTES * 60 + 1
    )


def test_live_token_provider_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _Response(403, {}))

    with pytest.raises(VoiceProviderError, match=r"Google refused.*403"):
        mint_gemini_token(
            "secret",
            instructions="Interview safely.",
            voice="Kore",
            language="de",
            patience_ms=1_400,
        )


def test_live_token_rejects_a_malformed_provider_response(
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    class MalformedResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            raise ValueError("provider response was not JSON")

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: MalformedResponse())

    with pytest.raises(VoiceProviderError, match="invalid session token response"):
        mint_gemini_token(
            "secret",
            instructions="Interview safely.",
            voice="Kore",
            language="de",
            patience_ms=1_400,
        )


def test_live_token_rejects_a_key_without_data_processing_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "secret")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("unconfirmed Gemini egress was attempted"),
    )
    try:
        with pytest.raises(VoiceProviderError, match="not configured"):
            mint_gemini_token(
                "secret",
                instructions="Interview safely.",
                voice="Kore",
                language="de",
                patience_ms=1_400,
            )
    finally:
        get_settings.cache_clear()
