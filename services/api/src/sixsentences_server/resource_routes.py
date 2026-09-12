"""Tenant-isolated research, library, data, writing and knowledge routes."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sixsentences.querylang.parser import QueryParseError, parse_query
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.dependencies import (
    Principal,
    get_principal,
    get_session,
    tenant_query,
    tenant_record,
)
from sixsentences_server.models import (
    Brainstorm,
    Dataset,
    Figure,
    Job,
    KnowledgePage,
    LibraryItem,
    OutboxEmail,
    Project,
    ResearchRun,
    WriterDocument,
)
from sixsentences_server.research_data import DatasetImportError, parse_dataset
from sixsentences_server.schemas import (
    BrainstormRequest,
    DatasetRequest,
    FigureRequest,
    KnowledgePatch,
    KnowledgeRequest,
    LibraryItemRequest,
    MailRequest,
    ProjectPatch,
    ProjectRequest,
    RunRequest,
    WriterPatch,
    WriterRequest,
)
from sixsentences_server.serialization import (
    brainstorm,
    dataset,
    figure,
    knowledge,
    library_item,
    project,
    research_run,
    timestamp,
    writer,
)

router = APIRouter(tags=["workspace"])


def _project(
    session: Session,
    project_id: str | None,
    principal: Principal,
) -> Project | None:
    if project_id is None:
        return None
    return tenant_record(session, Project, project_id, principal)


def _project_id(session: Session, internal_id: int | None) -> str | None:
    if internal_id is None:
        return None
    row = session.get(Project, internal_id)
    return row.public_id if row is not None else None


def _with_project_id(session: Session, value: dict[str, Any]) -> dict[str, Any]:
    value["project_id"] = _project_id(session, value.get("project_id"))
    return value


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = Project(org_id=principal.org_id, name=payload.name, description=payload.description)
    session.add(row)
    session.flush()
    return project(row)


@router.get("/projects")
def list_projects(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(tenant_query(Project, principal).order_by(Project.created_at.desc()))
    return [project(row) for row in rows]


@router.get("/projects/{record_id}")
def get_project(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return project(tenant_record(session, Project, record_id, principal))


@router.patch("/projects/{record_id}")
def update_project(
    record_id: str,
    payload: ProjectPatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = tenant_record(session, Project, record_id, principal)
    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    session.flush()
    return project(row)


@router.delete("/projects/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, Project, record_id, principal))


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
@router.post("/projects/{path_project_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: RunRequest,
    request: Request,
    path_project_id: str | None = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    selected_project = path_project_id or payload.project_id
    project_row = _project(session, selected_project, principal)
    if (
        payload.year_from is not None
        and payload.year_to is not None
        and payload.year_from > payload.year_to
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid year range")
    try:
        parse_query(payload.query)
    except QueryParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    row = ResearchRun(
        org_id=principal.org_id,
        project_id=project_row.id if project_row is not None else None,
        question=payload.question,
        query=payload.query,
    )
    session.add(row)
    session.flush()
    session.add(
        Job(
            org_id=principal.org_id,
            kind="research.run",
            payload={
                "run_id": row.public_id,
                "question": payload.question,
                "query": payload.query,
                "limit": payload.limit,
                "year_from": payload.year_from,
                "year_to": payload.year_to,
            },
            max_attempts=request.app.state.settings.worker_max_attempts,
        )
    )
    session.flush()
    return _with_project_id(session, research_run(row))


@router.get("/runs")
def list_runs(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        tenant_query(ResearchRun, principal).order_by(ResearchRun.created_at.desc())
    )
    return [_with_project_id(session, research_run(row)) for row in rows]


@router.get("/runs/{record_id}")
def get_run(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _with_project_id(
        session, research_run(tenant_record(session, ResearchRun, record_id, principal))
    )


@router.post("/library", status_code=status.HTTP_201_CREATED)
@router.post("/documents", status_code=status.HTTP_201_CREATED)
def create_library_item(
    payload: LibraryItemRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project_row = _project(session, payload.project_id, principal)
    row = LibraryItem(
        org_id=principal.org_id,
        project_id=project_row.id if project_row is not None else None,
        kind=payload.kind,
        title=payload.title,
        abstract=payload.abstract,
        content=payload.content,
        identifiers=payload.identifiers,
        provenance=payload.provenance,
    )
    session.add(row)
    session.flush()
    return _with_project_id(session, library_item(row))


@router.get("/library")
@router.get("/documents")
def list_library_items(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        tenant_query(LibraryItem, principal).order_by(LibraryItem.created_at.desc())
    )
    return [_with_project_id(session, library_item(row)) for row in rows]


@router.get("/library/{record_id}")
@router.get("/documents/{record_id}")
def get_library_item(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _with_project_id(
        session, library_item(tenant_record(session, LibraryItem, record_id, principal))
    )


@router.delete("/library/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/documents/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_library_item(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, LibraryItem, record_id, principal))


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
def create_dataset(
    payload: DatasetRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project_row = _project(session, payload.project_id, principal)
    if Path(payload.filename).name != payload.filename:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "filename must be a basename")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid base64 data") from exc
    if len(content) > request.app.state.settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "dataset exceeds local limit")
    try:
        parsed = parse_dataset(content, payload.filename)
    except DatasetImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    row = Dataset(
        org_id=principal.org_id,
        project_id=project_row.id if project_row is not None else None,
        name=payload.name,
        source_format=parsed.format,
        rows=parsed.rows,
        profile=parsed.profile,
    )
    session.add(row)
    session.flush()
    return _with_project_id(session, dataset(row, include_rows=True))


@router.get("/datasets")
def list_datasets(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(tenant_query(Dataset, principal).order_by(Dataset.created_at.desc()))
    return [_with_project_id(session, dataset(row)) for row in rows]


@router.get("/datasets/{record_id}")
def get_dataset(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _with_project_id(
        session, dataset(tenant_record(session, Dataset, record_id, principal), include_rows=True)
    )


@router.delete("/datasets/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, Dataset, record_id, principal))


@router.post("/figures", status_code=status.HTTP_201_CREATED)
def create_figure(
    payload: FigureRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    dataset_row = (
        tenant_record(session, Dataset, payload.dataset_id, principal)
        if payload.dataset_id is not None
        else None
    )
    row = Figure(
        org_id=principal.org_id,
        dataset_id=dataset_row.id if dataset_row is not None else None,
        title=payload.title,
        kind=payload.kind,
        specification=payload.specification,
    )
    session.add(row)
    session.flush()
    value = figure(row)
    value["dataset_id"] = dataset_row.public_id if dataset_row is not None else None
    return value


@router.get("/figures")
def list_figures(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(tenant_query(Figure, principal).order_by(Figure.created_at.desc()))
    output: list[dict[str, Any]] = []
    for row in rows:
        value = figure(row)
        source = session.get(Dataset, row.dataset_id) if row.dataset_id is not None else None
        value["dataset_id"] = source.public_id if source is not None else None
        output.append(value)
    return output


@router.get("/figures/{record_id}")
def get_figure(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = tenant_record(session, Figure, record_id, principal)
    value = figure(row)
    source = session.get(Dataset, row.dataset_id) if row.dataset_id is not None else None
    value["dataset_id"] = source.public_id if source is not None else None
    return value


@router.delete("/figures/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_figure(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, Figure, record_id, principal))


@router.post("/writer", status_code=status.HTTP_201_CREATED)
def create_writer(
    payload: WriterRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project_row = _project(session, payload.project_id, principal)
    row = WriterDocument(
        org_id=principal.org_id,
        project_id=project_row.id if project_row is not None else None,
        title=payload.title,
        content=payload.content,
        citations=payload.citations,
    )
    session.add(row)
    session.flush()
    return _with_project_id(session, writer(row))


@router.get("/writer")
def list_writers(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        tenant_query(WriterDocument, principal).order_by(WriterDocument.updated_at.desc())
    )
    return [_with_project_id(session, writer(row)) for row in rows]


@router.get("/writer/{record_id}")
def get_writer(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _with_project_id(
        session, writer(tenant_record(session, WriterDocument, record_id, principal))
    )


@router.patch("/writer/{record_id}")
def update_writer(
    record_id: str,
    payload: WriterPatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = tenant_record(session, WriterDocument, record_id, principal)
    if payload.title is not None:
        row.title = payload.title
    if payload.content is not None:
        row.content = payload.content
    if payload.citations is not None:
        row.citations = payload.citations
    session.flush()
    return _with_project_id(session, writer(row))


@router.delete("/writer/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_writer(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, WriterDocument, record_id, principal))


@router.post("/knowledge/pages", status_code=status.HTTP_201_CREATED)
def create_knowledge_page(
    payload: KnowledgeRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project_row = _project(session, payload.project_id, principal)
    row = KnowledgePage(
        org_id=principal.org_id,
        project_id=project_row.id if project_row is not None else None,
        title=payload.title,
        body=payload.body,
    )
    session.add(row)
    session.flush()
    return _with_project_id(session, knowledge(row))


@router.get("/knowledge/pages")
def list_knowledge_pages(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        tenant_query(KnowledgePage, principal).order_by(KnowledgePage.updated_at.desc())
    )
    return [_with_project_id(session, knowledge(row)) for row in rows]


@router.get("/knowledge/pages/{record_id}")
def get_knowledge_page(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _with_project_id(
        session, knowledge(tenant_record(session, KnowledgePage, record_id, principal))
    )


@router.patch("/knowledge/pages/{record_id}")
def update_knowledge_page(
    record_id: str,
    payload: KnowledgePatch,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = tenant_record(session, KnowledgePage, record_id, principal)
    if row.revision != payload.expected_revision:
        raise HTTPException(status.HTTP_409_CONFLICT, "knowledge page changed")
    if payload.title is not None:
        row.title = payload.title
    if payload.body is not None:
        row.body = payload.body
    row.revision += 1
    session.flush()
    return _with_project_id(session, knowledge(row))


@router.delete("/knowledge/pages/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_page(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    session.delete(tenant_record(session, KnowledgePage, record_id, principal))


@router.post("/brainstorms", status_code=status.HTTP_202_ACCEPTED)
def create_brainstorm(
    payload: BrainstormRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project_row = _project(session, payload.project_id, principal)
    row = Brainstorm(
        org_id=principal.org_id,
        project_id=project_row.id if project_row is not None else None,
        prompt=payload.prompt,
    )
    session.add(row)
    session.flush()
    session.add(
        Job(
            org_id=principal.org_id,
            kind="brainstorm.generate",
            payload={"brainstorm_id": row.public_id},
            max_attempts=request.app.state.settings.worker_max_attempts,
        )
    )
    session.flush()
    return _with_project_id(session, brainstorm(row))


@router.get("/brainstorms")
def list_brainstorms(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        tenant_query(Brainstorm, principal).order_by(Brainstorm.created_at.desc())
    )
    return [_with_project_id(session, brainstorm(row)) for row in rows]


@router.get("/brainstorms/{record_id}")
def get_brainstorm(
    record_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _with_project_id(
        session, brainstorm(tenant_record(session, Brainstorm, record_id, principal))
    )


@router.post("/mail/messages", status_code=status.HTTP_202_ACCEPTED)
def queue_mail(
    payload: MailRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = OutboxEmail(
        org_id=principal.org_id,
        recipient=payload.recipient,
        subject=payload.subject,
        text_body=payload.text_body,
    )
    session.add(row)
    session.flush()
    session.add(
        Job(
            org_id=principal.org_id,
            kind="mail.send",
            payload={"message_id": row.public_id},
            max_attempts=request.app.state.settings.worker_max_attempts,
        )
    )
    session.flush()
    return {"id": row.public_id, "status": row.status, "created_at": timestamp(row.created_at)}


@router.get("/mail/messages")
def list_mail(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(OutboxEmail)
        .where(OutboxEmail.org_id == principal.org_id)
        .order_by(OutboxEmail.created_at.desc())
    )
    return [
        {
            "id": row.public_id,
            "recipient": row.recipient,
            "subject": row.subject,
            "status": row.status,
            "attempts": row.attempts,
            "error": row.error,
            "created_at": timestamp(row.created_at),
            "sent_at": timestamp(row.sent_at),
        }
        for row in rows
    ]
