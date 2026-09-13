"""SQLAlchemy ORM tables and session helpers.

Tenancy: every row that belongs to a tenant carries org_id from day one, even
though the skeleton runs single-tenant with a lazily created "default" org.
run_events is append-only by convention: nothing in the codebase updates or
deletes rows there.
"""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    false,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from sixsentences_server.config import get_settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


# runs are addressed publicly (URLs, the topbar) by an opaque slug, never by
# the auto-increment key: sequential numbers read unprofessional and leak how
# many searches exist. Lowercase base32 without look-alikes (l/1, o/0).
_PUBLIC_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_public_id(length: int = 10) -> str:
    return "".join(secrets.choice(_PUBLIC_ID_ALPHABET) for _ in range(length))


def new_capability_token() -> str:
    """Return a bearer-link token with 130 bits of cryptographic entropy."""
    return new_public_id(26)


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    plan: Mapped[str] = mapped_column(String(20), default="community")
    # Stable local anchor for recurring operator-defined usage windows.
    capacity_cycle_anchor: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RateLimitBucket(Base):
    """One short-lived, pseudonymized abuse-control counter.

    The composite primary key makes increments atomic across API replicas.
    Raw IP addresses and credential identifiers are never persisted.
    """

    __tablename__ = "rate_limit_buckets"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_id: Mapped[int] = mapped_column(primary_key=True, index=True)
    count: Mapped[int] = mapped_column(default=1)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # Stable Google account identifier (`sub`). Email is deliberately not used
    # as the external identity because it can change.
    google_sub: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    first_name: Mapped[str] = mapped_column(String(80), default="")  # greeting name
    language: Mapped[str] = mapped_column(String(5), default="en")
    assistant_preferences: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="member")  # owner | member
    is_active: Mapped[bool] = mapped_column(default=True)
    # Existing and operator-provisioned accounts are verified immediately.
    # Public self-signup explicitly writes NULL until its one-time token is spent.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # when the user finished (or skipped) the first-run intro; NULL = not yet,
    # so the welcome overlay shows once and never again
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # First successful login is retained for account security and onboarding.
    first_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    age_requirement_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Public signup records the exact legal texts the user saw. Privacy is an
    # acknowledgement, while optional marketing remains separate consent.
    terms_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    privacy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    privacy_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dpa_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dpa_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    marketing_consent: Mapped[bool] = mapped_column(default=False)
    marketing_consent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    marketing_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # TOTP secret is Fernet-encrypted with the deployment credential key.
    # Recovery codes are random high-entropy values and only their SHA-256
    # hashes are retained. ``enabled_at`` distinguishes a pending setup from
    # an active second factor.
    two_factor_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    two_factor_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    two_factor_recovery_hashes: Mapped[list[str]] = mapped_column(JSON, default=list)
    two_factor_last_counter: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UserLegalEventRow(Base):
    """Version-bound declarations and notices; never overwrite earlier events.

    No IP address, credentials or duplicate account contact data is collected.
    These account records are erased with the account.
    """

    __tablename__ = "user_legal_events"
    __table_args__ = (
        Index(
            "ix_user_legal_events_workspace_document",
            "org_id",
            "document_id",
            "version",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_kind: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), default="en")
    declaration: Mapped[str] = mapped_column(Text)
    document_content: Mapped[str] = mapped_column(Text, default="")
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    controller_name: Mapped[str] = mapped_column(String(240), default="")
    actor_role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuthToken(Base):
    """Opaque auth artifact. Only the SHA-256 hash is stored."""

    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # session, API key, single-purpose flow token, or one-use stream ticket
    kind: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(24))  # non-secret display prefix
    name: Mapped[str] = mapped_column(String(200), default="")
    # API keys are deny-by-default and carry only explicitly granted machine
    # scopes. Session and one-purpose tokens keep an empty list.
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(40), default="research")
    phase: Mapped[str] = mapped_column(String(40), default="planning")
    status: Mapped[str] = mapped_column(String(20), default="active")
    question: Mapped[str] = mapped_column(Text, default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class KnowledgePageRow(Base):
    """One private thought page in a user's Knowledge workspace.

    Knowledge pages deliberately contain user-authored thought material only.
    A project is an optional filing reference; it does not make project data
    part of the page or widen the page to other workspace members.
    """

    __tablename__ = "knowledge_pages"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "creator_user_id",
            "client_request_id",
            name="uq_knowledge_page_create_request",
        ),
        CheckConstraint(
            "state IN ('inbox', 'developing', 'evergreen', 'archived')",
            name="ck_knowledge_page_state",
        ),
        CheckConstraint("revision >= 1", name="ck_knowledge_page_revision"),
        CheckConstraint(
            "position >= 0 AND position <= 1000000000",
            name="ck_knowledge_page_position",
        ),
        Index(
            "ix_knowledge_pages_creator_updated",
            "org_id",
            "creator_user_id",
            "updated_at",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    creator_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    client_request_id: Mapped[str] = mapped_column(String(100))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(240), default="")
    body_markdown: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(16), default="inbox", index=True)
    pinned: Mapped[bool] = mapped_column(default=False, index=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags_index: Mapped[str] = mapped_column(String(1600), default="")
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_pages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    revision: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class StudyRow(Base):
    """One underlying study, potentially represented by several reports.

    Grouping publications here prevents a preprint, journal article and
    follow-up from silently being counted as independent evidence.
    """

    __tablename__ = "studies"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    design: Mapped[str] = mapped_column(String(80), default="not_specified")
    status: Mapped[str] = mapped_column(String(24), default="candidate")
    registry_id: Mapped[str] = mapped_column(String(120), default="")
    population: Mapped[str] = mapped_column(Text, default="")
    intervention: Mapped[str] = mapped_column(Text, default="")
    comparator: Mapped[str] = mapped_column(Text, default="")
    outcomes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    report_work_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProjectTaskRow(Base):
    """A concrete next action attached to a research object."""

    __tablename__ = "project_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    assignee_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_type: Mapped[str] = mapped_column(String(40), default="project")
    linked_id: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProjectEventRow(Base):
    """Append-only research workspace activity and decision trail."""

    __tablename__ = "project_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ExtractionRow(Base):
    """One included work's structured evidence extraction for a run.

    payload: {field: {value, quote, page, verified, source}}. Human edits
    replace single fields with source="human" (the reviewer vouches for the
    value, so it counts as verified).
    """

    __tablename__ = "extractions"
    __table_args__ = (UniqueConstraint("run_id", "work_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    work_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|failed
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterDocumentRow(Base):
    """A Writer document: one LaTeX main file wired to the run(s) whose
    include sets feed its live bibliography. Snapshots are the writing
    ledger; the compiled PDF lives on disk under data_dir/writer."""

    __tablename__ = "writer_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    # opaque address for URLs; access stays session + org gated (not shareable)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), default="Untitled")
    content: Mapped[str] = mapped_column(Text, default="")  # main.tex
    run_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    # Own experimental/survey datasets available to the writing assistant.
    dataset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # the seeded example draft (tour anchor; created alongside the demo run)
    is_demo: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    derived_from_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("writer_documents.id"), nullable=True, index=True
    )
    target_template: Mapped[str] = mapped_column(String(160), default="")
    retarget_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    compile_status: Mapped[str] = mapped_column(String(16), default="none")  # none|ok|error
    compile_log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterFileRow(Base):
    """An editable text file that belongs to a Writer project.

    ``WriterDocumentRow.content`` remains the canonical ``main.tex`` for
    backwards compatibility. This table stores every additional source file
    (.tex, .bib, .sty, .cls and plain text) with its relative project path.
    """

    __tablename__ = "writer_files"
    __table_args__ = (UniqueConstraint("document_id", "path"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    path: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(default=1)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterSourceRow(Base):
    """A user-owned, citable source attached directly to a manuscript."""

    __tablename__ = "writer_sources"
    __table_args__ = (
        UniqueConstraint("document_id", "cite_key"),
        UniqueConstraint(
            "document_id",
            "source_document_id",
            name="uq_writer_sources_document_source",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(500), default="Untitled source")
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(nullable=True)
    doi: Mapped[str] = mapped_column(String(200), default="")
    cite_key: Mapped[str] = mapped_column(String(120))
    bibtex: Mapped[str] = mapped_column(Text, default="")
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterInterviewContextRow(Base):
    """An org-owned interview made available to one manuscript assistant.

    ``mode`` is deliberately explicit: analysis-only links never expose raw
    transcript turns, while transcript links allow bounded, query-relevant
    passages to be retrieved with stable segment and timestamp provenance.
    """

    __tablename__ = "writer_interview_contexts"
    __table_args__ = (UniqueConstraint("document_id", "interview_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="analysis")
    include_methodology: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterSurveyContextRow(Base):
    """A survey instrument and its results linked to one manuscript assistant.

    The instrument contract is always available. ``mode`` controls whether
    the assistant receives deterministic descriptive results only or also a
    bounded set of query-relevant, de-identified response rows.
    """

    __tablename__ = "writer_survey_contexts"
    __table_args__ = (UniqueConstraint("document_id", "survey_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("surveys.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="summary")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterSnapshotRow(Base):
    """Append-only, project-wide Writer version (compiles + manual + AI)."""

    __tablename__ = "writer_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(120), default="")
    semantic_summary: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WriterCollaboratorRow(Base):
    """A named manuscript permission override within one shared workspace."""

    __tablename__ = "writer_collaborators"
    __table_args__ = (UniqueConstraint("document_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="editor")  # editor | reviewer | viewer
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterPresenceRow(Base):
    """Ephemeral awareness heartbeat for an open manuscript editor."""

    __tablename__ = "writer_presence"
    __table_args__ = (UniqueConstraint("document_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    path: Mapped[str] = mapped_column(String(300), default="main.tex")
    line: Mapped[int] = mapped_column(default=1)
    mode: Mapped[str] = mapped_column(String(16), default="source")
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


class WriterMessageRow(Base):
    """One turn of a Writer document's chat. payload carries the assistant's
    edit proposals ({find, replace} pairs) and the PDF selection a user
    message referred to — the writing ledger's conversational half."""

    __tablename__ = "writer_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WriterAssetRow(Base):
    """An uploaded figure for a Writer document; bytes live on disk under
    data_dir/writer/{document_id}/assets and join every compile workdir."""

    __tablename__ = "writer_assets"
    __table_args__ = (UniqueConstraint("document_id", "filename"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    byte_size: Mapped[int] = mapped_column(default=0)
    # extracted text of PDF uploads, so the editing chat can read them
    text_content: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RepositoryConnectionRow(Base):
    """Creator-private, repository-bound GitHub credential.

    The raw personal access token never reaches this row. ``credential_encrypted``
    contains an authenticated ciphertext produced by the deployment credential
    key. Connections are deliberately bound to one canonical repository instead
    of acting as general-purpose GitHub accounts.
    """

    __tablename__ = "repository_connections"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "creator_user_id",
            "github_repository_id",
            name="uq_repository_connection_creator_repository",
        ),
        CheckConstraint(
            "provider = 'github'",
            name="ck_repository_connection_provider",
        ),
        CheckConstraint(
            "repository_visibility IN ('private', 'internal')",
            name="ck_repository_connection_visibility",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=lambda: new_public_id(16)
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    creator_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), default="github")
    github_repository_id: Mapped[int] = mapped_column(BigInteger)
    full_name: Mapped[str] = mapped_column(String(201))
    repository_url: Mapped[str] = mapped_column(String(500))
    owner: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    repository_visibility: Mapped[str] = mapped_column(String(12), default="private")
    credential_encrypted: Mapped[str] = mapped_column(Text)
    credential_storage_version: Mapped[str] = mapped_column(String(40))
    credential_storage_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RepositoryAnalysisRow(Base):
    """Creator-private analysis of one immutable GitHub snapshot.

    Repository archives are hostile, ephemeral input and are never persisted.
    The durable record contains only a bounded inventory, secret-screened
    evidence and the validated diagram specification derived from one commit.
    """

    __tablename__ = "repository_analyses"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "creator_user_id",
            "client_request_id",
            name="uq_repository_analysis_create_request",
        ),
        CheckConstraint(
            "status IN ('queued', 'fetching', 'analyzing', 'needs_scope', "
            "'ready', 'error', 'cancelled')",
            name="ck_repository_analysis_status",
        ),
        CheckConstraint(
            "diagram_kind IN ('architecture', 'flow', 'deployment', 'module')",
            name="ck_repository_analysis_diagram_kind",
        ),
        CheckConstraint(
            "language IN ('en', 'de')",
            name="ck_repository_analysis_language",
        ),
        CheckConstraint(
            "repository_access IN ('public', 'private')",
            name="ck_repository_analysis_access",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    creator_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    repository_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("repository_connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    repository_access: Mapped[str] = mapped_column(String(12), default="public")
    client_request_id: Mapped[str] = mapped_column(String(100))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    repository_url: Mapped[str] = mapped_column(String(500))
    owner: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    ref: Mapped[str] = mapped_column(String(200), default="")
    subpath: Mapped[str] = mapped_column(String(500), default="")
    commit_sha: Mapped[str] = mapped_column(String(40), default="")
    archive_sha256: Mapped[str] = mapped_column(String(64), default="")
    goal: Mapped[str] = mapped_column(Text)
    diagram_kind: Mapped[str] = mapped_column(String(20))
    language: Mapped[str] = mapped_column(String(8), default="en")
    rights_confirmed: Mapped[bool] = mapped_column(default=False)
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String(80), default="")
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    diagram_spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    action_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    base_credits: Mapped[int] = mapped_column(default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class FigureRow(Base):
    """A generated scientific figure. Bytes live on disk under
    data_dir/figures/{id}.png; the row is the record: prompt, grounding
    run, model and status, org-scoped and addressed by an opaque id."""

    __tablename__ = "figures"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    prompt: Mapped[str] = mapped_column(Text)
    context_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    repository_analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("repository_analyses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|ok|error
    error: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    # Visual Lab render contract (resolution, aspect ratio, kind, review pass).
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    byte_size: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ResearchDatasetRow(Base):
    """A workspace-owned primary-research dataset with machine-readable profile.

    Raw bytes live under data_dir/datasets/{id}; profile contains the schema,
    preview, bounded records, missingness and descriptive statistics used by
    the Writer and Visual Lab without guessing at the data.
    """

    __tablename__ = "research_datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(240))
    filename: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[str] = mapped_column(Text, default="")
    license: Mapped[str] = mapped_column(String(120), default="Not specified")
    format: Mapped[str] = mapped_column(String(20), default="csv")
    row_count: Mapped[int] = mapped_column(default=0)
    byte_size: Mapped[int] = mapped_column(default=0)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class DatasetVersionRow(Base):
    """Immutable profile/checksum for one uploaded dataset revision."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("research_datasets.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    filename: Mapped[str] = mapped_column(String(300))
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(default=0)
    row_count: Mapped[int] = mapped_column(default=0)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DatasetMessageRow(Base):
    """Persistent analysis conversation for one research dataset."""

    __tablename__ = "dataset_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("research_datasets.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class InterviewRow(Base):
    """One uploaded interview recording with its audited transcript.

    The normalized MP3 lives on disk under data_dir/interviews/{id}/; the row
    carries the record: duration, billed model, editable speaker names, the
    verified analysis and the live pipeline state, org-scoped and addressed
    by an opaque id. byte_size is the stored audio (0 once removed) so the
    workspace storage meter stays honest.
    """

    __tablename__ = "interviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(240))
    # upload = a recording brought in; live = conducted by the voice agent
    kind: Mapped[str] = mapped_column(String(16), default="upload")
    language: Mapped[str] = mapped_column(String(8), default="auto")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|ready|error
    error: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    guide: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(default=0)
    byte_size: Mapped[int] = mapped_column(default=0)
    audio_removed: Mapped[bool] = mapped_column(default=False)
    # editable display names per stable model label, e.g. {"S1": "Interviewer"}
    speakers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # pipeline stages, active_stage, report revision counter
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class InterviewSegmentRow(Base):
    """One speaker turn of a transcript; idx is the citable anchor."""

    __tablename__ = "interview_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"), index=True)
    idx: Mapped[int] = mapped_column(default=0)
    speaker: Mapped[str] = mapped_column(String(12), default="S1")
    start_ms: Mapped[int] = mapped_column(default=0)
    end_ms: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    edited: Mapped[bool] = mapped_column(default=False)


class LiveCompanionSessionRow(Base):
    """A creator-private text-only companion capture session."""

    __tablename__ = "live_companion_sessions"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "client_session_id",
            name="uq_live_companion_client_session",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    interview_id: Mapped[int | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="SET NULL"), index=True, nullable=True
    )
    client_session_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300), default="Live conversation")
    # Frozen at creation. Brainstorm sessions are solo, microphone-only and
    # must never materialize into the organisation-visible Interview tables.
    purpose: Mapped[str] = mapped_column(String(20), default="conversation", index=True)
    language: Mapped[str] = mapped_column(String(8), default="auto")
    status: Mapped[str] = mapped_column(String(20), default="recording", index=True)
    consent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    max_duration_ms: Mapped[int] = mapped_column(default=240 * 60_000)
    revision: Mapped[int] = mapped_column(default=0)
    last_event_sequence: Mapped[int] = mapped_column(default=0)
    last_segment_end_ms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class LiveCompanionSegmentRow(Base):
    """An immutable client event containing one final transcript segment."""

    __tablename__ = "live_companion_segments"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "client_event_id",
            name="uq_live_companion_segment_event",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_companion_sessions.id", ondelete="CASCADE"), index=True
    )
    client_event_id: Mapped[str] = mapped_column(String(100))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(index=True)
    channel: Mapped[str] = mapped_column(String(16))
    speaker: Mapped[str] = mapped_column(String(160), default="Speaker")
    start_ms: Mapped[int] = mapped_column(default=0)
    end_ms: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    is_final: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class LiveCompanionAskRow(Base):
    """One idempotent live answer or structured brainstorm receipt."""

    __tablename__ = "live_companion_asks"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "client_request_id",
            name="uq_live_companion_ask_request",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_companion_sessions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="ask", index=True)
    client_request_id: Mapped[str] = mapped_column(String(100))
    question: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    # Immutable request snapshot. The worker may run after more transcript
    # text arrives, so it must never infer context from the session head.
    context_through_sequence: Mapped[int] = mapped_column(default=0, index=True)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    action_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    base_credits: Mapped[int] = mapped_column(default=0)
    settlement_charged: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    answer: Mapped[str] = mapped_column(Text)
    # Brainstorm output stays typed JSON instead of being smuggled through the
    # free-text answer field used by ordinary Live Ask.
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default="")
    error_code: Mapped[str] = mapped_column(String(80), default="")
    transcript_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    project_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sequence: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class BrainstormProjectDocumentRow(Base):
    """The creator-private, revisioned synthesis for one research project."""

    __tablename__ = "brainstorm_project_documents"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "project_id",
            name="uq_brainstorm_project_document_owner",
        ),
        CheckConstraint("revision >= 0", name="ck_brainstorm_project_document_revision"),
        CheckConstraint(
            "manual_revision >= 0",
            name="ck_brainstorm_project_document_manual_revision",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(default=0)
    manual_markdown: Mapped[str] = mapped_column(Text, default="")
    manual_revision: Mapped[int] = mapped_column(default=0)
    source_set_stale: Mapped[bool] = mapped_column(default=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_sessions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class BrainstormProjectSynthesisRow(Base):
    """One frozen, idempotent attempt to update a project brainstorm document."""

    __tablename__ = "brainstorm_project_syntheses"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "client_request_id",
            name="uq_brainstorm_project_synthesis_request",
        ),
        CheckConstraint("base_revision >= 0", name="ck_brainstorm_synthesis_revision"),
        Index(
            "uq_brainstorm_project_synthesis_active",
            "org_id",
            "user_id",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("brainstorm_project_documents.id", ondelete="CASCADE"), index=True
    )
    retry_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("brainstorm_project_syntheses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    client_request_id: Mapped[str] = mapped_column(String(100))
    request_sha256: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="selected")
    output_language: Mapped[str] = mapped_column(String(8), default="en")
    base_revision: Mapped[int] = mapped_column(default=0)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    action_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    base_credits: Mapped[int] = mapped_column(default=0)
    settlement_charged: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default="")
    error_code: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class BrainstormProjectSynthesisSourceRow(Base):
    """Normalized immutable source edge for one project synthesis receipt."""

    __tablename__ = "brainstorm_project_synthesis_sources"
    __table_args__ = (
        UniqueConstraint(
            "synthesis_id",
            "session_id",
            name="uq_brainstorm_project_synthesis_source",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    synthesis_id: Mapped[int] = mapped_column(
        ForeignKey("brainstorm_project_syntheses.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_companion_sessions.id", ondelete="RESTRICT"), index=True
    )
    brainstorm_id: Mapped[int] = mapped_column(
        ForeignKey("live_companion_asks.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BrainstormProjectDocumentSourceRow(Base):
    """Normalized current-source edge protecting a grounded project document."""

    __tablename__ = "brainstorm_project_document_sources"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "session_id",
            name="uq_brainstorm_project_document_source",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("brainstorm_project_documents.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_companion_sessions.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LiveCompanionEventRow(Base):
    """Append-only cursor log for low-cost web polling."""

    __tablename__ = "live_companion_events"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_live_companion_event_sequence",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("live_companion_sessions.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class VoiceStudyRow(Base):
    """One live-interview study: the versioned guide and the agent persona.

    The research hypothesis deliberately has no field here: the interviewer
    prompt is assembled ONLY from what this row carries, so the agent cannot
    steer toward hoped-for answers (the bias firewall from the strategy).
    """

    __tablename__ = "voice_studies"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(240))
    language: Mapped[str] = mapped_column(String(8), default="de")
    voice: Mapped[str] = mapped_column(String(40), default="Kore")
    tone: Mapped[str] = mapped_column(String(16), default="warm")
    # guided = work through the guide's topics; iterative = one opening
    # question, every follow-up emerges from the participant's answers
    mode: Mapped[str] = mapped_column(String(16), default="guided")
    patience_ms: Mapped[int] = mapped_column(default=1400)
    # API-created studies set the plan-specific 30/60-minute launch default
    # explicitly. Keep this fallback at the minimum useful AI-interview window
    # for rows created outside the API.
    max_session_minutes: Mapped[int] = mapped_column(default=30)
    retention: Mapped[str] = mapped_column(
        String(20), default="transcript_only"
    )  # keep|transcript_only
    # participant-facing: the researcher's consent paragraph and closing line
    consent_text: Mapped[str] = mapped_column(Text, default="")
    contact_line: Mapped[str] = mapped_column(String(240), default="")
    participant_information: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # total conversation budget across all field sessions of this study
    budget_minutes: Mapped[int] = mapped_column(default=120)
    # {"context": str, "sections": [{title, question, probes[], must_cover}]}
    guide: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    guide_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class VoiceSessionRow(Base):
    """One live conversation with the interviewer agent.

    The fully assembled prompt is stored per session, so every interview is
    reviewable and reproducible end to end. Completed sessions materialize
    as a normal InterviewRow (kind="live").
    """

    __tablename__ = "voice_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=new_capability_token
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("voice_studies.id"), index=True)
    invite_id: Mapped[int | None] = mapped_column(ForeignKey("voice_invites.id"), nullable=True)
    interview_id: Mapped[int | None] = mapped_column(ForeignKey("interviews.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="pilot")
    status: Mapped[str] = mapped_column(
        String(16), default="running"
    )  # running|settling(internal)|completed|aborted|error
    participant_label: Mapped[str] = mapped_column(String(120), default="Pilot")
    model: Mapped[str] = mapped_column(String(120), default="")
    guide_version: Mapped[int] = mapped_column(default=1)
    prompt: Mapped[str] = mapped_column(Text, default="")
    # Server-authorized wall-clock lifetime captured when the capacity action
    # begins. This snapshot remains authoritative after plan or study edits.
    authorized_duration_ms: Mapped[int] = mapped_column(default=0)
    # A durable claim is written before each text-model request. It bounds
    # concurrent participant turns even while the provider call is in flight.
    text_turns_claimed: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(default=0)
    cost_units: Mapped[int] = mapped_column(default=0)
    # Number of browser credentials issued. New sessions issue exactly one;
    # the persisted count is retained for historical provider reconciliation.
    provider_token_issues: Mapped[int] = mapped_column(default=0)
    # New spoken sessions use one server-owned relay. Legacy direct tokens
    # retain their conservative settlement and are never upgraded in place.
    transport: Mapped[str] = mapped_column(String(24), default="legacy")
    relay_ticket_hash: Mapped[str] = mapped_column(String(64), default="")
    relay_ticket_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    relay_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    relay_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bounded server-written setup snapshot, aggregate meter and final provider
    # transcript. No API key, relay bearer ticket, raw audio or resume handle.
    relay_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Legacy sessions may have only a hash. Never invent missing historic copy.
    consent_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    consent_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VoiceStudyMessageRow(Base):
    """Persistent design conversation for one voice study."""

    __tablename__ = "voice_study_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("voice_studies.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class VoiceInviteRow(Base):
    """A public participation link for one study.

    The public_id IS the /talk URL token. Optional recruitment passcodes are
    stored as one-way PBKDF2 hashes and are never returned after creation.
    """

    __tablename__ = "voice_invites"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=new_capability_token
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("voice_studies.id"), index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    passcode: Mapped[str] = mapped_column(String(120), default="")
    max_sessions: Mapped[int] = mapped_column(default=25)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class InterviewMessageRow(Base):
    """Persistent grounded conversation for one interview."""

    __tablename__ = "interview_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RunShareRow(Base):
    """A public read-only link to a finished run's auditable record."""

    __tablename__ = "run_shares"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    token: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=new_capability_token
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WriterShareRow(Base):
    """A public, optionally password-protected review link to a manuscript."""

    __tablename__ = "writer_shares"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    token: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=new_capability_token
    )
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WriterCommentRow(Base):
    """A manuscript comment with an optional resilient PDF text anchor.

    ``quote`` plus its short prefix/suffix form a TextQuote-style selector:
    page is only a navigation hint, so comments survive ordinary LaTeX
    reflow when paragraphs move to another page.
    """

    __tablename__ = "writer_comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    share_id: Mapped[int | None] = mapped_column(
        ForeignKey("writer_shares.id"), index=True, nullable=True
    )
    author_label: Mapped[str] = mapped_column(String(120), default="")
    author_key: Mapped[str] = mapped_column(String(120), default="")
    color_index: Mapped[int] = mapped_column(default=0)
    quote: Mapped[str] = mapped_column(Text, default="")
    anchor_prefix: Mapped[str] = mapped_column(Text, default="")
    anchor_suffix: Mapped[str] = mapped_column(Text, default="")
    anchor_revision: Mapped[str] = mapped_column(String(80), default="")
    page: Mapped[int | None] = mapped_column(nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisRecipeRow(Base):
    """A version-pinned, reproducible analysis or transformation recipe."""

    __tablename__ = "analysis_recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    dataset_id: Mapped[int] = mapped_column(ForeignKey("research_datasets.id"), index=True)
    dataset_version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(40), default="summary")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class RiskOfBiasRow(Base):
    """A reviewer-owned structured appraisal, never an opaque AI score."""

    __tablename__ = "risk_of_bias"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    study_id: Mapped[int | None] = mapped_column(ForeignKey("studies.id"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    work_id: Mapped[str] = mapped_column(String(64), default="")
    reviewer_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    tool: Mapped[str] = mapped_column(String(40), default="rob2")
    overall: Mapped[str] = mapped_column(String(40), default="some_concerns")
    domains: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class EvidenceClaimRow(Base):
    """A manuscript/project claim whose support can be audited and invalidated."""

    __tablename__ = "evidence_claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    writer_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("writer_documents.id"), nullable=True, index=True
    )
    text: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    confidence: Mapped[str] = mapped_column(String(20), default="unrated")
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ClaimEvidenceRow(Base):
    """Typed edge from a claim to a study, work, quote, dataset or analysis."""

    __tablename__ = "claim_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("evidence_claims.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(80))
    relationship: Mapped[str] = mapped_column(String(20), default="supports")
    locator: Mapped[str] = mapped_column(String(200), default="")
    quote: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    source_version: Mapped[str] = mapped_column(String(80), default="")
    verified: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SubmissionRow(Base):
    """A journal submission package and its human-verifiable checklist."""

    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    writer_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("writer_documents.id"), nullable=True
    )
    journal: Mapped[str] = mapped_column(String(240), default="")
    article_type: Mapped[str] = mapped_column(String(120), default="Article")
    status: Mapped[str] = mapped_column(String(24), default="preparing")
    checklist: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    target_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterTemplateRow(Base):
    """An org's own LaTeX template: any document can be saved as one, and
    new documents can start from it instead of a built-in."""

    __tablename__ = "writer_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WriterTemplateShareRow(Base):
    """A revocable bearer link for copying a template into another workspace."""

    __tablename__ = "writer_template_shares"
    __table_args__ = (UniqueConstraint("template_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("writer_templates.id"), unique=True, index=True
    )
    token: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=new_capability_token
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WriterTemplateOriginRow(Base):
    """Provenance and package metadata for a linked template.

    Package bytes live under ``data_dir/writer_templates/{template_id}``.
    Keeping this separate preserves pasted single-file templates while a
    remote template can retain its complete project and legal provenance.
    """

    __tablename__ = "writer_template_origins"
    __table_args__ = (UniqueConstraint("template_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("writer_templates.id"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    upstream_url: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    license_name: Mapped[str] = mapped_column(String(200), default="")
    license_url: Mapped[str] = mapped_column(Text, default="")
    license_status: Mapped[str] = mapped_column(String(20), default="unverified")
    rights_confirmed: Mapped[bool] = mapped_column(default=False)
    package_filename: Mapped[str] = mapped_column(String(200), default="template.zip")
    package_sha256: Mapped[str] = mapped_column(String(64), default="")
    byte_size: Mapped[int] = mapped_column(default=0)
    file_count: Mapped[int] = mapped_column(default=1)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WriterContributionRow(Base):
    """Append-only AI transparency ledger for a Writer document: which
    assistant proposals actually landed in the text (and how), so the
    contribution log reports what happened instead of guessing."""

    __tablename__ = "writer_contributions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("writer_documents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ImportBatchRow(Base):
    """An uploaded RIS/BibTeX export, parsed into records at upload time.

    Fed into a run's identification stage as an additional database arm
    (Scopus, Web of Science, PubMed exports). Org-scoped: the upload is the
    tenant's own search artifact.
    """

    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(300), default="")
    records: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ReferenceConnectorRow(Base):
    """A scoped Zotero library or Citavi exchange project.

    Zotero credentials are encrypted before they reach ``credential``. Citavi
    has no public cloud API, so its connector is a durable, versioned exchange
    target populated by RIS/BibTeX/ENW uploads.
    """

    __tablename__ = "reference_connectors"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", "library_type", "library_id", "collection_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)  # zotero | citavi
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="connected")
    library_type: Mapped[str] = mapped_column(String(20), default="project")
    library_id: Mapped[str] = mapped_column(String(100))
    collection_key: Mapped[str] = mapped_column(String(100), default="")
    credential: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    library_version: Mapped[int] = mapped_column(default=0)
    item_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ReferenceConnectorItemRow(Base):
    """One versioned item mirrored from a reference connector."""

    __tablename__ = "reference_connector_items"
    __table_args__ = (UniqueConstraint("connector_id", "external_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    connector_id: Mapped[int] = mapped_column(
        ForeignKey("reference_connectors.id", ondelete="CASCADE"), index=True
    )
    external_key: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(default=0)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProtocolRow(Base):
    __tablename__ = "protocols"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    # follows the run: a protocol of an unfiled run has no project either
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    version: Mapped[int] = mapped_column(default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    # opaque public address (URLs / topbar); the int id stays the join key
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    # a chat/search may live outside any project folder
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    protocol_id: Mapped[int | None] = mapped_column(ForeignKey("protocols.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    question: Mapped[str] = mapped_column(Text)
    # user-chosen display name; the question stays the immutable audit record
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # run flags (resume + echo)
    corpus_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prisma: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # the seeded example search every org starts with (excluded from quotas,
    # badged in the UI, replayed from a fixture instead of costing tokens)
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def companion_paper_run_owner(run: Run) -> int | None:
    """Return the creator of a private Companion paper run, failing closed."""

    config = dict(run.config or {})
    if config.get("surface") != "companion_paper":
        return None
    owner = config.get("owner_user_id")
    if not isinstance(owner, (int, str)) or isinstance(owner, bool):
        return -1
    try:
        return int(owner)
    except (TypeError, ValueError):
        return -1


def run_visible_to_user(run: Run, user_id: int | None) -> bool:
    """Keep creator-private Companion paper runs out of every shared surface."""

    owner_id = companion_paper_run_owner(run)
    return owner_id is None or (user_id is not None and owner_id == user_id)


class RunEvent(Base):
    """Append-only audit log: one row per pipeline event."""

    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(100))
    event: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BackgroundJobRow(Base):
    """Durable task queue with lease-based worker ownership.

    The API commits a job before returning 202. A separate worker claims one
    row at a time. ``lease_token`` fences stale workers: a process may only
    complete or retry the exact claim it owns. Expired leases can be reclaimed
    by another replica without resetting healthy work in the same lane.
    """

    __tablename__ = "background_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("orgs.id"), nullable=True, index=True)
    task: Mapped[str] = mapped_column(String(80), index=True)
    lane: Mapped[str] = mapped_column(String(16), default="all", index=True)
    priority: Mapped[int] = mapped_column(default=0, index=True)
    args: Mapped[list[Any]] = mapped_column(JSON, default=list)
    kwargs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    timeout_seconds: Mapped[int] = mapped_column(default=21_600)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    lease_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WorkerReplicaRow(Base):
    """Private liveness and capacity record for one worker process.

    The local heartbeat file is only a container health signal. This row is
    the fleet-wide source of truth used by operator health and scaling checks.
    It intentionally contains no tenant or task payload.
    """

    __tablename__ = "worker_replicas"
    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    lane: Mapped[str] = mapped_column(String(16), index=True)
    hostname: Mapped[str] = mapped_column(String(200))
    pid: Mapped[int] = mapped_column()
    release_revision: Mapped[str] = mapped_column(String(64), default="unknown")
    concurrency: Mapped[int] = mapped_column(default=1)
    active_slots: Mapped[int] = mapped_column(default=0)
    draining: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class WorkRow(Base):
    """Works touched by runs (canonical id = OpenAlex id)."""

    __tablename__ = "works"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    doi: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SourceRecordRow(Base):
    """Provenance: which run/query found which work in which source."""

    __tablename__ = "source_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    corpus_version: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ScreeningDecisionRow(Base):
    __tablename__ = "screening_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(200))
    verdict: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentRow(Base):
    """Acquisition ledger: one row per work sought for full-text retrieval.

    A ``retrieved`` row carries the stored blob's provenance and — crucially —
    its ``legal_basis`` (principle 7). A ``not_retrieved`` row carries the honest
    ``reason`` and feeds the PRISMA "reports not retrieved" box. Org-scoped:
    acquired documents are tenant data. ``run_id`` is nullable: a user upload
    starts life pending (no run yet) and is attached when its run is created.
    """

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    folder: Mapped[str | None] = mapped_column(String(120), nullable=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))  # retrieved | not_retrieved
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    legal_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)
    license: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    byte_size: Mapped[int] = mapped_column(default=0)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_status: Mapped[str] = mapped_column(String(30), default="not_retrieved")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Explicit Library deletion keeps run evidence but suppresses it from the
    # workspace Library until a new durable original is materialized.
    library_suppressed: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentAnnotationRow(Base):
    """A durable passage annotation in a stored PDF.

    ``source`` distinguishes a reader's own mark from a passage proposed by
    the assistant. The quote is the stable anchor because pdf.js text layers
    do not expose portable coordinates across renderer versions.
    """

    __tablename__ = "document_annotations"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="user")  # user | assistant
    page: Mapped[int] = mapped_column()
    quote: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(20), default="moss")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ChatMessageRow(Base):
    """A turn in the grounded Q&A thread over a run's results.

    role="tool" rows record an agentic tool invocation (web search, paper
    lookup) the assistant made while answering; payload carries the tool name,
    query and result summaries so the UI can render the step.
    """

    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list)  # work ids cited
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ConversationReviewRow(Base):
    """A user-consented, point-in-time review request for one conversation.

    The message boundary is frozen when consent is given. Community instances
    retain the request locally; no transcript or note is sent to a hosted
    operator by this service.
    """

    __tablename__ = "conversation_reviews"
    __table_args__ = (
        UniqueConstraint("run_id", "user_id", name="uq_conversation_reviews_run_user"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=lambda: new_public_id(16)
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message_through_id: Mapped[int] = mapped_column(default=0)
    category: Mapped[str] = mapped_column(String(40), default="incorrect_answer")
    note: Mapped[str] = mapped_column(Text, default="")
    consented: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ChatTurnRow(Base):
    """Durable control plane for one streamed chat turn.

    ``turn_id`` is generated by the client and makes retries idempotent.  An
    active turn owns slot ``1`` for its run; terminal rows release the slot by
    setting it to NULL.  SQL unique constraints therefore enforce both one
    logical execution per turn id and at most one execution per conversation.
    """

    __tablename__ = "chat_turns"
    __table_args__ = (
        UniqueConstraint("run_id", "turn_id", name="uq_chat_turns_run_turn"),
        UniqueConstraint("run_id", "active_slot", name="uq_chat_turns_run_active_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(64))
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(60), default="auto")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    active_slot: Mapped[int | None] = mapped_column(nullable=True)
    action_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    base_charged: Mapped[bool] = mapped_column(default=False)
    settlement_charged: Mapped[bool] = mapped_column(default=False)
    event_count: Mapped[int] = mapped_column(default=0)
    terminal_event_sequence: Mapped[int | None] = mapped_column(nullable=True)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatTurnEventRow(Base):
    """One append-only public SSE frame emitted by a Quick Answer turn."""

    __tablename__ = "chat_turn_events"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence", name="uq_chat_turn_events_turn_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    turn_id: Mapped[int] = mapped_column(ForeignKey("chat_turns.id"), index=True)
    sequence: Mapped[int] = mapped_column(index=True)
    event: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AgentTurnRow(Base):
    """Durable control plane shared by interactive specialist agents.

    A client-generated ``turn_id`` makes retries idempotent across reconnects.
    ``active_slot`` is ``1`` only while a resource owns an active turn, so a
    database constraint protects the same workspace across tabs and replicas.
    The event count allocates monotonic sequence numbers for the append-only
    event ledger without relying on process-local state.
    """

    __tablename__ = "agent_turns"
    __table_args__ = (
        UniqueConstraint("org_id", "turn_id", name="uq_agent_turns_org_turn"),
        UniqueConstraint(
            "org_id",
            "resource_kind",
            "resource_id",
            "active_slot",
            name="uq_agent_turns_resource_active_slot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(64))
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    resource_kind: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    active_slot: Mapped[int | None] = mapped_column(nullable=True)
    event_count: Mapped[int] = mapped_column(default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentEventRow(Base):
    """One replayable, user-safe event emitted by an interactive agent turn."""

    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence", name="uq_agent_events_turn_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    turn_id: Mapped[int] = mapped_column(ForeignKey("agent_turns.id"), index=True)
    sequence: Mapped[int] = mapped_column(index=True)
    event: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SurveyRow(Base):
    """A hosted questionnaire whose responses remain workspace-owned."""

    __tablename__ = "surveys"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=new_capability_token
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    questions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    participant_information: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SurveyResponseRow(Base):
    """One submitted response; answers are keyed by durable question ids."""

    __tablename__ = "survey_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=new_capability_token
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("surveys.id"), index=True)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    respondent_label: Mapped[str] = mapped_column(String(160), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    participant_information_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SurveyMessageRow(Base):
    """Persistent analysis conversation for one survey."""

    __tablename__ = "survey_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("surveys.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ReportRow(Base):
    """A synthesized run report (the PDF's content), cached per run."""

    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WebSourceRow(Base):
    """A grey-literature web source found for a run (NOT academic literature)."""

    __tablename__ = "web_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(200), default="")
    score: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LibraryWebSourceRow(Base):
    """A user-confirmed browser capture kept separate from academic papers.

    The record stores allowlisted citation metadata and an optional selected
    excerpt. It deliberately has no page-body, DOM, cookie or request-header
    field, so the browser extension cannot turn the Library into browsing
    surveillance by accident.
    """

    __tablename__ = "library_web_sources"
    __table_args__ = (
        UniqueConstraint("org_id", "canonical_url_hash", name="uq_library_web_source_url"),
        UniqueConstraint("org_id", "capture_id", name="uq_library_web_source_capture"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    canonical_url: Mapped[str] = mapped_column(Text)
    canonical_url_hash: Mapped[str] = mapped_column(String(64), index=True)
    capture_id: Mapped[str] = mapped_column(String(100))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    original_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500))
    site_name: Mapped[str] = mapped_column(String(200), default="")
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    selected_excerpt: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[str] = mapped_column(String(20), default="web")
    doi: Mapped[str] = mapped_column(String(300), default="")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class LibraryAccessGrantRow(Base):
    """One account-bound, read-only view onto another workspace's Library.

    A NULL ``project_id`` grants the whole Library, including future entries.
    A non-NULL project keeps the scope dynamic: moving an entry into or out of
    that project immediately changes whether it is visible to the recipient.
    The grant never changes document ownership or copies tenant data.
    """

    __tablename__ = "library_access_grants"
    __table_args__ = (
        CheckConstraint("role = 'viewer'", name="ck_library_access_grant_role"),
        Index(
            "uq_library_access_grant_whole",
            "org_id",
            "grantee_user_id",
            unique=True,
            sqlite_where=text("project_id IS NULL AND revoked_at IS NULL"),
            postgresql_where=text("project_id IS NULL AND revoked_at IS NULL"),
        ),
        Index(
            "uq_library_access_grant_project",
            "org_id",
            "grantee_user_id",
            "project_id",
            unique=True,
            sqlite_where=text("project_id IS NOT NULL AND revoked_at IS NULL"),
            postgresql_where=text("project_id IS NOT NULL AND revoked_at IS NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=lambda: new_public_id(16)
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    grantee_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rights_statement_version: Mapped[str] = mapped_column(String(32))
    rights_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BrowserCaptureReceiptRow(Base):
    """Idempotency receipt for any capture; no page bodies or raw bytes retained."""

    __tablename__ = "browser_capture_receipts"
    __table_args__ = (UniqueConstraint("org_id", "capture_id", name="uq_browser_capture_receipt"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    capture_id: Mapped[str] = mapped_column(String(100))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    web_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_web_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CompanionPaperChatReceiptRow(Base):
    """Creator-private idempotency receipt for one local-PDF paper chat.

    Raw PDF bytes and local paths never enter this table.  The content digest
    binds a client retry to the exact confirmed file while the two document
    references distinguish the durable Library original from the run-bound
    chat clone used for passage selections.
    """

    __tablename__ = "companion_paper_chat_receipts"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "client_request_id",
            name="uq_companion_paper_chat_request",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_request_id: Mapped[str] = mapped_column(String(100))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    claim_token: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="processing", index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    library_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chat_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class BrowserCapturedPaperMetadataRow(Base):
    """Org-private reviewed metadata layered over a Library paper ledger row."""

    __tablename__ = "browser_captured_paper_metadata"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_browser_paper_metadata_document"),
        UniqueConstraint(
            "org_id", "canonical_url_hash", name="uq_browser_paper_metadata_canonical"
        ),
        UniqueConstraint("org_id", "doi_identity", name="uq_browser_paper_metadata_doi"),
        UniqueConstraint("org_id", "pdf_checksum", name="uq_browser_paper_metadata_checksum"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500), default="")
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    doi: Mapped[str] = mapped_column(String(300), default="")
    doi_identity: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    pdf_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    canonical_url_hash: Mapped[str] = mapped_column(String(64), index=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class PaperEnrichmentRow(Base):
    """Creator-private receipt for one explicitly requested paper lookup.

    Results remain proposals until the same creator applies them.  The stable
    identity and source revision freeze the paper that was checked, while the
    background-job reference makes cancellation and operational recovery
    durable without exposing queue internals in the public API.
    """

    __tablename__ = "paper_enrichments"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "user_id",
            "request_id",
            name="uq_paper_enrichment_request",
        ),
        Index(
            "uq_paper_enrichment_active",
            "org_id",
            "user_id",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    background_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_id: Mapped[str] = mapped_column(String(100))
    identity_kind: Mapped[str] = mapped_column(String(20))
    identity_value: Mapped[str] = mapped_column(String(500))
    source_revision: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    retry_count: Mapped[int] = mapped_column(default=0)
    suggestions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    provider_receipts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    applied_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_code: Mapped[str] = mapped_column(String(80), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Webhook(Base):
    """Outbound notification endpoint (org-scoped). events=[] means all events."""

    __tablename__ = "webhooks"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    secret: Mapped[str] = mapped_column(String(64))  # signs deliveries (HMAC-SHA256)
    events: Mapped[list[Any]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CreditEventRow(Base):
    """One local resource-usage event in auditable capacity units."""

    __tablename__ = "credit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40))  # question | search | screening
    model: Mapped[str] = mapped_column(String(60), default="auto")  # chosen menu id
    credits: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CapacityReservationRow(Base):
    """Capacity held for a long-running workflow.

    Reservations close the gap between authorizing a search and settling its
    actual screening volume.  ``remaining_credits`` shrinks as durable credit
    events are written; unused capacity is released on terminal completion.
    One resource/action pair owns one lifecycle, so retries and resumes reuse
    the same row instead of reserving twice. Resources include runs, figures,
    interviews, surveys, datasets and writer documents.
    """

    __tablename__ = "capacity_reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=new_public_id
    )
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), default="run", index=True)
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(40), default="screening")
    reserved_credits: Mapped[int] = mapped_column(default=0)
    remaining_credits: Mapped[int] = mapped_column(default=0)
    cost_limit_usd: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LLMCallRow(Base):
    """One LLM call in the local per-run provider-usage audit."""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    action_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    resource_type: Mapped[str] = mapped_column(String(32), default="run", index=True)
    resource_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    # ``provider`` means the gateway returned the measured amount; ``catalog``
    # is the conservative local price-table fallback used by direct adapters.
    cost_source: Mapped[str] = mapped_column(String(16), default="catalog")
    duration_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProviderCostRow(Base):
    """Non-chat provider spend such as image generation and transcription.

    Text-model calls stay in ``llm_calls`` because their token audit is useful.
    This sibling ledger gives every other provider action the same
    organization/resource attribution and monthly hard-budget accounting.
    """

    __tablename__ = "provider_costs"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    action_id: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(String(50), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    cost_source: Mapped[str] = mapped_column(String(16), default="provider")
    request_count: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = get_settings().resolved_database_url
        if url.startswith("sqlite"):
            # WAL lets API reads proceed while a run is writing, and the busy
            # timeout makes short writers wait instead of failing with
            # "database is locked" while a checkpoint commit is in flight.
            _engine = create_engine(url, connect_args={"timeout": 30})

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        else:
            settings = get_settings()
            _engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_size=max(1, settings.database_pool_size),
                max_overflow=max(0, settings.database_max_overflow),
                pool_recycle=max(60, settings.database_pool_recycle_seconds),
                pool_use_lifo=True,
            )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    """Create all tables (dev convenience; deployments use alembic)."""
    Base.metadata.create_all(get_engine())


@contextmanager
def db_session() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_default_org(session: Session) -> Org:
    org = session.scalar(select(Org).where(Org.name == "default"))
    if org is None:
        org = Org(name="default")
        session.add(org)
        session.flush()
    return org
