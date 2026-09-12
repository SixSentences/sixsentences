"""Create the complete, clean community schema.

Revision ID: 20260912_0001
Revises: none
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    """Install the standalone schema without importing mutable application models."""

    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])
    op.create_table(
        "projects",
        *_tenant_columns(),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
    )
    op.create_index("ix_projects_org_id", "projects", ["org_id"])
    op.create_table(
        "library_items",
        *_tenant_columns(),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("identifiers", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
    )
    op.create_index("ix_library_items_org_id", "library_items", ["org_id"])
    op.create_table(
        "research_runs",
        *_tenant_columns(),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_research_runs_org_id", "research_runs", ["org_id"])
    op.create_index("ix_research_runs_status", "research_runs", ["status"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_jobs_org_id", "jobs", ["org_id"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "available_at", "created_at"])
    op.create_table(
        "datasets",
        *_tenant_columns(),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
        ),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("source_format", sa.String(24), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
    )
    op.create_index("ix_datasets_org_id", "datasets", ["org_id"])
    op.create_table(
        "figures",
        *_tenant_columns(),
        sa.Column(
            "dataset_id",
            sa.Integer(),
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
    )
    op.create_index("ix_figures_org_id", "figures", ["org_id"])
    op.create_table(
        "surveys",
        *_tenant_columns(),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("participant_information", sa.JSON(), nullable=False),
        sa.Column("public_token_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_surveys_org_id", "surveys", ["org_id"])
    op.create_index("ix_surveys_public_token_hash", "surveys", ["public_token_hash"])
    op.create_table(
        "survey_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "survey_id",
            sa.Integer(),
            sa.ForeignKey("surveys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("consent_snapshot", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_survey_responses_org_id", "survey_responses", ["org_id"])
    op.create_index("ix_survey_responses_survey_id", "survey_responses", ["survey_id"])
    op.create_table(
        "interview_studies",
        *_tenant_columns(),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("modalities", sa.JSON(), nullable=False),
        sa.Column("guide", sa.JSON(), nullable=False),
        sa.Column("participant_information", sa.JSON(), nullable=False),
        sa.Column("scope_version", sa.String(32), nullable=False),
        sa.Column("scope_fingerprint", sa.String(64), nullable=False),
        sa.Column("public_token_hash", sa.String(64), nullable=False),
        sa.Column("max_session_minutes", sa.Integer(), nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("store_audio", sa.Boolean(), nullable=False),
        sa.Column("provider_disclosure", sa.Text(), nullable=False),
    )
    op.create_index("ix_interview_studies_org_id", "interview_studies", ["org_id"])
    op.create_index(
        "ix_interview_studies_public_token_hash",
        "interview_studies",
        ["public_token_hash"],
    )
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "study_id",
            sa.Integer(),
            sa.ForeignKey("interview_studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("modality", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("scope_fingerprint", sa.String(64), nullable=False),
        sa.Column("consent_snapshot", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_interview_sessions_org_id", "interview_sessions", ["org_id"])
    op.create_index("ix_interview_sessions_study_id", "interview_sessions", ["study_id"])
    op.create_table(
        "interview_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("audio_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_interview_turns_org_id", "interview_turns", ["org_id"])
    op.create_index(
        "ix_interview_turn_sequence", "interview_turns", ["session_id", "sequence"], unique=True
    )
    op.create_table(
        "writer_documents",
        *_tenant_columns(),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
    )
    op.create_index("ix_writer_documents_org_id", "writer_documents", ["org_id"])
    op.create_table(
        "knowledge_pages",
        *_tenant_columns(),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_index("ix_knowledge_pages_org_id", "knowledge_pages", ["org_id"])
    op.create_table(
        "brainstorms",
        *_tenant_columns(),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_brainstorms_org_id", "brainstorms", ["org_id"])
    op.create_table(
        "outbox_email",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(240), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_outbox_email_org_id", "outbox_email", ["org_id"])


def downgrade() -> None:
    """Remove only community-owned tables in reverse dependency order."""

    for table_name in (
        "outbox_email",
        "brainstorms",
        "knowledge_pages",
        "writer_documents",
        "interview_turns",
        "interview_sessions",
        "interview_studies",
        "survey_responses",
        "surveys",
        "figures",
        "datasets",
        "jobs",
        "research_runs",
        "library_items",
        "projects",
        "auth_tokens",
        "users",
        "organizations",
    ):
        op.drop_table(table_name)
