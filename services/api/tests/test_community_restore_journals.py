"""Unprivileged restore journal selection with real authenticated synthetic chains."""

from __future__ import annotations

import importlib.util
import io
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from sixsentences_server.config import Settings
from sixsentences_server.ops.erasure_ledger import ErasureLedgerError, append_event

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "community_restore_journals", ROOT / "deploy/community/restore_journals.py"
)
assert SPEC is not None and SPEC.loader is not None
JOURNALS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JOURNALS)


def append_synthetic(settings: Settings, number: int) -> bytes:
    """Append an authenticated fixture event without any real identity or database."""
    append_event(
        settings,
        subject="workspace",
        reason="account_request",
        email=f"synthetic-{number}@example.invalid",
        org_id=number,
        user_id=number,
    )
    return settings.resolved_erasure_ledger_path.read_bytes()


def test_verification_uses_private_temporary_file_and_original_parser(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = append_synthetic(settings, 1)
    original_path = settings.resolved_erasure_ledger_path
    original_reader = JOURNALS.read_events
    checked: list[Path] = []

    def inspect(copied: Settings) -> object:
        path = copied.resolved_erasure_ledger_path
        assert path != original_path and path.parent == Path("/tmp")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_bytes() == payload
        checked.append(path)
        return original_reader(copied)

    monkeypatch.setattr(JOURNALS, "read_events", inspect)
    JOURNALS.verify_bytes(payload, settings)
    assert len(checked) == 1 and not checked[0].exists()
    assert settings.resolved_erasure_ledger_path == original_path
    assert original_path.read_bytes() == payload


@pytest.mark.parametrize("candidate_state", ["empty", "equal", "older", "newer"])
def test_selection_keeps_exact_authenticated_prefix_extension(
    settings: Settings, candidate_state: str
) -> None:
    first = append_synthetic(settings, 1)
    second = append_synthetic(settings, 2)
    candidate = {"empty": b"", "equal": second, "older": first, "newer": second}[candidate_state]
    if candidate_state == "newer":
        settings.resolved_erasure_ledger_path.write_bytes(first)
    assert JOURNALS.select_journal(candidate, settings) == (
        "backup" if candidate_state == "newer" else "current"
    )
    assert stat.S_IMODE(settings.resolved_erasure_ledger_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.resolved_erasure_ledger_path.parent.stat().st_mode) == 0o700


def test_only_initially_missing_live_journal_is_empty(settings: Settings) -> None:
    path = settings.resolved_erasure_ledger_path
    assert not path.exists()
    assert JOURNALS.select_journal(b"", settings) == "current"
    candidate_settings = settings.model_copy(
        update={"erasure_ledger_path": settings.data_dir / "candidate.jsonl"}
    )
    candidate = append_synthetic(candidate_settings, 1)
    assert JOURNALS.select_journal(candidate, settings) == "backup"
    assert not path.exists()


def test_divergent_valid_chains_are_never_merged(settings: Settings) -> None:
    append_synthetic(settings, 1)
    candidate_settings = settings.model_copy(
        update={"erasure_ledger_path": settings.data_dir / "candidate.jsonl"}
    )
    candidate = append_synthetic(candidate_settings, 2)
    with pytest.raises(ValueError, match="diverge"):
        JOURNALS.select_journal(candidate, settings)


@pytest.mark.parametrize("payload", [b"\x00", b"\xff", b"\n", b"{}", b"private-invalid-journal"])
def test_invalid_candidate_and_live_bytes_are_rejected(settings: Settings, payload: bytes) -> None:
    with pytest.raises((ErasureLedgerError, UnicodeDecodeError)):
        JOURNALS.verify_bytes(payload, settings)
    path = settings.resolved_erasure_ledger_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    with pytest.raises((ErasureLedgerError, UnicodeDecodeError)):
        JOURNALS.select_journal(b"", settings)


def test_wrong_authentication_key_is_rejected(settings: Settings) -> None:
    candidate = append_synthetic(settings, 1)
    wrong_key = settings.model_copy(
        update={"erasure_ledger_hmac_key": "different-synthetic-key-" + "0" * 32}
    )
    with pytest.raises(ErasureLedgerError):
        JOURNALS.verify_bytes(candidate, wrong_key)


@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "directory", "fifo"])
def test_nonregular_live_journal_is_never_treated_as_absent(
    settings: Settings, tmp_path: Path, kind: str
) -> None:
    path = settings.resolved_erasure_ledger_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        target = tmp_path / "link-target"
        if kind == "symlink":
            target.write_bytes(b"")
        path.symlink_to(target)
    with pytest.raises(ValueError, match="regular non-symlink"):
        JOURNALS.select_journal(b"", settings)


@pytest.mark.parametrize("operation", ["lstat", "open"])
def test_unreadable_live_journal_never_becomes_an_empty_chain(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    append_synthetic(settings, 1)
    path = settings.resolved_erasure_ledger_path
    original = getattr(JOURNALS.os, operation)

    def deny(target: object, *args: object, **kwargs: object) -> object:
        if target == path:
            raise PermissionError("private-path-and-journal-must-not-escape")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(JOURNALS.os, operation, deny)
    with pytest.raises(PermissionError):
        JOURNALS.select_journal(b"", settings)


def test_live_symlink_replacement_cannot_bypass_nofollow(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    append_synthetic(settings, 1)
    path = settings.resolved_erasure_ledger_path
    target = tmp_path / "replacement"
    target.write_bytes(b"")
    original_open = JOURNALS.os.open

    def replace_then_open(target_path: object, flags: int, *args: object, **kwargs: object) -> int:
        if target_path == path:
            assert flags & os.O_NOFOLLOW
            path.unlink()
            path.symlink_to(target)
        return original_open(target_path, flags, *args, **kwargs)

    monkeypatch.setattr(JOURNALS.os, "open", replace_then_open)
    with pytest.raises(OSError):
        JOURNALS.read_live_journal(path)


@pytest.mark.parametrize("phase", ["verify", "select"])
def test_cli_prints_only_fixed_contract(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
) -> None:
    candidate = append_synthetic(settings, 1)
    monkeypatch.setattr("sys.argv", ["-c", phase])
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(candidate)))
    JOURNALS.main()
    output = capsys.readouterr()
    assert output.out == ("current\n" if phase == "select" else "")
    assert output.err == ""


@pytest.mark.parametrize("failure", ["invalid", "unreadable", "invalid_argument"])
def test_cli_errors_never_print_journal_path_or_environment(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["-c", "private-argument" if failure == "invalid_argument" else "select"]
    )
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(b"private-invalid-content")))
    if failure == "unreadable":
        monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(b"")))

        def denied(_path: Path) -> bytes:
            raise PermissionError("private-journal-path-and-secret")

        monkeypatch.setattr(JOURNALS, "read_live_journal", denied)
    with pytest.raises(SystemExit) as error:
        JOURNALS.main()
    assert str(error.value) == JOURNALS.FAILURE_MESSAGE
    output = capsys.readouterr()
    assert output.out == "" and output.err == ""
