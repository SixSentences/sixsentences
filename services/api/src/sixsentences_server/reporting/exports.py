"""Bibliography exports: BibTeX, RIS, CSL-JSON.

Pure formatters over `WorkRecord` lists (unit-testable without a DB), plus one
helper that loads the works of a persisted run for the CLI/API. Every exported
entry carries the canonical OpenAlex id so a citation always traces back to a
resolvable corpus record (VISION.md #1).
"""

import json
import re
import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.acquisition.upload import is_verified_work_id
from sixsentences_server.core.db import (
    BrowserCapturedPaperMetadataRow,
    DocumentRow,
    ProtocolRow,
    Run,
    SourceRecordRow,
    WorkRow,
)
from sixsentences_server.core.models import ReviewProtocol, WorkRecord
from sixsentences_server.ranking.scorer import rank_works
from sixsentences_server.screening.evidence import final_decisions

# --- helpers ---------------------------------------------------------------

_LATEX_SPECIALS = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}
_NON_KEY = re.compile(r"[^a-z0-9]+")


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _bibtex_escape(text: str) -> str:
    return "".join(_LATEX_SPECIALS.get(ch, ch) for ch in text)


def _surname(display_name: str) -> str:
    """Best-effort family name from an OpenAlex 'Given Family' display name."""
    parts = display_name.strip().split()
    return parts[-1] if parts else ""


def _family_given(display_name: str) -> tuple[str, str]:
    parts = display_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _citation_key(work: WorkRecord, used: set[str]) -> str:
    author = _surname(work.authors[0]) if work.authors else ""
    year = str(work.year) if work.year else ""
    first_word = ""
    for token in _NON_KEY.split(_ascii(work.title).lower()):
        if len(token) > 2:
            first_word = token
            break
    stem = _NON_KEY.sub("", _ascii(author).lower()) + year + first_word
    stem = stem or _ascii(work.id).lower()
    key, suffix = stem, ord("a")
    while key in used:
        key = f"{stem}{chr(suffix)}"
        suffix += 1
    used.add(key)
    return key


# --- formatters ------------------------------------------------------------


def to_bibtex(works: list[WorkRecord]) -> str:
    used: set[str] = set()
    entries: list[str] = []
    for work in works:
        key = _citation_key(work, used)
        fields: list[tuple[str, str]] = [("title", _bibtex_escape(work.title))]
        if work.authors:
            fields.append(("author", " and ".join(_bibtex_escape(a) for a in work.authors)))
        if work.year:
            fields.append(("year", str(work.year)))
        if work.venue:
            fields.append(("journal", _bibtex_escape(work.venue)))
        if work.doi:
            fields.append(("doi", work.doi))
        if is_verified_work_id(work.id):
            # only a REAL index id may claim provenance; an upload's synthetic
            # id must never pretend the record exists there
            fields.append(("note", f"OpenAlex:{work.id}"))
        if work.is_retracted:
            fields.append(("annotation", "RETRACTED"))
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        entries.append(f"@article{{{key},\n{body}\n}}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def to_ris(works: list[WorkRecord]) -> str:
    blocks: list[str] = []
    for work in works:
        lines = ["TY  - JOUR"]
        for author in work.authors:
            family, given = _family_given(author)
            lines.append(f"AU  - {family}, {given}" if given else f"AU  - {family}")
        lines.append(f"TI  - {work.title}")
        if work.year:
            lines.append(f"PY  - {work.year}")
        if work.venue:
            lines.append(f"JO  - {work.venue}")
        if work.doi:
            lines.append(f"DO  - {work.doi}")
        if is_verified_work_id(work.id):
            lines.append(f"ID  - {work.id}")
        lines.append("ER  - ")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + ("\n" if blocks else "")


def to_csl_json(works: list[WorkRecord]) -> str:
    items: list[dict[str, object]] = []
    for work in works:
        item: dict[str, object] = {
            "id": work.id,
            "type": "article-journal",
            "title": work.title,
        }
        if work.authors:
            item["author"] = [
                {"family": fam, "given": giv} if giv else {"family": fam}
                for fam, giv in (_family_given(a) for a in work.authors)
            ]
        if work.year:
            item["issued"] = {"date-parts": [[work.year]]}
        if work.venue:
            item["container-title"] = work.venue
        if work.doi:
            item["DOI"] = work.doi
        if work.is_retracted:
            item["note"] = "RETRACTED"
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)


FORMATS = {"bibtex": to_bibtex, "ris": to_ris, "csl": to_csl_json}
EXTENSIONS = {"bibtex": "bib", "ris": "ris", "csl": "json"}


def render(works: list[WorkRecord], fmt: str) -> str:
    if fmt not in FORMATS:
        raise ValueError(f"unknown export format {fmt!r}; choose from {sorted(FORMATS)}")
    return FORMATS[fmt](works)


# --- persistence helper ----------------------------------------------------


def works_for_run(
    session: Session,
    run_id: int,
    *,
    included_only: bool = False,
    org_id: int | None = None,
    bounded: bool = True,
) -> list[WorkRecord]:
    """Reconstruct the user-facing paper set of a run.

    The complete identification and screening ledgers stay available for
    PRISMA and audit exports. When a run has a paper limit, this helper returns
    only the most relevant eligible papers selected after screening so results,
    chat, exports and downstream workflows share the same bounded output set.

    ``included_only`` applies the effective final decision after the ranked
    working set is selected. Human decisions therefore supersede model
    decisions instead of an earlier include leaking through a later exclude.
    ``org_id`` scopes provenance to a tenant (defence in depth). When it is
    omitted, the run itself supplies the tenant boundary. Browser-captured
    citation metadata remains org-private and is layered over the deliberately
    neutral global ``WorkRow`` only for documents attached to this run.
    """
    run = session.get(Run, run_id)
    if run is None or (org_id is not None and run.org_id != org_id):
        return []
    effective_org_id = run.org_id
    stmt = (
        select(WorkRow)
        .join(SourceRecordRow, SourceRecordRow.work_id == WorkRow.id)
        .where(
            SourceRecordRow.run_id == run_id,
            SourceRecordRow.org_id == effective_org_id,
        )
    )
    rows = session.scalars(stmt).unique().all()
    works = [WorkRecord.model_validate(row.payload) for row in rows]

    capture_metadata_by_work: dict[str, BrowserCapturedPaperMetadataRow] = {}
    captured_rows = session.execute(
        select(DocumentRow.work_id, BrowserCapturedPaperMetadataRow)
        .join(
            BrowserCapturedPaperMetadataRow,
            BrowserCapturedPaperMetadataRow.document_id == DocumentRow.id,
        )
        .where(
            DocumentRow.run_id == run_id,
            DocumentRow.org_id == effective_org_id,
            BrowserCapturedPaperMetadataRow.org_id == effective_org_id,
        )
        .order_by(DocumentRow.id.desc())
    ).all()
    for work_id, metadata in captured_rows:
        capture_metadata_by_work.setdefault(work_id, metadata)
    works = [
        _with_private_capture_metadata(work, capture_metadata_by_work.get(work.id))
        for work in works
    ]

    config: dict[str, Any] = {}
    configured_limit = 0
    config = dict(run.config or {})
    # ``screen_limit`` is the backwards-compatible source for older runs.
    configured_limit = int(config.get("paper_limit") or config.get("screen_limit") or 0)
    if bounded and configured_limit > 0 and works:
        persisted_ids = [
            str(work_id) for work_id in config.get("paper_selection_ids", []) if work_id
        ]
        if persisted_ids or config.get("paper_selection_finalized"):
            by_id = {work.id: work for work in works}
            works = [by_id[work_id] for work_id in persisted_ids if work_id in by_id]
        else:
            protocol: ReviewProtocol | None = None
            if run is not None and run.protocol_id is not None:
                protocol_row = session.get(ProtocolRow, run.protocol_id)
                if protocol_row is not None:
                    protocol = ReviewProtocol.model_validate(protocol_row.payload)
            if protocol is None:
                question = run.question if run is not None else ""
                protocol = ReviewProtocol(question=question, query_string=question)
            ranked = rank_works(works, protocol)
            works = [item.work for item in ranked[:configured_limit]]
    else:
        works.sort(key=lambda work: work.cited_by_count, reverse=True)

    if included_only and works:
        final = final_decisions(session, run_id, effective_org_id)
        works = [
            work
            for work in works
            if (decision := final.get(work.id)) is not None and decision.verdict == "include"
        ]
    return works


def _with_private_capture_metadata(
    work: WorkRecord,
    metadata: BrowserCapturedPaperMetadataRow | None,
) -> WorkRecord:
    """Layer one tenant-owned browser capture over neutral shared metadata."""

    if metadata is None:
        return work
    published_year = (
        int(metadata.published_at[:4])
        if metadata.published_at and metadata.published_at[:4].isdigit()
        else work.year
    )
    authors = list(metadata.authors or [])
    seen = {author.casefold().strip() for author in authors}
    for author in work.authors:
        identity = author.casefold().strip()
        if identity and identity not in seen:
            authors.append(author)
            seen.add(identity)
    return work.model_copy(
        update={
            "title": metadata.title or work.title,
            "authors": authors,
            "doi": metadata.doi or work.doi,
            "year": published_year,
            "abstract": metadata.description or work.abstract,
            "source": "browser_capture",
        }
    )
