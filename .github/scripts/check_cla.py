#!/usr/bin/env python3
"""Verify a pull-request author's public acceptance of the project CLA."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Final

ACCEPTANCE: Final = "I have read and agree to the SixSentences CLA."
INSTRUCTION_MARKER: Final = "<!-- sixsentences-cla-instructions -->"


def _request(url: str, *, token: str, method: str = "GET", payload: object | None = None) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


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


def _author_login(api_url: str, *, token: str, repository: str, number: int) -> str:
    payload = _request(f"{api_url}/repos/{repository}/pulls/{number}", token=token)
    if not isinstance(payload, dict):
        raise ValueError("GitHub pull-request response was not an object")
    user = payload.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if not isinstance(login, str) or not login:
        raise ValueError("pull request has no author login")
    return login


def _accepted(comments: list[dict[str, object]], *, author: str) -> bool:
    for comment in comments:
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        body = comment.get("body")
        if login == author and isinstance(body, str) and body.strip() == ACCEPTANCE:
            return True
    return False


def _has_instruction(comments: list[dict[str, object]]) -> bool:
    return any(
        isinstance(comment.get("body"), str) and INSTRUCTION_MARKER in str(comment["body"])
        for comment in comments
    )


def _post_instruction(api_url: str, *, token: str, repository: str, number: int) -> None:
    body = (
        f"{INSTRUCTION_MARKER}\n"
        "This project requires both DCO sign-off on every commit and acceptance "
        f"of [CLA.md](https://github.com/{repository}/blob/main/CLA.md). The "
        "pull-request author can accept by "
        "posting this exact standalone comment:\n\n"
        f"```text\n{ACCEPTANCE}\n```"
    )
    _request(
        f"{api_url}/repos/{repository}/issues/{number}/comments",
        token=token,
        method="POST",
        payload={"body": body},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--api-url", default="https://api.github.com")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Check CLA acceptance and leave one instruction when it is missing."""

    args = _parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("CLA check failed: GITHUB_TOKEN is missing.", file=sys.stderr)
        return 2
    try:
        author = _author_login(
            args.api_url, token=token, repository=args.repository, number=args.number
        )
        comments = _comments(
            args.api_url, token=token, repository=args.repository, number=args.number
        )
        if _accepted(comments, author=author):
            print(f"CLA acceptance verified for @{author} on pull request #{args.number}.")
            return 0
        if not _has_instruction(comments):
            _post_instruction(
                args.api_url,
                token=token,
                repository=args.repository,
                number=args.number,
            )
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        print(f"CLA check could not run: {exc}", file=sys.stderr)
        return 2
    print(
        f"CLA acceptance is missing: @{author} must post the exact comment from CLA.md.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
