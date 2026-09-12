"""No-change receipts terminate safely without manufactured Writer edits."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.agent.events import agent_event_sink
from sixsentences_server.writer.assistant import (
    AssistantTurn,
    _writer_identity_receipts,
    run_assistant_turn,
)

_PASSAGE = (
    "These synthetic observations describe five fictional tasks. "
    "Three succeeded and two failed; these are not research findings."
)
_SOURCE = "\\section{Purpose}\nSynthetic QA only.\n\\section{Observations}\n" + _PASSAGE
_REQUEST = (
    "In main.tex rewrite only the Observations section as two concise sentences, "
    "preserving exactly five fictional tasks, three successes, and two failures. "
    "Explicitly label the observations synthetic, not research evidence. "
    "Do not change other sections or add sources. "
    "Return an edit proposal for me to apply."
)
_IDENTITY = {"path": "main.tex", "find": _PASSAGE, "replace": _PASSAGE}


class _SequencePool:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self.decisions = list(decisions)
        self.calls = 0
        self.cancel_check = None

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        self.calls += 1
        return SimpleNamespace(text=json.dumps(self.decisions.pop(0)))


def _finish(edits: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "action": "finish",
        "update": "Inspecting the proposed wording.",
        "final": {
            "reply": "Everything was edited and compiled successfully; awaiting your confirmation.",
            "edits": edits,
            "visual_request": None,
            "workspace_actions": [],
        },
    }


def _run(
    pool: _SequencePool,
    compiled: list[list[dict[str, Any]]],
    events: list[dict[str, Any]],
    *,
    fail_first_compile: bool = False,
) -> AssistantTurn:
    """Use only an inert sequence and compile receipt, never a real provider."""

    project = {"main.tex": _SOURCE}

    def compiler(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {
            "status": "failed" if fail_first_compile and len(compiled) == 1 else "passed",
            "errors": [],
            "log_tail": "",
        }

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files=project,
            active_path="main.tex",
            message=_REQUEST,
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compiler,
        )
    assert project == {"main.tex": _SOURCE}
    return turn


@pytest.mark.parametrize("via_compile_tool", [False, True])
def test_verified_identity_finishes_without_compilation_or_approval(via_compile_tool: bool) -> None:
    decisions = (
        [
            {
                "action": "tool",
                "update": "Checking the exact passage.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "observations", "edit": _IDENTITY},
            },
            _finish([]),
        ]
        if via_compile_tool
        else [_finish([_IDENTITY])]
    )
    pool = _SequencePool(decisions)
    compiled: list = []
    events: list = []
    turn = _run(pool, compiled, events)

    assert pool.calls == (2 if via_compile_tool else 1)
    assert compiled == []
    assert turn.edits == [] and turn.workspace_actions == [] and turn.visual_request is None
    assert turn.verification is None  # No fake compile-passed/failed badge.
    assert turn.reply == (
        "The proposed wording is identical to the existing passage. "
        "Nothing was changed; no approval is needed."
    )
    assert events[-1]["output"]["status"] == "unchanged"
    assert not any(event.get("label") == "Requested outcome verified" for event in events)
    assert not any(event.get("label") == "Manuscript proposal validated" for event in events)


def test_empty_model_claim_is_not_a_receipt_and_has_a_bounded_neutral_exit() -> None:
    pool = _SequencePool([_finish([]), _finish([])])
    compiled: list = []
    events: list = []
    turn = _run(pool, compiled, events)
    assert pool.calls == 2 and compiled == []
    assert turn.edits == []
    assert "No verified edit proposal is available" in turn.reply
    assert "identical" not in turn.reply and "awaiting" not in turn.reply
    assert events[-1]["event"] == "tool.failed"
    assert events[-1]["output"]["status"] == "unverified"


@pytest.mark.parametrize(
    "edits,source,user_request",
    [
        ([], _SOURCE, _REQUEST),
        ([{**_IDENTITY, "path": "another-tenant.tex"}], _SOURCE, _REQUEST),
        ([{**_IDENTITY, "find": "Invented", "replace": "Invented"}], _SOURCE, _REQUEST),
        ([_IDENTITY], _SOURCE + "\n" + _PASSAGE, _REQUEST),
        (
            [{**_IDENTITY, "find": "Synthetic QA only.", "replace": "Synthetic QA only."}],
            _SOURCE,
            _REQUEST,
        ),
        ([_IDENTITY], _SOURCE, "Rewrite the missing Conclusion section."),
        ([_IDENTITY], _SOURCE, "In missing.tex rewrite the Observations section."),
        ([_IDENTITY], _SOURCE, "Rewrite both the Purpose and Observations sections."),
        ([_IDENTITY, {**_IDENTITY, "replace": "A real change."}], _SOURCE, _REQUEST),
    ],
)
def test_unproven_identity_scope_fails_closed(edits: list, source: str, user_request: str) -> None:
    assert (
        _writer_identity_receipts(
            edits,
            project_files={"main.tex": source},
            active_path="main.tex",
            message=user_request,
        )
        == []
    )


def test_named_file_scope_cannot_be_replaced_by_another_project_file() -> None:
    assert (
        _writer_identity_receipts(
            [{**_IDENTITY, "path": "other.tex"}],
            project_files={"main.tex": _SOURCE, "other.tex": _SOURCE},
            active_path="main.tex",
            message=_REQUEST,
        )
        == []
    )


def test_identical_proposal_for_wrong_section_exits_without_success_claim() -> None:
    wrong = {"path": "main.tex", "find": "Synthetic QA only.", "replace": "Synthetic QA only."}
    pool = _SequencePool([_finish([wrong])])
    events: list = []
    turn = _run(pool, [], events)
    assert pool.calls == 1 and turn.edits == []
    assert events[-1]["output"]["status"] == "unverified"


def test_identity_cannot_hide_a_failed_pending_edit() -> None:
    real = {**_IDENTITY, "replace": _PASSAGE + " This is synthetic QA data."}
    repaired = {**_IDENTITY, "replace": _PASSAGE.replace("describe", "summarize")}
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Validating the real edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "observations", "edit": real},
            },
            _finish([_IDENTITY]),
            {
                "action": "tool",
                "update": "Retrying the same pending edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "observations", "edit": repaired},
            },
            _finish([]),
        ]
    )
    compiled: list = []
    events: list = []
    turn = _run(pool, compiled, events, fail_first_compile=True)
    assert pool.calls == 4 and len(compiled) == 2
    assert len(turn.edits) == 1 and turn.edits[0]["replace"] == repaired["replace"]
    assert turn.verification is not None and turn.verification["status"] == "passed"
    assert not any((event.get("output") or {}).get("status") == "unchanged" for event in events)


def test_identity_cannot_discard_an_already_staged_real_edit() -> None:
    real = {**_IDENTITY, "replace": _PASSAGE + " This is synthetic QA data."}
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Validating the real edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "observations", "edit": real},
            },
            _finish([_IDENTITY]),
            _finish([]),
        ]
    )
    compiled: list = []
    events: list = []
    turn = _run(pool, compiled, events)
    assert pool.calls == 3 and len(compiled) == 1
    assert len(turn.edits) == 1 and turn.edits[0]["replace"] == real["replace"]
    assert turn.verification is not None and turn.verification["status"] == "passed"
    assert not any((event.get("output") or {}).get("status") == "unchanged" for event in events)


def test_selected_local_identity_uses_the_same_no_change_boundary() -> None:
    pool = _SequencePool([_finish([_IDENTITY])["final"]])
    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": _SOURCE},
        active_path="main.tex",
        message="Rewrite this selected paragraph, preserving its meaning.",
        selection={"kind": "source", "path": "main.tex", "quote": _PASSAGE, "line": 4},
        citations=[],
        assets=[],
        works=[],
        history=[],
    )
    assert pool.calls == 1 and turn.edits == [] and turn.verification is None
    assert "identical to the existing passage" in turn.reply
