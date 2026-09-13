"""Private multimedia must never send content through OpenRouter."""

import base64
import json
from io import BytesIO
from typing import Any

import httpx
import pytest
from PIL import Image

from sixsentences_server.acquisition import vision
from sixsentences_server.config import get_settings
from sixsentences_server.figures import service as figures
from sixsentences_server.interviews import service as interviews


def _png() -> bytes:
    out = BytesIO()
    Image.new("RGB", (64, 64), "white").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def paid_google(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "synthetic-gateway-key")
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "synthetic-google-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_legacy_gateway_media_calls_fail_before_network(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("private gateway egress"))
    with pytest.raises(vision.ImageReadError):
        vision._via_openrouter(_png(), "synthetic-key", 1)
    with pytest.raises(interviews.TranscriptionError):
        interviews._post_openrouter_audio(
            "audio",
            "private context",
            api_key="synthetic-key",
            model="google/gemini-3.5-flash",
            timeout=1,
        )
    with pytest.raises(figures.FigureGenerationError):
        figures._post_openrouter(
            "private brief",
            api_key="synthetic-key",
            model="google/gemini-3-pro-image",
            timeout=1,
        )
    with pytest.raises(figures.FigureGenerationError):
        figures._post_openrouter_edit(
            "private brief",
            _png(),
            api_key="synthetic-key",
            model="google/gemini-3-pro-image",
            timeout=1,
        )
    with pytest.raises(figures.FigureGenerationError):
        figures.render_figure(
            "private brief",
            api_key="synthetic-key",
            model="google/gemini-3-pro-image",
            provider="openrouter",
        )


def test_private_ocr_uses_google_even_with_gateway_key(monkeypatch, paid_google):
    urls = []
    recorded = []

    def post(url, **kwargs):
        urls.append(url)
        assert kwargs["headers"] == {"x-goog-api-key": "synthetic-google-key"}
        return httpx.Response(
            200,
            json={
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 20,
                    "thoughtsTokenCount": 5,
                    "totalTokenCount": 125,
                },
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "synthetic hidden reasoning", "thought": True},
                                {"text": "Synthetic source text"},
                            ]
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    assert (
        vision.transcribe_image(
            _png(),
            on_cost=lambda *args: recorded.append(args),
        )
        == "Synthetic source text"
    )
    assert urls == [vision._GOOGLE_ENDPOINT.format(model="gemini-3.5-flash")]
    assert recorded[0][2] == pytest.approx((100 * 1.5 + 25 * 9) / 1e6)
    assert recorded[0][3] == "gemini_usage"


def test_private_ocr_failure_never_falls_back(monkeypatch, paid_google):
    urls = []

    def post(url, **kwargs):
        urls.append(url)
        return httpx.Response(503, text="synthetic private provider response")

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(vision.ImageReadError):
        vision.transcribe_image(_png())
    assert urls == [vision._GOOGLE_ENDPOINT.format(model="gemini-3.5-flash")]


def test_private_ocr_requires_paid_google_even_when_gateway_available(monkeypatch, paid_google):
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("unapproved egress"))
    with pytest.raises(vision.ImageReadError):
        vision.transcribe_image(_png())


def test_checked_native_figure_executes_all_stages_and_accounts_every_call(
    monkeypatch,
    paid_google,
):
    requests: list[tuple[str, dict[str, Any]]] = []
    costs = []
    stages = []
    encoded = base64.b64encode(_png()).decode("ascii")

    def post(url, **kwargs):
        body = kwargs["json"]
        assert body["serviceTier"] == "standard"
        requests.append((url, body))
        generation = body["generationConfig"]
        usage = {
            "serviceTier": "standard",
            "promptTokenCount": 100,
            "candidatesTokenCount": 100,
            "thoughtsTokenCount": 5,
            "totalTokenCount": 205,
        }
        if "IMAGE" in generation.get("responseModalities", []):
            assert generation["imageConfig"] == {"imageSize": "1K", "aspectRatio": "4:3"}
            usage.update(candidatesTokenCount=1120, totalTokenCount=1225)
            parts = [{"inlineData": {"mimeType": "image/png", "data": encoded}}]
        elif "description" in generation["responseJsonSchema"]["properties"]:
            parts = [{"text": json.dumps({"description": "Synthetic planned visual. " * 8})}]
        else:
            assert any("inlineData" in p for p in body["contents"][0]["parts"])
            parts = [
                {
                    "text": json.dumps(
                        {
                            "needs_revision": True,
                            "issues": ["Enlarge the synthetic label."],
                            "revised_description": "Synthetic corrected visual with large labels. "
                            * 5,
                        }
                    )
                }
            ]
        return httpx.Response(
            200,
            json={
                "usageMetadata": usage,
                "candidates": [{"content": {"parts": parts}}],
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    result = figures.render_figure(
        "Synthetic three-stage diagram",
        api_key="synthetic-google-key",
        model="gemini-3-pro-image",
        provider="google",
        resolution="1k",
        on_cost=lambda *args: costs.append(args),
        on_stage=lambda stage, status, detail: stages.append((stage, status)),
    )
    assert result.startswith(b"\x89PNG")
    assert len(requests) == len(costs) == 4
    assert all(url.startswith("https://generativelanguage.googleapis.com/") for url, _ in requests)
    assert [c[0] for c in costs] == [
        "figure_planning",
        "figure_render",
        "figure_review",
        "figure_render",
    ]
    assert all(c[2] > 0 for c in costs)
    assert ("refine_1", "completed") in stages
