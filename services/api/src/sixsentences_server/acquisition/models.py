"""Acquisition domain models.

The load-bearing idea is `LegalBasis`: every candidate and every stored document
records *why we are allowed to have it*. The resolver only ever emits the
open-access bases; the reserved bases name the later acquisition arms from the
concept (TDM crawler, publisher agreements, the client-fetch fleet) so the data
model is faithful to the full plan while the built path stays open-access-only.
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field


class LegalBasis(StrEnum):
    # Emitted by the OA resolver today:
    OA_GOLD = "oa_gold"  # published in a fully-OA venue (typically CC-BY)
    OA_HYBRID = "oa_hybrid"  # OA article inside a subscription venue
    OA_BRONZE = "oa_bronze"  # free-to-read on the publisher site, license unclear
    OA_GREEN = "oa_green"  # author/repository copy (arXiv, PMC, institutional)
    OA_DIAMOND = "oa_diamond"  # no-fee OA venue
    # Reserved for later acquisition arms (named in the concept, not yet built):
    TDM = "tdm"  # §44b UrhG / DSM Art. 4 mining of lawful content (H2)
    PUBLISHER_AGREEMENT = "publisher_agreement"  # negotiated TDM contract (H2)
    USER_AUTHENTICATED = "user_authenticated"  # client-fetch fleet, tenant-scoped (H1+)
    USER_UPLOAD = "user_upload"  # the user supplied the file


# The only bases the resolver is allowed to produce — enforced by test.
OA_BASES = frozenset(
    {
        LegalBasis.OA_GOLD,
        LegalBasis.OA_HYBRID,
        LegalBasis.OA_BRONZE,
        LegalBasis.OA_GREEN,
        LegalBasis.OA_DIAMOND,
    }
)


class DocumentSource(StrEnum):
    ARXIV = "arxiv"
    PMC = "pmc"  # PubMed Central / Europe-PMC OA (full-text JATS XML)
    UNPAYWALL = "unpaywall"  # OpenAlex/Unpaywall best OA location (publisher or repo)
    REPOSITORY = "repository"
    UPLOAD = "upload"


class TextStatus(StrEnum):
    PARSED = "parsed"  # full text extracted
    STORED_UNPARSED = "stored_unparsed"  # bytes stored, no extractor (e.g. PDF, backend off)
    EMPTY = "empty"  # fetched but no text found
    UNSUPPORTED = "unsupported"  # a content type we do not handle
    NOT_RETRIEVED = "not_retrieved"  # nothing fetched


class AcquisitionStatus(StrEnum):
    RETRIEVED = "retrieved"
    NOT_RETRIEVED = "not_retrieved"


class AcquisitionCandidate(BaseModel):
    """One legally-groundable place to download a work's full text."""

    url: str
    source: DocumentSource
    legal_basis: LegalBasis
    license: str | None = None
    version: str | None = None  # submittedVersion | acceptedVersion | publishedVersion
    content_hint: str | None = None  # "pdf" | "xml" — steers the extractor


class AcquisitionPlan(BaseModel):
    work_id: str
    candidates: list[AcquisitionCandidate] = Field(default_factory=list)
    reason_if_empty: str | None = None  # why nothing can be legally acquired


@dataclass
class FetchedBlob:
    """Transient: raw bytes off the wire (never persisted as-is)."""

    content: bytes
    content_type: str
    final_url: str

    @property
    def byte_size(self) -> int:
        return len(self.content)


@dataclass
class ExtractedText:
    text: str
    status: TextStatus


class AcquisitionResult(BaseModel):
    """Outcome for one work — the row written to the acquisition ledger."""

    work_id: str
    status: AcquisitionStatus
    source: DocumentSource | None = None
    legal_basis: LegalBasis | None = None
    license: str | None = None
    version: str | None = None
    url: str | None = None
    content_type: str | None = None
    checksum: str | None = None  # sha256 of the stored bytes
    byte_size: int = 0
    storage_path: str | None = None
    text_status: TextStatus = TextStatus.NOT_RETRIEVED
    reason: str | None = None  # populated when not retrieved
    note: str | None = None  # provenance note, e.g. arXiv title-match fallback


class AcquisitionSummary(BaseModel):
    """Aggregate over a run's acquisition stage — feeds PRISMA + methods."""

    sought: int = 0
    retrieved: int = 0
    not_retrieved: int = 0
    parsed: int = 0
    stored_unparsed: int = 0
    fallback_found: int = 0  # retrieved via the arXiv title-match fallback
    by_legal_basis: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
