"""Focused contracts for iterative Quick Answer and Manuscript prompts."""

from sixsentences_server.chat.service import (
    CHAT_SYSTEM,
    RESEARCH_TOOL_HARD_LIMIT,
    STANDARD_TOOL_HARD_LIMIT,
    TOOL_DECISION_SYSTEM,
)
from sixsentences_server.pipeline.ask import _ASSESS_SYSTEM, _PLAN_SYSTEM, ASK_SYSTEM
from sixsentences_server.writer.assistant import _DIRECT_RESPONSE_SYSTEM, _SYSTEM


def test_quick_answer_synthesis_cannot_request_another_tool() -> None:
    """Final synthesis describes capabilities without inventing execution."""

    assert "At this synthesis stage you cannot launch another tool" in CHAT_SYSTEM
    assert "Do not emit suggest_followups syntax" in CHAT_SYSTEM
    assert "unless its completed result is supplied below" in CHAT_SYSTEM
    assert "offer follow-up suggestions via the suggest_followups tool" not in CHAT_SYSTEM


def test_quick_answer_router_uses_iterative_coverage_not_early_finish_defaults() -> None:
    """One router JSON object is one loop decision, not the whole turn."""

    assert "At this iteration, choose ONE next tool" in TOOL_DECISION_SYSTEM
    assert "runtime may ask you again after the observation" in TOOL_DECISION_SYSTEM
    assert "at least three distinct evidence-search passes" in TOOL_DECISION_SYSTEM
    assert "Finish only when the collected material covers the requested outcome" in (
        TOOL_DECISION_SYSTEM
    )
    assert "tool budget" not in TOOL_DECISION_SYSTEM
    assert "Remaining budget" not in TOOL_DECISION_SYSTEM
    assert STANDARD_TOOL_HARD_LIMIT >= 32
    assert RESEARCH_TOOL_HARD_LIMIT >= 48
    assert "When in doubt, answer" not in TOOL_DECISION_SYSTEM
    assert "Use a third angle only when" not in TOOL_DECISION_SYSTEM


def test_ask_plan_and_reflection_are_explicitly_adaptive() -> None:
    """The initial plan and later sufficiency check do not claim completion."""

    assert "initial adaptive preparation plan" in _PLAN_SYSTEM
    assert "not a one-shot execution or a claim that the answer is complete" in _PLAN_SYSTEM
    assert "inside an iterative research loop" in _ASSESS_SYSTEM
    assert "may be called again after the next search" in _ASSESS_SYSTEM
    assert "not merely because one plausible title was retrieved" in _ASSESS_SYSTEM
    assert "this final-answer call cannot launch another tool" in ASK_SYSTEM
    assert "what this quick scan cannot settle" not in ASK_SYSTEM


def test_manuscript_prompt_separates_catalog_tools_and_terminal_payload() -> None:
    """The Writer must inspect source before proposing an iterative final."""

    assert "initial context is a file manifest" in _SYSTEM
    assert "not the complete contents" in _SYSTEM
    assert "only the value inside the controller's final field" in _SYSTEM
    assert "Do not return it as a bare one-shot response" in _SYSTEM
    assert "does not grant access to project files or evidence" in _DIRECT_RESPONSE_SYSTEM
    assert "You can inspect the manuscript's complete multi-file" not in _SYSTEM
