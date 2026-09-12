"""Domain models (Pydantic v2).

These are the wire/domain shapes; persistence lives in core.db. Every model that
crosses a pipeline stage is deliberately explicit about provenance so the audit
trail (run_events) can reconstruct any number in a report.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_PROTOCOL_APPROVAL = "awaiting_protocol_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageName(StrEnum):
    PROTOCOL_SYNTHESIS = "protocol_synthesis"
    QUERY_COMPILATION = "query_compilation"
    RETRIEVAL = "retrieval"
    WEB_SEARCH = "web_search"
    INTEGRITY = "integrity"
    SCREENING_TITLE_ABSTRACT = "screening_title_abstract"
    SNOWBALL = "snowball"
    RANKING = "ranking"
    ACQUISITION = "acquisition"
    SCREENING_FULL_TEXT = "screening_full_text"
    REPORT = "report"


class ReviewProtocol(BaseModel):
    """Formal, versioned review protocol (the contract for a run)."""

    question: str
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    languages: list[str] = Field(default_factory=lambda: ["en"])
    peer_reviewed_only: bool = False  # exclude preprints + non-article types
    query_string: str
    # A comparison mentioned in the research question is a synthesis subgroup
    # by default. It becomes a hard eligibility requirement only when the
    # methodologist explicitly marks it as such.
    comparison_required: bool = False
    comparison_note: str = ""
    eligibility_dimensions: list[str] = Field(default_factory=list)
    synthesized_by: str = "heuristic"  # "heuristic" | model id
    version: int = 1


class WorkRecord(BaseModel):
    """Canonical work as stored in the corpus (one row per OpenAlex work)."""

    id: str  # OpenAlex id, e.g. W2741809807
    doi: str | None = None
    title: str
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    authors: list[str] = Field(default_factory=list)
    cited_by_count: int = 0
    is_retracted: bool = False
    referenced_works: list[str] = Field(default_factory=list)  # OpenAlex ids this work cites
    work_type: str | None = None  # OpenAlex type: article | review | preprint | dataset | ...
    source: str = "openalex"
    # Open-access location (from OpenAlex/Unpaywall) — drives full-text
    # acquisition (H1). A closed work carries oa_status="closed" and no urls,
    # which is exactly what makes it honestly un-acquirable (principle 7).
    oa_status: str | None = None  # gold | green | hybrid | bronze | diamond | closed
    oa_url: str | None = None  # best OA landing/read URL
    pdf_url: str | None = None  # best OA direct PDF URL (across all OA locations)
    oa_landing_url: str | None = None  # OA landing page — resolve its citation_pdf_url
    oa_license: str | None = None  # e.g. cc-by, cc0, publisher-specific
    oa_version: str | None = None  # submittedVersion | acceptedVersion | publishedVersion
    arxiv_id: str | None = None  # parsed from an arXiv location, if any
    pmid: str | None = None  # PubMed id exposed by OpenAlex's ids map
    pmcid: str | None = None  # PubMed Central id, if any (Europe-PMC OA XML)
    publication_date: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    language: str | None = None
    issn: str | None = None
    # Every OA location returned by OpenAlex, not just ``best_oa_location``.
    # Acquisition walks these alternatives when the preferred publisher or
    # repository link is stale, blocked or points at the wrong document.
    oa_locations: list[dict[str, str | None]] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    """Outcome of the retrieval stage with full provenance."""

    corpus_version: str
    query_verbatim: str
    records_returned: int
    unique_works: int
    duplicates_removed: int
    works: list[WorkRecord]


class Verdict(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNSURE = "unsure"


class ScreeningDecision(BaseModel):
    work_id: str
    reviewer: str  # model id or "human:<user>" or "stub"
    verdict: Verdict
    reason: str
    quote: str | None = None
    confidence: float | None = None
    created_at: datetime = Field(default_factory=utcnow)


class PrismaCounts(BaseModel):
    """PRISMA 2020 flow counters (identification -> screening -> included).

    Every number must be reconstructable from run_events; this model is the
    denormalized view used by reports and the API.
    """

    records_identified: int = 0
    # PRISMA 2020 right-hand arm: papers recovered from web results
    # (identification via other methods); already part of records_identified
    other_identified: int = 0
    # identification via citation searching (snowballing); already part of
    # records_identified — PRISMA 2020 tracks it as its own stream
    citation_identified: int = 0
    duplicates_removed: int = 0
    # multiple reports of the same study (preprint + published) folded into
    # one canonical record; a subset of duplicates_removed
    companion_reports_merged: int = 0
    records_screened: int = 0
    records_excluded: int = 0
    records_unsure: int = 0
    retracted_flagged: int = 0
    included: int = 0
    # Retrieval of reports (PRISMA 2020 lower half) — populated when the
    # full-text acquisition stage runs; 0 when it is skipped.
    reports_sought_for_retrieval: int = 0
    reports_not_retrieved: int = 0
    # Full-text eligibility (PRISMA bottom) — populated when full-text screening
    # runs; studies_included is the terminal count (passed both screening passes).
    reports_assessed_for_eligibility: int = 0
    reports_excluded_fulltext: int = 0
    studies_included: int = 0


class SearchExecution(BaseModel):
    """PRISMA-S item bundle for one executed search against one source."""

    source: str  # e.g. "sixsentences-corpus" | "openalex-live"
    platform: str  # corpus version or API name — PRISMA-S "platform"
    query_verbatim: str
    limits: list[str] = Field(default_factory=list)
    date_run: datetime = Field(default_factory=utcnow)
    records_returned: int = 0
    deduplication_method: str = "doi+openalex_id exact match"
