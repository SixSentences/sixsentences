"""Bounded database retrieval for a single authorized specialist conversation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from sixsentences_server.core.conversation import context_query_terms


def load_context_rows(
    session: Session,
    model: Any,
    *,
    resource_column: Any,
    resource_id: Any,
    org_id: int,
    current_request: str = "",
) -> list[Any]:
    """Return newest-first rows, retaining user anchors beyond the recent window.

    The caller must first authorize the resource. Every query independently
    binds both its resource and owning organization. This is retrieval, not a
    persistent summary: the model renderer still applies its strict size budget.
    """
    base = select(model).where(
        resource_column == resource_id,
        model.org_id == org_id,
        model.role.in_(("user", "assistant")),
    )
    users = base.where(model.role == "user")
    queries = [
        base.order_by(model.id.desc()).limit(120),
        users.order_by(model.id.desc()).limit(240),
        users.order_by(model.id.asc()).limit(4),
    ]
    # Older corrections/constraints must not disappear merely because the
    # user has since asked many short follow-ups. Retrieval is fixed-size.
    cues = (
        "instead",
        "correction",
        "do not",
        "never",
        "always",
        "without",
        "stattdessen",
        "korrektur",
        "nicht",
        "ohne",
        "immer",
        "behalte",
    )
    queries.append(
        users.where(or_(*(model.content.icontains(cue, autoescape=True) for cue in cues)))
        .order_by(model.id.desc())
        .limit(48)
    )
    terms = context_query_terms(current_request)[:6]
    if terms:
        queries.append(
            users.where(or_(*(model.content.icontains(term, autoescape=True) for term in terms)))
            .order_by(model.id.desc())
            .limit(48)
        )
    rows = {row.id: row for query in queries for row in session.scalars(query).all()}
    return [rows[key] for key in sorted(rows, reverse=True)]
