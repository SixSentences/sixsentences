"""Faithful transcription of photographed or scanned sources.

A participant-supplied image (a phone photo of a lecturer's PDF, a
screenshot, a scanned page) becomes plain text ONCE at upload time, so
every chat model — including the text-only ones — can read, search and
quote it afterwards. The transcript is stored like extracted PDF text and
is therefore auditable: what the models later cite is exactly what the
transcriber wrote down.
"""

import base64
import contextlib
import logging
import secrets
from collections.abc import Callable
from io import BytesIO

import httpx
from PIL import Image

from sixsentences_server.config import get_settings
from sixsentences_server.llm.gemini_pricing import (
    gemini_projected_cost_usd,
    gemini_usage_cost_usd,
    report_gemini_uncertain_cost,
)

_GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GOOGLE_MODEL = "gemini-3.5-flash"
_MAX_EDGE = 2200  # plenty for print-sized type, keeps the call cheap
ProviderCostCallback = Callable[[str, str, float, str], None]
ProviderCostGuard = Callable[[float], None]

_TRANSCRIBE_PROMPT = (
    "Transcribe this image faithfully. It is usually a photographed or "
    "scanned page from an academic source. Return the full text verbatim in "
    "reading order, keeping the language of the source. Preserve headings "
    "and paragraph breaks, render tables as markdown tables, and describe "
    "non-text elements briefly in brackets, e.g. [Figure: bar chart of "
    "response times]. Mark spots you cannot read as [illegible]. Do not "
    "summarize, do not comment, do not invent or complete text that is cut "
    "off. Output the transcription only."
)


class ImageReadError(RuntimeError):
    """The image could not be transcribed; message is user-safe."""


def _normalized_jpeg(content: bytes) -> bytes:
    """Flatten and downscale the shot so the vision call stays cheap."""
    with Image.open(BytesIO(content)) as decoded:
        decoded.load()
        image: Image.Image = decoded
        if image.mode in ("RGBA", "LA", "P", "PA"):
            rgba = image.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            image = flat
        elif image.mode != "RGB":
            image = image.convert("RGB")
        longest = max(image.size)
        if longest > _MAX_EDGE:
            ratio = _MAX_EDGE / longest
            image = image.resize(
                (
                    max(1, round(image.width * ratio)),
                    max(1, round(image.height * ratio)),
                ),
                Image.Resampling.LANCZOS,
            )
        out = BytesIO()
        image.save(out, format="JPEG", quality=88)
        return out.getvalue()


def _via_openrouter(
    jpeg: bytes,
    api_key: str,
    timeout: float,
    *,
    before_request: ProviderCostGuard | None = None,
    on_cost: ProviderCostCallback | None = None,
) -> str | None:
    raise ImageReadError("Reading photos requires the private document service.")


def _via_google(
    jpeg: bytes,
    api_key: str,
    timeout: float,
    *,
    before_request: ProviderCostGuard | None = None,
    on_cost: ProviderCostCallback | None = None,
) -> str | None:
    configured_api_key = get_settings().gemini_egress_api_key
    if not configured_api_key or not secrets.compare_digest(api_key, configured_api_key):
        raise ImageReadError("Reading photos is temporarily unavailable. Please try again later.")
    body = {
        "serviceTier": "standard",
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode("ascii"),
                        }
                    },
                    {"text": _TRANSCRIBE_PROMPT},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 6000,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }
    projected_cost = gemini_projected_cost_usd(
        _GOOGLE_MODEL,
        8_000 + len(_TRANSCRIBE_PROMPT.encode("utf-8")),
        6_000,
    )
    if before_request is not None:
        before_request(projected_cost)
    try:
        response = httpx.post(
            _GOOGLE_ENDPOINT.format(model=_GOOGLE_MODEL),
            headers={"x-goog-api-key": api_key},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        if not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            report_gemini_uncertain_cost(
                _GOOGLE_MODEL,
                task="document_vision",
                reserved_cost_usd=projected_cost,
                on_cost=on_cost,
            )
        raise ImageReadError("The image reader is unreachable. Please try again shortly.") from exc
    if response.status_code != 200:
        if response.status_code >= 500:
            report_gemini_uncertain_cost(
                _GOOGLE_MODEL,
                task="document_vision",
                reserved_cost_usd=projected_cost,
                on_cost=on_cost,
            )
        logging.getLogger(__name__).warning(
            "image transcription via google failed: HTTP %s",
            response.status_code,
        )
        return None
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Non-object provider response")
    except ValueError as exc:
        report_gemini_uncertain_cost(
            _GOOGLE_MODEL,
            task="document_vision",
            reserved_cost_usd=projected_cost,
            on_cost=on_cost,
        )
        raise ImageReadError(
            "The image reader returned no usable result. Please try again."
        ) from exc
    if on_cost is not None:
        measured = gemini_usage_cost_usd(_GOOGLE_MODEL, payload.get("usageMetadata"))
        cost, source = measured if measured is not None else (projected_cost, "catalog")
        on_cost("document_vision", _GOOGLE_MODEL, cost, source)
    with contextlib.suppress(Exception):
        parts = payload["candidates"][0]["content"]["parts"]
        text = "\n".join(
            str(part.get("text") or "")
            for part in parts
            if part.get("text") and not part.get("thought")
        ).strip()
        if text:
            return text
    return None


def transcribe_image(
    content: bytes,
    *,
    timeout: float = 120.0,
    before_request: ProviderCostGuard | None = None,
    on_cost: ProviderCostCallback | None = None,
) -> str:
    """Image bytes -> faithful transcript, or a user-safe ImageReadError."""
    settings = get_settings()
    if not settings.gemini_enabled:
        raise ImageReadError(
            "Reading photos is temporarily unavailable in this workspace. Please contact support."
        )
    try:
        jpeg = _normalized_jpeg(content)
    except Exception as exc:
        raise ImageReadError("that image could not be decoded") from exc
    try:
        if settings.gemini_enabled:
            text = _via_google(
                jpeg,
                settings.gemini_egress_api_key,
                timeout,
                before_request=before_request,
                on_cost=on_cost,
            )
            if text:
                return text
    except httpx.HTTPError as exc:
        raise ImageReadError(
            "the image reader is unreachable right now; try again shortly"
        ) from exc
    raise ImageReadError("the image could not be transcribed; try a sharper photo")
