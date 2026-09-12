"""Google Identity Services token verification and local account exchange."""

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core import google_auth
from sixsentences_server.core.db import User, db_session
from sixsentences_server.core.google_auth import (
    GoogleIdentity,
    GoogleTokenError,
    verify_google_id_token,
)

CLIENT_ID = "browser-client.apps.googleusercontent.com"
TEST_MODULUS = (
    "pmw4BtsU83ZLmIPWr_3pmsQUI9iErWUM-nFCL3vtkIxgn7qXV5R-fKBsH3snSrVze-yhdzwTU7VUx2THuU"
    "FKRTkIgi_3K_7GWHrXVYlr9gJTDWo7y4Qm4Pmj1mKQv8ZkDvtw5KUHsSfFdZU40yb2sTSdOcTNpisxo5Mc9"
    "yiST18q7NvV0br9Abw7XcBL3alGm12fwDN4SUbsMjPDk0Ef-TYZtf22RsbqK3k7vlqmzw_d7mnjudxyCxUro"
    "uWNs2P4Izba15izGMeJVC--ZfQGQXCZQFl0CKJNn2MxyqjJ3IlyGy29qmIuYCzZz78S61kEHJvRF4_pkIwmG"
    "RApssri4Q"
)
TEST_CREDENTIAL = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6InRlc3Qta2V5IiwidHlwIjoiSldUIn0."
    "eyJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLCJhdWQiOiJicm93c2VyLWNsaWVudC5hcHBz"
    "Lmdvb2dsZXVzZXJjb250ZW50LmNvbSIsInN1YiI6Imdvb2dsZS1zdWJqZWN0LTEyMyIsImVtYWlsIjoiYWRh"
    "QGdtYWlsLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlLCJnaXZlbl9uYW1lIjoiQWRhIiwibmFtZSI6IkFk"
    "YSBMb3ZlbGFjZSIsImlhdCI6MTc4NTA1MjgwMCwiZXhwIjoxNzg1MDUzNDAwfQ."
    "WuQSI2aK3Tkv2tGgocKDwr78dWhVOIHCmAriinNw2EreZtMaXDjyb-YTKryco8iuz5mKmJRpd4Mj7co0n5uX"
    "jNMFRw-tT5Z_wQo6y6B3DF6tku2nwgOLhTUIThr8PmZ1PDwn4Fx1xvufozhUUmsywQb2t9lYIWlsrZyCloo_"
    "TTuz_bnzlQ1fQl-Z1ahuNIhh6Y35VG6W9H1fYMGbLN0igrEh-qkUHVy_ONGZTCVUJCKzcuyalKtlCt25X-Lk"
    "1CrGDVcCI0AQw3FTsy5YEWzcOVll0o8yBbPLqU8N15hj2F-7Fk7dhshXUI2pmk0ILJHTGS3FjlKbX2mhPt_"
    "Epz7dNQ"
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _encoded_json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode())


def _credential(claims: dict[str, Any], *, kid: str = "test-key") -> str:
    header = _encoded_json({"alg": "RS256", "kid": kid, "typ": "JWT"})
    payload = _encoded_json(claims)
    return f"{header}.{payload}.{_b64(b'test-signature')}"


def _jwks() -> dict[str, Any]:
    return {
        "keys": [
            {
                "kid": "test-key",
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "n": TEST_MODULUS,
                "e": "AQAB",
            }
        ]
    }


def _claims(now: datetime) -> dict[str, Any]:
    return {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-subject-123",
        "email": "ada@gmail.com",
        "email_verified": True,
        "given_name": "Ada",
        "name": "Ada Lovelace",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }


def test_google_id_token_verifies_signature_and_claims() -> None:
    now = datetime(2026, 7, 26, 8, tzinfo=UTC)

    identity = verify_google_id_token(
        TEST_CREDENTIAL,
        CLIENT_ID,
        jwks=_jwks(),
        now=now,
    )

    assert identity == GoogleIdentity(
        subject="google-subject-123",
        email="ada@gmail.com",
        email_verified=True,
        name="Ada Lovelace",
        given_name="Ada",
    )
    assert identity.has_authoritative_email is True


@pytest.mark.parametrize(
    ("changed_claim", "value"),
    [
        ("aud", "another-client.apps.googleusercontent.com"),
        ("iss", "https://attacker.example"),
        ("email_verified", False),
    ],
)
def test_google_id_token_rejects_untrusted_claims(
    changed_claim: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 26, 8, tzinfo=UTC)
    claims = _claims(now)
    claims[changed_claim] = value
    monkeypatch.setattr(google_auth, "_verify_rsa_sha256_signature", lambda *args: True)

    with pytest.raises(GoogleTokenError):
        verify_google_id_token(
            _credential(claims),
            CLIENT_ID,
            jwks=_jwks(),
            now=now,
        )


def test_google_id_token_rejects_expiry_and_modified_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 26, 8, tzinfo=UTC)
    expired = _claims(now)
    expired["exp"] = int((now - timedelta(minutes=5)).timestamp())

    with monkeypatch.context() as patch:
        patch.setattr(google_auth, "_verify_rsa_sha256_signature", lambda *args: True)
        with pytest.raises(GoogleTokenError, match="expired"):
            verify_google_id_token(_credential(expired), CLIENT_ID, jwks=_jwks(), now=now)

    header, payload, signature = TEST_CREDENTIAL.split(".")
    tampered_signature = f"{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
    tampered = f"{header}.{payload}.{tampered_signature}"
    with pytest.raises(GoogleTokenError, match="signature"):
        verify_google_id_token(tampered, CLIENT_ID, jwks=_jwks(), now=now)


def test_google_signing_keys_refresh_once_after_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 26, 8, tzinfo=UTC)
    current_jwks = _jwks()
    monkeypatch.setattr(
        google_auth,
        "_jwks_cache",
        (time.monotonic() + 3600, {"keys": []}),
    )
    calls = 0

    def fetch_keys(*args: object, **kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=current_jwks,
            headers={"cache-control": "public, max-age=3600"},
            request=httpx.Request("GET", google_auth.GOOGLE_JWKS_URL),
        )

    monkeypatch.setattr(google_auth.httpx, "get", fetch_keys)

    identity = verify_google_id_token(
        TEST_CREDENTIAL,
        CLIENT_ID,
        now=now,
    )

    assert identity.subject == "google-subject-123"
    assert calls == 1


def test_google_signup_and_login_issue_local_session(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.google_oauth_client_id = CLIENT_ID
    identity = GoogleIdentity(
        subject="ada-google-sub",
        email="ada@gmail.com",
        email_verified=True,
        name="Ada Lovelace",
        given_name="Ada",
    )
    monkeypatch.setattr(
        "sixsentences_server.api.app.verify_google_id_token",
        lambda credential, client_id: identity,
    )
    client = TestClient(create_app())
    credential = "google-credential-" * 10

    signup = client.post(
        "/auth/google",
        json={"credential": credential, "intent": "signup"},
    )

    assert signup.status_code == 200
    assert signup.json()["token"].startswith("six_ss_")
    assert signup.json()["verification_required"] is False
    with db_session() as session:
        user = session.scalar(select(User).where(User.google_sub == identity.subject))
        assert user is not None
        assert user.email == identity.email
        assert user.first_name == "Ada"
        assert user.email_verified_at is not None

    login = client.post(
        "/auth/google",
        json={"credential": credential, "intent": "login"},
    )
    assert login.status_code == 200
    assert login.json()["token"].startswith("six_ss_")

    duplicate_signup = client.post(
        "/auth/google",
        json={"credential": credential, "intent": "signup"},
    )
    assert duplicate_signup.status_code == 400
    assert "already exists" in duplicate_signup.json()["detail"]


def test_google_login_links_only_authoritative_existing_email(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.google_oauth_client_id = CLIENT_ID
    client = TestClient(create_app())
    registration = client.post(
        "/auth/register",
        json={
            "email": "researcher@example.org",
            "password": "StrongPass123!",
            "org_name": "Existing Lab",
            "name": "Lin",
        },
    )
    assert registration.status_code == 201

    identity = GoogleIdentity(
        subject="third-party-google-sub",
        email="researcher@example.org",
        email_verified=True,
        given_name="Lin",
    )
    monkeypatch.setattr(
        "sixsentences_server.api.app.verify_google_id_token",
        lambda credential, client_id: identity,
    )
    blocked = client.post(
        "/auth/google",
        json={"credential": "google-credential-" * 10, "intent": "login"},
    )
    assert blocked.status_code == 409
    assert "password" in blocked.json()["detail"]

    workspace_identity = GoogleIdentity(
        subject="workspace-google-sub",
        email="researcher@example.org",
        email_verified=True,
        given_name="Lin",
        hosted_domain="example.org",
    )
    monkeypatch.setattr(
        "sixsentences_server.api.app.verify_google_id_token",
        lambda credential, client_id: workspace_identity,
    )
    linked = client.post(
        "/auth/google",
        json={"credential": "google-credential-" * 10, "intent": "login"},
    )
    assert linked.status_code == 200
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "researcher@example.org"))
        assert user is not None
        assert user.google_sub == "workspace-google-sub"


def test_third_party_google_signup_still_confirms_email(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.google_oauth_client_id = CLIENT_ID
    settings.require_email_verification = True
    sent: list[str] = []
    monkeypatch.setattr(
        "sixsentences_server.api.app.verify_google_id_token",
        lambda credential, client_id: GoogleIdentity(
            subject="third-party-new-sub",
            email="new@university.example",
            email_verified=True,
            given_name="Grace",
        ),
    )
    monkeypatch.setattr(
        "sixsentences_server.api.app.send_email",
        lambda recipient, rendered, settings: sent.append(recipient),
    )
    client = TestClient(create_app())

    response = client.post(
        "/auth/google",
        json={"credential": "google-credential-" * 10, "intent": "signup"},
    )

    assert response.status_code == 200
    assert response.json()["verification_required"] is True
    assert response.json()["email_sent"] is True
    assert sent == ["new@university.example"]
