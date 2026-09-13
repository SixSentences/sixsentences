"""Versioned AI-content provenance; never label human contributions as synthetic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

MIXED_AI_SOURCE_TYPE = "http://cv.iptc.org/newscodes/digitalsourcetype/compositeSynthetic"
PROVENANCE_VERSION = "sixsentences-ai-provenance-v1"


def interview_provenance(*, artifact: str, live: bool, analysis: bool = False) -> dict[str, Any]:
    """Describe known generated portions without inferring that source speech is AI."""

    contributions = ["human-participant" if live else "source-speech"]
    if live:
        contributions.append("ai-interviewer")
    if artifact != "audio":
        contributions.append("machine-transcription")
    if analysis:
        contributions.append("ai-assisted-analysis")
    generated = live or analysis
    return {
        "schema": PROVENANCE_VERSION,
        "generator": "SixSentences_",
        "artifact": artifact,
        "ai_generated_parts": generated,
        "contributions": contributions,
        "scope": "not-all-content-is-ai-generated",
        **({"digital_source_type": MIXED_AI_SOURCE_TYPE} if generated else {}),
        "authenticity": "unsigned-self-declaration",
    }


def provenance_text(payload: dict[str, Any]) -> str:
    """Serialize a stable, parser-readable marker without customer identifiers."""

    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def audio_provenance_sidecar(path: Path) -> Path:
    """Return the sibling provenance filename for one stored recording."""

    return path.with_suffix(path.suffix + ".provenance.json")


def live_audio_metadata() -> dict[str, str]:
    """Embed a public mixed-origin declaration, never participant identifiers."""

    payload = interview_provenance(artifact="audio", live=True)
    return {
        "comment": provenance_text(payload),
        "DigitalSourceType": MIXED_AI_SOURCE_TYPE,
        "encoded_by": "SixSentences_",
    }


def write_live_audio_provenance(path: Path) -> None:
    """Write provenance for newly stored mixed live audio, leaving samples intact."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    payload = interview_provenance(artifact="audio", live=True)
    payload["media_sha256"] = digest.hexdigest()
    payload["media_type"] = "audio/mpeg"
    payload["binding"] = "sha256-of-original-normalized-media-bytes"
    audio_provenance_sidecar(path).write_text(provenance_text(payload) + "\n", encoding="utf-8")
