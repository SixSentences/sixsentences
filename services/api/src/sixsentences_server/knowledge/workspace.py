"""Creator-private Knowledge pages and their bounded HTTP contract.

This surface stores thoughts written by the user. It intentionally does not
aggregate Library, project, browser-capture or Companion data. Projects are
optional filing references only and never broaden page visibility.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.routing import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from sixsentences_server.core.auth import AuthContext
from sixsentences_server.core.db import KnowledgePageRow, Project, db_session

KnowledgeState = Literal["inbox", "developing", "evergreen", "archived"]
KnowledgeStateFilter = Literal["active", "inbox", "developing", "evergreen", "archived"]
KnowledgeSearchScope = Literal["all", "title"]

MAX_BODY_LENGTH = 50_000
MAX_HIERARCHY_DEPTH = 8
MAX_PAGES_PER_CREATOR = 10_000
MAX_TOTAL_BODY_CHARS = 5_000_000
MAX_TAGS = 12
MAX_TAG_LENGTH = 32
ROOT_PARENT = "root"
_PUBLIC_ID_ALPHABET = frozenset("abcdefghjkmnpqrstuvwxyz23456789")


def _normalize_tag(value: str) -> str:
    tag = unicodedata.normalize("NFKC", value).strip()
    if not tag:
        raise ValueError("tags cannot be empty")
    if len(tag) > MAX_TAG_LENGTH:
        raise ValueError(f"tags cannot exceed {MAX_TAG_LENGTH} characters")
    if "|" in tag or any(unicodedata.category(character).startswith("C") for character in tag):
        raise ValueError("tags cannot contain separators or control characters")
    return tag


def _normalize_tags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = _normalize_tag(raw)
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    if len(normalized) > MAX_TAGS:
        raise ValueError(f"a page can have at most {MAX_TAGS} tags")
    return normalized


def _tags_index(tags: list[str]) -> str:
    return f"|{'|'.join(tag.casefold() for tag in tags)}|" if tags else ""


def _normalize_title(value: str) -> str:
    title = unicodedata.normalize("NFKC", value).strip()
    if len(title) > 240:
        raise ValueError("title cannot exceed 240 characters after normalization")
    if any(unicodedata.category(character).startswith("C") for character in title):
        raise ValueError("title cannot contain control characters")
    return title


def _normalize_body(value: str) -> str:
    if "\x00" in value:
        raise ValueError("body_markdown cannot contain NUL characters")
    return value.replace("\r\n", "\n").replace("\r", "\n")


class KnowledgePageCreate(BaseModel):
    """Idempotent quick-capture request."""

    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=100)
    title: str = Field(default="", max_length=240)
    body_markdown: str = Field(default="", max_length=MAX_BODY_LENGTH)
    state: KnowledgeState = "inbox"
    pinned: bool = False
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    parent_id: str | None = Field(default=None, min_length=10, max_length=16)
    project_id: int | None = Field(default=None, ge=1)
    position: int = Field(default=0, ge=0, le=1_000_000_000)

    @field_validator("client_request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 8:
            raise ValueError("client_request_id must contain at least 8 non-space characters")
        if "\x00" in normalized:
            raise ValueError("client_request_id cannot contain NUL characters")
        return normalized

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _normalize_title(value)

    @field_validator("body_markdown")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return _normalize_body(value)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        return _normalize_tags(values)

    @model_validator(mode="after")
    def validate_content(self) -> KnowledgePageCreate:
        if not self.title and not self.body_markdown.strip():
            raise ValueError("title or body_markdown is required")
        return self


class KnowledgePagePatch(BaseModel):
    """Optimistic partial update; omitted fields remain untouched."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=240)
    body_markdown: str | None = Field(default=None, max_length=MAX_BODY_LENGTH)
    state: KnowledgeState | None = None
    pinned: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=MAX_TAGS)
    parent_id: str | None = Field(default=None, min_length=10, max_length=16)
    project_id: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return _normalize_title(value) if value is not None else None

    @field_validator("body_markdown")
    @classmethod
    def validate_body(cls, value: str | None) -> str | None:
        return _normalize_body(value) if value is not None else None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str] | None) -> list[str] | None:
        return _normalize_tags(values) if values is not None else None

    @model_validator(mode="after")
    def reject_null_values(self) -> KnowledgePagePatch:
        nullable_fields = {"parent_id", "project_id"}
        for field_name in self.model_fields_set - nullable_fields - {"expected_revision"}:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


def _valid_public_id(value: str) -> bool:
    return 10 <= len(value) <= 16 and all(character in _PUBLIC_ID_ALPHABET for character in value)


def _owned_page(session: Session, ctx: AuthContext, public_id: str) -> KnowledgePageRow:
    if not _valid_public_id(public_id):
        raise HTTPException(404, "knowledge page not found")
    row = session.scalar(
        select(KnowledgePageRow).where(
            KnowledgePageRow.public_id == public_id,
            KnowledgePageRow.org_id == ctx.org_id,
            KnowledgePageRow.creator_user_id == ctx.user_id,
        )
    )
    if row is None:
        raise HTTPException(404, "knowledge page not found")
    return row


def _project_id(session: Session, ctx: AuthContext, project_id: int | None) -> int | None:
    if project_id is None:
        return None
    project = session.scalar(
        select(Project)
        .where(Project.id == project_id, Project.org_id == ctx.org_id)
        .with_for_update()
    )
    if project is None:
        raise HTTPException(404, "project not found")
    return project.id


def _parent_row(
    session: Session,
    ctx: AuthContext,
    parent_public_id: str | None,
) -> KnowledgePageRow | None:
    if parent_public_id is None:
        return None
    if not _valid_public_id(parent_public_id):
        raise HTTPException(404, "parent knowledge page not found")
    parent = session.scalar(
        select(KnowledgePageRow).where(
            KnowledgePageRow.public_id == parent_public_id,
            KnowledgePageRow.org_id == ctx.org_id,
            KnowledgePageRow.creator_user_id == ctx.user_id,
        )
    )
    if parent is None:
        raise HTTPException(404, "parent knowledge page not found")
    return parent


def _lock_creator_hierarchy(session: Session, ctx: AuthContext) -> None:
    """Serialize hierarchy mutations across API replicas for one creator."""

    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        connection = session.connection()
        raw_connection = connection.connection.driver_connection
        if not bool(getattr(raw_connection, "in_transaction", False)):
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        return
    if dialect != "postgresql":
        return
    digest = hashlib.sha256(f"knowledge-hierarchy:{ctx.org_id}:{ctx.user_id}".encode()).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    session.execute(
        text("SELECT pg_advisory_xact_lock(:knowledge_lock)"),
        {"knowledge_lock": lock_key},
    )


def _validate_hierarchy(
    session: Session,
    ctx: AuthContext,
    *,
    row_id: int | None,
    parent: KnowledgePageRow | None,
) -> None:
    """Reject cycles and keep the complete resulting tree within eight levels."""

    pairs = list(
        session.execute(
            select(KnowledgePageRow.id, KnowledgePageRow.parent_id)
            .where(
                KnowledgePageRow.org_id == ctx.org_id,
                KnowledgePageRow.creator_user_id == ctx.user_id,
            )
            .limit(MAX_PAGES_PER_CREATOR + 1)
        ).all()
    )
    if len(pairs) > MAX_PAGES_PER_CREATOR:
        raise HTTPException(
            409,
            detail={
                "code": "knowledge_page_limit_reached",
                "message": "The Knowledge workspace exceeds its supported page limit.",
                "limit": MAX_PAGES_PER_CREATOR,
            },
        )
    parent_by_id = {page_id: parent_id for page_id, parent_id in pairs}
    if row_id is not None and row_id not in parent_by_id:
        raise HTTPException(404, "knowledge page not found")

    parent_id = parent.id if parent is not None else None
    if row_id is not None and parent_id == row_id:
        raise HTTPException(
            409,
            detail={
                "code": "knowledge_hierarchy_cycle",
                "message": "A page cannot be its own parent.",
            },
        )

    ancestor_depth = 0
    ancestor_seen: set[int] = set()
    cursor_id = parent_id
    while cursor_id is not None:
        if cursor_id in ancestor_seen or cursor_id == row_id:
            raise HTTPException(
                409,
                detail={
                    "code": "knowledge_hierarchy_cycle",
                    "message": "This parent would create a Knowledge page cycle.",
                },
            )
        ancestor_seen.add(cursor_id)
        ancestor_depth += 1
        cursor_id = parent_by_id.get(cursor_id)

    subtree_height = 1
    if row_id is not None:
        children: dict[int, list[int]] = defaultdict(list)
        for child_id, current_parent_id in pairs:
            if current_parent_id is not None:
                children[current_parent_id].append(child_id)
        frontier: list[tuple[int, int]] = [(row_id, 1)]
        descendants_seen: set[int] = set()
        while frontier:
            current_id, height = frontier.pop()
            if current_id in descendants_seen:
                raise HTTPException(
                    409,
                    detail={
                        "code": "knowledge_hierarchy_cycle",
                        "message": "The existing Knowledge hierarchy contains a cycle.",
                    },
                )
            descendants_seen.add(current_id)
            subtree_height = max(subtree_height, height)
            frontier.extend((child_id, height + 1) for child_id in children[current_id])

    if ancestor_depth + subtree_height > MAX_HIERARCHY_DEPTH:
        raise HTTPException(
            422,
            detail={
                "code": "knowledge_hierarchy_too_deep",
                "message": f"Knowledge hierarchies support at most {MAX_HIERARCHY_DEPTH} levels.",
                "max_depth": MAX_HIERARCHY_DEPTH,
            },
        )


def _request_fingerprint(body: KnowledgePageCreate) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json", exclude={"client_request_id"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _content_chars(
    session: Session,
    ctx: AuthContext,
    *,
    exclude_page_id: int | None = None,
) -> int:
    statement = select(func.sum(func.length(KnowledgePageRow.body_markdown))).where(
        KnowledgePageRow.org_id == ctx.org_id,
        KnowledgePageRow.creator_user_id == ctx.user_id,
    )
    if exclude_page_id is not None:
        statement = statement.where(KnowledgePageRow.id != exclude_page_id)
    return int(session.scalar(statement) or 0)


def _enforce_content_limit(current_chars: int, candidate_body: str) -> None:
    if current_chars + len(candidate_body) <= MAX_TOTAL_BODY_CHARS:
        return
    raise HTTPException(
        409,
        detail={
            "code": "knowledge_content_limit_reached",
            "message": "Shorten or delete an existing Knowledge page before saving more text.",
            "limit_chars": MAX_TOTAL_BODY_CHARS,
        },
    )


def _related_labels(
    session: Session,
    ctx: AuthContext,
    rows: list[KnowledgePageRow],
) -> tuple[dict[int, tuple[str, str]], dict[int, str]]:
    parent_ids = {row.parent_id for row in rows if row.parent_id is not None}
    project_ids = {row.project_id for row in rows if row.project_id is not None}
    parents = (
        {
            parent_id: (public_id, title)
            for parent_id, public_id, title in session.execute(
                select(
                    KnowledgePageRow.id,
                    KnowledgePageRow.public_id,
                    KnowledgePageRow.title,
                ).where(
                    KnowledgePageRow.id.in_(parent_ids),
                    KnowledgePageRow.org_id == ctx.org_id,
                    KnowledgePageRow.creator_user_id == ctx.user_id,
                )
            ).all()
        }
        if parent_ids
        else {}
    )
    projects: dict[int, str] = (
        {
            project_id: project_name
            for project_id, project_name in session.execute(
                select(Project.id, Project.name).where(
                    Project.id.in_(project_ids),
                    Project.org_id == ctx.org_id,
                )
            ).all()
        }
        if project_ids
        else {}
    )
    return parents, projects


def _page_payload(
    row: KnowledgePageRow,
    parents: dict[int, tuple[str, str]],
    projects: dict[int, str],
) -> dict[str, Any]:
    parent = parents.get(row.parent_id) if row.parent_id is not None else None
    return {
        "public_id": row.public_id,
        "title": row.title,
        "body_markdown": row.body_markdown,
        "state": row.state,
        "pinned": row.pinned,
        "tags": list(row.tags or []),
        "parent_id": parent[0] if parent is not None else None,
        "parent_title": parent[1] if parent is not None else None,
        "project_id": row.project_id if row.project_id in projects else None,
        "project_name": projects.get(row.project_id) if row.project_id is not None else None,
        "position": row.position,
        "revision": row.revision,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _one_page_payload(
    session: Session,
    ctx: AuthContext,
    row: KnowledgePageRow,
) -> dict[str, Any]:
    parents, projects = _related_labels(session, ctx, [row])
    return _page_payload(row, parents, projects)


def _revision_conflict(
    session: Session,
    ctx: AuthContext,
    row: KnowledgePageRow,
    expected: int,
) -> HTTPException:
    return HTTPException(
        409,
        detail={
            "code": "knowledge_revision_conflict",
            "message": "This page changed since it was opened. Reload before saving.",
            "expected_revision": expected,
            "current": _one_page_payload(session, ctx, row),
        },
    )


def install_knowledge_workspace_routes(
    app: FastAPI,
    *,
    require_session: Callable[..., AuthContext],
) -> None:
    """Attach the session-only Knowledge workspace routes to the application."""

    router = APIRouter(prefix="/knowledge/pages", tags=["knowledge"])

    @router.get("")
    def list_knowledge_pages(
        q: str = Query(default="", max_length=200),
        search_scope: KnowledgeSearchScope = "all",
        state: KnowledgeStateFilter | None = None,
        tag: str = Query(default="", max_length=MAX_TAG_LENGTH),
        pinned: bool | None = Query(default=None),
        project_id: int | None = Query(default=None, ge=1),
        parent_id: str | None = Query(default=None, max_length=16),
        offset: int = Query(default=0, ge=0, le=MAX_PAGES_PER_CREATOR),
        limit: int = Query(default=50, ge=1, le=100),
        ctx: AuthContext = Depends(require_session),
    ) -> dict[str, Any]:
        """Search and filter only the caller's authored thought pages."""

        with db_session() as session:
            statement = select(KnowledgePageRow).where(
                KnowledgePageRow.org_id == ctx.org_id,
                KnowledgePageRow.creator_user_id == ctx.user_id,
            )
            if "\x00" in q:
                raise HTTPException(422, "q cannot contain NUL characters")
            normalized_query = q.strip().lower()
            if normalized_query:
                title_match = func.lower(KnowledgePageRow.title).contains(
                    normalized_query, autoescape=True
                )
                if search_scope == "title":
                    statement = statement.where(title_match)
                else:
                    statement = statement.where(
                        or_(
                            title_match,
                            func.lower(KnowledgePageRow.body_markdown).contains(
                                normalized_query, autoescape=True
                            ),
                        )
                    )
            if state == "active":
                statement = statement.where(KnowledgePageRow.state != "archived")
            elif state is not None:
                statement = statement.where(KnowledgePageRow.state == state)
            if tag.strip():
                try:
                    normalized_tag = _normalize_tag(tag).casefold()
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                statement = statement.where(
                    KnowledgePageRow.tags_index.contains(f"|{normalized_tag}|", autoescape=True)
                )
            if pinned is not None:
                statement = statement.where(KnowledgePageRow.pinned.is_(pinned))
            if project_id is not None:
                statement = statement.where(KnowledgePageRow.project_id == project_id)
            if parent_id is not None:
                if parent_id == ROOT_PARENT:
                    statement = statement.where(KnowledgePageRow.parent_id.is_(None))
                else:
                    parent = _parent_row(session, ctx, parent_id)
                    if parent is None:
                        raise HTTPException(404, "parent knowledge page not found")
                    statement = statement.where(KnowledgePageRow.parent_id == parent.id)

            total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(
                        KnowledgePageRow.pinned.desc(),
                        KnowledgePageRow.position.asc(),
                        KnowledgePageRow.updated_at.desc(),
                        KnowledgePageRow.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            parents, projects = _related_labels(session, ctx, rows)
            items = [_page_payload(row, parents, projects) for row in rows]
            return {
                "items": items,
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(items) < total,
            }

    @router.post("", status_code=201)
    def create_knowledge_page(
        body: KnowledgePageCreate,
        ctx: AuthContext = Depends(require_session),
    ) -> dict[str, Any]:
        """Capture one page, safely replaying the same client request."""

        fingerprint = _request_fingerprint(body)
        with db_session() as session:
            _lock_creator_hierarchy(session, ctx)
            existing = session.scalar(
                select(KnowledgePageRow).where(
                    KnowledgePageRow.org_id == ctx.org_id,
                    KnowledgePageRow.creator_user_id == ctx.user_id,
                    KnowledgePageRow.client_request_id == body.client_request_id,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise HTTPException(
                        409,
                        detail={
                            "code": "knowledge_idempotency_conflict",
                            "message": "This client_request_id was already used for another page.",
                        },
                    )
                return _one_page_payload(session, ctx, existing)

            page_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgePageRow)
                    .where(
                        KnowledgePageRow.org_id == ctx.org_id,
                        KnowledgePageRow.creator_user_id == ctx.user_id,
                    )
                )
                or 0
            )
            if page_count >= MAX_PAGES_PER_CREATOR:
                raise HTTPException(
                    409,
                    detail={
                        "code": "knowledge_page_limit_reached",
                        "message": (
                            "Delete an existing Knowledge page before creating another one."
                        ),
                        "limit": MAX_PAGES_PER_CREATOR,
                    },
                )
            _enforce_content_limit(_content_chars(session, ctx), body.body_markdown)

            parent = _parent_row(session, ctx, body.parent_id)
            _validate_hierarchy(session, ctx, row_id=None, parent=parent)
            project_id = _project_id(session, ctx, body.project_id)
            row = KnowledgePageRow(
                org_id=ctx.org_id,
                creator_user_id=ctx.user_id,
                client_request_id=body.client_request_id,
                request_fingerprint=fingerprint,
                title=body.title,
                body_markdown=body.body_markdown,
                state=body.state,
                pinned=body.pinned,
                tags=body.tags,
                tags_index=_tags_index(body.tags),
                parent_id=parent.id if parent is not None else None,
                project_id=project_id,
                position=body.position,
            )
            session.add(row)
            session.flush()
            return _one_page_payload(session, ctx, row)

    @router.get("/{page_id}")
    def get_knowledge_page(
        page_id: str,
        ctx: AuthContext = Depends(require_session),
    ) -> dict[str, Any]:
        with db_session() as session:
            return _one_page_payload(session, ctx, _owned_page(session, ctx, page_id))

    @router.patch("/{page_id}")
    def update_knowledge_page(
        page_id: str,
        body: KnowledgePagePatch,
        ctx: AuthContext = Depends(require_session),
    ) -> dict[str, Any]:
        """Apply an optimistic edit without overwriting another browser tab."""

        with db_session() as session:
            _lock_creator_hierarchy(session, ctx)
            row = _owned_page(session, ctx, page_id)
            if row.revision != body.expected_revision:
                raise _revision_conflict(session, ctx, row, body.expected_revision)

            changed = body.model_fields_set - {"expected_revision"}
            if not changed:
                return _one_page_payload(session, ctx, row)

            parent: KnowledgePageRow | None = None
            if "parent_id" in changed:
                parent = _parent_row(session, ctx, body.parent_id)
                _validate_hierarchy(session, ctx, row_id=row.id, parent=parent)
            if "project_id" in changed:
                _project_id(session, ctx, body.project_id)

            next_title = body.title if "title" in changed else row.title
            next_body = body.body_markdown if "body_markdown" in changed else row.body_markdown
            if next_title is None or next_body is None:
                raise HTTPException(422, "title and body_markdown cannot be null")
            if not next_title and not next_body.strip():
                raise HTTPException(422, "title or body_markdown is required")
            if "body_markdown" in changed:
                _enforce_content_limit(
                    _content_chars(session, ctx, exclude_page_id=row.id),
                    next_body,
                )

            values: dict[str, Any] = {}
            if "title" in changed:
                values["title"] = next_title
            if "body_markdown" in changed:
                values["body_markdown"] = next_body
            if "state" in changed:
                if body.state is None:
                    raise HTTPException(422, "state cannot be null")
                values["state"] = body.state
            if "pinned" in changed:
                if body.pinned is None:
                    raise HTTPException(422, "pinned cannot be null")
                values["pinned"] = body.pinned
            if "tags" in changed:
                if body.tags is None:
                    raise HTTPException(422, "tags cannot be null")
                values["tags"] = body.tags
                values["tags_index"] = _tags_index(body.tags)
            if "parent_id" in changed:
                values["parent_id"] = parent.id if parent is not None else None
            if "project_id" in changed:
                values["project_id"] = body.project_id
            if "position" in changed:
                if body.position is None:
                    raise HTTPException(422, "position cannot be null")
                values["position"] = body.position
            values["revision"] = body.expected_revision + 1
            values["updated_at"] = datetime.now(UTC)
            changed_row = session.execute(
                update(KnowledgePageRow)
                .where(
                    KnowledgePageRow.id == row.id,
                    KnowledgePageRow.org_id == ctx.org_id,
                    KnowledgePageRow.creator_user_id == ctx.user_id,
                    KnowledgePageRow.revision == body.expected_revision,
                )
                .values(**values)
            )
            if getattr(changed_row, "rowcount", 0) != 1:
                session.expire_all()
                current = _owned_page(session, ctx, page_id)
                raise _revision_conflict(session, ctx, current, body.expected_revision)
            session.expire_all()
            return _one_page_payload(
                session,
                ctx,
                _owned_page(session, ctx, page_id),
            )

    @router.delete("/{page_id}", status_code=204)
    def delete_knowledge_page(
        page_id: str,
        expected_revision: int = Query(ge=1),
        ctx: AuthContext = Depends(require_session),
    ) -> None:
        """Delete one page; direct children become root pages via the FK."""

        with db_session() as session:
            _lock_creator_hierarchy(session, ctx)
            row = _owned_page(session, ctx, page_id)
            if row.revision != expected_revision:
                raise _revision_conflict(session, ctx, row, expected_revision)
            now = datetime.now(UTC)
            session.execute(
                update(KnowledgePageRow)
                .where(
                    KnowledgePageRow.org_id == ctx.org_id,
                    KnowledgePageRow.creator_user_id == ctx.user_id,
                    KnowledgePageRow.parent_id == row.id,
                )
                .values(
                    parent_id=None,
                    revision=KnowledgePageRow.revision + 1,
                    updated_at=now,
                )
            )
            deleted = session.execute(
                delete(KnowledgePageRow).where(
                    KnowledgePageRow.id == row.id,
                    KnowledgePageRow.org_id == ctx.org_id,
                    KnowledgePageRow.creator_user_id == ctx.user_id,
                    KnowledgePageRow.revision == expected_revision,
                )
            )
            if getattr(deleted, "rowcount", 0) != 1:
                session.expire_all()
                current = _owned_page(session, ctx, page_id)
                raise _revision_conflict(session, ctx, current, expected_revision)

    # Route-level API-key audits inspect concrete routes instead of lazy
    # include_router wrappers. Match the existing Live Companion integration.
    app.router.routes.extend(router.routes)
