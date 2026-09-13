"""Consent, tenancy, and retention behavior for local conversation reviews."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    ChatMessageRow,
    ConversationReviewRow,
    Project,
    Run,
    User,
    db_session,
)


def _authed(app: FastAPI, *, email: str, org_name: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "StrongPass123!",
            "org_name": org_name,
            "name": "Reviewer",
        },
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _conversation(email: str) -> tuple[str, int, int]:
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        project = Project(org_id=user.org_id, name="Review fixture")
        session.add(project)
        session.flush()
        run = Run(
            org_id=user.org_id,
            project_id=project.id,
            question="How reliable is this result?",
            status="completed",
        )
        session.add(run)
        session.flush()
        first = ChatMessageRow(
            org_id=user.org_id,
            run_id=run.id,
            role="user",
            content="Explain the result.",
        )
        second = ChatMessageRow(
            org_id=user.org_id,
            run_id=run.id,
            role="assistant",
            content="A grounded answer.",
        )
        session.add_all((first, second))
        session.flush()
        return run.public_id, run.id, second.id


def test_conversation_review_requires_explicit_consent_and_valid_category(
    settings: Settings,
) -> None:
    client = _authed(create_app(), email="reviewer@example.org", org_name="Review Org")
    public_id, run_id, _message_id = _conversation("reviewer@example.org")

    refused = client.post(
        f"/runs/{public_id}/chat-report",
        json={"category": "unsafe", "note": "Please inspect this", "consent": False},
    )
    assert refused.status_code == 422
    invalid = client.post(
        f"/runs/{public_id}/chat-report",
        json={"category": "unbounded", "consent": True},
    )
    assert invalid.status_code == 422
    with db_session() as session:
        assert (
            session.scalar(
                select(func.count(ConversationReviewRow.id)).where(
                    ConversationReviewRow.run_id == run_id
                )
            )
            == 0
        )


def test_conversation_review_is_tenant_scoped_and_freezes_then_refreshes_boundary(
    settings: Settings,
) -> None:
    app = create_app()
    owner = _authed(app, email="owner@example.org", org_name="Owner Org")
    outsider = _authed(app, email="outsider@example.org", org_name="Outsider Org")
    public_id, run_id, original_boundary = _conversation("owner@example.org")

    forbidden = outsider.post(
        f"/runs/{public_id}/chat-report",
        json={"category": "other", "note": "Cross-tenant", "consent": True},
    )
    assert forbidden.status_code == 404

    created = owner.post(
        f"/runs/{public_id}/chat-report",
        json={
            "category": "missing_source",
            "note": "  The supporting source is missing.  ",
            "consent": True,
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "open"
    review_id = created.json()["id"]

    with db_session() as session:
        row = session.scalar(
            select(ConversationReviewRow).where(ConversationReviewRow.public_id == review_id)
        )
        assert row is not None
        assert row.run_id == run_id
        assert row.message_through_id == original_boundary
        assert row.category == "missing_source"
        assert row.note == "The supporting source is missing."
        assert row.consented is True
        user = session.scalar(select(User).where(User.email == "owner@example.org"))
        assert user is not None
        later = ChatMessageRow(
            org_id=user.org_id,
            run_id=run_id,
            role="user",
            content="A later turn.",
        )
        session.add(later)
        session.flush()
        later_id = later.id

    updated = owner.post(
        f"/runs/{public_id}/chat-report",
        json={"category": "tool_failure", "note": "Retry failed.", "consent": True},
    )
    assert updated.status_code == 201
    assert updated.json() == {"id": review_id, "status": "open"}
    with db_session() as session:
        rows = session.scalars(
            select(ConversationReviewRow).where(ConversationReviewRow.run_id == run_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].message_through_id == later_id
        assert rows[0].category == "tool_failure"
        assert rows[0].note == "Retry failed."


def test_conversation_review_has_no_platform_management_route(settings: Settings) -> None:
    paths = set(create_app().openapi()["paths"])

    assert "/runs/{run_id}/chat-report" in paths
    assert not [
        path for path in paths if "conversation-review" in path and path.startswith("/admin")
    ]
