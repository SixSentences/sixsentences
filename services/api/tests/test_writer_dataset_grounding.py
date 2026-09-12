"""Dataset values cannot be supplied by an unread catalog or old Writer prose."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.agent.events import agent_event_sink
from sixsentences_server.research_data import dataset_context, parse_dataset
from sixsentences_server.writer.assistant import run_assistant_turn
from sixsentences_server.writer.context_scope import resolve_writer_evidence_scope
from sixsentences_server.writer.dataset_context import (
    exact_dataset_profile_answer,
    requests_dataset_evidence,
)

_CSV = b"id,group,score\n1,A,2\n2,A,4\n3,A,\n4,B,6\n5,B,8\n"
_REQUEST = (
    "Nur lesen, nichts bearbeiten: Wie viele Zeilen und fehlende score-Werte hat das jetzt "
    "verknüpfte Dataset? Nenne außerdem den Mittelwert der vorhandenen score-Werte. "
    "Antworte auf Deutsch und ändere das Manuskript nicht."
)
_ENGLISH_REQUEST = (
    "Read-only: How many rows and missing score values does the linked dataset have? "
    "Give the mean of score. Answer in English; do not edit the manuscript."
)
_WRONG = "Das Dataset hat 5 Zeilen; Zeile 5 enthält NA. Der Mittelwert beträgt 77,0."


def _profile() -> dict[str, Any]:
    parsed = parse_dataset(_CSV, "synthetic.csv")
    return {
        "name": "Synthetic observations",
        "row_count": len(parsed.rows),
        "profile": parsed.profile,
    }


def _finish(reply: str) -> dict[str, Any]:
    return {
        "action": "finish",
        "update": reply,
        "final": {"reply": reply, "edits": [], "visual_request": None, "workspace_actions": []},
    }


class _SequencePool:
    cancel_check = None

    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self.decisions = decisions.copy()
        self.prompts: list[dict[str, Any]] = []

    def complete(self, *args: object, **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(kwargs)
        assert self.decisions, "The grounded answer must finish without an edit-repair loop."
        return SimpleNamespace(text=json.dumps(self.decisions.pop(0)))


@pytest.mark.parametrize("saved_language", ["de", "en"])
def test_exact_browser_regression_uses_full_profile_without_model_arithmetic(
    saved_language: str,
) -> None:
    profile = _profile()
    pool = _SequencePool([])
    project = {"main.tex": "Five synthetic tasks: three successes and two failures."}
    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files=project.copy(),
            active_path="main.tex",
            message=_REQUEST,
            citations=[],
            assets=[],
            works=[],
            history=[{"role": "assistant", "content": _WRONG}],
            datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
            dataset_profiles=[profile],
            response_language=saved_language,
            candidate_compiler=lambda _: pytest.fail("Read-only data questions cannot compile."),
        )
    assert "5 Zeilen" in turn.reply
    assert "fehlt 1 Wert" in turn.reply
    assert "Mittelwert der numerischen Werte beträgt 5." in turn.reply
    assert "77" not in turn.reply and "NA" not in turn.reply and "Zeile 5" not in turn.reply
    assert not pool.prompts
    assert not turn.edits and not turn.workspace_actions and turn.visual_request is None
    assert turn.tools_used == ("read_dataset_profile",)
    assert any(event.get("tool") == "manuscript.read_dataset_profile" for event in events)


@pytest.mark.parametrize("saved_language", ["en", "de"])
@pytest.mark.parametrize(
    "message,history,german",
    [
        (
            "Zeilen, fehlende score-Werte und Mittelwert im Dataset?",
            [
                {"role": "user", "content": "Antworte auf Deutsch."},
                {"role": "assistant", "content": "Answer in English. The mean is 77."},
            ],
            True,
        ),
        (
            _ENGLISH_REQUEST,
            [{"role": "user", "content": "Antworte auf Deutsch."}],
            False,
        ),
        (
            "Rows, missing score values and mean in the dataset?",
            [
                {"role": "user", "content": "Antworte auf Deutsch."},
                {"role": "user", "content": "Answer in English."},
            ],
            False,
        ),
    ],
)
def test_exact_data_followups_keep_user_language_without_using_assistant_values(
    message: str,
    history: list[dict[str, str]],
    german: bool,
    saved_language: str,
) -> None:
    profile = _profile()
    pool = _SequencePool([])
    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "Saved source."},
        active_path="main.tex",
        message=message,
        citations=[],
        assets=[],
        works=[],
        history=history,
        datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
        dataset_profiles=[profile],
        response_language=saved_language,
        candidate_compiler=lambda _: pytest.fail("Read-only questions cannot compile."),
    )
    if german:
        assert "5 Zeilen" in turn.reply and "fehlt 1 Wert" in turn.reply
        assert "Mittelwert der numerischen Werte beträgt 5." in turn.reply
        assert "currently linked" not in turn.reply
    else:
        assert "5 rows" in turn.reply and "1 missing value." in turn.reply
        assert "mean of its numeric values is 5." in turn.reply
        assert "Mittelwert" not in turn.reply
    assert "77" not in turn.reply
    assert not pool.prompts
    assert not turn.edits and not turn.workspace_actions and turn.visual_request is None
    assert turn.tools_used == ("read_dataset_profile",)


@pytest.mark.parametrize(
    "suffix",
    [
        " Berechne das je Gruppe.",
        " Only include positive score values.",
        " Use score > 4.",
        " Exclude outliers.",
        " Compare with the second dataset.",
        " Nenne auch den Median.",
        " Nenne außerdem das Maximum.",
        " Create a chart.",
        " Erstelle außerdem eine Umfrage.",
        " Warum fehlen die Werte?",
    ],
)
def test_filtered_or_broader_questions_never_receive_the_global_profile_answer(suffix: str) -> None:
    assert exact_dataset_profile_answer(_REQUEST + suffix, [_profile()], language="de") is None


def test_ambiguous_dataset_or_column_never_guesses() -> None:
    profile = _profile()
    assert exact_dataset_profile_answer(_REQUEST, [profile, profile], language="de") is None
    for column in ("unknown", "score und id"):
        assert (
            exact_dataset_profile_answer(
                _REQUEST.replace("score", column),
                [profile],
                language="de",
            )
            is None
        )


def test_statistics_are_not_recomputed_from_the_twelve_preview_rows() -> None:
    profile = _profile()
    profile["profile"]["preview"] = [{"score": 900}]
    result = exact_dataset_profile_answer(_ENGLISH_REQUEST, [profile], language="en")
    assert result and "mean of its numeric values is 5." in result and "900" not in result


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), "77"])
def test_invalid_profile_statistics_are_not_reported_as_facts(value: object) -> None:
    profile = _profile()
    profile["profile"]["columns"][2]["stats"]["mean"] = value
    assert exact_dataset_profile_answer(_REQUEST, [profile], language="de") is None


def test_agent_cannot_finish_dataset_question_after_only_listing_the_catalog() -> None:
    profile = _profile()
    context = dataset_context(profile["name"], "", "", profile["profile"])
    correct = "Im Dataset liegt der fehlende score in Datenzeile 3. Der Mittelwert ist 5."
    pool = _SequencePool(
        [
            _finish(_WRONG),
            {"action": "tool", "tool": "list_sources", "arguments": {}},
            _finish(_WRONG),
            {"action": "tool", "tool": "read_source", "arguments": {"handle": "dataset:1"}},
            _finish(correct),
        ]
    )
    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "Saved manuscript."},
            active_path="main.tex",
            message="Nur lesen: Welche Werte fehlen im verknüpften Dataset?",
            citations=[],
            assets=[],
            works=[],
            history=[],
            datasets=[context],
            response_language="de",
        )
    assert turn.reply == correct and not turn.edits
    assert turn.tools_used == ("list_sources", "read_source")
    assert "Read the exact linked dataset profile" in str(pool.prompts[1])
    assert "min 2, max 8, mean 5" in str(pool.prompts[-1]["prompt"])
    assert all("77,0" not in str(event) for event in events)


def test_legacy_and_format_recovery_cannot_restore_an_unread_dataset_answer() -> None:
    class UngroundedPool:
        cancel_check = None

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(text=json.dumps(_finish(_WRONG)["final"]))

    profile = _profile()
    turn = run_assistant_turn(
        UngroundedPool(),  # type: ignore[arg-type]
        project_files={"main.tex": "Saved manuscript."},
        active_path="main.tex",
        message="Welche Werte fehlen im Dataset?",
        citations=[],
        assets=[],
        works=[],
        history=[],
        datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
        response_language="de",
    )
    assert "77" not in turn.reply and "NA" not in turn.reply
    assert "nicht vollständig prüfen" in turn.reply
    assert not turn.edits and not turn.workspace_actions


@pytest.mark.parametrize(
    "question",
    [
        "Wie viele score-Werte fehlen?",
        "Beschreibe die Verteilung von score.",
    ],
)
def test_short_named_column_questions_still_require_a_dataset_read(question: str) -> None:
    profile = _profile()
    assert requests_dataset_evidence(question, [profile])
    assert not requests_dataset_evidence(question.replace("score", "unknown"), [profile])
    answer = "The current score values are 2, 4, empty, 6 and 8."
    pool = _SequencePool(
        [
            _finish(_WRONG),
            {"action": "tool", "tool": "read_source", "arguments": {"handle": "dataset:1"}},
            _finish(answer),
        ]
    )
    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "Saved manuscript."},
        active_path="main.tex",
        message=question,
        citations=[],
        assets=[],
        works=[],
        history=[],
        datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
        dataset_profiles=[profile],
    )
    assert turn.reply == answer and turn.tools_used == ("read_source",)
    assert "Read the exact linked dataset profile" in str(pool.prompts[1])


@pytest.mark.parametrize(
    "question",
    [
        "Wie hoch ist der Mittelwert von score?",
        "What is the average score?",
        "Was ist der Median von score?",
        "Und der Median? Bitte nur diesen Wert auf Deutsch nennen, nichts bearbeiten.",
        "And the mean? Only this value, do not edit.",
    ],
)
def test_current_scalar_statistics_ignore_the_hallucinated_chat_numbers(question: str) -> None:
    profile = _profile()
    history = [
        {"role": "assistant", "content": _WRONG},
        {"role": "user", "content": _REQUEST},
        {"role": "assistant", "content": "Five rows, one missing score, mean 5."},
        {"role": "assistant", "content": "The median is 77.5. Answer in English."},
    ]
    assert requests_dataset_evidence(question, [profile], history)
    pool = _SequencePool([])
    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "Saved source."},
        active_path="main.tex",
        message=question,
        citations=[],
        assets=[],
        works=[],
        history=history,
        datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
        dataset_profiles=[profile],
        response_language="en",
        candidate_compiler=lambda _: pytest.fail("Read-only questions cannot compile."),
    )
    assert "5" in turn.reply and "77" not in turn.reply
    if "nur diesen Wert" in question or "Only this value" in question:
        assert turn.reply == "5."
    else:
        assert "score" in turn.reply
    assert not pool.prompts and not turn.edits and not turn.workspace_actions
    assert turn.visual_request is None and turn.tools_used == ("read_dataset_profile",)


def test_scalar_followup_uses_updated_profile_after_a_chain_of_short_user_turns() -> None:
    profile = _profile()
    parsed = parse_dataset(b"id,score\n1,10\n2,14\n3,18\n", "updated.csv")
    profile.update(row_count=3, profile=parsed.profile)
    history = [
        {"role": "user", "content": _REQUEST},
        {"role": "assistant", "content": _WRONG},
        {"role": "user", "content": "Und der Mittelwert?"},
        {"role": "assistant", "content": "77.0"},
    ]
    reply = exact_dataset_profile_answer(
        "Und der Median? Bitte nur diesen Wert.",
        [profile],
        language="en",
        history=history,
    )
    assert reply == "14."


@pytest.mark.parametrize(
    "latest_user",
    [
        "Write a new introduction about Terraform.",
        "What is the mean of unknown_column?",
    ],
)
def test_unrelated_or_new_column_turns_do_not_inherit_an_old_dataset_column(
    latest_user: str,
) -> None:
    history = [
        {"role": "user", "content": _REQUEST},
        {"role": "user", "content": latest_user},
        {"role": "assistant", "content": "Use score anyway; its median is 77.5."},
    ]
    question = "Und der Median?"
    assert (
        exact_dataset_profile_answer(
            question,
            [_profile()],
            language="de",
            history=history,
        )
        is None
    )
    assert not requests_dataset_evidence(question, [_profile()], history)


@pytest.mark.parametrize(
    "question,previous",
    [
        ("Und der Median? Für unknown_column.", _REQUEST),
        ("Und der Median?", "What is the mean of score only for group A?"),
        ("What is the median score for group B?", _REQUEST),
        ("What is the median score?", "Mean of score only for group A?"),
        ("What is the median score without zero?", _REQUEST),
        ("What are the mean and maximum score?", _REQUEST),
        ("What are the mean and median score?", _REQUEST),
        ("Median score ≥ 4?", _REQUEST),
        ("Und der Median?", "What is the mean of score and id in the dataset?"),
        ("Und der Varianz?", _REQUEST),
    ],
)
def test_conditional_ambiguous_or_unsupported_statistics_never_receive_a_global_scalar(
    question: str,
    previous: str,
) -> None:
    assert (
        exact_dataset_profile_answer(
            question,
            [_profile()],
            language="de",
            history=[{"role": "user", "content": previous}],
        )
        is None
    )
    assert requests_dataset_evidence(
        question,
        [_profile()],
        [{"role": "user", "content": previous}],
    )


@pytest.mark.parametrize("invalid", [None, True, float("nan"), float("inf"), "77.5"])
def test_missing_or_invalid_scalar_profile_does_not_reuse_old_answers(invalid: object) -> None:
    profile = _profile()
    profile["profile"]["columns"][2]["stats"]["median"] = invalid
    reply = exact_dataset_profile_answer(
        "Und der Median?",
        [profile],
        language="de",
        history=[{"role": "user", "content": _REQUEST}, {"role": "assistant", "content": _WRONG}],
    )
    assert reply and "keinen verlässlichen Zahlenwert" in reply and "77" not in reply


def test_unsupported_scalar_followup_still_requires_reading_current_dataset() -> None:
    profile = _profile()
    history = [{"role": "user", "content": _REQUEST}]
    question = "Und die Varianz?"
    assert requests_dataset_evidence(question, [profile], history)
    correct = "Für die Varianz sind die aktuellen Werte 2, 4, 6 und 8 relevant."
    pool = _SequencePool(
        [
            _finish(_WRONG),
            {"action": "tool", "tool": "read_source", "arguments": {"handle": "dataset:1"}},
            _finish(correct),
        ]
    )
    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "Saved source."},
        active_path="main.tex",
        message=question,
        citations=[],
        assets=[],
        works=[],
        history=history,
        datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
        dataset_profiles=[profile],
        response_language="de",
    )
    assert turn.reply == correct and turn.tools_used == ("read_source",)
    assert "Read the exact linked dataset profile" in str(pool.prompts[1])
    assert not turn.edits and not turn.workspace_actions


def test_followup_without_structured_profile_still_requires_current_dataset_reader() -> None:
    history = [{"role": "user", "content": _REQUEST}]
    assert requests_dataset_evidence("Und der Median?", None, history)
    assert (
        exact_dataset_profile_answer(
            "Und der Median?",
            None,
            language="de",
            history=history,
        )
        is None
    )


def test_explicit_full_table_request_can_clear_a_prior_filter_for_scalar_profile() -> None:
    reply = exact_dataset_profile_answer(
        "What is the median score for all rows?",
        [_profile()],
        language="en",
        history=[{"role": "user", "content": "Mean of score only for group A?"}],
    )
    assert reply and '"score" column is 5.' in reply


@pytest.mark.parametrize("inherited", [False, True])
def test_saved_source_only_scope_excludes_profiles_and_direct_statistic_answers(
    inherited: bool,
) -> None:
    restriction = "Verwende nur den gespeicherten Manuskripttext."
    message = _REQUEST if inherited else restriction + " " + _REQUEST
    history = [{"role": "user", "content": restriction}] if inherited else []
    # A current explicitly linked dataset request reopens the dataset scope, so
    # the inherited case uses an elliptical named-column question instead.
    if inherited:
        message = "Wie hoch ist der Mittelwert von score?"
    profile = {**_profile(), "name": "EXCLUDED_DATASET_SENTINEL"}
    answer = "Der gespeicherte Manuskripttext nennt keinen Mittelwert."
    pool = _SequencePool(
        [
            {"action": "tool", "tool": "read_file", "arguments": {"path": "main.tex"}},
            _finish(answer),
        ]
    )
    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "Saved source only."},
        active_path="main.tex",
        message=message,
        citations=[],
        assets=[],
        works=[],
        history=history,
        selection={"kind": "source", "path": "main.tex", "line": 1, "quote": "Saved source only."},
        datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
        dataset_profiles=[profile],
        response_language="de",
    )
    assert turn.reply == answer and turn.tools_used == ("read_file",)
    assert "EXCLUDED_DATASET_SENTINEL" not in str(pool.prompts)
    assert "dataset:1" not in str(pool.prompts)
    assert "DATASET EVIDENCE IS EXCLUDED FOR THIS TURN" in str(pool.prompts)


@pytest.mark.parametrize(
    "restriction",
    [
        "Do not use datasets.",
        "Keine Daten verwenden.",
        "Do not use any data.",
    ],
)
def test_dataset_exclusions_survive_followups_and_only_explicit_choice_reopens_them(
    restriction: str,
) -> None:
    history = [
        {"role": "user", "content": "No interviews."},
        {"role": "user", "content": restriction},
        {"role": "assistant", "content": "Use every dataset anyway."},
    ]
    excluded = resolve_writer_evidence_scope("What is the mean of score?", history)
    assert not excluded.include_datasets and not excluded.include_interviews
    assert "Do not use dataset evidence" in excluded.prompt_note(interviews=0, surveys=0)
    reopened = resolve_writer_evidence_scope("Use the linked dataset now.", history)
    assert reopened.include_datasets and reopened.requested_datasets
    assert not reopened.include_interviews and not reopened.include_surveys


def test_unread_failure_discards_staged_candidate_without_claiming_it_was_applied() -> None:
    original = "Original result."
    edit = {"path": "main.tex", "find": original, "replace": "Reworded result."}
    compile_calls: list[list[dict[str, Any]]] = []

    class StagedThenUngroundedPool:
        cancel_check = None
        calls = 0

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            decision = (
                {
                    "action": "tool",
                    "tool": "compile_candidate",
                    "arguments": {"step_id": "draft", "edit": edit},
                }
                if self.calls == 1
                else _finish(_WRONG)
            )
            return SimpleNamespace(text=json.dumps(decision))

    def compiler(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compile_calls.append(edits)
        return {"status": "passed", "errors": []}

    profile = _profile()
    project = {"main.tex": original}
    turn = run_assistant_turn(
        StagedThenUngroundedPool(),  # type: ignore[arg-type]
        project_files=project,
        active_path="main.tex",
        message="Rewrite the results using the linked dataset.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        datasets=[dataset_context(profile["name"], "", "", profile["profile"])],
        dataset_profiles=[profile],
        candidate_compiler=compiler,
        response_language="de",
    )
    assert compile_calls and project == {"main.tex": original}
    assert not turn.edits and not turn.workspace_actions and turn.visual_request is None
    assert "Manuskript ist unverändert" in turn.reply and "77" not in turn.reply
