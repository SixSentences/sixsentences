"""ffmpeg-backed audio preparation for interview transcription.

One transcode normalizes any uploaded container (audio or the sound track of
a video) into a small seekable mono MP3 that browsers can play and providers
accept, then the file is cut into chunks small enough for one model call
each. ffmpeg is the only dependency and every call is a bounded subprocess.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Chunks are cut without overlap; speaker continuity across the cut comes
# from handing the tail of the previous chunk's transcript to the next call.
CHUNK_SECONDS = 600

ACCEPTED_SUFFIXES = frozenset(
    {
        ".mp3",
        ".m4a",
        ".wav",
        ".ogg",
        ".oga",
        ".opus",
        ".flac",
        ".aac",
        ".webm",
        ".mp4",
        ".mov",
        ".mkv",
    }
)


class AudioProcessingError(RuntimeError):
    """The upload could not be prepared; the message is user-safe."""


def _run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AudioProcessingError(
            "Audio processing is not configured yet: the deployment administrator "
            "needs to install ffmpeg on the server."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioProcessingError(
            "Preparing this recording took too long. Try a shorter or smaller file."
        ) from exc


def probe_duration_ms(path: Path, *, command: str = "ffprobe") -> int:
    """Read the recording length from the container header."""
    result = _run(
        [
            command,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=60.0,
    )
    duration = 0.0
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout.decode("utf-8", "replace"))
            duration = float((payload.get("format") or {}).get("duration") or 0.0)
        except (json.JSONDecodeError, TypeError, ValueError):
            duration = 0.0
    if duration <= 0:
        raise AudioProcessingError(
            "This file does not contain a readable audio track. Upload an "
            "audio recording (MP3, M4A, WAV, OGG) or a video with sound."
        )
    return round(duration * 1000)


def transcode_to_mp3(
    source: Path,
    target: Path,
    *,
    command: str = "ffmpeg",
    metadata: dict[str, str] | None = None,
) -> int:
    """Normalize any input to mono 22.05 kHz MP3 and return its byte size.

    The result is the single stored artifact: small enough for long
    interviews, seekable in every browser, and accepted by the providers.
    Optional service-owned metadata is embedded in ID3v2 without changing the
    audio pipeline. Ordinary uploads do not receive a synthetic-origin label.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            command,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-b:a",
            "48k",
            *(
                ["-id3v2_version", "4"]
                + [
                    argument
                    for key, value in sorted(metadata.items())
                    for argument in ("-metadata", f"{key}={value}")
                ]
                if metadata
                else []
            ),
            "-f",
            "mp3",
            str(target),
        ],
        timeout=900.0,
    )
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise AudioProcessingError(
            "This file could not be converted. Upload an audio recording "
            "(MP3, M4A, WAV, OGG) or a video with sound."
        )
    return target.stat().st_size


def cut_chunk(
    source: Path,
    start_seconds: int,
    length_seconds: int,
    target: Path,
    *,
    command: str = "ffmpeg",
) -> None:
    """Copy one transcription window out of the normalized MP3."""
    result = _run(
        [
            command,
            "-y",
            "-ss",
            str(start_seconds),
            "-t",
            str(length_seconds),
            "-i",
            str(source),
            "-c",
            "copy",
            "-f",
            "mp3",
            str(target),
        ],
        timeout=300.0,
    )
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise AudioProcessingError("Cutting the recording into parts failed. Try again.")


def chunk_starts(duration_ms: int) -> list[int]:
    """Start offsets (in seconds) for every transcription window."""
    total_seconds = max(1, -(-duration_ms // 1000))
    return list(range(0, total_seconds, CHUNK_SECONDS))
