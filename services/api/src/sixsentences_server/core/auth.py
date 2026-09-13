"""Authentication & account management (stdlib-only, no new dependencies).

Design choices, deliberately conservative:
- Passwords: PBKDF2-HMAC-SHA256 with a per-user random salt and a high work
  factor (OWASP-tier). Stored as `pbkdf2_sha256$iterations$salt$hash`.
- Tokens: opaque, high-entropy bearer tokens (`six_sk_...` API keys,
  `six_ss_...` sessions). Only the SHA-256 hash is persisted — a database leak
  never exposes a usable token. Lookups are by hash (constant work), password
  checks use `hmac.compare_digest`.
- Tenancy: a token resolves to an AuthContext carrying org_id; the API scopes
  every tenant query by it. No credential for a commercial database is ever
  stored (see DATA_BACKBONE.md).
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sixsentences_server.core.assistant_preferences import (
    normalize_assistant_preferences,
)
from sixsentences_server.core.credentials import (
    CredentialError,
    decrypt_credential,
    encrypt_credential,
)
from sixsentences_server.core.db import AuthToken, Org, User
from sixsentences_server.core.google_auth import GoogleIdentity
from sixsentences_server.core.mfa import (
    consume_recovery_code,
    generate_recovery_codes,
    generate_totp_secret,
    totp_uri,
    verify_totp,
)

PBKDF2_ITERATIONS = 600000
SESSION_TTL = timedelta(days=30)
RESET_TTL = timedelta(hours=1)
EMAIL_VERIFICATION_TTL = timedelta(hours=24)
MFA_CHALLENGE_TTL = timedelta(minutes=5)
STREAM_TICKET_TTL = timedelta(minutes=2)
COMPANION_CODE_TTL = timedelta(minutes=5)
CAPTURE_CODE_TTL = timedelta(minutes=5)
MAX_BROWSER_CAPTURE_DEVICES = 5
TOKEN_TOUCH_INTERVAL = timedelta(minutes=5)
MIN_PASSWORD_LEN = 8
SIGNUP_PASSWORD_MIN_LENGTH = 12
SIGNUP_PASSPHRASE_MIN_LENGTH = 16
_COMMON_PASSWORDS = {
    "admin",
    "adminadmin",
    "changeme",
    "letmein",
    "password",
    "password1",
    "password123",
    "qwerty",
    "qwerty123",
    "sixsentences",
    "welcome",
    "welcome123",
}
_OBVIOUS_PASSWORD_SEQUENCES = ("123456", "abcdef", "qwerty")
_BEARER_KINDS = ("session", "api_key")
API_KEY_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    "research:read": {
        "label": "Read research",
        "description": "Projects, run status, audit events, results and review records.",
    },
    "research:write": {
        "label": "Run research",
        "description": "Create and control runs, answer gates and use grounded run chat.",
    },
    "library:read": {
        "label": "Read papers",
        "description": "List acquisition records and download workspace PDFs.",
    },
    "library:write": {
        "label": "Add papers",
        "description": "Upload, attach and acquire open-access PDFs without delete access.",
    },
    "companion:read": {
        "label": "Read live companion sessions",
        "description": "Read only your own live transcript sessions and grounded answers.",
    },
    "companion:write": {
        "label": "Capture live conversations",
        "description": "Create and finish your own text-only live companion sessions.",
    },
    "capture:read": {
        "label": "Read browser captures",
        "description": "List web sources saved by the SixSentences browser extension.",
    },
    "capture:write": {
        "label": "Save browser captures",
        "description": "Save explicitly confirmed paper metadata and web sources.",
    },
}
PRODUCT_ONLY_API_KEY_SCOPES = frozenset(
    {"companion:read", "companion:write", "capture:read", "capture:write"}
)
DEFAULT_API_KEY_SCOPES = ("research:read",)
API_KEY_EXPIRY_DAYS = (30, 90, 180, 365)
_API_KEY_ROUTE_SCOPES: dict[tuple[str, str], str] = {
    ("GET", "/projects"): "research:read",
    ("GET", "/runs"): "research:read",
    ("GET", "/runs/{run_id}"): "research:read",
    ("GET", "/runs/{run_id}/events"): "research:read",
    ("GET", "/runs/{run_id}/events/stream"): "research:read",
    ("GET", "/runs/{run_id}/works"): "research:read",
    ("GET", "/runs/{run_id}/web-sources"): "research:read",
    ("GET", "/runs/{run_id}/methods"): "research:read",
    ("GET", "/runs/{run_id}/report"): "research:read",
    ("GET", "/runs/{run_id}/protocol"): "research:read",
    ("GET", "/runs/{run_id}/queue"): "research:read",
    ("GET", "/runs/{run_id}/decisions"): "research:read",
    ("GET", "/runs/{run_id}/chat"): "research:read",
    ("GET", "/runs/{run_id}/export"): "research:read",
    ("POST", "/projects"): "research:write",
    ("PATCH", "/projects/{project_id}"): "research:write",
    ("POST", "/runs"): "research:write",
    ("POST", "/projects/{project_id}/runs"): "research:write",
    ("PATCH", "/runs/{run_id}"): "research:write",
    ("POST", "/runs/{run_id}/pause"): "research:write",
    ("POST", "/runs/{run_id}/resume"): "research:write",
    ("POST", "/runs/{run_id}/retry"): "research:write",
    ("POST", "/runs/{run_id}/cancel"): "research:write",
    ("POST", "/runs/{run_id}/report"): "research:write",
    ("POST", "/runs/{run_id}/protocol/regenerate"): "research:write",
    ("POST", "/runs/{run_id}/protocol/approve"): "research:write",
    ("POST", "/runs/{run_id}/decisions"): "research:write",
    ("POST", "/runs/{run_id}/calibration"): "research:write",
    ("POST", "/runs/{run_id}/chat"): "research:write",
    ("POST", "/runs/{run_id}/living"): "research:write",
    ("POST", "/runs/{run_id}/recheck"): "research:write",
    ("GET", "/documents"): "library:read",
    ("GET", "/documents/{document_id}/file"): "library:read",
    ("GET", "/runs/{run_id}/documents"): "library:read",
    ("POST", "/orgs/current/documents"): "library:write",
    ("POST", "/runs/{run_id}/documents"): "library:write",
    ("POST", "/runs/{run_id}/acquire"): "library:write",
    ("PATCH", "/documents/{document_id}/metadata"): "library:write",
    ("GET", "/interviews/live/config"): "companion:read",
    ("GET", "/interviews/live/sessions"): "companion:read",
    ("GET", "/interviews/live/sessions/{session_id}"): "companion:read",
    (
        "GET",
        "/interviews/live/sessions/{session_id}/segments/{client_event_id}",
    ): "companion:read",
    ("GET", "/interviews/live/sessions/{session_id}/events"): "companion:read",
    ("GET", "/interviews/live/sessions/{session_id}/asks"): "companion:read",
    ("GET", "/interviews/live/sessions/{session_id}/brainstorms"): "companion:read",
    (
        "GET",
        "/interviews/live/sessions/{session_id}/brainstorms/{brainstorm_id}",
    ): "companion:read",
    ("POST", "/interviews/live/sessions"): "companion:write",
    ("PATCH", "/interviews/live/sessions/{session_id}"): "companion:write",
    ("POST", "/interviews/live/sessions/{session_id}/segments"): "companion:write",
    ("POST", "/interviews/live/sessions/{session_id}/ask"): "companion:write",
    ("POST", "/interviews/live/sessions/{session_id}/brainstorms"): "companion:write",
    (
        "POST",
        "/interviews/live/sessions/{session_id}/brainstorms/{brainstorm_id}/cancel",
    ): "companion:write",
    ("POST", "/interviews/live/sessions/{session_id}/complete"): "companion:write",
    ("POST", "/interviews/live/sessions/{session_id}/cancel"): "companion:write",
    ("POST", "/companion/paper-chats"): "companion:write",
    ("GET", "/companion/paper-chats/{chat_id}"): "companion:read",
    ("GET", "/companion/paper-chats/{chat_id}/history"): "companion:read",
    ("POST", "/companion/paper-chats/{chat_id}/turns/stream"): "companion:write",
    ("GET", "/companion/paper-chats/{chat_id}/turns/active"): "companion:read",
    ("GET", "/companion/paper-chats/{chat_id}/turns/latest"): "companion:read",
    ("GET", "/companion/paper-chats/{chat_id}/turns/{turn_id}"): "companion:read",
    (
        "GET",
        "/companion/paper-chats/{chat_id}/turns/{turn_id}/events/stream",
    ): "companion:read",
    (
        "POST",
        "/companion/paper-chats/{chat_id}/turns/{turn_id}/stop",
    ): "companion:write",
    ("GET", "/library/web-sources"): "capture:read",
    ("POST", "/library/web-sources"): "capture:write",
    ("POST", "/browser-capture/web"): "capture:write",
    ("POST", "/browser-capture/papers"): "capture:write",
    ("POST", "/browser-capture/citations"): "capture:write",
    ("POST", "/browser-capture/device/revoke"): "capture:write",
    ("PATCH", "/library/web-sources/{source_id}"): "library:write",
}


def normalize_api_scopes(scopes: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Validate, de-duplicate and add the read prerequisite for write scopes."""
    requested = list(scopes or DEFAULT_API_KEY_SCOPES)
    unknown = sorted(set(requested) - set(API_KEY_SCOPE_CATALOG))
    if unknown:
        raise AuthError(f"unknown API key scope: {', '.join(unknown)}")
    if not requested:
        raise AuthError("choose at least one API key scope")
    if "research:write" in requested and "research:read" not in requested:
        requested.insert(0, "research:read")
    if "library:write" in requested and "library:read" not in requested:
        requested.insert(0, "library:read")
    if "companion:write" in requested and "companion:read" not in requested:
        requested.insert(0, "companion:read")
    return tuple(scope for scope in API_KEY_SCOPE_CATALOG if scope in requested)


def api_key_scope_for(method: str, route_path: str) -> str | None:
    """Return the machine scope for a public API route, or ``None`` when the
    endpoint belongs only to an interactive browser session."""
    return _API_KEY_ROUTE_SCOPES.get((method.upper(), route_path))


def public_api_route_scopes() -> dict[tuple[str, str], str]:
    """Return an isolated copy of the public machine API allowlist."""
    return dict(_API_KEY_ROUTE_SCOPES)


class AuthError(RuntimeError):
    pass


class EmailVerificationRequired(AuthError):
    pass


class MFARequired(AuthError):
    def __init__(self, user_id: int) -> None:
        super().__init__("two-factor authentication required")
        self.user_id = user_id


class MFAError(AuthError):
    pass


class GoogleAccountLinkRequired(AuthError):
    """An email match is not strong enough to link an existing account."""


@dataclass(frozen=True)
class GoogleAuthResult:
    user: User
    token: str | None
    created: bool
    verification_required: bool


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    org_id: int
    token_id: int
    role: str
    email: str
    token_kind: str = "session"
    api_scopes: tuple[str, ...] = ()
    first_name: str = ""
    language: str = "en"
    assistant_preferences: dict[str, str] = field(default_factory=dict)
    onboarded: bool = False
    two_factor_enabled: bool = False


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt_b64), int(iterations))
    return hmac.compare_digest(dk, _unb64(hash_b64))


def _consume_password_work(password: str) -> None:
    """Spend one production password-hash work factor for an unknown user.

    Authentication must not become measurably cheaper merely because an email
    address is absent.  A fixed, non-secret salt is sufficient here because
    the derived bytes are deliberately discarded; this is timing padding, not
    a credential hash.
    """
    hashlib.pbkdf2_hmac("sha256", password.encode(), b"sixsentences-auth-miss", PBKDF2_ITERATIONS)


def signup_password_issues(
    password: str, *, email: str = "", first_name: str = ""
) -> tuple[str, ...]:
    """Return stable, user-facing reasons why a signup password is weak.

    A long passphrase is accepted without arbitrary composition rules. Shorter
    passwords need character variety, and obvious or personal choices are
    rejected regardless of length.
    """
    issues: list[str] = []
    if len(password) < SIGNUP_PASSWORD_MIN_LENGTH:
        issues.append(f"use at least {SIGNUP_PASSWORD_MIN_LENGTH} characters")
    character_classes = sum(
        (
            any((character.islower() for character in password)),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
        )
    )
    if len(password) < SIGNUP_PASSPHRASE_MIN_LENGTH and character_classes < 3:
        issues.append(
            f"use at least three character types or a passphrase with {SIGNUP_PASSPHRASE_MIN_LENGTH} characters"
        )
    normalized = "".join(character.lower() for character in password if character.isalnum())
    if (
        normalized in _COMMON_PASSWORDS
        or any(sequence in normalized for sequence in _OBVIOUS_PASSWORD_SEQUENCES)
        or (len(normalized) >= 8 and len(set(normalized)) == 1)
    ):
        issues.append("avoid common passwords, repeated characters and sequences")
    personal_values = (email.partition("@")[0], first_name)
    personal_tokens = {
        "".join(character.lower() for character in value if character.isalnum())
        for value in personal_values
    }
    if any(len(token) >= 4 and token in normalized for token in personal_tokens):
        issues.append("do not use your name or email address")
    return tuple(issues)


def validate_signup_password(password: str, *, email: str = "", first_name: str = "") -> None:
    """Reject a weak password at the public account-creation boundary."""
    issues = signup_password_issues(password, email=email, first_name=first_name)
    if issues:
        raise AuthError("choose a stronger password: " + "; ".join(issues))


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _issue_token(
    session: Session,
    user: User,
    kind: str,
    *,
    name: str = "",
    ttl: timedelta | None = None,
    scopes: tuple[str, ...] = (),
) -> str:
    marker = {
        "api_key": "sk",
        "email_verification": "ev",
        "mfa_challenge": "mc",
        "stream_ticket": "st",
        "companion_code": "cc",
        "capture_code": "bc",
    }.get(kind, "ss")
    raw = f"six_{marker}_{secrets.token_urlsafe(32)}"
    token = AuthToken(
        org_id=user.org_id,
        user_id=user.id,
        kind=kind,
        token_hash=_token_hash(raw),
        prefix=raw[:16],
        name=name,
        scopes=list(scopes),
        expires_at=datetime.now(UTC) + ttl if ttl is not None else None,
    )
    session.add(token)
    session.flush()
    return raw


def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < datetime.now(UTC)


def register(
    session: Session, email: str, password: str, org_name: str, first_name: str = ""
) -> tuple[User, str]:
    """Create a new org and its owner user; return (user, session token)."""
    user = provision_owner(session, email, password, org_name, first_name)
    token = _issue_token(session, user, "session", ttl=SESSION_TTL)
    return (user, token)


def provision_owner(
    session: Session,
    email: str,
    password: str,
    org_name: str,
    first_name: str = "",
    *,
    email_verified: bool = True,
) -> User:
    """Create an org owner without issuing a session token.

    Operator-created and emailed accounts must not leave an unused bearer
    token in the database before the recipient has ever signed in.
    """
    email = email.strip().lower()
    first_name = first_name.strip()
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise AuthError("email already registered")
    name = org_name or f"{email}'s org"
    if session.scalar(select(Org).where(Org.name == name)) is not None:
        raise AuthError("organization name already taken")
    org = Org(name=name)
    session.add(org)
    session.flush()
    user = User(
        org_id=org.id,
        email=email,
        first_name=first_name,
        password_hash=hash_password(password),
        role="owner",
        email_verified_at=datetime.now(UTC) if email_verified else None,
    )
    session.add(user)
    session.flush()
    return user


def add_member(
    session: Session,
    org_id: int,
    email: str,
    password: str,
    role: str,
    first_name: str = "",
) -> User:
    """Owner-only: add a member user to an existing org."""
    email = email.strip().lower()
    if role not in ("owner", "member"):
        raise AuthError("role must be 'owner' or 'member'")
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise AuthError("email already registered")
    user = User(
        org_id=org_id,
        email=email,
        first_name=first_name.strip(),
        password_hash=hash_password(password),
        role=role,
        email_verified_at=datetime.now(UTC),
    )
    session.add(user)
    session.flush()
    return user


def _finish_login(session: Session, user: User) -> str:
    if user.first_login_at is None:
        user.first_login_at = datetime.now(UTC)
    return _issue_token(session, user, "session", ttl=SESSION_TTL)


def authenticate(session: Session, email: str, password: str) -> str:
    """Verify credentials and issue a session, or request the second factor."""
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        _consume_password_work(password)
        raise AuthError("invalid credentials")
    password_valid = verify_password(password, user.password_hash)
    if not password_valid or not user.is_active:
        raise AuthError("invalid credentials")
    if user.email_verified_at is None:
        raise EmailVerificationRequired("email verification required")
    if user.two_factor_enabled_at is not None:
        raise MFARequired(user.id)
    return _finish_login(session, user)


def _available_org_name(session: Session, requested: str, email: str) -> str:
    base = requested.strip() or f"{email.split('@', 1)[0]}'s workspace"
    base = base[:200]
    candidate = base
    suffix = 2
    while session.scalar(select(Org.id).where(Org.name == candidate)) is not None:
        tail = f" ({suffix})"
        candidate = f"{base[: 200 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def authenticate_google(
    session: Session,
    identity: GoogleIdentity,
    *,
    intent: str,
    allow_signup: bool,
    org_name: str = "",
) -> GoogleAuthResult:
    """Link or provision a Google identity, then run the normal login policy."""
    if intent not in {"login", "signup"}:
        raise AuthError("invalid Google authentication intent")
    created = False
    user = session.scalar(select(User).where(User.google_sub == identity.subject))
    if user is not None and intent == "signup":
        raise AuthError("An account already exists. Sign in instead.")
    if user is None:
        email_match = session.scalar(select(User).where(User.email == identity.email))
        if email_match is not None:
            if intent == "signup":
                raise AuthError("An account already exists. Sign in instead.")
            if email_match.google_sub not in (None, identity.subject):
                raise AuthError("this email is linked to another Google account")
            if not identity.has_authoritative_email:
                raise GoogleAccountLinkRequired(
                    "Sign in with your password before connecting this Google account."
                )
            email_match.google_sub = identity.subject
            email_match.email_verified_at = email_match.email_verified_at or datetime.now(UTC)
            user = email_match
        else:
            if intent != "signup" or not allow_signup:
                raise AuthError("no SixSentences_ account exists for this Google account")
            org = Org(name=_available_org_name(session, org_name, identity.email))
            session.add(org)
            session.flush()
            user = User(
                org_id=org.id,
                email=identity.email,
                google_sub=identity.subject,
                first_name=(identity.given_name or identity.name).strip()[:80],
                password_hash=hash_password(secrets.token_urlsafe(48)),
                role="owner",
                email_verified_at=datetime.now(UTC) if identity.has_authoritative_email else None,
            )
            session.add(user)
            session.flush()
            created = True
            if user.email_verified_at is None:
                return GoogleAuthResult(
                    user=user, token=None, created=True, verification_required=True
                )
    if not user.is_active:
        raise AuthError("invalid credentials")
    if user.email_verified_at is None:
        raise EmailVerificationRequired("email verification required")
    if user.two_factor_enabled_at is not None:
        raise MFARequired(user.id)
    return GoogleAuthResult(
        user=user,
        token=_finish_login(session, user),
        created=created,
        verification_required=False,
    )


def create_mfa_challenge(session: Session, user_id: int) -> str:
    """Replace any older login challenge and return a five-minute token."""
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.two_factor_enabled_at is None:
        raise MFAError("two-factor authentication is not enabled")
    now = datetime.now(UTC)
    previous = session.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.kind == "mfa_challenge",
            AuthToken.revoked_at.is_(None),
        )
    ).all()
    for token in previous:
        token.revoked_at = now
    return _issue_token(session, user, "mfa_challenge", ttl=MFA_CHALLENGE_TTL)


def _totp_secret(user: User) -> str:
    if not user.two_factor_secret_encrypted:
        raise MFAError("two-factor authentication is not configured")
    try:
        return decrypt_credential(user.two_factor_secret_encrypted)
    except CredentialError as exc:
        raise MFAError("two-factor authentication is unavailable") from exc


def _verify_second_factor(user: User, code: str) -> None:
    secret = _totp_secret(user)
    matched_counter = verify_totp(secret, code, last_counter=user.two_factor_last_counter)
    if matched_counter is not None:
        user.two_factor_last_counter = matched_counter
        return
    remaining = consume_recovery_code(code, list(user.two_factor_recovery_hashes or []))
    if remaining is None:
        raise MFAError("invalid or already used authentication code")
    user.two_factor_recovery_hashes = remaining


def verify_mfa_challenge(session: Session, raw_challenge: str, code: str) -> str:
    """Spend a valid challenge and issue the full session token."""
    challenge = session.scalar(
        select(AuthToken).where(AuthToken.token_hash == _token_hash(raw_challenge))
    )
    if (
        challenge is None
        or challenge.kind != "mfa_challenge"
        or challenge.revoked_at is not None
        or _is_expired(challenge.expires_at)
    ):
        raise MFAError("invalid or expired two-factor challenge")
    user = session.get(User, challenge.user_id)
    if user is None or not user.is_active or user.two_factor_enabled_at is None:
        raise MFAError("invalid or expired two-factor challenge")
    _verify_second_factor(user, code)
    spent = session.execute(
        update(AuthToken)
        .where(AuthToken.id == challenge.id, AuthToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    if getattr(spent, "rowcount", 0) != 1:
        raise MFAError("invalid or expired two-factor challenge")
    return _finish_login(session, user)


def start_totp_setup(session: Session, user_id: int, current_password: str) -> tuple[str, str]:
    """Create a pending TOTP secret after re-authenticating the user."""
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise MFAError("user not found")
    if not verify_password(current_password, user.password_hash):
        raise MFAError("the current password is wrong")
    if user.two_factor_enabled_at is not None:
        raise MFAError("two-factor authentication is already enabled")
    secret = generate_totp_secret()
    try:
        user.two_factor_secret_encrypted = encrypt_credential(secret)
    except CredentialError as exc:
        raise MFAError("two-factor authentication is unavailable") from exc
    user.two_factor_recovery_hashes = []
    user.two_factor_last_counter = None
    return (secret, totp_uri(secret, user.email))


def _revoke_other_sessions(session: Session, user_id: int, keep_token_id: int) -> None:
    now = datetime.now(UTC)
    sessions = session.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user_id,
            AuthToken.kind == "session",
            AuthToken.revoked_at.is_(None),
            AuthToken.id != keep_token_id,
        )
    ).all()
    for token in sessions:
        token.revoked_at = now


def enable_totp(session: Session, user_id: int, code: str, *, keep_token_id: int) -> list[str]:
    """Confirm a pending secret and return one-time recovery codes."""
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise MFAError("user not found")
    if user.two_factor_enabled_at is not None:
        raise MFAError("two-factor authentication is already enabled")
    secret = _totp_secret(user)
    matched_counter = verify_totp(secret, code)
    if matched_counter is None:
        raise MFAError("invalid authentication code")
    recovery_codes, recovery_hashes = generate_recovery_codes()
    user.two_factor_enabled_at = datetime.now(UTC)
    user.two_factor_last_counter = None
    user.two_factor_recovery_hashes = recovery_hashes
    _revoke_other_sessions(session, user.id, keep_token_id)
    return recovery_codes


def disable_totp(
    session: Session,
    user_id: int,
    current_password: str,
    code: str,
    *,
    keep_token_id: int,
) -> None:
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.two_factor_enabled_at is None:
        raise MFAError("two-factor authentication is not enabled")
    if not verify_password(current_password, user.password_hash):
        raise MFAError("the current password is wrong")
    _verify_second_factor(user, code)
    user.two_factor_secret_encrypted = None
    user.two_factor_enabled_at = None
    user.two_factor_recovery_hashes = []
    user.two_factor_last_counter = None
    _revoke_other_sessions(session, user.id, keep_token_id)


def regenerate_recovery_codes(
    session: Session, user_id: int, current_password: str, code: str
) -> list[str]:
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.two_factor_enabled_at is None:
        raise MFAError("two-factor authentication is not enabled")
    if not verify_password(current_password, user.password_hash):
        raise MFAError("the current password is wrong")
    _verify_second_factor(user, code)
    recovery_codes, recovery_hashes = generate_recovery_codes()
    user.two_factor_recovery_hashes = recovery_hashes
    return recovery_codes


def create_api_key(
    session: Session,
    user: User,
    name: str,
    *,
    scopes: list[str] | tuple[str, ...] | None = None,
    expires_in_days: int = 90,
) -> str:
    """Create a scoped, expiring API key; return the raw value exactly once."""
    if expires_in_days not in API_KEY_EXPIRY_DAYS:
        raise AuthError("API keys may expire after 30, 90, 180 or 365 days")
    normalized = normalize_api_scopes(scopes)
    return _issue_token(
        session,
        user,
        "api_key",
        name=name,
        ttl=timedelta(days=expires_in_days),
        scopes=normalized,
    )


def create_stream_ticket(session: Session, user_id: int, run_id: int) -> str:
    """Issue a short-lived ticket that can open exactly one run stream."""
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("user not found")
    return _issue_token(session, user, "stream_ticket", name=f"run:{run_id}", ttl=STREAM_TICKET_TTL)


def create_companion_code(
    session: Session, user_id: int, code_challenge: str, device_name: str | None = None
) -> str:
    """Issue a short-lived one-use code for browser-to-desktop pairing."""
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("user not found")
    binding_scopes = [f"pkce:{code_challenge}"]
    if device_name:
        binding_scopes.append(f"device:{device_name}")
    return _issue_token(
        session,
        user,
        "companion_code",
        name="live-companion-pairing",
        ttl=COMPANION_CODE_TTL,
        scopes=tuple(binding_scopes),
    )


def exchange_companion_code(session: Session, raw_code: str, code_verifier: str) -> str | None:
    """Atomically consume a pairing code and return one scoped API key."""
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    code = session.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(raw_code)))
    if (
        code is None
        or code.kind != "companion_code"
        or code.name != "live-companion-pairing"
        or (code.revoked_at is not None)
        or _is_expired(code.expires_at)
    ):
        return None
    expected = next(
        (scope.removeprefix("pkce:") for scope in code.scopes or [] if scope.startswith("pkce:")),
        "",
    )
    verifier_digest = hashlib.sha256(code_verifier.encode()).digest()
    actual = base64.urlsafe_b64encode(verifier_digest).decode().rstrip("=")
    if not expected or not hmac.compare_digest(expected, actual):
        return None
    device_name = next(
        (
            scope.removeprefix("device:")
            for scope in code.scopes or []
            if scope.startswith("device:")
        ),
        "",
    )
    user = session.get(User, code.user_id)
    if user is None or not user.is_active or user.org_id != code.org_id:
        return None
    now = datetime.now(UTC)
    spent = session.execute(
        update(AuthToken)
        .where(
            AuthToken.id == code.id,
            AuthToken.kind == "companion_code",
            AuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=now, last_used_at=now)
    )
    if getattr(spent, "rowcount", 0) != 1:
        return None
    return create_api_key(
        session,
        user,
        f"Live Companion · {device_name}" if device_name else "Live Companion",
        scopes=("companion:read", "companion:write"),
        expires_in_days=90,
    )


def create_capture_code(
    session: Session,
    user_id: int,
    code_challenge: str,
    state: str,
    device_name: str | None = None,
) -> str:
    """Issue a five-minute, one-use PKCE code for Browser Capture."""
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("user not found")
    scopes = [f"pkce:{code_challenge}", f"state:{state}"]
    if device_name:
        scopes.append(f"device:{device_name}")
    return _issue_token(
        session,
        user,
        "capture_code",
        name="browser-capture-pairing",
        ttl=CAPTURE_CODE_TTL,
        scopes=tuple(scopes),
    )


def exchange_capture_code(
    session: Session, raw_code: str, code_verifier: str, state: str
) -> str | None:
    """Consume a correctly bound code; a wrong verifier never spends it."""
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    code = session.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(raw_code)))
    if (
        code is None
        or code.kind != "capture_code"
        or code.name != "browser-capture-pairing"
        or (code.revoked_at is not None)
        or _is_expired(code.expires_at)
    ):
        return None
    expected_challenge = next(
        (scope.removeprefix("pkce:") for scope in code.scopes or [] if scope.startswith("pkce:")),
        "",
    )
    expected_state = next(
        (scope.removeprefix("state:") for scope in code.scopes or [] if scope.startswith("state:")),
        "",
    )
    digest = hashlib.sha256(code_verifier.encode()).digest()
    actual_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if (
        not expected_challenge
        or not expected_state
        or (not hmac.compare_digest(expected_challenge, actual_challenge))
        or (not hmac.compare_digest(expected_state, state))
    ):
        return None
    user = session.get(User, code.user_id)
    if user is None or not user.is_active or user.org_id != code.org_id:
        return None
    active_devices = [
        token
        for token in session.scalars(
            select(AuthToken).where(
                AuthToken.user_id == user.id,
                AuthToken.org_id == user.org_id,
                AuthToken.kind == "api_key",
                AuthToken.revoked_at.is_(None),
            )
        ).all()
        if set(token.scopes or ()) == {"capture:write"} and (not _is_expired(token.expires_at))
    ]
    if len(active_devices) >= MAX_BROWSER_CAPTURE_DEVICES:
        raise AuthError("revoke an existing Browser Capture device before connecting another")
    now = datetime.now(UTC)
    spent = session.execute(
        update(AuthToken)
        .where(
            AuthToken.id == code.id,
            AuthToken.kind == "capture_code",
            AuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=now, last_used_at=now)
    )
    if getattr(spent, "rowcount", 0) != 1:
        return None
    device_name = next(
        (
            scope.removeprefix("device:")
            for scope in code.scopes or []
            if scope.startswith("device:")
        ),
        "Browser",
    )
    return create_api_key(
        session,
        user,
        f"Browser Capture · {device_name}",
        scopes=("capture:write",),
        expires_in_days=90,
    )


def consume_stream_ticket(session: Session, raw_ticket: str, run_id: int) -> AuthContext | None:
    """Atomically spend a ticket bound to ``run_id`` and resolve its owner."""
    ticket = session.scalar(
        select(AuthToken).where(AuthToken.token_hash == _token_hash(raw_ticket))
    )
    if (
        ticket is None
        or ticket.kind != "stream_ticket"
        or ticket.name != f"run:{run_id}"
        or (ticket.revoked_at is not None)
        or _is_expired(ticket.expires_at)
    ):
        return None
    user = session.get(User, ticket.user_id)
    if user is None or not user.is_active or user.org_id != ticket.org_id:
        return None
    now = datetime.now(UTC)
    spent = session.execute(
        update(AuthToken)
        .where(
            AuthToken.id == ticket.id,
            AuthToken.kind == "stream_ticket",
            AuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=now, last_used_at=now)
    )
    if getattr(spent, "rowcount", 0) != 1:
        return None
    session.flush()
    return AuthContext(
        user_id=user.id,
        org_id=ticket.org_id,
        token_id=ticket.id,
        role=user.role,
        email=user.email,
        token_kind=ticket.kind,
        api_scopes=(),
        first_name=user.first_name,
        language=user.language,
        assistant_preferences=normalize_assistant_preferences(user.assistant_preferences),
        onboarded=user.onboarded_at is not None,
        two_factor_enabled=user.two_factor_enabled_at is not None,
    )


def create_reset_token(session: Session, email: str) -> str | None:
    """Issue a one-hour, single-purpose password-reset token, or None when the
    email is unknown (the caller must not leak which of the two happened)."""
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or user.email_verified_at is None:
        return None
    return _issue_token(session, user, "reset", ttl=RESET_TTL)


def create_email_verification_token(session: Session, user: User) -> str | None:
    """Replace any older email token and return one 24-hour raw token."""
    if not user.is_active or user.email_verified_at is not None:
        return None
    now = datetime.now(UTC)
    previous = session.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.kind == "email_verification",
            AuthToken.revoked_at.is_(None),
        )
    ).all()
    for token in previous:
        token.revoked_at = now
    return _issue_token(session, user, "email_verification", ttl=EMAIL_VERIFICATION_TTL)


def verify_email(session: Session, raw_token: str) -> tuple[User, str] | None:
    """Spend one email token and issue the account's first session."""
    token = session.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(raw_token)))
    if (
        token is None
        or token.kind != "email_verification"
        or token.revoked_at is not None
        or _is_expired(token.expires_at)
    ):
        return None
    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        return None
    now = datetime.now(UTC)
    user.email_verified_at = user.email_verified_at or now
    token.revoked_at = now
    session_token = _issue_token(session, user, "session", ttl=SESSION_TTL)
    session.flush()
    return (user, session_token)


def reset_password(session: Session, raw_token: str, new_password: str) -> bool:
    """Spend a reset token: set the new password and revoke every session."""
    if len(new_password) < MIN_PASSWORD_LEN:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    token = session.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(raw_token)))
    if (
        token is None
        or token.kind != "reset"
        or token.revoked_at is not None
        or _is_expired(token.expires_at)
    ):
        return False
    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        return False
    user.password_hash = hash_password(new_password)
    now = datetime.now(UTC)
    token.revoked_at = now
    sessions = session.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.kind == "session",
            AuthToken.revoked_at.is_(None),
        )
    ).all()
    for stale in sessions:
        stale.revoked_at = now
    session.flush()
    return True


def change_password(
    session: Session,
    user_id: int,
    current_password: str,
    new_password: str,
    keep_token_id: int | None = None,
) -> None:
    """Verify the current password and set a new one. Every other session is
    signed out; `keep_token_id` (the caller's own session) stays valid so the
    user is not logged out by their own change."""
    if len(new_password) < MIN_PASSWORD_LEN:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("invalid credentials")
    if not verify_password(current_password, user.password_hash):
        raise AuthError("the current password is wrong")
    user.password_hash = hash_password(new_password)
    now = datetime.now(UTC)
    sessions = session.scalars(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.kind == "session",
            AuthToken.revoked_at.is_(None),
        )
    ).all()
    for stale in sessions:
        if stale.id != keep_token_id:
            stale.revoked_at = now
    session.flush()


def revoke_token(session: Session, org_id: int, token_id: int) -> bool:
    token = session.get(AuthToken, token_id)
    if token is None or token.org_id != org_id or token.revoked_at is not None:
        return False
    token.revoked_at = datetime.now(UTC)
    session.flush()
    return True


def resolve_token(session: Session, raw: str) -> AuthContext | None:
    """Resolve a bearer token to an AuthContext, or None if invalid."""
    token = session.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(raw)))
    if token is None or token.revoked_at is not None or _is_expired(token.expires_at):
        return None
    if token.kind not in _BEARER_KINDS:
        return None
    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        return None
    now = datetime.now(UTC)
    last_used_at = token.last_used_at
    if (
        last_used_at is None
        or (last_used_at.replace(tzinfo=UTC) if last_used_at.tzinfo is None else last_used_at)
        < now - TOKEN_TOUCH_INTERVAL
    ):
        token.last_used_at = now
        session.flush()
    return AuthContext(
        user_id=user.id,
        org_id=token.org_id,
        token_id=token.id,
        role=user.role,
        email=user.email,
        token_kind=token.kind,
        api_scopes=normalize_api_scopes(token.scopes) if token.kind == "api_key" else (),
        first_name=user.first_name,
        language=user.language,
        assistant_preferences=normalize_assistant_preferences(user.assistant_preferences),
        onboarded=user.onboarded_at is not None,
        two_factor_enabled=user.two_factor_enabled_at is not None,
    )
