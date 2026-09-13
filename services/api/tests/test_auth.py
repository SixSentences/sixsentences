"""Auth service: password hashing, token lifecycle, tenancy."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from sixsentences_server.config import Settings
from sixsentences_server.core.auth import (
    AuthError,
    api_key_scope_for,
    authenticate,
    create_api_key,
    create_reset_token,
    hash_password,
    normalize_api_scopes,
    register,
    reset_password,
    resolve_token,
    revoke_token,
    verify_password,
)
from sixsentences_server.core.db import AuthToken, db_session, init_db


def test_password_hash_roundtrip_and_uniqueness() -> None:
    h1 = hash_password("correct horse")
    h2 = hash_password("correct horse")
    assert h1 != h2  # random salt
    assert verify_password("correct horse", h1)
    assert not verify_password("wrong", h1)
    assert not verify_password("x", "not-a-valid-hash")


def test_register_creates_org_owner_and_working_token(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        user, token = register(session, "Owner@Example.org", "StrongPass123!", "Acme")
        assert user.email == "owner@example.org"  # normalized
        assert user.role == "owner"
        ctx = resolve_token(session, token)
        assert ctx is not None and ctx.user_id == user.id and ctx.org_id == user.org_id


def test_register_rejects_duplicate_and_short_password(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        register(session, "a@example.org", "StrongPass123!", "A")
        with pytest.raises(AuthError, match="already registered"):
            register(session, "a@example.org", "StrongPass123!", "A2")
        with pytest.raises(AuthError, match="at least"):
            register(session, "b@example.org", "short", "B")


def test_authenticate_wrong_password_raises(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        register(session, "a@example.org", "StrongPass123!", "A")
        with pytest.raises(AuthError):
            authenticate(session, "a@example.org", "nope")
        assert authenticate(session, "a@example.org", "StrongPass123!")


def test_unknown_email_still_consumes_password_hash_work(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    consumed: list[str] = []
    monkeypatch.setattr(
        "sixsentences_server.core.auth._consume_password_work",
        lambda password: consumed.append(password),
    )

    with db_session() as session, pytest.raises(AuthError, match="invalid credentials"):
        authenticate(session, "unknown@example.org", "candidate-password")

    assert consumed == ["candidate-password"]


def test_revoked_and_expired_tokens_do_not_resolve(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        user, _ = register(session, "a@example.org", "StrongPass123!", "A")
        raw = create_api_key(session, user, "ci")
        ctx = resolve_token(session, raw)
        assert ctx is not None

        # revoke by id
        assert revoke_token(session, user.org_id, ctx.token_id)
        assert resolve_token(session, raw) is None

        # expiry
        expired = create_api_key(session, user, "old")
        expired_ctx = resolve_token(session, expired)
        assert expired_ctx is not None
        row = session.get(AuthToken, expired_ctx.token_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.flush()
        assert resolve_token(session, expired) is None


def test_api_key_scopes_are_validated_and_write_implies_read(settings: Settings) -> None:
    assert normalize_api_scopes(["research:write"]) == (
        "research:read",
        "research:write",
    )
    assert normalize_api_scopes(["library:write", "library:write"]) == (
        "library:read",
        "library:write",
    )
    assert normalize_api_scopes(["companion:write"]) == (
        "companion:read",
        "companion:write",
    )
    with pytest.raises(AuthError, match="unknown API key scope"):
        normalize_api_scopes(["admin:write"])

    assert api_key_scope_for("GET", "/runs/{run_id}") == "research:read"
    assert (
        api_key_scope_for("POST", "/interviews/live/sessions/{session_id}/segments")
        == "companion:write"
    )
    assert api_key_scope_for("PATCH", "/documents/{document_id}/metadata") == "library:write"
    assert api_key_scope_for("PATCH", "/library/web-sources/{source_id}") == "library:write"
    assert api_key_scope_for("POST", "/browser-capture/web") == "capture:write"
    assert api_key_scope_for("DELETE", "/runs/{run_id}") is None
    assert api_key_scope_for("GET", "/writer") is None


def test_only_hash_is_stored_never_the_raw_token(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        _, token = register(session, "a@example.org", "StrongPass123!", "A")
        rows = session.scalars(select(AuthToken)).all()
        assert rows
        for row in rows:
            assert row.token_hash != token  # only the hash is stored
            assert len(row.token_hash) == 64  # sha256 hex


def test_register_rejects_duplicate_org_name(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        register(session, "first@lab.org", "StrongPass123!", "Shared Lab")
        with pytest.raises(AuthError, match="organization name already taken"):
            register(session, "second@lab.org", "StrongPass123!", "Shared Lab")


def test_password_reset_flow(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        _, session_token = register(session, "reset@lab.org", "OldPassword123!", "ResetLab")
        raw = create_reset_token(session, "reset@lab.org")
        assert raw is not None and raw.startswith("six_ss_") is False or raw  # opaque token
        # a reset token must never authenticate API calls
        assert resolve_token(session, raw) is None
        assert reset_password(session, raw, "newpassword1") is True
        # old password dead, new one works, old sessions revoked, token spent
        with pytest.raises(AuthError):
            authenticate(session, "reset@lab.org", "OldPassword123!")
        assert authenticate(session, "reset@lab.org", "newpassword1")
        assert resolve_token(session, session_token) is None
        assert reset_password(session, raw, "anotherpass1") is False


def test_reset_token_for_unknown_email_is_none(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        assert create_reset_token(session, "ghost@nowhere.org") is None
