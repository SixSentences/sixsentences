"""Speaker-attributed interview transcription over audio-capable chat models.

Participant audio is processed only by the confirmed paid Gemini API
(inline_data), never through a gateway or automatic provider fallback. The model
returns STRICT JSON segments with speaker labels and second-precise
timestamps relative to the chunk; offsets are added server-side so every
segment carries absolute time. Speaker continuity across chunks comes from
replaying the tail of the previous chunk's transcript into the next call.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from sixsentences_server.config import get_settings
from sixsentences_server.llm.gemini_pricing import (
    gemini_projected_cost_usd,
    gemini_usage_cost_usd,
    report_gemini_uncertain_cost,
)

_GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# One transcription window is 10 minutes of mono MP3 (~4.5 MB, ~6 MB base64),
# comfortably inside one request for both providers.
_MAX_SEGMENT_CHARS = 4_000
_CONTEXT_TURNS = 6

InterviewStageCallback = Callable[[str, str, str | None], None]
ProviderCostCallback = Callable[[str, str, float, str], None]
ProviderCostGuard = Callable[[float], None]


def _projected_transcription_cost(model: str, prompt: str = "") -> float:
    """Conservative per ten-minute chunk guard for direct audio models."""
    # Native generateContent audio uses 32 tokens/second: 19,200 per ten-minute
    # chunk, plus bounded prompt/structure overhead. The response is
    # bounded to 16k tokens below. The guard prices both at their full direct
    # Google tariff; observed native usage settles against this reserve.
    return gemini_projected_cost_usd(
        model,
        19_200 + len(prompt.encode("utf-8")) + 512,
        16_000,
    )


def _report_cost(
    payload: dict[str, Any],
    *,
    model: str,
    fallback_cost_usd: float,
    on_cost: ProviderCostCallback | None,
) -> None:
    if on_cost is None:
        return
    if model.startswith("gemini-"):
        measured = gemini_usage_cost_usd(model, payload.get("usageMetadata"))
        cost, source = measured if measured is not None else (fallback_cost_usd, "catalog")
        on_cost("interview_transcription", model, cost, source)
        return
    raw = (payload.get("usage") or {}).get("cost")
    try:
        cost = float(raw) if raw is not None else fallback_cost_usd
    except (TypeError, ValueError):
        cost = fallback_cost_usd
    on_cost(
        "interview_transcription",
        model,
        max(0.0, cost),
        "provider" if raw is not None else "catalog",
    )


# The picker's registry, mirrored by the frontend: "openrouter" is the slug
# billed there, "google" the confirmed paid Gemini API fallback. "auto" stays first and is
# the default; speaker attribution needs an audio-capable reasoning model,
# which is why the registry is Gemini-only for now.
TRANSCRIBE_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "auto",
        "label": "Auto",
        "openrouter": "google/gemini-3.5-flash",
        "google": "gemini-3.5-flash",
    },
    {
        "id": "gemini-flash",
        "label": "Gemini Flash",
        "openrouter": "google/gemini-3.5-flash",
        "google": "gemini-3.5-flash",
    },
    {
        "id": "gemini-pro",
        "label": "Gemini Pro",
        "openrouter": "google/gemini-3.1-pro-preview",
        "google": "gemini-3.1-pro-preview",
    },
)


def transcribe_model_ids() -> set[str]:
    return {entry["id"] for entry in TRANSCRIBE_MODELS}


def resolve_transcribe_model(model_id: str, provider: str) -> str:
    """Picker id -> the slug the chosen provider actually bills."""
    entry = next((m for m in TRANSCRIBE_MODELS if m["id"] == model_id), TRANSCRIBE_MODELS[0])
    if provider == "openrouter":
        return entry["openrouter"]
    return entry["google"] or TRANSCRIBE_MODELS[0]["google"]


class TranscriptionError(RuntimeError):
    """The provider failed or returned no transcript; message is user-safe."""


def interview_pipeline() -> list[dict[str, str]]:
    """Public, truthful stages for the workspace's live progress timeline."""
    return [
        {"id": "prepare", "label": "Preparing the recording", "status": "pending"},
        {
            "id": "transcribe",
            "label": "Transcribing with speaker turns",
            "status": "pending",
        },
        {"id": "structure", "label": "Structuring the transcript", "status": "pending"},
        {
            "id": "analyze",
            "label": "Analyzing themes and evidence",
            "status": "pending",
        },
    ]


def _friendly_provider_error(status: int) -> str:
    if status == 402:
        return (
            "The transcription service account has run out of balance. The "
            "workspace operator needs to top it up."
        )
    if status == 429:
        return "The transcription service has hit its usage limit. Try again in a few minutes."
    if status in (401, 403):
        return (
            "The transcription service rejected this workspace's access "
            "key. The workspace operator needs to check the configured key."
        )
    if status == 413:
        return "This recording part is too large for the transcription service."
    if status >= 500:
        return "The transcription service is having trouble right now. Try again shortly."
    return "The transcription service refused the request. Try again shortly."


def _refusal(response: httpx.Response) -> TranscriptionError:
    logging.getLogger(__name__).warning(
        "transcription provider refused the request (HTTP %s)", response.status_code
    )
    return TranscriptionError(_friendly_provider_error(response.status_code))


def _json_object(text: str) -> dict[str, Any] | None:
    """Recover one JSON object from strict or fenced model output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    candidates = [cleaned]
    first, last = cleaned.find("{"), cleaned.rfind("}")
    if first >= 0 and last > first:
        candidates.append(cleaned[first : last + 1])
    for candidate in candidates:
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
    return None


def _language_rule(language: str) -> str:
    if language == "de":
        return "The recording is in German; transcribe it verbatim in German."
    if language == "en":
        return "The recording is in English; transcribe it verbatim in English."
    return "Detect the spoken language and transcribe verbatim in that language; never translate."


def _chunk_prompt(language: str, context_tail: str) -> str:
    continuity = (
        "This audio continues an earlier part of the same recording. The "
        "previous part ended with these turns (keep the SAME speaker labels "
        f"for the same voices):\n{context_tail}\n\n"
        if context_tail
        else ""
    )
    return (
        "Transcribe this interview recording completely and verbatim.\n"
        f"{_language_rule(language)}\n"
        "Attribute every utterance to a speaker labeled S1, S2, S3... in "
        "order of first appearance; keep one label per voice for the whole "
        "recording. Start a new segment on every speaker change and "
        "whenever a monologue passes roughly 60 seconds. Transcribe what "
        "was actually said, including filler words when meaningful; mark "
        "unintelligible passages as [unclear]. Never invent, summarize or "
        "polish content.\n"
        f"{continuity}"
        'Return STRICT JSON only: {"segments":[{"speaker":"S1",'
        '"start":<seconds from the start of THIS audio, number>,'
        '"end":<seconds, number>,"text":"<verbatim utterance>"}]}.'
    )


def _post_openrouter_audio(
    audio_b64: str,
    prompt: str,
    *,
    api_key: str,
    model: str,
    timeout: float,
    before_request: ProviderCostGuard | None = None,
    on_cost: ProviderCostCallback | None = None,
) -> str:
    raise TranscriptionError("Please select an available private transcription model.")


def _post_google_audio(
    audio_b64: str,
    prompt: str,
    *,
    api_key: str,
    model: str,
    timeout: float,
    before_request: ProviderCostGuard | None = None,
    on_cost: ProviderCostCallback | None = None,
) -> str:
    configured_api_key = get_settings().gemini_egress_api_key
    if not configured_api_key or not secrets.compare_digest(api_key, configured_api_key):
        raise TranscriptionError(
            "Transcription is temporarily unavailable. Please try again later."
        )
    if model not in {entry["google"] for entry in TRANSCRIBE_MODELS}:
        raise TranscriptionError("Please select an available private transcription model.")
    body = {
        "serviceTier": "standard",
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "audio/mpeg", "data": audio_b64}},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "maxOutputTokens": 16_000,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    projected_cost = _projected_transcription_cost(model, prompt)
    if before_request is not None:
        before_request(projected_cost)
    try:
        response = httpx.post(
            _GOOGLE_ENDPOINT.format(model=model),
            headers={"x-goog-api-key": api_key},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        if not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            report_gemini_uncertain_cost(
                model,
                task="interview_transcription",
                reserved_cost_usd=projected_cost,
                on_cost=on_cost,
            )
        raise TranscriptionError(
            "The transcription service is unreachable right now; try again shortly."
        ) from exc
    if response.status_code != 200:
        if response.status_code >= 500:
            report_gemini_uncertain_cost(
                model,
                task="interview_transcription",
                reserved_cost_usd=projected_cost,
                on_cost=on_cost,
            )
        raise _refusal(response)
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Non-object provider response")
    except ValueError as exc:
        report_gemini_uncertain_cost(
            model,
            task="interview_transcription",
            reserved_cost_usd=projected_cost,
            on_cost=on_cost,
        )
        raise TranscriptionError(
            "The transcription service returned no usable result. Please try again."
        ) from exc
    _report_cost(
        payload,
        model=model,
        fallback_cost_usd=projected_cost,
        on_cost=on_cost,
    )
    for candidate in payload.get("candidates", []):
        for part in (candidate.get("content") or {}).get("parts", []):
            if part.get("text") and not part.get("thought"):
                return str(part["text"])
    raise TranscriptionError("The model returned no transcript for this part.")


def _parse_segments(raw: str, offset_ms: int) -> list[dict[str, Any]]:
    """Validate one chunk's segments and shift them to absolute time."""
    payload = _json_object(raw)
    if payload is None:
        raise TranscriptionError("The model returned an unreadable transcript part.")
    segments: list[dict[str, Any]] = []
    for item in payload.get("segments") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:_MAX_SEGMENT_CHARS]
        if not text:
            continue
        speaker = str(item.get("speaker") or "S1").strip()[:12] or "S1"
        if not re.fullmatch(r"S\d{1,2}", speaker):
            speaker = "S1"
        try:
            start = max(0.0, float(item.get("start") or 0.0))
            end = max(start, float(item.get("end") or start))
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        segments.append(
            {
                "speaker": speaker,
                "start_ms": offset_ms + round(start * 1000),
                "end_ms": offset_ms + round(end * 1000),
                "text": text,
            }
        )
    return segments


def transcribe_chunk(
    chunk_path: Path,
    *,
    api_key: str,
    model: str,
    provider: str,
    language: str,
    offset_ms: int,
    context_tail: str,
    timeout: float = 480.0,
    before_request: ProviderCostGuard | None = None,
    on_cost: ProviderCostCallback | None = None,
) -> list[dict[str, Any]]:
    """Transcribe one prepared MP3 window into absolute-time segments."""
    if provider != "google":
        raise TranscriptionError("Please select an available private transcription model.")
    audio_b64 = base64.b64encode(chunk_path.read_bytes()).decode("ascii")
    prompt = _chunk_prompt(language, context_tail)
    if provider == "openrouter":
        raw = _post_openrouter_audio(
            audio_b64,
            prompt,
            api_key=api_key,
            model=model,
            timeout=timeout,
            before_request=before_request,
            on_cost=on_cost,
        )
    else:
        raw = _post_google_audio(
            audio_b64,
            prompt,
            api_key=api_key,
            model=model,
            timeout=timeout,
            before_request=before_request,
            on_cost=on_cost,
        )
    return _parse_segments(raw, offset_ms)


def context_tail(segments: list[dict[str, Any]]) -> str:
    """The last turns of a chunk, replayed to the next call for continuity."""
    tail = segments[-_CONTEXT_TURNS:]
    return "\n".join(f"{segment['speaker']}: {str(segment['text'])[:280]}" for segment in tail)


def merge_segments(chunks: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Join per-chunk segments into one ordered, numbered transcript.

    Adjacent same-speaker fragments are merged when the model split a
    continuing utterance at a chunk boundary (gap under two seconds).
    """
    ordered: list[dict[str, Any]] = []
    for chunk in chunks:
        for segment in chunk:
            previous = ordered[-1] if ordered else None
            if (
                previous is not None
                and previous["speaker"] == segment["speaker"]
                and 0 <= segment["start_ms"] - previous["end_ms"] < 2_000
                and len(previous["text"]) + len(segment["text"]) < _MAX_SEGMENT_CHARS
            ):
                previous["text"] = f"{previous['text']} {segment['text']}".strip()
                previous["end_ms"] = max(previous["end_ms"], segment["end_ms"])
                continue
            ordered.append(dict(segment))
    for index, segment in enumerate(ordered, start=1):
        segment["idx"] = index
    return ordered


def default_speaker_names(segments: list[dict[str, Any]]) -> dict[str, str]:
    """Editable display names, seeded with the model's stable labels."""
    labels = sorted(
        {str(segment["speaker"]) for segment in segments},
        key=lambda label: int(label[1:]) if label[1:].isdigit() else 99,
    )
    return {label: label for label in labels}


def format_timestamp(ms: int) -> str:
    total_seconds = max(0, ms // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
