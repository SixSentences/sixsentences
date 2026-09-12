"""Keyless replay support for bounded, versioned AgentRunner trajectories."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

from sixsentences_server.agent.events import agent_event_sink
from sixsentences_server.agent.loop import (
    AgentFinalValidation,
    AgentLimits,
    AgentRunner,
    AgentRunResult,
    AgentStopReason,
    AgentTool,
    AgentToolAuthorization,
    AgentToolResult,
)

TRAJECTORY_FIXTURE_SCHEMA: Final = 1
MAX_FIXTURE_BYTES: Final = 128_000
MAX_CASES: Final = 12
MAX_DECISIONS: Final = 12
MAX_TOOL_RESULTS: Final = 8
MAX_EVENTS: Final = 64
MAX_JSON_DEPTH: Final = 6
MAX_JSON_ITEMS: Final = 100
MAX_JSON_STRING_CHARS: Final = 4_000

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{2,63}")
_EVENT_NAME = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+")
_STOP_REASONS = {
    "completed",
    "failure_limit",
    "iteration_limit",
    "time_limit",
    "tool_limit",
}
_RUNNER_EVENTS = {
    "agent.update",
    "answer.completed",
    "checkpoint.completed",
    "checkpoint.failed",
    "checkpoint.progress",
    "checkpoint.started",
    "context.compacted",
    "plan.created",
    "plan.updated",
    "tool.completed",
    "tool.failed",
    "tool.progress",
    "tool.started",
}
_TOOL_EFFECTS = {"read", "staged", "write"}


@dataclass(frozen=True)
class StubToolResult:
    """One deterministic result returned by a replay tool handler."""

    tool: str
    output: Any
    summary: str
    success: bool
    error_code: str
    retryable: bool


@dataclass(frozen=True)
class ExpectedToolCall:
    """One tool handler invocation expected from a trajectory."""

    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ExpectedTrajectory:
    """Semantic outcomes asserted after a deterministic replay."""

    completed: bool
    stop_reason: AgentStopReason
    tool_calls: int
    handler_calls: tuple[ExpectedToolCall, ...]
    events: tuple[str, ...]
    final: dict[str, Any]
    observation_markers: tuple[str, ...]


@dataclass(frozen=True)
class AgentTrajectoryCase:
    """One bounded sequence of model decisions and stubbed observations."""

    id: str
    request: str
    context: str
    decisions: tuple[dict[str, Any], ...]
    tool_results: tuple[StubToolResult, ...]
    expected: ExpectedTrajectory


@dataclass(frozen=True)
class RecordedToolCall:
    """One handler call recorded by the replay runtime."""

    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TrajectoryExecution:
    """Observable output from one keyless trajectory execution."""

    result: AgentRunResult
    events: tuple[dict[str, Any], ...]
    calls: tuple[RecordedToolCall, ...]
    prompts: tuple[str, ...]


class ReplayPool:
    """A strict completion pool that returns only fixture-owned decisions."""

    def __init__(self, decisions: Sequence[Mapping[str, Any]]) -> None:
        self._decisions = [dict(decision) for decision in decisions]
        self.prompts: list[str] = []

    def complete(self, *_args: object, **kwargs: object) -> SimpleNamespace:
        """Return the next decision and fail if the runtime asks for an extra one."""

        if not self._decisions:
            raise AssertionError("agent requested more decisions than the trajectory provides")
        self.prompts.append(str(kwargs.get("prompt") or ""))
        return SimpleNamespace(
            text=json.dumps(self._decisions.pop(0), ensure_ascii=False, sort_keys=True)
        )

    def assert_consumed(self) -> None:
        """Fail when the runtime finished before consuming the complete trajectory."""

        if self._decisions:
            raise AssertionError(f"agent left {len(self._decisions)} replay decision(s) unconsumed")


def load_agent_trajectories(path: Path) -> tuple[AgentTrajectoryCase, ...]:
    """Load a bounded trajectory fixture from disk and validate its full shape."""

    if path.stat().st_size > MAX_FIXTURE_BYTES:
        raise ValueError("agent trajectory fixture exceeds the byte limit")
    raw = path.read_bytes()
    if len(raw) > MAX_FIXTURE_BYTES:
        raise ValueError("agent trajectory fixture exceeds the byte limit")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("agent trajectory fixture is not valid UTF-8 JSON") from exc
    return parse_agent_trajectories(payload)


def parse_agent_trajectories(payload: object) -> tuple[AgentTrajectoryCase, ...]:
    """Validate an in-memory versioned trajectory fixture without silent defaults."""

    root = _mapping(payload, "fixture")
    _exact_keys(root, {"schema", "description", "cases"}, "fixture")
    if root.get("schema") != TRAJECTORY_FIXTURE_SCHEMA:
        raise ValueError("unsupported agent trajectory fixture schema")
    _text(root.get("description"), "fixture.description", maximum=500)
    rows = _sequence(root.get("cases"), "fixture.cases", minimum=1, maximum=MAX_CASES)

    cases: list[AgentTrajectoryCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(rows):
        case = _parse_case(raw_case, path=f"fixture.cases[{index}]")
        if case.id in seen_ids:
            raise ValueError(f"duplicate agent trajectory id: {case.id!r}")
        seen_ids.add(case.id)
        cases.append(case)
    return tuple(cases)


def run_agent_trajectory(case: AgentTrajectoryCase) -> TrajectoryExecution:
    """Execute one fixture through the real AgentRunner without provider access."""

    pool = ReplayPool(case.decisions)
    remaining_results = list(case.tool_results)
    calls: list[RecordedToolCall] = []

    def recorded_handler(tool: str) -> Callable[[dict[str, Any]], AgentToolResult]:
        def handle(arguments: dict[str, Any]) -> AgentToolResult:
            calls.append(RecordedToolCall(tool=tool, arguments=dict(arguments)))
            if not remaining_results:
                raise AssertionError(f"unexpected handler call for {tool!r}")
            stub = remaining_results.pop(0)
            if stub.tool != tool:
                raise AssertionError(f"expected handler {stub.tool!r}, but runtime called {tool!r}")
            return AgentToolResult(
                output=stub.output,
                summary=stub.summary,
                success=stub.success,
                error_code=stub.error_code,
                retryable=stub.retryable,
            )

        return handle

    tools = (
        AgentTool(
            name="lookup",
            label="Inspect evidence",
            description="Read one bounded evidence record.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=recorded_handler("lookup"),
            effect="read",
        ),
        AgentTool(
            name="compile_candidate",
            label="Compile a candidate",
            description="Compile one exact staged edit.",
            input_schema={
                "type": "object",
                "properties": {
                    "step_id": {"type": "string", "minLength": 1, "maxLength": 100},
                    "edit": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "minLength": 1, "maxLength": 200},
                            "find": {"type": "string", "minLength": 1, "maxLength": 500},
                            "replace": {"type": "string", "maxLength": 500},
                        },
                        "required": ["path", "find", "replace"],
                        "additionalProperties": False,
                    },
                },
                "required": ["step_id", "edit"],
                "additionalProperties": False,
            },
            handler=recorded_handler("compile_candidate"),
            effect="staged",
        ),
        AgentTool(
            name="write_note",
            label="Write a note",
            description="Persist one explicitly authorized note.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string", "minLength": 1, "maxLength": 500}},
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=recorded_handler("write_note"),
            effect="write",
            authorizer=lambda _arguments: AgentToolAuthorization(
                allowed=False,
                feedback="Explicit confirmation is missing.",
            ),
        ),
    )

    def validate_final(payload: dict[str, Any]) -> AgentFinalValidation:
        answer = payload.get("answer")
        if payload.get("verified") is True and isinstance(answer, str) and answer.strip():
            return AgentFinalValidation(True, dict(payload))
        return AgentFinalValidation(
            False,
            {},
            "The final answer must be non-empty and explicitly verified.",
        )

    runner = AgentRunner(
        pool,
        workspace="trajectory",
        instructions="Use only observed fixture evidence and respect server authorization.",
        tools=tools,
        limits=AgentLimits(
            max_iterations=MAX_DECISIONS,
            max_tool_calls=MAX_TOOL_RESULTS,
            max_consecutive_failures=4,
            observation_chars=12_000,
            decision_tokens=1_000,
            max_seconds=30,
        ),
        final_validator=validate_final,
    )
    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        result = runner.run(request=case.request, context=case.context)

    pool.assert_consumed()
    if remaining_results:
        raise AssertionError(f"trajectory left {len(remaining_results)} tool result(s) unconsumed")
    return TrajectoryExecution(
        result=result,
        events=tuple(events),
        calls=tuple(calls),
        prompts=tuple(pool.prompts),
    )


def canonical_event_trace(events: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Normalize random call ids while retaining semantically relevant event data."""

    call_ids: dict[str, str] = {}
    trace: list[dict[str, Any]] = []
    for event in events:
        raw_call_id = event.get("call_id")
        call_id = ""
        if isinstance(raw_call_id, str) and raw_call_id:
            call_id = call_ids.setdefault(raw_call_id, f"call-{len(call_ids) + 1}")
        trace.append(
            {
                "event": event.get("event"),
                "tool": event.get("tool"),
                "call_id": call_id,
                "lifecycle": event.get("lifecycle"),
                "effect": event.get("effect"),
                "input": event.get("input"),
                "output": event.get("output"),
            }
        )
    return tuple(trace)


def assert_trajectory_invariants(
    case: AgentTrajectoryCase,
    execution: TrajectoryExecution,
) -> None:
    """Assert lifecycle, observation and bounded-execution invariants."""

    if len(execution.prompts) != execution.result.iterations:
        raise AssertionError("every AgentRunner iteration must consume exactly one decision")
    if len(execution.events) > MAX_EVENTS:
        raise AssertionError("trajectory emitted more events than the harness permits")

    active_calls: dict[str, str] = {}
    terminal_calls: set[str] = set()
    started_tools = 0
    for event in execution.events:
        event_name = str(event.get("event") or "")
        if event_name not in _RUNNER_EVENTS:
            raise AssertionError(f"trajectory emitted unknown runner event {event_name!r}")
        call_id = str(event.get("call_id") or "")
        if event_name == "tool.started":
            if not call_id or call_id in active_calls or call_id in terminal_calls:
                raise AssertionError("tool.started requires one fresh correlated call id")
            effect = str(event.get("effect") or "")
            if effect not in _TOOL_EFFECTS:
                raise AssertionError(f"tool event exposed unknown effect {effect!r}")
            active_calls[call_id] = str(event.get("tool") or "")
            started_tools += 1
        elif event_name in {"tool.completed", "tool.failed"}:
            if not call_id or call_id in terminal_calls:
                raise AssertionError("tool terminal events require one unique call id")
            if event_name == "tool.completed" and call_id not in active_calls:
                raise AssertionError("tool.completed cannot appear without tool.started")
            started_tool = active_calls.get(call_id)
            if started_tool is not None and started_tool != str(event.get("tool") or ""):
                raise AssertionError("tool terminal event closed a different tool call")
            active_calls.pop(call_id, None)
            terminal_calls.add(call_id)

    if active_calls:
        raise AssertionError("trajectory left a started tool lifecycle unfinished")
    if started_tools != len(execution.calls):
        raise AssertionError("each executed handler must have one public tool.started frame")

    answer_events = [
        event for event in execution.events if event.get("event") == "answer.completed"
    ]
    if execution.result.completed:
        if len(answer_events) != 1 or execution.events[-1].get("event") != "answer.completed":
            raise AssertionError("a completed trajectory needs one final answer.completed event")
    elif answer_events:
        raise AssertionError("an incomplete trajectory cannot claim answer.completed")

    if case.expected.observation_markers:
        if len(execution.prompts) < 2:
            raise AssertionError("observed tool results require a later decision prompt")
        first_prompt = execution.prompts[0]
        later_prompts = "\n".join(execution.prompts[1:])
        for marker in case.expected.observation_markers:
            if marker in first_prompt or marker not in later_prompts:
                raise AssertionError(
                    f"tool observation marker {marker!r} was not observed before finishing"
                )


def _parse_case(value: object, *, path: str) -> AgentTrajectoryCase:
    case = _mapping(value, path)
    _exact_keys(
        case,
        {"id", "request", "context", "decisions", "tool_results", "expected"},
        path,
    )
    case_id = _text(case.get("id"), f"{path}.id", maximum=64)
    if _IDENTIFIER.fullmatch(case_id) is None:
        raise ValueError(f"{path}.id is not a stable lowercase identifier")
    request = _text(case.get("request"), f"{path}.request", maximum=2_000)
    context = _text(case.get("context"), f"{path}.context", maximum=4_000, allow_empty=True)

    raw_decisions = _sequence(
        case.get("decisions"),
        f"{path}.decisions",
        minimum=1,
        maximum=MAX_DECISIONS,
    )
    decisions = tuple(
        _parse_decision(decision, path=f"{path}.decisions[{index}]")
        for index, decision in enumerate(raw_decisions)
    )

    raw_results = _sequence(
        case.get("tool_results"),
        f"{path}.tool_results",
        minimum=0,
        maximum=MAX_TOOL_RESULTS,
    )
    tool_results = tuple(
        _parse_tool_result(result, path=f"{path}.tool_results[{index}]")
        for index, result in enumerate(raw_results)
    )
    expected = _parse_expected(case.get("expected"), path=f"{path}.expected")
    if expected.tool_calls != len(expected.handler_calls):
        raise ValueError(f"{path}.expected.tool_calls must match handler_calls")
    if len(tool_results) != len(expected.handler_calls):
        raise ValueError(f"{path}.tool_results must match expected handler_calls")
    if [result.tool for result in tool_results] != [call.tool for call in expected.handler_calls]:
        raise ValueError(f"{path}.tool_results must follow expected handler call order")
    return AgentTrajectoryCase(
        id=case_id,
        request=request,
        context=context,
        decisions=decisions,
        tool_results=tool_results,
        expected=expected,
    )


def _parse_decision(value: object, *, path: str) -> dict[str, Any]:
    decision = _mapping(value, path)
    action = decision.get("action")
    if action == "tool":
        _exact_keys(decision, {"action", "update", "tool", "arguments"}, path)
        tool = _text(decision.get("tool"), f"{path}.tool", maximum=64)
        if _IDENTIFIER.fullmatch(tool) is None:
            raise ValueError(f"{path}.tool is not a stable tool identifier")
        _text(decision.get("update"), f"{path}.update", maximum=600, allow_empty=True)
        _mapping(decision.get("arguments"), f"{path}.arguments")
    elif action == "finish":
        _exact_keys(decision, {"action", "update", "final"}, path)
        _text(decision.get("update"), f"{path}.update", maximum=600, allow_empty=True)
        _mapping(decision.get("final"), f"{path}.final")
    else:
        raise ValueError(f"{path}.action must be 'tool' or 'finish'")
    _bounded_json(decision, path=path, depth=0)
    return dict(decision)


def _parse_tool_result(value: object, *, path: str) -> StubToolResult:
    result = _mapping(value, path)
    _exact_keys(
        result,
        {"tool", "output", "summary", "success", "error_code", "retryable"},
        path,
    )
    tool = _text(result.get("tool"), f"{path}.tool", maximum=64)
    if _IDENTIFIER.fullmatch(tool) is None:
        raise ValueError(f"{path}.tool is not a stable tool identifier")
    summary = _text(result.get("summary"), f"{path}.summary", maximum=1_000)
    success = _boolean(result.get("success"), f"{path}.success")
    error_code = _text(
        result.get("error_code"),
        f"{path}.error_code",
        maximum=100,
        allow_empty=True,
    )
    retryable = _boolean(result.get("retryable"), f"{path}.retryable")
    if success and (error_code or retryable):
        raise ValueError(f"{path} cannot mark a successful result as retryable or errored")
    output = result.get("output")
    _bounded_json(output, path=f"{path}.output", depth=0)
    return StubToolResult(tool, output, summary, success, error_code, retryable)


def _parse_expected(value: object, *, path: str) -> ExpectedTrajectory:
    expected = _mapping(value, path)
    _exact_keys(
        expected,
        {
            "completed",
            "stop_reason",
            "tool_calls",
            "handler_calls",
            "events",
            "final",
            "observation_markers",
        },
        path,
    )
    completed = _boolean(expected.get("completed"), f"{path}.completed")
    stop_reason = _text(expected.get("stop_reason"), f"{path}.stop_reason", maximum=32)
    if stop_reason not in _STOP_REASONS:
        raise ValueError(f"{path}.stop_reason is unsupported")
    if completed != (stop_reason == "completed"):
        raise ValueError(f"{path}.completed conflicts with stop_reason")
    tool_calls = _integer(
        expected.get("tool_calls"),
        f"{path}.tool_calls",
        minimum=0,
        maximum=MAX_TOOL_RESULTS,
    )

    raw_calls = _sequence(
        expected.get("handler_calls"),
        f"{path}.handler_calls",
        minimum=0,
        maximum=MAX_TOOL_RESULTS,
    )
    handler_calls: list[ExpectedToolCall] = []
    for index, value in enumerate(raw_calls):
        call_path = f"{path}.handler_calls[{index}]"
        call = _mapping(value, call_path)
        _exact_keys(call, {"tool", "arguments"}, call_path)
        tool = _text(call.get("tool"), f"{call_path}.tool", maximum=64)
        arguments = dict(_mapping(call.get("arguments"), f"{call_path}.arguments"))
        _bounded_json(arguments, path=f"{call_path}.arguments", depth=0)
        handler_calls.append(ExpectedToolCall(tool=tool, arguments=arguments))

    raw_events = _sequence(
        expected.get("events"),
        f"{path}.events",
        minimum=1,
        maximum=MAX_EVENTS,
    )
    events = tuple(
        _text(event, f"{path}.events[{index}]", maximum=100)
        for index, event in enumerate(raw_events)
    )
    if any(_EVENT_NAME.fullmatch(event) is None for event in events):
        raise ValueError(f"{path}.events contains an invalid event name")
    if completed and events[-1] != "answer.completed":
        raise ValueError(f"{path}.events must end with answer.completed")

    final = dict(_mapping(expected.get("final"), f"{path}.final"))
    _bounded_json(final, path=f"{path}.final", depth=0)
    raw_markers = _sequence(
        expected.get("observation_markers"),
        f"{path}.observation_markers",
        minimum=0,
        maximum=MAX_TOOL_RESULTS,
    )
    markers = tuple(
        _text(marker, f"{path}.observation_markers[{index}]", maximum=200)
        for index, marker in enumerate(raw_markers)
    )
    if len(markers) != len(set(markers)):
        raise ValueError(f"{path}.observation_markers contains duplicates")
    return ExpectedTrajectory(
        completed=completed,
        stop_reason=stop_reason,
        tool_calls=tool_calls,
        handler_calls=tuple(handler_calls),
        events=events,
        final=final,
        observation_markers=markers,
    )


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be a JSON object with string keys")
    return value


def _sequence(
    value: object,
    path: str,
    *,
    minimum: int,
    maximum: int,
) -> Sequence[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{path} must contain between {minimum} and {maximum} items")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise ValueError(f"{path} has missing keys {missing} and unknown keys {unknown}")


def _text(
    value: object,
    path: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if len(value) > maximum or (not allow_empty and not value.strip()):
        minimum = 0 if allow_empty else 1
        raise ValueError(f"{path} must contain between {minimum} and {maximum} chars")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _integer(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{path} must be an integer from {minimum} through {maximum}")
    return value


def _bounded_json(value: Any, *, path: str, depth: int) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"{path} exceeds the JSON nesting limit")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING_CHARS:
            raise ValueError(f"{path} contains a string above the character limit")
        return
    if isinstance(value, list):
        if len(value) > MAX_JSON_ITEMS:
            raise ValueError(f"{path} contains too many JSON items")
        for index, item in enumerate(value):
            _bounded_json(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_JSON_ITEMS:
            raise ValueError(f"{path} contains too many JSON properties")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100:
                raise ValueError(f"{path} contains an invalid JSON property name")
            _bounded_json(item, path=f"{path}.{key}", depth=depth + 1)
        return
    raise ValueError(f"{path} contains a non-JSON value")
