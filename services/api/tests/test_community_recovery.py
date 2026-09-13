"""Executable recovery guards for community backup restoration."""

from pathlib import Path

import pytest

from sixsentences_server.ops.recovery import RecoveryStateError, select_erasure_journal


def test_recovery_selects_the_longer_compatible_erasure_journal(tmp_path: Path) -> None:
    current = tmp_path / "current.jsonl"
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_bytes(b"event-1\n")
    current.write_bytes(b"event-1\nevent-2\n")

    assert select_erasure_journal(current, snapshot) == "current"
    assert select_erasure_journal(snapshot, current) == "snapshot"


def test_recovery_refuses_divergent_or_non_regular_journals(tmp_path: Path) -> None:
    current = tmp_path / "current.jsonl"
    snapshot = tmp_path / "snapshot.jsonl"
    current.write_bytes(b"event-a\n")
    snapshot.write_bytes(b"event-b\n")

    with pytest.raises(RecoveryStateError, match="diverge"):
        select_erasure_journal(current, snapshot)

    current.unlink()
    current.mkdir()
    with pytest.raises(RecoveryStateError, match="regular file"):
        select_erasure_journal(current, snapshot)


def test_recovery_treats_an_absent_journal_as_the_empty_chain(tmp_path: Path) -> None:
    current = tmp_path / "missing-current.jsonl"
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_bytes(b"event-1\n")

    assert select_erasure_journal(current, snapshot) == "snapshot"
