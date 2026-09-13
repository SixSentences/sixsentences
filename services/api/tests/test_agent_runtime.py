"""Inspectable specialist-agent checkpoint contracts."""

import json
from types import SimpleNamespace

import pytest

from sixsentences_server.agent.events import (
    agent_event_sink,
    emit_agent_event,
    emit_change_events,
    safe_event_value,
)
from sixsentences_server.agent.runtime import (
    merge_missing_actions,
    review_action_coverage,
    review_execution_results,
)


class _FixedPool:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.prompts: list[dict[str, object]] = []

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        value = self.payload
        text = value if isinstance(value, str) else json.dumps(value)
        return SimpleNamespace(text=text)


class _SequencePool:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[dict[str, object]] = []

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        value = self.payloads.pop(0)
        text = value if isinstance(value, str) else json.dumps(value)
        return SimpleNamespace(text=text)


def test_every_specialist_tool_event_has_one_correlated_typed_lifecycle() -> None:
    events: list[dict[str, object]] = []
    tools = [
        "manuscript.propose_edits",
        "dataset.plan_operations",
        "interview.answer_from_transcript",
        "interview_study.propose_changes",
        "survey.propose_changes",
    ]

    with agent_event_sink(events.append):
        for tool in tools:
            emit_agent_event(
                "tool.started",
                tool=tool,
                label="Start",
                input={"resource": tool},
            )
            emit_agent_event("tool.progress", tool=tool, label="Continue")
            emit_agent_event(
                "tool.completed",
                tool=tool,
                label="Done",
                output={"status": "verified"},
            )

    assert len(events) == len(tools) * 3
    call_ids: set[str] = set()
    for index, tool in enumerate(tools):
        lifecycle = events[index * 3 : index * 3 + 3]
        assert [event["lifecycle"] for event in lifecycle] == [
            "started",
            "progress",
            "completed",
        ]
        assert {event["tool"] for event in lifecycle} == {tool}
        assert len({event["call_id"] for event in lifecycle}) == 1
        assert lifecycle[-1]["input"] == {"resource": tool}
        assert lifecycle[0]["output"] is None
        assert lifecycle[-1]["output"] == {"status": "verified"}
        call_ids.add(str(lifecycle[0]["call_id"]))
    assert len(call_ids) == len(tools)


def test_progress_first_and_terminal_only_tools_receive_distinct_call_ids() -> None:
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        emit_agent_event(
            "tool.progress",
            tool="survey.complete_plan",
            label="Repair the plan",
        )
        emit_agent_event(
            "tool.failed",
            tool="survey.complete_plan",
            label="Plan still incomplete",
        )
        emit_agent_event(
            "tool.completed",
            tool="survey.complete_plan",
            label="Independent server check",
        )

    assert [event["lifecycle"] for event in events] == [
        "progress",
        "failed",
        "completed",
    ]
    assert events[0]["call_id"] == events[1]["call_id"]
    assert events[2]["call_id"] != events[1]["call_id"]
    assert all("input" in event and "output" in event for event in events)


def test_checkpoints_close_correlated_calls_and_standalone_progress_is_terminal() -> None:
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        emit_agent_event(
            "checkpoint.progress",
            tool="research.reuse_observation",
            label="Continue from the available result",
        )
        emit_agent_event(
            "checkpoint.started",
            tool="research.inspect_results",
            label="Inspect the completed results",
        )
        emit_agent_event(
            "checkpoint.progress",
            tool="research.inspect_results",
            label="Check result coverage",
        )
        emit_agent_event(
            "checkpoint.completed",
            tool="research.inspect_results",
            label="Result review complete",
        )

    standalone, started, progress, completed = events
    assert standalone["lifecycle"] == "completed"
    assert [started["lifecycle"], progress["lifecycle"], completed["lifecycle"]] == [
        "started",
        "progress",
        "completed",
    ]
    assert len({started["call_id"], progress["call_id"], completed["call_id"]}) == 1
    started_ids = {event["call_id"] for event in events if event["lifecycle"] == "started"}
    terminal_ids = {
        event["call_id"] for event in events if event["lifecycle"] in {"completed", "failed"}
    }
    assert started_ids <= terminal_ids


def test_completion_merge_does_not_duplicate_aggregate_workspace_actions() -> None:
    guide = {"operation": "set_guide", "sections": [{"title": "Use", "questions": []}]}
    merged, added = merge_missing_actions(
        [guide],
        [
            {
                "operation": "set_guide",
                "sections": [{"title": "Different rewrite", "questions": []}],
            },
            {"operation": "rename", "title": "AI in higher education"},
        ],
        singleton_operations={"set_guide", "rename"},
    )

    assert merged == [guide, {"operation": "rename", "title": "AI in higher education"}]
    assert added == 1


def test_completion_merge_respects_aggregate_survey_replacement() -> None:
    replacement = {"operation": "replace_questions", "questions": [{"title": "Q1"}]}
    merged, added = merge_missing_actions(
        [replacement],
        [
            {"operation": "add_question", "title": "Q2"},
            {"operation": "set_title", "value": "AI survey"},
        ],
        singleton_operations={"replace_questions", "set_title"},
        dominant_operations={
            "replace_questions": {
                "add_question",
                "update_question",
                "delete_question",
                "replace_questions",
                "reorder_questions",
            }
        },
    )

    assert merged == [replacement, {"operation": "set_title", "value": "AI survey"}]
    assert added == 1


def test_completion_checkpoint_returns_only_missing_validatable_actions() -> None:
    pool = _FixedPool(
        {
            "status": "continue",
            "summary": "The requested title still needs an action.",
            "missing_actions": [
                {"operation": "set_title", "value": "Updated title"},
            ],
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_action_coverage(
            pool,
            workspace="survey",
            request="Rename the survey and add one question.",
            state_summary='{"title":"Old"}',
            validated_actions=[{"operation": "add_question", "title": "Q1"}],
            action_contract="set_title or add_question",
            language="en",
        )

    assert not review.complete
    assert review.missing_actions == [{"operation": "set_title", "value": "Updated title"}]
    assert [event["event"] for event in events] == [
        "checkpoint.started",
        "checkpoint.completed",
    ]
    assert events[-1]["output"] == {
        "status": "continue",
        "missing_actions": review.missing_actions,
    }
    system = str(pool.prompts[0]["system"])
    assert "describe the current work or result directly" in system
    assert "Do not deny hypothetical actions" in system


def test_completion_checkpoint_sanitizes_model_runtime_counters() -> None:
    pool = _FixedPool(
        {
            "status": "continue",
            "summary": "The iteration count is 4 and the tool budget limit was reached.",
            "missing_actions": [{"operation": "set_title", "value": "Updated"}],
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_action_coverage(
            pool,
            workspace="survey",
            request="Rename the survey.",
            state_summary='{"title":"Old"}',
            validated_actions=[],
            action_contract="set_title",
            language="en",
        )

    rendered = json.dumps(events).casefold()
    assert not review.complete
    assert review.summary == "The checkpoint found requested outcomes that still need an action."
    assert "iteration count" not in rendered
    assert "tool budget" not in rendered


def test_result_progress_hides_review_counters_and_model_runtime_summary() -> None:
    pool = _SequencePool(
        [
            {
                "status": "needs_attention",
                "summary": "Four iterations remain before the tool-call ceiling.",
                "answer": "The first draft needs refinement.",
            },
            {
                "status": "complete",
                "summary": "The result is grounded.",
                "answer": "The title was updated.",
            },
        ]
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="survey",
            request="Rename the survey.",
            initial_answer="Prepared.",
            executed_results=[{"operation": "set_title", "applied": True, "after": "Updated"}],
            language="en",
        )

    assert review.complete
    progress = next(event for event in events if event["event"] == "checkpoint.progress")
    rendered = json.dumps(progress).casefold()
    assert progress["output"] == {"status": "needs_attention"}
    assert "review_round" not in rendered
    assert "iterations" not in rendered
    assert "tool-call" not in rendered


def test_result_checkpoint_reports_validated_outputs_without_losing_answer() -> None:
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": "Two validated survey changes were applied.",
            "answer": "I updated the title and the first question.",
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="survey",
            request="Make the survey clearer.",
            initial_answer="Prepared.",
            executed_results=[
                {"operation": "set_title", "applied": True, "after": "Clear title"},
                {"operation": "update_question", "applied": True, "after": "Clear Q1"},
            ],
            language="en",
        )

    assert review.complete
    assert review.answer == "I updated the title and the first question."
    assert [event["event"] for event in events] == [
        "checkpoint.started",
        "checkpoint.completed",
    ]
    assert events[0]["label"] == "Review the completed work"
    assert events[0]["detail"] == (
        "Checking how the completed results address the requested outcome."
    )
    assert events[0]["call_id"] == events[1]["call_id"]
    system = str(pool.prompts[0]["system"])
    assert "Do not reveal hidden reasoning" in system
    assert "retries, guardrails, recovery paths" in system


def test_staged_proposal_skips_result_model_review() -> None:
    pool = _FixedPool(
        {
            "status": "needs_attention",
            "summary": "This response must not be used.",
            "answer": "This response must not be used.",
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="survey",
            request="Update the open survey.",
            initial_answer="I prepared six changes for your review.",
            executed_results=[
                {
                    "operation": "set_title",
                    "applied": False,
                    "staged": True,
                    "after": "AI in industry",
                },
                {
                    "operation": "replace_questions",
                    "applied": False,
                    "staged": True,
                    "after": ["Q1", "Q2"],
                },
            ],
            language="en",
        )

    assert review.complete
    assert review.answer == "I prepared six changes for your review."
    assert pool.prompts == []
    assert [event["event"] for event in events] == ["checkpoint.completed"]
    assert events[0]["output"] == {"status": "staged", "result_count": 2}


def test_verified_read_only_result_cannot_be_overturned_by_narration_model() -> None:
    pool = _FixedPool(
        {
            "status": "needs_attention",
            "summary": "The transcript operation was not executed.",
            "answer": "No verified transcript output is available.",
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="interview",
            request="Extract and summarize the most important points.",
            initial_answer="Alex described the experience as mixed.",
            executed_results=[
                {
                    "operation": "answer_from_transcript",
                    "applied": False,
                    "read_only": True,
                    "status": "completed",
                    "transcript_segments": 76,
                    "verified_quote_count": 1,
                    "verified_quotes": [
                        {
                            "segment": 18,
                            "text": "durchwachsen",
                            "verified": True,
                        }
                    ],
                }
            ],
            language="en",
        )

    assert review.complete
    assert "durchwachsen" in review.answer
    assert "No verified transcript output" not in review.answer
    assert len(pool.prompts) == 2
    assert [event["event"] for event in events] == [
        "checkpoint.started",
        "checkpoint.progress",
        "checkpoint.completed",
    ]
    prompt = str(pool.prompts[0]["prompt"])
    assert '"read_only": true' in prompt
    assert '"status": "completed"' in prompt
    assert '"text": "durchwachsen"' in prompt
    system = str(pool.prompts[0]["system"])
    assert "DID run successfully" in system
    assert "applied=false only means" in system


def test_transcript_review_failure_uses_exact_extractive_fallback() -> None:
    pool = _FixedPool("not valid json")

    review = review_execution_results(
        pool,
        workspace="interview",
        request="Summarize the interview.",
        initial_answer="An unchecked narrative draft.",
        executed_results=[
            {
                "operation": "answer_from_transcript",
                "applied": False,
                "read_only": True,
                "status": "completed",
                "verified_quote_count": 1,
                "verified_quotes": [
                    {
                        "segment": 18,
                        "speaker": "Alex",
                        "timestamp": "06:24",
                        "text": "Also ich muss sagen, so, sagen wir, durchwachsen.",
                        "verified": True,
                    }
                ],
            }
        ],
        language="de",
    )

    assert review.complete
    assert "An unchecked narrative draft" not in review.answer
    assert "Also ich muss sagen, so, sagen wir, durchwachsen." in review.answer
    assert "Alex · Seg. 18 · 06:24" in review.answer


def test_supported_transcript_narration_is_accepted_after_review() -> None:
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": "The response is grounded in the verified passage.",
            "answer": "Alex described the experience as mixed.",
        }
    )

    review = review_execution_results(
        pool,
        workspace="interview",
        request="Summarize the experience.",
        initial_answer="Prepared.",
        executed_results=[
            {
                "operation": "answer_from_transcript",
                "applied": False,
                "read_only": True,
                "status": "completed",
                "verified_quote_count": 1,
                "verified_quotes": [
                    {
                        "segment": 18,
                        "speaker": "Alex",
                        "text": "durchwachsen",
                        "verified": True,
                    }
                ],
            }
        ],
        language="en",
    )

    assert review.complete
    assert review.answer == "Alex described the experience as mixed."
    assert len(pool.prompts) == 1


@pytest.mark.parametrize(
    "summary",
    [
        "3 verifizierte Transkriptstellen stützen die Antwort.",
        "3 verifizierte Transkript-Passagen stützen die Antwort.",
        "3 verified transcript passages support the answer.",
        "3 verified quotes support the answer.",
    ],
)
def test_transcript_review_accepts_only_receipted_provenance_numbers(summary: str) -> None:
    quotes = [
        {
            "segment": segment,
            "speaker": "Pilot",
            "timestamp": timestamp,
            "text": text,
            "verified": True,
        }
        for segment, timestamp, text in (
            (3, "00:11", "Das hat sehr viel Spaß gemacht."),
            (5, "00:24", "Vor allem das gemeinsame Bauen."),
            (7, "00:42", "Mehr Details habe ich nicht genannt."),
        )
    ]
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": summary,
            "answer": (
                "In den Segmenten 3, 5 und 7 bleibt der Pilot bis auf das gemeinsame "
                "Bauen allgemein: „Das hat sehr viel Spaß gemacht.“ (00:11)"
            ),
        }
    )

    review = review_execution_results(
        pool,
        workspace="interview",
        request="Vergleiche die Aussagen mit Segmentnummern.",
        initial_answer="Entwurf.",
        executed_results=[
            {
                "operation": "answer_from_transcript",
                "read_only": True,
                "status": "completed",
                "verified_quote_count": 3,
                "verified_quotes": quotes,
            }
        ],
        language="de",
    )

    assert review.complete
    assert "Segmenten 3, 5 und 7" in review.answer
    assert "gemeinsame Bauen" in review.answer


@pytest.mark.parametrize(
    "answer",
    [
        "Segment 4 enthält den Grund.",
        "Die Segmente 3-7 enthalten alle konkrete Beispiele.",
        "Segments 3 through 7 all contain concrete examples.",
        "Die Segmente 3–7 enthalten alle konkrete Beispiele.",
        "3 Teilnehmende nannten diesen Grund.",
        "Die Aufgabe dauerte 00:11.",
        "3 verifizierte Transkriptstellen belegen diese Antwort.",
    ],
)
def test_transcript_review_does_not_turn_segment_ids_into_substantive_numbers(
    answer: str,
) -> None:
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": "Die Antwort ist geprüft.",
            "answer": answer,
        }
    )
    review = review_execution_results(
        pool,
        workspace="interview",
        request="Was wurde gesagt?",
        initial_answer="Entwurf.",
        executed_results=[
            {
                "operation": "answer_from_transcript",
                "read_only": True,
                "status": "completed",
                "verified_quote_count": 1,
                "verified_quotes": [
                    {
                        "segment": 3,
                        "timestamp": "00:11",
                        "text": "Das war hilfreich.",
                        "verified": True,
                    }
                ],
            }
        ],
        language="de",
    )

    assert review.complete
    assert answer not in review.answer
    assert "Verifizierte Transkriptstellen" in review.answer


def test_transcript_review_rejects_unsupported_numbers() -> None:
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": "The answer is complete.",
            "answer": "The study included 76 participants.",
        }
    )

    review = review_execution_results(
        pool,
        workspace="interview",
        request="Summarize the connection experience.",
        initial_answer="Prepared.",
        executed_results=[
            {
                "operation": "answer_from_transcript",
                "applied": False,
                "read_only": True,
                "status": "completed",
                "transcript_segments": 76,
                "verified_quote_count": 1,
                "verified_quotes": [
                    {
                        "segment": 18,
                        "text": "Die Verbindung hat nicht funktioniert.",
                        "verified": True,
                    }
                ],
            }
        ],
        language="en",
    )

    assert review.complete
    assert "76 participants" not in review.answer
    assert "Die Verbindung hat nicht funktioniert." in review.answer


@pytest.mark.parametrize(
    ("status", "expected_complete", "answer_fragment"),
    [
        ("empty", True, "no verifiable passage"),
        ("failed", False, "could not be verified safely"),
        ("completed", False, "could not be verified safely"),
    ],
)
def test_transcript_result_status_requires_verified_quotes(
    status: str,
    expected_complete: bool,
    answer_fragment: str,
) -> None:
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": "Everything succeeded.",
            "answer": "The participant strongly endorsed the product.",
        }
    )

    review = review_execution_results(
        pool,
        workspace="interview",
        request="Summarize the interview.",
        initial_answer="Unsafe draft.",
        executed_results=[
            {
                "operation": "answer_from_transcript",
                "applied": False,
                "read_only": True,
                "status": status,
                "verified_quote_count": 0,
                "verified_quotes": [],
            }
        ],
        language="en",
    )

    assert review.complete is expected_complete
    assert answer_fragment in review.answer
    assert "strongly endorsed" not in review.answer


def test_valid_manuscript_proposal_skips_result_model_review() -> None:
    pool = _FixedPool(
        {
            "status": "needs_attention",
            "summary": "This response must not be used.",
            "answer": "This response must not be used.",
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="manuscript",
            request="Rewrite the title and introduction.",
            initial_answer="I prepared the requested source edits for review.",
            executed_results=[
                {
                    "operation": "edit_source",
                    "applied": False,
                    "staged": True,
                    "applicable": True,
                },
                {
                    "operation": "compile_candidate",
                    "applied": False,
                    "status": "passed",
                },
            ],
            language="en",
        )

    assert review.complete
    assert review.answer == "I prepared the requested source edits for review."
    assert pool.prompts == []
    assert [event["event"] for event in events] == ["checkpoint.completed"]
    assert events[0]["output"] == {"status": "staged", "result_count": 2}


def test_invalid_manuscript_proposal_never_claims_changes_are_ready() -> None:
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": "This response must not be used.",
            "answer": "This response must not be used.",
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="manuscript",
            request="Rewrite the title and introduction.",
            initial_answer="I prepared the requested source edits for review.",
            executed_results=[
                {
                    "operation": "edit_source",
                    "applied": False,
                    "staged": False,
                    "applicable": False,
                },
                {
                    "operation": "compile_candidate",
                    "applied": False,
                    "status": "failed",
                },
            ],
            language="en",
        )

    assert not review.complete
    assert "not ready for review" in review.answer
    assert pool.prompts == []
    assert [event["event"] for event in events] == ["checkpoint.progress"]
    assert events[0]["label"] == "Changes need correction"
    assert events[0]["output"] == {"status": "needs_attention", "result_count": 2}


def test_failed_manuscript_compile_is_not_a_staged_success() -> None:
    pool = _FixedPool({"status": "complete", "summary": "unused", "answer": "unused"})
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="manuscript",
            request="Rewrite the introduction.",
            initial_answer="The edit is ready.",
            executed_results=[
                {
                    "operation": "edit_source",
                    "applied": False,
                    "staged": True,
                    "applicable": True,
                },
                {
                    "operation": "compile_candidate",
                    "applied": False,
                    "status": "failed",
                    "errors": [{"message": "Undefined control sequence"}],
                },
            ],
            language="en",
        )

    assert not review.complete
    assert pool.prompts == []
    assert [event["event"] for event in events] == ["checkpoint.progress"]
    assert events[0]["label"] == "Changes need correction"


def test_malformed_result_checkpoint_preserves_deterministic_work() -> None:
    pool = _FixedPool("not valid json")
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="dataset",
            request="Calculate missingness.",
            initial_answer="The validated calculation completed.",
            executed_results=[{"operation": "missingness", "result": {"missing": 3}}],
            language="en",
        )

    assert review.complete
    assert review.answer == "The validated calculation completed."
    assert events[-1]["label"] == "Validated results ready"


def test_result_review_model_receives_nested_provenance_values() -> None:
    pool = _FixedPool(
        {
            "status": "complete",
            "summary": "The nested provenance is present.",
            "answer": "The evidence was verified.",
        }
    )

    review = review_execution_results(
        pool,
        workspace="dataset",
        request="Summarize the evidence.",
        initial_answer="Prepared.",
        executed_results=[
            {
                "operation": "inspect_evidence",
                "result": {
                    "quotes": [
                        {
                            "segment": 18,
                            "speaker": "Alex",
                            "text": "durchwachsen",
                            "verified": True,
                        }
                    ]
                },
            }
        ],
        language="en",
    )

    assert review.complete
    prompt = str(pool.prompts[0]["prompt"])
    assert '"speaker": "Alex"' in prompt
    assert '"text": "durchwachsen"' in prompt
    assert '"verified": true' in prompt


def test_result_checkpoint_gets_one_bounded_refinement_round() -> None:
    pool = _SequencePool(
        [
            {
                "status": "needs_attention",
                "summary": "The report does not mention the unavailable row.",
                "answer": "Three rows were updated.",
            },
            {
                "status": "complete",
                "summary": "The validated limitation is now explicit.",
                "answer": "Three rows were updated. One unavailable row was left unchanged.",
            },
        ]
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        review = review_execution_results(
            pool,
            workspace="dataset",
            request="Update every valid row and tell me what could not be changed.",
            initial_answer="Prepared.",
            executed_results=[{"operation": "update_rows", "updated": 3, "unavailable": 1}],
            language="en",
        )

    assert review.complete
    assert review.answer.endswith("One unavailable row was left unchanged.")
    assert len(pool.prompts) == 2
    assert [event["event"] for event in events] == [
        "checkpoint.started",
        "checkpoint.progress",
        "checkpoint.completed",
    ]
    assert events[1]["output"] == {"status": "needs_attention"}


def test_agent_event_payloads_redact_secrets_and_remain_bounded() -> None:
    safe = safe_event_value(
        {
            "api_key": "must-not-leak",
            "nested": {"password": "must-not-leak", "value": "x" * 5_000},
            "rows": list(range(100)),
        }
    )

    assert safe["api_key"] == "[protected]"
    assert safe["nested"]["password"] == "[protected]"
    assert len(safe["nested"]["value"]) == 4_000
    assert len(safe["rows"]) == 30


def test_change_events_keep_plan_and_exact_safe_before_after_values() -> None:
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        emit_change_events(
            workspace="survey",
            changes=[
                {
                    "operation": "update_question",
                    "label": "Updated: AI use",
                    "before": {"title": "AI?", "type": "short_text"},
                    "after": {
                        "title": "How do you use AI?",
                        "type": "long_text",
                    },
                    "api_key": "must-not-leak",
                }
            ],
            applied=True,
        )

    assert [event["event"] for event in events] == [
        "agent.update",
        "change.completed",
    ]
    assert events[0]["output"] == {
        "change_count": 1,
        "operations": ["update_question"],
        "applied": True,
    }
    assert events[1]["before"] == {"title": "AI?", "type": "short_text"}
    assert events[1]["after"] == {
        "title": "How do you use AI?",
        "type": "long_text",
    }
    assert events[1]["input"]["api_key"] == "[protected]"
