"""User-supplied documents: upload/link ingest, verified metadata, the file
endpoint, citation cards and the split-view paper reader."""

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from sixsentences_server.acquisition.models import (
    AcquisitionResult,
    AcquisitionStatus,
    ExtractedText,
    TextStatus,
)
from sixsentences_server.acquisition.store import LocalDocumentStore
from sixsentences_server.acquisition.upload import ingest_document, is_verified_work_id
from sixsentences_server.api.app import create_app
from sixsentences_server.chat.service import answer_question
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    DocumentRow,
    Run,
    SourceRecordRow,
    WorkRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool


def _mini_pdf(text: str) -> bytes:
    """A minimal, well-formed one-page PDF whose page text is `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    )
    return bytes(out)


class _StubOA:
    """OpenAlex stub: fixed work for id lookups, fixed hits for searches."""

    def __init__(
        self, work: WorkRecord | None = None, search_hits: list[WorkRecord] | None = None
    ) -> None:
        self.work = work
        self.search_hits = search_hits or []
        self.requested: list[str] = []
        self.searched: list[str] = []

    def get_work(self, external_id: str) -> WorkRecord | None:
        self.requested.append(external_id)
        return self.work

    def search(self, query: str, *, limit: int = 1, **_: object) -> list[WorkRecord]:
        self.searched.append(query)
        return self.search_hits


class _NoAcquire:
    """Acquirer stub: there is never an open-access copy."""

    def acquire(self, work: WorkRecord) -> AcquisitionResult:
        return AcquisitionResult(
            work_id=work.id,
            status=AcquisitionStatus.NOT_RETRIEVED,
            reason="no open-access copy of this paper is available",
            text_status=TextStatus.NOT_RETRIEVED,
        )


def _register(client: TestClient, email: str, org: str) -> str:
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _authed(app: FastAPI, email: str = "docs@example.org", org: str = "Docs Lab") -> TestClient:
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {_register(client, email, org)}"
    return client


def test_ingest_upload_without_match_gets_honest_synthetic_work(settings: Settings) -> None:
    init_db()
    pdf = _mini_pdf("A Practitioner Study of Terraform Drift Detection in Cloud Fleets")
    with db_session() as session:
        org = get_default_org(session)
        doc = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=pdf,
            filename="drift.pdf",
            oa_client=_StubOA(),
        )
        assert doc.work_id.startswith("W0")
        assert not is_verified_work_id(doc.work_id)
        assert doc.legal_basis == "user_upload"
        assert doc.text_status == "parsed"
        assert doc.run_id is None
        work = session.get(WorkRow, doc.work_id)
        assert work is not None and "Terraform Drift" in work.title
        again = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=pdf,
            filename="copy.pdf",
            oa_client=_StubOA(),
        )
        assert again.work_id == doc.work_id


def test_ingest_upload_resolves_doi_to_verified_work(settings: Settings) -> None:
    init_db()
    pdf = _mini_pdf("Attention Is All You Need. doi:10.5555/3295222 Vaswani et al.")
    real = WorkRecord(id="W2741809807", title="Attention Is All You Need", year=2017)
    with db_session() as session:
        org = get_default_org(session)
        stub = _StubOA(real)
        doc = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=pdf,
            filename="attention.pdf",
            oa_client=stub,
        )
        assert doc.work_id == "W2741809807"
        assert is_verified_work_id(doc.work_id)
        assert stub.requested and stub.requested[0].startswith("doi:10.5555/")


def test_normal_upload_dedupes_different_files_with_same_verified_doi(settings: Settings) -> None:
    init_db()
    first_pdf = _mini_pdf("First edition. doi:10.5555/shared Identity study.")
    second_pdf = _mini_pdf("Second edition with changes. doi:10.5555/shared Identity study.")
    real = WorkRecord(
        id="W-shared-doi",
        title="Identity study",
        year=2025,
        doi="10.5555/shared",
        authors=["Ada Author"],
    )
    with db_session() as session:
        org = get_default_org(session)
        first = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=first_pdf,
            filename="first.pdf",
            oa_client=_StubOA(real),
            existing_document=lambda record, checksum, identity_kind: session.scalar(
                select(DocumentRow).where(
                    DocumentRow.org_id == org.id, DocumentRow.work_id == record.id
                )
            ),
        )
        second = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=second_pdf,
            filename="second.pdf",
            oa_client=_StubOA(real),
            existing_document=lambda record, checksum, identity_kind: session.scalar(
                select(DocumentRow).where(
                    DocumentRow.org_id == org.id, DocumentRow.work_id == record.id
                )
            ),
        )
        assert second.id == first.id
        assert second.checksum == first.checksum
        assert session.scalar(select(func.count(DocumentRow.id))) == 1


def test_title_search_resolution_never_dedupes_different_pdf_bytes(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    title_match = WorkRecord(id="W24681012", title="Only a title search match")
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload._resolve_metadata",
        lambda *args, **kwargs: (title_match, "title"),
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload.PdfTextExtractor.extract",
        lambda *args, **kwargs: ExtractedText("Title search PDF text.", TextStatus.PARSED),
    )
    responses = [
        client.post(
            "/orgs/current/documents",
            json={
                "filename": f"version-{index}.pdf",
                "content_base64": base64.b64encode(
                    _mini_pdf(f"Different PDF bytes for title match {index}")
                ).decode(),
            },
        )
        for index in (1, 2)
    ]
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["id"] != responses[1].json()["id"]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 2


def test_same_doi_reupload_enriches_work_fill_only_and_reports_alternative(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    records = iter(
        [
            WorkRecord(
                id="W1357911",
                doi="10.5555/work-enrichment",
                title="Reviewed canonical title",
                authors=["Ada Author"],
            ),
            WorkRecord(
                id="W1357911",
                doi="10.5555/work-enrichment",
                title="Conflicting provider title",
                year=2025,
                authors=["ada author", "Grace Researcher"],
                abstract="Newly available abstract.",
            ),
        ]
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload._resolve_metadata",
        lambda *args, **kwargs: (next(records), "doi"),
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload.PdfTextExtractor.extract",
        lambda *args, **kwargs: ExtractedText("Verified DOI PDF text.", TextStatus.PARSED),
    )
    first = client.post(
        "/orgs/current/documents",
        json={
            "filename": "first.pdf",
            "content_base64": base64.b64encode(_mini_pdf("First DOI edition")).decode(),
        },
    )
    second = client.post(
        "/orgs/current/documents",
        json={
            "filename": "second.pdf",
            "content_base64": base64.b64encode(_mini_pdf("Second DOI edition")).decode(),
        },
    )
    assert first.status_code == second.status_code == 201
    assert second.json()["id"] == first.json()["id"], (first.json(), second.json())
    assert second.json()["warnings"] == ["different_pdf_same_identity"]
    with db_session() as session:
        work = session.get(WorkRow, "W1357911")
        assert work is not None
        assert work.title == "Reviewed canonical title"
        assert work.year == 2025
        assert work.payload["authors"] == ["Ada Author", "Grace Researcher"]
        assert work.payload["abstract"] == "Newly available abstract."
        assert session.scalar(select(func.count(DocumentRow.id))) == 1


def test_exact_legacy_pdf_is_not_promoted_across_a_separate_verified_doi_attachment(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late DOI resolution must not bridge two existing full-text records."""
    client = _authed(create_app())
    verified = WorkRecord(
        id="W9753102468", doi="10.5555/late-identity-conflict", title="Verified DOI owner"
    )
    resolutions = iter([(verified, "doi"), (None, None), (verified, "doi")])
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload._resolve_metadata",
        lambda *args, **kwargs: next(resolutions),
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload.PdfTextExtractor.extract",
        lambda *args, **kwargs: ExtractedText("Resolved upload text.", TextStatus.PARSED),
    )
    verified_upload = client.post(
        "/orgs/current/documents",
        json={
            "filename": "verified.pdf",
            "content_base64": base64.b64encode(_mini_pdf("Verified attachment")).decode(),
        },
    )
    unresolved_bytes = _mini_pdf("Previously unresolved but different attachment")
    unresolved_upload = client.post(
        "/orgs/current/documents",
        json={
            "filename": "unresolved.pdf",
            "content_base64": base64.b64encode(unresolved_bytes).decode(),
        },
    )
    conflict = client.post(
        "/orgs/current/documents",
        json={
            "filename": "unresolved.pdf",
            "content_base64": base64.b64encode(unresolved_bytes).decode(),
        },
    )
    assert verified_upload.status_code == unresolved_upload.status_code == 201
    assert conflict.status_code == 409, conflict.text
    with db_session() as session:
        verified_document = session.get(DocumentRow, verified_upload.json()["id"])
        unresolved_document = session.get(DocumentRow, unresolved_upload.json()["id"])
        assert verified_document is not None
        assert unresolved_document is not None
        assert verified_document.work_id == verified.id
        assert unresolved_document.work_id != verified.id
        assert not is_verified_work_id(unresolved_document.work_id)
        assert session.scalar(select(func.count(DocumentRow.id))) == 2


def test_ingest_writes_the_work_before_the_ledger_row(
    settings: Settings, enforced_foreign_keys: None
) -> None:
    """documents.work_id is a real constraint in production.

    works and documents share no relationship(), so one flush orders them by
    mapper name and puts the child first. Under PostgreSQL that is a foreign
    key violation, and an unhandled 500 loses the CORS headers, so the browser
    only reports a dead connection.
    """
    init_db()
    pdf = _mini_pdf("Retrieval Practice in Higher Education. doi:10.5555/9911001")
    unseen = WorkRecord(id="W7163760099", title="Retrieval Practice in Higher Education")
    with db_session() as session:
        org = get_default_org(session)
        doc = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=pdf,
            filename="retrieval.pdf",
            oa_client=_StubOA(unseen),
        )
        assert doc.work_id == "W7163760099"
        assert session.get(WorkRow, doc.work_id) is not None


def test_ingest_resolves_arxiv_id_from_the_filename(settings: Settings) -> None:
    """2307.03172v3.pdf carries its arXiv id in the name — that must resolve."""
    init_db()
    pdf = _mini_pdf("Lost in the Middle How Language Models Use Long Contexts")
    real = WorkRecord(id="W4382755249", title="Lost in the Middle", year=2023)
    with db_session() as session:
        org = get_default_org(session)
        stub = _StubOA(real)
        doc = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=pdf,
            filename="2307.03172v3.pdf",
            oa_client=stub,
        )
        assert doc.work_id == "W4382755249"
        assert is_verified_work_id(doc.work_id)
        assert "doi:10.48550/arXiv.2307.03172" in stub.requested


def test_ingest_resolves_via_title_head_search(settings: Settings) -> None:
    """No DOI, no arXiv id: the head of page one finds the paper by title,
    and the hit only counts when its title is contained in that head."""
    init_db()
    page_head = "Lost in the Middle How Language Models Use Long Contexts Nelson Liu Kevin Lin Stanford University"
    real = WorkRecord(
        id="W4382755249",
        title="Lost in the Middle: How Language Models Use Long Contexts",
        year=2023,
    )
    with db_session() as session:
        org = get_default_org(session)
        stub = _StubOA(work=None, search_hits=[real])
        doc = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=_mini_pdf(page_head),
            filename="paper-final.pdf",
            oa_client=stub,
        )
        assert doc.work_id == "W4382755249"
        assert stub.searched
        wrong = _StubOA(
            work=None, search_hits=[WorkRecord(id="W1", title="A totally different botany survey")]
        )
        other = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=_mini_pdf("Continuous compliance pipelines for policy as code teams"),
            filename="other.pdf",
            oa_client=wrong,
        )
        assert other.work_id.startswith("W0")


def test_ingest_rejects_non_pdf(settings: Settings) -> None:
    from sixsentences_server.acquisition.upload import UploadError

    init_db()
    with db_session() as session:
        org = get_default_org(session)
        with pytest.raises(UploadError, match="not a PDF"):
            ingest_document(
                session,
                org_id=org.id,
                run_id=None,
                content=b"<html>a landing page</html>",
                filename="page.html",
                oa_client=_StubOA(),
            )


def _upload(client: TestClient, run_id: int, pdf: bytes, name: str = "paper.pdf") -> dict:
    resp = client.post(
        f"/runs/{run_id}/documents",
        json={"filename": name, "content_base64": base64.b64encode(pdf).decode()},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_upload_file_roundtrip_and_tenant_isolation(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app)
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "drift", "query": "transformer"}
    ).json()["id"]
    pdf = _mini_pdf("Terraform drift detection strategies for regulated industries")
    doc = _upload(client, run_id, pdf)
    assert doc["verified"] is False and doc["work_id"].startswith("W0")
    file_resp = client.get(f"/documents/{doc['id']}/file")
    assert file_resp.status_code == 200
    assert file_resp.content[:5] == b"%PDF-"
    assert file_resp.headers["content-type"].startswith("application/pdf")
    works = client.get(f"/runs/{run_id}/works").json()
    assert any(w["id"] == doc["work_id"] for w in works["works"])
    ledger = client.get(f"/runs/{run_id}/documents").json()
    entry = next(d for d in ledger if d["id"] == doc["id"])
    assert entry["has_file"] is True and entry["legal_basis"] == "user_upload"
    assert entry["title"] and "Terraform drift" in entry["title"]
    stranger = _authed(app, email="other@example.org", org="Other")
    assert stranger.get(f"/documents/{doc['id']}/file").status_code == 404


def test_pending_upload_attaches_via_create_run(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    pdf = _mini_pdf("Policy as code adoption in continuous compliance pipelines")
    pending = client.post(
        "/orgs/current/documents",
        json={"filename": "policy.pdf", "content_base64": base64.b64encode(pdf).decode()},
    ).json()
    assert pending["run_id"] is None
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "policy as code",
            "query": "transformer",
            "document_ids": [pending["id"]],
        },
    ).json()["id"]
    ledger = client.get(f"/runs/{run_id}/documents").json()
    assert len(ledger) == 1
    assert ledger[0]["id"] != pending["id"]
    assert ledger[0]["work_id"] == pending["work_id"]
    works = client.get(f"/runs/{run_id}/works").json()
    assert any(w["id"] == pending["work_id"] for w in works["works"])
    assert client.delete(f"/runs/{run_id}").status_code == 200
    assert client.get(f"/documents/{pending['id']}/file").status_code == 200
    assert [row["id"] for row in client.get("/documents").json()] == [pending["id"]]


def test_a_run_can_live_outside_any_project(corpus: DuckDBCorpus) -> None:
    """POST /runs creates a chat without a project folder: project_id stays
    null through detail and listing, and project-scoped listings skip it."""
    client = _authed(create_app())
    created = client.post("/runs", json={"question": "transformers", "query": "transformer"}).json()
    assert created["public_id"]
    detail = client.get(f"/runs/{created['public_id']}").json()
    assert detail["project_id"] is None
    listed = client.get("/runs").json()
    assert any(r["id"] == created["id"] and r["project_id"] is None for r in listed)
    project = client.post("/projects", json={"name": "demo"}).json()
    scoped = client.get(f"/runs?project_id={project['id']}").json()
    assert all(r["id"] != created["id"] for r in scoped)


def test_runs_are_addressed_by_an_opaque_public_id(corpus: DuckDBCorpus) -> None:
    """Runs carry a random public id for URLs/topbar (a sequential r/7 reads
    unprofessional and leaks volume); every run route resolves it, and the
    integer key keeps working for API compatibility."""
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    created = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()
    public_id = created["public_id"]
    assert len(public_id) >= 8 and (not public_id.isdigit())
    by_public = client.get(f"/runs/{public_id}").json()
    assert by_public["id"] == created["id"]
    assert by_public["public_id"] == public_id
    assert client.get(f"/runs/{created['id']}").json()["id"] == created["id"]
    assert client.get(f"/runs/{public_id}/works").status_code == 200
    listed = client.get("/runs").json()
    assert any(r["public_id"] == public_id for r in listed)
    stranger = _authed(create_app(), email="pid@example.org", org="Pid Lab")
    assert stranger.get(f"/runs/{public_id}").status_code == 404


def test_run_works_response_carries_the_search_string(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    works = client.get(f"/runs/{run_id}/works").json()
    assert works["search_string"] == "transformer"
    assert works["search_synthesized_by"] == "user"


def test_ask_with_attached_pdf_cites_on_request_only(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    get_settings.cache_clear()
    box: dict[str, str] = {}

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "Attached paper" in prompt:
            return f'''The uploaded paper argues for drift checks [{box["wid"]}].\nSOURCES: [{{"work_id": "{box["wid"]}", "page": 1, "quote": "drift checks keep Terraform states honest"}}, {{"work_id": "{box["wid"]}", "page": 1, "quote": "this sentence is not in the paper at all"}}]'''
        return '"terraform" AND "drift"'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search", lambda question, primary: ([], "none")
    )
    client = _authed(create_app())
    pdf = _mini_pdf("Continuous drift checks keep Terraform states honest")
    pending = client.post(
        "/orgs/current/documents",
        json={"filename": "drift.pdf", "content_base64": base64.b64encode(pdf).decode()},
    ).json()
    box["wid"] = pending["work_id"]
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Was sagt das Paper zu Drift? Gib mir die Quelle dazu.",
            "mode": "ask",
            "document_ids": [pending["id"]],
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    history = client.get(f"/runs/{run_id}/chat").json()
    answer = next(m for m in history if m["role"] == "assistant")
    assert answer["citations"] == [box["wid"]]
    assert "SOURCES:" not in answer["content"]
    evidence = answer["payload"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["page"] == 1
    assert evidence[0]["quote"] == "drift checks keep Terraform states honest"
    assert evidence[0]["work_id"] == box["wid"]
    card = next(m for m in history if m["role"] == "tool" and m["payload"]["tool"] == "cite")
    assert card["payload"]["kind"] == "ui"
    assert "Ready to cite" in card["payload"]["resource"]["text"]
    assert "not verified" in card["payload"]["resource"]["text"]


def test_ask_about_attached_pdf_skips_web_and_unrequested_citation(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A content question about an attached paper gets neither a pointless
    web search (even with a key configured) nor an unrequested citation."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "configured-but-unused")
    get_settings.cache_clear()

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "Attached document" in prompt:
            return "The paper's key point is continuous drift checks."
        return '"terraform" AND "drift"'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search", lambda question, primary: ([], "none")
    )
    client = _authed(create_app())
    pdf = _mini_pdf("Continuous drift checks keep Terraform states honest")
    pending = client.post(
        "/orgs/current/documents",
        json={"filename": "drift.pdf", "content_base64": base64.b64encode(pdf).decode()},
    ).json()
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Was sind die wichtigsten Punkte des Papers?",
            "mode": "ask",
            "document_ids": [pending["id"]],
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    history = client.get(f"/runs/{run_id}/chat").json()
    tools_used = [m["payload"]["tool"] for m in history if m["role"] == "tool"]
    assert "web_search" not in tools_used
    assert "cite" not in tools_used
    assert any(m["role"] == "assistant" for m in history)


def test_ask_marks_passages_in_the_reader_on_request(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Markier die wichtigsten Stellen' on a quick answer opens the reader
    with verified highlights, exactly like the chat's show_paper tool."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    get_settings.cache_clear()
    page_text = "Continuous drift checks keep Terraform states honest"

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if prompt.startswith("Reader's focus:"):
            return '{"highlights": [{"page": 1, "quote": "drift checks keep Terraform states honest", "note": "The core claim."}]}'
        if "Attached document" in prompt:
            return "The key passage is highlighted on page 1."
        return '"terraform" AND "drift"'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search", lambda question, primary: ([], "none")
    )
    client = _authed(create_app())
    pending = client.post(
        "/orgs/current/documents",
        json={
            "filename": "drift.pdf",
            "content_base64": base64.b64encode(_mini_pdf(page_text)).decode(),
        },
    ).json()
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Zeig die wichtigsten Punkte des Papers, makier die pls für mich",
            "mode": "ask",
            "document_ids": [pending["id"]],
        },
    ).json()["id"]
    detail = client.get(f"/runs/{run_id}").json()
    assert detail["status"] == "completed", detail["error"]
    history = client.get(f"/runs/{run_id}/chat").json()
    panel = next(m for m in history if m["role"] == "tool" and m["payload"]["tool"] == "show_paper")
    assert panel["payload"]["kind"] == "paper"
    assert panel["payload"]["highlights"][0]["page"] == 1
    assert "drift checks" in panel["payload"]["highlights"][0]["quote"]
    tools_used = [m["payload"]["tool"] for m in history if m["role"] == "tool"]
    assert "web_search" not in tools_used and "cite" not in tools_used


def test_chat_bibtex_wish_returns_citation_card(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return "The card below has the citation ready [W1]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    answer = client.post(
        f"/runs/{run_id}/chat", json={"question": "gib mir die Quelle als BibTeX"}
    ).json()
    assert "cite" in answer["tools_used"]
    history = client.get(f"/runs/{run_id}/chat").json()
    card = next(m for m in history if m["role"] == "tool" and m["payload"]["tool"] == "cite")
    assert card["payload"]["kind"] == "ui"
    text = card["payload"]["resource"]["text"]
    assert "@article" in text and "RIS (Zotero)" in text and ("Ready to cite" in text)
    assert '"intent"' in text


def test_show_paper_opens_reader_with_verified_highlights_only(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_text = "Terraform drift detection strategies for regulated industries"
    uploaded: dict = {}

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if prompt.startswith("Reader's focus:"):
            return '{"highlights": [{"page": 1, "quote": "drift detection strategies for regulated", "note": "Names the core strategy."}, {"page": 1, "quote": "this sentence is nowhere in the paper", "note": "Invented."}]}'
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return "The reader shows the passage on page 1."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "drift", "query": "transformer"}
    ).json()["id"]
    uploaded = _upload(client, run_id, _mini_pdf(page_text))
    answer = client.post(
        f"/runs/{run_id}/chat",
        json={"question": "Zeig mir das PDF und highlighte die wichtigen Stellen"},
    ).json()
    assert "show_paper" in answer["tools_used"]
    history = client.get(f"/runs/{run_id}/chat").json()
    panel = next(m for m in history if m["role"] == "tool" and m["payload"]["tool"] == "show_paper")
    assert panel["payload"]["kind"] == "paper"
    assert panel["payload"]["document_id"] == uploaded["id"]
    highlights = panel["payload"]["highlights"]
    assert len(highlights) == 1
    assert highlights[0]["page"] == 1
    assert "drift detection" in highlights[0]["quote"]
    assert panel["content"].startswith("Opened ")


def test_agent_can_save_the_current_paper_to_the_library(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return "The paper is saved in your Library."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "drift", "query": "transformer"}
    ).json()["id"]
    uploaded = _upload(
        client, run_id, _mini_pdf("Continuous drift checks keep Terraform state honest")
    )
    answer = client.post(
        f"/runs/{run_id}/chat", json={"question": "Speichere das Paper bitte in der Bibliothek"}
    ).json()
    assert "save_paper" in answer["tools_used"]
    history = client.get(f"/runs/{run_id}/chat").json()
    saved = next(
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "library_save"
    )
    assert saved["payload"]["document_id"] != uploaded["id"]
    assert saved["payload"]["results"][0]["already_saved"] is True
    library = client.get("/documents").json()
    paper = next(
        document for document in library if document["id"] == saved["payload"]["document_id"]
    )
    assert paper["project_id"] == project["id"]


def test_chat_selection_anchors_the_turn_on_the_marked_passage(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passage marked in the reader rides on the user message, reaches the
    prompt (with its page text), and a foreign document is rejected."""
    seen_prompts: list[str] = []

    def handler(model: str, prompt: str) -> str:
        seen_prompts.append(prompt)
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return "That passage names the core strategy [W1]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    app = create_app()
    client = _authed(app)
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "drift", "query": "transformer"}
    ).json()["id"]
    doc = _upload(client, run_id, _mini_pdf("Continuous drift checks keep Terraform states honest"))
    answer = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "Warum ist genau diese Stelle wichtig?",
            "selection": {
                "document_id": doc["id"],
                "page": 1,
                "quote": "drift checks keep Terraform states honest",
            },
        },
    )
    assert answer.status_code == 200, answer.text
    history = client.get(f"/runs/{run_id}/chat").json()
    user_row = next(m for m in history if m["role"] == "user")
    assert user_row["payload"]["selection"]["quote"].startswith("drift checks")
    assert user_row["payload"]["selection"]["page"] == 1
    assert user_row["payload"]["selection"]["document_id"] == doc["id"]
    answer_prompt = next(
        p for p in reversed(seen_prompts) if "Question:" in p and (not p.startswith("CLAIM:"))
    )
    assert "marked this passage" in answer_prompt
    assert "drift checks keep Terraform states honest" in answer_prompt
    assert "Attached paper" in answer_prompt
    assert "[page 1]" in answer_prompt
    assert "SOURCES:" in answer_prompt
    sources_part = answer_prompt.split("Sources (cite by id", 1)[1]
    assert sources_part.strip().startswith(f"in [brackets]):\n[{doc['work_id']}]")
    seen_prompts.clear()
    follow_up = client.post(
        f"/runs/{run_id}/chat", json={"question": "Was ist damit genau gemeint?"}
    )
    assert follow_up.status_code == 200, follow_up.text
    follow_up_prompt = next(
        p for p in reversed(seen_prompts) if "Question:" in p and (not p.startswith("CLAIM:"))
    )
    assert "marked this passage" in follow_up_prompt
    assert "drift checks keep Terraform states honest" in follow_up_prompt
    follow_up_users = [
        message
        for message in client.get(f"/runs/{run_id}/chat").json()
        if message["role"] == "user"
    ]
    assert follow_up_users[-1]["payload"]["selection"]["document_id"] == doc["id"]
    stranger = _authed(app, email="sel-other@example.org", org="Sel Other")
    other_project = stranger.post("/projects", json={"name": "x"}).json()
    other_run = stranger.post(
        f"/projects/{other_project['id']}/runs", json={"question": "q", "query": "transformer"}
    ).json()["id"]
    blocked = stranger.post(
        f"/runs/{other_run}/chat",
        json={
            "question": "q?",
            "selection": {"document_id": doc["id"], "page": 1, "quote": "drift checks"},
        },
    )
    assert blocked.status_code == 404


def test_adaptive_context_follows_relevance_without_a_user_knob(settings: Settings) -> None:
    """No context_size: the context sizes itself from the relevance curve."""
    from sixsentences_server.chat.service import _context_works

    init_db()
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="p")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="q", status="completed")
        session.add(run)
        session.flush()
        for index in range(30):
            on_topic = index < 6
            work = WorkRecord(
                id=f"W9{index:04d}",
                title="Terraform drift detection in cloud infrastructure"
                if on_topic
                else f"Unrelated topic paper number {index}",
                abstract="infrastructure as code drift" if on_topic else "botany",
                year=2024,
            )
            session.add(
                WorkRow(
                    id=work.id,
                    title=work.title,
                    year=work.year,
                    payload=work.model_dump(mode="json"),
                )
            )
            session.flush()
            session.add(
                SourceRecordRow(org_id=org.id, run_id=run.id, work_id=work.id, source="test")
            )
        session.flush()
        adaptive = _context_works(session, run.id, "terraform drift detection")
        explicit = _context_works(session, run.id, "terraform drift detection", size=3)
        assert len(explicit) == 3
        assert len(adaptive) >= 6
        assert len(adaptive) < 30
        titles = [w.title for w in adaptive[:6]]
        assert all("Terraform" in t for t in titles)


def test_exports_never_claim_index_provenance_for_uploads() -> None:
    """A synthetic upload id must not masquerade as an index record: BibTeX
    drops the OpenAlex note and RIS the ID line, while verified works keep
    their traceable provenance."""
    from sixsentences_server.core.models import WorkRecord
    from sixsentences_server.reporting.exports import to_bibtex, to_ris

    upload = WorkRecord(id="W0486502320152", title="Uploaded Preprint")
    verified = WorkRecord(id="W2741809807", title="Attention Is All You Need")
    assert "OpenAlex" not in to_bibtex([upload])
    assert "ID  -" not in to_ris([upload])
    assert "OpenAlex:W2741809807" in to_bibtex([verified])
    assert "ID  - W2741809807" in to_ris([verified])


def test_citation_over_title_only_upload_never_sends_private_title_to_web_search(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic citation enrichment never exports a private upload title."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    seen_prompts: list[str] = []

    def handler(model: str, prompt: str) -> str:
        seen_prompts.append(prompt)
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        return "Die Karte unter dieser Antwort ist bereit."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search", lambda question, primary: ([], "none")
    )

    def reject_external_search(*args: object, **kwargs: object) -> object:
        raise AssertionError("a title-only private upload must not trigger web-search egress")

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", reject_external_search)
    client = _authed(create_app(), email="cite-web@example.org", org="Cite Web Lab")
    pdf = _mini_pdf("Continuous drift checks keep Terraform states honest")
    pending = client.post(
        "/orgs/current/documents",
        json={"filename": "drift.pdf", "content_base64": base64.b64encode(pdf).decode()},
    ).json()
    assert pending["verified"] is False
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Erstell mir mal pls die citation dazu",
            "mode": "ask",
            "document_ids": [pending["id"]],
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    history = client.get(f"/runs/{run_id}/chat").json()
    assert not any(
        message["role"] == "tool" and message["payload"]["tool"] == "web_search"
        for message in history
    )
    card = next(m for m in history if m["role"] == "tool" and m["payload"]["tool"] == "cite")
    assert card["payload"]["results"][0]["verified"] is False
    assert "not verified" in card["payload"]["resource"]["text"]
    answer_prompt = next(p for p in reversed(seen_prompts) if "Question:" in p)
    assert "WAS confirmed" not in answer_prompt
    assert "could NOT be found" not in answer_prompt
    assert "DIRECTLY BELOW this answer" in answer_prompt


def test_citation_does_not_claim_web_lookup_when_processing_gate_is_closed(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured but contractually disabled web-search credential is inert.

    Citation generation for an unverified upload must still produce the local
    card, without calling an external tool or telling the answer model that a
    live-web verification either succeeded or failed.
    """
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "configured-but-disabled")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "0")
    get_settings.cache_clear()
    seen_prompts: list[str] = []

    def handler(model: str, prompt: str) -> str:
        seen_prompts.append(prompt)
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        return "Die lokale Zitationskarte ist bereit."

    def reject_external_tool(*args: object, **kwargs: object) -> object:
        raise AssertionError("the disabled web-search path must not execute")

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search", lambda question, primary: ([], "none")
    )
    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", reject_external_tool)
    client = _authed(create_app(), email="cite-no-egress@example.org", org="No Egress Lab")
    pending = client.post(
        "/orgs/current/documents",
        json={
            "filename": "local-only.pdf",
            "content_base64": base64.b64encode(
                _mini_pdf("A local paper whose metadata has not been verified")
            ).decode(),
        },
    ).json()
    assert pending["verified"] is False
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Erstell mir bitte die Citation dazu",
            "mode": "ask",
            "document_ids": [pending["id"]],
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    history = client.get(f"/runs/{run_id}/chat").json()
    assert not any(
        message["role"] == "tool" and message["payload"]["tool"] == "web_search"
        for message in history
    )
    card = next(
        message
        for message in history
        if message["role"] == "tool" and message["payload"]["tool"] == "cite"
    )
    assert card["payload"]["results"][0]["verified"] is False
    answer_prompt = next(prompt for prompt in reversed(seen_prompts) if "Question:" in prompt)
    assert "WAS confirmed" not in answer_prompt
    assert "could NOT be found" not in answer_prompt
    assert "DIRECTLY BELOW this answer" in answer_prompt


def test_citation_enrichment_uses_stored_public_identifier_without_title_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sixsentences_server.chat.service import _enrich_unverified_work
    from sixsentences_server.core.models import WorkRecord

    requested_ids: list[str] = []

    class _StubIndex:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def get_work(self, external_id: str) -> WorkRecord | None:
            requested_ids.append(external_id)
            return WorkRecord(
                id="W-confirmed", title="Stored public identity", doi="10.1234/public-id", year=2026
            )

    monkeypatch.setattr("sixsentences_server.chat.service.OpenAlexClient", _StubIndex)
    monkeypatch.setattr(
        "sixsentences_server.chat.service._execute_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("identifier enrichment must not call web search")
        ),
    )
    original = WorkRecord(
        id="W-local-upload", title="Stored public identity", doi="https://doi.org/10.1234/public-id"
    )
    with db_session() as session:
        enriched, verified, steps = _enrich_unverified_work(session, original)
    assert requested_ids == ["doi:10.1234/public-id"]
    assert verified is True
    assert steps == []
    assert enriched.id == original.id
    assert enriched.year == 2026


def test_quote_verification_folds_pdf_artifacts_and_fixes_the_page() -> None:
    """PDF extractors emit ligatures (ﬁ) and line-break hyphenation
    ("informa- tion"); a model copying 'verbatim' writes plain characters.
    Verification folds both, and a page number that slipped by one is
    corrected to the neighbour that really carries the quote — while
    invented text still never ships."""
    from sixsentences_server.chat.service import _locate_quote, _norm_ws

    page_one = "We ﬁnd that signiﬁcant drift occurs when relevant informa- tion moves."
    page_two = "Multi- document reading saturates long before retrieval quality does."
    pages = [page_one, page_two]
    quote_one = "We find that significant drift occurs when relevant information moves."
    assert _norm_ws(quote_one) in _norm_ws(page_one)
    assert _locate_quote(quote_one, pages, 1) == 1
    quote_two = "Multi-document reading saturates long before retrieval quality does."
    assert _locate_quote(quote_two, pages, 2) == 2
    assert _locate_quote(quote_two, pages, 1) == 2
    assert _locate_quote("Drift is bad for models.", pages, 1) is None


def test_quote_verification_survives_margin_line_numbers() -> None:
    """Preprints number their lines; extractors glue those numbers to line
    ends ("Decoupled1 Security"). A faithful copy of the READABLE text must
    still verify (digit-blind second pass) — while paraphrase stays dead."""
    from sixsentences_server.chat.service import _locate_quote

    page = "Hallucinated Resources, Brittle Oracles, Decoupled1 Security: An Empirical Study of LLM-Generated2 Terraform3 Abstract6 The pipeline funnel exhibited11 two sharp drops: a 46-percentage-point drop from generation to validation, driven almost entirely12 by argument-name hallucination across all runs.13"
    quote = "The pipeline funnel exhibited two sharp drops: a 46-percentage-point drop from generation to validation, driven almost entirely by argument-name hallucination across all runs."
    assert _locate_quote(quote, [page], 1) == 1
    assert _locate_quote("A completely invented claim about drops.", [page], 1) is None


def test_tidy_citations_enforces_bare_brackets() -> None:
    """Models drift into [W1 page 3] / [W1, W2] despite the prompt; the
    mechanical pass restores the chip contract without losing the page."""
    from sixsentences_server.chat.service import _tidy_citations

    assert (
        _tidy_citations("This holds [W0171152944670 page 1] firmly.")
        == "This holds [W0171152944670] (p. 1) firmly."
    )
    assert _tidy_citations("Both agree [W1, W2].") == "Both agree [W1] [W2]."
    assert _tidy_citations("See [W12, S. 4] there.") == "See [W12] (p. 4) there."
    assert _tidy_citations("Plain [W99] stays.") == "Plain [W99] stays."


def test_annotate_retry_recovers_verbatim_quotes() -> None:
    """When verification eats most highlights, one corrective pass demands
    exact copies — the reader gets a real spread, not a lucky survivor."""
    from sixsentences_server.chat.service import _annotate_pages

    page = "Continuous drift checks keep Terraform states honest across cloud fleets. They catch silent infrastructure changes before those changes ever reach the production environment of a busy team. Weekly reconciliation reports then close the loop for auditors and compliance reviewers alike."
    calls = {"n": 0}

    def handler(model: str, prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"highlights": [{"page": 1, "quote": "Continuous drift checks keep Terraform states honest across cloud fleets.", "note": "a"}, {"page": 1, "quote": "this is a paraphrase of the drift idea stretched to pass the length gate", "note": "b"}, {"page": 1, "quote": "another invented quote that is long enough but simply not on the page", "note": "c"}]}'
        assert "copied verbatim" in prompt
        return '{"highlights": [{"page": 1, "quote": "They catch silent infrastructure changes before those changes ever reach the production environment of a busy team.", "note": "d"}, {"page": 1, "quote": "Weekly reconciliation reports then close the loop for auditors and compliance reviewers alike.", "note": "e"}]}'

    result = _annotate_pages(mock_pool(handler), "key points", [page])
    assert calls["n"] == 2
    assert len(result) == 3
    assert all(h["quote"].lower() in page.lower() for h in result)


def test_annotate_honours_an_explicit_beginner_highlight_count() -> None:
    from sixsentences_server.chat.service import _annotate_pages

    page = " ".join(
        [
            "The Transformer uses attention instead of recurrent layers for sequence transduction.",
            "The architecture permits substantially more parallel computation during training.",
            "The large model reaches a new state of the art on machine translation benchmarks.",
            "The experiments also show strong generalisation to constituency parsing tasks.",
        ]
    )

    def handler(model: str, prompt: str) -> str:
        assert "exactly 3 highlights" in prompt
        return json.dumps(
            {
                "highlights": [
                    {"page": 1, "quote": sentence, "note": f"Point {index}"}
                    for index, sentence in enumerate(page.split(". "), start=1)
                ]
            }
        )

    result = _annotate_pages(mock_pool(handler), "markier bitte genau 3 wichtige Stellen", [page])
    assert len(result) == 3


def test_show_paper_without_open_access_copy_reports_honestly(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_db()

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return "There is no free PDF; here is the publisher link."

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    from sixsentences_server.config import get_settings

    get_settings.cache_clear()
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="p")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="q", status="completed")
        session.add(run)
        session.flush()
        work = WorkRecord(id="W7777", title="Closed access paper", doi="10.1/closed")
        session.add(
            WorkRow(
                id=work.id, doi=work.doi, title=work.title, payload=work.model_dump(mode="json")
            )
        )
        session.flush()
        session.add(SourceRecordRow(org_id=org.id, run_id=run.id, work_id=work.id, source="test"))
        session.flush()
        answer = answer_question(
            session, run, mock_pool(handler), "Show me the PDF of W7777", acquirer=_NoAcquire()
        )
        assert "show_paper" in answer.tools_used
        from sixsentences_server.chat.service import chat_history

        tool_rows = [m for m in chat_history(session, run.id) if m.role == "tool"]
        outcome = tool_rows[-1].payload["results"][0]
        assert "no open-access copy" in outcome["error"]
        assert outcome["publisher_url"] == "https://doi.org/10.1/closed"
        assert "kind" not in tool_rows[-1].payload


def test_new_arxiv_discovery_becomes_an_open_highlighted_reader(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paper found mid-conversation must immediately become reader-eligible."""
    from sixsentences_server.chat.service import ToolStep, chat_history

    discovered = WorkRecord(
        id="W-NEW-ARXIV",
        title="Attention Is All You Need",
        year=2017,
        doi="10.48550/arXiv.1706.03762",
        arxiv_id="1706.03762",
        oa_status="green",
        oa_url="https://arxiv.org/abs/1706.03762",
        oa_landing_url="https://arxiv.org/abs/1706.03762",
        pdf_url="https://export.arxiv.org/pdf/1706.03762",
        oa_license="arxiv",
    )
    pdf = _mini_pdf(
        "The Transformer relies entirely on attention mechanisms and dispenses with recurrence and convolutions in the model architecture."
    )

    class StoredAcquire:
        def acquire(self, work: WorkRecord) -> AcquisitionResult:
            assert work.id == discovered.id
            checksum, storage_path = LocalDocumentStore(settings.documents_dir).put(pdf)
            return AcquisitionResult(
                work_id=work.id,
                status=AcquisitionStatus.RETRIEVED,
                url=work.pdf_url,
                content_type="application/pdf",
                checksum=checksum,
                byte_size=len(pdf),
                storage_path=storage_path,
                text_status=TextStatus.PARSED,
            )

    router_calls = {"count": 0}

    def handler(model: str, prompt: str) -> str:
        if "User request:" in prompt:
            router_calls["count"] += 1
            if router_calls["count"] == 1:
                return '{"action":"tool","tool":"find_papers","query":"Attention Is All You Need arXiv","reason":"locate the named paper"}'
            return '{"action":"answer"}'
        if "Reader's focus:" in prompt:
            return '{"highlights":[{"page":1,"quote":"The Transformer relies entirely on attention mechanisms and dispenses with recurrence and convolutions in the model architecture.","note":"Core architectural contribution"}]}'
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        return "The paper is open in the reader with its key passage highlighted."

    def fake_execute(tool: str, query: str, reason: str):
        assert tool == "find_papers"
        return (
            ToolStep(
                tool=tool,
                query=query,
                reason=reason,
                results=[
                    {"id": discovered.id, "title": discovered.title, "url": discovered.oa_url}
                ],
            ),
            [discovered],
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_execute)
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        run = Run(org_id=org.id, question="q", status="completed")
        session.add(run)
        session.flush()
        answer = answer_question(
            session,
            run,
            mock_pool(handler),
            "Find Attention Is All You Need on arXiv, open it and highlight the key passage",
            acquirer=StoredAcquire(),
        )
        source = session.scalar(
            select(SourceRecordRow).where(
                SourceRecordRow.run_id == run.id, SourceRecordRow.work_id == discovered.id
            )
        )
        stored = session.get(WorkRow, discovered.id)
        panels = [
            message.payload
            for message in chat_history(session, run.id)
            if message.role == "tool" and (message.payload or {}).get("kind") == "paper"
        ]
    assert source is not None and source.source == "find_papers-chat"
    assert stored is not None and stored.payload["arxiv_id"] == "1706.03762"
    assert "find_papers" in answer.tools_used
    assert "show_paper" in answer.tools_used
    assert panels and panels[-1]["highlights"][0]["page"] == 1


def test_read_arxiv_page_repairs_oa_metadata_without_provider_hit(settings: Settings) -> None:
    """The arXiv page itself bridges stale metadata to the canonical PDF URL."""
    from sixsentences_server.chat.service import (
        ToolStep,
        _arxiv_discovery_from_tool_step,
        _hydrate_open_access_from_discovery,
    )

    init_db()
    with db_session() as session:
        stale = WorkRecord(
            id="W-STALE-ARXIV", title="Attention Is All You Need", year=2025, doi="10.65215/stale"
        )
        session.add(
            WorkRow(
                id=stale.id,
                doi=stale.doi,
                title=stale.title,
                year=stale.year,
                payload=stale.model_dump(mode="json"),
            )
        )
        session.flush()
        step = ToolStep(
            tool="read_webpage",
            query="https://arxiv.org/html/1706.03762",
            results=[
                {
                    "url": "https://arxiv.org/abs/1706.03762",
                    "title": "[1706.03762] Attention Is All You Need",
                    "domain": "arxiv.org",
                }
            ],
        )
        discovered = _arxiv_discovery_from_tool_step(session, stale.id, step)
        assert discovered is not None
        assert discovered.arxiv_id == "1706.03762"
        assert discovered.pdf_url == "https://export.arxiv.org/pdf/1706.03762"
        unrelated = step.model_copy(
            update={
                "results": [
                    {
                        "url": "https://arxiv.org/abs/2401.99999",
                        "title": "An Unrelated Paper About Protein Folding",
                    }
                ]
            }
        )
        assert _arxiv_discovery_from_tool_step(session, stale.id, unrelated) is None
        assert _hydrate_open_access_from_discovery(session, stale.id, [discovered])
        repaired = session.get(WorkRow, stale.id)
    assert repaired is not None
    assert repaired.payload["oa_status"] == "green"
    assert repaired.payload["arxiv_id"] == "1706.03762"
    assert repaired.payload["pdf_url"] == "https://export.arxiv.org/pdf/1706.03762"


def test_ingest_transcribes_photographed_sources(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A phone shot of a lecturer's PDF becomes a readable one-page document:
    stored as PDF, its text from a faithful vision transcription."""
    import io

    from PIL import Image

    init_db()
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload.transcribe_image",
        lambda content, **kwargs: "Kapitel 3: Methodik\n\nDie Stichprobe umfasst 42 Studierende.",
    )
    shot = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(shot, format="JPEG")
    with db_session() as session:
        org = get_default_org(session)
        doc = ingest_document(
            session,
            org_id=org.id,
            run_id=None,
            content=shot.getvalue(),
            filename="foto-von-dozentin.jpg",
            oa_client=_StubOA(),
        )
        assert doc.content_type == "application/pdf"
        assert doc.text_status == TextStatus.PARSED.value
        work = session.get(WorkRow, doc.work_id)
        assert work is not None and work.title == "Kapitel 3: Methodik"
        from sixsentences_server.acquisition.store import LocalDocumentStore

        store = LocalDocumentStore(settings.documents_dir)
        assert store.get(doc.checksum)[:5] == b"%PDF-"
        assert "Stichprobe umfasst 42" in (store.get_text(doc.checksum) or "")
    from sixsentences_server.acquisition.upload import UploadError
    from sixsentences_server.acquisition.vision import ImageReadError

    def refuse(content, **kwargs):
        raise ImageReadError("the image could not be transcribed; try a sharper photo")

    monkeypatch.setattr("sixsentences_server.acquisition.upload.transcribe_image", refuse)
    with db_session() as session:
        org = get_default_org(session)
        with pytest.raises(UploadError, match="sharper photo"):
            ingest_document(
                session,
                org_id=org.id,
                run_id=None,
                content=shot.getvalue(),
                filename="unscharf.jpg",
                oa_client=_StubOA(),
            )
