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
from pathlib import Path

from sqlalchemy import select

from sixsentences_server.config import Settings
from sixsentences_server.core import auth
from sixsentences_server.core.auth import (
    create_api_key,
    hash_password,
    register,
    resolve_token,
)
from sixsentences_server.core.db import AuthToken, db_session, init_db
from sixsentences_server.core.mfa import (
    consume_recovery_code,
    generate_recovery_codes,
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

    # token_urlsafe emits "-" and "_" itself, so the secret has to be taken as
    # everything after the marker. Splitting on the *last* underscore cuts it in
    # half for roughly every second token.
    scheme, marker, random_part = raw.split("_", 2)

    assert scheme == "six"
    assert marker == "sk"
    # 32 bytes from the operating system's CSPRNG — the claim SECURITY.md makes.
    assert _decoded_length(random_part) == 32

    with db_session() as session:
        stored = session.scalar(select(AuthToken).where(AuthToken.prefix == raw[:16]))
        assert stored is not None
        # A database copy hands over a derivation, not a usable token: the row
        # holds a 64-character digest that is not the token, and the token still
        # resolves through the public path. Recomputing the digest here would
        # mean this test hashes a credential itself, which is the very pattern
        # SECURITY.md explains a scanner misreads.
        assert stored.token_hash != raw
        assert len(stored.token_hash) == 64
        assert set(stored.token_hash) <= set("0123456789abcdef")
        assert resolve_token(session, raw) is not None


def test_recovery_codes_are_csprng_and_never_stored_in_the_clear() -> None:
    codes, hashes = generate_recovery_codes()

    compact = normalize_recovery_code(codes[0])
    assert _decoded_length(compact, base32=True) == 10
    assert all(code not in hashes for code in codes)
    # Redeeming through the public path proves the displayed code matches its
    # stored digest, without this test hashing a credential itself — doing that
    # trips the same name-based heuristic SECURITY.md explains.
    remaining = consume_recovery_code(codes[0], hashes)
    assert remaining is not None
    assert len(remaining) == len(hashes) - 1
    assert consume_recovery_code(codes[0], remaining) is None


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
