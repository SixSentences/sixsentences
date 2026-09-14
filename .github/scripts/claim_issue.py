#!/usr/bin/env python3
"""Let a contributor claim an issue with a comment, and let a claim expire.

Two people starting the same issue is the most expensive kind of duplicated
work in an open project: both finish, one pull request is closed unmerged. A
comment saying "I'll take this" only helps someone who reads the whole thread,
so this records the claim where every list view shows it — as an assignee and
the `claimed` label.

A claim that never expires is worse than no claim at all, because the issue
looks taken forever. `--sweep` releases claims that have gone quiet, and says so
in the thread rather than silently.

The comment body is untrusted input. It arrives through the environment, is
never interpolated into a shell command, and is only ever compared against a
fixed set of commands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

CLAIM_LABEL: Final = "claimed"
CLAIM_COMMAND: Final = "/claim"
RELEASE_COMMAND: Final = "/unclaim"
# One person holding every open issue helps nobody. Three is enough to work on
# something while a review is pending without parking the backlog.
MAX_OPEN_CLAIMS: Final = 3
# Long enough to cover a fortnight of not touching a side project, short enough
# that an abandoned claim does not outlast anyone's interest in the issue.
STALE_AFTER: Final = timedelta(days=14)
MAINTAINER_ASSOCIATIONS: Final = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

Action = Literal["claim", "release", "reply", "none"]


@dataclass(frozen=True)
class Decision:
    """What the command asks for, and what to say about it."""

    action: Action
    message: str = ""


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
            if response.status == 204 or not response.length:
                return None
            return json.load(response)
    except urllib.error.HTTPError as exc:
        path = urllib.parse.urlsplit(url).path
        raise ValueError(f"GitHub API {method} {path} returned HTTP {exc.code}") from exc


def parse_command(body: str) -> str | None:
    """Return the command a comment is, or None when it is an ordinary comment.

    The command has to be the whole comment. Someone writing "I think /claim is
    the wrong approach here" is discussing the feature, not using it.
    """

    stripped = body.strip().lower()
    if stripped in (CLAIM_COMMAND, RELEASE_COMMAND):
        return stripped
    return None


def assignee_logins(issue: dict[str, object]) -> list[str]:
    """Return the logins currently assigned to an issue."""

    assignees = issue.get("assignees")
    if not isinstance(assignees, list):
        return []
    return [
        str(entry["login"])
        for entry in assignees
        if isinstance(entry, dict) and isinstance(entry.get("login"), str)
    ]


def claim_decision(
    issue: dict[str, object],
    *,
    actor: str,
    open_claims: list[int],
    docs_url: str,
) -> Decision:
    """Decide what `/claim` should do, without touching the network."""

    if issue.get("state") != "open":
        return Decision(
            "reply",
            "This issue is closed, so there is nothing to claim. If it should be "
            "reopened, say why and a maintainer will take a look.",
        )

    holders = assignee_logins(issue)
    if actor in holders:
        return Decision(
            "reply",
            f"@{actor} already holds this one — it is yours, no need to claim it twice.",
        )
    if holders:
        held_by = ", ".join(f"@{login}" for login in holders)
        return Decision(
            "reply",
            f"This issue is already claimed by {held_by}. A claim is released "
            f"automatically after {STALE_AFTER.days} days without activity, and "
            f"the holder can release it earlier with `{RELEASE_COMMAND}`. Until "
            "then, please pick another issue rather than duplicating the work — "
            "or say here what you would do differently, since two approaches are "
            "worth discussing even when one person is implementing.",
        )

    outstanding = [number for number in open_claims if number != issue.get("number")]
    if len(outstanding) >= MAX_OPEN_CLAIMS:
        held = ", ".join(f"#{number}" for number in sorted(outstanding))
        return Decision(
            "reply",
            f"@{actor} already holds {len(outstanding)} open claims ({held}), "
            f"which is the limit of {MAX_OPEN_CLAIMS}. Finish or release one with "
            f"`{RELEASE_COMMAND}` and claim this afterwards. The limit exists so "
            "the backlog stays readable, not to rush anyone.",
        )

    return Decision("claim", claim_granted_body(actor, docs_url=docs_url))


def release_decision(issue: dict[str, object], *, actor: str, is_maintainer: bool) -> Decision:
    """Decide what `/unclaim` should do, without touching the network."""

    holders = assignee_logins(issue)
    if not holders:
        return Decision("reply", "Nobody holds this issue, so there is nothing to release.")
    if actor in holders or is_maintainer:
        released = ", ".join(f"@{login}" for login in holders)
        note = (
            f"Released by @{actor}."
            if actor in holders
            else f"Released by @{actor}, who maintains this repository."
        )
        return Decision(
            "release",
            f"{note} This issue is open for anyone again — {released} is no longer "
            "assigned. Thanks for saying so instead of letting it sit.",
        )
    held_by = ", ".join(f"@{login}" for login in holders)
    return Decision(
        "reply",
        f"Only {held_by} or a maintainer can release this claim. If it looks "
        f"abandoned, it is released automatically after {STALE_AFTER.days} days "
        "without activity.",
    )


def claim_granted_body(actor: str, *, docs_url: str) -> str:
    """Return the reply that a newcomer actually needs after claiming."""

    return f"""Assigned to @{actor} — this issue is yours.

Three gates apply to every contribution here, and none of them can be completed
for you. Reading them before you write code saves a round trip:

1. **Sign off every commit.** `git commit -s` adds the `Signed-off-by` trailer
   the DCO check requires.
2. **Sign your commits with your own key.** See the setup block in
   [`CONTRIBUTING.md`]({docs_url}).
3. **Accept the CLA** by posting the exact sentence from `CLA.md` as a
   standalone comment on your pull request. A bot will remind you there.

Branch from `main` and open the pull request back to `main`. Reference this
issue in the description so the two are linked.

A claim expires after {STALE_AFTER.days} days without activity, so the issue
does not stay parked if life gets in the way — commenting here resets that
clock. If you decide not to continue, `{RELEASE_COMMAND}` frees it immediately
and costs you nothing. Questions on the issue are welcome and count as activity.
"""


def stale_claim_body(holder: str) -> str:
    """Return the note left when a quiet claim is released."""

    return f"""This claim is released: there has been no activity for
{STALE_AFTER.days} days. @{holder}, this is not a complaint — if you are still
on it, comment `{CLAIM_COMMAND}` and take it straight back.

The issue is open for anyone else in the meantime.
"""


def stale_claims(
    issues: list[dict[str, object]], *, now: datetime, max_age: timedelta = STALE_AFTER
) -> list[tuple[int, str]]:
    """Return the (number, holder) pairs whose claim has gone quiet.

    `updated_at` moves on comments, label and assignment changes, and on the
    cross-reference a pull request creates when it names the issue, so ordinary
    progress keeps a claim alive without anyone having to report in.
    """

    cutoff = now - max_age
    result: list[tuple[int, str]] = []
    for issue in issues:
        if "pull_request" in issue or issue.get("state") != "open":
            continue
        number = issue.get("number")
        updated = issue.get("updated_at")
        if not isinstance(number, int) or not isinstance(updated, str):
            continue
        try:
            touched = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            continue
        if touched >= cutoff:
            continue
        result.extend((number, holder) for holder in assignee_logins(issue))
    return result


def _issue(api_url: str, *, token: str, repository: str, number: int) -> dict[str, object]:
    payload = _request(f"{api_url}/repos/{repository}/issues/{number}", token=token)
    if not isinstance(payload, dict):
        raise ValueError("GitHub issue response was not an object")
    return payload


def _open_claims(api_url: str, *, token: str, repository: str, actor: str) -> list[int]:
    query = urllib.parse.urlencode(
        {"state": "open", "assignee": actor, "labels": CLAIM_LABEL, "per_page": 100}
    )
    payload = _request(f"{api_url}/repos/{repository}/issues?{query}", token=token)
    if not isinstance(payload, list):
        raise ValueError("GitHub issue list response was not a list")
    return [
        item["number"]
        for item in payload
        if isinstance(item, dict)
        and isinstance(item.get("number"), int)
        and "pull_request" not in item
    ]


def _claimed_issues(api_url: str, *, token: str, repository: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {"state": "open", "labels": CLAIM_LABEL, "per_page": 100, "page": page}
        )
        payload = _request(f"{api_url}/repos/{repository}/issues?{query}", token=token)
        if not isinstance(payload, list):
            raise ValueError("GitHub issue list response was not a list")
        result.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            return result
        page += 1


def _comment(api_url: str, *, token: str, repository: str, number: int, body: str) -> None:
    _request(
        f"{api_url}/repos/{repository}/issues/{number}/comments",
        token=token,
        method="POST",
        payload={"body": body},
    )


def _assign(api_url: str, *, token: str, repository: str, number: int, actor: str) -> bool:
    """Assign and report whether it took effect.

    GitHub drops an assignee it does not consider assignable and still answers
    with a success status, so the result is read back rather than assumed.
    """

    _request(
        f"{api_url}/repos/{repository}/issues/{number}/assignees",
        token=token,
        method="POST",
        payload={"assignees": [actor]},
    )
    issue = _issue(api_url, token=token, repository=repository, number=number)
    return actor in assignee_logins(issue)


def _unassign(api_url: str, *, token: str, repository: str, number: int, logins: list[str]) -> None:
    if not logins:
        return
    _request(
        f"{api_url}/repos/{repository}/issues/{number}/assignees",
        token=token,
        method="DELETE",
        payload={"assignees": logins},
    )


def _add_label(api_url: str, *, token: str, repository: str, number: int) -> None:
    _request(
        f"{api_url}/repos/{repository}/issues/{number}/labels",
        token=token,
        method="POST",
        payload={"labels": [CLAIM_LABEL]},
    )


def _remove_label(api_url: str, *, token: str, repository: str, number: int) -> None:
    label = urllib.parse.quote(CLAIM_LABEL, safe="")
    try:
        _request(
            f"{api_url}/repos/{repository}/issues/{number}/labels/{label}",
            token=token,
            method="DELETE",
        )
    except ValueError as error:
        # Removing a label the issue does not carry is not a failure.
        if "HTTP 404" not in str(error):
            raise


def _run_command(args: argparse.Namespace, *, token: str) -> int:
    body = os.environ.get("COMMENT_BODY", "")
    actor = os.environ.get("COMMENT_AUTHOR", "")
    association = os.environ.get("AUTHOR_ASSOCIATION", "")
    command = parse_command(body)
    if command is None:
        print("Comment is not a claim command; nothing to do.")
        return 0
    if not actor or actor.endswith("[bot]"):
        print("Claim commands are for people; ignoring.")
        return 0

    issue = _issue(args.api_url, token=token, repository=args.repository, number=args.number)
    if command == CLAIM_COMMAND:
        open_claims = _open_claims(
            args.api_url, token=token, repository=args.repository, actor=actor
        )
        decision = claim_decision(
            issue, actor=actor, open_claims=open_claims, docs_url=args.docs_url
        )
    else:
        decision = release_decision(
            issue, actor=actor, is_maintainer=association in MAINTAINER_ASSOCIATIONS
        )

    if decision.action == "claim":
        assigned = _assign(
            args.api_url,
            token=token,
            repository=args.repository,
            number=args.number,
            actor=actor,
        )
        _add_label(args.api_url, token=token, repository=args.repository, number=args.number)
        message = decision.message
        if not assigned:
            # Honest about the difference: the label records the claim either
            # way, but the assignee field is what most people read.
            message = (
                f"@{actor}, this issue is marked `{CLAIM_LABEL}` for you and the "
                "claim counts, but GitHub would not accept you as an assignee — "
                "it only allows that for accounts it already knows in this "
                "repository. A maintainer can set the assignee manually.\n\n"
                + decision.message.split("\n", 2)[-1]
            )
        _comment(
            args.api_url,
            token=token,
            repository=args.repository,
            number=args.number,
            body=message,
        )
        print(f"Claimed #{args.number} for {actor} (assignee set: {assigned}).")
        return 0

    if decision.action == "release":
        _unassign(
            args.api_url,
            token=token,
            repository=args.repository,
            number=args.number,
            logins=assignee_logins(issue),
        )
        _remove_label(args.api_url, token=token, repository=args.repository, number=args.number)
        _comment(
            args.api_url,
            token=token,
            repository=args.repository,
            number=args.number,
            body=decision.message,
        )
        print(f"Released #{args.number}.")
        return 0

    _comment(
        args.api_url,
        token=token,
        repository=args.repository,
        number=args.number,
        body=decision.message,
    )
    print(f"Replied on #{args.number} without changing the claim.")
    return 0


def _run_sweep(args: argparse.Namespace, *, token: str) -> int:
    issues = _claimed_issues(args.api_url, token=token, repository=args.repository)
    expired = stale_claims(issues, now=datetime.now(UTC))
    for number, holder in expired:
        _unassign(
            args.api_url,
            token=token,
            repository=args.repository,
            number=number,
            logins=[holder],
        )
        _remove_label(args.api_url, token=token, repository=args.repository, number=number)
        _comment(
            args.api_url,
            token=token,
            repository=args.repository,
            number=number,
            body=stale_claim_body(holder),
        )
        print(f"Released #{number}, claimed by {holder}, after {STALE_AFTER.days} quiet days.")
    print(f"Checked {len(issues)} claimed issues; released {len(expired)}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    command = subcommands.add_parser("command", help="apply one /claim or /unclaim comment")
    command.add_argument("--repository", required=True)
    command.add_argument("--number", required=True, type=int)
    command.add_argument("--api-url", default="https://api.github.com")
    command.add_argument("--docs-url", required=True)

    sweep = subcommands.add_parser("sweep", help="release claims that have gone quiet")
    sweep.add_argument("--repository", required=True)
    sweep.add_argument("--api-url", default="https://api.github.com")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Apply a claim command, or release the claims that have gone quiet."""

    args = _parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("Claim handling failed: GITHUB_TOKEN is missing.", file=sys.stderr)
        return 2
    try:
        if args.command == "command":
            return _run_command(args, token=token)
        return _run_sweep(args, token=token)
    except ValueError as error:
        print(f"Claim handling failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
