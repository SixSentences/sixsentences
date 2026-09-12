"""Focused contracts for the iterative manuscript agent migration."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sixsentences_server.agent.events import agent_event_sink, safe_event_value
from sixsentences_server.agent.loop import AgentObservation
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.llm.base import LLMCancelledError
from sixsentences_server.writer.assistant import (
    _clarify_pending_writer_review,
    _manuscript_structure_report,
    _project_only_rewrite_request,
    _remaining_cleanup_markers,
    _requests_manuscript_mutation,
    _requests_template_cleanup,
    _writer_agent_tools,
    _writer_evidence_tools,
    _writer_public_update,
    _WriterRepairState,
    is_local_writer_edit_request,
    run_assistant_turn,
)


class _SequencePool:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self.decisions = list(decisions)
        self.prompts: list[dict[str, object]] = []
        self.cancel_check: Callable[[], bool] | None = None

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        return SimpleNamespace(text=json.dumps(self.decisions.pop(0)))


class _FixedPool:
    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        self.prompts: list[dict[str, object]] = []
        self.cancel_check: Callable[[], bool] | None = None

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        return SimpleNamespace(text=json.dumps(self.decision))


class _RawSequencePool:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[dict[str, object]] = []
        self.cancel_check: Callable[[], bool] | None = None

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        return SimpleNamespace(text=self.responses.pop(0))


class _PrefixThenFixedPool:
    def __init__(
        self,
        decisions: list[dict[str, Any]],
        fallback: dict[str, Any],
    ) -> None:
        self.decisions = list(decisions)
        self.fallback = fallback
        self.prompts: list[dict[str, object]] = []
        self.cancel_check: Callable[[], bool] | None = None

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        decision = self.decisions.pop(0) if self.decisions else self.fallback
        return SimpleNamespace(text=json.dumps(decision))


@pytest.mark.parametrize(
    "message",
    [
        "Replace the results placeholder with the final paragraph.",
        "Update the title in this template.",
        "Ändere den Platzhalter im Ergebnisteil.",
    ],
)
def test_writer_single_placeholder_edits_do_not_trigger_full_template_cleanup(
    message: str,
) -> None:
    assert _requests_template_cleanup(message) is False


def test_writer_complete_report_cleanup_is_detected() -> None:
    for message in (
        "Write a complete report and remove all boilerplate from the template.",
        "Create a complete report from the linked interview.",
        "Prepare a full manuscript from this evidence.",
        "Make a complete report from the transcript.",
        "Schreib einen kompletten Bericht und entferne alles vom bpilerplate.",
        "Mach einen kompletten Bericht aus dem Interview.",
    ):
        assert _requests_template_cleanup(message), message


def test_writer_mutation_detection_tolerates_one_adjacent_action_typo() -> None:
    assert _requests_manuscript_mutation(
        "SChrieb mla die App-Erfahung und Verbindungsprobleme passage neu pls"
    )
    assert not _requests_manuscript_mutation(
        "SChrieb die Passage noch nicht neu, zeig sie mir nur."
    )
    assert not _requests_manuscript_mutation("Schrieb Goethe diese Passage selbst?")
    assert not _requests_manuscript_mutation("Goethe schrieb mal eine Passage.")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Rewrite Observations. Propose the edit; do not publish or create sources.", True),
        ("Rewrite Observations, but do not change the title.", True),
        ("Schreib den Absatz neu; erstelle keine Quellen.", True),
        ("Do not edit the manuscript. Explain how to rewrite it.", False),
        ("Do not rewrite the paragraph; just explain the issue.", False),
        ("Do not write yet. Explain how to change the paragraph.", False),
    ],
)
def test_writer_negation_stays_with_the_restricted_action(message: str, expected: bool) -> None:
    assert _requests_manuscript_mutation(message) is expected


def test_writer_without_proposal_does_not_claim_pending_approval() -> None:
    reply = _clarify_pending_writer_review(
        "The Observations section compiled successfully and awaits your confirmation.",
        response_language="en",
        has_edits=False,
    )
    assert "No validated edit proposal" in reply
    assert "was not changed" in reply
    assert "compiled successfully" not in reply
    stored_state = "The saved manuscript contains a Purpose section."
    assert (
        _clarify_pending_writer_review(stored_state, response_language="en", has_edits=False)
        == stored_state
    )


@pytest.mark.parametrize(
    "message",
    [
        (
            "In main.tex, rewrite only the Observations paragraph into two clear sentences. "
            "Keep the exact fictional counts: five tasks, three successes, two failures. "
            "Explicitly state that these are synthetic QA data, not a research finding. "
            "Leave the Purpose section, title and author unchanged. "
            "Propose the edit; do not publish or create sources."
        ),
        # Even wording outside the heuristic cannot silently discard a staged edit.
        "Polish the Observations paragraph into two clear sentences.",
    ],
)
def test_writer_qa_proposal_survives_compact_final_and_unrelated_prohibitions(
    message: str,
) -> None:
    original = "Five fictional tasks were completed: three succeeded and two failed."
    replacement = (
        "Five fictional tasks were completed, with three successes and two failures. "
        "These are synthetic QA data, not a research finding."
    )
    source = (
        "\\documentclass{article}\n\\begin{document}\n"
        "\\section{Purpose}\nSynthetic launch QA.\n"
        "\\section{Observations}\n" + original + "\n\\end{document}\n"
    )
    edit = {"path": "main.tex", "find": original, "replace": replacement}
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Reading the exact Observations source.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 1, "end_line": 7},
            },
            {
                "action": "tool",
                "update": "Checking the proposed two-sentence paragraph.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "observations", "edit": edit},
            },
            {
                "action": "finish",
                "update": "The exact proposal is prepared.",
                "final": {
                    "reply": "The proposed paragraph is ready for review.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    files = {"main.tex": source}
    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            cast(Any, pool),
            project_files=files,
            active_path="main.tex",
            message=message,
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=lambda _edits: {"status": "passed", "errors": []},
        )
    assert len(turn.edits) == 1
    assert turn.edits[0]["replace"] == replacement
    assert "waiting for your approval" in turn.reply
    assert files == {"main.tex": source}
    assert turn.workspace_actions == []
    assert any(
        event.get("label") == "Manuscript proposal validated" and event.get("result_count") == 1
        for event in events
    )


def test_writer_reply_always_states_the_pending_approval_boundary() -> None:
    neutral = _clarify_pending_writer_review(
        "Ich habe eine präzisere Fassung vorbereitet.",
        response_language="de",
        has_edits=True,
    )
    false_claim = _clarify_pending_writer_review(
        "Die Passage wurde überarbeitet und gespeichert.",
        response_language="de",
        has_edits=True,
    )

    assert "noch nicht angewendet" in neutral
    assert "wartet auf deine Bestätigung" in neutral
    assert "als Änderungsvorschlag vorbereitet" in false_claim
    assert "noch nicht angewendet" in false_claim
    assert "gespeichert" not in false_claim


@pytest.mark.parametrize(
    ("update", "language"),
    [
        ("I rewrote the passage.", "en"),
        ("We have revised the paragraph.", "en"),
        ("Ich habe die Passage neu geschrieben.", "de"),
        ("Wir haben den Absatz geändert.", "de"),
        ("Die Passage wurde überarbeitet.", "de"),
    ],
)
def test_writer_public_update_neutralizes_applied_claims(
    update: str,
    language: str,
) -> None:
    filtered = _writer_public_update(update, response_language=language)

    assert filtered != update
    assert "review" in filtered.casefold() or "prüfung" in filtered.casefold()


@pytest.mark.parametrize(
    "message",
    [
        "Rewrite this passage using the linked interview.",
        "Rewrite the paragraph with a citation.",
        "What does this passage mean?",
    ],
)
def test_project_only_rewrite_keeps_evidence_and_questions_out(message: str) -> None:
    assert not _project_only_rewrite_request(message)


def test_typoed_targeted_rewrite_reads_only_manuscript_and_compiles_once() -> None:
    source_passage = (
        "Alex beschreibt die App-Erfahrung als durchwachsen. Beim Pairing traten "
        "mehrfach Verbindungsprobleme auf."
    )
    replacement = (
        "Die App hinterließ zunächst einen hochwertigen Eindruck, dessen positive "
        "Wirkung jedoch durch wiederholte Verbindungsprobleme beim Pairing abnahm."
    )
    edit = {"path": "main.tex", "find": source_passage, "replace": replacement}
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Ich suche die vorhandene Passage im Manuskript.",
                "tool": "search_project",
                "arguments": {"query": "App-Erfahrung und Verbindungsprobleme"},
            },
            {
                "action": "tool",
                "update": "Die Passage wurde überarbeitet und gespeichert.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "app-experience", "edit": edit},
            },
            {
                "action": "finish",
                "update": "Die Passage wurde überarbeitet.",
                "final": {
                    "reply": "Die Passage wurde bereits aktualisiert.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={
                "main.tex": "\\section{Ergebnisse}\n" + source_passage,
            },
            active_path="main.tex",
            message="SChrieb mla die App-Erfahung und Verbindungsprobleme passage neu pls",
            citations=[],
            assets=[],
            works=[],
            history=[],
            response_language="de",
            evidence_scope_note=("EVIDENCE SCOPE FOR THIS TURN: all linked evidence is available."),
            interview_evidence=[
                {
                    "title": "Kärcher Interview",
                    "analysis_text": "Die App-Erfahrung war durchwachsen.",
                    "passages": [],
                }
            ],
            candidate_compiler=compile_candidate,
        )

    assert len(compiled) == 1
    assert compiled[0][0]["path"] == edit["path"]
    assert compiled[0][0]["find"] == edit["find"]
    assert compiled[0][0]["replace"] == edit["replace"]
    assert [tool for tool in turn.tools_used if tool == "read_source"] == []
    assert turn.edits[0]["replace"] == replacement
    assert "noch nicht angewendet" in turn.reply
    assert '"name": "read_source"' not in str(pool.prompts[0]["system"])
    public_updates = [event for event in events if event.get("event") == "agent.update"]
    assert public_updates
    assert all("wurde überarbeitet" not in str(event.get("label")) for event in public_updates)
    assert all("gespeichert" not in str(event.get("detail")) for event in public_updates)


def test_typoed_targeted_rewrite_silently_reuses_reads_and_rejects_evidence_paths() -> None:
    source_passage = (
        "Alex beschreibt die App-Erfahrung als durchwachsen. Beim Pairing traten "
        "mehrfach Verbindungsprobleme auf."
    )
    replacement = (
        "Die App hinterließ zunächst einen hochwertigen Eindruck, der jedoch durch "
        "wiederholte Verbindungsprobleme beim Pairing beeinträchtigt wurde."
    )
    source_lines = [f"Manuscript line {index}." for index in range(1, 53)]
    source_lines[25] = source_passage
    source = "\n".join(source_lines)
    edit = {"path": "main.tex", "find": source_passage, "replace": replacement}
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "Ich lese die aktuelle Manuskriptdatei.",
                "plan": [
                    "Ich lese und verifiziere das Interview-Transkript erneut.",
                    "Ich formuliere die vorhandene Manuskriptpassage neu.",
                ],
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 1, "end_line": 52},
            },
            {
                "action": "tool",
                "update": "Ich lese die Zeilen 12 bis 18 noch einmal.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 12, "end_line": 18},
            },
            {
                "action": "tool",
                "update": "Ich lese die Zeilen 16 bis 24 noch einmal.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 16, "end_line": 24},
            },
            {
                "action": "tool",
                "update": "Ich lese die Zeilen 20 bis 30 noch einmal.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 20, "end_line": 30},
            },
            {
                "action": "tool",
                "update": "Ich lese die Zeilen 26 bis 34 noch einmal.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 26, "end_line": 34},
            },
            {
                "action": "tool",
                "update": "Ich lese die Zeilen 26 bis 30 noch einmal.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 26, "end_line": 30},
            },
            {
                "action": "tool",
                "update": "Ich lese den Interview-Handle als Datei.",
                "tool": "read_file",
                "arguments": {
                    "path": "interview:5ehf8ndvrq",
                    "start_line": 1,
                    "end_line": 100,
                },
            },
            {
                "action": "tool",
                "update": "Ich lese den Interview-Handle erneut als Datei.",
                "tool": "read_file",
                "arguments": {
                    "path": "interview:5ehf8ndvrq",
                    "start_line": 1,
                    "end_line": 999,
                },
            },
            {
                "action": "tool",
                "update": "Ich bereite die Änderung zur Prüfung vor.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "app-experience", "edit": edit},
            },
            {
                "action": "finish",
                "update": ("Die Passage wurde basierend auf dem Transkript neu formuliert."),
                "final": {
                    "reply": (
                        "Die Passage wurde auf Basis des Interviewtranscripts neu formuliert."
                    ),
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message="SChrieb mla die App-Erfahung und Verbindungsprobleme passage neu pls",
            citations=[],
            assets=[],
            works=[],
            history=[],
            response_language="de",
            evidence_scope_note=("EVIDENCE SCOPE FOR THIS TURN: all linked evidence is available."),
            interview_evidence=[
                {
                    "interview_id": "5ehf8ndvrq",
                    "title": "Kärcher Interview",
                    "analysis_text": "Die App-Erfahrung war durchwachsen.",
                    "passages": [],
                }
            ],
            candidate_compiler=lambda edits: (
                compiled.append(edits) or {"status": "passed", "errors": [], "log_tail": "ok"}
            ),
        )

    assert len(compiled) == 1
    assert turn.tools_used == ("read_file", "compile_candidate")
    assert len(turn.edits) == 1
    assert turn.edits[0]["path"] == edit["path"]
    assert turn.edits[0]["find"] == edit["find"]
    assert turn.edits[0]["replace"] == edit["replace"]
    first_system = str(pool.prompts[0]["system"])
    first_prompt = str(pool.prompts[0]["prompt"])
    assert '"name": "read_source"' not in first_system
    assert "LINKED INTERVIEW" not in first_prompt
    assert "EVIDENCE SCOPE" not in first_prompt
    assert "interview:5ehf8ndvrq" not in first_prompt
    rendered_prompts = " ".join(str(prompt["prompt"]) for prompt in pool.prompts)
    assert rendered_prompts.count("already_available") >= 5
    assert rendered_prompts.count("project_path_required") >= 2
    public_read_events = [
        event["event"] for event in events if event.get("tool") == "manuscript.read_file"
    ]
    assert public_read_events == ["tool.started", "tool.completed"]
    assert not any(event["event"] == "tool.failed" for event in events)
    public_copy = " ".join(
        str(event.get("detail") or "") for event in events if event.get("event") == "agent.update"
    )
    assert "noch einmal" not in public_copy
    assert "Interview-Handle" not in public_copy
    assert "Transkript" not in public_copy
    assert "Zeilen 12" not in public_copy
    assert "Zeilen 26" not in public_copy
    public_plans = [event for event in events if str(event.get("event")).startswith("plan.")]
    assert public_plans
    rendered_plans = json.dumps(public_plans, ensure_ascii=False)
    assert "Transkript" not in rendered_plans
    assert "erneut" not in rendered_plans
    assert "Neufassung der Manuskriptpassage" in rendered_plans
    assert "vorhandenen Manuskriptpassage" in turn.reply
    assert "Transkript" not in turn.reply
    assert "verifiziert" not in turn.reply
    assert "Evidenz" not in turn.reply


def test_writer_domain_use_of_placeholder_is_not_inferred_as_a_template() -> None:
    report = _manuscript_structure_report(
        {
            "main.tex": (
                "\\documentclass{article}\n\\begin{document}\n"
                "\\section{Placeholder detection}\n"
                "A placeholder can be semantically meaningful in a compiler. "
                "This paper compares placeholder representations.\n"
                "\\end{document}\n"
            )
        }
    )

    assert report["marker_count"] == 3
    assert report["strong_marker_count"] == 0
    assert report["template_like"] is False


def test_writer_boilerplate_cleanup_does_not_ban_new_domain_terms() -> None:
    initial = _manuscript_structure_report(
        {
            "main.tex": (
                "\\title{Conference Paper Title}\n\\section{Results}\nPlaceholder results.\n"
            )
        }
    )
    current = _manuscript_structure_report(
        {
            "main.tex": (
                "\\title{Compiler placeholder semantics}\n"
                "\\section{Results}\nA placeholder is a valid compiler construct.\n"
            )
        }
    )

    assert (
        _remaining_cleanup_markers(
            initial,
            current,
            strict_weak_cleanup=False,
        )
        == []
    )
    assert _remaining_cleanup_markers(
        initial,
        current,
        strict_weak_cleanup=True,
    )


def test_writer_agent_observes_project_tools_and_repairs_an_invalid_final() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am mapping the manuscript files first.",
                "plan": ["Map files", "Locate results", "Validate the edit"],
                "tool": "list_project_files",
                "arguments": {},
            },
            {
                "action": "tool",
                "update": "I am locating the exact results placeholder.",
                "tool": "search_project",
                "arguments": {"query": "Placeholder findings."},
            },
            {
                "action": "finish",
                "update": "I drafted the cross-file revision.",
                "final": {
                    "reply": "I prepared the revision.",
                    "edits": [
                        {
                            "path": "sections/results.tex",
                            "find": "An imagined source anchor.",
                            "replace": "A grounded bounded finding.",
                        },
                        {
                            "path": "main.tex",
                            "find": "A second imagined anchor.",
                            "replace": "Another ungrounded change.",
                        },
                    ],
                    "visual_request": None,
                    "workspace_actions": [
                        {
                            "type": "create_survey",
                            "title": "Unrequested bypass",
                            "questions": [],
                        }
                    ],
                },
            },
            {
                "action": "tool",
                "update": "I am reading the exact target after validation rejected the anchor.",
                "tool": "read_file",
                "arguments": {
                    "path": "sections/results.tex",
                    "start_line": 1,
                    "end_line": 4,
                },
            },
            {
                "action": "finish",
                "update": "The exact revision is ready for review.",
                "final": {
                    "reply": "I prepared the grounded results revision for review.",
                    "edits": [
                        {
                            "path": "sections/results.tex",
                            "find": "Placeholder findings.",
                            "replace": "A grounded bounded finding.",
                        }
                    ],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={
                "main.tex": "\\documentclass{article}\n\\input{sections/results}",
                "sections/results.tex": source,
            },
            active_path="main.tex",
            message="Review the whole manuscript and rewrite the results placeholder.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert len(pool.prompts) == 5
    assert "LATEX PROJECT MANIFEST" in str(pool.prompts[0]["prompt"])
    assert "DIRECT RESPONSE MODE" not in str(pool.prompts[0]["system"])
    assert "To call a tool" in str(pool.prompts[0]["system"])
    assert "Do not deny hypothetical actions" in str(pool.prompts[0]["system"])
    assert '"total_files": 2' in str(pool.prompts[1]["prompt"])
    assert "Placeholder findings." in str(pool.prompts[2]["prompt"])
    assert "unique existing source anchor" in str(pool.prompts[3]["prompt"])
    assert '"content": "\\\\section{Results}' in str(pool.prompts[4]["prompt"])
    assert turn.reply.startswith("I prepared the grounded results revision for review.")
    assert "has not been applied" in turn.reply
    assert len(turn.edits) == 1
    assert turn.edits[0]["path"] == "sections/results.tex"
    assert turn.edits[0]["applicable"] is True
    assert turn.tools_used == ("list_project_files", "search_project", "read_file")

    started_tools = {
        str(event.get("tool")) for event in events if event.get("event") == "tool.started"
    }
    assert {
        "manuscript.list_project_files",
        "manuscript.search_project",
        "manuscript.read_file",
    }.issubset(started_tools)
    assert any(event.get("event") == "checkpoint.failed" for event in events)
    verified = [
        event
        for event in events
        if event.get("tool") == "manuscript.verify_completion"
        and event.get("event") == "checkpoint.completed"
    ]
    assert len(verified) == 1
    assert verified[0]["output"] == {"status": "complete"}
    assert any(event.get("event") == "change.proposed" for event in events)


def test_legacy_writer_final_is_anchor_validated_before_completion() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    pool = _SequencePool(
        [
            {
                "reply": "I drafted the requested revision.",
                "edits": [
                    {
                        "path": "main.tex",
                        "find": "An invented source anchor.",
                        "replace": "A grounded finding.",
                    }
                ],
                "visual_request": None,
                "workspace_actions": [],
            },
            {
                "reply": "I corrected the exact edit anchor.",
                "edits": [
                    {
                        "path": "main.tex",
                        "find": "Placeholder findings.",
                        "replace": "A grounded finding.",
                    }
                ],
                "visual_request": None,
                "workspace_actions": [],
            },
        ]
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message="Review the manuscript and rewrite the results placeholder.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    verification_events = [
        event for event in events if event.get("tool") == "manuscript.verify_completion"
    ]
    assert any(
        event.get("event") == "checkpoint.failed" and event.get("output") == {"status": "continue"}
        for event in verification_events
    )
    completed = [
        event for event in verification_events if event.get("event") == "checkpoint.completed"
    ]
    assert len(completed) == 1
    assert completed[0]["output"] == {"status": "complete"}
    assert turn.edits[0]["find"] == "Placeholder findings."
    assert turn.edits[0]["applicable"] is True


def test_writer_rejects_and_repairs_a_partially_valid_edit_set() -> None:
    source = "\\section{Title}\nOld title.\n\\section{Introduction}\nOld introduction."
    partial = {
        "reply": "Both requested changes are ready.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Old title.",
                "replace": "New title.",
            },
            {
                "path": "main.tex",
                "find": "Invented introduction anchor.",
                "replace": "New introduction.",
            },
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    repaired = {
        **partial,
        "edits": [
            partial["edits"][0],
            {
                "path": "main.tex",
                "find": "Old introduction.",
                "replace": "New introduction.",
            },
        ],
    }
    pool = _SequencePool(
        [
            {"action": "finish", "update": "Drafted both changes.", "final": partial},
            {
                "action": "finish",
                "update": "Repaired the complete edit set.",
                "final": repaired,
            },
        ]
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message="Change both the title and the introduction.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert "repair the complete edit set" in str(pool.prompts[1]["prompt"])
    assert [edit["find"] for edit in turn.edits] == [
        "Old title.",
        "Old introduction.",
    ]
    verification_events = [
        event for event in events if event.get("tool") == "manuscript.verify_completion"
    ]
    assert [event["event"] for event in verification_events] == [
        "checkpoint.started",
        "checkpoint.failed",
        "checkpoint.started",
        "checkpoint.completed",
    ]
    assert sum(event.get("event") == "change.proposed" for event in events) == 2


def test_writer_recovery_fails_closed_when_no_edit_anchor_exists() -> None:
    pool = _FixedPool(
        {
            "reply": "The requested changes are ready.",
            "edits": [
                {
                    "path": "main.tex",
                    "find": "First invented anchor.",
                    "replace": "First change.",
                },
                {
                    "path": "main.tex",
                    "find": "Second invented anchor.",
                    "replace": "Second change.",
                },
            ],
            "visual_request": None,
            "workspace_actions": [],
        }
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nExisting findings."},
            active_path="main.tex",
            message="Rewrite the results with both requested changes.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert turn.edits == []
    assert "safely match the change to the right passage" in turn.reply
    assert not any(
        event.get("event") == "checkpoint.completed"
        and event.get("tool") == "manuscript.verify_completion"
        for event in events
    )
    assert not any(
        event.get("label")
        in {
            "Requested outcome verified",
            "Manuscript proposal validated",
            "Changes ready for review",
        }
        for event in events
    )
    assert events[-1]["event"] == "tool.failed"
    assert events[-1]["label"] == "Manuscript change needs attention"
    assert "could not be matched safely" in events[-1]["detail"]


def test_writer_recovery_never_returns_a_structurally_skipped_edit_subset() -> None:
    pool = _FixedPool(
        {
            "reply": "Both requested changes are ready.",
            "edits": [
                {
                    "path": "main.tex",
                    "find": "Old title.",
                    "replace": "New title.",
                },
                "This malformed edit used to be skipped.",
            ],
            "visual_request": None,
            "workspace_actions": [],
        }
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={
                "main.tex": (
                    "\\section{Title}\nOld title.\n\\section{Introduction}\nOld introduction."
                )
            },
            active_path="main.tex",
            message="Change both the title and the introduction.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert turn.edits == []
    assert "both requested changes are ready" not in turn.reply.casefold()
    assert "safely match the change to the right passage" in turn.reply
    assert not any(event.get("event") == "change.proposed" for event in events)
    assert not any(
        event.get("event") == "checkpoint.completed"
        and event.get("tool") == "manuscript.verify_completion"
        for event in events
    )
    assert events[-1]["event"] == "tool.failed"


def test_writer_recovery_replaces_a_partial_edit_set_only_as_a_complete_set() -> None:
    partial = {
        "reply": "Both requested changes are ready.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Old title.",
                "replace": "New title.",
            },
            {
                "path": "main.tex",
                "find": "Invented introduction anchor.",
                "replace": "New introduction.",
            },
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    complete = {
        **partial,
        "reply": "Both complete changes are ready for review.",
        "edits": [
            partial["edits"][0],
            {
                "path": "main.tex",
                "find": "Old introduction.",
                "replace": "New introduction.",
            },
        ],
    }
    pool = _RawSequencePool(
        [
            "not a structured agent response",
            json.dumps(partial),
            json.dumps(complete),
        ]
    )

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={
            "main.tex": ("\\section{Title}\nOld title.\n\\section{Introduction}\nOld introduction.")
        },
        active_path="main.tex",
        message="Rewrite both the title and the introduction.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert len(pool.prompts) == 3
    assert "EDIT REPAIR" in str(pool.prompts[2]["system"])
    assert [edit["find"] for edit in turn.edits] == [
        "Old title.",
        "Old introduction.",
    ]
    assert turn.reply.startswith("Both complete changes are ready for review.")
    assert "has not been applied" in turn.reply


def test_writer_never_accepts_a_shrunk_repair_for_a_rejected_edit_set() -> None:
    partial = {
        "reply": "Both requested changes are ready.",
        "edits": [
            {"path": "main.tex", "find": "Old title.", "replace": "New title."},
            {
                "path": "main.tex",
                "find": "Invented introduction anchor.",
                "replace": "New introduction.",
            },
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    shrunk = {
        **partial,
        "edits": [partial["edits"][0]],
    }

    class PartialThenShrunkPool:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args
            self.calls += 1
            system = str(kwargs.get("system") or "")
            if self.calls == 1 and "AVAILABLE TOOLS" in system:
                value: object = {
                    "action": "finish",
                    "update": "Prepared both requested changes.",
                    "final": partial,
                }
            elif "AVAILABLE TOOLS" in system:
                value = {
                    "action": "finish",
                    "update": "Repaired the proposal.",
                    "final": shrunk,
                }
            else:
                value = shrunk
            return SimpleNamespace(text=json.dumps(value))

    pool = PartialThenShrunkPool()
    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={
                "main.tex": (
                    "\\section{Title}\nOld title.\n\\section{Introduction}\nOld introduction."
                )
            },
            active_path="main.tex",
            message="Change both the title and the introduction.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert pool.calls >= 2
    assert turn.edits == []
    assert "both requested changes are ready" not in turn.reply.casefold()
    assert not any(event.get("event") == "change.proposed" for event in events)
    assert not any(
        event.get("event") == "checkpoint.completed"
        and event.get("tool") == "manuscript.verify_completion"
        for event in events
    )


def test_writer_project_tools_are_read_only_bounded_and_path_scoped() -> None:
    long_source = "".join(f"line {index:03d} " + ("x" * 120) + "\n" for index in range(500))
    tools = {
        tool.name: tool
        for tool in _writer_agent_tools(
            {
                "main.tex": long_source,
                "sections/results.tex": "First line\nNeedle result\nLast line",
            },
            "main.tex",
        )
    }
    assert {tool.effect for tool in tools.values()} == {"read"}
    assert tools["read_file"].max_calls == 32
    assert tools["search_project"].max_calls == 24

    listed = tools["list_project_files"].handler({})
    listed_output = cast(dict[str, Any], listed.output)
    assert listed.success
    assert listed_output["files"][0]["path"] == "main.tex"
    assert listed_output["files"][0]["active"] is True

    bounded = tools["read_file"].handler({"path": "main.tex", "start_line": 1, "end_line": 10_000})
    bounded_output = cast(dict[str, Any], bounded.output)
    assert bounded.success
    assert bounded_output["end_line"] <= 240
    assert len(bounded_output["content"]) <= 18_000
    assert bounded_output["truncated"] is True

    exact = tools["read_file"].handler(
        {"path": "sections/results.tex", "start_line": 2, "end_line": 2}
    )
    assert cast(dict[str, Any], exact.output)["content"] == "Needle result\n"

    outside = tools["read_file"].handler({"path": "../secrets.tex"})
    assert outside.success is False
    assert "not part of this manuscript project" in outside.summary

    searched = tools["search_project"].handler(
        {"query": "needle", "path": "sections/results.tex", "max_results": 5}
    )
    searched_output = cast(dict[str, Any], searched.output)
    assert searched.success
    assert searched_output["result_count"] == 1
    assert searched_output["matches"][0]["path"] == "sections/results.tex"
    assert searched_output["matches"][0]["line"] == 2


def test_writer_project_read_preflight_canonicalizes_ranges_and_eof() -> None:
    source = "first\nsecond\nthird\n"
    tools = {
        tool.name: tool
        for tool in _writer_agent_tools(
            {"main.tex": source},
            "main.tex",
            evidence_tools_available=False,
        )
    }
    read_file = tools["read_file"]

    first = read_file.handler({"path": "main.tex", "end_line": 10_000})
    repeated = read_file.preflight({"path": "main.tex"}) if read_file.preflight else None
    past_eof = (
        read_file.preflight({"path": "main.tex", "start_line": 50}) if read_file.preflight else None
    )
    wrong_tool = (
        read_file.preflight({"path": "interview:5ehf8ndvrq"}) if read_file.preflight else None
    )

    first_output = cast(dict[str, Any], first.output)
    assert first.success
    assert first_output["content"] == source
    assert first_output["truncated"] is False
    assert repeated is not None and repeated.success
    assert cast(dict[str, Any], repeated.output)["status"] == "already_available"
    assert past_eof is not None and past_eof.success
    assert cast(dict[str, Any], past_eof.output) == {
        "status": "end_of_file",
        "path": "main.tex",
        "requested_start_line": 50,
        "next_line": 4,
        "total_lines": 3,
        "content": "",
    }
    assert wrong_tool is not None and not wrong_tool.success
    assert cast(dict[str, Any], wrong_tool.output)["status"] == "project_path_required"


def test_writer_project_preflight_allows_only_tools_named_by_repair_state() -> None:
    repair = "Read the exact target with read_file, then retry compile_candidate."
    repair_state = _WriterRepairState()
    repair_state.require(repair, {"read_file", "compile_candidate"})
    tools = {
        tool.name: tool
        for tool in _writer_agent_tools(
            {"main.tex": "Current prose.\n"},
            "main.tex",
            repair_state=repair_state,
        )
    }

    list_redirect = (
        tools["list_project_files"].preflight({}) if tools["list_project_files"].preflight else None
    )
    search_redirect = (
        tools["search_project"].preflight({"query": "Current"})
        if tools["search_project"].preflight
        else None
    )
    allowed_read = (
        tools["read_file"].preflight({"path": "main.tex"}) if tools["read_file"].preflight else None
    )

    assert list_redirect is not None and not list_redirect.success
    assert search_redirect is not None and not search_redirect.success
    assert cast(dict[str, Any], list_redirect.output)["allowed_tools"] == [
        "compile_candidate",
        "read_file",
    ]
    assert allowed_read is None


def test_writer_evidence_reader_redirects_project_paths_and_clamps_eof() -> None:
    tools = {
        tool.name: tool
        for tool in _writer_evidence_tools(
            works=[],
            citations=[],
            attachments=[{"filename": "Interview.txt", "text": "short evidence"}],
            datasets=[],
            interview_evidence=[],
            survey_evidence=[],
            citation_receipts=set(),
            project_paths={"main.tex"},
        )
    }
    read_source = tools["read_source"]

    wrong_tool = read_source.preflight({"handle": "main.tex"}) if read_source.preflight else None
    beyond = (
        read_source.preflight({"handle": "source:1", "offset": 10_000})
        if read_source.preflight
        else None
    )

    assert wrong_tool is not None and not wrong_tool.success
    wrong_output = cast(dict[str, Any], wrong_tool.output)
    assert wrong_output["status"] == "wrong_tool"
    assert wrong_output["required_tool"] == "read_file"
    assert beyond is not None and beyond.success
    beyond_output = cast(dict[str, Any], beyond.output)
    assert beyond_output["status"] == "end_of_source"
    assert beyond_output["requested_offset"] == 10_000
    assert beyond_output["offset"] == len("short evidence")
    assert beyond_output["content"] == ""


def test_writer_structure_tool_reports_exact_template_residue() -> None:
    source = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\title{Conference Paper Title}\n"
        "\\section{Introduction}\n"
        "Motivate the problem and preview the contribution in three paragraphs.\n"
        "\\section*{Acknowledgment}\n\n"
        "\\bibliography{references}\n"
        "\\end{document}\n"
    )
    receipts: set[str] = set()
    tools = {
        tool.name: tool
        for tool in _writer_agent_tools(
            {
                "main.tex": source,
                "publisher.cls": "% Placeholder hook required by the class.\n",
            },
            "main.tex",
            structure_receipts=receipts,
        )
    }

    inspected = tools["inspect_document_structure"].handler({})
    report = cast(dict[str, Any], inspected.output)
    markers = cast(list[dict[str, Any]], report["markers"])

    assert inspected.success
    assert report["template_like"] is True
    assert {marker["kind"] for marker in markers} >= {
        "template_title",
        "instructional_prose",
        "empty_section",
    }
    assert any(
        marker["anchor"] == "\\section*{Acknowledgment}"
        for marker in markers
        if marker["kind"] == "empty_section"
    )
    assert {marker["path"] for marker in markers} == {"main.tex"}
    assert receipts == {"initial_structure"}


def test_writer_read_file_keeps_full_model_observation_but_bounds_public_event() -> None:
    marker = "MODEL_VISIBLE_AFTER_PUBLIC_PREVIEW"
    source = "".join(
        f"line {index:03d}: {'x' * 80}{marker if index == 60 else ''}\n" for index in range(100)
    )
    read_file = {tool.name: tool for tool in _writer_agent_tools({"main.tex": source}, "main.tex")}[
        "read_file"
    ]

    result = read_file.handler({"path": "main.tex", "start_line": 1, "end_line": 100})
    output = cast(dict[str, Any], result.output)
    observation = AgentObservation(
        iteration=1,
        kind="tool",
        name="read_file",
        summary=result.summary,
        output=result.output,
    ).prompt_value()
    model_output = cast(dict[str, Any], observation["output"])
    public_output = cast(dict[str, Any], safe_event_value(result.output))

    assert result.success
    assert len(output["content"]) > 4_000
    assert model_output["content"] == output["content"]
    assert marker in model_output["content"]
    assert model_output["end_line"] == 100
    assert len(public_output["content"]) == 4_000
    assert marker not in public_output["content"]


def test_writer_read_source_continues_after_model_visible_excerpt_with_receipt() -> None:
    marker = "SUPPORTING_ABSTRACT_PASSAGE"
    title = "T" * 4_300
    abstract = "A" * 300 + marker + "B" * 18_200
    expected_source = f"Title: {title}\nAbstract: {abstract}"
    receipts: set[str] = set()
    read_source = {
        tool.name: tool
        for tool in _writer_evidence_tools(
            works=[WorkRecord(id="W1", title=title, abstract=abstract)],
            citations=[{"key": "LongPaper2026", "title": title}],
            attachments=[],
            datasets=[],
            interview_evidence=[],
            survey_evidence=[],
            citation_receipts=receipts,
        )
    }["read_source"]

    first = read_source.handler({"handle": "paper:1", "offset": 0})
    first_model = cast(
        dict[str, Any],
        AgentObservation(
            iteration=1,
            kind="tool",
            name="read_source",
            summary=first.summary,
            output=first.output,
        ).prompt_value()["output"],
    )
    public_first = cast(dict[str, Any], safe_event_value(first.output))
    second = read_source.handler(
        {
            "handle": "paper:1",
            "offset": first_model["next_offset"],
        }
    )
    second_model = cast(
        dict[str, Any],
        AgentObservation(
            iteration=2,
            kind="tool",
            name="read_source",
            summary=second.summary,
            output=second.output,
        ).prompt_value()["output"],
    )

    assert first_model["content"] == expected_source[:18_000]
    assert marker in first_model["content"]
    assert first_model["citation_receipt"] == "LongPaper2026"
    assert first_model["next_offset"] == 18_000
    assert second_model["offset"] == 18_000
    assert second_model["content"] == expected_source[18_000:]
    assert first_model["content"] + second_model["content"] == expected_source
    assert len(public_first["content"]) == 4_000
    assert marker not in public_first["content"]
    assert receipts == {"LongPaper2026"}

    eof = read_source.handler({"handle": "paper:1", "offset": len(expected_source)})
    eof_output = cast(dict[str, Any], eof.output)
    assert eof.success
    assert eof_output["status"] == "end_of_source"
    assert eof_output["content"] == ""
    assert eof_output["next_offset"] == len(expected_source)
    assert eof_output["citation_receipt"] is None

    beyond = read_source.handler({"handle": "paper:1", "offset": len(expected_source) + 1})
    beyond_output = cast(dict[str, Any], beyond.output)
    assert beyond.success
    assert beyond_output["status"] == "end_of_source"
    assert beyond_output["requested_offset"] == len(expected_source) + 1
    assert beyond_output["offset"] == len(expected_source)


def test_writer_read_source_returns_an_8992_character_interview_in_one_read() -> None:
    rendered = "I" * 8_992
    read_source = {
        tool.name: tool
        for tool in _writer_evidence_tools(
            works=[],
            citations=[],
            attachments=[{"filename": "Interview.txt", "text": rendered}],
            datasets=[],
            interview_evidence=[],
            survey_evidence=[],
            citation_receipts=set(),
        )
    }["read_source"]

    result = read_source.handler({"handle": "source:1"})
    output = cast(dict[str, Any], result.output)

    assert result.success
    assert output["content"] == rendered
    assert output["next_offset"] == 8_992
    assert output["truncated"] is False

    repeated = read_source.handler({"handle": "source:1", "offset": 0})
    repeated_output = cast(dict[str, Any], repeated.output)
    assert repeated.success
    assert repeated_output["status"] == "already_available"
    assert repeated_output["content"] == ""


def test_writer_read_source_preflight_suppresses_a_covered_read_lifecycle() -> None:
    source = "Interview evidence about app pairing." * 250
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am reading the linked interview.",
                "tool": "read_source",
                "arguments": {"handle": "source:1"},
            },
            {
                "action": "tool",
                "update": "I am reading the same interview again.",
                "tool": "read_source",
                "arguments": {"handle": "source:1", "offset": 0},
            },
            {
                "action": "finish",
                "update": "The answer is ready.",
                "final": {
                    "reply": "The interview describes app pairing problems.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nCurrent prose."},
            active_path="main.tex",
            message="What does the linked interview say about app pairing?",
            citations=[],
            assets=[],
            works=[],
            history=[],
            attachments=[{"filename": "Interview.txt", "text": source}],
        )

    read_lifecycle = [
        event["event"] for event in events if event.get("tool") == "manuscript.read_source"
    ]
    assert read_lifecycle == ["tool.started", "tool.completed"]
    assert turn.tools_used.count("read_source") == 1
    assert "already available" in str(pool.prompts[2]["prompt"])


def test_broad_selected_rewrites_do_not_enter_the_local_writer_fast_path() -> None:
    selection = {
        "kind": "source",
        "path": "main.tex",
        "line": 3,
        "quote": "Selected paragraph.",
    }

    assert is_local_writer_edit_request(
        "Rewrite this selected paragraph more clearly.",
        selection,
    )
    assert not is_local_writer_edit_request(
        "Rewrite the entire manuscript for consistent terminology.",
        selection,
    )
    assert not is_local_writer_edit_request(
        "Überarbeite alle Kapitel im Manuskript.",
        selection,
    )


def test_writer_agent_propagates_cancellation_before_model_work() -> None:
    pool = _SequencePool([])
    pool.cancel_check = lambda: True

    with pytest.raises(LLMCancelledError):
        run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message="Review the whole manuscript and revise the results.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert pool.prompts == []


def test_writer_format_recovery_does_not_swallow_cancellation() -> None:
    class CancelDuringRecoveryPool:
        def __init__(self) -> None:
            self.calls = 0
            self.cancel_check = lambda: False

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(text="not structured")
            raise LLMCancelledError("writer recovery cancelled")

    pool = CancelDuringRecoveryPool()
    with pytest.raises(LLMCancelledError):
        run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message="Review the whole manuscript without changing it.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert pool.calls == 2


def test_writer_format_recovery_progress_stays_task_focused() -> None:
    pool = _RawSequencePool(
        [
            "not structured",
            json.dumps(
                {
                    "reply": "The clearer sentence is ready for review.",
                    "edits": [
                        {
                            "path": "main.tex",
                            "find": "Old sentence.",
                            "replace": "A clearer sentence.",
                        }
                    ],
                    "visual_request": None,
                    "workspace_actions": [],
                }
            ),
        ]
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nOld sentence."},
            active_path="main.tex",
            message="Rewrite this selected sentence more clearly.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            selection={
                "kind": "source",
                "path": "main.tex",
                "line": 2,
                "quote": "Old sentence.",
            },
        )

    assert turn.edits[0]["replace"] == "A clearer sentence."
    progress = next(event for event in events if event["event"] == "tool.progress")
    assert progress["label"] == "Validate the structured manuscript proposal"
    assert progress["detail"] == (
        "Preparing the manuscript proposal in the required structured format."
    )
    visible_copy = f"{progress['label']} {progress['detail']}".casefold()
    for internal_phrase in (
        "format repair",
        "first response",
        "unverified",
        "recovering",
        "discarding",
    ):
        assert internal_phrase not in visible_copy


def test_writer_completion_checkpoint_does_not_swallow_cancellation() -> None:
    deferred = json.dumps(
        {
            "reply": "I have not prepared the requested edit yet.",
            "edits": [],
            "visual_request": None,
            "workspace_actions": [],
        }
    )

    class CancelDuringCheckpointPool:
        def __init__(self) -> None:
            self.calls = 0
            self.cancel_check = lambda: False

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            self.calls += 1
            # The bounded no-edit path retries once, then stops without spending
            # a third request. Cancellation during that retry must still escape.
            if self.calls == 1:
                return SimpleNamespace(text=deferred)
            raise LLMCancelledError("writer completion review cancelled")

    pool = CancelDuringCheckpointPool()
    with pytest.raises(LLMCancelledError):
        run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Introduction}\nExisting text."},
            active_path="main.tex",
            message="Write a new limitations paragraph for this manuscript.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert pool.calls == 2


def test_writer_stages_and_compiles_two_edits_cumulatively_with_lifecycle() -> None:
    first = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A complete introduction.",
    }
    second = {
        "path": "main.tex",
        "find": "Results placeholder.",
        "replace": "Complete results.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am validating the introduction edit first.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "introduction", "edit": first},
            },
            {
                "action": "tool",
                "update": "The introduction passed; I am validating the results edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": second},
            },
            {
                "action": "finish",
                "update": "Both sequentially checked edits are ready for review.",
                "final": {
                    "reply": "Both edits are ready for review.",
                    "edits": [first, second],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    project_files = {
        "main.tex": (
            "\\section{Introduction}\nIntroduction placeholder.\n"
            "\\section{Results}\nResults placeholder."
        )
    }
    original_source = project_files["main.tex"]
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files=project_files,
            active_path="main.tex",
            message="Replace both the introduction and results placeholders.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert [len(candidate) for candidate in compiled] == [1, 2]
    assert [edit["replace"] for edit in compiled[1]] == [
        "A complete introduction.",
        "Complete results.",
    ]
    assert [edit["replace"] for edit in turn.edits] == [
        "A complete introduction.",
        "Complete results.",
    ]
    assert project_files["main.tex"] == original_source
    compile_events = [
        event for event in events if event.get("tool") == "manuscript.compile_candidate"
    ]
    assert [event["event"] for event in compile_events] == [
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.completed",
    ]
    assert compile_events[1]["output"]["edit_count"] == 1
    assert compile_events[3]["output"]["edit_count"] == 2
    assert [compile_events[index]["result_count"] for index in (1, 3)] == [1, 2]
    assert compile_events[0]["call_id"] == compile_events[1]["call_id"]
    assert compile_events[2]["call_id"] == compile_events[3]["call_id"]


def test_writer_keeps_staged_prefix_until_every_explicit_target_is_covered() -> None:
    title_edit = {
        "path": "main.tex",
        "find": "\\title{Old title}",
        "replace": "\\title{Grounded title}",
    }
    introduction_edit = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A complete introduction.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am validating the title first.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "title", "edit": title_edit},
            },
            {
                "action": "finish",
                "update": "Both requested changes are ready.",
                "final": {
                    "reply": "Both requested changes are ready.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
            {
                "action": "tool",
                "update": "The introduction is still missing, so I am validating it now.",
                "tool": "compile_candidate",
                "arguments": {
                    "step_id": "introduction",
                    "edit": introduction_edit,
                },
            },
            {
                "action": "finish",
                "update": "Both requested changes are now validated.",
                "final": {
                    "reply": "Both requested changes are ready for review.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={
            "main.tex": ("\\title{Old title}\n\\section{Introduction}\nIntroduction placeholder.")
        },
        active_path="main.tex",
        message="Change both the title and the introduction.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        candidate_compiler=compile_candidate,
    )

    assert [len(candidate) for candidate in compiled] == [1, 2]
    assert [edit["replace"] for edit in turn.edits] == [
        "\\title{Grounded title}",
        "A complete introduction.",
    ]
    assert "Missing explicitly requested manuscript targets: introduction" in str(
        pool.prompts[2]["prompt"]
    )


def test_writer_two_title_edits_do_not_cover_requested_introduction() -> None:
    title_edit = {
        "path": "main.tex",
        "find": "\\title{Old title}",
        "replace": "\\title{Grounded title}",
    }
    short_title_edit = {
        "path": "main.tex",
        "find": "\\newcommand{\\shorttitle}{Old short title}",
        "replace": "\\newcommand{\\shorttitle}{Grounded short title}",
    }
    introduction_edit = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A complete introduction.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am validating the main title.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "title", "edit": title_edit},
            },
            {
                "action": "tool",
                "update": "I am validating the short title.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "short-title", "edit": short_title_edit},
            },
            {
                "action": "finish",
                "update": "Both requested targets are ready.",
                "final": {
                    "reply": "Both requested targets are ready.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
            {
                "action": "tool",
                "update": "The introduction is still missing, so I am validating it.",
                "tool": "compile_candidate",
                "arguments": {
                    "step_id": "introduction",
                    "edit": introduction_edit,
                },
            },
            {
                "action": "finish",
                "update": "The title and introduction are now covered.",
                "final": {
                    "reply": "The requested title and introduction are ready.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={
            "main.tex": (
                "\\title{Old title}\n"
                "\\newcommand{\\shorttitle}{Old short title}\n"
                "\\section{Introduction}\nIntroduction placeholder."
            )
        },
        active_path="main.tex",
        message="Change both the title and the introduction.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        candidate_compiler=compile_candidate,
    )

    assert [len(candidate) for candidate in compiled] == [1, 2, 3]
    assert len(turn.edits) == 3
    assert "Missing explicitly requested manuscript targets: introduction" in str(
        pool.prompts[3]["prompt"]
    )


def test_legacy_writer_batch_cannot_finish_with_one_of_two_explicit_targets() -> None:
    title_edit = {
        "path": "main.tex",
        "find": "\\title{Old title}",
        "replace": "\\title{Grounded title}",
    }
    introduction_edit = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A complete introduction.",
    }
    pool = _SequencePool(
        [
            {
                "reply": "Both changes are ready.",
                "edits": [title_edit],
                "visual_request": None,
                "workspace_actions": [],
            },
            {
                "reply": "Both validated changes are ready.",
                "edits": [title_edit, introduction_edit],
                "visual_request": None,
                "workspace_actions": [],
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={
            "main.tex": ("\\title{Old title}\n\\section{Introduction}\nIntroduction placeholder.")
        },
        active_path="main.tex",
        message="Change both the title and the introduction.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        candidate_compiler=compile_candidate,
    )

    assert [len(candidate) for candidate in compiled] == [1, 2]
    assert len(turn.edits) == 2
    assert "Missing explicitly requested manuscript targets: introduction" in str(
        pool.prompts[1]["prompt"]
    )


def test_writer_reference_section_does_not_create_a_second_edit_target() -> None:
    title_edit = {
        "path": "main.tex",
        "find": "\\title{Old title}",
        "replace": "\\title{Introduction-aligned title}",
    }
    pool = _SequencePool(
        [
            {
                "reply": "The title is ready for review.",
                "edits": [title_edit],
                "visual_request": None,
                "workspace_actions": [],
            }
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={
            "main.tex": ("\\title{Old title}\n\\section{Introduction}\nIntroduction context.")
        },
        active_path="main.tex",
        message="Rewrite the title to align with the introduction.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        candidate_compiler=compile_candidate,
    )

    assert [len(candidate) for candidate in compiled] == [1]
    assert [edit["replace"] for edit in turn.edits] == ["\\title{Introduction-aligned title}"]


def test_writer_two_words_in_one_section_remain_one_edit_target() -> None:
    edit = {
        "path": "main.tex",
        "find": "two old words",
        "replace": "two new words",
    }
    pool = _SequencePool(
        [
            {
                "reply": "The two words are ready for review.",
                "edits": [edit],
                "visual_request": None,
                "workspace_actions": [],
            }
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\section{Introduction}\nReplace two old words here."},
        active_path="main.tex",
        message="Replace these two words in the introduction.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        candidate_compiler=compile_candidate,
    )

    assert [len(candidate) for candidate in compiled] == [1]
    assert turn.edits[0]["replace"] == "two new words"


def test_writer_read_context_and_rewrite_target_remain_one_edit_target() -> None:
    edit = {
        "path": "main.tex",
        "find": "\\title{Old title}",
        "replace": "\\title{Introduction-aligned title}",
    }
    pool = _SequencePool(
        [
            {
                "reply": "The contextualized title is ready for review.",
                "edits": [edit],
                "visual_request": None,
                "workspace_actions": [],
            }
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={
            "main.tex": ("\\title{Old title}\n\\section{Introduction}\nIntroduction context.")
        },
        active_path="main.tex",
        message="Read the introduction and rewrite the title.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        candidate_compiler=compile_candidate,
    )

    assert [len(candidate) for candidate in compiled] == [1]
    assert turn.edits[0]["replace"] == "\\title{Introduction-aligned title}"


def test_legacy_writer_batch_over_five_edits_is_staged_sequentially() -> None:
    edits = [
        {
            "path": "main.tex",
            "find": f"Placeholder {index}.",
            "replace": f"Completed section {index}.",
        }
        for index in range(1, 7)
    ]
    pool = _SequencePool(
        [
            {
                "reply": "The full report is ready for review.",
                "edits": edits,
                "visual_request": None,
                "workspace_actions": [],
            }
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(candidate: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(candidate)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\n".join(f"Placeholder {index}." for index in range(1, 7))},
            active_path="main.tex",
            message=("Replace all placeholders throughout the manuscript with the full report."),
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert [len(candidate) for candidate in compiled] == [1, 2, 3, 4, 5, 6]
    assert len(turn.edits) == 6
    assert turn.verification == {"status": "passed", "errors": [], "log_tail": "ok"}
    compile_events = [
        event for event in events if event.get("tool") == "manuscript.compile_candidate"
    ]
    assert [event["event"] for event in compile_events] == [
        lifecycle for _ in edits for lifecycle in ("tool.started", "tool.completed")
    ]
    assert all(
        "staged_edits" not in (event.get("output") or {})
        for event in compile_events
        if event["event"] == "tool.completed"
    )
    visible_compile_copy = " ".join(
        str(event.get(field) or "") for event in compile_events for field in ("label", "detail")
    ).casefold()
    assert "temporary candidate" not in visible_compile_copy
    assert "saved source" not in visible_compile_copy
    assert "saved manuscript" not in visible_compile_copy


def test_writer_retries_failed_second_edit_before_staging_the_next_result() -> None:
    first = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A complete introduction.",
    }
    broken_second = {
        "path": "main.tex",
        "find": "Results placeholder.",
        "replace": "\\undefinedcommand{Broken}",
    }
    repaired_second = {
        "path": "main.tex",
        "find": "Results placeholder.",
        "replace": "Complete results.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am validating the introduction edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "introduction", "edit": first},
            },
            {
                "action": "tool",
                "update": "I am validating the results edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": broken_second},
            },
            {
                "action": "tool",
                "update": "I corrected the same results edit and am retrying it.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": repaired_second},
            },
            {
                "action": "finish",
                "update": "The cumulative candidate now passes.",
                "final": {
                    "reply": "The corrected edits are ready for review.",
                    "edits": [first, repaired_second],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        if any("undefinedcommand" in str(edit["replace"]) for edit in edits):
            return {
                "status": "failed",
                "errors": [{"line": 4, "message": "Undefined control sequence"}],
                "log_tail": "undefinedcommand",
            }
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={
                "main.tex": (
                    "\\section{Introduction}\nIntroduction placeholder.\n"
                    "\\section{Results}\nResults placeholder."
                )
            },
            active_path="main.tex",
            message="Replace both the introduction and results placeholders.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert [len(candidate) for candidate in compiled] == [1, 2, 2]
    assert "undefinedcommand" in compiled[1][1]["replace"]
    assert compiled[2][1]["replace"] == "Complete results."
    assert turn.edits[1]["replace"] == "Complete results."
    assert "Undefined control sequence" in str(pool.prompts[2]["prompt"])
    assert [
        event["event"] for event in events if event.get("tool") == "manuscript.compile_candidate"
    ] == [
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.failed",
        "tool.started",
        "tool.completed",
    ]


def test_writer_discards_staged_prefix_when_second_edit_repair_is_exhausted() -> None:
    first = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A complete introduction.",
    }
    failed_steps = [
        {
            "action": "tool",
            "update": "I am correcting the current results edit.",
            "tool": "compile_candidate",
            "arguments": {
                "step_id": "results",
                "edit": {
                    "path": "main.tex",
                    "find": "Results placeholder.",
                    "replace": f"\\undefinedcommand{{Broken {index}}}",
                },
            },
        }
        for index in range(8)
    ]
    fallback = failed_steps[-1]
    pool = _PrefixThenFixedPool(
        [
            {
                "action": "tool",
                "update": "I am validating the introduction edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "introduction", "edit": first},
            },
            *failed_steps,
        ],
        fallback,
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        if len(edits) == 1:
            return {"status": "passed", "errors": [], "log_tail": "ok"}
        return {
            "status": "failed",
            "errors": [{"line": 4, "message": "Undefined control sequence"}],
            "log_tail": "undefinedcommand",
        }

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={
                "main.tex": (
                    "\\section{Introduction}\nIntroduction placeholder.\n"
                    "\\section{Results}\nResults placeholder."
                )
            },
            active_path="main.tex",
            message="Replace both the introduction and results placeholders.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert [len(candidate) for candidate in compiled] == [1, *([2] * 8)]
    assert turn.edits == []
    assert not any(event.get("event") == "change.proposed" for event in events)
    assert "limit" not in turn.reply.casefold()
    assert "iteration" not in turn.reply.casefold()


def test_writer_rejects_final_edits_that_differ_from_the_staged_candidate() -> None:
    staged = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A complete introduction.",
    }
    uncompiled = {
        "path": "main.tex",
        "find": "Introduction placeholder.",
        "replace": "A different uncompiled introduction.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am validating the introduction edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "introduction", "edit": staged},
            },
            {
                "action": "finish",
                "update": "The introduction is ready.",
                "final": {
                    "reply": "The introduction is ready.",
                    "edits": [uncompiled],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
            {
                "action": "finish",
                "update": "I restored the exact staged result.",
                "final": {
                    "reply": "The validated introduction is ready for review.",
                    "edits": [staged],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Introduction}\nIntroduction placeholder."},
            active_path="main.tex",
            message="Replace the introduction placeholder.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert len(compiled) == 1
    assert turn.edits[0]["replace"] == "A complete introduction."
    assert "changed a manuscript edit" in str(pool.prompts[2]["prompt"])
    assert any(
        event.get("event") == "checkpoint.failed"
        and "changed a manuscript edit" in str(event.get("detail", "")).casefold()
        for event in events
    )


def test_writer_rolls_back_from_the_edit_that_corrupted_interview_attribution() -> None:
    quote = "Das wöchentliche Mentoring hat meine Unsicherheit reduziert."
    attributed_source = f"P1 said: ``{quote}'' (P1, 00:30)."
    corrupted = {
        "path": "main.tex",
        "find": attributed_source,
        "replace": "Weekly mentoring reduced uncertainty.",
    }
    corrected = {
        "path": "main.tex",
        "find": attributed_source,
        "replace": f"Interview evidence remained explicit: ``{quote}'' (P1, 00:30).",
    }
    discussion = {
        "path": "main.tex",
        "find": "Discussion placeholder.",
        "replace": "A bounded discussion.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am validating the interview finding.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "interview-finding", "edit": corrupted},
            },
            {
                "action": "tool",
                "update": "I am validating the discussion next.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "discussion", "edit": discussion},
            },
            {
                "action": "finish",
                "update": "Both manuscript changes are ready.",
                "final": {
                    "reply": "Both changes are ready.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
            {
                "action": "tool",
                "update": "I am correcting the same interview finding with its attribution.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "interview-finding", "edit": corrected},
            },
            {
                "action": "tool",
                "update": "I am restoring the later discussion step onto the correction.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "discussion", "edit": discussion},
            },
            {
                "action": "finish",
                "update": "The corrected cumulative candidate is ready.",
                "final": {
                    "reply": "The attributed finding and discussion are ready for review.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={
                "main.tex": (
                    "\\section{Results}\n"
                    + attributed_source
                    + "\n\\section{Discussion}\nDiscussion placeholder."
                )
            },
            active_path="main.tex",
            message=(
                "Rewrite the result and improve the discussion, but preserve the German "
                "verbatim quote with speaker and timestamp."
            ),
            citations=[],
            assets=[],
            works=[],
            history=[],
            interview_evidence=[
                {
                    "title": "P1",
                    "analysis_text": "Mentoring reduced uncertainty.",
                    "passages": [
                        {
                            "speaker": "P1",
                            "start_ms": 30_000,
                            "end_ms": 60_000,
                            "text": quote,
                        }
                    ],
                }
            ],
            candidate_compiler=compile_candidate,
        )

    assert [len(candidate) for candidate in compiled] == [1, 2, 1, 2]
    assert compiled[0][0]["replace"] == "Weekly mentoring reduced uncertainty."
    assert quote in compiled[2][0]["replace"]
    assert [edit["replace"] for edit in turn.edits] == [
        corrected["replace"],
        discussion["replace"],
    ]
    assert "does not preserve" in str(pool.prompts[3]["prompt"])
    assert [
        event["event"] for event in events if event.get("tool") == "manuscript.compile_candidate"
    ] == [
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.completed",
    ]


def test_writer_retries_identical_edit_after_transient_compiler_exception() -> None:
    edit = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "A compilable result.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am validating the results edit.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": edit},
            },
            {
                "action": "tool",
                "update": "The temporary compiler was unavailable, so I am retrying.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": edit},
            },
            {
                "action": "finish",
                "update": "The retried candidate passed.",
                "final": {
                    "reply": "The validated edit is ready for review.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compile_attempts = 0

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        nonlocal compile_attempts
        compile_attempts += 1
        if compile_attempts == 1:
            raise RuntimeError("temporary compiler outage")
        assert edits[0]["replace"] == "A compilable result."
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message="Replace the placeholder with a final results sentence.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert compile_attempts == 2
    assert turn.edits[0]["replace"] == "A compilable result."
    assert [
        event["event"] for event in events if event.get("tool") == "manuscript.compile_candidate"
    ] == ["tool.started", "tool.failed", "tool.started", "tool.completed"]


def test_writer_agent_observes_compile_failure_and_rechecks_repaired_edits() -> None:
    broken = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "\\undefinedcommand{Broken}",
    }
    repaired = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "A compilable result.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am compiling the exact draft before finishing.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results-sentence", "edit": broken},
            },
            {
                "action": "tool",
                "update": "I repaired the LaTeX error and am compiling again.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results-sentence", "edit": repaired},
            },
            {
                "action": "finish",
                "update": "The repaired candidate is ready for review.",
                "final": {
                    "reply": "The compilable edit is ready for review.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        if "undefinedcommand" in str(edits[0]["replace"]):
            return {
                "status": "failed",
                "errors": [{"line": 2, "message": "Undefined control sequence"}],
                "log_tail": "undefinedcommand",
            }
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message="Replace the placeholder with a final results sentence.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert len(compiled) == 2
    assert compiled[0][0]["replace"] == "\\undefinedcommand{Broken}"
    assert compiled[1][0]["replace"] == "A compilable result."
    assert "Undefined control sequence" in str(pool.prompts[1]["prompt"])
    assert turn.edits[0]["replace"] == "A compilable result."
    assert turn.verification == {"status": "passed", "errors": [], "log_tail": "ok"}
    assert [
        event["event"] for event in events if event.get("tool") == "manuscript.compile_candidate"
    ] == ["tool.started", "tool.failed", "tool.started", "tool.completed"]


def test_compile_repair_allows_project_diagnostics_and_blocks_evidence_reads() -> None:
    broken = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "\\undefinedcommand{Broken}",
    }
    repaired = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "A compilable result.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": broken},
            },
            {
                "action": "tool",
                "update": "Inspect the source around the compile error.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 1, "end_line": 20},
            },
            {
                "action": "tool",
                "update": "Locate the exact failing anchor.",
                "tool": "search_project",
                "arguments": {"query": "Placeholder."},
            },
            {
                "action": "tool",
                "update": "Read the linked evidence instead.",
                "tool": "read_source",
                "arguments": {"handle": "source:1"},
            },
            {
                "action": "tool",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": repaired},
            },
            {
                "action": "finish",
                "final": {
                    "reply": "The compilable edit is ready for review.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        if "undefinedcommand" in str(edits[0]["replace"]):
            return {
                "status": "failed",
                "errors": [{"line": 2, "message": "Undefined control sequence"}],
                "log_tail": "undefinedcommand",
            }
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message=(
                "Use the linked source as context and replace the placeholder with a "
                "final results sentence."
            ),
            citations=[],
            assets=[],
            works=[],
            history=[],
            attachments=[{"filename": "Notes.txt", "text": "Supporting notes."}],
            candidate_compiler=compile_candidate,
        )

    assert len(compiled) == 2
    assert turn.edits[0]["replace"] == repaired["replace"]
    assert turn.tools_used == (
        "compile_candidate",
        "read_file",
        "search_project",
        "compile_candidate",
    )
    assert "allowed_tools" in str(pool.prompts[4]["prompt"])
    read_events = [
        event["event"]
        for event in events
        if event.get("tool")
        in {
            "manuscript.read_file",
            "manuscript.search_project",
        }
    ]
    assert read_events == [
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.completed",
    ]
    assert not any(event.get("tool") == "manuscript.read_source" for event in events)
    assert not any(
        event["event"] == "tool.failed"
        for event in events
        if event.get("tool") == "manuscript.read_source"
    )


def test_writer_full_report_cannot_finish_with_template_residue() -> None:
    source = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\title{Conference Paper Title}\n"
        "\\maketitle\n\n"
        "\\section{Introduction}\n"
        "Motivate the problem and preview the contribution in three paragraphs.\n\n"
        "\\section{Results}\n"
        "Placeholder results with Metric A and Metric B.\n\n"
        "\\section{Conclusion}\n"
        "The claim, the evidence for it, and the one open problem this result exposes.\n"
        "\\end{document}\n"
    )
    title = {
        "path": "main.tex",
        "find": "\\title{Conference Paper Title}",
        "replace": "\\title{Interview-based App Experience Report}",
    }
    introduction = {
        "path": "main.tex",
        "find": "Motivate the problem and preview the contribution in three paragraphs.",
        "replace": "This report analyses the linked interview evidence.",
    }
    results = {
        "path": "main.tex",
        "find": "Placeholder results with Metric A and Metric B.",
        "replace": "The interview identifies connection reliability as the main issue.",
    }
    conclusion = {
        "path": "main.tex",
        "find": ("The claim, the evidence for it, and the one open problem this result exposes."),
        "replace": "The findings motivate more reliable device pairing.",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am inventorying the initial template before drafting.",
                "tool": "inspect_document_structure",
                "arguments": {},
            },
            {
                "action": "tool",
                "update": "I am validating the report title.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "title", "edit": title},
            },
            {
                "action": "tool",
                "update": "I am replacing the template introduction.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "introduction", "edit": introduction},
            },
            {
                "action": "finish",
                "update": "The report is complete.",
                "final": {
                    "reply": "The full report is ready.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
            {
                "action": "tool",
                "update": "I am replacing the remaining dummy results.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "results", "edit": results},
            },
            {
                "action": "tool",
                "update": "I am replacing the final instructional section.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "conclusion", "edit": conclusion},
            },
            {
                "action": "finish",
                "update": "The complete cleaned report is ready for review.",
                "final": {
                    "reply": "The complete report and boilerplate cleanup are ready.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [],
                },
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message="Write a complete report and remove all boilerplate from the template.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert [len(candidate) for candidate in compiled] == [1, 2, 3, 4]
    assert len(turn.edits) == 4
    assert any(
        event.get("event") == "checkpoint.failed"
        and "marker" in str(event.get("detail") or "").casefold()
        for event in events
    )
    compile_results = [
        event
        for event in events
        if event.get("event") == "tool.completed"
        and event.get("tool") == "manuscript.compile_candidate"
    ]
    assert (compile_results[1].get("output") or {}).get("remaining_boilerplate_markers") == 4
    assert (compile_results[-1].get("output") or {}).get("remaining_boilerplate_markers") == 0


def test_writer_successful_staged_compile_recovers_an_empty_terminal_payload() -> None:
    source = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\title{Report}\n"
        "}\n"
        "\\maketitle\n"
        "\\end{document}\n"
    )
    edit = {
        "path": "main.tex",
        "find": "\\title{Report}\n}\n\\maketitle",
        "replace": "\\title{Report}\n\\maketitle",
    }
    pool = _SequencePool(
        [
            {
                "action": "tool",
                "update": "I am reading the exact failing source lines.",
                "tool": "read_file",
                "arguments": {"path": "main.tex", "start_line": 3, "end_line": 5},
            },
            {
                "action": "tool",
                "update": "I found the stray brace and am compiling the exact fix.",
                "tool": "compile_candidate",
                "arguments": {"step_id": "remove-stray-brace", "edit": edit},
            },
            {
                "action": "finish",
                "update": "The exact correction compiled successfully.",
                "final": {},
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": [], "log_tail": "main.pdf written"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message=(
                "The document fails to compile. Diagnose the error and propose an edit "
                "that fixes it."
            ),
            citations=[],
            assets=[],
            works=[],
            history=[],
            compile_errors=[{"line": 4, "message": "Too many }'s"}],
            compile_log="main.tex:4: Too many }'s",
            response_language="de",
            candidate_compiler=compile_candidate,
        )

    assert len(pool.prompts) == 3
    assert len(compiled) == 1
    assert turn.edits[0]["replace"] == "\\title{Report}\n\\maketitle"
    assert turn.reply.startswith(
        "Der exakte Manuskriptvorschlag wurde erfolgreich kompiliert und ist zur Prüfung bereit."
    )
    assert "noch nicht angewendet" in turn.reply
    compile_completed = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "tool.completed"
        and event.get("tool") == "manuscript.compile_candidate"
    )
    assert not any(
        event.get("event") == "checkpoint.failed" for event in events[compile_completed + 1 :]
    )


def test_legacy_writer_final_observes_compile_failure_and_repairs_it() -> None:
    broken = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "\\undefinedcommand{Broken}",
    }
    repaired = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "A compilable result.",
    }
    pool = _SequencePool(
        [
            {
                "reply": "The requested edit is ready.",
                "edits": [broken],
                "visual_request": None,
                "workspace_actions": [],
            },
            {
                "reply": "The corrected edit is ready.",
                "edits": [repaired],
                "visual_request": None,
                "workspace_actions": [],
            },
        ]
    )
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        if "undefinedcommand" in str(edits[0]["replace"]):
            return {
                "status": "failed",
                "errors": [{"line": 2, "message": "Undefined control sequence"}],
                "log_tail": "undefinedcommand",
            }
        return {"status": "passed", "errors": [], "log_tail": "ok"}

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message="Replace the placeholder with a final results sentence.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert len(compiled) == 2
    assert "Undefined control sequence" in str(pool.prompts[1]["prompt"])
    assert turn.edits[0]["replace"] == "A compilable result."
    assert turn.verification == {"status": "passed", "errors": [], "log_tail": "ok"}
    assert [
        event["event"] for event in events if event.get("tool") == "manuscript.compile_candidate"
    ] == ["tool.started", "tool.failed", "tool.started", "tool.completed"]


def test_writer_exhausted_compile_repair_never_returns_the_broken_patch() -> None:
    broken = {
        "reply": "The edit is ready.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder.",
                "replace": "\\undefinedcommand{Broken}",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _FixedPool(broken)
    compiled: list[list[dict[str, Any]]] = []

    def compile_candidate(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {
            "status": "failed",
            "errors": [{"line": 2, "message": "Undefined control sequence"}],
            "log_tail": "undefinedcommand",
        }

    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message="Replace the placeholder with the final results sentence.",
            citations=[],
            assets=[],
            works=[],
            history=[],
            candidate_compiler=compile_candidate,
        )

    assert len(compiled) == 1
    assert turn.edits == []
    assert turn.verification is not None
    assert turn.verification["status"] == "failed"
    assert "did not compile" in turn.reply
    assert "iteration" not in turn.reply.casefold()
    assert "limit" not in turn.reply.casefold()
    assert not any(event.get("event") == "change.proposed" for event in events)
    assert not any(
        event.get("label") in {"Requested outcome verified", "Manuscript proposal validated"}
        for event in events
    )
    assert events[-1]["event"] == "tool.failed"


def test_writer_missing_data_plot_is_rejected_before_completion() -> None:
    visual = {
        "reply": "The requested plot is ready.",
        "edits": [],
        "visual_request": {
            "prompt": "Draw a quantitative bar chart comparing Alpha and Beta performance.",
            "kind": "plot",
            "aspect_ratio": "4:3",
            "resolution": "2k",
            "review_passes": 1,
        },
        "workspace_actions": [],
    }

    class NativeThenLegacyPool:
        def __init__(self) -> None:
            self.prompts: list[dict[str, object]] = []

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args
            self.prompts.append(kwargs)
            if "AVAILABLE TOOLS" in str(kwargs.get("system") or ""):
                value = {
                    "action": "finish",
                    "update": "Prepared the requested quantitative plot.",
                    "final": visual,
                }
            else:
                value = visual
            return SimpleNamespace(text=json.dumps(value))

    pool = NativeThenLegacyPool()
    events: list[dict[str, Any]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nNo measurements are available."},
            active_path="main.tex",
            message="Create a quantitative bar chart comparing Alpha and Beta performance.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert turn.visual_request is None
    assert turn.edits == []
    assert "quantitative plot needs" in turn.reply.casefold()
    assert "source anchor" not in turn.reply.casefold()
    assert not any(
        event.get("event") == "checkpoint.completed"
        and event.get("tool") == "manuscript.verify_completion"
        for event in events
    )
    assert not any(event.get("event") == "change.proposed" for event in events)
    assert events[-1]["event"] == "tool.failed"


def test_writer_existing_uploaded_visual_cannot_be_replaced_by_a_new_render() -> None:
    pool = _FixedPool(
        {
            "reply": "I prepared a new render.",
            "edits": [],
            "visual_request": {
                "prompt": "Render a new conceptual figure for the manuscript.",
                "kind": "concept",
                "aspect_ratio": "4:3",
                "resolution": "2k",
                "review_passes": 1,
            },
            "workspace_actions": [],
        }
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Results}\nPlaceholder."},
            active_path="main.tex",
            message="Use the existing uploaded figure figure.png in the manuscript.",
            citations=[],
            assets=["figure.png"],
            works=[],
            history=[],
        )

    assert turn.visual_request is None
    assert turn.edits == []
    assert "new render" not in turn.reply.casefold()
    assert "existing uploaded visual" in turn.reply.casefold()
    assert not any(event.get("event") == "change.proposed" for event in events)
    assert not any(
        event.get("event") == "checkpoint.completed"
        and event.get("tool") == "manuscript.verify_completion"
        for event in events
    )


def test_writer_redirects_create_visual_workspace_action_into_visual_request() -> None:
    long_visual_prompt = (
        "Scientific objective:\nExplain the manuscript method.\n\n"
        "Renderer brief:\n"
        + "Draw the exact manuscript data collection and analysis flow. "
        * 250
        + "TAIL_WRITER_VISUAL_MARKER\n\n"
        "Integrity and reading order:\nKeep every relationship evidence-backed."
    )
    raw_visual_action = {
        "reply": "The method diagram is ready.",
        "edits": [],
        "visual_request": None,
        "workspace_actions": [
            {
                "type": "create_visual",
                "title": "Method flow",
                "prompt": "Draw the manuscript method flow.",
                "kind": "flow",
            }
        ],
    }
    corrected = {
        "reply": "The grounded method diagram is ready for review.",
        "edits": [],
        "visual_request": {
            "prompt": long_visual_prompt,
            "kind": "flow",
            "aspect_ratio": "4:3",
            "resolution": "2k",
            "review_passes": 1,
        },
        "workspace_actions": [],
    }
    pool = _SequencePool(
        [
            {
                "action": "finish",
                "update": "Prepared the visual action.",
                "final": raw_visual_action,
            },
            {
                "action": "finish",
                "update": "Moved the renderer request into the Writer field.",
                "final": corrected,
            },
        ]
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Methods}\nData collection precedes analysis."},
            active_path="main.tex",
            message="Create a method flow diagram for this manuscript.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert "put the requested figure in visual_request" in str(pool.prompts[1]["prompt"])
    assert turn.visual_request is not None
    assert "data collection" in turn.visual_request["prompt"]
    assert "TAIL_WRITER_VISUAL_MARKER" in turn.visual_request["prompt"]
    assert 14_000 < len(long_visual_prompt) < 16_000
    assert turn.workspace_actions == []
    assert (
        sum(
            event.get("event") == "checkpoint.completed"
            and event.get("tool") == "manuscript.verify_completion"
            for event in events
        )
        == 1
    )


def test_writer_repairs_a_partially_discarded_workspace_action_set() -> None:
    rename_action = {
        "type": "manage_resource",
        "title": "Rename manuscript",
        "operation": "rename",
        "resource_type": "manuscript",
        "selector": "the open resource",
        "new_name": "Polished copy",
        "destination": "",
        "resource_status": "",
    }
    pool = _SequencePool(
        [
            {
                "action": "finish",
                "update": "Prepared the workspace actions.",
                "final": {
                    "reply": "Both workspace actions are ready.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [
                        rename_action,
                        {"type": "create_dataset", "title": "Unrequested dataset"},
                    ],
                },
            },
            {
                "action": "finish",
                "update": "Removed the unauthorized action.",
                "final": {
                    "reply": "The manuscript rename is ready for confirmation.",
                    "edits": [],
                    "visual_request": None,
                    "workspace_actions": [rename_action],
                },
            },
        ]
    )

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\section{Introduction}\nDraft."},
        active_path="main.tex",
        message="Rename this manuscript to Polished copy.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert "repair every discarded action" in str(pool.prompts[1]["prompt"])
    assert len(turn.workspace_actions) == 1
    assert turn.workspace_actions[0]["type"] == "manage_resource"
    assert turn.workspace_actions[0]["new_name"] == "Polished copy"
    assert turn.reply == "The manuscript rename is ready for confirmation."


def test_writer_recovery_fails_closed_for_create_visual_workspace_action() -> None:
    pool = _FixedPool(
        {
            "reply": "The method diagram is ready.",
            "edits": [],
            "visual_request": None,
            "workspace_actions": [
                {
                    "type": "create_visual",
                    "title": "Method flow",
                    "prompt": "Draw the manuscript method flow.",
                    "kind": "flow",
                }
            ],
        }
    )
    events: list[dict[str, Any]] = []

    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\section{Methods}\nData collection precedes analysis."},
            active_path="main.tex",
            message="Create a method flow diagram for this manuscript.",
            citations=[],
            assets=[],
            works=[],
            history=[],
        )

    assert turn.visual_request is None
    assert turn.workspace_actions == []
    assert "put the requested figure in visual_request" in turn.reply
    assert "diagram is ready" not in turn.reply.casefold()
    assert not any(
        event.get("event") == "checkpoint.completed"
        and event.get("tool") == "manuscript.verify_completion"
        for event in events
    )
    assert not any(event.get("event") == "change.proposed" for event in events)
    assert events[-1]["event"] == "tool.failed"


def test_writer_final_reply_removes_internal_loop_disclosures() -> None:
    pool = _FixedPool(
        {
            "reply": (
                "I prepared the selected rewrite. "
                "I stopped because the 36 tool-call limit was reached."
            ),
            "edits": [
                {
                    "path": "main.tex",
                    "find": "Old title.",
                    "replace": "Clear title.",
                }
            ],
            "visual_request": None,
            "workspace_actions": [],
        }
    )

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\section{Title}\nOld title."},
        active_path="main.tex",
        message="Rewrite this selected sentence more clearly.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        selection={
            "kind": "source",
            "path": "main.tex",
            "line": 2,
            "quote": "Old title.",
        },
    )

    assert len(pool.prompts) == 1
    assert turn.edits[0]["replace"] == "Clear title."
    assert turn.reply.startswith("I prepared the selected rewrite.")
    assert "has not been applied" in turn.reply
    assert "tool-call" not in turn.reply.casefold()
    assert "limit" not in turn.reply.casefold()


def test_writer_agent_requires_a_passage_receipt_before_new_citation() -> None:
    cited_edit = {
        "path": "main.tex",
        "find": "Placeholder.",
        "replace": "Transformers use self-attention \\citep{Vaswani2017}.",
    }
    final = {
        "reply": "I prepared the cited sentence.",
        "edits": [cited_edit],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool(
        [
            {
                "action": "finish",
                "update": "I drafted the cited sentence.",
                "final": final,
            },
            {
                "action": "tool",
                "update": "I am reading the linked paper before citing it.",
                "tool": "read_source",
                "arguments": {"handle": "paper:1"},
            },
            {
                "action": "finish",
                "update": "The citation is grounded and ready.",
                "final": final,
            },
        ]
    )

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\section{Background}\nPlaceholder."},
        active_path="main.tex",
        message="Replace the placeholder with a cited transformer statement.",
        citations=[
            {
                "key": "Vaswani2017",
                "title": "Attention Is All You Need",
                "year": 2017,
            }
        ],
        assets=[],
        works=[
            WorkRecord(
                id="W1",
                title="Attention Is All You Need",
                abstract="The Transformer is based solely on attention mechanisms.",
                year=2017,
            )
        ],
        history=[],
    )

    assert "A catalog entry alone is not evidence" in str(pool.prompts[1]["prompt"])
    assert "attention mechanisms" in str(pool.prompts[2]["prompt"])
    assert turn.edits[0]["applicable"] is True
    assert "\\citep{Vaswani2017}" in turn.edits[0]["replace"]


def test_paper_metadata_without_abstract_does_not_create_a_citation_receipt() -> None:
    receipts: set[str] = set()
    tools = {
        tool.name: tool
        for tool in _writer_evidence_tools(
            works=[
                WorkRecord(id="W1", title="Metadata Only Paper", abstract=None),
                WorkRecord(
                    id="W2",
                    title="Supported Paper",
                    abstract="The observed intervention reduced the measured delay.",
                ),
            ],
            citations=[
                {"key": "Metadata2026", "title": "Metadata Only Paper"},
                {"key": "Supported2026", "title": "Supported Paper"},
            ],
            attachments=[
                {
                    "filename": "field-notes.pdf (cite as Notes2026)",
                    "text": "The field notes contain an exact supporting passage.",
                }
            ],
            datasets=[],
            interview_evidence=[],
            survey_evidence=[],
            citation_receipts=receipts,
        )
    }

    metadata = tools["read_source"].handler({"handle": "paper:1"})
    metadata_output = cast(dict[str, Any], metadata.output)
    assert metadata.success
    assert metadata_output["citation_key"] == "Metadata2026"
    assert metadata_output["citation_receipt"] is None
    assert receipts == set()

    title_match = tools["search_evidence"].handler({"query": "Metadata Only Paper"})
    title_output = cast(dict[str, Any], title_match.output)
    assert title_output["matches"][0]["citation_receipt"] is None
    assert receipts == set()

    supported = tools["read_source"].handler({"handle": "paper:2"})
    assert cast(dict[str, Any], supported.output)["citation_receipt"] == "Supported2026"
    assert receipts == {"Supported2026"}

    attachment = tools["read_source"].handler({"handle": "source:1"})
    assert cast(dict[str, Any], attachment.output)["citation_receipt"] == "Notes2026"
    assert receipts == {"Supported2026", "Notes2026"}
