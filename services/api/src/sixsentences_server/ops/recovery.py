"""Fail-closed helpers for selecting post-snapshot privacy state."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path


class RecoveryStateError(RuntimeError):
    """Recovery inputs cannot be reconciled without risking resurrected data."""


def read_regular_file(path: Path) -> bytes:
    """Read one regular file without following a final-component symlink."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return b""
    if not stat.S_ISREG(metadata.st_mode):
        raise RecoveryStateError("erasure journal is not a regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RecoveryStateError("erasure journal could not be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RecoveryStateError("erasure journal changed type while opening")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def select_erasure_journal(current_path: Path, snapshot_path: Path) -> str:
    """Choose the longer compatible journal after both chains were authenticated.

    The caller must run the HMAC-chain verifier for both inputs before invoking
    this comparison. A journal may only extend the other byte-for-byte; divergent
    histories fail closed rather than being guessed or merged.
    """

    current = read_regular_file(current_path)
    snapshot = read_regular_file(snapshot_path)
    if current == snapshot or current.startswith(snapshot):
        return "current"
    if snapshot.startswith(current):
        return "snapshot"
    raise RecoveryStateError("authenticated erasure journals diverge")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select compatible erasure recovery state")
    parser.add_argument("current", type=Path)
    parser.add_argument("snapshot", type=Path)
    return parser


def main() -> None:
    """Print only the selected source label; never journal bytes or paths."""

    args = _parser().parse_args()
    try:
        selected = select_erasure_journal(args.current, args.snapshot)
    except RecoveryStateError as exc:
        raise SystemExit(str(exc)) from exc
    print(selected)


if __name__ == "__main__":
    main()
