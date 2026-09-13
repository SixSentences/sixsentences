"""API lifecycle tests for repository-grounded manuscript prose."""

from __future__ import annotations

import hashlib
import importlib
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_repository_graphics_api import _database_app, _make_ready, _register, _request

from sixsentences_server.agent.ledger import agent_turn_action_id
from sixsentences_server.api.app import _privacy_export_tables
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    AgentEventRow,
    AgentTurnRow,
    CapacityReservationRow,
    LLMCallRow,
    RepositoryAnalysisRow,
    User,
    WriterContributionRow,
    WriterDocumentRow,
    WriterMessageRow,
    db_session,
)
from sixsentences_server.llm.base import LLMUsage


class ApiSelectionPool:
    """Return a fixed closed selection while recording provider egress."""

    def __init__(self, selection: dict[str, object]) -> None:
        self.selection = selection
        self.calls: list[dict[str, Any]] = []
        self.on_usage = None
        self.cancel_check = None
        self.budget = None

    def complete_json(self, _task: object, *, system: str, prompt: str, max_tokens: int) -> object:
        self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
        return SimpleNamespace(text=json.dumps(self.selection), provider="test", model="test-model")


class OpaqueApiSelectionPool(ApiSelectionPool):
    """Select provider-local aliases from the private topology payload."""

    def __init__(self) -> None:
        super().__init__({})

    def complete_json(self, _task: object, *, system: str, prompt: str, max_tokens: int) -> object:
        topology = dict(json.loads(prompt)["topology"])
        self.selection = {
            "selected_node_ids": [node["node_id"] for node in topology["nodes"]],
            "selected_edge_ids": [edge["edge_id"] for edge in topology["edges"][:3]],
        }
        return super().complete_json(_task, system=system, prompt=prompt, max_tokens=max_tokens)


def _ready_analysis(owner: Any, request_id: str) -> tuple[str, dict[str, object]]:
    created = owner.post("/repository-analyses", json=_request(request_id))
    assert created.status_code == 202, created.text
    public_id = str(created.json()["public_id"])
    _make_ready(public_id)
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(RepositoryAnalysisRow.public_id == public_id)
        )
        assert row is not None
        return (public_id, dict(row.diagram_spec or {}))


def _preview_body(request_id: str, *, kind: str = "description") -> dict[str, object]:
    return {
        "request_id": request_id,
        "kind": kind,
        "language": "en",
        "ai_generation_confirmed": True,
    }


def test_delete_purges_every_private_preview_turn_and_event(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="repo-prose-delete@example.org", org="Repo prose delete")
    analysis_id, spec = _ready_analysis(owner, "prose-delete-analysis")
    node_ids = [str(node["id"]) for node in spec["nodes"]]
    pool = ApiSelectionPool({"selected_node_ids": node_ids, "selected_edge_ids": []})
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: pool)
    response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/preview",
        json=_preview_body("preview-delete-001", kind="caption"),
    )
    assert response.status_code == 200, response.text
    deleted = owner.delete(f"/repository-analyses/{analysis_id}")
    assert deleted.status_code == 200, deleted.text
    with db_session() as session:
        assert (
            session.scalar(
                select(func.count(AgentTurnRow.id)).where(
                    AgentTurnRow.resource_id == analysis_id,
                    AgentTurnRow.resource_kind.in_(("repository-prose", "repository-proposal")),
                )
            )
            == 0
        )
        assert session.scalar(select(func.count(AgentEventRow.id))) == 0


def test_proposal_is_caption_safe_idempotent_and_manual_apply_only(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="repo-proposal@example.org", org="Repo proposal")
    analysis_id, spec = _ready_analysis(owner, "proposal-analysis-request")
    node_ids = [str(node["id"]) for node in spec["nodes"]]
    edge_ids = [str(edge["id"]) for edge in spec["edges"]]
    pool = ApiSelectionPool({"selected_node_ids": node_ids, "selected_edge_ids": edge_ids[:3]})
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(RepositoryAnalysisRow.public_id == analysis_id)
        )
        assert row is not None
        diagram = dict(row.diagram_spec or {})
        diagram_nodes = [dict(node) for node in diagram.get("nodes", [])]
        diagram_nodes[0]["label"] = "safe_component"
        diagram["nodes"] = diagram_nodes
        evidence = [dict(record) for record in list(row.evidence or [])]
        supporting_ids = set(diagram_nodes[0]["evidence_ids"])
        for record in evidence:
            if record.get("id") in supporting_ids:
                record["summary"] = (
                    f"{record.get('summary') or ''}; canonical display label safe_component;"
                )
        row.diagram_spec = diagram
        row.evidence = evidence
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: pool)
    compile_ok = {"value": False}
    compiled_sources: list[str] = []

    def compile_stub(source: str, *_args: object, **_kwargs: object) -> object:
        compiled_sources.append(source)
        return SimpleNamespace(
            ok=compile_ok["value"],
            errors=[] if compile_ok["value"] else ["synthetic compile failure"],
            log_tail="repository proposal compile checked",
        )

    monkeypatch.setattr(api_module, "compile_document", compile_stub)
    preview_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/preview",
        json=_preview_body("proposal-preview-001"),
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    document_response = owner.post(
        "/writer", json={"title": "Repository manuscript", "template": "blank"}
    )
    assert document_response.status_code == 201, document_response.text
    document = document_response.json()
    failed_compile = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals",
        json={
            "request_id": "proposal-compile-fail",
            "preview_request_id": preview["request_id"],
            "preview_sha256": preview["preview_sha256"],
            "writer_document_id": document["public_id"],
            "expected_writer_revision": document["revision"],
        },
    )
    assert failed_compile.status_code == 409, failed_compile.text
    assert failed_compile.json()["detail"]["code"] == "repository_prose_candidate_compile_failed"
    with db_session() as session:
        assert session.scalar(select(func.count(WriterMessageRow.id))) == 0
        assert (
            session.scalar(
                select(func.count(WriterContributionRow.id)).where(
                    WriterContributionRow.kind == "repository_prose_proposal"
                )
            )
            == 0
        )
    compile_ok["value"] = True
    proposal_body = {
        "request_id": "proposal-request-001",
        "preview_request_id": preview["request_id"],
        "preview_sha256": preview["preview_sha256"],
        "writer_document_id": document["public_id"],
        "expected_writer_revision": document["revision"],
    }
    proposal_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals", json=proposal_body
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal = proposal_response.json()
    assert proposal["requires_manual_review"] is True
    assert proposal["idempotent"] is False
    edit = proposal["edits"][0]
    assert edit["path"] == "main.tex"
    assert edit["find"] == "\\end{document}"
    assert "% sixsentences-repository-prose" in edit["replace"]
    assert "safe\\_component" in edit["replace"]
    assert "safe_component" not in edit["replace"]
    assert all("safe\\_component" in source for source in compiled_sources)
    replay = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals", json=proposal_body
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent"] is True
    assert replay.json()["message_id"] == proposal["message_id"]
    conflicting = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals",
        json={**proposal_body, "preview_sha256": "0" * 64},
    )
    assert conflicting.status_code == 409
    with db_session() as session:
        stored_message = session.get(WriterMessageRow, proposal["message_id"])
        assert stored_message is not None
        repository_prose = dict((stored_message.payload or {}).get("repository_prose") or {})
        assert repository_prose["repository_url"] == "https://github.com/acme/example"
        assert repository_prose["subpath"] is None
        assert len(repository_prose["compile_input_sha256"]) == 64
        assert (
            session.scalar(
                select(func.count(WriterMessageRow.id)).where(
                    WriterMessageRow.id == proposal["message_id"]
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count(WriterContributionRow.id)).where(
                    WriterContributionRow.kind == "repository_prose_proposal"
                )
            )
            == 1
        )
    apply_body = {
        "message_id": proposal["message_id"],
        "edits": [{"path": edit["path"], "find": edit["find"], "replace": edit["replace"]}],
    }
    automatic = owner.post(
        f"/writer/{document['public_id']}/edits/apply", json={**apply_body, "auto": True}
    )
    assert automatic.status_code == 409
    manual = owner.post(f"/writer/{document['public_id']}/edits/apply", json=apply_body)
    assert manual.status_code == 200, manual.text
    assert manual.json()["idempotent"] is False
    repeated_manual = owner.post(f"/writer/{document['public_id']}/edits/apply", json=apply_body)
    assert repeated_manual.status_code == 200, repeated_manual.text
    assert repeated_manual.json()["idempotent"] is True
    caption_pool = ApiSelectionPool(
        {"selected_node_ids": node_ids[: min(6, len(node_ids))], "selected_edge_ids": []}
    )
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: caption_pool)
    caption_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/preview",
        json=_preview_body("caption-preview-001", kind="caption"),
    )
    assert caption_response.status_code == 200, caption_response.text
    caption = caption_response.json()
    caption_proposal = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals",
        json={
            "request_id": "caption-proposal-001",
            "preview_request_id": caption["request_id"],
            "preview_sha256": caption["preview_sha256"],
            "writer_document_id": document["public_id"],
            "expected_writer_revision": manual.json()["files"][0]["revision"],
        },
    )
    assert caption_proposal.status_code == 409
    with db_session() as session:
        assert (
            session.scalar(
                select(func.count(WriterMessageRow.id)).where(
                    WriterMessageRow.document_id
                    == select(WriterDocumentRow.id)
                    .where(WriterDocumentRow.public_id == document["public_id"])
                    .scalar_subquery()
                )
            )
            == 1
        )


def test_private_proposal_and_apply_keep_shared_writer_provenance_generic(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="private-writer@example.org", org="Private Writer")
    analysis_id, spec = _ready_analysis(owner, "private-writer-analysis")
    repository_url = "https://github.com/secret-owner/private-repository-canary"
    repository_owner = "secret-owner"
    repository_name = "private-repository-canary"
    repository_ref = "secret-ref-canary"
    repository_subpath = "secret/subpath-canary"
    commit_sha = "c" * 40
    archive_sha256 = "d" * 64
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(RepositoryAnalysisRow.public_id == analysis_id)
        )
        assert row is not None
        row.repository_access = "private"
        row.repository_url = repository_url
        row.owner = repository_owner
        row.name = repository_name
        row.ref = repository_ref
        row.subpath = repository_subpath
        row.commit_sha = commit_sha
        row.archive_sha256 = archive_sha256
        diagram = dict(row.diagram_spec or {})
        diagram["title"] = f"{repository_name}: architecture"
        row.diagram_spec = diagram
        spec = diagram
    full_path_canaries = ("client/src/index.ts", "server/src/index.ts")
    derived_hash_canaries = (
        hashlib.sha256(b"password.txt").hexdigest()[:8],
        hashlib.sha256(b"src/index.ts").hexdigest()[:10],
        hashlib.sha256(b"private-dependency").hexdigest()[:10],
    )
    neutralized_label = f"client/Component-{derived_hash_canaries[0]}/file-{derived_hash_canaries[1]}/dep-{derived_hash_canaries[2]}"
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(RepositoryAnalysisRow.public_id == analysis_id)
        )
        assert row is not None
        diagram = dict(row.diagram_spec or {})
        nodes = list(diagram.get("nodes") or [])
        evidence = list(row.evidence or [])
        evidence_by_id = {str(record["id"]): record for record in evidence}
        assert len(nodes) >= 2
        for node, path in zip(nodes[:2], full_path_canaries, strict=True):
            node["label"] = path
            record = evidence_by_id[str(node["evidence_ids"][0])]
            record["path"] = path
            record["summary"] = f"Source module; canonical display label {path};"
        neutral_node = dict(nodes[1])
        neutral_node["id"] = "node_private_hash_projection"
        neutral_node["label"] = neutralized_label
        neutral_node["evidence_ids"] = ["ev_feedfacecafebeef"]
        neutral_record = dict(evidence_by_id[str(nodes[1]["evidence_ids"][0])])
        neutral_record["id"] = "ev_feedfacecafebeef"
        neutral_record["path"] = "neutralized-label-source"
        neutral_record["source_node_id"] = neutral_node["id"]
        neutral_record["source_node_kind"] = neutral_node["kind"]
        neutral_record["source_node_group"] = neutral_node["group"]
        neutral_record["target_node_id"] = None
        neutral_record["target_node_kind"] = None
        neutral_record["target_node_group"] = None
        neutral_record["relationship"] = None
        neutral_record["summary"] = f"Source module; canonical display label {neutralized_label};"
        nodes.append(neutral_node)
        evidence.append(neutral_record)
        diagram["nodes"] = nodes
        row.diagram_spec = diagram
        row.evidence = evidence
        spec = diagram
    node_ids = [str(node["id"]) for node in spec["nodes"]]
    edge_ids = [str(edge["id"]) for edge in spec["edges"]]
    pool = OpaqueApiSelectionPool()
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: pool)
    monkeypatch.setattr(
        api_module,
        "compile_document",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, errors=[], log_tail="passed"),
    )
    preview_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/preview",
        json=_preview_body("private-writer-preview"),
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    provider_egress = "\n".join(str(call["prompt"]) for call in pool.calls)
    assert all(
        canary not in provider_egress
        for canary in (
            repository_url,
            repository_owner,
            repository_name,
            repository_ref,
            repository_subpath,
            commit_sha,
            archive_sha256,
            *full_path_canaries,
            "password.txt",
            "private-dependency",
            neutralized_label,
            *derived_hash_canaries,
            *node_ids,
            *edge_ids,
        )
    )
    document_response = owner.post(
        "/writer", json={"title": "Private repository manuscript", "template": "blank"}
    )
    assert document_response.status_code == 201, document_response.text
    document = document_response.json()
    proposal_body = {
        "request_id": "private-writer-proposal",
        "preview_request_id": preview["request_id"],
        "preview_sha256": preview["preview_sha256"],
        "writer_document_id": document["public_id"],
        "expected_writer_revision": document["revision"],
    }
    proposal_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals", json=proposal_body
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal = proposal_response.json()
    assert proposal["request_id"] == proposal_body["request_id"]
    assert proposal["analysis_id"] == analysis_id
    assert proposal["repository_access"] == "private"
    edit = proposal["edits"][0]
    assert "% sixsentences-repository-prose source=private verified=true" in edit["replace"]
    claim_ids = {
        str(identifier)
        for claim in preview["claims"]
        for key in ("node_ids", "edge_ids", "evidence_ids")
        for identifier in claim.get(key, [])
    }
    forbidden = {
        analysis_id,
        repository_url,
        repository_owner,
        repository_name,
        repository_ref,
        repository_subpath,
        commit_sha,
        archive_sha256,
        neutralized_label,
        "password.txt",
        "private-dependency",
        *derived_hash_canaries,
        *full_path_canaries,
        preview["request_id"],
        preview["preview_sha256"],
        preview["grounding_sha256"],
        *claim_ids,
    }
    with db_session() as session:
        stored_message = session.get(WriterMessageRow, proposal["message_id"])
        assert stored_message is not None
        repository_prose = dict((stored_message.payload or {}).get("repository_prose") or {})
        assert repository_prose["repository_access"] == "private"
        assert all(set(claim) <= {"text", "support"} for claim in repository_prose["claims"])
        shared_message = json.dumps(
            {"content": stored_message.content, "payload": stored_message.payload}
        )
        assert all(value not in shared_message for value in forbidden)
        receipt = session.scalar(
            select(WriterContributionRow).where(
                WriterContributionRow.kind == "repository_prose_proposal",
                WriterContributionRow.document_id == stored_message.document_id,
            )
        )
        assert receipt is not None
        serialized_receipt = json.dumps(receipt.payload)
        assert analysis_id not in serialized_receipt
        assert proposal_body["request_id"] not in serialized_receipt
    proposal_replay = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals", json=proposal_body
    )
    assert proposal_replay.status_code == 200, proposal_replay.text
    assert proposal_replay.json()["idempotent"] is True
    assert proposal_replay.json()["request_id"] == proposal_body["request_id"]
    assert proposal_replay.json()["analysis_id"] == analysis_id
    apply_body = {
        "message_id": proposal["message_id"],
        "edits": [{"path": edit["path"], "find": edit["find"], "replace": edit["replace"]}],
    }
    applied = owner.post(f"/writer/{document['public_id']}/edits/apply", json=apply_body)
    assert applied.status_code == 200, applied.text
    assert applied.json()["idempotent"] is False
    replay = owner.post(f"/writer/{document['public_id']}/edits/apply", json=apply_body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent"] is True
    contribution_log = owner.get(f"/writer/{document['public_id']}/contribution-log")
    assert contribution_log.status_code == 200, contribution_log.text
    serialized_log = contribution_log.text
    assert "edit_fingerprint" not in serialized_log
    assert all(value not in serialized_log for value in forbidden)
    assert all(
        event["payload"] == {"repository_access": "private", "verification_status": "passed"}
        for event in contribution_log.json()["events"]
        if event["kind"]
        in {"repository_prose_proposal", "writer_edit_apply_receipt", "ai_edit_applied"}
    )
    current_document = owner.get(f"/writer/{document['public_id']}")
    assert current_document.status_code == 200
    assert all(value not in current_document.json()["content"] for value in forbidden)
    with db_session() as session:
        owner_row = session.scalar(select(User).where(User.email == "private-writer@example.org"))
        assert owner_row is not None
        tables, _inventory = _privacy_export_tables(session, user=owner_row)
        shared_export = json.dumps(
            {
                "writer_documents": tables.get("writer_documents", []),
                "writer_messages": tables.get("writer_messages", []),
                "writer_contributions": tables.get("writer_contributions", []),
            }
        )
        assert "edit_fingerprint" not in shared_export
        assert all(value not in shared_export for value in forbidden)


def test_proposal_compile_does_not_lock_writer_and_discards_stale_snapshot(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="repo-proposal-race@example.org", org="Repo race")
    analysis_id, spec = _ready_analysis(owner, "proposal-race-analysis")
    node_ids = [str(node["id"]) for node in spec["nodes"]]
    edge_ids = [str(edge["id"]) for edge in spec["edges"]]
    pool = ApiSelectionPool({"selected_node_ids": node_ids, "selected_edge_ids": edge_ids[:3]})
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: pool)
    compile_started = threading.Event()
    release_compile = threading.Event()

    def blocked_compile(*_args: object, **_kwargs: object) -> object:
        compile_started.set()
        assert release_compile.wait(timeout=5)
        return SimpleNamespace(ok=True, errors=[], log_tail="passed")

    monkeypatch.setattr(api_module, "compile_document", blocked_compile)
    preview_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/preview",
        json=_preview_body("proposal-race-preview"),
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    document_response = owner.post(
        "/writer", json={"title": "Concurrent manuscript", "template": "blank"}
    )
    assert document_response.status_code == 201
    document = document_response.json()
    proposal_result: dict[str, Any] = {}

    def propose() -> None:
        proposal_result["response"] = owner.post(
            f"/repository-analyses/{analysis_id}/manuscript/proposals",
            json={
                "request_id": "proposal-race-request",
                "preview_request_id": preview["request_id"],
                "preview_sha256": preview["preview_sha256"],
                "writer_document_id": document["public_id"],
                "expected_writer_revision": document["revision"],
            },
        )

    worker = threading.Thread(target=propose, daemon=True)
    worker.start()
    assert compile_started.wait(timeout=5)
    changed_content = document["content"].replace("Start writing.", "Human autosave wins.")
    autosave = owner.patch(f"/writer/{document['public_id']}", json={"content": changed_content})
    assert autosave.status_code == 200, autosave.text
    release_compile.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    response = proposal_result["response"]
    assert response.status_code == 409, response.text
    with db_session() as session:
        doc = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == document["public_id"])
        )
        assert doc is not None and "Human autosave wins." in doc.content
        assert (
            session.scalar(
                select(func.count(WriterMessageRow.id)).where(
                    WriterMessageRow.document_id == doc.id
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(WriterContributionRow.id)).where(
                    WriterContributionRow.document_id == doc.id,
                    WriterContributionRow.kind == "repository_prose_proposal",
                )
            )
            == 0
        )


def test_preview_cancel_after_billable_response_keeps_isolated_usage(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="repo-usage-race@example.org", org="Repo usage race")
    analysis_id, spec = _ready_analysis(owner, "prose-usage-analysis")
    node_ids = [str(node["id"]) for node in spec["nodes"]]
    edge_ids = [str(edge["id"]) for edge in spec["edges"]]
    usage_recorded = threading.Event()
    release_provider = threading.Event()

    class BillablePool(ApiSelectionPool):
        def complete_json(
            self, _task: object, *, system: str, prompt: str, max_tokens: int
        ) -> object:
            self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
            assert self.on_usage is not None
            self.on_usage(
                LLMUsage(
                    task="repository_analysis",
                    provider="test",
                    model="test-model",
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=0.01,
                    cost_source="provider",
                    duration_ms=1,
                )
            )
            usage_recorded.set()
            assert release_provider.wait(timeout=5)
            return SimpleNamespace(
                text=json.dumps(self.selection), provider="test", model="test-model"
            )

    pool = BillablePool({"selected_node_ids": node_ids, "selected_edge_ids": edge_ids[:3]})
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: pool)
    outcome: dict[str, object] = {}

    def generate() -> None:
        client = TestClient(app, headers=dict(owner.headers))
        try:
            outcome["response"] = client.post(
                f"/repository-analyses/{analysis_id}/manuscript/preview",
                json=_preview_body("preview-usage-race"),
            )
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=generate, daemon=True)
    worker.start()
    assert usage_recorded.wait(timeout=5)
    stopped = owner.post("/agent/turns/preview-usage-race/stop")
    assert stopped.status_code == 200, stopped.text
    release_provider.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    with db_session() as session:
        turn = session.scalar(
            select(AgentTurnRow).where(AgentTurnRow.turn_id == "preview-usage-race")
        )
        assert turn is not None and turn.status == "cancelled"
        assert turn.result is None
        action_id = agent_turn_action_id(turn.org_id, turn.turn_id)
        reservation = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert reservation is not None and reservation.status == "cancelled"
        usage = session.scalars(select(LLMCallRow).where(LLMCallRow.action_id == action_id)).all()
        assert len(usage) == 1
        assert usage[0].cost_usd == 0.01


def test_manual_apply_rejects_changed_non_main_compile_inputs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="repo-apply-fence@example.org", org="Repo apply fence")
    analysis_id, spec = _ready_analysis(owner, "apply-fence-analysis")
    node_ids = [str(node["id"]) for node in spec["nodes"]]
    edge_ids = [str(edge["id"]) for edge in spec["edges"]]
    pool = ApiSelectionPool({"selected_node_ids": node_ids, "selected_edge_ids": edge_ids[:3]})
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: pool)
    monkeypatch.setattr(
        api_module,
        "compile_document",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, errors=[], log_tail="passed"),
    )
    preview_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/preview",
        json=_preview_body("apply-fence-preview"),
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    document_response = owner.post(
        "/writer", json={"title": "Compile input fence", "template": "blank"}
    )
    assert document_response.status_code == 201
    document = document_response.json()
    proposal_response = owner.post(
        f"/repository-analyses/{analysis_id}/manuscript/proposals",
        json={
            "request_id": "apply-fence-proposal",
            "preview_request_id": preview["request_id"],
            "preview_sha256": preview["preview_sha256"],
            "writer_document_id": document["public_id"],
            "expected_writer_revision": document["revision"],
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal = proposal_response.json()
    edit = proposal["edits"][0]
    added_file = owner.post(
        f"/writer/{document['public_id']}/files",
        json={"path": "late-change.tex", "content": "A post-proposal change."},
    )
    assert added_file.status_code == 201, added_file.text
    assert owner.get(f"/writer/{document['public_id']}").json()["revision"] == document["revision"]
    rejected = owner.post(
        f"/writer/{document['public_id']}/edits/apply",
        json={
            "message_id": proposal["message_id"],
            "edits": [{"path": edit["path"], "find": edit["find"], "replace": edit["replace"]}],
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert "compile inputs changed" in rejected.json()["detail"]
    current = owner.get(f"/writer/{document['public_id']}").json()
    assert current["content"] == document["content"]


def test_owner_privacy_export_excludes_member_repository_preview_and_event(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    _register(app, email="repo-export-owner@example.org", org="Repo export")
    _register(app, email="repo-export-member@example.org", org="Temporary export org")
    canary = "MEMBER_PRIVATE_REPOSITORY_PREVIEW"
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "repo-export-owner@example.org"))
        member = session.scalar(select(User).where(User.email == "repo-export-member@example.org"))
        assert owner is not None and member is not None
        member.org_id = owner.org_id
        member.role = "member"
        session.flush()
        turn = AgentTurnRow(
            turn_id="member-private-preview",
            org_id=owner.org_id,
            user_id=member.id,
            resource_kind="repository-prose",
            resource_id="private-analysis",
            request_hash="f" * 64,
            status="completed",
            result={"text": canary},
        )
        session.add(turn)
        session.flush()
        session.add(
            AgentEventRow(
                org_id=owner.org_id,
                turn_id=turn.id,
                sequence=1,
                event="turn.completed",
                payload={"result": {"text": canary}},
            )
        )
        session.flush()
        owner_tables, _owner_inventory = _privacy_export_tables(session, user=owner)
        member_tables, _member_inventory = _privacy_export_tables(session, user=member)
    assert canary not in json.dumps(owner_tables, sort_keys=True)
    assert canary in json.dumps(member_tables, sort_keys=True)


def test_member_erasure_fences_active_preview_without_resurrection(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    _register(app, email="repo-erasure-owner@example.org", org="Repo erasure")
    member = _register(app, email="repo-erasure-member@example.org", org="Temporary erasure org")
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "repo-erasure-owner@example.org"))
        member_user = session.scalar(
            select(User).where(User.email == "repo-erasure-member@example.org")
        )
        assert owner is not None and member_user is not None
        member_user.org_id = owner.org_id
        member_user.role = "member"
        member_user_id = member_user.id
        org_id = owner.org_id
    analysis_id, spec = _ready_analysis(member, "erasure-preview-analysis")
    node_ids = [str(node["id"]) for node in spec["nodes"]]
    edge_ids = [str(edge["id"]) for edge in spec["edges"]]
    provider_started = threading.Event()
    release_provider = threading.Event()

    class BlockingPool(ApiSelectionPool):
        def complete_json(
            self, _task: object, *, system: str, prompt: str, max_tokens: int
        ) -> object:
            self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
            provider_started.set()
            assert release_provider.wait(timeout=5)
            return SimpleNamespace(text=json.dumps(self.selection))

    pool = BlockingPool({"selected_node_ids": node_ids, "selected_edge_ids": edge_ids[:3]})
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: pool)
    outcome: dict[str, object] = {}

    def generate() -> None:
        client = TestClient(app, headers=dict(member.headers))
        try:
            outcome["response"] = client.post(
                f"/repository-analyses/{analysis_id}/manuscript/preview",
                json=_preview_body("erasure-preview-turn"),
            )
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=generate, daemon=True)
    worker.start()
    assert provider_started.wait(timeout=5)
    deleted = member.request("DELETE", "/auth/account", json={"password": "StrongPass123!"})
    assert deleted.status_code == 200, deleted.text
    release_provider.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    action_id = agent_turn_action_id(org_id, "erasure-preview-turn")
    with db_session() as session:
        assert session.get(User, member_user_id) is None
        assert (
            session.scalar(
                select(func.count(AgentTurnRow.id)).where(
                    AgentTurnRow.turn_id == "erasure-preview-turn"
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(AgentEventRow.id)).where(AgentEventRow.org_id == org_id)
            )
            == 0
        )
        reservation = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert reservation is None or reservation.status == "cancelled"
