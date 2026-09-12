"""Password hashing and opaque bearer-token helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.models import AuthToken, User

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def normalize_email(value: str) -> str:
    """Normalize an email address and reject malformed values."""

    email = value.strip().casefold()
    if len(email) > 320 or _EMAIL.fullmatch(email) is None:
        raise ValueError("invalid email address")
    return email


def hash_password(password: str) -> str:
    """Hash a password with a per-user salt and memory-hard scrypt."""

    if not 12 <= len(password) <= 1024:
        raise ValueError("password must contain between 12 and 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password without exposing parse failures or timing shortcuts."""

    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        if (n, r, p) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        salt = base64.urlsafe_b64decode(raw_salt)
        expected = base64.urlsafe_b64decode(raw_digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def token_hash(raw_token: str) -> str:
    """Hash a bearer or public capability before persistence."""

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def random_token(prefix: str = "sixc") -> str:
    """Generate a scoped opaque capability value."""

    return f"{prefix}_{secrets.token_urlsafe(32)}"


def issue_auth_token(
    session: Session,
    user: User,
    *,
    hours: int,
    kind: str = "session",
    name: str = "",
) -> tuple[AuthToken, str]:
    """Persist only the digest and return the raw token exactly once."""

    raw = random_token("sixc_api" if kind == "api" else "sixc_session")
    record = AuthToken(
        user_id=user.id,
        token_hash=token_hash(raw),
        kind=kind,
        name=name.strip()[:120],
        expires_at=None if kind == "api" else datetime.now(UTC) + timedelta(hours=hours),
    )
    session.add(record)
    session.flush()
    return record, raw


def authenticate_token(session: Session, raw_token: str) -> tuple[User, AuthToken] | None:
    """Resolve one active token and its enabled user."""

    record = session.scalar(select(AuthToken).where(AuthToken.token_hash == token_hash(raw_token)))
    if record is None:
        return None
    now = datetime.now(UTC)
    expires_at = record.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            session.delete(record)
            session.flush()
            return None
    user = session.get(User, record.user_id)
    if user is None or user.disabled:
        return None
    record.last_used_at = now
    return user, record
