"""Model-directed agent-loop contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sixsentences_server.agent.events import agent_event_sink, safe_agent_text, safe_event_value
from sixsentences_server.agent.loop import (
    AgentFinalValidation,
    AgentLimits,
    AgentRunner,
    AgentTool,
    AgentToolAuthorization,
    AgentToolResult,
)
from sixsentences_server.llm.base import LLMCancelledError


class _SequencePool:
    def __init__(self, decisions: list[object]) -> None:
        self.decisions = list(decisions)
        self.prompts: list[dict[str, object]] = []
        self.cancel_check = None

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        decision = self.decisions.pop(0)
        return SimpleNamespace(text=decision if isinstance(decision, str) else json.dumps(decision))


def test_default_agent_safeguards_leave_room_for_long_iterative_work() -> None:
    limits = AgentLimits()

    assert limits.max_iterations >= 64
    assert limits.max_tool_calls >= 48
    assert limits.max_consecutive_failures >= 8
    assert limits.max_seconds >= 1_200


def _lookup_tool(calls: list[dict[str, object]]) -> AgentTool:
    def lookup(arguments: dict[str, object]) -> AgentToolResult:
        calls.append(arguments)
        return AgentToolResult(
            output=[{"title": "Grounded result", "query": arguments["query"]}],
            summary="Found one grounded result",
        )

    return AgentTool(
        name="lookup",
        label="Search the evidence",
        description="Find evidence for one focused query.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=lookup,
    )


def test_agent_observes_tool_output_before_finishing() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am checking the strongest evidence first.",
                "tool": "lookup",
                "arguments": {"query": "agent loops"},
            },
            {
                "action": "finish",
                "update": "The evidence check is complete.",
                "final": {"answer": "The grounded result supports the answer."},
            },
        ]
    )
    calls: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Answer from observed evidence only.",
        tools=[_lookup_tool(calls)],
        plan_steps=["Inspect evidence", "Verify the answer"],
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Explain agent loops", context="No prior evidence")

    assert result.completed
    assert result.final == {"answer": "The grounded result supports the answer."}
    assert result.iterations == 2
    assert result.tool_calls == 1
    assert calls == [{"query": "agent loops"}]
    assert "Grounded result" in str(pool.prompts[1]["prompt"])
    system = str(pool.prompts[0]["system"])
    assert "describe the current work or result directly" in system
    assert "Do not deny hypothetical actions" in system
    assert "retries, guardrails, recovery paths" in system
    assert [event["event"] for event in events] == [
        "plan.created",
        "agent.update",
        "tool.started",
        "tool.completed",
        "agent.update",
        "checkpoint.started",
        "checkpoint.completed",
        "answer.completed",
    ]


@pytest.mark.parametrize("neutral", [False, True])
def test_completion_copy_is_overridden_only_by_the_server_validator(neutral: bool) -> None:
    """Model fields cannot replace ordinary verification or invent no-op receipts."""

    pool = _SequencePool(
        [
            {
                "action": "finish",
                "update": "Prepared the response.",
                "final": {"answer": "Original text remains.", "completion_label": "Model claim"},
            }
        ]
    )
    events: list[dict[str, object]] = []

    def validate(payload: dict[str, object]) -> AgentFinalValidation:
        return AgentFinalValidation(
            True,
            dict(payload),
            completion_label="Manuscript unchanged" if neutral else None,
            completion_detail="No source was changed." if neutral else None,
        )

    with agent_event_sink(events.append):
        result = AgentRunner(
            pool,
            workspace="manuscript",
            instructions="Inspect the source.",
            final_validator=validate,
        ).run(request="Review the passage")
    assert result.completed
    checkpoint = next(event for event in events if event["event"] == "checkpoint.completed")
    assert checkpoint["label"] == (
        "Manuscript unchanged" if neutral else "Requested outcome verified"
    )
    assert checkpoint["detail"] == (
        "No source was changed." if neutral else "The result covers the requested outcome."
    )


def test_agent_continues_after_final_validation_feedback() -> None:
    pool = _SequencePool(
        [
            {
                "action": "finish",
                "update": "I drafted the requested edit.",
                "final": {"reply": "Done", "edits": []},
            },
            {
                "action": "finish",
                "update": "I corrected the proposal with an exact edit.",
                "final": {
                    "reply": "Prepared for review.",
                    "edits": [{"find": "Old", "replace": "New"}],
                },
            },
        ]
    )

    def validate(payload: dict[str, object]) -> AgentFinalValidation:
        if not payload.get("edits"):
            return AgentFinalValidation(
                False,
                {},
                "The request requires at least one exact edit proposal.",
            )
        return AgentFinalValidation(True, dict(payload))

    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="manuscript",
        instructions="Prepare exact manuscript edits.",
        final_validator=validate,
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Replace the title")

    assert result.completed
    assert result.iterations == 2
    assert result.final["reply"] == "Prepared for review."
    assert "at least one exact edit" in str(pool.prompts[1]["prompt"])
    completion_checks = [
        event for event in events if event.get("tool") == "manuscript.verify_completion"
    ]
    assert [event["event"] for event in completion_checks] == [
        "checkpoint.started",
        "checkpoint.failed",
        "checkpoint.started",
        "checkpoint.completed",
    ]
    assert [event["lifecycle"] for event in completion_checks] == [
        "started",
        "failed",
        "started",
        "completed",
    ]
    assert completion_checks[0]["call_id"] == completion_checks[1]["call_id"]
    assert completion_checks[2]["call_id"] == completion_checks[3]["call_id"]
    assert completion_checks[0]["call_id"] != completion_checks[2]["call_id"]


def test_agent_blocks_an_identical_tool_call_and_reuses_observation() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Search once.",
                "tool": "lookup",
                "arguments": {"query": "same"},
            },
            {
                "action": "tool",
                "update": "Search the same thing again.",
                "tool": "lookup",
                "arguments": {"query": "same"},
            },
            {
                "action": "finish",
                "update": "I used the existing result.",
                "final": {"answer": "Complete"},
            },
        ]
    )
    calls: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Do not repeat completed work.",
        tools=[_lookup_tool(calls)],
    )

    events: list[dict[str, object]] = []
    with agent_event_sink(events.append):
        result = runner.run(request="Search safely")

    assert result.completed
    assert result.tool_calls == 1
    assert calls == [{"query": "same"}]
    assert result.observations[-1].output == {
        "status": "already_available",
        "previous_summary": "Found one grounded result",
    }
    assert "Continued with the result already available" in str(pool.prompts[2]["prompt"])
    lookup_events = [event for event in events if event.get("tool") == "research.lookup"]
    assert [event["event"] for event in lookup_events] == [
        "tool.started",
        "tool.completed",
    ]
    reuse = next(event for event in events if event.get("tool") == "research.reuse_observation")
    assert reuse["event"] == "checkpoint.progress"
    assert reuse["label"] == "Continued from the available result"
    assert reuse["detail"] == "Continued with the result already available."
    assert reuse["output"] == {"status": "ready"}


def test_tool_preflight_reuses_semantic_result_before_public_lifecycle() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Read the first source range.",
                "tool": "read_source",
                "arguments": {"offset": 0, "max_chars": 2_000},
            },
            {
                "action": "tool",
                "update": "Read the full source again.",
                "tool": "read_source",
                "arguments": {"offset": 0, "max_chars": 8_000},
            },
            {
                "action": "finish",
                "update": "The source evidence is available.",
                "final": {"answer": "Complete"},
            },
        ]
    )
    reads: list[dict[str, object]] = []
    source_complete = False

    def preflight(arguments: dict[str, object]) -> AgentToolResult | None:
        if source_complete:
            return AgentToolResult(
                output={
                    "status": "already_available",
                    "requested_offset": arguments["offset"],
                },
                summary="The requested source range is already available.",
            )
        return None

    def read_source(arguments: dict[str, object]) -> AgentToolResult:
        nonlocal source_complete
        reads.append(arguments)
        source_complete = True
        return AgentToolResult(
            output={"content": "grounded source", "complete": True},
            summary="Read the complete source.",
        )

    runner = AgentRunner(
        pool,
        workspace="manuscript",
        instructions="Use source evidence once.",
        tools=[
            AgentTool(
                name="read_source",
                description="Read a source range.",
                input_schema={"type": "object"},
                handler=read_source,
                preflight=preflight,
            )
        ],
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        result = runner.run(request="Use the source")

    assert result.completed
    assert result.tool_calls == 1
    assert reads == [{"offset": 0, "max_chars": 2_000}]
    assert result.observations[-1].executed is False
    assert "already available" in str(pool.prompts[2]["prompt"])
    assert not any(
        "full source again" in str(event.get("detail", "")).casefold() for event in events
    )
    tool_events = [event for event in events if event.get("tool") == "manuscript.read_source"]
    assert [event["event"] for event in tool_events] == [
        "tool.started",
        "tool.completed",
    ]


def test_tool_preflight_redirects_without_failed_public_event() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Read unrelated evidence.",
                "tool": "read_source",
                "arguments": {},
            },
            {
                "action": "finish",
                "update": "I followed the required repair step.",
                "final": {"answer": "Use the manuscript anchor."},
            },
        ]
    )
    handler_calls = 0

    def read_source(_arguments: dict[str, object]) -> AgentToolResult:
        nonlocal handler_calls
        handler_calls += 1
        return AgentToolResult({}, "must not run")

    runner = AgentRunner(
        pool,
        workspace="manuscript",
        instructions="Repair the manuscript anchor first.",
        tools=[
            AgentTool(
                name="read_source",
                description="Read source evidence.",
                input_schema={"type": "object"},
                handler=read_source,
                preflight=lambda _arguments: AgentToolResult(
                    output={"required_next_step": "read_file"},
                    summary="Inspect the current manuscript anchor before more evidence.",
                    success=False,
                    error_code="repair_step_required",
                ),
            )
        ],
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        result = runner.run(request="Rewrite a passage")

    assert result.completed
    assert result.tool_calls == 0
    assert handler_calls == 0
    assert result.observations[0].executed is False
    assert "read_file" in str(pool.prompts[1]["prompt"])
    assert not any(event["event"].startswith("tool.") for event in events)
    assert not any(
        "unrelated evidence" in str(event.get("detail", "")).casefold() for event in events
    )


def test_workspace_public_update_filter_applies_before_tool_events() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "The passage was already revised.",
                "tool": "lookup",
                "arguments": {"query": "anchor"},
            },
            {
                "action": "finish",
                "update": "The proposal is ready.",
                "final": {"answer": "Complete"},
            },
        ]
    )

    def filter_update(value: str) -> str:
        if "already revised" in value:
            return "Preparing the proposed revision for review."
        return value

    runner = AgentRunner(
        pool,
        workspace="manuscript",
        instructions="Do not claim unapplied edits.",
        tools=[_lookup_tool([])],
        public_update_filter=filter_update,
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        result = runner.run(request="Rewrite the passage")

    assert result.completed
    filtered_update = next(event for event in events if event["event"] == "agent.update")
    started = next(event for event in events if event["event"] == "tool.started")
    assert filtered_update["detail"] == "Preparing the proposed revision for review."
    assert started["detail"] == "Preparing the proposed revision for review."
    assert "already revised" not in json.dumps(events).casefold()


def test_agent_can_retry_an_identical_transient_read_failure() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "tool": "lookup",
                "arguments": {"query": "retry"},
            },
            {
                "action": "tool",
                "tool": "lookup",
                "arguments": {"query": "retry"},
            },
            {"action": "finish", "final": {"answer": "Recovered"}},
        ]
    )
    attempts = 0

    def lookup(_arguments: dict[str, object]) -> AgentToolResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return AgentToolResult(
                output={"error": "temporary connector failure"},
                summary="The evidence connector was temporarily unavailable.",
                success=False,
                error_code="connector_failed",
                retryable=True,
            )
        return AgentToolResult(output={"result": "grounded"}, summary="Recovered evidence.")

    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Retry only transient read failures.",
        tools=[
            AgentTool(
                name="lookup",
                description="Read evidence.",
                input_schema={"type": "object"},
                handler=lookup,
            )
        ],
    )

    result = runner.run(request="Read the evidence")

    assert result.completed
    assert result.tool_calls == 2
    assert attempts == 2


def test_agent_limit_event_does_not_expose_internal_counters() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Inspect the source.",
                "tool": "lookup",
                "arguments": {"query": "a"},
            },
            {
                "action": "tool",
                "update": "Inspect another source.",
                "tool": "lookup",
                "arguments": {"query": "b"},
            },
        ]
    )
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Inspect sources.",
        tools=[_lookup_tool([])],
        limits=AgentLimits(max_iterations=2, max_tool_calls=2),
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Inspect both sources")

    assert not result.completed
    public_event = events[-1]
    rendered = json.dumps(public_event).casefold()
    assert public_event["event"] == "checkpoint.progress"
    assert public_event["output"] == {"status": "needs_attention"}
    assert "limit" not in rendered
    assert "budget" not in rendered
    assert "tool_calls" not in rendered
    assert "iterations" not in rendered


def test_agent_redacts_runtime_controls_from_model_authored_updates() -> None:
    pool = _SequencePool(
        [
            {
                "action": "finish",
                "update": "The tool-call limit leaves two iterations remaining.",
                "final": {"answer": "Complete"},
            }
        ]
    )
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Return a safe result.",
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Answer safely")

    assert result.completed
    update = next(event for event in events if event["event"] == "agent.update")
    rendered = json.dumps(update).casefold()
    assert update["label"] == "Continuing with the next validated step."
    assert "tool-call" not in rendered
    assert "iteration" not in rendered
    assert "limit" not in rendered


def test_agent_sanitizes_plan_tool_detail_and_final_prose() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "The tool-call limit leaves one iteration.",
                "plan": ["Use the remaining tool-call budget", "Write the answer"],
                "tool": "lookup",
                "arguments": {"query": "safe"},
            },
            {
                "action": "finish",
                "update": "The evidence is ready.",
                "final": {
                    "answer": (
                        "The grounded result supports the answer. "
                        "I stopped because the tool-call limit was reached."
                    )
                },
            },
        ]
    )
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Return a safe result.",
        tools=[_lookup_tool([])],
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Answer safely")

    assert result.completed
    assert result.final == {"answer": "The grounded result supports the answer."}
    rendered = json.dumps(events).casefold()
    assert "tool-call limit" not in rendered
    assert "remaining tool-call" not in rendered
    plan = next(event for event in events if event["event"] == "plan.created")
    assert plan["steps"] == [
        "Continuing with the next validated step.",
        "Write the answer",
    ]
    started = next(event for event in events if event["event"] == "tool.started")
    assert started["detail"] == "Continuing with the next validated step."
    assert ":" not in str(started["call_id"])
    assert "iteration" not in started


@pytest.mark.parametrize(
    "disclosure",
    [
        "The iteration count is 4.",
        "Four iterations were used.",
        "The 4-iteration ceiling was reached.",
    ],
)
def test_agent_text_sanitizes_runtime_counter_variants(disclosure: str) -> None:
    assert safe_agent_text(disclosure, fallback="Continue safely.") == "Continue safely."


def test_agent_text_preserves_scientific_iteration_results() -> None:
    text = "The optimization algorithm converged in four iterations."

    assert safe_agent_text(text, fallback="Continue safely.") == text


def test_authorization_failure_never_exposes_runtime_feedback() -> None:
    pool = _SequencePool([{"action": "tool", "tool": "lookup", "arguments": {"query": "x"}}])
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Use authorized evidence only.",
        tools=[
            AgentTool(
                name="lookup",
                description="Read evidence.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=lambda _arguments: AgentToolResult({}, "unused"),
                authorizer=lambda _arguments: AgentToolAuthorization(
                    False,
                    "The tool budget limit was reached after four iterations.",
                ),
            )
        ],
        limits=AgentLimits(
            max_iterations=2,
            max_tool_calls=1,
            max_consecutive_failures=1,
        ),
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Read evidence")

    assert not result.completed
    failed = next(event for event in events if event["event"] == "tool.failed")
    rendered = json.dumps(failed).casefold()
    assert failed["label"] == "Action is not available for this request"
    assert "tool budget" not in rendered
    assert "iteration" not in rendered


def test_unknown_tools_stop_at_the_consecutive_failure_guard() -> None:
    pool = _SequencePool(
        [
            {"action": "tool", "tool": "missing", "arguments": {}},
            {"action": "tool", "tool": "missing", "arguments": {}},
            {"action": "tool", "tool": "missing", "arguments": {}},
            {"action": "finish", "final": {"answer": "must not run"}},
        ]
    )
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Use listed tools only.",
        limits=AgentLimits(
            max_iterations=10,
            max_tool_calls=8,
            max_consecutive_failures=3,
        ),
    )

    result = runner.run(request="Use a real tool")

    assert not result.completed
    assert result.stop_reason == "failure_limit"
    assert len(pool.prompts) == 3


def test_per_tool_guard_is_neutral_in_public_events() -> None:
    calls: list[dict[str, object]] = []
    tool = _lookup_tool(calls)
    tool = AgentTool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        handler=tool.handler,
        max_calls=1,
    )
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Inspect the first source.",
                "tool": "lookup",
                "arguments": {"query": "a"},
            },
            {
                "action": "tool",
                "update": "Inspect another source.",
                "tool": "lookup",
                "arguments": {"query": "b"},
            },
            {
                "action": "finish",
                "update": "I used the validated observation.",
                "final": {"answer": "Complete"},
            },
        ]
    )
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Inspect sources.",
        tools=[tool],
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Inspect sources")

    assert result.completed
    assert calls == [{"query": "a"}]
    guard = next(event for event in events if event.get("tool") == "research.validate_action")
    rendered = json.dumps(
        {
            "label": guard.get("label"),
            "detail": guard.get("detail"),
            "output": guard.get("output"),
        }
    ).casefold()
    assert guard["event"] == "checkpoint.progress"
    assert "limit" not in rendered
    assert "budget" not in rendered
    assert "tool_calls" not in rendered
    assert "iterations" not in rendered


def test_agent_compacts_old_observations_without_losing_recent_results() -> None:
    decisions: list[object] = []
    for index in range(4):
        decisions.append(
            {
                "action": "tool",
                "update": f"Inspect batch {index}.",
                "tool": "inspect",
                "arguments": {"batch": index},
            }
        )
    decisions.append(
        {
            "action": "finish",
            "update": "All batches are inspected.",
            "final": {"answer": "Complete"},
        }
    )
    pool = _SequencePool(decisions)

    def inspect(arguments: dict[str, object]) -> AgentToolResult:
        return AgentToolResult(
            output={"batch": arguments["batch"], "text": "x" * 1_200},
            summary=f"Inspected batch {arguments['batch']}",
        )

    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="manuscript",
        instructions="Inspect every requested batch.",
        tools=[
            AgentTool(
                name="inspect",
                description="Inspect one distinct batch.",
                input_schema={"type": "object"},
                handler=inspect,
            )
        ],
        limits=AgentLimits(
            max_iterations=6,
            max_tool_calls=5,
            observation_chars=2_400,
        ),
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Inspect four batches")

    assert result.completed
    assert any(event["event"] == "context.compacted" for event in events)
    compacted = next(event for event in events if event["event"] == "context.compacted")
    assert compacted["label"] == "Refreshed the working context"
    assert compacted["output"] == {"status": "ready"}
    assert "Inspected batch 3" in str(pool.prompts[-1]["prompt"])


def test_agent_propagates_cancellation_before_model_or_tool_work() -> None:
    pool = _SequencePool([])
    pool.cancel_check = lambda: True
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Work safely.",
    )

    with pytest.raises(LLMCancelledError):
        runner.run(request="Stop now")

    assert pool.prompts == []


def test_agent_fails_closed_after_repeated_invalid_decisions() -> None:
    pool = _SequencePool(["not json", "still not json"])
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Return structured actions.",
        limits=AgentLimits(
            max_iterations=5,
            max_tool_calls=2,
            max_consecutive_failures=2,
        ),
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Answer safely")

    assert not result.completed
    assert result.stop_reason == "failure_limit"
    assert result.final == {}
    assert len(pool.prompts) == 2
    progress_copy = [
        f"{event.get('label', '')} {event.get('detail', '')}".casefold()
        for event in events
        if event["event"] == "checkpoint.progress"
    ]
    assert progress_copy[:2] == [
        "continue the requested work preparing the next task-focused step.",
        "continue the requested work preparing the next task-focused step.",
    ]
    assert all(
        term not in " ".join(progress_copy)
        for term in ("invalid", "repair", "unverified", "malformed", "retry")
    )


def test_agent_reports_a_tool_exception_as_a_material_blocker_only() -> None:
    pool = _SequencePool(
        [
            {"action": "tool", "tool": "lookup", "arguments": {"query": "x"}},
            {"action": "finish", "final": {"answer": "The source was unavailable."}},
        ]
    )

    def unavailable(_arguments: dict[str, object]) -> AgentToolResult:
        raise RuntimeError("private connector stack trace")

    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Inspect the requested source.",
        tools=[
            AgentTool(
                name="lookup",
                description="Read one source.",
                input_schema={"type": "object"},
                handler=unavailable,
            )
        ],
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        result = runner.run(request="Inspect the source")

    assert result.completed
    failure = next(event for event in events if event["event"] == "tool.failed")
    public_copy = f"{failure['label']} {failure['detail']}".casefold()
    assert public_copy == (
        "this step is temporarily unavailable. this step is temporarily unavailable."
    )
    assert "failed safely" not in public_copy
    assert "unverified" not in public_copy
    assert "stack trace" not in json.dumps(failure).casefold()


def test_agent_emits_model_updated_plan_after_observing_work() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Inspect the evidence.",
                "plan": ["Inspect evidence", "Draft answer"],
                "tool": "lookup",
                "arguments": {"query": "planning"},
            },
            {
                "action": "finish",
                "update": "The result is ready.",
                "plan": ["Inspect evidence", "Verify answer", "Draft answer"],
                "final": {"answer": "Complete"},
            },
        ]
    )
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Keep the visible plan current.",
        tools=[_lookup_tool([])],
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Research and answer")

    assert result.completed
    plan_events = [event for event in events if str(event["event"]).startswith("plan.")]
    assert [event["event"] for event in plan_events] == [
        "plan.created",
        "plan.updated",
    ]
    assert plan_events[-1]["steps"] == [
        "Inspect evidence",
        "Verify answer",
        "Draft answer",
    ]


def test_agent_rejects_invalid_arguments_before_tool_execution() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Search the evidence.",
                "tool": "lookup",
                "arguments": {},
            },
            {
                "action": "finish",
                "update": "No unverified lookup was used.",
                "final": {"answer": "Could not search without a query."},
            },
        ]
    )
    calls: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Use valid tool arguments.",
        tools=[_lookup_tool(calls)],
    )

    result = runner.run(request="Search safely")

    assert result.completed
    assert result.tool_calls == 0
    assert calls == []
    assert "Missing required tool arguments" in str(pool.prompts[1]["prompt"])


def test_agent_validates_nested_tool_arguments_before_execution() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "tool": "compile_candidate",
                "arguments": {
                    "step_id": "step-1",
                    "edit": {"path": "paper.tex", "find": "old"},
                },
            },
            {"action": "finish", "final": {"answer": "No invalid edit ran."}},
        ]
    )
    calls: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="writer",
        instructions="Validate edits before compiling.",
        tools=[
            AgentTool(
                name="compile_candidate",
                description="Compile one exact edit.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string", "minLength": 1},
                        "edit": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "minLength": 1},
                                "find": {"type": "string", "minLength": 1},
                                "replace": {"type": "string"},
                            },
                            "required": ["path", "find", "replace"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["step_id", "edit"],
                    "additionalProperties": False,
                },
                handler=lambda arguments: (
                    calls.append(arguments)
                    or AgentToolResult(output={"compiled": True}, summary="Compiled")
                ),
                effect="staged",
            )
        ],
    )

    result = runner.run(request="Compile safely")

    assert result.completed
    assert calls == []
    assert "arguments.edit" in str(pool.prompts[1]["prompt"])
    assert "replace" in str(pool.prompts[1]["prompt"])


def test_agent_validates_array_items_and_bounds_before_execution() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "tool": "search_evidence",
                "arguments": {"query": "evidence", "kinds": ["paper", "secret"]},
            },
            {"action": "finish", "final": {"answer": "Rejected invalid kinds."}},
        ]
    )
    calls: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="writer",
        instructions="Search only supported evidence kinds.",
        tools=[
            AgentTool(
                name="search_evidence",
                description="Search supported evidence.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 100},
                        "kinds": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["paper", "dataset"]},
                            "maxItems": 2,
                            "uniqueItems": True,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=lambda arguments: (
                    calls.append(arguments) or AgentToolResult(output=[], summary="Searched")
                ),
            )
        ],
    )

    result = runner.run(request="Search safely")

    assert result.completed
    assert calls == []
    assert "arguments.kinds[1]" in str(pool.prompts[1]["prompt"])
    assert "allowed values" in str(pool.prompts[1]["prompt"])


def test_agent_tool_schema_rejects_unsupported_keywords_at_registration() -> None:
    with pytest.raises(ValueError, match="unsupported keyword.*oneOf"):
        AgentTool(
            name="unsafe_schema",
            description="Must never silently ignore its schema.",
            input_schema={"type": "object", "oneOf": [{"required": ["value"]}]},
            handler=lambda _arguments: AgentToolResult({}, "unused"),
        )


@pytest.mark.parametrize(
    "schema, keyword",
    [
        ({"type": "object", "properties": {"edit": {"properties": {}}}}, "properties"),
        ({"type": "object", "properties": {"items": {"items": {}}}}, "items"),
    ],
)
def test_agent_tool_schema_rejects_untyped_structural_constraints(
    schema: dict[str, object],
    keyword: str,
) -> None:
    with pytest.raises(ValueError, match=rf"{keyword} requires"):
        AgentTool(
            name="ambiguous_schema",
            description="Must enforce every registered constraint.",
            input_schema=schema,
            handler=lambda _arguments: AgentToolResult({}, "unused"),
        )


def test_agent_rejects_non_finite_large_integer_without_crashing() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "tool": "bounded_count",
                "arguments": {"count": 10**400},
            },
            {"action": "finish", "final": {"answer": "Rejected invalid count."}},
        ]
    )
    calls: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Use bounded numeric inputs.",
        tools=[
            AgentTool(
                name="bounded_count",
                description="Use one finite count.",
                input_schema={
                    "type": "object",
                    "properties": {"count": {"type": "integer", "minimum": 0, "maximum": 100}},
                    "required": ["count"],
                    "additionalProperties": False,
                },
                handler=lambda arguments: (
                    calls.append(arguments) or AgentToolResult(output={}, summary="Counted")
                ),
            )
        ],
    )

    result = runner.run(request="Count safely")

    assert result.completed
    assert calls == []
    assert "finite JSON number" in str(pool.prompts[1]["prompt"])


def test_write_tool_requires_and_obeys_server_authorization() -> None:
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Apply the requested change.",
                "tool": "write_value",
                "arguments": {"value": "new"},
            },
            {
                "action": "finish",
                "update": "I left the workspace unchanged.",
                "final": {"answer": "Confirmation is required."},
            },
        ]
    )
    calls: list[dict[str, object]] = []

    def write_value(arguments: dict[str, object]) -> AgentToolResult:
        calls.append(arguments)
        return AgentToolResult(output={"written": True}, summary="Value written")

    runner = AgentRunner(
        pool,
        workspace="dataset",
        instructions="Respect server permissions.",
        tools=[
            AgentTool(
                name="write_value",
                description="Write one value after explicit authorization.",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                handler=write_value,
                effect="write",
                authorizer=lambda _arguments: AgentToolAuthorization(
                    False,
                    "The user has not confirmed this write.",
                ),
            )
        ],
    )

    result = runner.run(request="Prepare this change")

    assert result.completed
    assert calls == []
    assert result.tool_calls == 0
    assert "not confirmed" in str(pool.prompts[1]["prompt"])


def test_nested_tool_arguments_are_isolated_across_authorization_and_execution() -> None:
    arguments = {
        "edit": {
            "path": "notes/review.md",
            "replacements": [{"find": "draft", "replace": "reviewed"}],
        }
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Apply the authorized nested edit.",
                "tool": "write_nested",
                "arguments": arguments,
            },
            {
                "action": "tool",
                "update": "Reuse the identical nested edit.",
                "tool": "write_nested",
                "arguments": arguments,
            },
            {
                "action": "finish",
                "update": "The authorized edit is complete.",
                "final": {"answer": "Complete"},
            },
        ]
    )
    authorized: list[dict[str, object]] = []
    prepared: list[dict[str, object]] = []
    handled: list[dict[str, object]] = []

    def copy_arguments(value: dict[str, object]) -> dict[str, object]:
        return json.loads(json.dumps(value))

    def authorize(value: dict[str, object]) -> AgentToolAuthorization:
        authorized.append(copy_arguments(value))
        edit = value["edit"]
        assert isinstance(edit, dict)
        edit["path"] = "unauthorized/auth-mutated.md"
        return AgentToolAuthorization(True)

    def preflight(value: dict[str, object]) -> AgentToolResult | None:
        prepared.append(copy_arguments(value))
        edit = value["edit"]
        assert isinstance(edit, dict)
        replacements = edit["replacements"]
        assert isinstance(replacements, list)
        replacement = replacements[0]
        assert isinstance(replacement, dict)
        replacement["replace"] = "preflight-mutated"
        return None

    def write_nested(value: dict[str, object]) -> AgentToolResult:
        handled.append(copy_arguments(value))
        return AgentToolResult(output={"written": True}, summary="Nested edit written")

    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="writer",
        instructions="Execute only the exact authorized nested payload.",
        tools=[
            AgentTool(
                name="write_nested",
                description="Write one bounded nested edit.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "edit": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "maxLength": 500},
                                "replacements": {
                                    "type": "array",
                                    "maxItems": 10,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "find": {"type": "string"},
                                            "replace": {"type": "string"},
                                        },
                                        "required": ["find", "replace"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["path", "replacements"],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["edit"],
                    "additionalProperties": False,
                },
                handler=write_nested,
                effect="write",
                authorizer=authorize,
                preflight=preflight,
            )
        ],
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Apply the exact nested edit")

    assert result.completed
    assert result.tool_calls == 1
    assert authorized == [arguments, arguments]
    assert prepared == [arguments, arguments]
    assert handled == [arguments]
    started = next(event for event in events if event["event"] == "tool.started")
    assert started["input"] == safe_event_value(arguments)
