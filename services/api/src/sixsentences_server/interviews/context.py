"""Deterministic transcript windows with original, chronological evidence.

Selection never summarizes testimony. The complete stored transcript remains the
source of truth; a working window is explicitly not a complete interview review.
"""

from __future__ import annotations

import heapq
import re
from collections import deque
from collections.abc import Callable, Iterable

_STOPWORDS = frozenset(
    [
        "about",
        "after",
        "also",
        "answer",
        "before",
        "could",
        "does",
        "from",
        "have",
        "interview",
        "please",
        "said",
        "says",
        "tell",
        "that",
        "their",
        "them",
        "there",
        "these",
        "they",
        "this",
        "transcript",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
        "your",
        "bitte",
        "dazu",
        "diese",
        "dieser",
        "dieses",
        "eine",
        "einem",
        "einen",
        "einer",
        "etwas",
        "frage",
        "gesagt",
        "hatte",
        "haben",
        "hier",
        "ihre",
        "ihren",
        "ihrem",
        "kann",
        "kannst",
        "nochmal",
        "sage",
        "sagt",
        "schon",
        "sich",
        "sind",
        "über",
        "unten",
        "waren",
        "wurde",
        "wurden",
        "zusammenfassen",
    ]
)
_CORRECTION = re.compile(
    r"\b(?:correction|correcting|actually|instead|i\s+meant|not\s+anymore|"
    r"korrektur|eigentlich|stattdessen|ich\s+meinte|doch\s+nicht|sondern)\b",
    re.IGNORECASE,
)


def select_transcript_window[Item](
    items: Iterable[Item],
    *,
    text: Callable[[Item], str],
    request: str,
    max_chars: int,
    framing_chars: int = 96,
) -> list[Item]:
    """Keep opening, newest and request-matching evidence in source order.

    Iterate the complete frozen snapshot with bounded retained candidates. A
    relevant passage carries its neighbouring turn where room permits, so an
    interviewer question is not mistaken for the participant's answer. Returned
    objects and their source identifiers are never rewritten or renumbered.
    """
    if max_chars <= framing_chars:
        return []
    tokens = {
        word
        for word in re.findall(r"\w+", request.casefold())
        if len(word) >= 4 and word not in _STOPWORDS
    }
    head: list[tuple[int, Item, int]] = []
    tail: deque[tuple[int, Item, int]] = deque()
    tail_chars = 0
    # Heap values have a unique chronological index, so item objects are never
    # compared. The candidate bound is independent of transcript length.
    relevant: list[tuple[int, int, Item, int, tuple[int, Item, int] | None]] = []
    neighbours: dict[int, tuple[int, Item, int]] = {}
    previous: tuple[int, Item, int] | None = None
    previous_was_relevant = False
    total_chars = 0
    all_small: list[tuple[int, Item, int]] | None = []
    for index, item in enumerate(items):
        content = text(item)
        cost = len(content) + framing_chars
        entry = (index, item, cost)
        total_chars += cost
        if all_small is not None:
            if total_chars <= max_chars:
                all_small.append(entry)
            else:
                all_small = None
        if len(head) < 3:
            head.append(entry)
        tail.append(entry)
        tail_chars += cost
        while len(tail) > 1 and tail_chars > max_chars:
            tail_chars -= tail.popleft()[2]
        if previous_was_relevant and previous is not None:
            neighbours[previous[0]] = entry
        words = set(re.findall(r"\w+", content.casefold()))
        score = 10 * len(tokens & words) + (2 if _CORRECTION.search(content) else 0)
        previous_was_relevant = score > 0
        if score:
            candidate = (score, index, item, cost, previous)
            if len(relevant) < 128:
                heapq.heappush(relevant, candidate)
            elif candidate[:2] > relevant[0][:2]:
                removed = heapq.heapreplace(relevant, candidate)
                neighbours.pop(removed[1], None)
            else:
                previous_was_relevant = False
        previous = entry
    if all_small is not None:
        return [item for _index, item, _cost in all_small]

    selected: dict[int, Item] = {}
    used = 0

    def add(entry: tuple[int, Item, int] | None, ceiling: int = max_chars) -> None:
        nonlocal used
        if entry is None:
            return
        index, item, cost = entry
        if index not in selected and used + cost <= ceiling:
            selected[index] = item
            used += cost

    # A complete newest turn and the opening survive even if many passages
    # share generic topic words. Fill most of the remaining space by relevance.
    add(tail[-1] if tail else None)
    add(head[0] if head else None)
    for entry in head[1:]:
        add(entry, max(max_chars // 3, used))
    for _score, index, item, cost, before in sorted(relevant, reverse=True):
        add((index, item, cost))
        if index in selected:
            add(before)
            add(neighbours.get(index))
    for entry in reversed(tail):
        add(entry)
    for entry in head:
        add(entry)
    return [selected[index] for index in sorted(selected)]
