"""TOTP setup, login challenges and one-time recovery codes."""

from fastapi.testclient import TestClient

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import User, db_session
from sixsentences_server.core.mfa import (
    consume_recovery_code,
    generate_recovery_codes,
    hash_recovery_code,
    totp_code,
    totp_uri,
    verify_totp,
)


def test_rfc_6238_totp_vector_and_replay_guard() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    assert totp_code(secret, at=59) == "287082"
    counter = verify_totp(secret, "287082", at=59)
    assert counter == 1
    assert verify_totp(secret, "287082", at=59, last_counter=counter) is None
    assert verify_totp(secret, "not-a-code", at=59) is None


def test_recovery_codes_are_hashed_and_single_use() -> None:
    codes, hashes = generate_recovery_codes()

    assert len(codes) == len(hashes) == 10
    assert all(code not in hashes for code in codes)
    assert hash_recovery_code(codes[0].lower()) == hashes[0]
    remaining = consume_recovery_code(codes[0], hashes)
    assert remaining is not None and len(remaining) == 9
    assert consume_recovery_code(codes[0], remaining) is None


def test_totp_uri_escapes_account_label() -> None:
    uri = totp_uri("ABC123", "researcher+demo@example.org")

    assert uri.startswith("otpauth://totp/")
    assert "researcher%2Bdemo%40example.org" in uri
    assert "issuer=SixSentences_" in uri


def test_two_factor_api_setup_login_recovery_and_disable(
    settings: Settings,
) -> None:
    client = TestClient(create_app())
    registration = client.post(
        "/auth/register",
        json={
            "email": "secure@example.org",
            "password": "StrongPass123!",
            "org_name": "Secure Lab",
            "name": "Ada",
        },
    )
    assert registration.status_code == 201
    session_token = registration.json()["token"]
    client.headers["Authorization"] = f"Bearer {session_token}"

    wrong_password = client.post(
        "/auth/2fa/setup",
        json={"password": "wrong-password"},
    )
    assert wrong_password.status_code == 400

    setup = client.post(
        "/auth/2fa/setup",
        json={"password": "StrongPass123!"},
    )
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert secret in setup.json()["otpauth_uri"]

    with db_session() as session:
        user = session.query(User).filter_by(email="secure@example.org").one()
        assert user.two_factor_secret_encrypted
        assert secret not in user.two_factor_secret_encrypted
        assert user.two_factor_enabled_at is None

    enabled = client.post(
        "/auth/2fa/enable",
        json={"code": totp_code(secret)},
    )
    assert enabled.status_code == 200
    recovery_codes = enabled.json()["recovery_codes"]
    assert len(recovery_codes) == 10
    assert client.get("/auth/me").json()["two_factor_enabled"] is True
    assert client.get("/auth/2fa").json()["recovery_codes_remaining"] == 10

    password_stage = TestClient(create_app()).post(
        "/auth/login",
        json={"email": "secure@example.org", "password": "StrongPass123!"},
    )
    assert password_stage.status_code == 200
    assert "token" not in password_stage.json()
    challenge = password_stage.json()["challenge"]
    assert password_stage.json()["mfa_required"] is True

    second_factor = TestClient(create_app()).post(
        "/auth/2fa/verify",
        json={"challenge": challenge, "code": totp_code(secret)},
    )
    assert second_factor.status_code == 200
    assert second_factor.json()["token"].startswith("six_ss_")

    replay_stage = TestClient(create_app()).post(
        "/auth/login",
        json={"email": "secure@example.org", "password": "StrongPass123!"},
    )
    replay_challenge = replay_stage.json()["challenge"]
    replay = TestClient(create_app()).post(
        "/auth/2fa/verify",
        json={"challenge": replay_challenge, "code": totp_code(secret)},
    )
    assert replay.status_code == 401

    recovered = TestClient(create_app()).post(
        "/auth/2fa/verify",
        json={"challenge": replay_challenge, "code": recovery_codes[0]},
    )
    assert recovered.status_code == 200
    assert client.get("/auth/2fa").json()["recovery_codes_remaining"] == 9

    disabled = client.post(
        "/auth/2fa/disable",
        json={"password": "StrongPass123!", "code": recovery_codes[1]},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert client.get("/auth/me").json()["two_factor_enabled"] is False

    direct_login = TestClient(create_app()).post(
        "/auth/login",
        json={"email": "secure@example.org", "password": "StrongPass123!"},
    )
    assert direct_login.status_code == 200
    assert direct_login.json()["token"].startswith("six_ss_")
