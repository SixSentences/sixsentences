"""The doctor answers an operator's questions without printing their secrets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sixsentences_server.cli import DOCTOR_JSON_FIELDS, app
from sixsentences_server.config import Settings, get_settings
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
    monkeypatch.setenv("SIX_PUBMED_ENABLED", "true")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", "pubmed-contact-canary@example.org")
    monkeypatch.setenv("SIX_PUBMED_API_KEY", "pubmed-key-canary")
    init_db()

    rendered = " ".join(f"{check.name} {check.detail}" for check in run_doctor(Settings()))

    for canary in (
        "mail-user-canary",
        "mail-password-canary",
        "gemini-key-canary",
        "pubmed-contact-canary",
        "pubmed-key-canary",
    ):
        assert canary not in rendered
    # The enabled feature is still reported, just without its credential.
    assert "speech" in rendered

    # The machine-readable form is pasted into issues just as often.
    emitted = CliRunner().invoke(app, ["doctor", "--json"]).stdout
    for canary in (
        "mail-user-canary",
        "mail-password-canary",
        "gemini-key-canary",
        "pubmed-contact-canary",
        "pubmed-key-canary",
    ):
        assert canary not in emitted


def test_pubmed_doctor_is_off_ready_or_failed_without_network_access() -> None:
    disabled = Settings(pubmed_enabled=False)
    ready = Settings(pubmed_enabled=True, pubmed_email="operator@example.org")
    invalid = Settings(pubmed_enabled=True, pubmed_email="invalid contact@example.org")

    disabled_check = next(check for check in run_doctor(disabled) if check.name == "PubMed")
    ready_check = next(check for check in run_doctor(ready) if check.name == "PubMed")
    invalid_check = next(check for check in run_doctor(invalid) if check.name == "PubMed")

    assert disabled_check.state == "off"
    assert ready_check.state == "ok"
    assert invalid_check.state == "failed"


def test_json_output_is_the_whole_of_stdout(settings: Settings) -> None:
    """Whatever reads this is parsing it, so nothing else may share the stream."""
    init_db()

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload
    for entry in payload:
        assert tuple(entry) == DOCTOR_JSON_FIELDS
        assert entry["state"] in {"ok", "off", "failed"}
        assert entry["detail"]
    # The same questions the text table answers, in the same order.
    assert [entry["name"] for entry in payload] == [check.name for check in run_doctor(settings)]


def test_json_output_keeps_the_exit_code(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A monitor reads both the body and the status."""
    monkeypatch.setenv("SIX_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SIX_SMTP_PORT", "1")
    # The command reads its own settings, and they are cached.
    get_settings.cache_clear()
    init_db()

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert [entry for entry in payload if entry["state"] == "failed"]


def test_the_text_table_is_what_it_was(settings: Settings) -> None:
    """--json is an addition; the format people read must not move."""
    init_db()

    result = CliRunner().invoke(app, ["doctor"])
    checks = run_doctor(settings)
    width = max(len(check.name) for check in checks)
    expected = [
        f"{ {'ok': 'ok', 'off': 'off', 'failed': 'FAIL'}[check.state]:>4}  "
        f"{check.name:<{width}}  {check.detail}"
        for check in checks
    ]

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [*expected, "", "Every configured feature answered."]


def test_unwritable_storage_is_reported_rather_than_raised(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    check = _storage_check(blocker / "data")

    assert check.state == "failed"
    assert "not writable" in check.detail
