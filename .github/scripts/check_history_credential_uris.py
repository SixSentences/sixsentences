#!/usr/bin/env python3
"""Reject credential-bearing URIs in every commit reachable from ``HEAD``."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Final

_COMMIT_PREFIX: Final = "__SIX_COMMIT__"
_HUNK_RE: Final = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<line>\d+)(?:,\d+)? @@")
_CREDENTIAL_URI_RE: Final = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://"
    r"(?P<username>[^\s/:@]+):(?P<password>[^\s/@]*)@"
    r"(?P<host>\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)",
    re.IGNORECASE,
)
_SYNTHETIC_USERNAMES: Final = frozenset({"example", "test", "user", "username"})
_SYNTHETIC_PASSWORDS: Final = frozenset(
    {"example", "pass", "password", "placeholder", "secret", "test"}
)
_RESERVED_HOSTS: Final = frozenset({"example.com", "example.net", "example.org"})
_RESERVED_SUFFIXES: Final = (".example", ".invalid", ".localhost", ".test")
# These two literals were introduced by this scanner's own synthetic unit tests
# before the location-scoped baseline existed. They are reserved-domain examples,
# not credentials. Keep exceptions bound to the immutable commit, path, and line.
_REVIEWED_HISTORY_FIXTURES: Final = frozenset(
    {
        (
            "1cdbf0f27c764379c81aeb7b3297f7bef9e7213d",
            "tests/test_repository_automation.py",
            304,
        ),
        (
            "1cdbf0f27c764379c81aeb7b3297f7bef9e7213d",
            "tests/test_repository_automation.py",
            317,
        ),
    }
)


@dataclass(frozen=True)
class Violation:
    """A credential URI location without retaining or printing its value."""

    commit: str
    path: str
    line: int

    def label(self) -> str:
        """Return a redacted, actionable source location."""

        return f"{self.commit[:12]}:{self.path}:{self.line}"


def _is_explicit_synthetic_fixture(username: str, password: str, host: str) -> bool:
    """Allow only obvious placeholders hosted by reserved example domains."""

    normalized_host = host.removeprefix("[").removesuffix("]").casefold()
    reserved_host = (
        normalized_host in _RESERVED_HOSTS
        or any(normalized_host.endswith(f".{host}") for host in _RESERVED_HOSTS)
        or normalized_host.endswith(_RESERVED_SUFFIXES)
    )
    return (
        reserved_host
        and username.casefold() in _SYNTHETIC_USERNAMES
        and password.casefold() in _SYNTHETIC_PASSWORDS
    )


def find_violations(patch: str) -> list[Violation]:
    """Return unsafe credential URI locations from a root-inclusive Git patch log."""

    commit = "unknown"
    path = "unknown"
    new_line: int | None = None
    violations: list[Violation] = []

    for raw_line in patch.splitlines():
        if raw_line.startswith(_COMMIT_PREFIX):
            commit = raw_line.removeprefix(_COMMIT_PREFIX).strip()
            new_line = None
            continue
        if raw_line.startswith("+++ "):
            candidate = raw_line[4:].strip()
            path = candidate[2:] if candidate.startswith("b/") else candidate
            new_line = None
            continue

        hunk = _HUNK_RE.match(raw_line)
        if hunk is not None:
            new_line = int(hunk.group("line"))
            continue
        if new_line is None or raw_line.startswith("\\ No newline"):
            continue
        if raw_line.startswith("-"):
            continue

        content = raw_line[1:] if raw_line.startswith("+") else raw_line
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            for match in _CREDENTIAL_URI_RE.finditer(content):
                if not _is_explicit_synthetic_fixture(
                    match.group("username"), match.group("password"), match.group("host")
                ):
                    violations.append(Violation(commit=commit, path=path, line=new_line))
        new_line += 1

    return violations


def unreviewed_violations(violations: list[Violation]) -> list[Violation]:
    """Remove only immutable, location-bound synthetic history fixtures."""

    return [
        violation
        for violation in violations
        if (violation.commit, violation.path, violation.line) not in _REVIEWED_HISTORY_FIXTURES
    ]


def _history_patch() -> str:
    """Return every added source line in the complete history reachable from ``HEAD``."""

    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if shallow != "false":
        raise RuntimeError("credential URI history scan requires a complete checkout")

    return subprocess.run(
        [
            "git",
            "log",
            "--root",
            f"--format={_COMMIT_PREFIX}%H",
            "--patch",
            "--unified=0",
            "--no-ext-diff",
            "--no-renames",
            "--no-color",
            "HEAD",
            "--",
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def main() -> int:
    """Scan reachable history and emit only redacted locations for violations."""

    try:
        violations = unreviewed_violations(find_violations(_history_patch()))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Credential URI history scan failed safely: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("Credential-bearing URIs outside the synthetic allowlist were found:")
        for violation in violations:
            print(f"- {violation.label()}")
        return 1

    print("Credential URI history scan passed for every commit reachable from HEAD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
