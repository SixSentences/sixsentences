"""Protected figures must not turn a text proposal into a visual repair loop."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.agent.actions import workspace_action_types_requested
from sixsentences_server.agent.events import agent_event_sink
from sixsentences_server.writer.assistant import run_assistant_turn

_SYNTHETIC_REQUEST = (
    "Release QA Gemini Pro 2026-09-08: Erstelle einen Änderungsvorschlag ausschließlich "
    "für den Absatz im Abschnitt Ergebnisse. Formuliere ihn in zwei klaren deutschen "
    "Sätzen um. Erhalte genau fünf fiktive Beobachtungen, drei in Gruppe A und zwei in "
    "Gruppe B sowie die ausdrückliche Aussage, dass dies keine reale Forschungsevidenz "
    "ist. Limitations, Abbildung und alle anderen Abschnitte unverändert lassen. "
    "Keine Quellen ergänzen, keine Websuche, nicht automatisch anwenden."
)


@pytest.mark.parametrize(
    "prompt",
    [
        _SYNTHETIC_REQUEST,
        "Erstelle einen Vorschlag für Ergebnisse. Abbildung und Titel unverändert lassen.",
        "Erstelle einen Vorschlag. Limitations, Abbildung und Titel unberührt lassen.",
        "Prepare the results proposal. Leave limitations, figures and all other sections unchanged.",
        "Rewrite Results. Keep the existing figure and caption untouched.",
        "Create the results proposal; leave the figure as-is.",
    ],
)
def test_protected_visual_list_does_not_require_creation(prompt: str) -> None:
    assert "create_visual" not in workspace_action_types_requested(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Erstelle eine neue Abbildung für den Ablauf.",
        "Die vorhandene Abbildung unverändert lassen. Erstelle eine zusätzliche Grafik.",
        "Keep the old figure unchanged; create a new diagram.",
        "Create a new figure; keep the old figure unchanged.",
        "Keep the existing figure unchanged and create a new diagram.",
        "Erstelle eine Abbildung und lass die Tabelle unverändert.",
        "Create a figure and leave the table unchanged.",
        "Create a figure, keep the table unchanged.",
        "Keep the existing figure unchanged; create a new diagram.",
        "Create a figure; the table remains unchanged.",
        "Create a figure, table unchanged.",
    ],
)
def test_positive_visual_request_survives_other_preservation_scopes(prompt: str) -> None:
    assert "create_visual" in workspace_action_types_requested(prompt)


@pytest.mark.parametrize("legacy_payload", [False, True])
def test_exact_ui_request_finishes_after_one_validation_without_visual_retries(
    legacy_payload: bool,
) -> None:
    original = (
        "Es wurden fünf fiktive Beobachtungen verwendet: drei in Gruppe A und zwei in "
        "Gruppe B. Dies ist keine reale Forschungsevidenz."
    )
    replacement = (
        "Die fünf fiktiven Beobachtungen verteilen sich auf drei in Gruppe A und zwei "
        "in Gruppe B. Dies ist keine reale Forschungsevidenz."
    )
    source = (
        "\\documentclass{article}\n\\begin{document}\n\\section{Ergebnisse}\n"
        + original
        + "\n\\section{Limitations}\nSynthetic limitations stay unchanged.\n"
        "\\section{Abbildung}\nSynthetic figure placeholder stays unchanged.\n"
        "\\end{document}\n"
    )
    payload = {
        "reply": "Der Vorschlag ist zur Prüfung bereit und wurde nicht angewendet.",
        "edits": [{"path": "main.tex", "find": original, "replace": replacement}],
        "visual_request": None,
        "workspace_actions": [],
    }
    response = payload if legacy_payload else {"action": "finish", "final": payload}
    calls: list[dict[str, Any]] = []

    class SyntheticPool:
        cancel_check = None

        def complete_json(self, *args: object, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            assert len(calls) <= 2, "A correct proposal must not incur repeated final retries."
            return SimpleNamespace(text=json.dumps(response, ensure_ascii=False))

        complete = complete_json

    compiled: list[list[dict[str, Any]]] = []

    def compiler(edits: list[dict[str, Any]]) -> dict[str, Any]:
        compiled.append(edits)
        return {"status": "passed", "errors": []}

    events: list[dict[str, Any]] = []
    project = {"main.tex": source}
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            SyntheticPool(),  # type: ignore[arg-type]
            project_files=project,
            active_path="main.tex",
            message=_SYNTHETIC_REQUEST,
            citations=[],
            assets=[],
            works=[],
            history=[],
            response_language="de",
            candidate_compiler=compiler,
        )

    assert len(calls) == (2 if legacy_payload else 1)
    assert len(compiled) == 1 and len(turn.edits) == 1
    assert turn.edits[0]["find"] == original and turn.edits[0]["replace"] == replacement
    assert turn.visual_request is None and turn.workspace_actions == []
    assert project == {"main.tex": source}
    assert not any(event.get("event") == "checkpoint.failed" for event in events)
    assert any(event.get("event") == "change.proposed" for event in events)
