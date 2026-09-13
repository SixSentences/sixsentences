"""Creator-private local-PDF chats exposed to the paired macOS Companion."""

from __future__ import annotations

import base64
import hashlib
import io
import threading
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import sixsentences_server.api.app as appmod
from sixsentences_server.api.app import _privacy_export_tables, create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.auth import AuthContext, add_member, authenticate
from sixsentences_server.core.db import (
    CapacityReservationRow,
    CompanionPaperChatReceiptRow,
    CreditEventRow,
    DocumentAnnotationRow,
    DocumentRow,
    LLMCallRow,
    Org,
    Run,
    SourceRecordRow,
    User,
    db_session,
)
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.llm.pool import LLMPool

PASSWORD = "StrongPass123!"


def _pdf(text: str) -> bytes:
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
    for index, value in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + value + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    )
    return bytes(out)


def _register(settings: Settings) -> tuple[TestClient, str, int]:
    del settings
    app = create_app()
    browser = TestClient(app)
    registered = browser.post(
        "/auth/register",
        json={
            "email": "paper-owner@example.org",
            "password": PASSWORD,
            "org_name": "Paper Chat Workspace",
            "name": "Paper Owner",
        },
    )
    assert registered.status_code == 201, registered.text
    browser.headers["Authorization"] = f"Bearer {registered.json()['token']}"
    org_id = int(browser.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"
        owner = session.scalar(select(User).where(User.email == "paper-owner@example.org"))
        assert owner is not None
        from sixsentences_server.core.auth import create_api_key

        key = create_api_key(
            session,
            owner,
            "Live Companion · Paper tests",
            scopes=("companion:read", "companion:write"),
        )
    return (browser, key, org_id)


def _body(
    *,
    request_id: str = "paper-open-0001",
    text: str = "Local PDF passages remain grounded in the selected page.",
) -> dict[str, object]:
    content = _pdf(text)
    return {
        "client_request_id": request_id,
        "filename": "local-paper.pdf",
        "content_base64": base64.b64encode(content).decode(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "project_id": None,
    }


def test_paper_chat_upload_replay_dedupes_and_keeps_narrow_scope(settings: Settings) -> None:
    browser, key, org_id = _register(settings)
    companion = TestClient(browser.app, headers={"Authorization": f"Bearer {key}"})
    body = _body()
    first = companion.post("/companion/paper-chats", json=body)
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["status"] == "created"
    assert result["paper"]["library_document_id"] != result["paper"]["chat_document_id"]
    assert result["chat"]["web_url"].endswith(f"/r/{result['chat']['id']}")
    replay = companion.post("/companion/paper-chats", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == result
    assert companion.get("/documents").status_code == 403
    assert companion.get("/runs").status_code == 403
    assert companion.get(f"/runs/{result['chat']['id']}/chat").status_code == 403
    assert companion.get(f"/companion/paper-chats/{result['chat']['id']}").json() == result
    second_body = dict(body)
    second_body["client_request_id"] = "paper-open-0002"
    duplicate = companion.post("/companion/paper-chats", json=second_body)
    assert duplicate.status_code == 200, duplicate.text
    duplicate_result = duplicate.json()
    assert duplicate_result["status"] == "already_saved"
    assert (
        duplicate_result["paper"]["library_document_id"] == result["paper"]["library_document_id"]
    )
    assert duplicate_result["paper"]["chat_document_id"] != result["paper"]["chat_document_id"]
    assert duplicate_result["chat"]["id"] != result["chat"]["id"]
    with db_session() as session:
        assert (
            session.scalar(
                select(func.count(CompanionPaperChatReceiptRow.id)).where(
                    CompanionPaperChatReceiptRow.org_id == org_id
                )
            )
            == 2
        )
        assert (
            session.scalar(select(func.count(DocumentRow.id)).where(DocumentRow.org_id == org_id))
            == 3
        )
        assert session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id)) == 2
    conflict = companion.post(
        "/companion/paper-chats",
        json=_body(text="A different local PDF must not reuse this request receipt."),
    )
    assert conflict.status_code == 409


def test_paper_chat_is_private_to_creator_even_inside_one_workspace(
    settings: Settings, monkeypatch
) -> None:
    browser, key, org_id = _register(settings)
    owner_companion = TestClient(browser.app, headers={"Authorization": f"Bearer {key}"})
    created = owner_companion.post("/companion/paper-chats", json=_body()).json()
    chat_id = created["chat"]["id"]
    with db_session() as session:
        private_run = session.scalar(select(Run).where(Run.public_id == chat_id))
        assert private_run is not None
        private_run.title = "SECRET PRIVATE PAPER TITLE"
        private_run.question = "SECRET PRIVATE PAPER QUESTION"
        private_run_id = private_run.id
        chat_document_id = int(created["paper"]["chat_document_id"])
        library_document_id = int(created["paper"]["library_document_id"])
        add_member(session, org_id, "paper-member@example.org", PASSWORD, "member")
        member_token = authenticate(session, "paper-member@example.org", PASSWORD)
        member = session.scalar(select(User).where(User.email == "paper-member@example.org"))
        assert member is not None
        from sixsentences_server.core.auth import create_api_key

        member_key = create_api_key(
            session, member, "Live Companion · Member", scopes=("companion:read", "companion:write")
        )
    member = TestClient(browser.app, headers={"Authorization": f"Bearer {member_key}"})
    for path in (
        f"/companion/paper-chats/{chat_id}",
        f"/companion/paper-chats/{chat_id}/history",
        f"/companion/paper-chats/{chat_id}/turns/latest",
    ):
        assert member.get(path).status_code == 404
    assert (
        member.post(f"/companion/paper-chats/{chat_id}/turns/member-turn-0001/stop").status_code
        == 404
    )
    member_browser = TestClient(browser.app, headers={"Authorization": f"Bearer {member_token}"})
    assert all(run["public_id"] != chat_id for run in member_browser.get("/runs").json())
    assert member_browser.get(f"/runs/{chat_id}").status_code == 404
    assert member_browser.get(f"/documents/{chat_document_id}/file").status_code == 404
    assert member_browser.get(f"/documents/{chat_document_id}/annotations").status_code == 404
    assert (
        member_browser.post(
            f"/documents/{chat_document_id}/translation", json={"page": 1, "language": "de"}
        ).status_code
        == 404
    )
    assert member_browser.post(
        f"/documents/{chat_document_id}/figures",
        json={
            "image_base64": base64.b64encode(b"not-a-private-crop").decode(),
            "page": 1,
            "caption": "private",
        },
    ).status_code in {404, 422}
    assert member_browser.get(f"/documents/{library_document_id}/file").status_code == 200
    assert member_browser.get("/documents", params={"q": "SECRET PRIVATE"}).json() == []
    assert browser.post(f"/runs/{chat_id}/share").status_code == 409
    assert (
        browser.post(
            "/writer",
            json={"title": "No private link", "template": "blank", "run_ids": [private_run_id]},
        ).status_code
        == 409
    )
    assert (
        member_browser.post(
            "/writer",
            json={"title": "No guessed link", "template": "blank", "run_ids": [private_run_id]},
        ).status_code
        == 404
    )
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-only-figure-key")
    from sixsentences_server.config import get_settings

    get_settings.cache_clear()
    assert (
        browser.post(
            "/figures", json={"prompt": "Do not bind this private paper", "run_id": private_run_id}
        ).status_code
        == 409
    )
    assert (
        member_browser.post(
            "/figures", json={"prompt": "Do not bind a guessed paper", "run_id": private_run_id}
        ).status_code
        == 404
    )
    project = browser.post("/projects", json={"name": "Shared project"}).json()
    with db_session() as session:
        private_run = session.get(Run, private_run_id)
        assert private_run is not None
        private_run.project_id = int(project["id"])
    risk_body = {"run_id": private_run_id, "work_id": str(created["paper"]["work_id"])}
    assert (
        browser.post(f"/projects/{project['id']}/risk-of-bias", json=risk_body).status_code == 409
    )
    assert (
        member_browser.post(f"/projects/{project['id']}/risk-of-bias", json=risk_body).status_code
        == 404
    )
    with db_session() as session:
        normal_run = Run(
            org_id=org_id,
            question="Shared normal research chat",
            title="Shared normal research chat",
            status="completed",
            config={"mode": "ask"},
        )
        session.add(normal_run)
        session.flush()
        session.add(
            SourceRecordRow(
                org_id=org_id,
                run_id=normal_run.id,
                work_id=str(created["paper"]["work_id"]),
                source="upload",
            )
        )
        normal_public_id = normal_run.public_id
    routed = False

    def handler(_model: str, prompt: str) -> str:
        nonlocal routed
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        if "User request:" in prompt and (not routed):
            routed = True
            return (
                '{"action":"tool","tool":"recall_history","query":"","reason":"recall prior chats"}'
            )
        if "User request:" in prompt:
            return '{"action":"answer"}'
        return "No creator-private paper chat was recalled."

    monkeypatch.setattr(appmod, "_build_pool", lambda _settings: mock_pool(handler))
    recalled = member_browser.post(
        f"/runs/{normal_public_id}/chat", json={"question": "What did we discuss before?"}
    )
    assert recalled.status_code == 200, recalled.text
    history = member_browser.get(f"/runs/{normal_public_id}/chat").json()
    recall_step = next(
        row
        for row in history
        if row["role"] == "tool" and row["payload"].get("tool") == "recall_history"
    )
    serialized_recall = str(recall_step["payload"]["results"])
    assert chat_id not in serialized_recall
    assert "SECRET PRIVATE PAPER TITLE" not in serialized_recall
    assert "SECRET PRIVATE PAPER QUESTION" not in serialized_recall


def test_paper_chat_selection_is_server_bound_and_stream_is_idempotent(
    settings: Settings, monkeypatch
) -> None:

    def handler(_model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        if "User request:" in prompt:
            return '{"action":"answer"}'
        return "The selected passage is grounded in the local paper."

    monkeypatch.setattr(appmod, "_build_pool", lambda _settings: mock_pool(handler))
    browser, key, _org_id = _register(settings)
    companion = TestClient(browser.app, headers={"Authorization": f"Bearer {key}"})
    result = companion.post("/companion/paper-chats", json=_body()).json()
    chat_id = result["chat"]["id"]
    turn_body = {
        "turn_id": "paper-turn-0001",
        "question": "Why is this passage important?",
        "selection": {"page": 1, "quote": "passages remain grounded in the selected page"},
    }
    streamed = companion.post(f"/companion/paper-chats/{chat_id}/turns/stream", json=turn_body)
    assert streamed.status_code == 200, streamed.text
    assert "event: turn.completed" in streamed.text
    replay = companion.post(f"/companion/paper-chats/{chat_id}/turns/stream", json=turn_body)
    assert replay.status_code == 200, replay.text
    history = companion.get(f"/companion/paper-chats/{chat_id}/history").json()
    user_rows = [row for row in history if row["role"] == "user"]
    assert len(user_rows) == 1
    assert (
        user_rows[0]["payload"]["selection"]["document_id"] == result["paper"]["chat_document_id"]
    )
    assert "document_id" not in turn_body["selection"]
    wrong_page = companion.post(
        f"/companion/paper-chats/{chat_id}/turns/stream",
        json={
            "turn_id": "paper-turn-0002",
            "question": "What does this say?",
            "selection": {"page": 2, "quote": "passages remain grounded"},
        },
    )
    assert wrong_page.status_code == 422
    invented = companion.post(
        f"/companion/paper-chats/{chat_id}/turns/stream",
        json={
            "turn_id": "paper-turn-0003",
            "question": "What does this say?",
            "selection": {"page": 1, "quote": "this quote is not in the PDF"},
        },
    )
    assert invented.status_code == 422
    injected_id = companion.post(
        f"/companion/paper-chats/{chat_id}/turns/stream",
        json={
            "turn_id": "paper-turn-0004",
            "question": "No arbitrary document ids",
            "selection": {
                "page": 1,
                "quote": "passages remain grounded",
                "document_id": result["paper"]["library_document_id"],
            },
        },
    )
    assert injected_id.status_code == 422


def test_stream_stop_after_provider_return_keeps_usage_without_settlement(
    settings: Settings, monkeypatch
) -> None:
    provider_returned = threading.Event()
    release_provider = threading.Event()
    answer_text = "The provider completed this grounded local-paper answer."

    def handler(_model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        if "User request:" in prompt:
            return '{"action":"answer"}'
        return answer_text

    original_complete = LLMPool.complete

    def complete_at_stop_boundary(self: LLMPool, *args, **kwargs):
        response = original_complete(self, *args, **kwargs)
        if kwargs.get("on_delta") is not None:
            provider_returned.set()
            assert release_provider.wait(timeout=5)
        return response

    monkeypatch.setattr(LLMPool, "complete", complete_at_stop_boundary)
    monkeypatch.setattr(appmod, "_build_pool", lambda _settings: mock_pool(handler))
    browser, key, _org_id = _register(settings)
    companion = TestClient(browser.app, headers={"Authorization": f"Bearer {key}"})
    created = companion.post("/companion/paper-chats", json=_body()).json()
    chat_id = str(created["chat"]["id"])
    turn_id = "provider-return-stop-0001"
    result: dict[str, object] = {}

    def stream() -> None:
        result["response"] = companion.post(
            f"/companion/paper-chats/{chat_id}/turns/stream",
            json={"turn_id": turn_id, "question": "Explain the local paper."},
        )

    worker = threading.Thread(target=stream)
    worker.start()
    assert provider_returned.wait(timeout=5)
    stopped = companion.post(f"/companion/paper-chats/{chat_id}/turns/{turn_id}/stop")
    release_provider.set()
    worker.join(timeout=5)
    assert stopped.status_code == 200, stopped.text
    assert not worker.is_alive()
    response = result["response"]
    assert hasattr(response, "text")
    assert "event: turn.cancelled" in response.text
    assert "event: turn.completed" not in response.text
    with db_session() as session:
        run = session.scalar(select(Run).where(Run.public_id == chat_id))
        assert run is not None
        calls = session.scalars(
            select(LLMCallRow).where(LLMCallRow.run_id == run.id, LLMCallRow.resource_id == turn_id)
        ).all()
        assert calls
        assert any(call.output_tokens >= len(answer_text) // 4 for call in calls)
        charges = session.scalars(
            select(CreditEventRow).where(CreditEventRow.run_id == run.id)
        ).all()
        assert [charge.action for charge in charges].count("question") == 1
        assert all(charge.action != "agent_tools" for charge in charges)
        reservation = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.resource_id == turn_id)
        )
        assert reservation is not None and reservation.status == "cancelled"


def test_companion_upload_has_exact_size_limit_and_busy_guard(
    settings: Settings, monkeypatch
) -> None:
    browser, key, _org_id = _register(settings)
    companion = TestClient(browser.app, headers={"Authorization": f"Bearer {key}"})
    exact = b"%PDF-" + b"x" * (appmod.MAX_COMPANION_PAPER_BYTES - 5)
    exact_body = {
        "client_request_id": "exact-size-paper-0001",
        "filename": "exact-limit.pdf",
        "content_base64": base64.b64encode(exact).decode(),
        "sha256": hashlib.sha256(exact).hexdigest(),
        "project_id": None,
    }
    appmod.CompanionPaperChatCreate.model_validate(exact_body)
    over = exact + b"x"
    over_body = {
        **exact_body,
        "client_request_id": "over-size-paper-0001",
        "content_base64": base64.b64encode(over).decode(),
        "sha256": hashlib.sha256(over).hexdigest(),
    }
    too_large = companion.post("/companion/paper-chats", json=over_body)
    assert too_large.status_code == 413, too_large.text

    class BusySlots:
        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

        def release(self) -> None:
            raise AssertionError("an unacquired upload slot must not be released")

    monkeypatch.setattr(appmod, "_companion_paper_upload_slots", BusySlots())
    busy = companion.post("/companion/paper-chats", json=_body(request_id="busy-paper-0001"))
    assert busy.status_code == 503, busy.text
    assert busy.json()["detail"]["code"] == "companion_upload_busy"


def test_stale_upload_claim_cannot_abandon_its_replacement(settings: Settings) -> None:
    browser, _key, org_id = _register(settings)
    del browser
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "paper-owner@example.org"))
        assert owner is not None
        user_id = owner.id
        session.add(
            CompanionPaperChatReceiptRow(
                org_id=org_id,
                user_id=user_id,
                client_request_id="stale-takeover-0001",
                payload_sha256="c" * 64,
                claim_token="replacement-claim-token",
                status="processing",
                result={},
            )
        )
    ctx = AuthContext(
        user_id=user_id, org_id=org_id, token_id=1, role="owner", email="paper-owner@example.org"
    )
    appmod._abandon_companion_paper_reservation(
        ctx, "stale-takeover-0001", "c" * 64, "stale-original-claim-token"
    )
    with db_session() as session:
        receipt = session.scalar(
            select(CompanionPaperChatReceiptRow).where(
                CompanionPaperChatReceiptRow.client_request_id == "stale-takeover-0001"
            )
        )
        assert receipt is not None
        assert receipt.claim_token == "replacement-claim-token"


def test_deleting_paper_chat_keeps_library_original_and_removes_receipt(settings: Settings) -> None:
    browser, key, org_id = _register(settings)
    companion = TestClient(browser.app, headers={"Authorization": f"Bearer {key}"})
    created = companion.post("/companion/paper-chats", json=_body()).json()
    chat_id = created["chat"]["id"]
    library_id = created["paper"]["library_document_id"]
    deleted = browser.delete(f"/runs/{chat_id}")
    assert deleted.status_code == 200, deleted.text
    with db_session() as session:
        library = session.get(DocumentRow, library_id)
        assert library is not None and library.run_id is None and library.storage_path
        assert (
            session.scalar(
                select(func.count(CompanionPaperChatReceiptRow.id)).where(
                    CompanionPaperChatReceiptRow.org_id == org_id
                )
            )
            == 0
        )
    assert companion.get(f"/companion/paper-chats/{chat_id}").status_code == 404


def test_library_then_chat_deletion_removes_the_last_private_pdf_blob(settings: Settings) -> None:
    browser, key, _org_id = _register(settings)
    companion = TestClient(browser.app, headers={"Authorization": f"Bearer {key}"})
    created = companion.post("/companion/paper-chats", json=_body()).json()
    library_id = int(created["paper"]["library_document_id"])
    chat_id = str(created["chat"]["id"])
    with db_session() as session:
        library = session.get(DocumentRow, library_id)
        assert library is not None and library.checksum and library.storage_path
        checksum = library.checksum
        blob_path = Path(library.storage_path)
    assert blob_path.exists()
    appmod._cleanup_document_checksums([checksum], settings)
    assert blob_path.exists()
    assert browser.delete(f"/documents/{library_id}").status_code == 204
    assert blob_path.exists()
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "paper-owner@example.org"))
        assert owner is not None
        add_member(session, owner.org_id, "clone-probe@example.org", PASSWORD, "member")
        probe_token = authenticate(session, "clone-probe@example.org", PASSWORD)
    probe = TestClient(browser.app, headers={"Authorization": f"Bearer {probe_token}"})
    escaped = probe.post(
        "/runs",
        json={
            "question": "Try to clone a private ledger row",
            "mode": "ask",
            "document_ids": [created["paper"]["chat_document_id"]],
        },
    )
    assert escaped.status_code == 404
    assert browser.delete(f"/runs/{chat_id}").status_code == 200
    assert not blob_path.exists()
    assert not (settings.documents_dir / "text" / f"{checksum}.txt").exists()
    with db_session() as session:
        assert not session.scalar(
            select(func.count(DocumentRow.id)).where(DocumentRow.checksum == checksum)
        )


def test_member_export_and_erasure_keep_only_the_shared_library_original(
    settings: Settings, enforced_foreign_keys: None
) -> None:
    browser, _owner_key, org_id = _register(settings)
    with db_session() as session:
        add_member(session, org_id, "paper-member@example.org", PASSWORD, "member")
        member_token = authenticate(session, "paper-member@example.org", PASSWORD)
        member_user = session.scalar(select(User).where(User.email == "paper-member@example.org"))
        owner_user = session.scalar(select(User).where(User.email == "paper-owner@example.org"))
        assert member_user is not None and owner_user is not None
        member_id = member_user.id
        from sixsentences_server.core.auth import create_api_key

        member_key = create_api_key(
            session,
            member_user,
            "Live Companion · Member paper",
            scopes=("companion:read", "companion:write"),
        )
    member_companion = TestClient(browser.app, headers={"Authorization": f"Bearer {member_key}"})
    created_response = member_companion.post("/companion/paper-chats", json=_body())
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    run_public_id = created["chat"]["id"]
    library_id = int(created["paper"]["library_document_id"])
    chat_document_id = int(created["paper"]["chat_document_id"])
    orphan_response = member_companion.post(
        "/companion/paper-chats",
        json=_body(
            request_id="paper-open-orphan-0001",
            text="This second private PDF is removed after its Library original.",
        ),
    )
    assert orphan_response.status_code == 201, orphan_response.text
    orphan = orphan_response.json()
    orphan_library_id = int(orphan["paper"]["library_document_id"])
    orphan_chat_document_id = int(orphan["paper"]["chat_document_id"])
    with db_session() as session:
        orphan_library = session.get(DocumentRow, orphan_library_id)
        assert orphan_library is not None and orphan_library.storage_path
        orphan_blob_path = Path(orphan_library.storage_path)
    with db_session() as session:
        member_user = session.get(User, member_id)
        owner_user = session.scalar(select(User).where(User.email == "paper-owner@example.org"))
        assert member_user is not None and owner_user is not None
        session.add(
            DocumentAnnotationRow(
                org_id=org_id,
                document_id=chat_document_id,
                user_id=member_id,
                source="user",
                page=1,
                quote="VICTIM PRIVATE SELECTED QUOTE",
                note="VICTIM PRIVATE NOTE",
            )
        )
        session.flush()
        member_tables, member_inventory = _privacy_export_tables(session, user=member_user)
        owner_tables, _owner_inventory = _privacy_export_tables(session, user=owner_user)
        assert member_inventory["scope"] == "personal"
        assert len(member_tables[CompanionPaperChatReceiptRow.__tablename__]) == 2
        assert {row["public_id"] for row in member_tables[Run.__tablename__]} == {
            run_public_id,
            orphan["chat"]["id"],
        }
        assert {row["id"] for row in member_tables[DocumentRow.__tablename__]} == {
            chat_document_id,
            orphan_chat_document_id,
        }
        assert CompanionPaperChatReceiptRow.__tablename__ not in owner_tables
        assert all(
            row.get("quote") != "VICTIM PRIVATE SELECTED QUOTE"
            for row in owner_tables.get(DocumentAnnotationRow.__tablename__, [])
        )
        assert all(
            row["public_id"] != run_public_id for row in owner_tables.get(Run.__tablename__, [])
        )
        assert chat_document_id not in {
            row["id"] for row in owner_tables.get(DocumentRow.__tablename__, [])
        }
        assert library_id in {row["id"] for row in owner_tables[DocumentRow.__tablename__]}
    member_browser = TestClient(browser.app, headers={"Authorization": f"Bearer {member_token}"})
    assert member_browser.delete(f"/documents/{orphan_library_id}").status_code == 204
    assert orphan_blob_path.exists()
    owner_export = browser.post("/auth/account/export", json={"password": PASSWORD})
    assert owner_export.status_code == 200, owner_export.text
    with zipfile.ZipFile(io.BytesIO(owner_export.content)) as archive:
        names = set(archive.namelist())
        assert not any(name.startswith(f"files/library/{chat_document_id}/") for name in names)
        assert not any(
            name.startswith(f"files/library/{orphan_chat_document_id}/") for name in names
        )
    deleted = member_browser.request("DELETE", "/auth/account", json={"password": PASSWORD})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": "account"}
    with db_session() as session:
        assert session.get(User, member_id) is None
        assert session.scalar(select(Run).where(Run.public_id == run_public_id)) is None
        assert session.get(DocumentRow, chat_document_id) is None
        assert session.get(DocumentRow, orphan_chat_document_id) is None
        assert session.get(DocumentRow, orphan_library_id) is None
        library = session.get(DocumentRow, library_id)
        assert library is not None and library.run_id is None and library.storage_path
        assert (
            session.scalar(
                select(func.count(CompanionPaperChatReceiptRow.id)).where(
                    CompanionPaperChatReceiptRow.org_id == org_id
                )
            )
            == 0
        )
    assert not orphan_blob_path.exists()
