"""Tenant-private Browser Capture metadata across downstream consumers."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from io import BytesIO
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    BrowserCapturedPaperMetadataRow,
    BrowserCaptureReceiptRow,
    DocumentRow,
    Org,
    Run,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
    db_session,
)
from sixsentences_server.reporting.exports import works_for_run

PASSWORD = "StrongPass123!"
PRIVATE_TITLE = "Tenant Capture Methods"
PRIVATE_AUTHOR = "Ada Tenant"
PRIVATE_DOI = "10.1234/tenant.capture"


def _authed_client() -> tuple[TestClient, int]:
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "email": "capture-consumers@example.org",
            "password": PASSWORD,
            "org_name": "Capture Consumer Lab",
            "name": "Capture Owner",
        },
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    org_id = int(response.json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"
    return (client, org_id)


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _capture_pdf(client: TestClient, project_id: int, *, capture_id: str) -> dict[str, Any]:
    content = _blank_pdf()
    response = client.post(
        "/browser-capture/papers",
        json={
            "capture_id": capture_id,
            "filename": "private-browser-title.pdf",
            "content_base64": base64.b64encode(content).decode(),
            "source_url": "https://example.org/private-paper.pdf",
            "sha256": hashlib.sha256(content).hexdigest(),
            "project_id": project_id,
            "title": PRIVATE_TITLE,
            "authors": [PRIVATE_AUTHOR],
            "published_at": "2024-03-02",
            "doi": PRIVATE_DOI,
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _attach_completed_run(
    *, document_id: int, org_id: int, project_id: int
) -> tuple[int, str, str]:
    with db_session() as session:
        document = session.get(DocumentRow, document_id)
        assert document is not None and document.org_id == org_id
        run = Run(
            org_id=org_id,
            project_id=project_id,
            question="What does the captured paper report?",
            status="completed",
            config={},
            corpus_version="browser-capture-fixture",
        )
        session.add(run)
        session.flush()
        document.run_id = run.id
        session.add(
            SourceRecordRow(
                org_id=org_id,
                run_id=run.id,
                work_id=document.work_id,
                source="browser_capture",
                corpus_version="browser-capture-fixture",
            )
        )
        session.add(
            ScreeningDecisionRow(
                org_id=org_id,
                run_id=run.id,
                work_id=document.work_id,
                reviewer="human:capture-owner",
                verdict="include",
                reason="Included for the consumer integration regression.",
                quote=None,
            )
        )
        session.flush()
        return (run.id, run.public_id, document.work_id)


def test_private_capture_metadata_reaches_run_writer_report_and_exports(settings: Settings) -> None:
    client, org_id = _authed_client()
    project_id = client.post("/projects", json={"name": "Capture project"}).json()["id"]
    captured = _capture_pdf(client, project_id, capture_id="capture-consumer-pdf-0001")
    document_id = int(captured["item"]["id"])
    run_id, run_public_id, work_id = _attach_completed_run(
        document_id=document_id, org_id=org_id, project_id=project_id
    )
    with db_session() as session:
        global_work = session.get(WorkRow, work_id)
        assert global_work is not None
        assert global_work.title == "Captured paper"
        assert global_work.doi is None
        assert PRIVATE_TITLE not in repr(global_work.payload)
        resolved = works_for_run(session, run_id, org_id=org_id)
        assert [(work.title, work.authors, work.doi, work.year) for work in resolved] == [
            (PRIVATE_TITLE, [PRIVATE_AUTHOR], PRIVATE_DOI, 2024)
        ]
        assert works_for_run(session, run_id, org_id=org_id + 10000) == []
    run_documents = client.get(f"/runs/{run_public_id}/documents")
    assert run_documents.status_code == 200, run_documents.text
    ledger = run_documents.json()[0]
    assert (ledger["title"], ledger["authors"], ledger["doi"], ledger["year"]) == (
        PRIVATE_TITLE,
        [PRIVATE_AUTHOR],
        PRIVATE_DOI,
        2024,
    )
    run_works = client.get(f"/runs/{run_public_id}/works")
    assert run_works.status_code == 200, run_works.text
    work = next(item for item in run_works.json()["works"] if item["id"] == work_id)
    assert (work["title"], work["authors"], work["doi"], work["year"]) == (
        PRIVATE_TITLE,
        [PRIVATE_AUTHOR],
        PRIVATE_DOI,
        2024,
    )
    writer = client.post(
        "/writer",
        json={"title": "Capture manuscript", "template": "blank", "project_id": project_id},
    ).json()
    imported = client.post(
        f"/writer/{writer['public_id']}/sources/library", json={"document_ids": [document_id]}
    )
    assert imported.status_code == 201, imported.text
    source = imported.json()["sources"][0]
    assert (source["title"], source["authors"], source["doi"], source["year"]) == (
        PRIVATE_TITLE,
        [PRIVATE_AUTHOR],
        PRIVATE_DOI,
        2024,
    )
    exported = client.get(f"/runs/{run_public_id}/export?format=bibtex")
    assert exported.status_code == 200, exported.text
    assert all(marker in exported.text for marker in (PRIVATE_TITLE, PRIVATE_AUTHOR, PRIVATE_DOI))
    report = client.post(f"/runs/{run_public_id}/report", json={})
    assert report.status_code == 200, report.text
    included = report.json()["report"]["included"][0]
    assert (included["title"], included["authors"], included["doi"], included["year"]) == (
        PRIVATE_TITLE,
        [PRIVATE_AUTHOR],
        PRIVATE_DOI,
        2024,
    )
    bundle_response = client.get(f"/runs/{run_public_id}/bundle.zip")
    assert bundle_response.status_code == 200, bundle_response.text
    with zipfile.ZipFile(io.BytesIO(bundle_response.content)) as bundle:
        works_csv = bundle.read("works.csv").decode()
    assert PRIVATE_TITLE in works_csv and PRIVATE_DOI in works_csv
    workspace = client.get(f"/projects/{project_id}/workspace").json()
    project_document = next(row for row in workspace["documents"] if row["id"] == document_id)
    assert (
        project_document["title"],
        project_document["authors"],
        project_document["doi"],
        project_document["year"],
    ) == (PRIVATE_TITLE, [PRIVATE_AUTHOR], PRIVATE_DOI, 2024)
    crate = client.get(f"/projects/{project_id}/reproducibility").json()
    assert any(row["id"] == document_id for row in crate["documents"])


def test_writer_import_accepts_mixed_pdf_and_metadata_only_capture(settings: Settings) -> None:
    client, _org_id = _authed_client()
    project_id = client.post("/projects", json={"name": "Mixed captures"}).json()["id"]
    pdf_capture = _capture_pdf(client, project_id, capture_id="capture-consumer-pdf-0002")
    citation = client.post(
        "/browser-capture/citations",
        json={
            "capture_id": "capture-consumer-citation-0001",
            "source_url": "https://example.org/landing-page",
            "canonical_url": "https://example.org/landing-page",
            "title": "Metadata Only Study",
            "authors": ["Grace Citation"],
            "published_at": "2023-11-04",
            "description": "No PDF was uploaded.",
            "doi": "10.1234/metadata.only",
            "project_id": project_id,
            "captured_at": "2026-08-13T18:00:00Z",
            "extension_version": "0.1.0",
            "metadata_fields": ["citation_title", "citation_doi"],
        },
    )
    assert citation.status_code == 201, citation.text
    pdf_id = int(pdf_capture["item"]["id"])
    citation_id = int(citation.json()["item"]["id"])
    writer = client.post(
        "/writer", json={"title": "Mixed source manuscript", "template": "blank"}
    ).json()
    imported = client.post(
        f"/writer/{writer['public_id']}/sources/library",
        json={"document_ids": [pdf_id, citation_id]},
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["created"] == 2
    sources = {row["title"]: row for row in imported.json()["sources"]}
    metadata_source = sources["Metadata Only Study"]
    assert metadata_source["authors"] == ["Grace Citation"]
    assert metadata_source["doi"] == "10.1234/metadata.only"
    assert metadata_source["year"] == 2023
    assert metadata_source["filename"].endswith(".url")
    assert metadata_source["readable"] is False
    workspace = client.get(f"/projects/{project_id}/workspace").json()
    assert {row["id"] for row in workspace["documents"]} == {pdf_id, citation_id}
    crate = client.get(f"/projects/{project_id}/reproducibility").json()
    assert {row["id"] for row in crate["documents"]} == {pdf_id, citation_id}


def test_capture_library_document_survives_reuse_and_run_deletion(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sixsentences_server.api.app as api_module

    monkeypatch.setattr(api_module, "enqueue_job", lambda *args, **kwargs: None)
    client, org_id = _authed_client()
    project_id = client.post("/projects", json={"name": "Reusable capture"}).json()["id"]
    captured = _capture_pdf(client, project_id, capture_id="capture-consumer-reuse-0001")
    original_id = int(captured["item"]["id"])
    runs: list[dict[str, Any]] = []
    clone_ids: list[int] = []
    for index in range(2):
        response = client.post(
            "/runs",
            json={
                "question": f"Ask the reusable capture {index + 1}",
                "mode": "ask",
                "document_ids": [original_id],
            },
        )
        assert response.status_code == 202, response.text
        run = cast(dict[str, Any], response.json())
        runs.append(run)
        documents = client.get(f"/runs/{run['public_id']}/documents")
        assert documents.status_code == 200, documents.text
        assert len(documents.json()) == 1
        clone = documents.json()[0]
        assert clone["id"] != original_id
        assert clone["title"] == PRIVATE_TITLE
        clone_ids.append(int(clone["id"]))
        run_works = client.get(f"/runs/{run['public_id']}/works")
        assert run_works.status_code == 200, run_works.text
        assert any(work["title"] == PRIVATE_TITLE for work in run_works.json()["works"])
        if index == 0:
            with db_session() as session:
                stored_run = session.get(Run, int(run["id"]))
                assert stored_run is not None
                stored_run.status = "completed"
    assert clone_ids[0] != clone_ids[1]
    assert len(client.get(f"/runs/{runs[0]['public_id']}/documents").json()) == 1
    library = client.get("/documents").json()
    assert [row["id"] for row in library] == [original_id]
    with db_session() as session:
        original = session.get(DocumentRow, original_id)
        assert original is not None and original.run_id is None
        metadata = session.scalars(
            select(BrowserCapturedPaperMetadataRow).where(
                BrowserCapturedPaperMetadataRow.org_id == org_id
            )
        ).all()
        assert {row.document_id for row in metadata} == {original_id, *clone_ids}
        receipts = session.scalars(
            select(BrowserCaptureReceiptRow).where(BrowserCaptureReceiptRow.org_id == org_id)
        ).all()
        assert [row.document_id for row in receipts] == [original_id]
    first_delete = client.delete(f"/runs/{runs[0]['public_id']}")
    assert first_delete.status_code == 200, first_delete.text
    assert client.get(f"/documents/{original_id}/file").status_code == 200
    assert len(client.get(f"/runs/{runs[1]['public_id']}/documents").json()) == 1
    assert any(
        work["title"] == PRIVATE_TITLE
        for work in client.get(f"/runs/{runs[1]['public_id']}/works").json()["works"]
    )
    second_delete = client.delete(f"/runs/{runs[1]['public_id']}")
    assert second_delete.status_code == 200, second_delete.text
    assert client.get(f"/documents/{original_id}/file").status_code == 200
    with db_session() as session:
        original = session.get(DocumentRow, original_id)
        assert original is not None and original.run_id is None
        metadata = session.scalars(
            select(BrowserCapturedPaperMetadataRow).where(
                BrowserCapturedPaperMetadataRow.org_id == org_id
            )
        ).all()
        assert [row.document_id for row in metadata] == [original_id]
        receipt = session.scalar(
            select(BrowserCaptureReceiptRow).where(BrowserCaptureReceiptRow.org_id == org_id)
        )
        assert receipt is not None and receipt.document_id == original_id
