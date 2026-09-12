"""Creator-private Knowledge pages: contract, isolation and durability."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import sixsentences_server.knowledge.workspace as knowledge_workspace
from sixsentences_server.api.app import create_app
from sixsentences_server.core.db import KnowledgePageRow, Org, Project, User, db_session

PASSWORD = "StrongPass123!"
PAGE_KEYS = {
    "public_id",
    "title",
    "body_markdown",
    "state",
    "pinned",
    "tags",
    "parent_id",
    "parent_title",
    "project_id",
    "project_name",
    "position",
    "revision",
    "created_at",
    "updated_at",
}


def _register(app: FastAPI, *, email: str, org: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "org_name": org,
            "name": "Knowledge researcher",
        },
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _member(app: FastAPI, owner: TestClient, *, email: str) -> TestClient:
    org_id = int(owner.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"
    response = owner.post(
        "/orgs/current/users", json={"email": email, "password": PASSWORD, "role": "member"}
    )
    assert response.status_code == 201, response.text
    client = TestClient(app)
    login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    return client


def _create(
    client: TestClient,
    request_id: str,
    *,
    title: str = "Untitled thought",
    body_markdown: str = "A private research thought.",
    **values: Any,
) -> dict[str, Any]:
    response = client.post(
        "/knowledge/pages",
        json={
            "client_request_id": request_id,
            "title": title,
            "body_markdown": body_markdown,
            **values,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_create_replay_lifecycle_bounds_and_required_delete_revision(settings: object) -> None:
    del settings
    app = create_app()
    client = _register(app, email="knowledge-owner@example.org", org="Knowledge contract")
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "knowledge-owner@example.org"))
        assert user is not None
        project = Project(org_id=user.org_id, name="Transformer thesis")
        session.add(project)
        session.flush()
        project_id = project.id
    created = _create(
        client,
        "capture-contract-0001",
        title="  Attention notes  ",
        body_markdown="Line one\r\nLine two",
        tags=["Transformers", " transformers ", "Thesis"],
        project_id=project_id,
        pinned=True,
        position=12,
    )
    assert set(created) == PAGE_KEYS
    assert created == {
        **created,
        "title": "Attention notes",
        "body_markdown": "Line one\nLine two",
        "tags": ["Transformers", "Thesis"],
        "project_id": project_id,
        "project_name": "Transformer thesis",
        "parent_id": None,
        "parent_title": None,
        "revision": 1,
    }
    replay = client.post(
        "/knowledge/pages",
        json={
            "client_request_id": "capture-contract-0001",
            "title": "Attention notes",
            "body_markdown": "Line one\nLine two",
            "tags": ["Transformers", "Thesis"],
            "project_id": project_id,
            "pinned": True,
            "position": 12,
        },
    )
    assert replay.status_code == 201
    assert replay.json()["public_id"] == created["public_id"]
    with db_session() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgePageRow)) == 1
    collision = client.post(
        "/knowledge/pages",
        json={"client_request_id": "capture-contract-0001", "title": "A different thought"},
    )
    assert collision.status_code == 409
    assert collision.json()["detail"]["code"] == "knowledge_idempotency_conflict"
    updated = client.patch(
        f"/knowledge/pages/{created['public_id']}",
        json={
            "expected_revision": 1,
            "state": "evergreen",
            "body_markdown": "Refined evergreen note.",
            "project_id": None,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert updated.json()["state"] == "evergreen"
    assert updated.json()["project_id"] is None
    stale = client.patch(
        f"/knowledge/pages/{created['public_id']}",
        json={"expected_revision": 1, "title": "Lost update"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "knowledge_revision_conflict"
    assert stale.json()["detail"]["current"]["revision"] == 2
    for field in ("title", "body_markdown", "state", "pinned", "tags", "position"):
        invalid = client.patch(
            f"/knowledge/pages/{created['public_id']}", json={"expected_revision": 2, field: None}
        )
        assert invalid.status_code == 422, (field, invalid.text)
    missing_revision = client.delete(f"/knowledge/pages/{created['public_id']}")
    assert missing_revision.status_code == 422
    stale_delete = client.delete(f"/knowledge/pages/{created['public_id']}?expected_revision=1")
    assert stale_delete.status_code == 409
    deleted = client.delete(f"/knowledge/pages/{created['public_id']}?expected_revision=2")
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/knowledge/pages/{created['public_id']}").status_code == 404
    invalid_create_payloads = (
        {"client_request_id": "invalid-blank-01", "title": "", "body_markdown": "  "},
        {"client_request_id": "invalid-body-001", "body_markdown": "x" * 50001},
        {"client_request_id": "invalid-tags-001", "title": "x", "tags": ["t"] * 13},
        {"client_request_id": "invalid-tag-length", "title": "x", "tags": ["t" * 33]},
        {"client_request_id": "invalid-position", "title": "x", "position": 1000000001},
        {"client_request_id": "invalid-body-nul", "title": "x", "body_markdown": "before\x00after"},
        {"client_request_id": "invalid-request\x00id", "title": "x"},
    )
    for payload in invalid_create_payloads:
        response = client.post("/knowledge/pages", json=payload)
        assert response.status_code == 422, response.text
    assert client.get("/knowledge/pages?q=%00").status_code == 422


def test_active_filter_search_pagination_and_root_contract(settings: object) -> None:
    del settings
    app = create_app()
    client = _register(app, email="knowledge-list@example.org", org="Knowledge list")
    root = _create(
        client,
        "list-root-page-01",
        title="Root transformer map",
        state="developing",
        tags=["Architecture"],
        position=10,
    )
    inbox = _create(
        client,
        "list-inbox-page-1",
        title="Inbox capture",
        state="inbox",
        tags=["Inbox"],
        position=30,
    )
    evergreen = _create(
        client,
        "list-evergreen-01",
        title="Durable transformer insight",
        state="evergreen",
        tags=["Architecture"],
        parent_id=root["public_id"],
        pinned=True,
        position=20,
    )
    archived = _create(
        client,
        "list-archived-01",
        title="Old transformer idea",
        state="archived",
        tags=["Architecture"],
        position=0,
    )
    first = client.get("/knowledge/pages?state=active&limit=2")
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 3
    assert first.json()["has_more"] is True
    assert [item["public_id"] for item in first.json()["items"]] == [
        evergreen["public_id"],
        root["public_id"],
    ]
    second = client.get("/knowledge/pages?state=active&limit=2&offset=2")
    assert second.json()["total"] == 3
    assert second.json()["has_more"] is False
    assert [item["public_id"] for item in second.json()["items"]] == [inbox["public_id"]]
    archived_result = client.get("/knowledge/pages?state=archived").json()
    assert archived_result["total"] == 1
    assert archived_result["items"][0]["public_id"] == archived["public_id"]
    tag_result = client.get("/knowledge/pages?tag=architecture").json()
    assert tag_result["total"] == 3
    search_result = client.get("/knowledge/pages?q=durable%20transformer").json()
    assert [item["public_id"] for item in search_result["items"]] == [evergreen["public_id"]]
    body_search = client.get("/knowledge/pages?q=private%20research").json()
    assert body_search["total"] == 4
    title_only = client.get("/knowledge/pages?q=private%20research&search_scope=title").json()
    assert title_only["total"] == 0
    roots = client.get("/knowledge/pages?parent_id=root").json()
    assert roots["total"] == 3
    assert evergreen["public_id"] not in {item["public_id"] for item in roots["items"]}
    children = client.get(f"/knowledge/pages?parent_id={root['public_id']}").json()
    assert children["total"] == 1
    assert children["items"][0]["parent_title"] == "Root transformer map"
    assert client.get("/knowledge/pages?limit=101").status_code == 422
    assert client.get("/knowledge/pages?offset=10001").status_code == 422


def test_hierarchy_depth_cycle_delete_policy_and_page_cap(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    del settings
    app = create_app()
    client = _register(app, email="knowledge-tree@example.org", org="Knowledge tree")
    pages: list[dict[str, Any]] = []
    parent_id: str | None = None
    for index in range(8):
        values: dict[str, Any] = {}
        if parent_id is not None:
            values["parent_id"] = parent_id
        page = _create(client, f"tree-depth-{index:04d}", title=f"Level {index + 1}", **values)
        pages.append(page)
        parent_id = str(page["public_id"])
    too_deep = client.post(
        "/knowledge/pages",
        json={
            "client_request_id": "tree-depth-overflow",
            "title": "Level 9",
            "parent_id": pages[-1]["public_id"],
        },
    )
    assert too_deep.status_code == 422
    assert too_deep.json()["detail"]["code"] == "knowledge_hierarchy_too_deep"
    second_chain: list[dict[str, Any]] = []
    second_parent_id: str | None = None
    for index in range(4):
        values = {"parent_id": second_parent_id} if second_parent_id is not None else {}
        page = _create(
            client, f"tree-second-{index:03d}", title=f"Second branch {index + 1}", **values
        )
        second_chain.append(page)
        second_parent_id = str(page["public_id"])
    subtree_too_deep = client.patch(
        f"/knowledge/pages/{pages[3]['public_id']}",
        json={"expected_revision": 1, "parent_id": second_chain[-1]["public_id"]},
    )
    assert subtree_too_deep.status_code == 422
    assert subtree_too_deep.json()["detail"]["code"] == "knowledge_hierarchy_too_deep"
    cycle = client.patch(
        f"/knowledge/pages/{pages[0]['public_id']}",
        json={"expected_revision": 1, "parent_id": pages[-1]["public_id"]},
    )
    assert cycle.status_code == 409
    assert cycle.json()["detail"]["code"] == "knowledge_hierarchy_cycle"
    self_parent = client.patch(
        f"/knowledge/pages/{pages[3]['public_id']}",
        json={"expected_revision": 1, "parent_id": pages[3]["public_id"]},
    )
    assert self_parent.status_code == 409
    deleted = client.delete(f"/knowledge/pages/{pages[3]['public_id']}?expected_revision=1")
    assert deleted.status_code == 204
    detached_child = client.get(f"/knowledge/pages/{pages[4]['public_id']}").json()
    assert detached_child["parent_id"] is None
    assert detached_child["revision"] == 2
    monkeypatch.setattr(knowledge_workspace, "MAX_PAGES_PER_CREATOR", 11)
    capped = client.post(
        "/knowledge/pages", json={"client_request_id": "tree-page-cap-01", "title": "At cap"}
    )
    assert capped.status_code == 409
    assert capped.json()["detail"] == {
        "code": "knowledge_page_limit_reached",
        "message": "Delete an existing Knowledge page before creating another one.",
        "limit": 11,
    }


def test_concurrent_create_replay_and_stale_save_are_serialized(settings: object) -> None:
    del settings
    app = create_app()
    owner = _register(app, email="knowledge-race@example.org", org="Knowledge race")
    token = owner.headers["Authorization"]
    create_barrier = threading.Barrier(2)
    create_results: list[tuple[int, dict[str, Any]]] = []

    def capture() -> None:
        client = TestClient(app, headers={"Authorization": token})
        create_barrier.wait()
        response = client.post(
            "/knowledge/pages",
            json={"client_request_id": "concurrent-create-01", "title": "One captured page"},
        )
        create_results.append((response.status_code, dict(response.json())))

    threads = [threading.Thread(target=capture) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted((status for status, _ in create_results)) == [201, 201]
    assert len({payload["public_id"] for _, payload in create_results}) == 1
    page_id = str(create_results[0][1]["public_id"])
    save_barrier = threading.Barrier(2)
    save_results: list[int] = []

    def save(title: str) -> None:
        client = TestClient(app, headers={"Authorization": token})
        save_barrier.wait()
        response = client.patch(
            f"/knowledge/pages/{page_id}", json={"expected_revision": 1, "title": title}
        )
        save_results.append(response.status_code)

    threads = [
        threading.Thread(target=save, args=("First edit",)),
        threading.Thread(target=save, args=("Second edit",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(save_results) == [200, 409]
    assert owner.get(f"/knowledge/pages/{page_id}").json()["revision"] == 2
    first_page = _create(owner, "concurrent-tree-a", title="Tree A")
    second_page = _create(owner, "concurrent-tree-b", title="Tree B")
    hierarchy_barrier = threading.Barrier(2)
    hierarchy_results: list[int] = []

    def move(page: dict[str, Any], parent: dict[str, Any]) -> None:
        client = TestClient(app, headers={"Authorization": token})
        hierarchy_barrier.wait()
        response = client.patch(
            f"/knowledge/pages/{page['public_id']}",
            json={"expected_revision": 1, "parent_id": parent["public_id"]},
        )
        hierarchy_results.append(response.status_code)

    threads = [
        threading.Thread(target=move, args=(first_page, second_page)),
        threading.Thread(target=move, args=(second_page, first_page)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(hierarchy_results) == [200, 409]
    first_current = owner.get(f"/knowledge/pages/{first_page['public_id']}").json()
    second_current = owner.get(f"/knowledge/pages/{second_page['public_id']}").json()
    assert not (
        first_current["parent_id"] == second_page["public_id"]
        and second_current["parent_id"] == first_page["public_id"]
    )


def test_project_deletion_clears_filing_reference_and_bumps_revision(settings: object) -> None:
    del settings
    app = create_app()
    client = _register(app, email="knowledge-project@example.org", org="Knowledge project")
    created_project = client.post("/projects", json={"name": "Temporary filing project"})
    assert created_project.status_code == 200, created_project.text
    project_id = int(created_project.json()["id"])
    page = _create(
        client, "project-delete-page", title="Thought survives project", project_id=project_id
    )
    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 200, deleted.text
    current = client.get(f"/knowledge/pages/{page['public_id']}").json()
    assert current["project_id"] is None
    assert current["project_name"] is None
    assert current["revision"] == 2


def test_project_delete_and_page_autosave_finish_without_lock_failure(settings: object) -> None:
    del settings
    app = create_app()
    owner = _register(app, email="knowledge-project-race@example.org", org="Project race")
    token = owner.headers["Authorization"]
    created_project = owner.post("/projects", json={"name": "Concurrent project"})
    assert created_project.status_code == 200
    project_id = int(created_project.json()["id"])
    page = _create(owner, "project-race-page", title="Before autosave", project_id=project_id)
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def autosave() -> None:
        client = TestClient(app, headers={"Authorization": token})
        barrier.wait()
        response = client.patch(
            f"/knowledge/pages/{page['public_id']}",
            json={"expected_revision": 1, "title": "After autosave", "project_id": project_id},
        )
        results["autosave"] = response.status_code

    def delete_project() -> None:
        client = TestClient(app, headers={"Authorization": token})
        barrier.wait()
        results["delete"] = client.delete(f"/projects/{project_id}").status_code

    threads = [threading.Thread(target=autosave), threading.Thread(target=delete_project)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert results["delete"] == 200
    assert results["autosave"] in {200, 404, 409}
    current = owner.get(f"/knowledge/pages/{page['public_id']}")
    assert current.status_code == 200
    assert current.json()["project_id"] is None
    assert current.json()["revision"] in {2, 3}


def test_creator_body_corpus_cap_guards_create_and_patch(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    del settings
    monkeypatch.setattr(knowledge_workspace, "MAX_TOTAL_BODY_CHARS", 10)
    app = create_app()
    client = _register(app, email="knowledge-corpus@example.org", org="Knowledge corpus")
    first = _create(client, "corpus-first-page", title="First", body_markdown="12345")
    _create(client, "corpus-second-pg", title="Second", body_markdown="67890")
    create_overflow = client.post(
        "/knowledge/pages",
        json={"client_request_id": "corpus-overflow-1", "title": "Third", "body_markdown": "x"},
    )
    assert create_overflow.status_code == 409
    assert create_overflow.json()["detail"] == {
        "code": "knowledge_content_limit_reached",
        "message": "Shorten or delete an existing Knowledge page before saving more text.",
        "limit_chars": 10,
    }
    patch_overflow = client.patch(
        f"/knowledge/pages/{first['public_id']}",
        json={"expected_revision": 1, "body_markdown": "123456"},
    )
    assert patch_overflow.status_code == 409
    assert patch_overflow.json()["detail"]["code"] == "knowledge_content_limit_reached"
    current = client.get(f"/knowledge/pages/{first['public_id']}").json()
    assert current["body_markdown"] == "12345"
    assert current["revision"] == 1


def test_private_export_member_erasure_and_workspace_purge(settings: object) -> None:
    del settings
    app = create_app()
    owner = _register(app, email="knowledge-export-owner@example.org", org="Knowledge privacy")
    member = _member(app, owner, email="knowledge-export-member@example.org")
    owner_page = _create(owner, "privacy-owner-page", title="Owner private thought")
    member_page = _create(member, "privacy-member-page", title="Member private thought")
    for client, expected_id, excluded_title in (
        (owner, owner_page["public_id"], "Member private thought"),
        (member, member_page["public_id"], "Owner private thought"),
    ):
        response = client.post("/auth/account/export", json={"password": PASSWORD})
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            pages = json.loads(archive.read("data/knowledge_pages.json"))
            assert [page["public_id"] for page in pages] == [expected_id]
            serialized = json.dumps(pages)
            assert excluded_title not in serialized
    member_deleted = member.request("DELETE", "/auth/account", json={"password": PASSWORD})
    assert member_deleted.status_code == 200, member_deleted.text
    with db_session() as session:
        remaining = list(session.scalars(select(KnowledgePageRow)).all())
        assert [row.public_id for row in remaining] == [owner_page["public_id"]]
        assert (
            session.scalar(select(User).where(User.email == "knowledge-export-member@example.org"))
            is None
        )
    org_id = int(owner.get("/auth/me").json()["org_id"])
    workspace_deleted = owner.request("DELETE", "/auth/account", json={"password": PASSWORD})
    assert workspace_deleted.status_code == 200, workspace_deleted.text
    with db_session() as session:
        assert session.get(Org, org_id) is None
        assert session.scalar(select(func.count()).select_from(KnowledgePageRow)) == 0
