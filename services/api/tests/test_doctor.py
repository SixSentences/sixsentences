"""The doctor answers an operator's questions without printing their secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from sixsentences_server.config import Settings
from sixsentences_server.core.db import init_db
from sixsentences_server.ops.doctor import _mail_check, _storage_check, failed, run_doctor


def test_a_configured_deployment_answers_every_question(settings: Settings) -> None:
    init_db()

    checks = {check.name: check for check in run_doctor(settings)}

    assert checks["database"].state == "ok"
    assert "tables" in checks["database"].detail
    assert checks["storage"].state == "ok"
    # A feature nobody configured is off, not broken.
    assert checks["mail"].state == "off"
    assert "registration" in checks["mail"].detail
    # Every question is answered, and none of the answers is a fault.
    assert {check.state for check in checks.values()} <= {"ok", "off"}
    assert all(check.detail for check in checks.values())
    assert failed(list(checks.values())) == []


def test_an_unreachable_mail_host_is_a_failure_with_a_reason(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIX_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SIX_SMTP_PORT", "1")

    check = _mail_check(Settings())

    assert check.state == "failed"
    assert "127.0.0.1:1" in check.detail


def test_no_configured_secret_reaches_the_output(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator has to be able to paste this into an issue."""

    monkeypatch.setenv("SIX_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SIX_SMTP_PORT", "1")
    monkeypatch.setenv("SIX_SMTP_USERNAME", "mail-user-canary")
    monkeypatch.setenv("SIX_SMTP_PASSWORD", "mail-password-canary")
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "gemini-key-canary")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "true")
    init_db()

    rendered = " ".join(f"{check.name} {check.detail}" for check in run_doctor(Settings()))

    for canary in ("mail-user-canary", "mail-password-canary", "gemini-key-canary"):
        assert canary not in rendered
    # The enabled feature is still reported, just without its credential.
    assert "speech" in rendered


def test_unwritable_storage_is_reported_rather_than_raised(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    check = _storage_check(blocker / "data")

    assert check.state == "failed"
    assert "not writable" in check.detail
