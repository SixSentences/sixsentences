"""Org-private Library citation copy/download and capture invalidation cursors."""

from __future__ import annotations

import base64
import hashlib
import re
from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.auth import add_member, authenticate
from sixsentences_server.core.db import (
    BrowserCapturedPaperMetadataRow,
    DocumentRow,
    Run,
    User,
    WorkRow,
    db_session,
)
from sixsentences_server.reporting.library_citations import (
    CITATION_FORMATS,
    LibraryCitationRecord,
    record_from_metadata,
    render_citation,
    render_citation_export,
)

PASSWORD = "StrongPass123!"


def _client(app: FastAPI, *, email: str, org: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "org_name": org, "name": "Test"},
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _citation_payload(
    identity: str,
    *,
    title: str = "Attention & Control",
    authors: list[str] | None = None,
    published_at: str | None = "2024-03-12",
) -> dict[str, object]:
    return {
        "capture_id": f"citation-export-{identity}",
        "source_url": f"https://journals.example.org/{identity}",
        "canonical_url": f"https://journals.example.org/{identity}",
        "title": title,
        "authors": ["Ada Author", "Grace Researcher"] if authors is None else authors,
        "published_at": published_at,
        "description": "Reviewed metadata.",
        "doi": f"10.1234/{identity}",
        "project_id": None,
        "captured_at": "2026-08-14T08:00:00Z",
        "extension_version": "0.1.9",
        "metadata_fields": ["citation_title", "citation_doi"],
        "metadata": {
            "container_title": "Journal of Reliable Systems",
            "volume": "12",
            "issue": "3",
            "pages": "41-59",
            "publisher": "Example Press",
            "language": "en",
            "issn": "1234-5678",
            "item_type": "article",
        },
    }


def _web_payload(identity: str) -> dict[str, object]:
    return {
        "capture_id": f"revision-web-{identity}",
        "url": f"https://example.org/source/{identity}",
        "canonical_url": f"https://example.org/source/{identity}",
        "title": "A captured web source",
        "site_name": "Example",
        "authors": ["Ada Author"],
        "published_at": "2026-08-14",
        "description": "Allowlisted metadata only.",
        "selected_excerpt": "",
        "source_kind": "web",
        "doi": "",
        "project_id": None,
        "captured_at": "2026-08-14T08:00:00Z",
        "extension_version": "0.1.9",
        "metadata_fields": ["citation_title"],
    }


def _paper_payload(identity: str) -> dict[str, object]:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    content = buffer.getvalue()
    return {
        "capture_id": f"revision-paper-{identity}",
        "filename": "Captured paper.pdf",
        "content_base64": base64.b64encode(content).decode(),
        "source_url": f"https://papers.example.org/{identity}.pdf",
        "sha256": hashlib.sha256(content).hexdigest(),
        "project_id": None,
        "title": "Captured research paper",
        "authors": ["Ada Author"],
        "published_at": "2025-06-01",
        "doi": f"10.5678/{identity}",
    }


def _save_citation(client: TestClient, identity: str, **changes: object) -> int:
    payload = _citation_payload(identity)
    payload.update(changes)
    response = client.post("/browser-capture/citations", json=payload)
    assert response.status_code == 201, response.text
    return int(response.json()["item"]["id"])


def test_library_citations_return_all_copy_formats_and_download_selected_batch(
    settings: Settings,
) -> None:
    app = create_app()
    client = _client(app, email="citations@example.org", org="Citation Lab")
    first_id = _save_citation(
        client, "one", title="Shared Beginning: One & {Safety}", authors=["Ada Author"]
    )
    second_id = _save_citation(client, "two", title="Shared Beginning: Two", authors=["Ada Author"])
    copy_response = client.get(f"/documents/{first_id}/citations")
    assert copy_response.status_code == 200, copy_response.text
    assert copy_response.headers["cache-control"] == "private, no-store"
    copied = copy_response.json()
    assert copied["document_id"] == first_id
    assert copied["title"] == "Shared Beginning: One & {Safety}"
    assert copied["metadata_revision"].startswith("sha256:")
    assert copied["missing_fields"] == []
    assert set(copied["citations"]) == set(CITATION_FORMATS)
    assert all(set(item) == {"value"} and item["value"] for item in copied["citations"].values())
    assert "Author, A. (2024)." in copied["citations"]["apa"]["value"]
    assert "Author, Ada. 2024." in copied["citations"]["chicago"]["value"]
    export = client.post(
        "/documents/citations/export",
        json={"document_ids": [first_id, first_id, second_id], "format": "bibtex"},
    )
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "application/x-bibtex; charset=utf-8"
    assert (
        export.headers["content-disposition"] == 'attachment; filename="sixsentences-citations.bib"'
    )
    assert export.headers["cache-control"] == "private, no-store"
    assert export.headers["x-content-type-options"] == "nosniff"
    assert export.text.count("@article{") == 2
    assert "@article{author2024shared," in export.text
    assert "@article{author2024shareda," in export.text
    assert "title = {Shared Beginning: One \\& \\{Safety\\}}" in export.text
    assert copied["citations"]["bibtex"]["value"].count("  author = {") == 1
    apa_export = client.post(
        "/documents/citations/export", json={"document_ids": [second_id], "format": "apa"}
    )
    assert apa_export.status_code == 200
    assert apa_export.headers["content-type"] == "text/plain; charset=utf-8"
    assert (
        apa_export.headers["content-disposition"]
        == 'attachment; filename="sixsentences-citations-apa.txt"'
    )


def test_library_citations_do_not_invent_missing_metadata(settings: Settings) -> None:
    app = create_app()
    client = _client(app, email="missing@example.org", org="Missing Citation Lab")
    document_id = _save_citation(
        client, "missing", title="Title Only", authors=[], published_at=None, metadata=None, doi=""
    )
    response = client.get(f"/documents/{document_id}/citations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["missing_fields"] == ["authors", "year", "container_title"]
    assert body["citations"]["apa"]["value"].startswith("Title Only. (n.d.).")
    assert "Unknown" not in " ".join(item["value"] for item in body["citations"].values())


def test_library_citations_are_tenant_scoped_and_batch_authorization_is_atomic(
    settings: Settings,
) -> None:
    app = create_app()
    first = _client(app, email="first-citation@example.org", org="First Citation Org")
    second = _client(app, email="second-citation@example.org", org="Second Citation Org")
    first_id = _save_citation(first, "tenant-first")
    second_id = _save_citation(second, "tenant-second")
    assert second.get(f"/documents/{first_id}/citations").status_code == 404
    mixed = second.post(
        "/documents/citations/export", json={"document_ids": [second_id, first_id], "format": "ris"}
    )
    assert mixed.status_code == 404
    assert "First Citation Org" not in mixed.text
    own = second.post(
        "/documents/citations/export", json={"document_ids": [second_id], "format": "ris"}
    )
    assert own.status_code == 200
    assert own.text.count("ER  - ") == 1


def test_library_citations_keep_private_companion_clones_creator_only(settings: Settings) -> None:
    app = create_app()
    owner = _client(app, email="clone-owner@example.org", org="Private Clone Lab")
    owner_identity = owner.get("/auth/me").json()
    with db_session() as session:
        add_member(
            session, int(owner_identity["org_id"]), "clone-member@example.org", PASSWORD, "member"
        )
        member_token = authenticate(session, "clone-member@example.org", PASSWORD)
        owner_user = session.get(User, int(owner_identity["user_id"]))
        member_user = session.scalar(select(User).where(User.email == "clone-member@example.org"))
        assert owner_user is not None and member_user is not None
        work = WorkRow(
            id="WPRIVATECITATIONCLONE",
            title="Shared paper title",
            year=2025,
            payload={
                "id": "WPRIVATECITATIONCLONE",
                "title": "Shared paper title",
                "year": 2025,
                "authors": ["Ada Author"],
                "venue": "Shared Journal",
            },
        )
        session.add(work)
        session.flush()
        shared = DocumentRow(
            org_id=owner_user.org_id,
            work_id=work.id,
            status="retrieved",
            checksum="a" * 64,
            storage_path="/test/shared-paper.pdf",
            source="upload",
        )
        owner_run = Run(
            org_id=owner_user.org_id,
            question="Private owner paper",
            status="completed",
            config={"surface": "companion_paper", "owner_user_id": owner_user.id},
        )
        member_run = Run(
            org_id=owner_user.org_id,
            question="Private member paper",
            status="completed",
            config={"surface": "companion_paper", "owner_user_id": member_user.id},
        )
        session.add_all([shared, owner_run, member_run])
        session.flush()
        owner_clone = DocumentRow(
            org_id=owner_user.org_id,
            run_id=owner_run.id,
            work_id=work.id,
            status="retrieved",
            checksum="a" * 64,
            storage_path="/test/shared-paper.pdf",
            source="companion",
        )
        member_clone = DocumentRow(
            org_id=owner_user.org_id,
            run_id=member_run.id,
            work_id=work.id,
            status="retrieved",
            checksum="a" * 64,
            storage_path="/test/shared-paper.pdf",
            source="companion",
        )
        session.add_all([owner_clone, member_clone])
        session.flush()
        session.add(
            BrowserCapturedPaperMetadataRow(
                org_id=owner_user.org_id,
                user_id=owner_user.id,
                document_id=owner_clone.id,
                title="FOREIGN PRIVATE PAPER TITLE",
                source_url="https://private.example.org/owner",
                canonical_url="https://private.example.org/owner",
                canonical_url_hash="b" * 64,
            )
        )
        shared_id = shared.id
        owner_clone_id = owner_clone.id
        member_clone_id = member_clone.id
    member = TestClient(app, headers={"Authorization": f"Bearer {member_token}"})
    assert member.get(f"/documents/{owner_clone_id}/citations").status_code == 404
    mixed = member.post(
        "/documents/citations/export",
        json={"document_ids": [shared_id, owner_clone_id], "format": "apa"},
    )
    assert mixed.status_code == 404
    own_copy = member.get(f"/documents/{member_clone_id}/citations")
    assert own_copy.status_code == 200, own_copy.text
    assert own_copy.json()["document_id"] == shared_id
    assert "FOREIGN PRIVATE" not in own_copy.text
    deduplicated = member.post(
        "/documents/citations/export",
        json={"document_ids": [shared_id, member_clone_id], "format": "ris"},
    )
    assert deduplicated.status_code == 200, deduplicated.text
    assert deduplicated.text.count("ER  - ") == 1


def test_library_citation_batch_validation_is_bounded(settings: Settings) -> None:
    app = create_app()
    client = _client(app, email="bounds@example.org", org="Citation Bounds")
    for payload in (
        {"document_ids": [], "format": "apa"},
        {"document_ids": list(range(1, 102)), "format": "apa"},
        {"document_ids": [0], "format": "apa"},
        {"document_ids": [1], "format": "unknown"},
        {"document_ids": [1], "format": "apa", "extra": True},
    ):
        response = client.post("/documents/citations/export", json=payload)
        assert response.status_code == 422, response.text


def test_citation_renderers_strip_control_lines_and_escape_bibtex() -> None:
    record = record_from_metadata(
        9,
        {
            "title": "Line one\r\nER  - injected & {unsafe}_value\\tail",
            "authors": ["Doe, Jane\nTI  - injected"],
            "year": 2026,
            "canonical_url": "javascript:alert(1)",
        },
    )
    ris = render_citation(record, "ris")
    bibtex = render_citation(record, "bibtex")
    assert ris.count("\nER  - ") == 1
    assert "\nTI  - injected" not in ris
    assert "UR  - " not in ris
    assert "\\& \\{unsafe\\}\\_value{\\textbackslash{}}tail" in bibtex
    assert "\r" not in bibtex and "\nER  - injected" not in bibtex


def test_internal_placeholder_title_is_reported_missing_and_never_exported(
    settings: Settings,
) -> None:
    app = create_app()
    client = _client(app, email="sentinel@example.org", org="Sentinel Citation Lab")
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        work = WorkRow(
            id="WSENTINELCITATION",
            title="Uploaded document",
            payload={"id": "WSENTINELCITATION", "title": "Uploaded document"},
        )
        session.add(work)
        session.flush()
        document = DocumentRow(
            org_id=org_id,
            work_id=work.id,
            status="not_retrieved",
            source="browser_capture",
            url="https://example.org/missing-title",
        )
        session.add(document)
        session.flush()
        document_id = document.id
    response = client.get(f"/documents/{document_id}/citations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == ""
    assert "title" in body["missing_fields"]
    assert all(
        "Uploaded document" not in citation["value"] for citation in body["citations"].values()
    )
    for sentinel in (
        "Captured paper",
        "Captured paper citation",
        "Untitled",
        "Untitled paper",
        "Untitled source",
    ):
        assert record_from_metadata(1, {"title": sentinel}).title == ""
    assert (
        record_from_metadata(1, {"title": "WSYNTHETIC"}, internal_titles=("WSYNTHETIC",)).title
        == ""
    )


def test_bibtex_export_keys_are_safe_and_unique_at_the_full_batch_bound() -> None:
    records = [
        record_from_metadata(
            document_id,
            {
                "title": f"Same title {document_id}",
                "authors": ["Ada Author"],
                "year": 2024,
                "citation_key": "chosen-key",
            },
        )
        for document_id in range(1, 101)
    ]
    exported = render_citation_export(records, "bibtex")
    keys = re.findall("@article\\{([^,]+),", exported)
    assert len(keys) == 100
    assert len(set(keys)) == 100
    assert keys[:3] == ["chosenkey", "chosenkeya", "chosenkeyb"]
    assert all(re.fullmatch("[a-z0-9]+", key) for key in keys)


def test_apa_and_chicago_author_date_golden_author_cardinalities() -> None:

    def record(authors: list[str]) -> LibraryCitationRecord:
        return record_from_metadata(
            1,
            {
                "title": "A Reliable Result",
                "authors": authors,
                "year": 2026,
                "item_type": "preprint",
            },
        )

    assert render_citation(record([]), "apa") == "A Reliable Result. (2026)."
    assert render_citation(record(["Ada Author"]), "apa").startswith("Author, A. (2026).")
    assert render_citation(record(["Ada Author", "Grace Researcher"]), "apa").startswith(
        "Author, A., & Researcher, G. (2026)."
    )
    assert render_citation(
        record(["Ada Author", "Grace Researcher", "Alan Turing"]), "apa"
    ).startswith("Author, A., Researcher, G., & Turing, A. (2026).")
    twenty_one = [f"Given{i} Family{i}" for i in range(1, 22)]
    apa_many = render_citation(record(twenty_one), "apa")
    assert "Family19, G., … Family21, G. (2026)." in apa_many
    assert "Family20" not in apa_many
    assert render_citation(record([]), "chicago").startswith("“A Reliable Result.” 2026.")
    assert render_citation(record(["Ada Author"]), "chicago").startswith("Author, Ada. 2026.")
    assert render_citation(record(["Ada Author", "Grace Researcher"]), "chicago").startswith(
        "Author, Ada, and Grace Researcher. 2026."
    )
    assert render_citation(
        record(["Ada Author", "Grace Researcher", "Alan Turing"]), "chicago"
    ).startswith("Author, Ada, Grace Researcher, and Alan Turing. 2026.")
    chicago_many = render_citation(record([*twenty_one[:7]]), "chicago")
    assert chicago_many.startswith("Family1, Given1, Given2 Family2, Given3 Family3, et al. 2026.")
    assert "Family4" not in chicago_many
