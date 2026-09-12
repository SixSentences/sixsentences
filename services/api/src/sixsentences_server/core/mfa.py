"""TOTP and recovery-code primitives for two-factor authentication.

The implementation follows RFC 6238 with the authenticator-app defaults:
HMAC-SHA1, six digits and a 30-second period. It is intentionally stdlib-only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
RECOVERY_CODE_COUNT = 10


def generate_totp_secret() -> str:
    """Return a 160-bit Base32 secret accepted by common authenticator apps."""

    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_uri(secret: str, email: str, *, issuer: str = "SixSentences") -> str:
    """Build an ``otpauth://`` URI without exposing the secret to a third party."""

    label = quote(f"{issuer}:{email}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": str(TOTP_DIGITS),
            "period": str(TOTP_PERIOD_SECONDS),
        }
    )
    return f"otpauth://totp/{label}?{query}"


def _counter(at: float | int | None = None) -> int:
    timestamp = time.time() if at is None else float(at)
    return int(timestamp // TOTP_PERIOD_SECONDS)


def totp_code(secret: str, *, at: float | int | None = None) -> str:
    """Generate the six-digit code for ``at`` (used by tests and verification)."""

    counter = _counter(at)
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(
    secret: str,
    code: str,
    *,
    last_counter: int | None = None,
    at: float | int | None = None,
) -> int | None:
    """Return the matched counter, rejecting malformed and replayed codes."""

    normalized = "".join(code.split())
    if len(normalized) != TOTP_DIGITS or not normalized.isdigit():
        return None
    current = _counter(at)
    for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        candidate_counter = current + offset
        if candidate_counter < 0:
            continue
        candidate_at = candidate_counter * TOTP_PERIOD_SECONDS
        if hmac.compare_digest(totp_code(secret, at=candidate_at), normalized):
            if last_counter is not None and candidate_counter <= last_counter:
                return None
            return candidate_counter
    return None


def normalize_recovery_code(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(normalize_recovery_code(code).encode()).hexdigest()


def generate_recovery_codes() -> tuple[list[str], list[str]]:
    """Return display codes and their one-way hashes."""

    raw_codes: list[str] = []
    hashes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        compact = base64.b32encode(secrets.token_bytes(10)).decode().rstrip("=")
        display = "-".join(compact[index : index + 4] for index in range(0, 16, 4))
        raw_codes.append(display)
        hashes.append(hash_recovery_code(display))
    return raw_codes, hashes


def consume_recovery_code(code: str, stored_hashes: list[str]) -> list[str] | None:
    """Return the remaining hashes when ``code`` matches, otherwise ``None``."""

    candidate = hash_recovery_code(code)
    for index, stored in enumerate(stored_hashes):
        if hmac.compare_digest(candidate, stored):
            return [*stored_hashes[:index], *stored_hashes[index + 1 :]]
    return None
