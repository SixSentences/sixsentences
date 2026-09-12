"""Shared persistence and normalisation for Zotero and Citavi connectors."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import ReferenceConnectorItemRow, ReferenceConnectorRow


def reference_fingerprint(record: dict[str, Any]) -> str:
    doi = str(record.get("doi") or "").strip().lower()
    if doi:
        identity = f"doi:{doi}"
    else:
        title = re.sub(r"\W+", " ", str(record.get("title") or "").lower()).strip()
        identity = f"title:{title}|year:{record.get('year') or ''}"
    return hashlib.sha256(identity.encode()).hexdigest()


def zotero_record(item: dict[str, Any]) -> dict[str, Any]:
    """Map a Zotero API item without discarding library structure or notes."""
    data = item.get("data") or {}
    creators = data.get("creators") or []
    authors: list[str] = []
    for creator in creators:
        if creator.get("creatorType") not in ("author", "editor", "contributor"):
            continue
        name = str(creator.get("name") or "").strip()
        if not name:
            name = " ".join(
                part
                for part in (
                    str(creator.get("firstName") or "").strip(),
                    str(creator.get("lastName") or "").strip(),
                )
                if part
            )
        if name:
            authors.append(name)
    date = str(data.get("date") or "")
    year_match = re.search(r"(?:19|20)\d{2}", date)
    item_type = str(data.get("itemType") or "article")
    title = str(data.get("title") or "").strip()
    if item_type == "note" and not title:
        title = "Zotero note"
    return {
        "title": title,
        "abstract": str(data.get("abstractNote") or "").strip(),
        "year": int(year_match.group(0)) if year_match else None,
        "doi": str(data.get("DOI") or "").strip().lower(),
        "authors": authors,
        "venue": str(
            data.get("publicationTitle")
            or data.get("conferenceName")
            or data.get("publisher")
            or ""
        ).strip(),
        "reference_type": item_type,
        "citation_key": str(data.get("citationKey") or ""),
        "keywords": [
            str(tag.get("tag") or "").strip()
            for tag in (data.get("tags") or [])
            if str(tag.get("tag") or "").strip()
        ],
        "categories": [str(key) for key in (data.get("collections") or [])],
        "notes": [str(data.get("note") or "").strip()] if data.get("note") else [],
        "url": str(data.get("url") or "").strip(),
        "isbn": str(data.get("ISBN") or "").strip(),
        "issn": str(data.get("ISSN") or "").strip(),
        "attachments": [],
        "parent_key": str(data.get("parentItem") or ""),
        "item_kind": item_type,
    }


def upsert_connector_items(
    session: Session,
    connector: ReferenceConnectorRow,
    records: list[tuple[str, int, dict[str, Any]]],
) -> tuple[int, int]:
    """Insert or version-update records. Returns (created, updated)."""
    existing = {
        row.external_key: row
        for row in session.scalars(
            select(ReferenceConnectorItemRow).where(
                ReferenceConnectorItemRow.connector_id == connector.id
            )
        ).all()
    }
    created = updated = 0
    for external_key, version, payload in records:
        fingerprint = reference_fingerprint(payload)
        row = existing.get(external_key)
        if row is None:
            row = ReferenceConnectorItemRow(
                org_id=connector.org_id,
                connector_id=connector.id,
                external_key=external_key,
                version=version,
                fingerprint=fingerprint,
                payload=payload,
            )
            session.add(row)
            created += 1
        elif version >= row.version and (
            row.payload != payload or row.deleted or row.version != version
        ):
            row.version = version
            row.fingerprint = fingerprint
            row.payload = payload
            row.deleted = False
            updated += 1
    session.flush()
    connector.item_count = int(
        session.scalar(
            select(func.count(ReferenceConnectorItemRow.id)).where(
                ReferenceConnectorItemRow.connector_id == connector.id,
                ReferenceConnectorItemRow.deleted.is_(False),
            )
        )
        or 0
    )
    connector.last_synced_at = datetime.now(UTC)
    connector.last_error = ""
    connector.status = "connected"
    return created, updated


def mark_deleted(session: Session, connector_id: int, keys: list[str]) -> int:
    if not keys:
        return 0
    rows = session.scalars(
        select(ReferenceConnectorItemRow).where(
            ReferenceConnectorItemRow.connector_id == connector_id,
            ReferenceConnectorItemRow.external_key.in_(keys),
            ReferenceConnectorItemRow.deleted.is_(False),
        )
    ).all()
    for row in rows:
        row.deleted = True
    return len(rows)


def citavi_records(
    text_records: list[dict[str, Any]],
) -> list[tuple[str, int, dict[str, Any]]]:
    """Stable keys make repeated Citavi exports behave like sync, not duplication."""
    result: list[tuple[str, int, dict[str, Any]]] = []
    seen: dict[str, int] = {}
    for record in text_records:
        preferred = str(record.get("citation_key") or "").strip()
        stem = preferred or reference_fingerprint(record)[:20]
        seen[stem] = seen.get(stem, 0) + 1
        key = stem if seen[stem] == 1 else f"{stem}-{seen[stem]}"
        result.append((key, 1, record))
    return result


def connector_records(
    session: Session, connector_id: int, *, bibliographic_only: bool = True
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ReferenceConnectorItemRow)
        .where(
            ReferenceConnectorItemRow.connector_id == connector_id,
            ReferenceConnectorItemRow.deleted.is_(False),
        )
        .order_by(ReferenceConnectorItemRow.updated_at.desc())
    ).all()
    records = [dict(row.payload or {}) for row in rows]
    if bibliographic_only:
        records = [
            record
            for record in records
            if record.get("title")
            and record.get("item_kind") not in ("attachment", "note", "annotation")
        ]
    return records


def export_payload(records: list[dict[str, Any]]) -> str:
    """Stable JSON for audit/debug endpoints without exposing credentials."""
    return json.dumps(records, ensure_ascii=False, sort_keys=True)
