"""Read-only manuscript questions finish without manufacturing edit proposals."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.agent.events import agent_event_sink
from sixsentences_server.writer.assistant import _requests_manuscript_mutation, run_assistant_turn

_REQUEST = (
    "Nur prüfen, nichts ändern: Welche fiktiven Gesamtzahlen stehen aktuell im Abschnitt "
    "Observations? Wurden konkrete Aufgabennummern übernommen? Antworte kurz auf Deutsch "
    "und unterscheide den gespeicherten Text von früher verworfenen Vorschlägen."
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (_REQUEST, False),
        ("Nur prüfen, nichts ändern.", False),
        ("Noch nichts ändern; zeig mir nur den gespeicherten Text.", False),
        ("Change nothing; just inspect the saved text.", False),
        ("Do not change anything. Tell me what is saved.", False),
        ("Nichts ändern, aber korrigiere den Titel.", True),
        ("Change nothing, but change the title.", True),
        ("Schreib den Absatz neu, nichts am Titel ändern.", True),
        ("Rewrite the paragraph; do not change anything else.", True),
        ("Korrigiere den Titel nicht, zeig ihn nur.", False),
        ("Are the saved totals correct?", False),
        ("Wurden korrigierte Werte übernommen?", False),
    ],
)
def test_readonly_negation_does_not_create_an_edit_requirement(
    message: str,
    expected: bool,
) -> None:
    assert _requests_manuscript_mutation(message) is expected


@pytest.mark.parametrize(
    "message",
    [
        _REQUEST,
        "Nur prüfen, nichts ändern: Welche Aussagen stehen in beiden Abschnitten "
        "Introduction und Conclusion? Antworte kurz auf Deutsch.",
    ],
)
def test_grounded_readonly_answer_finishes_after_reading_without_edit_validation(
    message: str,
) -> None:
    source = (
        "\\section{Introduction}\nSynthetic QA only.\n"
        "\\section{Observations}\nFive fictional tasks: three successes and two failures.\n"
        "\\section{Conclusion}\nThese totals are synthetic, not research findings.\n"
    )
    answer = (
        "Gespeichert sind fünf fiktive Aufgaben, davon drei erfolgreich und zwei fehlgeschlagen. "
        "Konkrete Aufgabennummern stehen dort nicht. Frühere verworfene Vorschläge "
        "sind nicht Bestandteil dieses gespeicherten Texts."
    )
    decisions = [
        {
            "action": "tool",
            "update": "Reading the current saved manuscript.",
            "tool": "read_file",
            "arguments": {"path": "main.tex"},
        },
        {
            "action": "finish",
            "update": "The saved source has been inspected.",
            "final": {
                "reply": answer,
                "edits": [],
                "visual_request": None,
                "workspace_actions": [],
            },
        },
    ]
    calls: list[dict[str, Any]] = []
    compile_calls: list[list[dict[str, Any]]] = []
    events: list[dict[str, Any]] = []

    class SequencePool:
        cancel_check = None

        def complete(self, *args: object, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            assert decisions, "A read-only final answer must not enter the edit-repair loop."
            return SimpleNamespace(text=json.dumps(decisions.pop(0)))

    def compiler(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compile_calls.append(edits)
        return {"status": "passed", "errors": []}

    project = {"main.tex": source}
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            SequencePool(),  # type: ignore[arg-type]
            project_files=project,
            active_path="main.tex",
            message=message,
            citations=[],
            assets=[],
            works=[],
            response_language="de",
            history=[
                {"role": "user", "content": "Rewrite Observations with task numbers."},
                {"role": "assistant", "content": "Proposal: tasks 1, 2 and 3 succeeded."},
                {"role": "user", "content": "Discard that proposal; keep the saved totals."},
            ],
            candidate_compiler=compiler,
        )

    assert len(calls) == 2 and decisions == []
    assert turn.reply == answer
    assert turn.edits == [] and turn.workspace_actions == [] and turn.visual_request is None
    assert compile_calls == [] and project == {"main.tex": source}
    assert any(event.get("event") == "answer.completed" for event in events)
    assert not any(event.get("event") == "checkpoint.failed" for event in events)
