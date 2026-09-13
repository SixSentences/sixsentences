"""Public signup requires one expiring, single-use email confirmation."""

import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import _cleanup_expired_unverified_accounts, create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.auth import provision_owner
from sixsentences_server.core.db import User, db_session
from sixsentences_server.mail.templates import RenderedEmail


def _token(message: RenderedEmail) -> str:
    match = re.search(r"six_ev_[A-Za-z0-9_-]+", message.text)
    assert match is not None
    return match.group(0)


def test_signup_email_resend_and_confirmation(
    corpus: object,
    settings: Settings,
    monkeypatch,
) -> None:
    del corpus
    settings.require_email_verification = True
    settings.smtp_host = "mail"
    sent: list[tuple[str, RenderedEmail]] = []
    monkeypatch.setattr(
        "sixsentences_server.api.app.send_email",
        lambda recipient, rendered, settings: sent.append((recipient, rendered)),
    )
    client = TestClient(create_app())

    registration = client.post(
        "/auth/register",
        json={
            "email": "Ada@University.EDU",
            "password": "StrongPass123!",
            "org_name": "Verification Lab",
            "name": "Ada",
        },
    )

    assert registration.status_code == 201
    assert registration.json() == {
        "verification_required": True,
        "email_sent": True,
        "email": "ada@university.edu",
    }
    assert sent[0][0] == "ada@university.edu"
    first_token = _token(sent[0][1])
    assert (
        client.post(
            "/auth/login",
            json={"email": "ada@university.edu", "password": "StrongPass123!"},
        ).status_code
        == 401
    )
    assert client.post(
        "/auth/login",
        json={"email": "ada@university.edu", "password": "StrongPass123!"},
    ).json() == {"detail": "invalid credentials"}

    resend = client.post(
        "/auth/verification/resend",
        json={"email": "ada@university.edu"},
    )
    assert resend.status_code == 200
    second_token = _token(sent[1][1])
    assert second_token != first_token
    assert client.post("/auth/verify", json={"token": first_token}).status_code == 400

    confirmation = client.post("/auth/verify", json={"token": second_token})

    assert confirmation.status_code == 200
    client.headers["Authorization"] = f"Bearer {confirmation.json()['token']}"
    assert client.get("/auth/me").json()["email"] == "ada@university.edu"
    assert client.post("/auth/verify", json={"token": second_token}).status_code == 400
    assert (
        client.post(
            "/auth/verification/resend",
            json={"email": "ada@university.edu"},
        ).status_code
        == 200
    )
    assert len(sent) == 2


def test_signup_does_not_disclose_an_existing_email(
    corpus: object,
    settings: Settings,
    monkeypatch,
) -> None:
    del corpus
    settings.require_email_verification = True
    sent: list[str] = []
    monkeypatch.setattr(
        "sixsentences_server.api.app.send_email",
        lambda recipient, rendered, settings: sent.append(recipient),
    )
    client = TestClient(create_app())
    payload = {
        "email": "same@example.org",
        "password": "StrongPass123!",
        "org_name": "First workspace",
        "name": "Ada",
    }

    first = client.post("/auth/register", json=payload)
    second = client.post("/auth/register", json={**payload, "org_name": "Another workspace"})

    assert first.status_code == second.status_code == 201
    assert (
        first.json()
        == second.json()
        == {
            "verification_required": True,
            "email_sent": True,
            "email": "same@example.org",
        }
    )
    with db_session() as session:
        assert len(session.scalars(select(User).where(User.email == "same@example.org")).all()) == 1
    assert sent == ["same@example.org"]


def test_abandoned_unverified_signup_is_removed_after_seven_days(
    corpus: object,
) -> None:
    del corpus
    client = TestClient(create_app())
    with db_session() as session:
        user = provision_owner(
            session,
            "abandoned@example.org",
            "StrongPass123!",
            "Abandoned",
            email_verified=False,
        )
        user.created_at = datetime.now(UTC) - timedelta(days=8)

    assert client.get("/health").json() == {"status": "ok"}
    with db_session() as session:
        assert _cleanup_expired_unverified_accounts(session) == 1
    with db_session() as session:
        assert session.scalar(select(User).where(User.email == "abandoned@example.org")) is None
