"""Portable SQLite backup helpers for community operators."""

import sqlite3
from pathlib import Path

import pytest

from sixsentences_server.ops.backup import (
    BackupIntegrityError,
    snapshot_database,
    verify_database,
)


def test_snapshot_includes_committed_wal_rows(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    target = tmp_path / "snapshot" / "six.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO records (value) VALUES ('kept')")
        connection.commit()
        report = snapshot_database(source, target)

    assert report["integrity"] == "ok"
    with sqlite3.connect(target) as restored:
        assert restored.execute("SELECT value FROM records").fetchone() == ("kept",)


def test_verify_rejects_corrupt_database(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a sqlite database")

    with pytest.raises((BackupIntegrityError, sqlite3.DatabaseError)):
        verify_database(corrupt)
