"""Widen rate-limit window ids for sub-second provider gates.

Revision ID: 20260919_0002
Revises: 20260912_0001
Create Date: 2026-09-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "20260919_0002"
down_revision = "20260912_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_window_id_type(
    existing_type: sa.types.TypeEngine[object],
    target_type: sa.types.TypeEngine[object],
) -> None:
    inspector = sa.inspect(op.get_bind())
    if "rate_limit_buckets" not in inspector.get_table_names():
        return
    with op.batch_alter_table("rate_limit_buckets") as batch:
        batch.alter_column(
            "window_id",
            existing_type=existing_type,
            type_=target_type,
            existing_nullable=False,
        )


def upgrade() -> None:
    _set_window_id_type(sa.Integer(), sa.BigInteger())


def downgrade() -> None:
    # Sub-second PubMed windows exceed PostgreSQL INTEGER by construction.
    # They are ephemeral coordination slots, so remove only those two scopes
    # before restoring the prior type; unrelated abuse controls survive.
    if "rate_limit_buckets" in sa.inspect(op.get_bind()).get_table_names():
        op.execute(
            sa.text(
                "DELETE FROM rate_limit_buckets "
                "WHERE scope IN ('pubmed-egress-keyed-v1', 'pubmed-egress-unkeyed-v1')"
            )
        )
    _set_window_id_type(sa.BigInteger(), sa.Integer())
