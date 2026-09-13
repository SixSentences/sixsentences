"""Source-only regressions for Writer continuity and linked-evidence ordinals."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sixsentences_server.writer.assistant import _writer_evidence_tools, run_assistant_turn
from sixsentences_server.writer.context_scope import resolve_writer_evidence_scope
from sixsentences_server.writer.interview_context import render_interview_evidence
from sixsentences_server.writer.survey_context import render_survey_evidence


@pytest.mark.parametrize(
    "correction",
    ["No interviews.", "Keine Interviews.", "Do not use the interview."],
)
def test_terse_followup_keeps_latest_source_exclusion(correction: str) -> None:
    history = [
        {"role": "user", "content": "Use the linked interviews for the results."},
        {"role": "assistant", "content": "I drafted an interview-based paragraph."},
        {"role": "user", "content": correction},
        {"role": "assistant", "content": "Use the interviews anyway."},
    ]

    scope = resolve_writer_evidence_scope("Make the paragraph clearer.", history)

    assert not scope.include_interviews
    # Removing the previously requested interview does not authorize substitution
    # with a different evidence family that was not requested.
    assert not scope.include_surveys
    assert not scope.requested_interviews
    assert correction in scope.retrieval_query
    note = scope.prompt_note(interviews=0, surveys=1)
    assert "Do not use interview evidence, including excerpts from older turns" in note
    assert "all linked evidence is available" not in note


def test_source_correction_survives_more_than_twelve_user_followups() -> None:
    correction = "Use the linked surveys, not the interviews."
    history = [
        {"role": "user", "content": "Use the interviews for the results."},
        {"role": "user", "content": correction},
        *[{"role": "user", "content": f"Make paragraph {index} clearer."} for index in range(24)],
    ]

    scope = resolve_writer_evidence_scope("Continue.", history)

    assert scope.include_surveys and scope.requested_surveys
    assert not scope.include_interviews
    assert correction in scope.retrieval_query
    assert "no survey is linked" in scope.prompt_note(interviews=0, surveys=0)


def test_current_source_instruction_overrides_inherited_exclusion() -> None:
    scope = resolve_writer_evidence_scope(
        "Use the linked interviews now.",
        [{"role": "user", "content": "No interviews."}],
    )

    assert scope.include_interviews and scope.requested_interviews
    assert not scope.include_surveys
    assert scope.retrieval_query == "Use the linked interviews now."


def test_excluding_both_primary_source_families_does_not_reenable_them_on_continue() -> None:
    scope = resolve_writer_evidence_scope(
        "Continue.",
        [{"role": "user", "content": "No interviews. No surveys."}],
    )

    assert not scope.include_interviews and not scope.include_surveys
    note = scope.prompt_note(interviews=0, surveys=0)
    assert "Do not use interview evidence" in note
    assert "Do not use survey evidence" in note


@pytest.mark.parametrize("second_exclusion_is_current", [False, True])
def test_separate_source_exclusions_accumulate(second_exclusion_is_current: bool) -> None:
    history = [{"role": "user", "content": "No interviews."}]
    if not second_exclusion_is_current:
        history.append({"role": "user", "content": "No surveys."})
    scope = resolve_writer_evidence_scope(
        "No surveys." if second_exclusion_is_current else "Continue.",
        history,
    )

    assert not scope.include_interviews and not scope.include_surveys
    assert not scope.requested_interviews and not scope.requested_surveys


def test_explicit_positive_choice_reopens_only_the_named_source_family() -> None:
    history = [
        {"role": "user", "content": "No interviews."},
        {"role": "user", "content": "No surveys."},
        {"role": "user", "content": "Use the linked surveys now."},
    ]
    scope = resolve_writer_evidence_scope("Continue.", history)

    assert scope.include_surveys and scope.requested_surveys
    assert not scope.include_interviews and not scope.requested_interviews


def test_single_source_tool_reads_keep_the_same_ordinals_as_the_full_catalog() -> None:
    interviews = [
        {
            "interview_id": f"interview-{index}",
            "title": f"Synthetic interview {index}",
            "mode": "transcript",
            "analysis_text": f"Analysis for participant {index}.",
            "passages": [
                {
                    "segment": 7,
                    "speaker": f"P{index}",
                    "start_ms": 60_000,
                    "end_ms": 65_000,
                    "text": f"Synthetic statement {index}.",
                }
            ],
        }
        for index in (1, 2)
    ]
    surveys = [
        {
            "survey_id": f"survey-{index}",
            "title": f"Synthetic survey {index}",
            "mode": "responses",
            "instrument_text": f"Questionnaire {index}.",
            "summary_text": "Submitted responses: 1",
            "response_rows": [
                {
                    "response_id": f"response-{index}",
                    "answers": [{"question": "What helped?", "value": f"Support {index}."}],
                }
            ],
        }
        for index in (1, 2)
    ]
    tools = {
        tool.name: tool
        for tool in _writer_evidence_tools(
            works=[],
            citations=[],
            attachments=[],
            datasets=[],
            interview_evidence=interviews,
            survey_evidence=surveys,
            citation_receipts=set(),
        )
    }

    for kind, contexts, render, anchor in (
        ("interview", interviews, render_interview_evidence, "I2:S7"),
        ("survey", surveys, render_survey_evidence, "Q2:R1"),
    ):
        result = tools["read_source"].handler({"handle": f"{kind}:{kind}-2"})
        assert result.success
        content = str(cast(dict[str, Any], result.output)["content"])
        assert f"[{anchor} |" in content
        assert content in render(contexts)
        assert "SOURCE I1:" not in content
        assert "SOURCE Q1:" not in content

    # A stale handle from chat cannot grant access to an unlinked source.
    missing = tools["read_source"].handler({"handle": "interview:removed-source"})
    assert not missing.success


def test_writer_context_renderer_receives_current_request_and_current_saved_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[dict[str, Any]] = []
    prompts: list[dict[str, Any]] = []
    message = "Rewrite this selected sentence clearly."
    current_source = "The current saved result remains uncertain."
    history = [{"role": "assistant", "content": "An old proposed result was certain."}]

    def capture_context(
        supplied_history: object,
        *,
        pool: object,
        local_edit: bool,
        current_request: str = "",
    ) -> str:
        rendered.append(
            {
                "history": supplied_history,
                "local_edit": local_edit,
                "current_request": current_request,
            }
        )
        return "Prior assistant prose is an unaccepted proposal."

    class LocalPool:
        cancel_check = None

        def complete(self, *args: object, **kwargs: Any) -> SimpleNamespace:
            prompts.append(kwargs)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "reply": "The current sentence already preserves that uncertainty.",
                        "edits": [
                            {"path": "main.tex", "find": current_source, "replace": current_source}
                        ],
                        "visual_request": None,
                        "workspace_actions": [],
                    }
                )
            )

    monkeypatch.setattr(
        "sixsentences_server.writer.assistant.render_model_aware_context",
        capture_context,
    )
    run_assistant_turn(
        LocalPool(),  # type: ignore[arg-type]
        project_files={"main.tex": current_source},
        active_path="main.tex",
        message=message,
        citations=[],
        assets=[],
        works=[],
        history=history,
        selection={"kind": "source", "path": "main.tex", "line": 1, "quote": current_source},
    )

    assert rendered == [{"history": history, "local_edit": True, "current_request": message}]
    assert current_source in prompts[0]["prompt"]
    assert "HIGHLIGHTED SOURCE SELECTION IN main.tex" in prompts[0]["prompt"]
    assert "authoritative manuscript and evidence state" in prompts[0]["system"]
    assert "not proof that a proposed edit was accepted" in prompts[0]["system"]
