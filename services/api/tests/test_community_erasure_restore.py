"""Community account erasure and post-restore replay invariants."""

import stat

from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.auth import provision_owner
from sixsentences_server.core.db import Org, Project, User, db_session, init_db
from sixsentences_server.ops.erasure import (
    cleanup_replayed_erasure_artifacts,
    replay_erasure_events,
)
from sixsentences_server.ops.erasure_ledger import append_event, read_events


def test_account_endpoint_journals_before_removing_the_workspace(
    settings: Settings, corpus: object
) -> None:
    del corpus
    client = TestClient(create_app())
    password = "CommunityErasurePassword-42!"
    registered = client.post(
        "/auth/register",
        json={
            "email": "erase-owner@example.org",
            "password": password,
            "name": "Ada",
            "org_name": "Erasure Lab",
        },
    )
    assert registered.status_code == 201, registered.text
    client.headers["Authorization"] = f"Bearer {registered.json()['token']}"
    created = client.post("/projects", json={"name": "Private study"})
    assert created.status_code == 200

    refused = client.request("DELETE", "/auth/account", json={"password": "wrong-password"})
    assert refused.status_code == 403
    deleted = client.request("DELETE", "/auth/account", json={"password": password})
    assert deleted.json() == {"deleted": "workspace", "storage_cleanup": "complete"}

    with db_session() as session:
        assert session.scalar(select(Org).where(Org.name == "Erasure Lab")) is None
        assert session.scalar(select(User).where(User.email == "erase-owner@example.org")) is None
    events = read_events(settings)
    assert len(events) == 1 and events[0].subject == "workspace"
    raw = settings.resolved_erasure_ledger_path.read_text(encoding="utf-8")
    assert "erase-owner@example.org" not in raw
    assert password not in raw
    assert stat.S_IMODE(settings.resolved_erasure_ledger_path.stat().st_mode) == 0o600


def test_authenticated_erasure_ledger_reapplies_after_database_restore(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        owner = provision_owner(
            session,
            "restored-owner@example.org",
            "CommunityRestorePassword-42!",
            "Restored Lab",
        )
        session.add(Project(org_id=owner.org_id, name="Restored private study"))
        org_id = owner.org_id
        user_id = owner.id

    append_event(
        settings,
        subject="workspace",
        reason="account_request",
        email="restored-owner@example.org",
        org_id=org_id,
        user_id=user_id,
    )
    with db_session() as session:
        report = replay_erasure_events(session, settings=settings)
    cleanup_replayed_erasure_artifacts(report, settings)

    assert report["passed"] is True
    assert report["applied"] == {"workspace": 1, "orphan_workspace": 0, "member": 0}
    with db_session() as session:
        assert session.get(Org, org_id) is None
        assert session.get(User, user_id) is None

    with db_session() as session:
        replayed = replay_erasure_events(session, settings=settings)
    cleanup_replayed_erasure_artifacts(replayed, settings)
    assert replayed["already_absent"]["workspace"] == 1
