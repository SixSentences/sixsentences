"""Stable JSON views that never expose internal keys or credential digests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sixsentences_server import models


def timestamp(value: datetime | None) -> str | None:
    """Render timestamps consistently for SQLite and PostgreSQL rows."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def project(row: models.Project) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "name": row.name,
        "description": row.description,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }


def library_item(row: models.LibraryItem) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "project_id": row.project_id,
        "kind": row.kind,
        "title": row.title,
        "abstract": row.abstract,
        "content": row.content,
        "identifiers": row.identifiers,
        "provenance": row.provenance,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }


def research_run(row: models.ResearchRun) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "project_id": row.project_id,
        "question": row.question,
        "query": row.query,
        "status": row.status,
        "result": row.result,
        "error": row.error,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
        "completed_at": timestamp(row.completed_at),
    }


def dataset(row: models.Dataset, *, include_rows: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": row.public_id,
        "project_id": row.project_id,
        "name": row.name,
        "format": row.source_format,
        "profile": row.profile,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }
    if include_rows:
        value["rows"] = row.rows
    return value


def figure(row: models.Figure) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "dataset_id": row.dataset_id,
        "title": row.title,
        "kind": row.kind,
        "specification": row.specification,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }


def survey(row: models.Survey, *, private: bool = True) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": row.public_id,
        "title": row.title,
        "status": row.status,
        "questions": row.questions,
        "participant_information": row.participant_information,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }
    if not private:
        value.pop("created_at", None)
        value.pop("updated_at", None)
    return value


def study(row: models.InterviewStudy, *, private: bool = True) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": row.public_id,
        "project_id": row.project_id,
        "title": row.title,
        "language": row.language,
        "status": row.status,
        "modalities": row.modalities,
        "guide": row.guide,
        "participant_information": row.participant_information,
        "scope_version": row.scope_version,
        "scope_fingerprint": row.scope_fingerprint,
        "max_session_minutes": row.max_session_minutes,
        "max_turns": row.max_turns,
        "retention_days": row.retention_days,
        "store_audio": row.store_audio,
        "provider_disclosure": row.provider_disclosure,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }
    if not private:
        for key in ("project_id", "guide", "created_at", "updated_at", "status"):
            value.pop(key, None)
    return value


def interview_session(row: models.InterviewSession) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "study_id": row.study_id,
        "modality": row.modality,
        "status": row.status,
        "scope_fingerprint": row.scope_fingerprint,
        "consent_snapshot": row.consent_snapshot,
        "started_at": timestamp(row.started_at),
        "completed_at": timestamp(row.completed_at),
        "duration_seconds": row.duration_seconds,
        "turn_count": row.turn_count,
    }


def interview_turn(row: models.InterviewTurn) -> dict[str, Any]:
    return {
        "sequence": row.sequence,
        "role": row.role,
        "content": row.content,
        "audio_metadata": row.audio_metadata,
        "created_at": timestamp(row.created_at),
    }


def writer(row: models.WriterDocument) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "project_id": row.project_id,
        "title": row.title,
        "content": row.content,
        "citations": row.citations,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }


def knowledge(row: models.KnowledgePage) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "project_id": row.project_id,
        "title": row.title,
        "body": row.body,
        "revision": row.revision,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }


def brainstorm(row: models.Brainstorm) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "project_id": row.project_id,
        "prompt": row.prompt,
        "status": row.status,
        "result": row.result,
        "created_at": timestamp(row.created_at),
        "updated_at": timestamp(row.updated_at),
    }
