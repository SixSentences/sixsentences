#!/usr/bin/env python3
"""Publish a narrowly validated GitHub commit status."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Final

_OBJECT_ID_RE: Final = re.compile(r"^[0-9a-fA-F]{40}$")
_REPOSITORY_RE: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def publish_status(
    api_url: str,
    *,
    token: str,
    repository: str,
    sha: str,
    context: str,
    state: str,
    description: str,
    target_url: str,
) -> None:
    """Publish one status after validating every URL-forming input."""

    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise ValueError("repository must be an owner/name pair")
    if _OBJECT_ID_RE.fullmatch(sha) is None:
        raise ValueError("sha must be a full Git object id")
    if state not in {"success", "failure"}:
        raise ValueError("state must be success or failure")
    if not context or len(context) > 100:
        raise ValueError("context must contain 1 to 100 characters")
    if not description or len(description) > 140:
        raise ValueError("description must contain 1 to 140 characters")
    if urllib.parse.urlsplit(target_url).scheme != "https":
        raise ValueError("target URL must use HTTPS")

    endpoint = f"{api_url.rstrip('/')}/repos/{repository}/statuses/{sha.lower()}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(
            {
                "state": state,
                "context": context,
                "description": description,
                "target_url": target_url,
            }
        ).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 201:
                raise ValueError(f"GitHub status API returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"GitHub status API returned HTTP {exc.code}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--state", required=True, choices=("success", "failure"))
    parser.add_argument("--success-description", required=True)
    parser.add_argument("--failure-description", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--api-url", default="https://api.github.com")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Publish the requested status and return a conventional exit code."""

    args = _parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("Status publication failed: GITHUB_TOKEN is missing.", file=sys.stderr)
        return 2
    description = args.success_description if args.state == "success" else args.failure_description
    try:
        publish_status(
            args.api_url,
            token=token,
            repository=args.repository,
            sha=args.sha,
            context=args.context,
            state=args.state,
            description=description,
            target_url=args.target_url,
        )
    except (OSError, ValueError) as exc:
        print(f"Status publication failed: {exc}", file=sys.stderr)
        return 2
    print(f"Published {args.context!r}={args.state} for {args.sha}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
