"""Safe, request-scoped progress events for interactive specialist agents.

The event stream deliberately contains operational summaries and validated
tool results, never a model's private scratchpad or hidden chain of thought.
Callers that do not bind a sink keep the existing synchronous behaviour.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

AgentEvent = dict[str, Any]
AgentEventSink = Callable[[AgentEvent], None]
AgentCancelCheck = Callable[[], bool]
ToolLifecycle = Literal["started", "progress", "completed", "failed"]

PUBLIC_PROGRESS_COPY_RULE = (
    "For every user-facing update, plan item and operational checkpoint summary, "
    "describe the current work or result directly in positive, task-focused language. "
    "Do not deny hypothetical actions the user did not request, and do not narrate "
    "malformed responses, duplicate calls, retries, guardrails, recovery paths or "
    "internal failure history. Mention a constraint or failure only when the user "
    "explicitly asked about it or it is a material blocker they need to resolve."
)


class NormalizedToolEvent(TypedDict):
    """Stable public contract used to render one specialist lifecycle."""

    event: str
    tool: str
    call_id: str
    lifecycle: ToolLifecycle
    label: str
    input: Any
    output: Any


@dataclass(frozen=True)
class _ActiveToolCall:
    call_id: str
    input: Any


@dataclass
class AgentEventNormalizer:
    """Correlate request-scoped tool and checkpoint events before persistence.

    Specialist services historically emitted a mixture of complete lifecycles,
    progress-first recovery calls and completion-only server checks.  A renderer
    cannot safely group repeated calls by tool name alone, so every tool event is
    assigned a durable call id and an explicit lifecycle.  Existing ids from the
    iterative runner are preserved.
    """

    _active_calls: dict[tuple[str, str], list[_ActiveToolCall]] = field(default_factory=dict)

    def normalize(self, event: AgentEvent) -> AgentEvent:
        """Return a copy conforming to :class:`NormalizedToolEvent` when applicable."""

        normalized = dict(event)
        event_name = str(normalized.get("event") or "")
        lifecycle = _LIFECYCLE_EVENT_NAMES.get(event_name)
        if lifecycle is None:
            return normalized

        tool = str(normalized.get("tool") or "agent.tool").strip() or "agent.tool"
        supplied_call_id = _valid_call_id(normalized.get("call_id"))
        family = event_name.partition(".")[0]
        active_key = (family, tool)
        active = self._active_calls.setdefault(active_key, [])
        # Checkpoint progress is frequently a complete one-shot status update
        # (for example, reusing an available result). Only an explicitly opened
        # checkpoint remains in progress; standalone updates must never render as
        # an indefinitely running task.
        if event_name == "checkpoint.progress" and not active:
            lifecycle = "completed"

        if lifecycle == "started":
            call = _ActiveToolCall(
                call_id=supplied_call_id or secrets.token_urlsafe(9),
                input=normalized.get("input"),
            )
            active.append(call)
        elif lifecycle == "progress":
            call = self._resolve_active_call(
                active,
                supplied_call_id=supplied_call_id,
                input_value=normalized.get("input"),
                create=True,
            )
        else:
            call = self._resolve_active_call(
                active,
                supplied_call_id=supplied_call_id,
                input_value=normalized.get("input"),
                create=False,
            )
            self._finish_call(active, call.call_id)

        if not active:
            self._active_calls.pop(active_key, None)
        normalized.update(
            {
                "event": event_name,
                "tool": tool,
                "call_id": call.call_id,
                "lifecycle": lifecycle,
                "label": _tool_event_label(normalized.get("label"), tool),
                "input": normalized.get("input", call.input),
                "output": normalized.get("output"),
            }
        )
        return normalized

    @staticmethod
    def _resolve_active_call(
        active: list[_ActiveToolCall],
        *,
        supplied_call_id: str | None,
        input_value: Any,
        create: bool,
    ) -> _ActiveToolCall:
        if supplied_call_id is not None:
            matching = next(
                (call for call in reversed(active) if call.call_id == supplied_call_id),
                None,
            )
            if matching is not None:
                return matching
        elif active:
            return active[-1]

        call = _ActiveToolCall(
            call_id=supplied_call_id or secrets.token_urlsafe(9),
            input=input_value,
        )
        if create:
            active.append(call)
        return call

    @staticmethod
    def _finish_call(active: list[_ActiveToolCall], call_id: str) -> None:
        for index in range(len(active) - 1, -1, -1):
            if active[index].call_id == call_id:
                active.pop(index)
                return


_LIFECYCLE_EVENT_NAMES: dict[str, ToolLifecycle] = {
    "tool.started": "started",
    "tool.progress": "progress",
    "tool.completed": "completed",
    "tool.failed": "failed",
    "checkpoint.started": "started",
    "checkpoint.progress": "progress",
    "checkpoint.completed": "completed",
    "checkpoint.failed": "failed",
}
_CALL_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{4,128}")


def _valid_call_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _CALL_ID_PATTERN.fullmatch(candidate) else None


def _tool_event_label(value: Any, tool: str) -> str:
    label = str(value or "").strip()
    if label:
        return label[:500]
    return tool.rsplit(".", 1)[-1].replace("_", " ").title()[:500]


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "password_hash",
    "secret",
    "token",
}

_PUBLIC_EVENT_MAX_DEPTH = 4
_PUBLIC_EVENT_MAX_ITEMS = 30
_PUBLIC_EVENT_MAX_STRING_CHARS = 4_000
_MODEL_OBSERVATION_MAX_DEPTH = 6
_MODEL_OBSERVATION_MAX_ITEMS = 100
_MODEL_OBSERVATION_MAX_STRING_CHARS = 24_000

_INTERNAL_PROGRESS_DISCLOSURE = re.compile(
    r"(?ix)"
    r"(?:\b(?:iteration(?:s)?[\s_-]*(?:\d+(?:\s*/\s*\d+)?|count|limit|"
    r"ceiling|cap|budget|remaining|left)|"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve)[\s_-]+iterations?|\d+[\s_-]*iteration[\s_-]*(?:ceiling|cap|limit)|"
    r"(?:current|next|final)[\s_-]*iteration|tool[\s_-]*calls?|"
    r"work[\s_-]*limit|safe[\s_-]*guard|safety[\s_-]*limit|"
    r"\d+[\s_-]+(?:distinct[\s_-]+)?search[\s_-]*passes?|"
    r"search[\s_-]*passes?[\s_-]+(?:remain|required|left)|"
    r"remaining[\s_-]*(?:budget|calls?|tools?))\b|"
    r"\b(?:iteration(?:en)?[\s_-]*(?:\d+|anzahl|limit|obergrenze|budget|"
    r"verbleibend)|(?:ein(?:e)?|zwei|drei|vier|f(?:ü|ue)nf|sechs|sieben|"
    r"acht|neun|zehn)[\s_-]+iteration(?:en)?|"
    r"(?:aktuelle|n(?:ä|ae)chste|letzte)[\s_-]*iteration|"
    r"tool[\s_-]*aufrufe?|arbeitslimit|sicherheitslimit|"
    r"\d+[\s_-]+suchl(?:auf|äufe)|"
    r"verbleibende[\s_-]*(?:aufrufe?|tools?|budget))\b)"
)

_DOMAIN_ITERATION_CONTEXT = re.compile(
    r"(?ix)\b(?:algorithm|solver|optimization|optimisation|training|model|method|"
    r"experiment|simulation|converg\w*|epoch|gradient|verfahren|algorithmus|"
    r"optimierung|training|modell|methode|experiment|simulation)\w*\b"
)
_HARD_RUNTIME_CONTEXT = re.compile(
    r"(?ix)\b(?:agent|turn|tool[\s_-]*calls?|budget|limit|ceiling|cap|remaining|"
    r"left|guard|review[\s_-]*round|search[\s_-]*passes?|"
    r"aufrufe?|arbeitslimit|sicherheitslimit|obergrenze|verbleibend)\b"
)

_EVENT_SINK: ContextVar[AgentEventSink | None] = ContextVar(
    "sixsentences_agent_event_sink",
    default=None,
)
_CANCEL_CHECK: ContextVar[AgentCancelCheck | None] = ContextVar(
    "sixsentences_agent_cancel_check",
    default=None,
)


@contextmanager
def agent_event_sink(sink: AgentEventSink) -> Iterator[None]:
    """Bind ``sink`` to the current specialist-agent request."""

    normalizer = AgentEventNormalizer()

    def normalized_sink(event: AgentEvent) -> None:
        sink(normalizer.normalize(event))

    token = _EVENT_SINK.set(normalized_sink)
    try:
        yield
    finally:
        _EVENT_SINK.reset(token)


@contextmanager
def agent_cancellation_scope(cancel_check: AgentCancelCheck) -> Iterator[None]:
    """Bind cooperative cancellation to every model pool in this agent turn."""

    token = _CANCEL_CHECK.set(cancel_check)
    try:
        yield
    finally:
        _CANCEL_CHECK.reset(token)


def current_agent_cancel_check() -> AgentCancelCheck | None:
    """Return the request-scoped cancellation probe, when one is active."""

    return _CANCEL_CHECK.get()


def emit_agent_event(event: str, /, **payload: Any) -> None:
    """Emit one safe progress event when a live consumer is attached."""

    sink = _EVENT_SINK.get()
    if sink is not None:
        sink({"event": event, **payload})


def safe_agent_text(value: str, *, fallback: str) -> str:
    """Remove runtime-control disclosures from model-authored public prose."""

    text = str(value or "").strip()
    if not text:
        return fallback
    if not _INTERNAL_PROGRESS_DISCLOSURE.search(text):
        return text[:12_000]
    parts = re.split(r"(?<=[.!?])\s+|[\r\n]+", text)
    kept = []
    for part in parts:
        disclosure = _INTERNAL_PROGRESS_DISCLOSURE.search(part)
        if disclosure is None:
            kept.append(part.strip())
            continue
        # Preserve substantive iteration counts in scientific content while
        # still removing bare progress counters and every explicit runtime marker.
        if _DOMAIN_ITERATION_CONTEXT.search(part) and not _HARD_RUNTIME_CONTEXT.search(part):
            kept.append(part.strip())
    cleaned = " ".join(part for part in kept if part).strip()
    return cleaned[:12_000] or fallback


def _safe_value(
    value: Any,
    *,
    depth: int,
    max_depth: int,
    max_items: int,
    max_string_chars: int,
) -> Any:
    """Return a bounded inspectable value with sensitive fields removed."""

    if depth >= max_depth:
        return "…"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:max_items]:
            key = str(raw_key)
            if key.casefold() in _SENSITIVE_KEYS:
                cleaned[key] = "[protected]"
            else:
                cleaned[key] = _safe_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_string_chars=max_string_chars,
                )
        return cleaned
    if isinstance(value, (list, tuple)):
        return [
            _safe_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for item in list(value)[:max_items]
        ]
    if isinstance(value, str):
        return value[:max_string_chars]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[: min(1_000, max_string_chars)]


def _safe_event_value(value: Any, *, depth: int = 0) -> Any:
    """Return a small, inspectable public event value with secrets removed.

    Agent events are persisted and rendered to users. They may contain exact
    editor inputs and deterministic outputs, but never credentials or an
    unbounded copy of a workspace.
    """

    return _safe_value(
        value,
        depth=depth,
        max_depth=_PUBLIC_EVENT_MAX_DEPTH,
        max_items=_PUBLIC_EVENT_MAX_ITEMS,
        max_string_chars=_PUBLIC_EVENT_MAX_STRING_CHARS,
    )


def safe_event_value(value: Any) -> Any:
    """Public wrapper used by API-owned tool execution events."""

    return _safe_event_value(value)


def safe_model_observation_value(value: Any) -> Any:
    """Return model-visible tool data without applying the public 4k preview cap.

    Writer read tools already bound individual excerpts to at most 18k
    characters. Keeping a separate, still finite model bound ensures their
    continuation cursors and citation receipts describe text the model actually
    received, while persisted public events retain their smaller preview.
    """

    return _safe_value(
        value,
        depth=0,
        max_depth=_MODEL_OBSERVATION_MAX_DEPTH,
        max_items=_MODEL_OBSERVATION_MAX_ITEMS,
        max_string_chars=_MODEL_OBSERVATION_MAX_STRING_CHARS,
    )


def emit_change_events(
    *,
    workspace: str,
    changes: list[dict[str, Any]],
    applied: bool,
    summary_label: str = "",
    summary_detail: str = "",
) -> None:
    """Expose validated changes one by one without leaking model scratchpads."""

    total = len(changes)
    if total:
        emit_agent_event(
            "agent.update",
            tool=f"{workspace}.report_plan",
            label=(
                summary_label[:500]
                or f"Prepared {total} validated change{'s' if total != 1 else ''}"
            ),
            detail=(
                summary_detail[:2_000]
                or "The proposed operations passed the workspace contract. "
                "Each change is listed separately with its exact safe inputs and result."
            ),
            output=_safe_event_value(
                {
                    "change_count": total,
                    "operations": [
                        str(change.get("operation") or change.get("type") or "change")
                        for change in changes
                    ],
                    "applied": applied,
                }
            ),
            result_count=total,
        )
    for index, change in enumerate(changes, start=1):
        operation = str(change.get("operation") or change.get("type") or "change")
        label = str(
            change.get("label")
            or change.get("title")
            or change.get("name")
            or change.get("text")
            or operation.replace("_", " ").title()
        )[:500]
        detail_value = (
            change.get("detail")
            or change.get("description")
            or change.get("changes")
            or change.get("definition")
            or change.get("replace")
            or ""
        )
        detail = (
            json.dumps(detail_value, ensure_ascii=False, sort_keys=True)
            if isinstance(detail_value, (dict, list))
            else str(detail_value)
        )[:2_000]
        before = change.get("find") or change.get("before")
        after = change.get("replace") or change.get("after") or change.get("changes")
        emit_agent_event(
            "change.completed" if applied else "change.proposed",
            tool=f"{workspace}.apply_change" if applied else f"{workspace}.propose_change",
            operation=operation,
            label=label,
            detail=detail,
            input=_safe_event_value(
                {key: value for key, value in change.items() if key not in {"result", "output"}}
            ),
            output=_safe_event_value(change.get("result") or change.get("output")),
            before=_safe_event_value(before),
            after=_safe_event_value(after),
            applied=applied,
            index=index,
            total=total,
        )
