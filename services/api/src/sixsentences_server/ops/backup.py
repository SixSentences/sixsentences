"""Consistent SQLite snapshots and restore verification.

Copying a live SQLite file is unsafe when WAL contains committed pages. This
module uses SQLite's online backup API, verifies the resulting database, and
atomically publishes only a valid snapshot for the encrypted offsite backup.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class BackupIntegrityError(RuntimeError):
    """A snapshot failed SQLite's own integrity verification."""


def verify_database(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            detail = str(result[0] if result else "no result")
            raise BackupIntegrityError(f"SQLite integrity check failed: {detail}")
        pages = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "pages": pages,
        "page_size": page_size,
        "integrity": "ok",
    }


def snapshot_database(source: Path, target: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.partial")
    temporary.unlink(missing_ok=True)
    try:
        with (
            sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as source_connection,
            sqlite3.connect(temporary, timeout=30) as target_connection,
        ):
            source_connection.backup(target_connection, pages=1024, sleep=0.05)
            target_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        report = verify_database(temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {**report, "path": str(target)}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SixSentences backup tooling")
    subcommands = parser.add_subparsers(dest="command", required=True)
    snapshot = subcommands.add_parser("snapshot")
    snapshot.add_argument("source", type=Path)
    snapshot.add_argument("target", type=Path)
    verify = subcommands.add_parser("verify")
    verify.add_argument("path", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "snapshot":
        report = snapshot_database(args.source, args.target)
    else:
        report = verify_database(args.path)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
