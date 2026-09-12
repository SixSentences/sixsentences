"""Google Identity Services ID-token verification.

The browser only sends the credential returned by Google Identity Services.
The API verifies the JWT against Google's rotating public keys and never stores
Google access or refresh tokens.
"""

import base64
import hashlib
import hmac
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_CLOCK_SKEW_SECONDS = 60
_DEFAULT_CACHE_SECONDS = 3600
_MAX_CACHE_SECONDS = 86_400
_jwks_lock = threading.Lock()
_jwks_cache: tuple[float, dict[str, Any]] | None = None


class GoogleTokenError(ValueError):
    """The supplied Google credential could not be trusted."""


class _UnknownGoogleSigningKey(GoogleTokenError):
    """The cached key set may predate a Google signing-key rotation."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Verified identity claims needed by SixSentences_."""

    subject: str
    email: str
    email_verified: bool
    name: str = ""
    given_name: str = ""
    hosted_domain: str = ""

    @property
    def has_authoritative_email(self) -> bool:
        """Whether Google remains authoritative for control of this mailbox."""

        domain = self.email.rsplit("@", 1)[-1].lower()
        return self.email_verified and (domain == "gmail.com" or bool(self.hosted_domain))


def _decode_segment(segment: str, label: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (ValueError, TypeError) as exc:
        raise GoogleTokenError(f"invalid Google token {label}") from exc


def _decode_json(segment: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_decode_segment(segment, label))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GoogleTokenError(f"invalid Google token {label}") from exc
    if not isinstance(value, dict):
        raise GoogleTokenError(f"invalid Google token {label}")
    return value


def _cache_seconds(headers: httpx.Headers) -> int:
    match = re.search(r"(?:^|,)\s*max-age=(\d+)", headers.get("cache-control", ""))
    if not match:
        return _DEFAULT_CACHE_SECONDS
    return min(max(int(match.group(1)[:10]), 60), _MAX_CACHE_SECONDS)


def _google_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
    global _jwks_cache

    now = time.monotonic()
    if not force_refresh and _jwks_cache is not None and _jwks_cache[0] > now:
        return _jwks_cache[1]
    with _jwks_lock:
        now = time.monotonic()
        if not force_refresh and _jwks_cache is not None and _jwks_cache[0] > now:
            return _jwks_cache[1]
        try:
            response = httpx.get(
                GOOGLE_JWKS_URL,
                timeout=5.0,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GoogleTokenError("Google sign-in is temporarily unavailable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise GoogleTokenError("Google returned invalid signing keys")
        _jwks_cache = (now + _cache_seconds(response.headers), payload)
        return payload


def _claim_text(
    claims: dict[str, Any],
    name: str,
    *,
    required: bool = False,
    max_length: int,
) -> str:
    value = claims.get(name, "")
    if not isinstance(value, str) or len(value) > max_length:
        raise GoogleTokenError(f"invalid Google token claim: {name}")
    value = value.strip()
    if required and not value:
        raise GoogleTokenError(f"missing Google token claim: {name}")
    return value


def _claim_timestamp(claims: dict[str, Any], name: str, *, required: bool) -> int | None:
    value = claims.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GoogleTokenError(f"invalid Google token claim: {name}")
    try:
        return int(value)
    except (OverflowError, ValueError) as exc:
        raise GoogleTokenError(f"invalid Google token claim: {name}") from exc


def _public_key(jwks: dict[str, Any], kid: str) -> tuple[int, int]:
    keys = jwks.get("keys", [])
    key = next(
        (
            candidate
            for candidate in keys
            if isinstance(candidate, dict) and candidate.get("kid") == kid
        ),
        None,
    )
    if (
        key is None
        or key.get("kty") != "RSA"
        or key.get("alg") not in (None, "RS256")
        or key.get("use") not in (None, "sig")
        or not isinstance(key.get("n"), str)
        or not isinstance(key.get("e"), str)
    ):
        raise _UnknownGoogleSigningKey("unknown Google signing key")
    modulus_bytes = _decode_segment(key["n"], "signing key")
    exponent_bytes = _decode_segment(key["e"], "signing key")
    if len(modulus_bytes) < 256 or len(exponent_bytes) > 4:
        raise GoogleTokenError("invalid Google signing key")
    modulus = int.from_bytes(modulus_bytes, "big")
    exponent = int.from_bytes(exponent_bytes, "big")
    if modulus.bit_length() < 2048 or exponent < 3 or exponent % 2 == 0:
        raise GoogleTokenError("invalid Google signing key")
    return modulus, exponent


def _verify_rsa_sha256_signature(
    signature: bytes,
    message: bytes,
    public_key: tuple[int, int],
) -> bool:
    """Verify strict RSASSA-PKCS1-v1_5 with SHA-256 for a Google JWK."""

    modulus, exponent = public_key
    encoded_length = (modulus.bit_length() + 7) // 8
    if len(signature) != encoded_length:
        return False
    signature_number = int.from_bytes(signature, "big")
    if signature_number >= modulus:
        return False
    encoded = pow(signature_number, exponent, modulus).to_bytes(encoded_length, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
    digest_info += hashlib.sha256(message).digest()
    padding_length = encoded_length - len(digest_info) - 3
    if padding_length < 8:
        return False
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    return hmac.compare_digest(encoded, expected)


def verify_google_id_token(
    credential: str,
    client_id: str,
    *,
    jwks: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> GoogleIdentity:
    """Verify one GIS credential and return its minimal trusted identity."""

    if not client_id.strip():
        raise GoogleTokenError("Google sign-in is not configured")
    if len(credential) > 8192:
        raise GoogleTokenError("invalid Google token")
    parts = credential.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise GoogleTokenError("invalid Google token")
    encoded_header, encoded_claims, encoded_signature = parts
    header = _decode_json(encoded_header, "header")
    claims = _decode_json(encoded_claims, "payload")
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise GoogleTokenError("unsupported Google token signature")

    try:
        public_key = _public_key(jwks or _google_jwks(), header["kid"])
    except _UnknownGoogleSigningKey:
        if jwks is not None:
            raise
        # A valid token can reference a freshly rotated key just before the
        # cached set expires, so refresh Google's fixed key endpoint once.
        public_key = _public_key(_google_jwks(force_refresh=True), header["kid"])
    try:
        signature_valid = _verify_rsa_sha256_signature(
            _decode_segment(encoded_signature, "signature"),
            f"{encoded_header}.{encoded_claims}".encode("ascii"),
            public_key,
        )
    except UnicodeEncodeError as exc:
        raise GoogleTokenError("invalid Google token signature") from exc
    if not signature_valid:
        raise GoogleTokenError("invalid Google token signature")

    issuer = _claim_text(claims, "iss", required=True, max_length=100)
    if issuer not in GOOGLE_ISSUERS:
        raise GoogleTokenError("invalid Google token issuer")

    audience = claims.get("aud")
    expected_audience = client_id.strip()
    if isinstance(audience, str):
        audience_matches = audience == expected_audience
    elif isinstance(audience, list) and all(isinstance(item, str) for item in audience):
        audience_matches = expected_audience in audience
        if len(audience) > 1:
            audience_matches = audience_matches and claims.get("azp") == expected_audience
    else:
        audience_matches = False
    if not audience_matches:
        raise GoogleTokenError("Google token was issued for another application")

    current_timestamp = int((now or datetime.now(UTC)).timestamp())
    expires_at = _claim_timestamp(claims, "exp", required=True)
    issued_at = _claim_timestamp(claims, "iat", required=True)
    not_before = _claim_timestamp(claims, "nbf", required=False)
    assert expires_at is not None and issued_at is not None
    if expires_at < current_timestamp - _CLOCK_SKEW_SECONDS:
        raise GoogleTokenError("Google token has expired")
    if issued_at > current_timestamp + _CLOCK_SKEW_SECONDS:
        raise GoogleTokenError("Google token is not valid yet")
    if not_before is not None and not_before > current_timestamp + _CLOCK_SKEW_SECONDS:
        raise GoogleTokenError("Google token is not valid yet")

    email_verified = claims.get("email_verified")
    if email_verified is not True:
        raise GoogleTokenError("Google has not verified this email address")
    email = _claim_text(claims, "email", required=True, max_length=320).lower()
    if email.count("@") != 1:
        raise GoogleTokenError("invalid Google email address")

    return GoogleIdentity(
        subject=_claim_text(claims, "sub", required=True, max_length=255),
        email=email,
        email_verified=True,
        name=_claim_text(claims, "name", max_length=200),
        given_name=_claim_text(claims, "given_name", max_length=80),
        hosted_domain=_claim_text(claims, "hd", max_length=255).lower(),
    )
