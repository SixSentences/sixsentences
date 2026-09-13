"""Behavioral checks for fixed-cost multimedia provider guards."""

import base64
from collections.abc import Iterator
from io import BytesIO
from typing import Any

import httpx
import pytest
from PIL import Image

from sixsentences_server.acquisition.vision import _TRANSCRIBE_PROMPT, ImageReadError, _via_google
from sixsentences_server.config import get_settings
from sixsentences_server.figures.service import (
    FigureGenerationError,
    _post_google,
    _post_google_chat,
    resolve_figure_model,
)
from sixsentences_server.interviews.service import (
    TranscriptionError,
    _post_google_audio,
    _projected_transcription_cost,
    resolve_transcribe_model,
)
from sixsentences_server.llm.base import openrouter_price_cap_of, price_of
from sixsentences_server.llm.gemini_pricing import gemini_projected_cost_usd, gemini_usage_cost_usd


@pytest.fixture()
def confirmed_direct_gemini(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_google_vision_guard_covers_the_full_output_cap(
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    seen: dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        seen.update(kwargs)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "transcript"}]}}]},
        )

    monkeypatch.setattr("sixsentences_server.acquisition.vision.httpx.post", fake_post)
    projected: list[float] = []
    recorded: list[float] = []

    result = _via_google(
        b"jpeg",
        "key",
        30.0,
        before_request=projected.append,
        on_cost=lambda _task, _model, cost, _source: recorded.append(cost),
    )

    assert result == "transcript"
    expected = gemini_projected_cost_usd(
        "gemini-3.5-flash",
        8_000 + len(_TRANSCRIBE_PROMPT.encode("utf-8")),
        6_000,
    )
    assert projected == [expected]
    assert recorded == [expected]
    assert seen["json"]["serviceTier"] == "standard"
    assert seen["json"]["generationConfig"]["maxOutputTokens"] == 6_000


@pytest.mark.parametrize(
    ("model", "expected"),
    [("gemini-3.5-flash", 0.1728), ("gemini-3.1-pro-preview", 0.2304)],
)
def test_transcription_guard_prices_the_bounded_ten_minute_request(
    model: str,
    expected: float,
) -> None:
    assert _projected_transcription_cost(model) >= expected
    assert _projected_transcription_cost(model, "你好") > _projected_transcription_cost(model)


def test_google_transcription_request_enforces_the_costed_output_cap(
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    seen: dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        seen.update(kwargs)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
        )

    monkeypatch.setattr("sixsentences_server.interviews.service.httpx.post", fake_post)
    projected: list[float] = []

    assert (
        _post_google_audio(
            "audio",
            "prompt",
            api_key="key",
            model="gemini-3.1-pro-preview",
            timeout=30.0,
            before_request=projected.append,
        )
        == "{}"
    )
    assert projected == [_projected_transcription_cost("gemini-3.1-pro-preview", "prompt")]
    assert seen["json"]["serviceTier"] == "standard"
    assert seen["json"]["generationConfig"]["maxOutputTokens"] == 16_000


def test_direct_google_multimedia_egress_rejects_a_key_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("unconfirmed Gemini egress was attempted"),
    )
    try:
        with pytest.raises(ImageReadError, match="temporarily unavailable"):
            _via_google(b"jpeg", "key", 30.0)
        with pytest.raises(TranscriptionError, match="temporarily unavailable"):
            _post_google_audio(
                "audio",
                "prompt",
                api_key="key",
                model="gemini-3.1-pro-preview",
                timeout=30.0,
            )
        with pytest.raises(FigureGenerationError, match="temporarily unavailable"):
            _post_google(
                "prompt",
                api_key="key",
                model="gemini-3-pro-image",
                timeout=30.0,
            )
    finally:
        get_settings.cache_clear()


def test_multimedia_registries_use_current_google_model_ids() -> None:
    assert resolve_transcribe_model("gemini-pro", "openrouter") == ("google/gemini-3.1-pro-preview")
    assert resolve_transcribe_model("gemini-pro", "google") == "gemini-3.1-pro-preview"
    assert resolve_figure_model("auto", "google", "gemini-3-pro-image") == ("gemini-3-pro-image")


def test_specialized_openrouter_catalog_prices_have_fail_closed_ten_percent_caps() -> None:
    assert openrouter_price_cap_of("google/gemini-3.5-flash") == (1.65, 9.9)
    assert openrouter_price_cap_of("google/gemini-3-pro-image") == (2.2, 13.2)
    assert openrouter_price_cap_of("google/gemini-3.1-flash-image") == (0.55, 3.3)
    assert openrouter_price_cap_of("google/gemini-3.1-pro-preview") == (4.4, 19.8)


def test_gemini_pro_transcription_catalog_applies_the_published_long_context_tier() -> None:
    model = "google/gemini-3.1-pro-preview"
    assert price_of(model, 199_999) == (2.0, 12.0)
    assert price_of(model, 200_000) == (4.0, 18.0)


@pytest.mark.parametrize("surface", ["vision", "audio"])
def test_native_multimedia_settles_observed_thinking_and_cache_not_full_reserve(
    surface: str,
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    metadata = {
        "serviceTier": "standard",
        "promptTokenCount": 1_000,
        "cachedContentTokenCount": 500,
        "candidatesTokenCount": 200,
        "thoughtsTokenCount": 100,
        "totalTokenCount": 1_300,
    }
    model = "gemini-3.5-flash"
    payload = {
        "usageMetadata": metadata,
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "private thought must not escape", "thought": True},
                        {"text": "{}"},
                    ]
                }
            }
        ],
    }
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: httpx.Response(200, json=payload))
    projected: list[float] = []
    recorded: list[tuple[float, str]] = []
    kwargs = {
        "before_request": projected.append,
        "on_cost": lambda _task, _model, cost, source: recorded.append((cost, source)),
    }
    if surface == "vision":
        result = _via_google(b"jpeg", "key", 30.0, **kwargs)
    else:
        result = _post_google_audio(
            "audio",
            "prompt",
            api_key="key",
            model=model,
            timeout=30.0,
            **kwargs,
        )
    assert result == "{}"
    assert recorded == [gemini_usage_cost_usd(model, metadata)]
    assert recorded[0][0] == pytest.approx(0.003525)
    assert recorded[0][0] < projected[0]


@pytest.mark.parametrize("surface", ["vision", "audio"])
def test_native_multimedia_invalid_zero_usage_retains_nonzero_reserve(
    surface: str,
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    payload = {
        "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0},
        "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
    }
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: httpx.Response(200, json=payload))
    projected: list[float] = []
    recorded: list[tuple[float, str]] = []
    kwargs = {
        "before_request": projected.append,
        "on_cost": lambda _task, _model, cost, source: recorded.append((cost, source)),
    }
    if surface == "vision":
        _via_google(b"jpeg", "key", 30.0, **kwargs)
    else:
        _post_google_audio(
            "audio",
            "prompt",
            api_key="key",
            model="gemini-3.5-flash",
            timeout=30.0,
            **kwargs,
        )
    assert recorded == [(projected[0], "catalog")]
    assert recorded[0][0] > 0


@pytest.mark.parametrize(
    "model,image_tokens,image_rate",
    [
        ("gemini-3-pro-image", 1_120, 120.0),
        ("gemini-3.1-flash-image", 1_680, 60.0),
    ],
)
def test_native_image_output_cost_includes_all_images_and_thinking(
    model: str,
    image_tokens: int,
    image_rate: float,
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(buffer, format="PNG")
    encoded_image = base64.b64encode(buffer.getvalue()).decode("ascii")
    inline = {"inlineData": {"mimeType": "image/png", "data": encoded_image}}
    metadata = {
        "serviceTier": "standard",
        "promptTokenCount": 100,
        "candidatesTokenCount": image_tokens * 2 + 100,
        "thoughtsTokenCount": 50,
        "candidatesTokensDetails": [
            {"modality": "IMAGE", "tokenCount": image_tokens * 2},
            {"modality": "TEXT", "tokenCount": 100},
        ],
    }
    payload = {
        "usageMetadata": metadata,
        "candidates": [{"content": {"parts": [inline, {"text": "done"}, inline]}}],
    }
    seen: dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        seen.update(kwargs)
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx, "post", fake_post)
    projected: list[float] = []
    recorded: list[tuple[float, str]] = []
    image = _post_google(
        "draw a chart",
        api_key="key",
        model=model,
        timeout=30.0,
        resolution="2k",
        before_request=projected.append,
        on_cost=lambda _task, _model, cost, source: recorded.append((cost, source)),
    )
    assert image.startswith(b"\x89PNG")
    assert seen["json"]["serviceTier"] == "standard"
    assert seen["json"]["generationConfig"]["imageConfig"]["imageSize"] == "2K"
    expected = gemini_usage_cost_usd(model, metadata, output_image_count=2, image_size="2K")
    assert recorded == [expected]
    assert recorded[0][0] >= image_tokens * 2 * image_rate / 1e6
    assert projected[0] >= recorded[0][0]


def _invoke_native_surface(surface: str, **kwargs: Any) -> object:
    if surface == "vision":
        return _via_google(b"jpeg", "key", 30.0, **kwargs)
    if surface == "audio":
        return _post_google_audio(
            "audio",
            "prompt",
            api_key="key",
            model="gemini-3.5-flash",
            timeout=30.0,
            **kwargs,
        )
    if surface == "planner":
        return _post_google_chat(
            [{"role": "user", "content": "draw a chart"}],
            api_key="key",
            model="gemini-3.5-flash",
            max_tokens=1_000,
            timeout=30.0,
            **kwargs,
        )
    return _post_google(
        "draw a chart",
        api_key="key",
        model="gemini-3-pro-image",
        timeout=30.0,
        **kwargs,
    )


@pytest.mark.parametrize("surface", ["vision", "audio", "planner", "image"])
@pytest.mark.parametrize(
    "failure",
    [
        "read_timeout",
        "write_error",
        "server_error",
        "bad_json",
        "bad_shape",
    ],
)
def test_unknown_post_egress_outcome_is_recorded_once_as_uncertain_reserve(
    surface: str,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    calls: list[object] = []

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        calls.append(args)
        if failure == "read_timeout":
            raise httpx.ReadTimeout("raw provider detail must stay private")
        if failure == "write_error":
            raise httpx.WriteError("raw provider detail must stay private")
        if failure == "server_error":
            return httpx.Response(503, text="raw provider detail must stay private")
        if failure == "bad_shape":
            return httpx.Response(200, json=[])
        return httpx.Response(200, text="raw provider detail must stay private")

    monkeypatch.setattr(httpx, "post", fake_post)
    projected: list[float] = []
    recorded: list[tuple[float, str]] = []
    try:
        result = _invoke_native_surface(
            surface,
            before_request=projected.append,
            on_cost=lambda _task, _model, cost, source: recorded.append((cost, source)),
        )
        assert result is None  # OCR 5xx preserves its public no-result contract.
    except (ImageReadError, TranscriptionError, FigureGenerationError) as exc:
        assert "raw provider detail" not in str(exc)
    assert len(calls) == 1
    assert recorded == [(projected[0], "uncertain")]
    assert recorded[0][0] > 0


@pytest.mark.parametrize("surface", ["vision", "audio", "planner", "image"])
@pytest.mark.parametrize(
    "failure",
    [
        "connect_timeout",
        "connect_error",
        "pool_timeout",
        "rejected",
    ],
)
def test_pre_dispatch_and_explicit_rejections_do_not_charge_uncertain_spend(
    surface: str,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        errors = {
            "connect_timeout": httpx.ConnectTimeout,
            "connect_error": httpx.ConnectError,
            "pool_timeout": httpx.PoolTimeout,
        }
        if failure in errors:
            raise errors[failure]("not dispatched")
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    recorded: list[float] = []
    try:
        result = _invoke_native_surface(
            surface,
            on_cost=lambda _task, _model, cost, _source: recorded.append(cost),
        )
        assert result is None
    except (ImageReadError, TranscriptionError, FigureGenerationError):
        pass
    assert recorded == []


@pytest.mark.parametrize("surface", ["vision", "audio", "planner", "image"])
def test_native_media_budget_refusal_never_dispatches_or_charges(
    surface: str,
    monkeypatch: pytest.MonkeyPatch,
    confirmed_direct_gemini: None,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: pytest.fail("Egress after refusal"),
    )
    recorded: list[float] = []

    def deny(_projected: float) -> None:
        raise RuntimeError("synthetic budget refusal")

    with pytest.raises(RuntimeError, match="synthetic budget refusal"):
        _invoke_native_surface(
            surface,
            before_request=deny,
            on_cost=lambda _task, _model, cost, _source: recorded.append(cost),
        )
    assert recorded == []
