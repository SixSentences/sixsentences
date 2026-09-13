#!/usr/bin/env python3
"""Validate existing DCO trailers without creating legal attestations."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from typing import Final

_IDENTITY_RE: Final = re.compile(r"^(?P<name>[^<>\r\n]+?)\s+<(?P<email>[^<>\s@]+@[^<>\s@]+)>$")
_OBJECT_ID_RE: Final = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_GITHUB_NOREPLY_RE: Final = re.compile(
    r"^(?:[0-9]+\+)?(?P<login>[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?)"
    r"@users\.noreply\.github\.com$"
)
_DEPENDABOT_PR_AUTHOR: Final = "dependabot[bot]"
_DEPENDABOT_FOOTER: Final = "Signed-off-by: dependabot[bot] <support@github.com>"


@dataclass(frozen=True)
class Identity:
    """Normalized public identity from Git metadata or an identity trailer."""

    name: str
    email: str

    @classmethod
    def parse(cls, value: str) -> Identity:
        """Parse and normalize ``Name <email>`` without accepting multiline data."""

        match = _IDENTITY_RE.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"invalid identity trailer: {value!r}")
        name = " ".join(unicodedata.normalize("NFKC", match.group("name")).split()).casefold()
        email = unicodedata.normalize("NFKC", match.group("email")).casefold()
        return cls(name=name, email=email)

    def label(self) -> str:
        """Return a stable diagnostic label."""

        return f"{self.name} <{self.email}>"

    def matches(self, other: Identity) -> bool:
        """Match an exact identity or two official no-reply aliases of one GitHub login."""

        if self == other:
            return True
        own_login = _github_noreply_login(self.email)
        other_login = _github_noreply_login(other.email)
        return own_login is not None and own_login == other_login


@dataclass(frozen=True)
class IdentityTrailers:
    """Identity-bearing trailers parsed from Git's canonical trailer block."""

    signoffs: tuple[Identity, ...]
    coauthors: tuple[Identity, ...]


_DEPENDABOT_AUTHORS: Final = frozenset(
    {
        Identity.parse("dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"),
        Identity.parse("dependabot[bot] <support@github.com>"),
    }
)
_DEPENDABOT_SIGNOFF: Final = Identity.parse("dependabot[bot] <support@github.com>")


def _github_noreply_login(email: str) -> str | None:
    """Return the login encoded in an official GitHub no-reply address."""

    match = _GITHUB_NOREPLY_RE.fullmatch(email)
    return match.group("login") if match is not None else None


def _contains_identity(identities: set[Identity], expected: Identity) -> bool:
    """Return whether a sign-off represents the expected public identity."""

    return any(expected.matches(candidate) for candidate in identities)


def parse_trailer_output(output: str) -> IdentityTrailers:
    """Parse identity trailers emitted by ``git interpret-trailers --parse``."""

    signoffs: list[Identity] = []
    coauthors: list[Identity] = []
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        normalized_key = key.strip().casefold()
        if normalized_key not in {"signed-off-by", "co-authored-by"}:
            continue
        if not separator:
            raise ValueError(f"malformed {key.strip()} trailer")
        identity = Identity.parse(value)
        if normalized_key == "signed-off-by":
            signoffs.append(identity)
        else:
            coauthors.append(identity)
    return IdentityTrailers(tuple(signoffs), tuple(coauthors))


def _has_exact_final_dependabot_footer(message: str) -> bool:
    """Return whether the last non-empty line is GitHub's canonical bot footer."""

    non_empty_lines = [line for line in message.splitlines() if line.strip()]
    return bool(non_empty_lines) and non_empty_lines[-1] == _DEPENDABOT_FOOTER


def interpret_identity_trailers(
    message: str,
    *,
    author: Identity | None = None,
    pull_request_author: str = "",
) -> IdentityTrailers:
    """Ask Git to isolate the real footer before parsing identity trailers.

    Dependabot appends a YAML metadata block to some update messages. Git then
    declines to treat the final sign-off as a trailer. The narrow fallback below
    is available only for GitHub's known bot identity in a Dependabot-owned pull
    request, and only for the exact canonical footer as the last non-empty line.
    """

    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=message,
        check=True,
        capture_output=True,
        text=True,
    )
    trailers = parse_trailer_output(result.stdout)
    if (
        pull_request_author == _DEPENDABOT_PR_AUTHOR
        and author in _DEPENDABOT_AUTHORS
        and _DEPENDABOT_SIGNOFF not in trailers.signoffs
        and _has_exact_final_dependabot_footer(message)
    ):
        return IdentityTrailers(
            signoffs=(*trailers.signoffs, _DEPENDABOT_SIGNOFF),
            coauthors=trailers.coauthors,
        )
    return trailers


def validate_identities(
    author: Identity,
    trailers: IdentityTrailers,
    *,
    pull_request_author: str = "",
) -> list[str]:
    """Return every DCO identity mismatch for one commit.

    Dependabot cannot make a human legal attestation. Its known GitHub-generated
    identity pair is accepted only when GitHub says the pull request itself is
    owned by ``dependabot[bot]``. Human commits always require an exact normalized
    author sign-off, and every declared co-author requires their own exact sign-off.
    """

    errors: list[str] = []
    available = set(trailers.signoffs)
    if pull_request_author == _DEPENDABOT_PR_AUTHOR:
        if author not in _DEPENDABOT_AUTHORS:
            errors.append(
                "Dependabot-owned pull requests may contain only Dependabot-authored commits"
            )
        elif _DEPENDABOT_SIGNOFF not in available:
            errors.append("trusted Dependabot commit is missing its GitHub bot sign-off")
    elif not _contains_identity(available, author):
        errors.append(f"commit author is not signed off: {author.label()}")

    for coauthor in dict.fromkeys(trailers.coauthors):
        if not _contains_identity(available, coauthor):
            errors.append(f"co-author is not signed off: {coauthor.label()}")
    return errors


def _git(*arguments: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        input=input_text,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _validate_object_id(value: str, *, name: str) -> str:
    if _OBJECT_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full Git object id")
    return value.lower()


def commits_in_range(base: str, head: str) -> list[str]:
    """Return contribution commits in deterministic oldest-first order."""

    base_id = _validate_object_id(base, name="base")
    head_id = _validate_object_id(head, name="head")
    commits = _git("rev-list", "--reverse", f"{base_id}..{head_id}").splitlines()
    if not commits:
        raise ValueError("pull request contains no commits to validate")
    return commits


def validate_commit(commit: str, *, pull_request_author: str = "") -> list[str]:
    """Validate the author and co-author sign-offs of one existing commit."""

    commit_id = _validate_object_id(commit, name="commit")
    raw = _git("show", "--no-patch", "--format=%an%x00%ae%x00%B", commit_id)
    parts = raw.split("\x00", maxsplit=2)
    if len(parts) != 3:
        raise ValueError(f"could not read author and message for {commit_id}")
    author_name, author_email, message = parts
    author = Identity.parse(f"{author_name} <{author_email}>")
    trailers = interpret_identity_trailers(
        message,
        author=author,
        pull_request_author=pull_request_author,
    )
    return validate_identities(
        author,
        trailers,
        pull_request_author=pull_request_author,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="full base commit object id")
    parser.add_argument("--head", required=True, help="full head commit object id")
    parser.add_argument(
        "--pull-request-author",
        default="",
        help="GitHub login that owns the pull request; used only for Dependabot validation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate a pull-request commit range and return a conventional exit code."""

    args = _parser().parse_args(argv)
    try:
        commits = commits_in_range(args.base, args.head)
        failures: list[str] = []
        for commit in commits:
            failures.extend(
                f"{commit}: {error}"
                for error in validate_commit(
                    commit,
                    pull_request_author=args.pull_request_author,
                )
            )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"DCO validation could not run: {exc}", file=sys.stderr)
        return 2

    if failures:
        for failure in failures:
            print(f"DCO validation failed: {failure}", file=sys.stderr)
        return 1
    print(f"DCO sign-offs validated for {len(commits)} commit(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
