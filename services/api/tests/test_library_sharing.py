"""Account-bound, read-only sharing of Library papers and web sources."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.acquisition.store import LocalDocumentStore
from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    BrowserCapturedPaperMetadataRow,
    DocumentAnnotationRow,
    DocumentRow,
    LibraryAccessGrantRow,
    LibraryWebSourceRow,
    Org,
    Project,
    Run,
    User,
    WorkRow,
    db_session,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus

PASSWORD = "StrongPass123!"


def _register(app: FastAPI, *, email: str, org: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD, "org_name": org, "name": org}
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        user.email_verified_at = user.email_verified_at or datetime.now(UTC)
    return client


def _share_body(email: str, *, project_id: int | None = None) -> dict[str, object]:
    return {
        "email": email,
        "scope": "project" if project_id is not None else "library",
        "project_id": project_id,
        "role": "viewer",
        "rights_confirmed": True,
    }


def _identity(client: TestClient) -> tuple[int, int]:
    identity = client.get("/auth/me")
    assert identity.status_code == 200, identity.text
    payload = identity.json()
    return (int(payload["user_id"]), int(payload["org_id"]))


def test_owner_create_list_replay_revoke_and_cross_tenant_boundary(corpus: DuckDBCorpus) -> None:
    del corpus
    app = create_app()
    owner = _register(app, email="sharing-owner@example.org", org="Sharing Owner")
    recipient = _register(app, email="sharing-recipient@example.org", org="Recipient")
    outsider = _register(app, email="sharing-outsider@example.org", org="Outsider")
    created = owner.post("/library/shares", json=_share_body("sharing-recipient@example.org"))
    assert created.status_code == 201, created.text
    share = created.json()
    assert len(share["id"]) == 16
    assert share["scope"] == "library"
    assert share["role"] == "viewer"
    assert share["rights_statement_version"] == "library-share-v1"
    assert created.headers["cache-control"] == "private, no-store"
    replay = owner.post("/library/shares", json=_share_body("sharing-recipient@example.org"))
    assert replay.status_code == 200, replay.text
    assert replay.json() == share
    assert owner.get("/library/shares").json() == [share]
    received = recipient.get("/library/shares/received")
    assert received.status_code == 200, received.text
    assert received.headers["cache-control"] == "private, no-store"
    assert [row["id"] for row in received.json()] == [share["id"]]
    assert outsider.get(f"/library/shares/received/{share['id']}/documents").status_code == 404
    revoked = owner.delete(f"/library/shares/{share['id']}")
    assert revoked.status_code == 204, revoked.text
    assert owner.get("/library/shares").json() == []
    assert recipient.get("/library/shares/received").json() == []
    assert recipient.get(f"/library/shares/received/{share['id']}/documents").status_code == 404


def test_shared_pdf_requires_ledger_storage_path_and_emits_hardened_headers(
    settings: Settings, corpus: DuckDBCorpus
) -> None:
    del corpus
    app = create_app()
    owner = _register(app, email="pdf-owner@example.org", org="PDF Owner")
    recipient = _register(app, email="pdf-recipient@example.org", org="PDF Recipient")
    _owner_user_id, owner_org_id = _identity(owner)
    pdf = b"%PDF-1.4\nsharing fixture\n%%EOF"
    checksum, storage_path = LocalDocumentStore(settings.documents_dir).put(pdf)
    globally_stored_pdf = b"%PDF-1.4\nglobal blob without scoped ledger path\n%%EOF"
    global_checksum, _global_storage_path = LocalDocumentStore(settings.documents_dir).put(
        globally_stored_pdf
    )
    with db_session() as session:
        stored_work = WorkRow(
            id="W-share-stored",
            doi="10.1234/stored",
            title="Stored shared paper",
            year=2026,
            payload={"authors": ["Ada Researcher"]},
        )
        citation_work = WorkRow(
            id="W-share-citation",
            doi="10.1234/citation",
            title="Citation without ledger file",
            year=2025,
            payload={"authors": ["Grace Researcher"]},
        )
        session.add_all((stored_work, citation_work))
        session.flush()
        stored = DocumentRow(
            org_id=owner_org_id,
            run_id=None,
            project_id=None,
            work_id=stored_work.id,
            status="retrieved",
            source="open_access",
            legal_basis="open_access",
            content_type="application/pdf",
            checksum=checksum,
            byte_size=len(pdf),
            storage_path=storage_path,
            text_status="parsed",
        )
        citation = DocumentRow(
            org_id=owner_org_id,
            run_id=None,
            project_id=None,
            work_id=citation_work.id,
            status="not_retrieved",
            source="browser_capture",
            legal_basis="citation_only",
            url="https://example.org/citation",
            content_type="application/pdf",
            checksum=global_checksum,
            byte_size=0,
            storage_path=None,
            text_status="not_retrieved",
        )
        session.add_all((stored, citation))
        session.flush()
        stored_id, citation_id = (stored.id, citation.id)
    created = owner.post("/library/shares", json=_share_body("pdf-recipient@example.org"))
    assert created.status_code == 201, created.text
    share_id = created.json()["id"]
    fetched = recipient.get(f"/library/shares/received/{share_id}/documents/{stored_id}/file")
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == pdf
    assert fetched.headers["content-type"].startswith("application/pdf")
    assert fetched.headers["cache-control"] == "private, no-store"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    content_security_policy = fetched.headers["content-security-policy"]
    assert "sandbox" in content_security_policy
    assert "default-src 'none'" in content_security_policy
    assert "frame-ancestors 'none'" in content_security_policy
    assert "base-uri 'none'" in content_security_policy
    assert (
        fetched.headers["content-disposition"] == f'inline; filename="shared-paper-{stored_id}.pdf"'
    )
    missing = recipient.get(f"/library/shares/received/{share_id}/documents/{citation_id}/file")
    assert missing.status_code == 404, missing.text


def test_project_scope_overlap_dynamic_moves_revoke_and_project_delete(
    settings: Settings, corpus: DuckDBCorpus
) -> None:
    del corpus
    app = create_app()
    owner = _register(app, email="project-owner@example.org", org="Project Owner")
    recipient = _register(app, email="project-recipient@example.org", org="Project Recipient")
    outsider = _register(app, email="project-outsider@example.org", org="Project Outsider")
    _owner_user_id, owner_org_id = _identity(owner)
    project_one = owner.post("/projects", json={"name": "Included"}).json()
    project_two = owner.post("/projects", json={"name": "Excluded"}).json()
    outsider_project = outsider.post("/projects", json={"name": "Foreign"}).json()
    foreign = owner.post(
        "/library/shares",
        json=_share_body("project-recipient@example.org", project_id=int(outsider_project["id"])),
    )
    assert foreign.status_code == 404, foreign.text
    first = owner.post(
        "/library/shares",
        json=_share_body("project-recipient@example.org", project_id=int(project_one["id"])),
    )
    second = owner.post(
        "/library/shares",
        json=_share_body("project-recipient@example.org", project_id=int(project_two["id"])),
    )
    assert first.status_code == 201 and second.status_code == 201
    overlap = owner.post("/library/shares", json=_share_body("project-recipient@example.org"))
    assert overlap.status_code == 409, overlap.text
    assert overlap.json()["detail"]["code"] == "library_share_scope_overlap"
    assert owner.delete(f"/library/shares/{first.json()['id']}").status_code == 204
    assert owner.delete(f"/library/shares/{second.json()['id']}").status_code == 204
    whole = owner.post("/library/shares", json=_share_body("project-recipient@example.org"))
    assert whole.status_code == 201, whole.text
    reverse_overlap = owner.post(
        "/library/shares",
        json=_share_body("project-recipient@example.org", project_id=int(project_one["id"])),
    )
    assert reverse_overlap.status_code == 409, reverse_overlap.text
    assert reverse_overlap.json()["detail"]["code"] == "library_share_scope_overlap"
    assert owner.delete(f"/library/shares/{whole.json()['id']}").status_code == 204
    scoped = owner.post(
        "/library/shares",
        json=_share_body("project-recipient@example.org", project_id=int(project_one["id"])),
    )
    assert scoped.status_code == 201, scoped.text
    share_id = scoped.json()["id"]
    pdf = b"%PDF-1.4\ndynamic project fixture\n%%EOF"
    checksum, storage_path = LocalDocumentStore(settings.documents_dir).put(pdf)
    with db_session() as session:
        work = WorkRow(
            id="W-dynamic-project",
            doi="10.1234/dynamic",
            title="Dynamic project paper",
            year=2026,
            payload={},
        )
        session.add(work)
        session.flush()
        document = DocumentRow(
            org_id=owner_org_id,
            run_id=None,
            project_id=int(project_two["id"]),
            work_id=work.id,
            status="retrieved",
            source="open_access",
            legal_basis="open_access",
            content_type="application/pdf",
            checksum=checksum,
            byte_size=len(pdf),
            storage_path=storage_path,
            text_status="parsed",
        )
        session.add(document)
        session.flush()
        document_id = document.id
    documents_url = f"/library/shares/received/{share_id}/documents"
    assert recipient.get(documents_url).json() == []
    moved_in = owner.patch(
        f"/documents/{document_id}", json={"project_id": int(project_one["id"]), "folder": None}
    )
    assert moved_in.status_code == 200, moved_in.text
    assert [row["id"] for row in recipient.get(documents_url).json()] == [document_id]
    moved_out = owner.patch(
        f"/documents/{document_id}", json={"project_id": int(project_two["id"]), "folder": None}
    )
    assert moved_out.status_code == 200, moved_out.text
    assert recipient.get(documents_url).json() == []
    assert owner.delete(f"/library/shares/{share_id}").status_code == 204
    assert recipient.get(documents_url).status_code == 404
    recreated = owner.post(
        "/library/shares",
        json=_share_body("project-recipient@example.org", project_id=int(project_one["id"])),
    )
    assert recreated.status_code == 201, recreated.text
    assert owner.delete(f"/projects/{project_one['id']}").status_code == 200
    assert recipient.get("/library/shares/received").json() == []
    assert (
        recipient.get(f"/library/shares/received/{recreated.json()['id']}/documents").status_code
        == 404
    )


def test_recipient_reads_only_sanitized_library_surfaces_and_not_private_runs(
    settings: Settings, corpus: DuckDBCorpus
) -> None:
    del corpus
    app = create_app()
    owner = _register(app, email="surface-owner@example.org", org="Surface Owner")
    recipient = _register(app, email="surface-recipient@example.org", org="Surface Recipient")
    outsider = _register(app, email="surface-outsider@example.org", org="Surface Outsider")
    owner_user_id, owner_org_id = _identity(owner)
    _recipient_user_id, _recipient_org_id = _identity(recipient)
    _outsider_user_id, outsider_org_id = _identity(outsider)
    pdf = b"%PDF-1.4\nordinary acquisition run\n%%EOF"
    checksum, storage_path = LocalDocumentStore(settings.documents_dir).put(pdf)
    foreign_pdf = b"%PDF-1.4\nforeign tenant\n%%EOF"
    foreign_checksum, foreign_storage_path = LocalDocumentStore(settings.documents_dir).put(
        foreign_pdf
    )
    secret_url = "https://example.org/paper?chapter=2&access_token=top-secret#private-fragment"
    secret_pdf_url = "https://example.org/paper.pdf?download=1&X-Amz-Signature=secret-signature"
    secret_canaries = {
        "PRIVATE-CAPTURE-PROVENANCE",
        "PRIVATE-ANNOTATION-NOTE",
        "PRIVATE-WEB-PROVENANCE",
        "top-secret",
        "private-fragment",
        "secret-signature",
    }
    with db_session() as session:
        private_work = WorkRow(
            id="W-private-collision",
            doi="10.1234/shared-surface",
            title="Private collision",
            year=2026,
            payload={},
        )
        normal_work = WorkRow(
            id="W-normal-acquisition",
            doi="10.1234/shared-surface",
            title="Ordinary acquisition paper",
            year=2026,
            payload={
                "authors": ["Ada Researcher", "Grace Researcher"],
                "abstract": "A safely shared abstract.",
                "pdf_url": secret_pdf_url,
                "venue": "Safe Journal",
            },
        )
        foreign_work = WorkRow(
            id="W-foreign-tenant",
            doi="10.1234/foreign",
            title="Foreign tenant paper",
            year=2024,
            payload={},
        )
        session.add_all((private_work, normal_work, foreign_work))
        session.flush()
        private_run = Run(
            org_id=owner_org_id,
            project_id=None,
            status="completed",
            question="private",
            config={"surface": "companion_paper", "owner_user_id": owner_user_id},
        )
        normal_run = Run(
            org_id=owner_org_id,
            project_id=None,
            status="completed",
            question="ordinary acquisition",
            config={"surface": "research"},
        )
        session.add_all((private_run, normal_run))
        session.flush()
        private_document = DocumentRow(
            org_id=owner_org_id,
            run_id=private_run.id,
            project_id=None,
            work_id=private_work.id,
            status="retrieved",
            source="upload",
            legal_basis="user_upload",
            content_type="application/pdf",
            checksum=checksum,
            byte_size=len(pdf),
            storage_path=storage_path,
            text_status="parsed",
        )
        ordinary_document = DocumentRow(
            org_id=owner_org_id,
            run_id=normal_run.id,
            project_id=None,
            work_id=normal_work.id,
            status="retrieved",
            source="open_access",
            legal_basis="open_access",
            license="cc-by",
            url=secret_url,
            content_type="application/pdf",
            checksum=checksum,
            byte_size=len(pdf),
            storage_path=storage_path,
            text_status="parsed",
        )
        foreign_document = DocumentRow(
            org_id=outsider_org_id,
            run_id=None,
            project_id=None,
            work_id=foreign_work.id,
            status="retrieved",
            source="open_access",
            legal_basis="open_access",
            content_type="application/pdf",
            checksum=foreign_checksum,
            byte_size=len(foreign_pdf),
            storage_path=foreign_storage_path,
            text_status="parsed",
        )
        session.add_all((private_document, ordinary_document, foreign_document))
        session.flush()
        private_document_id = private_document.id
        ordinary_document_id = ordinary_document.id
        foreign_document_id = foreign_document.id
        metadata = BrowserCapturedPaperMetadataRow(
            org_id=owner_org_id,
            user_id=owner_user_id,
            document_id=ordinary_document.id,
            title="Ordinary acquisition paper",
            authors=["Ada Researcher", "Grace Researcher"],
            doi="10.1234/shared-surface",
            doi_identity="10.1234/shared-surface",
            pdf_checksum=checksum,
            published_at="2026-01-15",
            description="A safely shared abstract.",
            source_url=secret_url,
            canonical_url=secret_url,
            canonical_url_hash=hashlib.sha256(secret_url.encode()).hexdigest(),
            provenance={
                "private_capture_receipt": "PRIVATE-CAPTURE-PROVENANCE",
                "bibliographic": {"pdf_url": secret_pdf_url},
            },
        )
        annotation = DocumentAnnotationRow(
            org_id=owner_org_id,
            document_id=ordinary_document.id,
            user_id=owner_user_id,
            source="user",
            page=1,
            quote="Private marked passage",
            note="PRIVATE-ANNOTATION-NOTE",
            color="moss",
        )
        web_source = LibraryWebSourceRow(
            org_id=owner_org_id,
            created_by=owner_user_id,
            project_id=None,
            canonical_url=secret_url,
            canonical_url_hash=hashlib.sha256(b"surface-web-source").hexdigest(),
            capture_id="surface-web-source-0001",
            payload_sha256=hashlib.sha256(b"surface-payload").hexdigest(),
            original_url=secret_url,
            title="Safe shared web source",
            site_name="Example",
            authors=["Web Author"],
            published_at="2026-02-01",
            description="Public source description",
            selected_excerpt="Public selected excerpt",
            source_kind="web",
            doi="",
            provenance={"private_receipt": "PRIVATE-WEB-PROVENANCE"},
        )
        session.add_all((metadata, annotation, web_source))
        session.flush()
        web_source_id = web_source.public_id
    created = owner.post("/library/shares", json=_share_body("surface-recipient@example.org"))
    assert created.status_code == 201, created.text
    share_id = created.json()["id"]
    received = recipient.get("/library/shares/received")
    assert received.status_code == 200, received.text
    assert received.json()[0]["owner"] == {
        "name": "Surface Owner",
        "email": "surface-owner@example.org",
    }
    documents_url = f"/library/shares/received/{share_id}/documents"
    documents = recipient.get(documents_url)
    assert documents.status_code == 200, documents.text
    assert documents.headers["cache-control"] == "private, no-store"
    assert [row["id"] for row in documents.json()] == [ordinary_document_id]
    assert recipient.get(f"{documents_url}?q=ordinary").json()[0]["id"] == ordinary_document_id
    assert recipient.get(f"{documents_url}?q=does-not-exist").json() == []
    shared_document = documents.json()[0]
    assert shared_document["access_role"] == "viewer"
    assert shared_document["share_id"] == share_id
    assert shared_document["has_file"] is True
    assert shared_document["url"] == "https://example.org/paper?chapter=2"
    assert shared_document["metadata"]["source_url"] == "https://example.org/paper?chapter=2"
    assert shared_document["metadata"]["canonical_url"] == "https://example.org/paper?chapter=2"
    assert shared_document["metadata"]["pdf_url"] == "https://example.org/paper.pdf?download=1"
    serialized_document = json.dumps(shared_document, sort_keys=True)
    for forbidden_key in (
        "run_id",
        "storage_path",
        "checksum",
        "capture_id",
        "provenance",
        "field_provenance",
        "annotations",
    ):
        assert forbidden_key not in shared_document
        assert f'"{forbidden_key}"' not in serialized_document
    assert all(canary not in serialized_document for canary in secret_canaries)
    detail = recipient.get(f"{documents_url}/{ordinary_document_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json() == shared_document
    assert recipient.get(f"{documents_url}/{private_document_id}").status_code == 404
    assert recipient.get(f"{documents_url}/{foreign_document_id}").status_code == 404
    citations = recipient.get(f"{documents_url}/{ordinary_document_id}/citations")
    assert citations.status_code == 200, citations.text
    assert citations.headers["cache-control"] == "private, no-store"
    assert citations.json()["document_id"] == ordinary_document_id
    assert all(citation["value"].strip() for citation in citations.json()["citations"].values())
    file_response = recipient.get(f"{documents_url}/{ordinary_document_id}/file")
    assert file_response.status_code == 200 and file_response.content == pdf
    web_url = f"/library/shares/received/{share_id}/web-sources"
    web_sources = recipient.get(web_url)
    assert web_sources.status_code == 200, web_sources.text
    assert web_sources.headers["cache-control"] == "private, no-store"
    assert [row["id"] for row in web_sources.json()] == [web_source_id]
    shared_web = web_sources.json()[0]
    assert shared_web["url"] == "https://example.org/paper?chapter=2"
    assert shared_web["canonical_url"] == "https://example.org/paper?chapter=2"
    assert shared_web["access_role"] == "viewer"
    serialized_web = json.dumps(shared_web, sort_keys=True)
    assert '"provenance"' not in serialized_web
    assert all(canary not in serialized_web for canary in secret_canaries)
    web_detail = recipient.get(f"{web_url}/{web_source_id}")
    assert web_detail.status_code == 200 and web_detail.json() == shared_web
    assert recipient.get(f"{web_url}?q=shared").json()[0]["id"] == web_source_id
    assert outsider.get(documents_url).status_code == 404
    assert outsider.get(f"{documents_url}/{ordinary_document_id}/file").status_code == 404
    assert outsider.get(web_url).status_code == 404
    assert outsider.get(f"{web_url}/{web_source_id}").status_code == 404
    assert (
        recipient.patch(
            f"/documents/{ordinary_document_id}",
            json={"project_id": None, "folder": "recipient-mutation"},
        ).status_code
        == 404
    )
    assert recipient.delete(f"/documents/{ordinary_document_id}").status_code == 404
    assert recipient.get("/documents").json() == []


def test_active_share_limit_is_enforced_after_org_scoped_serialization(
    corpus: DuckDBCorpus,
) -> None:
    del corpus
    app = create_app()
    owner = _register(app, email="limit-owner@example.org", org="Limit Owner")
    saturated_recipient = _register(
        app, email="limit-saturated@example.org", org="Limit Saturated Recipient"
    )
    _target = _register(app, email="limit-target@example.org", org="Limit Target")
    owner_user_id, owner_org_id = _identity(owner)
    saturated_user_id, _saturated_org_id = _identity(saturated_recipient)
    now = datetime.now(UTC)
    with db_session() as session:
        projects = [
            Project(org_id=owner_org_id, name=f"Shared project {index}") for index in range(100)
        ]
        session.add_all(projects)
        session.flush()
        grants = [
            LibraryAccessGrantRow(
                org_id=owner_org_id,
                grantee_user_id=saturated_user_id,
                project_id=project.id,
                role="viewer",
                created_by_user_id=owner_user_id,
                rights_statement_version="library-share-v1",
                rights_confirmed_at=now,
                created_at=now,
            )
            for project in projects
        ]
        session.add_all(grants)
        session.flush()
        revoke_id = grants[0].public_id
    blocked = owner.post("/library/shares", json=_share_body("limit-target@example.org"))
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "library_share_limit_reached"
    assert owner.delete(f"/library/shares/{revoke_id}").status_code == 204
    created = owner.post("/library/shares", json=_share_body("limit-target@example.org"))
    assert created.status_code == 201, created.text


def test_incoming_grant_privacy_export_and_account_workspace_lifecycle(
    settings: Settings, corpus: DuckDBCorpus
) -> None:
    del corpus
    app = create_app()
    owner = _register(app, email="lifecycle-owner@example.org", org="Lifecycle Owner")
    recipient = _register(app, email="lifecycle-recipient@example.org", org="Lifecycle Recipient")
    _owner_user_id, owner_org_id = _identity(owner)
    recipient_user_id, _recipient_org_id = _identity(recipient)
    pdf = b"%PDF-1.4\nowner-only export canary\n%%EOF"
    checksum, storage_path = LocalDocumentStore(settings.documents_dir).put(pdf)
    with db_session() as session:
        work = WorkRow(
            id="W-owner-export-canary",
            doi="10.1234/owner-export-canary",
            title="OWNER-DOCUMENT-MUST-NOT-ENTER-RECIPIENT-EXPORT",
            year=2026,
            payload={},
        )
        session.add(work)
        session.flush()
        session.add(
            DocumentRow(
                org_id=owner_org_id,
                run_id=None,
                project_id=None,
                work_id=work.id,
                status="retrieved",
                source="open_access",
                legal_basis="open_access",
                content_type="application/pdf",
                checksum=checksum,
                byte_size=len(pdf),
                storage_path=storage_path,
                text_status="parsed",
            )
        )
    created = owner.post("/library/shares", json=_share_body("lifecycle-recipient@example.org"))
    assert created.status_code == 201, created.text
    share_id = created.json()["id"]
    exported = recipient.post("/auth/account/export", json={"password": PASSWORD})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        names = set(archive.namelist())
        assert "data/library_access_grants.json" in names
        grant_rows = json.loads(archive.read("data/library_access_grants.json"))
        assert len(grant_rows) == 1
        assert grant_rows[0]["public_id"] == share_id
        assert grant_rows[0]["grantee_user_id"] == recipient_user_id
        assert grant_rows[0]["created_by_user_id"] is None
        exported_payload = b"".join(archive.read(name) for name in names)
        assert b"OWNER-DOCUMENT-MUST-NOT-ENTER-RECIPIENT-EXPORT" not in exported_payload
        assert "data/documents.json" not in names
    deleted_recipient = recipient.request("DELETE", "/auth/account", json={"password": PASSWORD})
    assert deleted_recipient.status_code == 200, deleted_recipient.text
    with db_session() as session:
        assert session.get(User, recipient_user_id) is None
        assert (
            session.scalar(
                select(LibraryAccessGrantRow).where(LibraryAccessGrantRow.public_id == share_id)
            )
            is None
        )
    assert owner.get("/library/shares").json() == []
    second_recipient = _register(
        app, email="lifecycle-second@example.org", org="Lifecycle Second Recipient"
    )
    second_share = owner.post("/library/shares", json=_share_body("lifecycle-second@example.org"))
    assert second_share.status_code == 201, second_share.text
    second_share_id = second_share.json()["id"]
    assert [row["id"] for row in second_recipient.get("/library/shares/received").json()] == [
        second_share_id
    ]
    deleted_owner = owner.request("DELETE", "/auth/account", json={"password": PASSWORD})
    assert deleted_owner.status_code == 200, deleted_owner.text
    assert second_recipient.get("/library/shares/received").json() == []
    with db_session() as session:
        assert session.get(Org, owner_org_id) is None
        assert (
            session.scalar(
                select(LibraryAccessGrantRow).where(
                    LibraryAccessGrantRow.public_id == second_share_id
                )
            )
            is None
        )
