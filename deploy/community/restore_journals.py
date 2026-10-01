"""Authenticate restore journal bytes without privileged container filesystem access."""

from __future__ import annotations

import fcntl
import os
import stat
import sys
import tempfile
from pathlib import Path

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.ops.erasure_ledger import read_events

FAILURE_MESSAGE = "Erasure journal verification or selection failed; restore refused."


def verify_bytes(payload: bytes, settings: Settings) -> None:
    """Authenticate exactly these bytes with the application's existing HMAC parser."""
    # NamedTemporaryFile creates mode 0600. /tmp is the container's private
    # writable tmpfs; neither journal content nor credentials enter argv/logs.
    with tempfile.NamedTemporaryFile(prefix="six-restore-journal-", dir="/tmp") as candidate:
        candidate.write(payload)
        candidate.flush()
        copied = settings.model_copy(update={"erasure_ledger_path": Path(candidate.name)})
        read_events(copied)


def read_live_journal(path: Path) -> bytes:
    """Read a regular, non-symlink live journal; only true initial absence is empty."""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return b""
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Live erasure journal must be a regular non-symlink file.")
    # O_NONBLOCK also prevents a race replacing the checked file with a FIFO
    # from hanging the restore before fstat can reject it.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise ValueError("Live erasure journal changed during verification.")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def select_journal(candidate: bytes, settings: Settings) -> str:
    """Choose only an authenticated byte-prefix extension, never merge divergent chains."""
    verify_bytes(candidate, settings)
    current = read_live_journal(settings.resolved_erasure_ledger_path)
    # Authenticate the same immutable byte snapshot used for comparison, not a
    # second filesystem read that might observe different live content.
    verify_bytes(current, settings)
    if current == candidate or current.startswith(candidate):
        return "current"
    if candidate.startswith(current):
        return "backup"
    raise ValueError("Authenticated erasure journals diverge.")


def main() -> None:
    """Accept candidate bytes on stdin and emit only the fixed selection contract."""
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in {"verify", "select"}:
            raise ValueError("Expected verify or select.")
        candidate = sys.stdin.buffer.read()
        settings = get_settings()
        if sys.argv[1] == "verify":
            verify_bytes(candidate, settings)
        else:
            print(select_journal(candidate, settings))
    except Exception:
        # No traceback, raw journal, path, identity or environment-derived
        # exception text may be written into a public CI log.
        raise SystemExit(FAILURE_MESSAGE) from None


if __name__ == "__main__":
    main()
