"""Portable domain models for the community engine.

The models deliberately contain no account, tenant, payment, provider, or
deployment state. Persistence and orchestration remain caller-owned.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EngineModel(BaseModel):
    """Base class with fail-closed input validation."""

    model_config = ConfigDict(extra="forbid", strict=True)


def utcnow() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


class ReviewProtocol(EngineModel):
    """Formal review protocol used by deterministic engine stages."""

    question: str
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    languages: list[str] = Field(default_factory=lambda: ["en"])
    peer_reviewed_only: bool = False
    query_string: str
    comparison_required: bool = False
    comparison_note: str = ""
    eligibility_dimensions: list[str] = Field(default_factory=list)
    synthesized_by: str = "human"
    version: int = 1

    @field_validator("question", "query_string")
    @classmethod
    def _require_protocol_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @model_validator(mode="after")
    def _validate_protocol(self) -> Self:
        if self.year_from is not None and not 0 <= self.year_from <= 9999:
            raise ValueError("year_from must be between 0 and 9999")
        if self.year_to is not None and not 0 <= self.year_to <= 9999:
            raise ValueError("year_to must be between 0 and 9999")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from must not be later than year_to")
        if self.version < 1:
            raise ValueError("version must be a positive integer")
        from sixsentences.querylang.parser import parse_query

        parse_query(self.query_string)
        return self


class WorkRecord(EngineModel):
    """Canonical scholarly work with OpenAlex-compatible metadata."""

    id: str
    doi: str | None = None
    title: str
    abstract: str | None = None
    year: int | None = Field(default=None, ge=0, le=9999)
    venue: str | None = None
    authors: list[str] = Field(default_factory=list)
    cited_by_count: int = Field(default=0, ge=0)
    is_retracted: bool = False
    referenced_works: list[str] = Field(default_factory=list)
    work_type: str | None = None
    source: str = "openalex"
    oa_status: str | None = None
    oa_url: str | None = None
    pdf_url: str | None = None
    oa_landing_url: str | None = None
    oa_license: str | None = None
    oa_version: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    publication_date: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    language: str | None = None
    issn: str | None = None
    oa_locations: list[dict[str, str | None]] = Field(default_factory=list)

    @field_validator("id", "title")
    @classmethod
    def _require_non_empty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped


class Verdict(StrEnum):
    """Three-way screening verdict."""

    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNSURE = "unsure"


class ScreeningDecision(EngineModel):
    """Caller-supplied decision with an optional source quote."""

    work_id: str
    reviewer: str
    verdict: Verdict
    reason: str
    quote: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator("work_id", "reviewer", "reason")
    @classmethod
    def _require_decision_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped


class PrismaCounts(EngineModel):
    """Explicit PRISMA 2020 counters supplied by a caller-owned audit log."""

    records_identified: int = Field(default=0, ge=0)
    other_identified: int = Field(default=0, ge=0)
    citation_identified: int = Field(default=0, ge=0)
    duplicates_removed: int = Field(default=0, ge=0)
    companion_reports_linked: int = Field(default=0, ge=0)
    records_screened: int = Field(default=0, ge=0)
    records_excluded: int = Field(default=0, ge=0)
    records_unsure: int = Field(default=0, ge=0)
    retracted_flagged: int = Field(default=0, ge=0)
    included: int = Field(default=0, ge=0)
    reports_sought_for_retrieval: int = Field(default=0, ge=0)
    reports_not_retrieved: int = Field(default=0, ge=0)
    reports_assessed_for_eligibility: int = Field(default=0, ge=0)
    reports_excluded_fulltext: int = Field(default=0, ge=0)
    reports_included: int = Field(default=0, ge=0)
    studies_included: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_flow_consistency(self) -> Self:
        if self.other_identified + self.citation_identified > self.records_identified:
            raise ValueError("identification substreams exceed records_identified")
        if self.duplicates_removed > self.records_identified:
            raise ValueError("duplicates_removed exceeds records_identified")
        if self.companion_reports_linked > self.records_identified:
            raise ValueError("companion_reports_linked exceeds records_identified")
        available_for_screening = self.records_identified - self.duplicates_removed
        if self.records_screened != available_for_screening:
            raise ValueError("records_screened must equal records remaining after deduplication")
        decided = self.records_excluded + self.records_unsure + self.included
        if decided != self.records_screened:
            raise ValueError("screening outcomes must equal records_screened")
        if self.retracted_flagged > self.records_identified:
            raise ValueError("retracted_flagged exceeds records_identified")
        advanced = self.included + self.records_unsure
        retrieval_started = any(
            (
                self.reports_sought_for_retrieval,
                self.reports_not_retrieved,
                self.reports_assessed_for_eligibility,
                self.reports_excluded_fulltext,
                self.reports_included,
                self.studies_included,
            )
        )
        if retrieval_started and self.reports_sought_for_retrieval != advanced:
            raise ValueError("reports_sought_for_retrieval must equal records advanced")
        if self.reports_not_retrieved > self.reports_sought_for_retrieval:
            raise ValueError("reports_not_retrieved exceeds reports_sought_for_retrieval")
        retrieved = self.reports_sought_for_retrieval - self.reports_not_retrieved
        if retrieval_started and self.reports_assessed_for_eligibility != retrieved:
            raise ValueError("reports assessed for eligibility must equal reports retrieved")
        report_outcomes = self.reports_excluded_fulltext + self.reports_included
        if retrieval_started and report_outcomes != self.reports_assessed_for_eligibility:
            raise ValueError("report outcomes must equal reports assessed for eligibility")
        if self.studies_included > self.reports_included:
            raise ValueError("studies_included exceeds reports_included")
        if self.reports_included > 0 and self.studies_included == 0:
            raise ValueError("reports_included requires at least one included study")
        return self


class SearchExecution(EngineModel):
    """PRISMA-S description of one executed search."""

    source: str
    platform: str
    query_verbatim: str
    limits: list[str] = Field(default_factory=list)
    date_run: datetime = Field(default_factory=utcnow)
    records_returned: int = Field(default=0, ge=0)
    deduplication_method: str = "not reported"

    @field_validator("source", "platform", "query_verbatim", "deduplication_method")
    @classmethod
    def _require_search_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped
