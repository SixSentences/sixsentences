"""The storage of each credential class depends on an entropy assumption.

`SECURITY.md` explains why account passwords go through 600,000 PBKDF2
iterations while bearer tokens and recovery codes are stored as a plain SHA-256
digest: the latter are CSPRNG output with no guessable structure, so a slow hash
would buy nothing and would run on every authenticated request. That reasoning
holds only while the tokens really are what it says they are, which is what
these tests pin.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from sqlalchemy import select

from sixsentences_server.config import Settings
from sixsentences_server.core import auth
from sixsentences_server.core.auth import create_api_key, hash_password, register
from sixsentences_server.core.db import AuthToken, db_session, init_db
from sixsentences_server.core.mfa import (
    generate_recovery_codes,
    hash_recovery_code,
    normalize_recovery_code,
)


def _decoded_length(value: str, *, base32: bool = False) -> int:
    padded = value + "=" * (-len(value) % (8 if base32 else 4))
    decoder = base64.b32decode if base32 else base64.urlsafe_b64decode
    return len(decoder(padded))


def test_issued_bearer_tokens_carry_the_entropy_their_storage_assumes(
    settings: Settings,
) -> None:
    init_db()
    with db_session() as session:
        user, _ = register(session, "owner@example.org", "StrongPass123!", "Owner")
        raw = create_api_key(session, user, "ci")

    prefix, _, random_part = raw.rpartition("_")

    assert prefix.startswith("six_")
    # 32 bytes from the operating system's CSPRNG — the claim SECURITY.md makes.
    assert _decoded_length(random_part) == 32

    with db_session() as session:
        stored = session.scalar(select(AuthToken).where(AuthToken.prefix == raw[:16]))

    # A database copy hands over digests, not usable tokens.
    assert stored is not None
    assert stored.token_hash != raw
    assert stored.token_hash == hashlib.sha256(raw.encode()).hexdigest()


def test_recovery_codes_are_csprng_and_never_stored_in_the_clear() -> None:
    codes, hashes = generate_recovery_codes()

    compact = normalize_recovery_code(codes[0])
    assert _decoded_length(compact, base32=True) == 10
    assert all(code not in hashes for code in codes)
    assert hash_recovery_code(codes[0]) in hashes


def test_account_passwords_keep_the_documented_cost_and_format(settings: Settings) -> None:
    stored = hash_password("StrongPass123!")
    algorithm, iterations, salt, digest = stored.split("$")

    assert algorithm == "pbkdf2_sha256"
    assert int(iterations) >= 1000  # the suite lowers this; the source sets the real cost
    assert _decoded_length(salt) == 16
    assert _decoded_length(digest) == 32
    # The documented cost lives in the source, not in a test-time override.
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "PBKDF2_ITERATIONS = 600000" in source
