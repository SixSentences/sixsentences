"""Conflict-safe primitives for collaborative manuscript editing.

The API stores complete LaTeX files, so the collaboration boundary is a
revisioned file rather than an unversioned last-write-wins blob. Independent
line edits are merged automatically. Edits that touch the same base lines are
returned as a conflict and must be resolved by the author.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class TextChange:
    """One replacement of a half-open line range in the common base."""

    start: int
    end: int
    replacement: tuple[str, ...]


def _changes(base: list[str], variant: list[str]) -> list[TextChange]:
    matcher = SequenceMatcher(a=base, b=variant, autojunk=False)
    changes: list[TextChange] = []
    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append(
            TextChange(
                start=a_start,
                end=a_end,
                replacement=tuple(variant[b_start:b_end]),
            )
        )
    return changes


def _overlap(left: TextChange, right: TextChange) -> bool:
    """Return whether two base-relative edits compete for the same position."""

    if left.start == left.end and right.start == right.end:
        return left.start == right.start and left.replacement != right.replacement
    if left.start == left.end:
        return right.start < left.start < right.end
    if right.start == right.end:
        return left.start < right.start < left.end
    return max(left.start, right.start) < min(left.end, right.end)


def merge_text(base: str, local: str, remote: str) -> str | None:
    """Three-way merge line-disjoint edits.

    ``base`` is what the client opened, ``local`` is its current draft and
    ``remote`` is the latest server revision. ``None`` means both authors
    changed the same base range and a human choice is required.
    """

    if local == remote:
        return local
    if remote == base:
        return local
    if local == base:
        return remote

    base_lines = base.splitlines(keepends=True)
    local_changes = _changes(base_lines, local.splitlines(keepends=True))
    remote_changes = _changes(base_lines, remote.splitlines(keepends=True))

    for local_change in local_changes:
        for remote_change in remote_changes:
            if (
                local_change.start == remote_change.start
                and local_change.end == remote_change.end
                and local_change.replacement == remote_change.replacement
            ):
                continue
            if _overlap(local_change, remote_change):
                return None

    combined: dict[tuple[int, int], TextChange] = {}
    for change in [*remote_changes, *local_changes]:
        combined[(change.start, change.end)] = change

    result = list(base_lines)
    for change in sorted(
        combined.values(),
        key=lambda item: (item.start, item.end),
        reverse=True,
    ):
        result[change.start : change.end] = list(change.replacement)
    return "".join(result)
