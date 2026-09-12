"""Generated export metadata is parseable and never rewrites source speech."""

import hashlib
import json
import shutil
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sixsentences_server.api.app import _interview_audio_path, _public_report_payload, create_app
from sixsentences_server.core.db import InterviewRow, db_session
from sixsentences_server.core.media_provenance import (
    audio_provenance_sidecar,
    interview_provenance,
    live_audio_metadata,
    write_live_audio_provenance,
)
from sixsentences_server.interviews.audio import transcode_to_mp3
from sixsentences_server.interviews.report import render_report_tex


def test_standalone_live_mp3_retains_mixed_origin_without_sidecar(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real MP3 metadata verification requires ffmpeg and ffprobe")
    source = tmp_path / "synthetic-silence.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b"\x00\x00" * 22050)
    marked = tmp_path / "mixed.mp3"
    ordinary = tmp_path / "source.mp3"
    transcode_to_mp3(source, marked, metadata=live_audio_metadata())
    transcode_to_mp3(source, ordinary)

    def tags(path: Path) -> dict[str, str]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return json.loads(result.stdout)["format"].get("tags", {})

    embedded = tags(marked)
    payload = json.loads(embedded["comment"])
    assert payload == interview_provenance(artifact="audio", live=True)
    assert embedded["DigitalSourceType"].endswith("/compositeSynthetic")
    assert payload["contributions"] == ["human-participant", "ai-interviewer"]
    assert not audio_provenance_sidecar(marked).exists()
    assert "DigitalSourceType" not in tags(ordinary)
    assert "comment" not in tags(ordinary)

    # Metadata must not change the normalized audio samples.
    def samples(path: Path) -> bytes:
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-"],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout

    assert samples(marked) == samples(ordinary)


def test_mixed_audio_sidecar_binds_original_bytes_without_modifying_samples(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-mixed.mp3"
    original = b"ID3-synthetic-audio-fixture-not-a-customer-recording"
    path.write_bytes(original)
    write_live_audio_provenance(path)
    assert path.read_bytes() == original
    metadata = json.loads(audio_provenance_sidecar(path).read_text())
    assert metadata["media_sha256"] == hashlib.sha256(original).hexdigest()
    assert metadata["ai_generated_parts"] is True
    assert metadata["scope"] == "not-all-content-is-ai-generated"
    assert metadata["contributions"] == ["human-participant", "ai-interviewer"]
    assert metadata["digital_source_type"].endswith("/compositeSynthetic")
    assert metadata["authenticity"] == "unsigned-self-declaration"


def test_report_tex_has_parser_readable_provenance_and_pdf_metadata() -> None:
    tex = render_report_tex(
        title="Synthetic report",
        language="en",
        duration_ms=10000,
        speakers={"S1": "Synthetic participant"},
        model="private-route-not-exported",
        created_at=datetime.now(UTC),
        generated_at=datetime.now(UTC),
        segments=[],
        analysis={"summary": "Synthetic analysis."},
        live=True,
    )
    marker = next(line[2:] for line in tex.splitlines() if line.startswith('% {"'))
    payload = json.loads(marker)
    assert payload["ai_generated_parts"] is True
    assert "human-participant" in payload["contributions"]
    assert "ai-interviewer" in payload["contributions"]
    assert "pdfkeywords={sixsentences-ai-provenance-v1" in tex
    assert "private-route-not-exported" not in tex


def test_public_report_exposes_generation_fact_without_private_route() -> None:
    assert _public_report_payload({"model": "private:model"}) == {"ai_generated_sections": True}
    assert _public_report_payload({"model": ""}) == {"ai_generated_sections": False}
    assert interview_provenance(artifact="transcript", live=False)["ai_generated_parts"] is False


def test_live_audio_sidecar_download_is_scoped_and_deleted_with_recording(corpus) -> None:
    clients = []
    for label in ("owner", "other"):
        client = TestClient(create_app())
        registered = client.post(
            "/auth/register",
            json={
                "email": f"{label}@example.org",
                "password": "StrongPass123!",
                "org_name": f"Synthetic provenance {label}",
                "name": label,
            },
        )
        assert registered.status_code == 201, registered.text
        client.headers["Authorization"] = f"Bearer {registered.json()['token']}"
        clients.append(client)
    owner, other = clients
    with db_session() as session:
        row = InterviewRow(
            org_id=owner.get("/auth/me").json()["org_id"],
            kind="live",
            title="Synthetic mixed recording",
            status="ready",
            model="text",
            audio_removed=False,
            duration_ms=10000,
            byte_size=20,
        )
        session.add(row)
        session.flush()
        public_id, row_id = row.public_id, row.id
    path = _interview_audio_path(row_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ID3-synthetic-audio")
    write_live_audio_provenance(path)
    audio = owner.get(f"/interviews/{public_id}/audio")
    assert audio.status_code == 200
    assert 'rel="describedby"' in audio.headers["link"]
    sidecar = owner.get(f"/interviews/{public_id}/audio/provenance")
    assert sidecar.status_code == 200
    assert sidecar.json()["media_sha256"] == hashlib.sha256(audio.content).hexdigest()
    assert other.get(f"/interviews/{public_id}/audio/provenance").status_code == 404
    assert owner.delete(f"/interviews/{public_id}/audio").status_code == 200
    assert not audio_provenance_sidecar(path).exists()
    assert owner.get(f"/interviews/{public_id}/audio/provenance").status_code == 404
