#!/usr/bin/env python3
"""Verify a pull-request author's public acceptance of the project CLA."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Final

CLA_VERSION: Final = "1.0"
ACCEPTANCE: Final = f"I have read and agree to the SixSentences CLA v{CLA_VERSION}."
STATUS_CONTEXT: Final = "CLA / acceptance"
EXEMPT_AUTHORS: Final = frozenset({"dependabot[bot]"})


def _request(url: str, *, token: str, method: str = "GET", payload: object | None = None) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        path = urllib.parse.urlsplit(url).path
        raise ValueError(f"GitHub API {method} {path} returned HTTP {exc.code}") from exc


def _comments(api_url: str, *, token: str, repository: str, number: int) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    page = 1
    while True:
        payload = _request(
            f"{api_url}/repos/{repository}/issues/{number}/comments?per_page=100&page={page}",
            token=token,
        )
        if not isinstance(payload, list):
            raise ValueError("GitHub comments response was not a list")
        typed = [item for item in payload if isinstance(item, dict)]
        result.extend(typed)
        if len(payload) < 100:
            return result
        page += 1


def _pull_request_identity(
    api_url: str, *, token: str, repository: str, number: int
) -> tuple[str, str]:
    payload = _request(f"{api_url}/repos/{repository}/pulls/{number}", token=token)
    if not isinstance(payload, dict):
        raise ValueError("GitHub pull-request response was not an object")
    user = payload.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if not isinstance(login, str) or not login:
        raise ValueError("pull request has no author login")
    head = payload.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or len(sha) != 40:
        raise ValueError("pull request has no valid head SHA")
    return login, sha


def _accepted(comments: list[dict[str, object]], *, author: str) -> bool:
    for comment in comments:
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        body = comment.get("body")
        if login == author and isinstance(body, str) and body.strip() == ACCEPTANCE:
            return True
    return False


def _set_status(
    api_url: str,
    *,
    token: str,
    repository: str,
    sha: str,
    cla_sha: str,
    state: str,
) -> None:
    descriptions = {
        "pending": f"Checking the PR author's CLA v{CLA_VERSION} acceptance.",
        "success": f"Accepted CLA v{CLA_VERSION} by the pull-request author.",
        "failure": f"PR author must post the exact CLA v{CLA_VERSION} acceptance comment.",
    }
    if state not in descriptions:
        raise ValueError(f"unsupported CLA status state: {state}")
    if len(cla_sha) != 40 or any(character not in "0123456789abcdef" for character in cla_sha):
        raise ValueError("CLA source SHA must be a lowercase full Git object id")
    _request(
        f"{api_url}/repos/{repository}/statuses/{sha}",
        token=token,
        method="POST",
        payload={
            "state": state,
            "context": STATUS_CONTEXT,
            "description": descriptions[state],
            "target_url": f"https://github.com/{repository}/blob/{cla_sha}/CLA.md",
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--cla-sha", required=True)
    parser.add_argument("--api-url", default="https://api.github.com")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Publish the CLA result against the exact pull-request head commit."""

    args = _parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("CLA check failed: GITHUB_TOKEN is missing.", file=sys.stderr)
        return 2
    try:
        author, head_sha = _pull_request_identity(
            args.api_url, token=token, repository=args.repository, number=args.number
        )
        _set_status(
            args.api_url,
            token=token,
            repository=args.repository,
            sha=head_sha,
            cla_sha=args.cla_sha,
            state="pending",
        )
        comments = _comments(
            args.api_url, token=token, repository=args.repository, number=args.number
        )
        exempt = author in EXEMPT_AUTHORS
        accepted = exempt or _accepted(comments, author=author)
        _set_status(
            args.api_url,
            token=token,
            repository=args.repository,
            sha=head_sha,
            cla_sha=args.cla_sha,
            state="success" if accepted else "failure",
        )
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        print(f"CLA check could not run: {exc}", file=sys.stderr)
        return 2
    if exempt:
        print(f"CLA exemption verified for trusted automation author @{author}.")
        return 0
    if accepted:
        print(f"CLA acceptance verified for @{author} on pull request #{args.number}.")
        return 0
    print(
        f"CLA acceptance is missing: @{author} must post the exact comment from CLA.md.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
