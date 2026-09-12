"""Fail-closed working-tree checks for the public community service."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tomllib
from pathlib import Path

MAX_FILE_BYTES = 4_000_000
MAX_TOTAL_BYTES = 25_000_000
SENSITIVE_SUFFIXES = {
    ".7z",
    ".bak",
    ".db",
    ".duckdb",
    ".gz",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".zip",
}
SENSITIVE_NAMES = {
    ".env",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_ed25519",
}
IGNORED_DIRECTORIES = {".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}
RUNTIME_DENY = re.compile(
    r"\b(?:"
    r"str" + r"ipe|"
    r"billing[_-]?(?:portal|account|customer|invoice)|"
    r"checkout[_-]?(?:session|flow)|"
    r"subscription[_-]?(?:plan|status|tier)|"
    r"price[_-]?(?:catalog|table)|"
    r"top[_-]?up|"
    r"waitlist|support[_-]?ticket|hosted[_-]?operator"
    r")\b",
    re.IGNORECASE,
)
SECRET_PATTERNS = {
    "cloud access key": re.compile(r"(?:AK" + r"IA|ASIA)[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"gh" + r"[pousr]_[0-9A-Za-z]{20,}"),
    "fine-grained GitHub token": re.compile(r"github_" + r"pat_[0-9A-Za-z_]{20,}"),
    "Slack token": re.compile(r"xo" + r"x[baprs]-[0-9A-Za-z-]{20,}"),
    "npm token": re.compile(r"npm" + r"_[0-9A-Za-z]{30,}"),
    "JWT": re.compile(r"eyJ[0-9A-Za-z_-]{12,}\.[0-9A-Za-z_-]{12,}\.[0-9A-Za-z_-]{8,}"),
    "private key": re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
}
ASSIGNED_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|client[_-]?secret|password|private[_-]?key|access[_-]?token)"
    r"\s*(?::|=)\s*['\"][^'\"\r\n]{12,}['\"]"
)
PRODUCTION_REFERENCE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)*sixsentences" + r"\.com|"
    r"\b(?:back" + r"blaze|het" + r"zner|io" + r"nos|um" + r"ami)\b|"
    "/" + r"Users/[^/\s]+/|[A-Za-z]:\\" + r"Users\\[^\\\s]+\\",
    re.IGNORECASE,
)


def _files(root: Path) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name not in IGNORED_DIRECTORIES]
        current = Path(directory)
        for name in names:
            candidate = current / name
            if candidate.is_symlink():
                errors.append(f"symlink directory: {candidate.relative_to(root)}")
        for name in filenames:
            candidate = current / name
            relative = candidate.relative_to(root)
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                errors.append(f"symlink: {relative}")
            elif not stat.S_ISREG(mode):
                errors.append(f"special file: {relative}")
            else:
                files.append(candidate)
    return files, errors


def _verify_pins(root: Path, errors: list[str]) -> None:
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        groups = [
            project["project"]["dependencies"],
            project["dependency-groups"]["dev"],
            project["build-system"]["requires"],
        ]
    except (KeyError, OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"cannot parse dependency metadata: {exc}")
        return
    for group in groups:
        for dependency in group:
            if dependency.count("==") != 1:
                errors.append(f"direct dependency is not exactly pinned: {dependency}")


def audit(root: Path) -> list[str]:
    """Return every boundary violation found below ``root``."""

    files, errors = _files(root)
    total = 0
    for path in files:
        relative = path.relative_to(root)
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES:
            errors.append(f"oversized file: {relative} ({size} bytes)")
        if path.name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            errors.append(f"sensitive/generated file: {relative}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"non-UTF-8 or binary file: {relative}")
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"{label} signature: {relative}")
        if ASSIGNED_SECRET.search(content):
            errors.append(f"assigned secret-like literal: {relative}")
        if path.name != "uv.lock" and PRODUCTION_REFERENCE.search(content):
            errors.append(f"production/private reference: {relative}")
        if relative.parts[:2] == ("src", "sixsentences_server") and RUNTIME_DENY.search(content):
            errors.append(f"commercial/hosted runtime term: {relative}")
        if relative.parts[:2] == ("src", "sixsentences_server") and any(
            part.casefold()
            in {"admin", "billing", "commercial", "operator", "support", "waitlist"}
            for part in relative.parts[2:]
        ):
            errors.append(f"forbidden runtime path: {relative}")
    if total > MAX_TOTAL_BYTES:
        errors.append(f"service tree exceeds {MAX_TOTAL_BYTES} bytes")
    _verify_pins(root, errors)
    return sorted(set(errors))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    errors = audit(args.path.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("Community API boundary audit passed.")


if __name__ == "__main__":
    main()
