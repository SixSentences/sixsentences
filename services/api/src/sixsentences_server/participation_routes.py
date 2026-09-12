"""Public surveys and scope-bound text or voice research interviews."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sixsentences_server.dependencies import (
    Principal,
    get_principal,
    get_session,
    tenant_query,
    tenant_record,
)
from sixsentences_server.models import (
    InterviewSession,
    InterviewStudy,
    InterviewTurn,
    Project,
    Survey,
    SurveyResponse,
)
from sixsentences_server.participant_policy import (
    PARTICIPANT_SCOPE_VERSION,
    information_gaps,
    public_notice,
    scope_fingerprint,
)
from sixsentences_server.providers import ProviderUnavailable
from sixsentences_server.schemas import (
    PublicAudioTurnRequest,
    PublicSessionRequest,
    PublicSurveyResponse,
    PublicTurnRequest,
    StudyPatch,
    StudyRequest,
    SurveyPatch,
    SurveyRequest,
)
from sixsentences_server.security import random_token, token_hash
from sixsentences_server.serialization import (
    interview_session,
    interview_turn,
    study,
    survey,
    timestamp,
)

router = APIRouter(tags=["participation"])


def _project_id(session: Session, project_id: str | None, principal: Principal) -> int | None:
    if project_id is None:
        return None
    row = tenant_record(session, Project, project_id, principal)
    return row.id


def _survey_publication(
    row: Survey,
    *,
    publish: bool,
) -> str | None:
    if not publish:
        row.status = "draft"
        row.public_token_hash = ""
        return None
    gaps = information_gaps(row.participant_information, ["text"])
    if gaps:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "publication gaps: " + ", ".join(gaps),
        )
    raw = random_token("sixc_survey")
    row.public_token_hash = token_hash(raw)
    row.status = "published"
    return raw


@router.post("/surveys", status_code=status.HTTP_201_CREATED)
def create_survey(
    payload: SurveyRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = Survey(
        org_id=principal.org_id,
        title=payload.title,
        questions=payload.questions,
        participant_information=payload.participant_information,
    )
    session.add(row)
    session.flush()
    return survey(row)


@router.get("/surveys")
def list_surveys(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(tenant_query(Survey, principal).order_by(Survey.created_at.desc()))
    return [survey(row) for row in rows]


@router.get("/surveys/{record_id}")
def get_survey(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = tenant_record(session, Survey, record_id, principal)
    value = survey(row)
    value["responses"] = session.scalar(
        select(func.count(SurveyResponse.id)).where(SurveyResponse.survey_id == row.id)
    )
    return value


@router.patch("/surveys/{record_id}")
def update_survey(
    record_id: str,
    payload: SurveyPatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = tenant_record(session, Survey, record_id, principal)
    material_change = False
    if payload.title is not None:
        row.title = payload.title
        material_change = True
    if payload.questions is not None:
        row.questions = payload.questions
        material_change = True
    if payload.participant_information is not None:
        row.participant_information = payload.participant_information
        material_change = True
    if material_change:
        row.status = "draft"
        row.public_token_hash = ""
    raw = None
    if payload.published is not None:
        raw = _survey_publication(row, publish=payload.published)
    session.flush()
    value = survey(row)
    if raw is not None:
        value["public_token"] = raw
        value["public_path"] = f"/public/surveys/{raw}"
    return value


@router.delete("/surveys/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_survey(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, Survey, record_id, principal))


def _public_survey(session: Session, token: str) -> Survey:
    row = session.scalar(
        select(Survey).where(
            Survey.public_token_hash == token_hash(token),
            Survey.status == "published",
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "survey not found")
    return row


@router.get("/public/surveys/{token}")
def public_survey(token: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return survey(_public_survey(session, token), private=False)


@router.post("/public/surveys/{token}/responses", status_code=status.HTTP_201_CREATED)
def submit_survey(
    token: str,
    payload: PublicSurveyResponse,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = _public_survey(session, token)
    if not payload.accepted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "participation not accepted")
    expected = {
        str(question.get("id"))
        for question in row.questions
        if isinstance(question, dict) and question.get("id") is not None
    }
    unexpected = set(payload.answers) - expected
    if unexpected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "answers contain unknown question identifiers",
        )
    response = SurveyResponse(
        org_id=row.org_id,
        survey_id=row.id,
        answers=payload.answers,
        consent_snapshot={
            "accepted": True,
            "accepted_at": datetime.now(UTC).isoformat(),
            "participant_information": row.participant_information,
        },
    )
    session.add(response)
    session.flush()
    return {"id": response.public_id, "submitted_at": timestamp(response.submitted_at)}


def _study_scope(row: InterviewStudy) -> str:
    return scope_fingerprint(
        title=row.title,
        language=row.language,
        modalities=list(row.modalities),
        guide=dict(row.guide),
        information=dict(row.participant_information),
        max_session_minutes=row.max_session_minutes,
        max_turns=row.max_turns,
        retention_days=row.retention_days,
        provider_disclosure=row.provider_disclosure,
    )


def _publish_study(row: InterviewStudy, request: Request, publish: bool) -> str | None:
    if not publish:
        row.status = "draft"
        row.public_token_hash = ""
        row.scope_fingerprint = ""
        return None
    modalities = list(dict.fromkeys(row.modalities))
    if not modalities or any(item not in {"text", "voice"} for item in modalities):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid interview modalities")
    gaps = list(information_gaps(row.participant_information, modalities))
    if "voice" in modalities:
        if request.app.state.settings.voice_provider == "disabled":
            gaps.append("configured server-side voice provider")
        if len(row.provider_disclosure.strip()) < 20:
            gaps.append("participant-facing voice processor disclosure")
        if row.store_audio:
            gaps.append("transcript-only storage")
    if gaps:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "publication gaps: " + ", ".join(gaps),
        )
    row.modalities = modalities
    row.scope_version = PARTICIPANT_SCOPE_VERSION
    row.scope_fingerprint = _study_scope(row)
    raw = random_token("sixc_interview")
    row.public_token_hash = token_hash(raw)
    row.status = "published"
    return raw


@router.post("/interview-studies", status_code=status.HTTP_201_CREATED)
@router.post("/voice/studies", status_code=status.HTTP_201_CREATED)
def create_study(
    payload: StudyRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    modalities = list(dict.fromkeys(payload.modalities))
    row = InterviewStudy(
        org_id=principal.org_id,
        project_id=_project_id(session, payload.project_id, principal),
        title=payload.title,
        language=payload.language,
        modalities=modalities,
        guide=payload.guide,
        participant_information=payload.participant_information.model_dump(mode="json"),
        max_session_minutes=payload.max_session_minutes,
        max_turns=payload.max_turns,
        retention_days=payload.retention_days,
        store_audio=False,
        provider_disclosure=payload.provider_disclosure,
    )
    session.add(row)
    session.flush()
    return study(row)


@router.get("/interview-studies")
@router.get("/voice/studies")
def list_studies(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        tenant_query(InterviewStudy, principal).order_by(InterviewStudy.created_at.desc())
    )
    return [study(row) for row in rows]


@router.get("/interview-studies/{record_id}")
@router.get("/voice/studies/{record_id}")
def get_study(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return study(tenant_record(session, InterviewStudy, record_id, principal))


@router.patch("/interview-studies/{record_id}")
@router.patch("/voice/studies/{record_id}")
def update_study(
    record_id: str,
    payload: StudyPatch,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = tenant_record(session, InterviewStudy, record_id, principal)
    updates = payload.model_dump(exclude_none=True)
    publish = updates.pop("published", None)
    material_change = bool(updates)
    for field, value in updates.items():
        if field == "participant_information":
            assert isinstance(value, dict)
        setattr(row, field, value)
    if material_change:
        row.status = "draft"
        row.public_token_hash = ""
        row.scope_fingerprint = ""
    raw = _publish_study(row, request, publish) if publish is not None else None
    session.flush()
    value = study(row)
    if raw is not None:
        value["public_token"] = raw
        value["public_path"] = f"/public/interviews/{raw}"
    return value


@router.delete("/interview-studies/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/voice/studies/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_study(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, InterviewStudy, record_id, principal))


def _public_study(session: Session, token: str) -> InterviewStudy:
    row = session.scalar(
        select(InterviewStudy).where(
            InterviewStudy.public_token_hash == token_hash(token),
            InterviewStudy.status == "published",
        )
    )
    if row is None or not row.scope_fingerprint or row.scope_fingerprint != _study_scope(row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interview not found")
    return row


@router.get("/public/interviews/{token}")
@router.get("/public/talk/{token}")
def public_study(
    token: str,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = _public_study(session, token)
    value = study(row, private=False)
    modality = "voice" if request.url.path.startswith("/public/talk/") else "text"
    if modality not in row.modalities:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interview modality not found")
    value["participant_notice"] = public_notice(
        row.participant_information,
        modality=modality,
        scope=row.scope_fingerprint,
        provider_disclosure=row.provider_disclosure,
    )
    return value


@router.post("/public/interviews/{token}/sessions", status_code=status.HTTP_201_CREATED)
@router.post("/public/talk/{token}/sessions", status_code=status.HTTP_201_CREATED)
def create_public_session(
    token: str,
    payload: PublicSessionRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = _public_study(session, token)
    if payload.modality not in row.modalities:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "modality is not published")
    if not payload.accepted or (payload.modality == "voice" and not payload.adult_confirmed):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "participation not accepted")
    if payload.scope_fingerprint != row.scope_fingerprint:
        raise HTTPException(status.HTTP_409_CONFLICT, "study scope changed; review it again")
    active = session.scalar(
        select(func.count(InterviewSession.id)).where(
            InterviewSession.study_id == row.id,
            InterviewSession.status == "active",
        )
    )
    if int(active or 0) >= request.app.state.settings.public_session_limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "study session capacity reached")
    notice = public_notice(
        row.participant_information,
        modality=payload.modality,
        scope=row.scope_fingerprint,
        provider_disclosure=row.provider_disclosure,
    )
    notice.update(
        {
            "accepted": True,
            "adult_confirmed": payload.adult_confirmed,
            "accepted_at": datetime.now(UTC).isoformat(),
        }
    )
    record = InterviewSession(
        org_id=row.org_id,
        study_id=row.id,
        modality=payload.modality,
        scope_fingerprint=row.scope_fingerprint,
        consent_snapshot=notice,
    )
    session.add(record)
    session.flush()
    value = interview_session(record)
    value["study_id"] = row.public_id
    return value


def _public_session(
    session: Session,
    study: InterviewStudy,
    session_id: str,
    *,
    modality: str | None = None,
) -> InterviewSession:
    record = session.scalar(
        select(InterviewSession).where(
            InterviewSession.public_id == session_id,
            InterviewSession.study_id == study.id,
            InterviewSession.org_id == study.org_id,
        )
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    if modality is not None and record.modality != modality:
        raise HTTPException(status.HTTP_409_CONFLICT, "session modality does not match")
    return record


def _ensure_active(study: InterviewStudy, record: InterviewSession) -> None:
    if record.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "session is not active")
    if record.scope_fingerprint != study.scope_fingerprint:
        raise HTTPException(status.HTTP_409_CONFLICT, "study scope changed")
    started = record.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    elapsed = (datetime.now(UTC) - started).total_seconds()
    if elapsed > study.max_session_minutes * 60:
        record.status = "expired"
        record.duration_seconds = elapsed
        record.completed_at = datetime.now(UTC)
        raise HTTPException(status.HTTP_410_GONE, "session duration limit reached")
    if record.turn_count + 2 > study.max_turns:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "session turn limit reached")


def _next_prompt(study: InterviewStudy, record: InterviewSession) -> str:
    questions = study.guide.get("questions") if isinstance(study.guide, dict) else None
    if isinstance(questions, list):
        index = min(record.turn_count // 2, len(questions) - 1)
        question = questions[index] if index >= 0 else None
        if isinstance(question, str) and question.strip():
            return question[:4000]
    return "Could you say more about what matters most in that experience?"


def _append_public_turn(
    request: Request,
    session: Session,
    study_row: InterviewStudy,
    session_row: InterviewSession,
    participant_text: str,
    *,
    audio_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_active(study_row, session_row)
    participant = InterviewTurn(
        org_id=study_row.org_id,
        session_id=session_row.id,
        sequence=session_row.turn_count,
        role="participant",
        content=participant_text,
        audio_metadata=audio_metadata or {},
    )
    session.add(participant)
    session_row.turn_count += 1
    previous = list(
        session.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == session_row.id)
            .order_by(InterviewTurn.sequence.desc())
            .limit(30)
        )
    )
    previous.reverse()
    try:
        reply = request.app.state.text_provider.complete(
            system=(
                "Conduct a voluntary qualitative research interview using the supplied guide. "
                "Disclose that you are AI, ask one neutral question at a time, allow stopping, "
                "do not diagnose, identify, score or infer sensitive personal traits, and do not "
                "make decisions about the participant."
            ),
            messages=[
                {"role": turn.role, "content": turn.content}
                for turn in previous
                if turn.role in {"participant", "assistant"}
            ]
            + [{"role": "participant", "content": participant_text}],
        )
    except (ProviderUnavailable, ValueError):
        reply = _next_prompt(study_row, session_row)
    assistant = InterviewTurn(
        org_id=study_row.org_id,
        session_id=session_row.id,
        sequence=session_row.turn_count,
        role="assistant",
        content=reply,
        audio_metadata={},
    )
    session.add(assistant)
    session_row.turn_count += 1
    session.flush()
    return {
        "participant": interview_turn(participant),
        "assistant": interview_turn(assistant),
        "remaining_turns": max(0, study_row.max_turns - session_row.turn_count),
    }


@router.post("/public/interviews/{token}/sessions/{session_id}/turns")
@router.post("/public/talk/{token}/sessions/{session_id}/messages")
def public_text_turn(
    token: str,
    session_id: str,
    payload: PublicTurnRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    study_row = _public_study(session, token)
    session_row = _public_session(session, study_row, session_id, modality="text")
    return _append_public_turn(request, session, study_row, session_row, payload.content)


@router.post("/public/interviews/{token}/sessions/{session_id}/audio")
def public_voice_turn(
    token: str,
    session_id: str,
    payload: PublicAudioTurnRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    study_row = _public_study(session, token)
    session_row = _public_session(session, study_row, session_id, modality="voice")
    _ensure_active(study_row, session_row)
    try:
        content = base64.b64decode(payload.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid base64 audio") from exc
    if not content or len(content) > request.app.state.settings.public_audio_chunk_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "audio chunk exceeds limit")
    try:
        transcript = request.app.state.speech_provider.transcribe(
            content=content,
            media_type=payload.media_type,
        )
    except ProviderUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return _append_public_turn(
        request,
        session,
        study_row,
        session_row,
        transcript,
        audio_metadata={
            "media_type": payload.media_type,
            "received_bytes": len(content),
            "stored": False,
        },
    )


@router.post("/public/interviews/{token}/sessions/{session_id}/complete")
@router.post("/public/talk/{token}/sessions/{session_id}/finalize")
def complete_public_session(
    token: str,
    session_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    study_row = _public_study(session, token)
    record = _public_session(session, study_row, session_id)
    _ensure_active(study_row, record)
    now = datetime.now(UTC)
    started = record.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    record.duration_seconds = max(0.0, (now - started).total_seconds())
    record.completed_at = now
    record.status = "completed"
    session.flush()
    return interview_session(record)


@router.get("/interviews")
def list_interview_sessions(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(InterviewSession)
        .where(InterviewSession.org_id == principal.org_id)
        .order_by(InterviewSession.started_at.desc())
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        value = interview_session(row)
        source = session.get(InterviewStudy, row.study_id)
        value["study_id"] = source.public_id if source is not None else None
        output.append(value)
    return output


@router.get("/interviews/{record_id}")
def get_interview_session(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = session.scalar(
        select(InterviewSession).where(
            InterviewSession.public_id == record_id,
            InterviewSession.org_id == principal.org_id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resource not found")
    turns = session.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.session_id == row.id, InterviewTurn.org_id == principal.org_id)
        .order_by(InterviewTurn.sequence)
    )
    value = interview_session(row)
    source = session.get(InterviewStudy, row.study_id)
    value["study_id"] = source.public_id if source is not None else None
    value["turns"] = [interview_turn(turn) for turn in turns]
    return value
