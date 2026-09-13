"""Bounded conversation context shared by every product agent.

The database remains the source of truth for the complete transcript.  This
module selects a stable, useful window for the model instead of letting every
feature truncate history differently.  Recent turns stay chronological and
explicit corrections are repeated as authoritative constraints so a later
"survey, not interview" cannot be lost behind an older assumption.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from sixsentences_server.agent.events import emit_agent_event

_CORRECTION = re.compile(
    r"\b(?:actually|rather|instead|correction|to\s+clarify|i\s+meant|"
    r"not\s+(?:the|an?|that)|do\s+not|don't|no\s+longer|"
    r"eigentlich|stattdessen|korrektur|zur\s+klarstellung|ich\s+meinte|"
    r"meinte|nicht\s+(?:das|den|die|ein\w*)|kein\w*|doch\s+nicht|"
    r"sondern)\b",
    re.IGNORECASE,
)
_REFERENCE = re.compile(
    r"\b(?:based\s+on|using|linked|attached|uploaded|from\s+(?:the|my)|"
    r"auf\s+basis|verknüpf\w*|verknuepf\w*|angehäng\w*|angehaeng\w*|"
    r"hochgeladen\w*|aus\s+(?:dem|der|den|meinem|meiner)|"
    r"survey|questionnaire|interview|transcript|dataset|paper|review|"
    r"umfrage|fragebogen|transkript|datensatz|quelle|manuskript)\b",
    re.IGNORECASE,
)
_CONSTRAINT = re.compile(
    r"\b(?:always|never|only|keep|preserve|without|must|avoid|"
    r"immer|niemals|nur|behalte\w*|ohne|muss|müssen|vermeide\w*)\b",
    re.IGNORECASE,
)
_QUERY_STOPWORDS = frozenset(
    [
        "this",
        "that",
        "these",
        "those",
        "with",
        "from",
        "about",
        "please",
        "could",
        "would",
        "should",
        "what",
        "where",
        "when",
        "which",
        "make",
        "more",
        "continue",
        "again",
        "using",
        "write",
        "tell",
        "show",
        "also",
        "have",
        "been",
        "schreib",
        "schreibe",
        "bitte",
        "weiter",
        "nochmal",
        "diese",
        "dieser",
        "dieses",
        "dafür",
        "daraus",
        "dabei",
        "etwas",
        "jetzt",
        "auch",
        "noch",
        "eine",
        "einer",
        "einen",
        "eines",
        "über",
        "durch",
        "mache",
        "machen",
        "kann",
        "kannst",
        "soll",
        "sollen",
        "erkläre",
        "erkläre",
        "mir",
    ]
)


def context_query_terms(request: str) -> tuple[str, ...]:
    """Extract bounded lexical retrieval hints, never a new external query."""
    return tuple(
        dict.fromkeys(
            token
            for token in re.findall(r"[^\W_]{4,}", request.casefold(), re.UNICODE)
            if token not in _QUERY_STOPWORDS
        )
    )[:12]


def _turn(item: Mapping[str, Any]) -> tuple[str, str] | None:
    role = str(item.get("role") or "").strip().lower()
    content = str(item.get("content") or "").strip()
    if role not in {"user", "assistant"} or not content:
        return None
    return role, content


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _excerpt(text: str, limit: int, terms: tuple[str, ...]) -> str:
    """Keep verbatim head, relevant/corrective middle and tail, marking gaps."""
    if len(text) <= limit:
        return text
    if limit < 120:
        return _clip(text, limit)
    window = (limit - 32) // 3
    hits = [match.start() for match in _CORRECTION.finditer(text)]
    folded = text.casefold()
    hits.extend(position for term in terms if (position := folded.find(term)) >= 0)
    middle = hits[-1] if hits else len(text) // 2
    middle = max(window, min(len(text) - 2 * window, middle - window // 3))
    return _clip(
        text[:window] + "\n[…]\n" + text[middle : middle + window] + "\n[…]\n" + text[-window:],
        limit,
    )


def render_conversation_context(
    history: Sequence[Mapping[str, Any]],
    *,
    max_chars: int = 16_000,
    recent_turns: int = 14,
    per_turn_chars: int = 2_400,
    current_request: str = "",
) -> str:
    """Render history with recency, corrections and a hard character budget.

    The newest turns receive most of the budget.  Older user turns survive
    when they name evidence or correct an assumption.  Nothing is summarized
    by another model, so this layer cannot invent state or silently change a
    research instruction.
    """
    return _render_context(
        history,
        max_chars=max_chars,
        recent_turns=recent_turns,
        per_turn_chars=per_turn_chars,
        current_request=current_request,
    )[0]


def _render_context(
    history: Sequence[Mapping[str, Any]],
    *,
    max_chars: int,
    recent_turns: int,
    per_turn_chars: int,
    current_request: str,
) -> tuple[str, bool]:
    """Allocate explicit user instructions before verbose assistant answers."""
    if max_chars <= 0:
        return "", bool(history)
    turns = [parsed for item in history if (parsed := _turn(item)) is not None]
    if not turns:
        return "", False
    terms = context_query_terms(current_request)
    users = [index for index, (role, _) in enumerate(turns) if role == "user"]
    corrections = [index for index in users if _CORRECTION.search(turns[index][1])]
    constraints = [index for index in users if _CONSTRAINT.search(turns[index][1])]
    recent_start = max(0, len(turns) - max(1, recent_turns))
    header = (
        "Conversation context (selected verbatim excerpts in chronological order; "
        "gaps are marked […]). The current request, later user instructions and "
        "current canonical workspace state override older turns. Assistant text "
        "is not proof of an applied edit or permission to access a source. "
        "Authoritative recent user corrections are retained below when available; "
        "do not revive superseded instructions. Ask if a required detail is missing.\n"
    )
    budget = max_chars - len(header)
    if budget <= 0:
        return _clip(header, max_chars), True
    rendered: dict[int, str] = {}
    clipped = False

    def retain(index: int, allowance: int) -> None:
        nonlocal budget, clipped
        if index in rendered or budget < 100:
            return
        role, content = turns[index]
        prefix = f"TURN {index + 1} {role.upper()}: "
        size = min(max(0, allowance), max(1, per_turn_chars), budget - len(prefix) - 1)
        if size < 60:
            return
        excerpt = _excerpt(content, size, terms)
        line = prefix + excerpt
        rendered[index] = line
        budget -= len(line) + 8
        clipped = clipped or len(content) > size

    # Reserve half of the working window for user anchors. The latest user
    # request and initial goal survive even a long run of assistant/tool prose.
    anchors = list(
        dict.fromkeys(
            [
                *users[-2:][::-1],
                *users[:1],
                *corrections[-8:][::-1],
                *constraints[-8:][::-1],
            ]
        )
    )
    anchor_limit = max(120, min(1_200, budget // max(2, 2 * len(anchors))))
    for index in anchors:
        retain(index, anchor_limit)

    # Relevant older user turns outrank old assistant guesses. Lexical matching
    # stays inside this authorized conversation; nothing is sent to web search.
    relevance = sorted(
        (index for index in users if index < recent_start and terms),
        key=lambda index: (
            sum(term in turns[index][1].casefold() for term in terms),
            index,
        ),
        reverse=True,
    )
    relevant_budget = max(0, budget // 4)
    for index in relevance[:8]:
        if not any(term in turns[index][1].casefold() for term in terms):
            continue
        before = budget
        retain(index, min(1_200, relevant_budget))
        relevant_budget -= before - budget
        if relevant_budget < 100:
            break
    for index in range(len(turns) - 1, recent_start - 1, -1):
        retain(index, per_turn_chars)
    for index in reversed(users):
        if _REFERENCE.search(turns[index][1]):
            retain(index, min(per_turn_chars, 1_200))
    lines: list[str] = []
    previous = -1
    for index in sorted(rendered):
        if index > previous + 1:
            lines.append("[…]")
        lines.append(rendered[index])
        previous = index
    body = "\n".join(lines)
    return _clip(header + body, max_chars), clipped or len(rendered) < len(turns)


def render_model_aware_context(
    history: Sequence[Mapping[str, Any]],
    *,
    pool: Any,
    local_edit: bool = False,
    current_request: str = "",
) -> str:
    """Render a stable history window sized to the selected model."""

    from sixsentences_server.llm.models import available_chat_models

    routed = getattr(getattr(pool, "routing", None), "synthesis", None)
    routed_model = str(getattr(routed, "model", "") or "")
    model = next(
        (entry for entry in available_chat_models() if entry.model == routed_model),
        None,
    )
    context_tokens = int(getattr(model, "context_length", 128_000))
    # Keep roughly one quarter of the selected model's window available for
    # conversation history while reserving the rest for workspace material,
    # tool schemas, retrieved evidence and the answer.  Character budgets are
    # deliberately conservative but no longer collapse a 128k model to an
    # 8k-token history window.
    history_token_budget = min(56_000, max(8_000, context_tokens // 4))
    max_chars = 4_000 if local_edit else min(196_000, history_token_budget * 4)
    # A selected-source rewrite deliberately stays local. Two recent turns are
    # enough to preserve the user's correction without dragging unrelated
    # research history into a small edit prompt.
    recent_turns = 2 if local_edit else min(72, max(18, context_tokens // 4_000))
    rendered, compacted = _render_context(
        history,
        max_chars=max_chars,
        recent_turns=recent_turns,
        per_turn_chars=1_200 if local_edit else 3_200,
        current_request=current_request,
    )
    if compacted:
        emit_agent_event(
            "context.compacted",
            tool="context.compact",
            label="Compacted the conversation context",
            detail=(
                "Kept the latest instructions, corrections and linked-evidence "
                f"references inside a {max_chars:,}-character working window."
            ),
            result_count=len(history),
        )
    return rendered
