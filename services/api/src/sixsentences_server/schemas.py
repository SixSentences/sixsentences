"""Validated request contracts for the community HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Reject unreviewed request fields instead of silently dropping them."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    organization: str = Field(min_length=1, max_length=240)
    locale: Literal["de", "en"] = "en"


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class ApiKeyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectRequest(StrictModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=5000)


class ProjectPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=5000)


class LibraryItemRequest(StrictModel):
    project_id: str | None = None
    kind: Literal["paper", "web", "note"] = "paper"
    title: str = Field(min_length=1, max_length=1000)
    abstract: str = Field(default="", max_length=200_000)
    content: str = Field(default="", max_length=2_000_000)
    identifiers: dict[str, str] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class RunRequest(StrictModel):
    project_id: str | None = None
    question: str = Field(min_length=3, max_length=10_000)
    query: str = Field(min_length=1, max_length=20_000)
    limit: int = Field(default=100, ge=1, le=1000)
    year_from: int | None = Field(default=None, ge=0, le=9999)
    year_to: int | None = Field(default=None, ge=0, le=9999)


class DatasetRequest(StrictModel):
    project_id: str | None = None
    name: str = Field(min_length=1, max_length=240)
    filename: str = Field(min_length=1, max_length=240)
    content_base64: str = Field(min_length=1)


class FigureRequest(StrictModel):
    dataset_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    kind: Literal["bar", "line", "scatter", "histogram", "forest", "funnel"]
    specification: dict[str, Any] = Field(default_factory=dict)


class SurveyRequest(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    questions: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    participant_information: dict[str, Any] = Field(default_factory=dict)


class SurveyPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    questions: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    participant_information: dict[str, Any] | None = None
    published: bool | None = None


class PublicSurveyResponse(StrictModel):
    answers: dict[str, Any]
    accepted: bool


class ParticipantInformation(StrictModel):
    language: Literal["de", "en"] = "en"
    controller_name: str = Field(min_length=3, max_length=240)
    controller_address: str = Field(min_length=3, max_length=500)
    contact_email: str = Field(min_length=3, max_length=320)
    data_protection_contact: str = Field(min_length=3, max_length=500)
    purpose: str = Field(min_length=3, max_length=2000)
    data_categories: str = Field(min_length=3, max_length=1500)
    legal_basis: Literal["consent", "public_task", "legitimate_interests"]
    legal_basis_details: str = Field(min_length=3, max_length=1500)
    retention_period: str = Field(min_length=3, max_length=1500)
    recipients: str = Field(min_length=3, max_length=1500)
    international_transfers: str = Field(min_length=3, max_length=1500)
    supervisory_authority: str = Field(min_length=3, max_length=1000)
    privacy_notice_url: str = Field(default="", max_length=2000)
    impact_assessment_completed: bool
    impact_assessment_reference: str = Field(min_length=20, max_length=1500)
    prohibited_use_attested: bool
    spoken_processing_approved: bool = False
    researcher_reviewed: bool


def _default_modalities() -> list[Literal["text", "voice"]]:
    return ["text"]


class StudyRequest(StrictModel):
    project_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    language: Literal["de", "en"] = "en"
    modalities: list[Literal["text", "voice"]] = Field(default_factory=_default_modalities)
    guide: dict[str, Any] = Field(default_factory=dict)
    participant_information: ParticipantInformation
    max_session_minutes: int = Field(default=30, ge=1, le=180)
    max_turns: int = Field(default=120, ge=1, le=720)
    retention_days: int = Field(default=30, ge=1, le=3650)
    store_audio: Literal[False] = False
    provider_disclosure: str = Field(default="", max_length=1500)


class StudyPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    guide: dict[str, Any] | None = None
    participant_information: ParticipantInformation | None = None
    modalities: list[Literal["text", "voice"]] | None = None
    max_session_minutes: int | None = Field(default=None, ge=1, le=180)
    max_turns: int | None = Field(default=None, ge=1, le=720)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    provider_disclosure: str | None = Field(default=None, max_length=1500)
    published: bool | None = None


class PublicSessionRequest(StrictModel):
    modality: Literal["text", "voice"]
    accepted: bool
    adult_confirmed: bool
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicTurnRequest(StrictModel):
    content: str = Field(min_length=1, max_length=65_536)


class PublicAudioTurnRequest(StrictModel):
    audio_base64: str = Field(min_length=1)
    media_type: Literal["audio/wav", "audio/webm", "audio/ogg", "audio/mpeg"]


class WriterRequest(StrictModel):
    project_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=2_000_000)
    citations: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)


class WriterPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=2_000_000)
    citations: list[dict[str, Any]] | None = Field(default=None, max_length=10_000)


class KnowledgeRequest(StrictModel):
    project_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default="", max_length=2_000_000)


class KnowledgePatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    body: str | None = Field(default=None, max_length=2_000_000)
    expected_revision: int = Field(ge=1)


class BrainstormRequest(StrictModel):
    project_id: str | None = None
    prompt: str = Field(min_length=3, max_length=20_000)


class MailRequest(StrictModel):
    recipient: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=240)
    text_body: str = Field(min_length=1, max_length=100_000)
