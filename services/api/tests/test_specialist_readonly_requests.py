"""Explicit current inspection cannot revive old or generated edit actions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.datasets import service as dataset
from sixsentences_server.surveys import service as survey
from sixsentences_server.voice import agent as study


@pytest.mark.parametrize("module", [survey, dataset, study])
@pytest.mark.parametrize(
    "user_request",
    [
        "Nur prüfen, nichts ändern: Welche Aussagen sind belegt?",
        "Just review this; do not change anything.",
        "Read-only: are the current results correct?",
    ],
)
@pytest.mark.parametrize("unsolicited_action", [False, True])
def test_readonly_return_precedes_deterministic_edits_and_coverage(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    user_request: str,
    unsolicited_action: bool,
) -> None:
    calls: list[dict[str, Any]] = []
    action = {
        survey: {"operation": "set_title", "value": "Unexpected title"},
        dataset: {"operation": "set_profile", "changes": {"name": "Unexpected name"}},
        study: {"operation": "rename", "title": "Unexpected title"},
    }[module]

    def complete(_pool: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        assert "return actions=[] and workspace_actions=[]" in kwargs["system"]
        return SimpleNamespace(
            text=json.dumps(
                {
                    "answer": (
                        "I changed the title."
                        if unsolicited_action
                        else "Only the supplied evidence is supported."
                    ),
                    "actions": [action] if unsolicited_action else [],
                    "workspace_actions": [],
                }
            )
        )

    monkeypatch.setattr(module, "request_structured_completion", complete)
    monkeypatch.setattr(module, "structured_recovery_pool", lambda pool: pool)
    monkeypatch.setattr(module, "render_model_aware_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        module,
        "review_action_coverage",
        lambda *_args, **_kwargs: pytest.fail("a read-only answer must not enter edit completion"),
    )
    history = [{"role": "user", "content": "Rename this and add three questions."}]
    common = {"request": user_request, "history": history, "language": "en"}
    if module is survey:
        monkeypatch.setattr(
            survey,
            "_question_wording_updates",
            lambda *_args: pytest.fail("a read-only answer must not synthesize wording changes"),
        )
        turn = survey.run_survey_agent(
            object(),
            title="Existing title",
            description="",
            status="draft",
            questions=[],
            settings={},
            responses=[],
            **common,
        )
    elif module is study:
        monkeypatch.setattr(
            study,
            "_requested_guide_shape",
            lambda *_args: pytest.fail("a read-only answer must not repair guide counts"),
        )
        turn = study.run_study_agent(object(), study={"title": "Existing", "guide": {}}, **common)
    else:
        monkeypatch.setattr(
            dataset,
            "_deterministic_profile_changes",
            lambda *_args: pytest.fail("a read-only answer must not synthesize metadata changes"),
        )
        turn = dataset.run_dataset_agent(
            object(),
            name="Existing",
            description="",
            provenance="",
            license="",
            format="csv",
            row_count=1,
            profile={"columns": [{"name": "score", "type": "number"}]},
            versions=[],
            **common,
        )
    assert turn.actions == [] and turn.workspace_actions == []
    assert len(calls) == 1
    if unsolicited_action:
        assert "I changed" not in turn.answer
        assert "unchanged" in turn.answer
    else:
        assert turn.answer == "Only the supplied evidence is supported."


@pytest.mark.parametrize("module", [survey, dataset, study])
@pytest.mark.parametrize(
    "user_request",
    [
        "Ja mach",
        "Rename this to New title",
        "Make the wording clearer, do not change options.",
        "Add one question but do not publish the survey.",
        "Rewrite the title; do not change anything else.",
        "Rename the dataset; don't modify anything other than its name.",
    ],
)
def test_unrecognized_or_scoped_edit_request_does_not_become_global_readonly(
    module: Any,
    user_request: str,
) -> None:
    assert not module._READ_ONLY_REQUEST.search(user_request)


@pytest.mark.parametrize("user_request", ["Do not rename it to Test", "Don't rename it to Test"])
def test_negated_rename_cannot_be_synthesized_from_an_empty_model_plan(user_request: str) -> None:
    assert dataset._deterministic_profile_changes(user_request) == {}
    assert dataset._negated_profile_fields(user_request) == {"name"}


def test_local_rename_restriction_keeps_requested_calculation_and_other_metadata() -> None:
    request = "Compute the median but do not rename it to Test. Change the description to Checked."
    excluded = dataset._negated_profile_fields(request)
    analysis = {
        "operation": "run_analysis",
        "kind": "descriptive",
        "definition": {"column": "score"},
    }
    assert dataset._deterministic_profile_changes(request) == {"description": "Checked"}
    assert dataset._without_negated_profile_changes(
        [
            analysis,
            {"operation": "set_profile", "changes": {"name": "Test", "description": "Checked"}},
        ],
        excluded,
    ) == [analysis, {"operation": "set_profile", "changes": {"description": "Checked"}}]


def test_postposed_german_rename_negation_removes_only_name_changes() -> None:
    request = "Berechne den Median. Benenne den Datensatz nicht um."
    analysis = {
        "operation": "run_analysis",
        "kind": "descriptive",
        "definition": {"column": "score"},
    }
    excluded = dataset._negated_profile_fields(request)
    assert excluded == {"name"}
    assert dataset._without_negated_profile_changes(
        [
            analysis,
            {"operation": "set_profile", "changes": {"name": "New name"}},
        ],
        excluded,
    ) == [analysis]
