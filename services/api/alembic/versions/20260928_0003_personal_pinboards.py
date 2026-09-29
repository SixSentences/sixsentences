"""Add creator-private pinboard notes.

Revision ID: 20260928_0003
Revises: 20260919_0002
"""

import sqlalchemy as sa

from alembic import op

revision = "20260928_0003"
down_revision = "20260919_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an isolated table without modifying existing research records."""
    if "personal_pinboards" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "personal_pinboards",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_pinboard_revision"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_personal_pinboards_org_id", "personal_pinboards", ["org_id"])


def downgrade() -> None:
    """Remove only the table introduced by this revision."""
    op.drop_index("ix_personal_pinboards_org_id", table_name="personal_pinboards")
    op.drop_table("personal_pinboards")
