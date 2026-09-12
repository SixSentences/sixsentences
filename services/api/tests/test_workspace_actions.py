"""Shared cross-feature agent action contract."""

import json
from types import SimpleNamespace

import pytest

from sixsentences_server.agent.actions import (
    analytical_chart_kind,
    bind_workspace_actions,
    control_only_workspace_actions,
    ensure_workspace_actions,
    normalize_workspace_actions,
    normalize_workspace_actions_for_request,
    propose_workspace_actions,
    workspace_action_confirmation_text,
    workspace_action_types_requested,
    workspace_actions_requested,
)
from sixsentences_server.agent.events import agent_event_sink
from sixsentences_server.datasets.service import run_dataset_agent
from sixsentences_server.interviews.analysis import run_interview_agent
from sixsentences_server.surveys.service import run_survey_agent
from sixsentences_server.voice.agent import run_study_agent
from sixsentences_server.writer.assistant import (
    is_local_writer_edit_request,
    local_writer_project_context_chars,
    run_assistant_turn,
    writer_selection_prompt,
)


class _ActionPool:
    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(
            text=json.dumps(
                {
                    "answer": "Prepared.",
                    "reply": "Prepared.",
                    "actions": [],
                    "edits": [],
                    "workspace_actions": [
                        {
                            "type": "create_survey",
                            "title": "Connected survey",
                            "questions": ["What should we measure?"],
                        }
                    ],
                }
            )
        )


def test_study_agent_scopes_generic_study_deletion_to_open_interview_study() -> None:
    class DeleteStudyPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "answer": "I prepared the protected deletion step.",
                        "actions": [],
                        "workspace_actions": [
                            {
                                "type": "manage_resource",
                                "title": "Delete this study",
                                "operation": "delete",
                                "resource_type": "interview_study",
                                "selector": "this study",
                            }
                        ],
                    }
                )
            )

    turn = run_study_agent(
        DeleteStudyPool(),  # type: ignore[arg-type]
        request="lösch die ganze studie",
        study={"title": "Open interview study"},
        history=[],
        language="de",
    )

    assert len(turn.workspace_actions) == 1
    action = turn.workspace_actions[0]
    assert action["type"] == "manage_resource"
    assert action["operation"] == "delete"
    assert action["resource_type"] == "interview_study"
    assert action["requires_confirmation"] is True


def test_selected_local_rewrite_uses_the_bounded_writer_fast_path() -> None:
    calls: list[dict[str, object]] = []
    pinned_models: list[str] = []

    class LocalEditPool:
        def pinned(self, ref: object) -> "LocalEditPool":
            pinned_models.append(str(getattr(ref, "model", "")))
            return self

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args
            calls.append(kwargs)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "reply": "I prepared the rewrite.",
                        "edits": [],
                        "workspace_actions": [],
                    }
                )
            )

    source = ("Before context.\n" * 1_500) + "Old text\n" + ("After context.\n" * 3_000)
    selection = {
        "kind": "source",
        "path": "main.tex",
        "line": 1_501,
        "quote": "Old text",
    }
    assert is_local_writer_edit_request(
        "Schreib das mal bitte um, egal was ist nur ein Beispiel",
        selection,
    )
    events: list[dict[str, object]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            LocalEditPool(),  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message="Schreib das mal bitte um, egal was ist nur ein Beispiel",
            citations=[{"key": "hidden", "title": "UNRELATED CITATION", "year": 2024}],
            assets=["unrelated-figure.png"],
            works=[],
            history=[
                {"role": "user", "content": "Earlier editing instruction"},
                {"role": "assistant", "content": "Recent context"},
                {"role": "user", "content": "Most recent context"},
            ],
            selection=selection,
            attachments=[{"filename": "hidden.pdf", "text": "UNRELATED PDF"}],
            datasets=["UNRELATED DATASET"],
            interview_evidence=[{"analysis_text": "UNRELATED INTERVIEW"}],
            survey_evidence=[{"summary_text": "UNRELATED SURVEY"}],
        )

    assert "Your manuscript is unchanged" in turn.reply
    assert "Select the passage or name the section" in turn.reply
    assert turn.edits == []
    assert events[-1]["event"] == "tool.failed"
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 900
    assert pinned_models == []
    assert "DIRECT RESPONSE MODE" in str(calls[0]["system"])
    prompt = str(calls[0]["prompt"])
    assert "LOCAL EDIT MODE" in prompt
    assert "Old text" in prompt
    # The bounded history keeps earlier instructions; unrelated workspace
    # evidence is still excluded from this local-edit fast path.
    assert "Earlier editing instruction" in prompt
    assert "Most recent context" in prompt
    assert "UNRELATED CITATION" not in prompt
    assert "UNRELATED PDF" not in prompt
    assert "UNRELATED DATASET" not in prompt
    assert "UNRELATED INTERVIEW" not in prompt
    assert "UNRELATED SURVEY" not in prompt
    assert len(prompt) < 20_000


def test_pdf_rewrite_fuzzily_locates_rendered_text_in_latex_source() -> None:
    calls: list[dict[str, object]] = []

    class LocalEditPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args
            calls.append(kwargs)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "reply": "Prepared.",
                        "edits": [],
                        "workspace_actions": [],
                    }
                )
            )

    target = (
        "The \\textbf{central problem} creates a measurable cost for "
        "research teams and slows the review process."
    )
    source = ("irrelevant preface\n" * 2_000) + target + ("\nappendix" * 2_000)
    events: list[dict[str, object]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            LocalEditPool(),  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message="Please rewrite this paragraph more clearly",
            citations=[],
            assets=[],
            works=[],
            history=[],
            selection={
                "kind": "pdf",
                "page": 3,
                "quote": (
                    "The central problem creates a measurable cost for research "
                    "teams and slows the review process."
                ),
            },
        )

    assert "Your manuscript is unchanged" in turn.reply
    assert "Select the passage or name the section" in turn.reply
    assert turn.edits == []
    assert events[-1]["event"] == "tool.failed"
    assert len(calls) == 1
    prompt = str(calls[0]["prompt"])
    assert "central problem" in prompt
    assert "SELECTION REGION" in prompt
    assert len(prompt) < 20_000


def test_writer_pdf_selection_keeps_complete_text_beyond_legacy_limit() -> None:
    calls: list[dict[str, object]] = []

    class LocalEditPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args
            calls.append(kwargs)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "reply": "Prepared.",
                        "edits": [],
                        "workspace_actions": [],
                    }
                )
            )

    middle = "This complete selected paragraph must remain available. " * 180
    tail = "SELECTION_TAIL_AFTER_SIX_HUNDRED"
    normalized_quote = f"Das ist unseriös. {middle}{tail}"
    raw_pdf_quote = f"Das ist unser-\n iös. {middle}{tail}"
    source = ("Preface context.\n" * 400) + normalized_quote + ("\nAppendix." * 1_000)
    selection = {
        "kind": "pdf",
        "page": 2,
        "page_end": 3,
        "quote": raw_pdf_quote,
    }

    run_assistant_turn(
        LocalEditPool(),  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message="Please rewrite this selected passage more clearly",
        citations=[],
        assets=[],
        works=[],
        history=[],
        selection=selection,
    )

    prompt = str(calls[0]["prompt"])
    assert len(raw_pdf_quote) > 600
    assert "Das ist unseriös" in prompt
    assert tail in prompt
    assert "pages 2\u20133" in prompt
    measured = local_writer_project_context_chars(
        {"main.tex": source},
        "main.tex",
        selection,
    )
    assert measured > 18_000
    assert measured <= 30_000
    segmented = {
        **selection,
        "segments": [
            {"quote": raw_pdf_quote, "page": 2, "page_end": 3},
        ],
    }
    assert len(writer_selection_prompt(segmented, "main.tex")) == len(
        writer_selection_prompt(selection, "main.tex")
    )


def test_local_rewrite_recovers_invalid_json_on_selected_writer_route() -> None:
    calls: list[str] = []

    class WriterPool:
        def __init__(self) -> None:
            self.responses = [
                "This is not JSON",
                json.dumps(
                    {
                        "reply": "I recovered the proposal.",
                        "edits": [],
                        "workspace_actions": [],
                    }
                ),
            ]

        def pinned(self, ref: object) -> "WriterPool":
            raise AssertionError(f"the selected model must not be overridden: {ref}")

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            calls.append("writer")
            return SimpleNamespace(text=self.responses.pop(0))

    turn = run_assistant_turn(
        WriterPool(),  # type: ignore[arg-type]
        project_files={"main.tex": "A paragraph to rewrite."},
        active_path="main.tex",
        message="Rewrite this paragraph",
        citations=[],
        assets=[],
        works=[],
        history=[],
        selection={
            "kind": "source",
            "path": "main.tex",
            "line": 1,
            "quote": "A paragraph to rewrite.",
        },
    )

    assert calls == ["writer", "writer"]
    assert "Your manuscript is unchanged" in turn.reply
    assert "Select the passage or name the section" in turn.reply
    assert turn.edits == []


@pytest.mark.parametrize(
    "message,has_compile_errors",
    [
        ("Rewrite this paragraph and add two citations", False),
        ("Schreib das mit den Survey Ergebnissen um", False),
        ("Schreib das bitte um", True),
    ],
)
def test_evidence_and_compile_edits_keep_full_writer_context(
    message: str,
    has_compile_errors: bool,
) -> None:
    assert not is_local_writer_edit_request(
        message,
        {"kind": "source", "quote": "Old text"},
        has_compile_errors=has_compile_errors,
    )


def test_normalizes_editable_survey_and_interview_proposals() -> None:
    actions = normalize_workspace_actions(
        [
            {
                "type": "create_survey",
                "title": "  Research practice  ",
                "project_id": 999,
                "questions": [
                    {
                        "title": "Role",
                        "type": "single_choice",
                        "options": ["Student", "Student", "Researcher"],
                    },
                    {"title": "", "type": "long_text"},
                ],
            },
            {
                "type": "create_ai_interview",
                "title": "Research workflow interview",
                "language": "de",
                "sections": [
                    {
                        "title": "Einstieg",
                        "question": "Wie beginnen Sie eine Literaturrecherche?",
                        "probes": ["Nennen Sie ein konkretes Beispiel."],
                    }
                ],
            },
        ]
    )

    assert [action["type"] for action in actions] == [
        "create_survey",
        "create_ai_interview",
    ]
    assert actions[0]["title"] == "Research practice"
    assert actions[0]["questions"][0]["options"] == ["Student", "Researcher"]
    assert "project_id" not in actions[0]
    assert actions[1]["language"] == "de"
    assert actions[1]["sections"][0]["must_cover"] is True
    assert all(action["requires_confirmation"] is True for action in actions)


def test_rejects_invalid_actions_and_deduplicates_proposals() -> None:
    valid = {
        "type": "create_visual",
        "title": "Evidence flow",
        "prompt": "Show the complete evidence flow from records to a supported claim.",
        "kind": "not-a-kind",
        "aspect_ratio": "panorama",
        "resolution": "8k",
        "review_passes": 99,
    }
    actions = normalize_workspace_actions(
        [
            valid,
            valid,
            {"type": "delete_everything", "title": "No"},
            {"type": "start_review", "title": "Missing question"},
        ]
    )

    assert len(actions) == 1
    assert actions[0]["kind"] == "concept"
    assert actions[0]["aspect_ratio"] == "4:3"
    assert actions[0]["resolution"] == "2k"
    assert actions[0]["review_passes"] == 2


def test_normalizes_only_allowlisted_workspace_control_values() -> None:
    actions = normalize_workspace_actions(
        [
            {"type": "set_theme", "title": "Use light mode", "theme": "light"},
            {"type": "set_language", "language": "de"},
            {
                "type": "update_assistant_preferences",
                "preferences": {
                    "detail": "concise",
                    "tone": "direct",
                    "format": "structured",
                    "custom_instructions": "Lead with the result.",
                    "unknown": "ignored",
                },
            },
            {"type": "connect_reference_manager", "provider": "zotero"},
        ]
    )

    assert [action["type"] for action in actions] == [
        "set_theme",
        "set_language",
        "update_assistant_preferences",
        "connect_reference_manager",
    ]
    assert actions[0]["theme"] == "light"
    assert actions[1]["language"] == "de"
    assert actions[2]["preferences"] == {
        "detail": "concise",
        "tone": "direct",
        "format": "structured",
        "custom_instructions": "Lead with the result.",
    }
    assert actions[3]["section"] == "integrations"
    assert all(action["requires_confirmation"] for action in actions)

    assert (
        normalize_workspace_actions(
            [
                {"type": "open_settings", "section": "api-keys"},
                {"type": "set_theme", "theme": "neon"},
                {"type": "connect_reference_manager", "provider": "unknown"},
            ]
        )[0]["section"]
        == "api-keys"
    )


@pytest.mark.parametrize(
    ("prompt", "operation", "resource_type"),
    [
        ("Öffne mein Manuskript Method Paper.", "open", "manuscript"),
        ("Benenne das Manuskript Method Paper um.", "rename", "manuscript"),
        ("Lösch bitte die Umfrage Onboarding 2026.", "delete", "survey"),
        ("Verschiebe den Datensatz Pilotdaten in Projekt Alpha.", "move", "dataset"),
        ("Archiviere das Projekt Terraform Studie.", "update_status", "project"),
        ("Schließe die Survey Follow-up.", "update_status", "survey"),
        ("Delete the visual Evidence Flow.", "delete", "visual"),
        ("Move interview Participant 04 to project Thesis.", "move", "interview"),
        ("Öffne die KI-Interviewstudie Nutzerforschung.", "open", "interview_study"),
        ("Lösche das Paper ORB-SLAM3 aus meiner Library.", "delete", "library_paper"),
        (
            "Häng das Paper ORB-SLAM3 aus meiner Library an mein Manuskript Thesis.",
            "attach_to_manuscript",
            "library_paper",
        ),
        (
            "Verknüpfe den Datensatz Experiment 2 mit meinem Manuskript Results.",
            "attach_to_manuscript",
            "dataset",
        ),
        (
            "Füge die Survey Pilot Study meinem Manuskript Thesis hinzu.",
            "attach_to_manuscript",
            "survey",
        ),
        (
            "Attach review LLM Screening to manuscript Journal Draft.",
            "attach_to_manuscript",
            "review",
        ),
    ],
)
def test_existing_workspace_resources_use_shared_confirmed_actions(
    prompt: str,
    operation: str,
    resource_type: str,
) -> None:
    assert workspace_action_types_requested(prompt) == ("manage_resource",)
    [action] = normalize_workspace_actions(
        [
            {
                "type": "manage_resource",
                "title": "Manage item",
                "operation": operation,
                "resource_type": resource_type,
                "selector": "Exact resource name",
                "new_name": "New name" if operation == "rename" else "",
                "destination": "Target" if operation in {"move", "attach_to_manuscript"} else "",
                "resource_status": (
                    "archived"
                    if operation == "update_status" and resource_type == "project"
                    else "closed"
                    if operation == "update_status"
                    else ""
                ),
            }
        ]
    )
    assert action["operation"] == operation
    assert action["resource_type"] == resource_type
    assert action["requires_confirmation"] is True


def test_resource_actions_fail_closed_for_how_to_and_invalid_combinations() -> None:
    assert workspace_action_types_requested("Wie kann ich ein Manuskript löschen?") == ()
    assert workspace_action_types_requested("Erkläre mir, wie Surveys archiviert werden.") == ()
    assert (
        workspace_action_types_requested("Welche LLM Paper sind jetzt in meiner Bibliothek?") == ()
    )
    assert workspace_action_types_requested("List all papers in my Library.") == ()
    assert (
        normalize_workspace_actions(
            [
                {
                    "type": "manage_resource",
                    "operation": "delete",
                    "resource_type": "account",
                    "selector": "everything",
                },
                {
                    "type": "manage_resource",
                    "operation": "rename",
                    "resource_type": "library_paper",
                    "selector": "A shared scholarly title",
                    "new_name": "Invented title",
                },
                {
                    "type": "manage_resource",
                    "operation": "update_status",
                    "resource_type": "visual",
                    "selector": "Figure 1",
                    "resource_status": "live",
                },
            ]
        )
        == []
    )


def test_read_only_library_open_and_new_outputs_do_not_create_manage_cards() -> None:
    assert workspace_action_types_requested(
        "Kannst du ein Paper aus meiner Library öffnen, egal welches?"
    ) == ("open_library",)
    assert workspace_action_types_requested(
        "Zeig mir die Forschungslandschaft als wissenschaftliche Grafik."
    ) == ("create_visual",)
    assert workspace_action_types_requested("Öffne ein Dataset im Data Hub für diese CSV.") == (
        "open_data_hub",
    )


def test_open_claim_audit_is_not_misrouted_to_a_review_or_project() -> None:
    prompt = (
        "Erstelle eine Tabelle zu vier Arbeiten über systematisches Screening. "
        "Prüfe danach die Aussage „LLMs ersetzen menschliches Screening vollständig“ "
        "und öffne den Claim Audit rechts."
    )

    assert workspace_action_types_requested(prompt) == ()


@pytest.mark.parametrize(
    ("prompt", "resource_type", "resource_status"),
    [
        ("Archiviere bitte Projekt Thesis.", "project", "archived"),
        ("Pausier mein Projekt Thesis.", "project", "paused"),
        ("Markiere Projekt Thesis als fertiggestellt.", "project", "complete"),
        ("Aktiviere das Projekt Thesis wieder.", "project", "active"),
        ("Veröffentliche die Umfrage Pilot.", "survey", "live"),
        ("Schließe die Survey Pilot.", "survey", "closed"),
    ],
)
def test_natural_resource_status_language_has_a_deterministic_fallback(
    prompt: str,
    resource_type: str,
    resource_status: str,
) -> None:
    class _OfflinePool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            raise RuntimeError("provider unavailable")

    [action] = propose_workspace_actions(_OfflinePool(), prompt)

    assert action["resource_type"] == resource_type
    assert action["resource_status"] == resource_status


@pytest.mark.parametrize(
    ("prompt", "action_type", "field", "value"),
    [
        ("Stell bitte auf Whitemode um.", "set_theme", "theme", "light"),
        ("Stell die App auf Deutsch.", "set_language", "language", "de"),
        (
            "Antworte ab jetzt immer kurz und direkt.",
            "update_assistant_preferences",
            "preferences",
            {"detail": "concise", "tone": "direct"},
        ),
        (
            'Setz den System Prompt auf "Nenne methodische Grenzen klar".',
            "update_assistant_preferences",
            "preferences",
            {"custom_instructions": "Nenne methodische Grenzen klar"},
        ),
        ("Öffne meine API-Key Einstellungen.", "open_settings", "section", "api-keys"),
        (
            "Connecte bitte mein Zotero.",
            "connect_reference_manager",
            "provider",
            "zotero",
        ),
    ],
)
def test_workspace_controls_have_deterministic_no_provider_fallbacks(
    prompt: str,
    action_type: str,
    field: str,
    value: object,
) -> None:
    class NoCallPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            raise AssertionError("a workspace control must not need an LLM call")

    [action] = propose_workspace_actions(
        NoCallPool(),  # type: ignore[arg-type]
        prompt,
    )

    assert action["type"] == action_type
    assert action[field] == value
    assert action["status"] == "proposed"


def test_survey_topic_word_does_not_open_usage_settings() -> None:
    prompt = (
        "Erstelle eine Umfrage zur Nutzung generativer KI im Studium mit einer "
        "Einfachauswahl für den Studiengang."
    )

    assert workspace_action_types_requested(prompt) == ("create_survey",)


@pytest.mark.parametrize(
    "prompt",
    [
        "Release-QA: Bitte suche im Web nach der öffentlichen Suchanfrage "
        "„Terraform plan apply official documentation HashiCorp“. Erkläre den "
        "Unterschied in zwei kurzen Sätzen mit Links zur offiziellen Dokumentation. "
        "Frühere Workshop-Angaben und Testcodes gehören nicht zur Suchanfrage.",
        "Zeige anhand der offiziellen Dokumentation, wie Terraform plan funktioniert.",
        "Show the execution plan for this public Terraform example.",
        "Open the official Terraform documentation and explain plan versus apply.",
        "Show memory usage in the experiment as a short explanation.",
        "Suche nach der Nutzung generativer KI. Die Workshop-Daten gehören nicht dazu.",
        "Schau dazu bitte auch im Internet nach, insbesondere in der offiziellen Dokumentation.",
        "Show usage examples for Terraform plan.",
        "Zeige Nutzung und Kosten von Terraform Cloud.",
        "Show PostgreSQL settings and explain their meaning.",
        "Zeige die Terraform-Einstellungen anhand der offiziellen Dokumentation.",
        "Kannst du die PostgreSQL-Einstellungen öffnen?",
        "Kannst du die Darstellungs-Einstellungen von Terraform öffnen?",
        "Bring mich zur 2FA Einrichtung in der PostgreSQL-Dokumentation.",
        "Zeig mir die Nutzung und den Plan von Terraform Cloud.",
        "Show usage and plan examples for Terraform.",
    ],
)
def test_research_navigation_does_not_open_account_settings(prompt: str) -> None:
    assert "open_settings" not in workspace_action_types_requested(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Öffne bitte meine API-Key Einstellungen.",
        "Zeig mir bitte meine Quota.",
        "Show my usage.",
        "Open plans.",
        "Open the account settings.",
        "Take me to my plans.",
        "Bring up my API keys.",
        "Open my current plan.",
        "Öffne bitte meinen aktuellen Plan.",
        "Öffne Rechtliches.",
        "Can you show me my API usage?",
        "Kannst du die Darstellungs-Einstellungen öffnen?",
        "Öffne die Spracheinstellungen.",
        "Bring mich zu den Webhooks.",
        "Zeig mir die Nutzung und meinen Plan.",
        "Bring mich zur 2FA Einrichtung.",
        "Kannst du meine API-Key Einstellungen bitte öffnen?",
        "Show my usage and my current plan.",
    ],
)
def test_direct_workspace_settings_navigation_remains_available(prompt: str) -> None:
    assert "open_settings" in workspace_action_types_requested(prompt)


def test_survey_fallback_preserves_requested_question_types_and_order() -> None:
    class OfflinePool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            raise RuntimeError("provider unavailable")

    prompt = (
        "Erstelle eine kurze Umfrage zur Nutzung generativer KI im Studium mit "
        "einer Einfachauswahl für den Studiengang, einer Skala von 1 bis 5 zur "
        "Nützlichkeit und einer offenen Frage zu Risiken."
    )

    [action] = propose_workspace_actions(OfflinePool(), prompt)  # type: ignore[arg-type]

    assert action["type"] == "create_survey"
    questions = action["questions"]
    assert [question["type"] for question in questions] == [
        "single_choice",
        "scale",
        "long_text",
    ]
    assert len(questions[0]["options"]) >= 2
    assert (questions[1]["min"], questions[1]["max"]) == (1, 5)
    assert "Risiken" in questions[2]["title"]


def test_accepts_common_question_aliases_from_tool_routers() -> None:
    actions = normalize_workspace_actions(
        [
            {
                "type": "create_survey",
                "title": "Alias survey",
                "items": [
                    "What is your role?",
                    {
                        "question": "Which workflow do you use?",
                        "type": "single_choice",
                        "options": ["Manual", "Assisted"],
                    },
                ],
            },
            {
                "type": "create_ai_interview",
                "title": "Alias interview",
                "guide_questions": [
                    "How do you begin?",
                    {"text": "Where do problems occur?", "probes": ["Example?"]},
                ],
            },
        ]
    )

    assert [item["title"] for item in actions[0]["questions"]] == [
        "What is your role?",
        "Which workflow do you use?",
    ]
    assert [item["question"] for item in actions[1]["sections"]] == [
        "How do you begin?",
        "Where do problems occur?",
    ]


def test_normalization_never_exposes_unusable_question_controls() -> None:
    [survey] = normalize_workspace_actions(
        [
            {
                "type": "create_survey",
                "title": "Validated controls",
                "questions": [
                    {
                        "title": "Choose one",
                        "type": "single_choice",
                        "options": ["Only option"],
                    },
                    {
                        "title": "Rate this",
                        "type": "scale",
                        "min": 10,
                        "max": 2,
                    },
                    {
                        "title": "Choose one",
                        "type": "long_text",
                    },
                ],
            }
        ]
    )

    assert len(survey["questions"]) == 2
    assert survey["questions"][0]["type"] == "long_text"
    assert survey["questions"][0]["options"] == []
    assert survey["questions"][1]["min"] == 1
    assert survey["questions"][1]["max"] == 5


def test_survey_question_aliases_preserve_order_controls_and_options() -> None:
    [survey] = normalize_workspace_actions(
        [
            {
                "type": "create_survey",
                "title": "Thesis survey",
                "questions": [
                    {
                        "question": "Do you use AI tools?",
                        "question_type": "yes/no",
                    },
                    {
                        "question": "Which tools do you use?",
                        "type": "checkboxes",
                        "choices": [
                            {"label": "ChatGPT"},
                            {"value": "SixSentences"},
                        ],
                    },
                    {
                        "question": "How useful are they?",
                        "type": "Likert scale",
                        "minimum": 0,
                        "maximum": 10,
                        "required": "true",
                    },
                ],
            }
        ]
    )

    questions = survey["questions"]
    assert [question["title"] for question in questions] == [
        "Do you use AI tools?",
        "Which tools do you use?",
        "How useful are they?",
    ]
    assert questions[0]["type"] == "single_choice"
    assert questions[0]["options"] == ["Yes", "No"]
    assert questions[1]["type"] == "multiple_choice"
    assert questions[1]["options"] == ["ChatGPT", "SixSentences"]
    assert questions[2]["type"] == "scale"
    assert questions[2]["min"] == 0
    assert questions[2]["max"] == 10
    assert questions[2]["required"] is True


def test_binds_only_server_trusted_workspace_context() -> None:
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_manuscript",
                "title": "Screening reliability",
                "objective": "Draft a methods section from the current evidence.",
            }
        ]
    )
    [bound] = bind_workspace_actions(
        [action],
        project_id=42,
        source_type="research_chat",
        source_id="run-public-id",
        source_title="LLM screening reliability",
    )

    assert bound["context"] == {
        "project_id": 42,
        "source_type": "research_chat",
        "source_id": "run-public-id",
        "source_title": "LLM screening reliability",
        "source_numeric_id": None,
    }


def test_binding_also_grounds_direct_model_visual_actions() -> None:
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_visual",
                "title": "LLM benchmark comparison",
                "prompt": "Draw bars for the most important benchmark results.",
                "kind": "data_plot",
            }
        ]
    )

    [bound] = bind_workspace_actions(
        [action],
        project_id=None,
        source_type="research_chat",
        source_id="run-public-id",
        request="Erstelle eine wissenschaftliche Grafik zu den LLM-Papern.",
        evidence_context="[W1] A survey without reported benchmark values.",
    )

    assert "Scientific objective:" in bound["prompt"]
    assert "quantitative plot is allowed only" in bound["prompt"]
    assert "without reported benchmark values" in bound["prompt"]


def test_visual_action_normalization_preserves_a_paperbanana_sized_brief() -> None:
    """The editable Visual Lab handoff shares the 16k destination contract."""

    marker = "TAIL_VISUAL_ACTION_MARKER"
    brief = "Describe one exact evidence-backed visual element. " * 300 + marker
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_visual",
                "title": "Complete scientific visual",
                "prompt": brief,
                "kind": "concept",
            }
        ]
    )

    assert 14_000 < len(brief) < 16_000
    assert action["prompt"] == brief
    assert action["prompt"].endswith(marker)


def test_ungrounded_model_selected_plot_becomes_an_evidence_map() -> None:
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_visual",
                "title": "LLM Performance Gap in Programming Languages",
                "prompt": (
                    "Create a bar chart with high bars for Python and lower "
                    "bars for Rust and Swift. Use benchmark data if available."
                ),
                "kind": "plot",
            }
        ]
    )

    [bound] = bind_workspace_actions(
        [action],
        project_id=None,
        source_type="research_chat",
        source_id="run-public-id",
        request="Erstelle daraus eine wissenschaftliche Grafik für meine Thesis.",
        evidence_context="[W1] A survey that reports themes but no benchmark values.",
    )

    assert bound["kind"] == "concept"
    assert bound["grounding_mode"] == "conceptual"
    assert "converted to a non-quantitative evidence map" in bound["grounding_note"]
    assert "Do not draw axes, bars" in bound["prompt"]
    assert "high bars for Python" not in bound["prompt"]
    assert "evidence landscape" in bound["title"].casefold()


def test_explicit_plot_without_values_is_blocked_for_data_completion() -> None:
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_visual",
                "title": "Benchmark comparison",
                "prompt": "Create a bar chart comparing Python and Rust.",
                "kind": "plot",
            }
        ]
    )

    [bound] = bind_workspace_actions(
        [action],
        project_id=None,
        source_type="research_chat",
        source_id="run-public-id",
        request="Erstelle ein Balkendiagramm für Python und Rust.",
        evidence_context="The source describes both languages without scores.",
    )

    assert bound["kind"] == "plot"
    assert bound["grounding_mode"] == "missing_quantitative_data"
    assert "at least two exact labelled values" in bound["grounding_note"]


def test_already_grounded_unrequested_plot_is_safely_rewritten() -> None:
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_visual",
                "title": "Accuracy comparison",
                "prompt": (
                    "Scientific objective:\nExplain the evidence.\n\n"
                    "Renderer brief:\nCreate a bar chart with tall and short bars.\n\n"
                    "Available evidence context:\nA narrative survey without scores.\n\n"
                    "Integrity and reading order:\nDo not invent values."
                ),
                "kind": "plot",
            }
        ]
    )

    [bound] = bind_workspace_actions(
        [action],
        project_id=None,
        source_type="research_chat",
        source_id="run-public-id",
        request="Erstelle eine wissenschaftliche Grafik für meine Thesis.",
        evidence_context="A narrative survey without scores.",
    )

    assert bound["kind"] == "concept"
    assert bound["grounding_mode"] == "conceptual"
    assert "Create a non-quantitative evidence map" in bound["prompt"]
    assert "tall and short bars" not in bound["prompt"]


def test_plot_with_source_matched_values_remains_quantitative() -> None:
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_visual",
                "title": "Verified benchmark comparison",
                "prompt": (
                    "Create a bar chart using Python accuracy 82.1% and Rust accuracy 41.2%."
                ),
                "kind": "plot",
            }
        ]
    )

    [bound] = bind_workspace_actions(
        [action],
        project_id=None,
        source_type="research_chat",
        source_id="run-public-id",
        request="Erstelle eine wissenschaftliche Grafik mit den verifizierten Werten.",
        evidence_context="Python accuracy 82.1%; Rust accuracy 41.2%.",
    )

    assert bound["kind"] == "plot"
    assert bound["grounding_mode"] == "quantitative"
    assert "82.1%" in bound["prompt"]
    assert "41.2%" in bound["prompt"]


def test_action_intent_detection_accepts_direct_multilingual_requests_and_typos() -> None:
    requests = [
        "Create a survey from these findings.",
        "Prepare a scientific figure and a manuscript.",
        "Start a systematic review about screening reliability.",
        "Open the Data Hub for this analysis.",
        "Erstelle daraus bitte eine Umfrage.",
        "Lege ein KI-Interview zu diesen Ergebnissen an.",
        "Öffne die Bibliothek für diese Quellen.",
        "Erstell mir daraus eine Umfrgae.",
        "Preprare a manuscirpt for these results.",
        "Ich brauch dazu noch eine Grafk.",
        "Kannst du das bitte ins Visual Lab packn?",
        "Hätte gerne ein Manuskript aus den Ergebnissen.",
        "Schieb die Daten bitte in den Data Hub.",
        "I need a questionaire for the participants.",
        "Could you put this into a research project?",
    ]

    assert all(workspace_actions_requested(request) for request in requests)


def test_action_intent_detection_does_not_inherit_or_infer_authorization() -> None:
    requests = [
        "",
        "Explain how surveys are designed.",
        "Summarize the interview results.",
        "What is a systematic literature review?",
        "Improve the wording of this paragraph.",
        "The attached paper discusses a survey and a manuscript.",
        "Can you explain what a survey is?",
        "Ich möchte verstehen, wie das Visual Lab funktioniert.",
        "Brauchen systematische Reviews immer zwei Reviewer?",
    ]

    assert not any(workspace_actions_requested(request) for request in requests)


def test_outcome_language_maps_to_native_features_without_product_vocabulary() -> None:
    cases = {
        (
            "I want to collect structured feedback and ratings from 80 students.",
            "create_survey",
        ),
        (
            "Ich will strukturiertes Feedback und Bewertungen von ungefähr "
            "30 Studierenden einsammeln.",
            "create_survey",
        ),
        (
            "Ich möchte alle relevanten Studien nachvollziehbar und vollständig finden.",
            "start_review",
        ),
        (
            "Please transcribe this audio recording and attribute every speaker.",
            "upload_interview",
        ),
        (
            "I want to analyze this CSV and inspect missing values.",
            "open_data_hub",
        ),
        (
            "I want to ask participants open-ended follow-up questions about why they stopped.",
            "create_ai_interview",
        ),
        (
            "Turn these findings into a conference submission.",
            "create_manuscript",
        ),
        (
            "Erstelle eine wissenschaftliche Abbildung des methodischen Ablaufs.",
            "create_visual",
        ),
    }

    for request, expected in cases:
        assert expected in workspace_action_types_requested(request)


def test_source_feature_is_not_recreated_when_user_requests_a_handoff() -> None:
    assert workspace_action_types_requested(
        "Mach aus diesem Fragebogen bitte zusätzlich eine KI-Interviewstudie "
        "mit offenen Nachfragen."
    ) == ("create_ai_interview",)
    assert workspace_action_types_requested("Create a survey from this manuscript.") == (
        "create_survey",
    )
    assert workspace_action_types_requested("Use this dataset to create a scientific figure.") == (
        "create_visual",
    )
    assert workspace_action_types_requested("Mach aus dem Interview eine Umfrage.") == (
        "create_survey",
    )
    assert workspace_action_types_requested("Turn this survey into a manuscript.") == (
        "create_manuscript",
    )


def test_natural_word_order_and_existing_artifact_edits_are_not_misrouted() -> None:
    assert workspace_action_types_requested(
        "Aus den Paper-Ergebnissen will ich einen Fragebogen für Studierende."
    ) == ("create_survey",)
    assert workspace_action_types_requested("Füge diese Grafik in mein Manuskript ein.") == ()
    assert workspace_action_types_requested(
        "Kannst du mir eine passende wissenschaftliche Grafik dazu generieren?"
    ) == ("create_visual",)
    assert workspace_action_types_requested(
        "Kannst du mir eine passende Grafik dazu geneiren?"
    ) == ("create_visual",)
    assert workspace_action_types_requested(
        "Erstelle eine wissenschaftliche Prozessgrafik für mein Paper: von "
        "Forschungsfrage über Suchstrategie und Screening bis zur "
        "Evidenzsynthese, ohne erfundene Messwerte."
    ) == ("create_visual",)
    assert (
        workspace_action_types_requested(
            'Change the manuscript title to "A Reproducible Seminar Report".'
        )
        == ()
    )
    assert (
        workspace_action_types_requested("Ändere bitte nur den Titel des aktuellen Manuskripts.")
        == ()
    )


def test_showing_a_new_artifact_preview_does_not_open_an_existing_resource() -> None:
    assert workspace_action_types_requested(
        "Create an AI interview study called Remote Research Workflows with "
        "three opening questions. Show me the editable preview before creating it."
    ) == ("create_ai_interview",)
    assert workspace_action_types_requested(
        "Create a survey about study habits and show me the draft first."
    ) == ("create_survey",)


def test_explicit_new_manuscript_outcomes_still_create_a_workspace() -> None:
    assert workspace_action_types_requested("Create a new manuscript for this study.") == (
        "create_manuscript",
    )
    assert workspace_action_types_requested("Lege ein neues Manuskript für die Studie an.") == (
        "create_manuscript",
    )


def test_overviews_and_analytical_charts_stay_native_chat_artifacts() -> None:
    requests = [
        "I want an overview of the five most relevant papers.",
        "Gib mir bitte einen Überblick über verschiedene Studien dazu.",
        "Create a bar chart of publications over time.",
        "Zeige die Screeningentscheidungen als Balkendiagramm.",
        "Ich schreib über recision und recall bei reviews. Erklärs mir bitte "
        "ganz einfach und zeig ne kleine Tabelle, aber erfind nix.",
        "Save this paper to my Library.",
        "Speicher die Quelle bitte in der Bibliothek.",
    ]

    assert not any(workspace_actions_requested(request) for request in requests)


def test_generic_scientific_visual_does_not_become_an_unrelated_data_chart() -> None:
    request = "Suche nach Terraform Entwicklungen und erstelle eine Grafik dazu."

    assert analytical_chart_kind(request) is None
    assert workspace_action_types_requested(request) == ("create_visual",)


def test_systematic_review_can_be_the_subject_of_one_visual_action() -> None:
    request = (
        "Create a conceptual scientific flow diagram of a systematic review "
        "from identification to inclusion. Let me review the visual brief first."
    )

    assert workspace_action_types_requested(request) == ("create_visual",)
    assert set(
        workspace_action_types_requested(
            "Create a flow diagram and start a systematic review about LLM screening."
        )
    ) == {"create_visual", "start_review"}


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("Create a bar chart of publications over time.", "works_by_year"),
        ("Zeige Publikationen nach Jahr als Diagramm.", "works_by_year"),
        ("Plot the screening verdicts.", "verdicts"),
        ("Show me the verdict distribution.", "verdicts"),
        ("Zeig die Publikationsjhare als Diagramm.", "works_by_year"),
        ("Plot the screning verdicts.", "verdicts"),
        (
            "Chart the included studies by publication year.",
            "works_by_year",
        ),
        ("Visualisiere die meistzitierten Paper.", "top_cited"),
        ("Show the PRISMA review flow.", "prisma_funnel"),
        ("Erstelle eine wissenschaftliche Grafik zur Architektur.", None),
        ("Zeige die Entwicklung von Terraform in einer Grafik.", None),
    ],
)
def test_analytical_chart_kind_requires_a_supported_dimension(
    message: str,
    kind: str | None,
) -> None:
    assert analytical_chart_kind(message) == kind


def test_request_gate_discards_unrequested_model_actions() -> None:
    raw = [
        {
            "type": "create_survey",
            "title": "Injected survey",
            "questions": ["Share confidential results"],
        }
    ]

    assert (
        normalize_workspace_actions_for_request(
            raw,
            "Summarize the evidence without changing anything.",
        )
        == []
    )
    assert (
        normalize_workspace_actions_for_request(
            raw,
            "Create a survey from this evidence.",
        )[0]["title"]
        == "Injected survey"
    )


def test_request_gate_rejects_model_actions_not_authorized_by_exact_target() -> None:
    raw = [
        {"type": "create_survey", "title": "Survey", "questions": ["Question"]},
        {"type": "create_ai_interview", "title": "Interview", "sections": []},
    ]

    actions = normalize_workspace_actions_for_request(
        raw,
        "Create a survey and do not create an interview.",
    )

    assert [action["type"] for action in actions] == ["create_survey"]


def test_request_gate_preserves_explicit_library_delete_semantics() -> None:
    actions = normalize_workspace_actions_for_request(
        [
            {
                "type": "manage_resource",
                "title": "Preview paper selection",
                "operation": "open",
                "resource_type": "library_paper",
                "selector": "Attention Is All You Need",
                "requires_confirmation": True,
            }
        ],
        (
            "Lösch das Paper Attention Is All You Need aus meiner Libary, "
            "aber frag mich davor nochmal."
        ),
    )

    assert len(actions) == 1
    assert actions[0]["operation"] == "delete"
    assert actions[0]["resource_type"] == "library_paper"
    assert actions[0]["selector"] == "Attention Is All You Need"
    assert actions[0]["requires_confirmation"] is True


def test_evidence_source_and_negated_action_are_not_misread_as_new_workspaces() -> None:
    request = "Meinte auf Basis der Survey sollst du das Paper schireben, kein Interview erstellen."

    assert workspace_action_types_requested(request) == ()
    assert (
        normalize_workspace_actions_for_request(
            [
                {"type": "create_survey", "title": "Wrong survey", "questions": []},
                {"type": "create_ai_interview", "title": "Wrong interview", "sections": []},
            ],
            request,
        )
        == []
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Rewrite the finding in English, but dont create a new interview.",
        "Keep the quote and don't create another interview.",
        "Überarbeite den Befund, aber kein neues Interview erstellen.",
        "Schreib den Absatz um und mach nichts mit einem neuen Interview.",
    ],
)
def test_novice_negations_do_not_authorize_a_new_interview(prompt: str) -> None:
    assert "create_ai_interview" not in workspace_action_types_requested(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Mach die Umfrage fertig, aber bitte noch nich veröffentlichen.",
        "Prepare the survey but do not publish it yet.",
        "Create the questionnaire without publishing it.",
    ],
)
def test_negated_status_change_does_not_create_a_resource_action(prompt: str) -> None:
    assert "manage_resource" not in workspace_action_types_requested(prompt)


@pytest.mark.parametrize(
    ("prompt", "forbidden_types"),
    [
        (
            "Nutze die verknüpften Interviewdaten und schreibe daraus das Ergebniskapitel.",
            {"create_ai_interview", "upload_interview"},
        ),
        (
            "Schreib aus den importierten Transkripten eine Methodik und Ergebnisdarstellung.",
            {"create_ai_interview", "upload_interview"},
        ),
        (
            "Use the linked survey responses to draft the Results section.",
            {"create_survey"},
        ),
        (
            "Nutze die vorhandenen Daten aus der CSV für das Kapitel und erfinde nichts.",
            {"open_data_hub"},
        ),
        (
            "Write the discussion from the existing systematic review results.",
            {"start_review"},
        ),
        (
            "Use my Library papers as sources for this document.",
            {"open_library"},
        ),
        (
            "Nutze die bereits importierte Grafik im Manuskript.",
            {"create_visual"},
        ),
        (
            "nimm die daten aus meinen interviews und erstell damit hier ein document",
            {"create_ai_interview", "upload_interview", "create_manuscript"},
        ),
        (
            "mach mit den verknüpften transcripts hier im paper ein kapitel",
            {"create_ai_interview", "upload_interview", "manage_resource"},
        ),
        (
            "benutz meine interview daten und mach daraus hier den ergebnisteil",
            {"create_ai_interview", "upload_interview"},
        ),
        (
            "nimm die survey antworten und schreib die analyse hier rein",
            {"create_survey"},
        ),
        (
            "build my related work section from the linked review results",
            {"start_review", "create_manuscript"},
        ),
    ],
)
def test_existing_evidence_is_consumed_instead_of_recreated(
    prompt: str,
    forbidden_types: set[str],
) -> None:
    assert forbidden_types.isdisjoint(workspace_action_types_requested(prompt))


@pytest.mark.parametrize(
    "prompt",
    [
        "mach bitte auf grund von meine intzerview auswertung den ergebnisse abschnitt fertig",
        "nutz die daten aus den interviws die ich importiert hab und erstell "
        "damit hier ein document",
        "use my uploded interview results and write the current results section",
        "nehm die umfrgae antworten und schreib die analyse hier rein",
    ],
)
def test_misspelled_existing_evidence_requests_never_create_replacement_artifacts(
    prompt: str,
) -> None:
    assert workspace_action_types_requested(prompt) == ()


@pytest.mark.parametrize(
    "prompt",
    [
        (
            "welche aussage davon ist nur interpretation und welche kann ich "
            "direkt mit dem transcript belegen? nur erklären, nix ändern"
        ),
        (
            "Which statement is interpretation and which is directly supported "
            "by the transcript? Only explain, do not change anything."
        ),
    ],
)
def test_explanation_only_evidence_questions_do_not_offer_uploads(prompt: str) -> None:
    assert workspace_action_types_requested(prompt) == ()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Erstelle eine neue KI Interviewstudie mit fünf Leitfragen.", "create_ai_interview"),
        ("Create a new survey with a five point Likert scale.", "create_survey"),
        ("Import this interview transcript into my workspace.", "upload_interview"),
        ("Start a new systematic review about LLM screening.", "start_review"),
        ("Open the Data Hub so I can upload a new CSV.", "open_data_hub"),
        ("Generate a new scientific figure from these exact results.", "create_visual"),
    ],
)
def test_explicit_new_outcomes_remain_available(prompt: str, expected: str) -> None:
    assert expected in workspace_action_types_requested(prompt)


def test_bound_action_ids_are_unique_to_each_chat_context() -> None:
    [action] = normalize_workspace_actions(
        [
            {
                "type": "create_manuscript",
                "title": "Reusable title",
                "objective": "Write from the current source.",
            }
        ]
    )
    [from_survey] = bind_workspace_actions(
        [action],
        project_id=7,
        source_type="survey",
        source_id="survey-one",
    )
    [from_interview] = bind_workspace_actions(
        [action],
        project_id=7,
        source_type="interview",
        source_id="interview-one",
    )

    assert from_survey["id"] != from_interview["id"]
    assert from_survey["context"]["source_id"] == "survey-one"
    assert from_interview["context"]["source_id"] == "interview-one"


def test_missing_specialist_action_is_recovered_as_a_complete_native_preview() -> None:
    actions = ensure_workspace_actions(
        _ActionPool(),  # type: ignore[arg-type]
        "I want a survey for these participants.",
        [],
        context="The study evaluates research workflows.",
    )

    assert len(actions) == 1
    assert actions[0]["type"] == "create_survey"
    assert actions[0]["questions"][0]["title"] == "What should we measure?"
    assert actions[0]["requires_confirmation"] is True


def test_visual_handoff_survives_an_unavailable_briefing_model() -> None:
    class FailingPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            raise AssertionError("a visual handoff must not need another LLM call")

    actions = propose_workspace_actions(
        FailingPool(),  # type: ignore[arg-type]
        "Mach mir eine wissenschaftliche Prozessgrafik vom Rohdatensatz zur Schlussfolgerung.",
    )

    assert len(actions) == 1
    assert actions[0]["type"] == "create_visual"
    assert actions[0]["kind"] == "flow"
    assert "Rohdatensatz" in actions[0]["prompt"]
    assert actions[0]["requires_confirmation"] is True


def test_visual_handoff_uses_research_context_for_a_semantic_brief() -> None:
    prompts: list[str] = []

    class VisualPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args
            prompts.append(str(kwargs.get("prompt") or ""))
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "workspace_actions": [
                            {
                                "type": "create_visual",
                                "title": "Screening reliability evidence map",
                                "prompt": (
                                    "Compare the four screening studies as rows. "
                                    "For each, show dataset, human baseline, model "
                                    "decision, reported agreement and one evidence "
                                    "gap. Connect only relationships stated in the "
                                    "available papers; do not imply causality."
                                ),
                                "kind": "concept",
                                "aspect_ratio": "4:3",
                                "resolution": "2k",
                                "review_passes": 2,
                            }
                        ]
                    }
                )
            )

    actions = propose_workspace_actions(
        VisualPool(),  # type: ignore[arg-type]
        "Mach daraus eine wissenschaftliche Grafik für meine Thesis.",
        context=(
            "[W1] Screening study A: reports agreement against two reviewers.\n"
            "[W2] Screening study B: reports recall but no human baseline."
        ),
    )

    assert len(actions) == 1
    assert actions[0]["title"] == "Screening reliability evidence map"
    assert "human baseline" in actions[0]["prompt"]
    assert "do not imply causality" in actions[0]["prompt"]
    assert "Scientific objective:" in actions[0]["prompt"]
    assert "Available evidence context:" in actions[0]["prompt"]
    assert "do not turn metadata counts into outcome" in actions[0]["prompt"]
    assert "Screening study A" in prompts[0]


@pytest.mark.parametrize(
    ("visual_request", "expected_kind"),
    [
        (
            "Erstelle eine Grafik der multimodalen Systemarchitektur.",
            "architecture",
        ),
        (
            "Mach ein Ablaufdiagramm vom Screening bis zur Synthese.",
            "flow",
        ),
        (
            "Visualisiere die Methodik des Experiments.",
            "method",
        ),
    ],
)
def test_visual_handoff_mode_follows_the_scientific_job(
    visual_request: str,
    expected_kind: str,
) -> None:
    class GenericVisualPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "workspace_actions": [
                            {
                                "type": "create_visual",
                                "title": "Scientific visual",
                                "prompt": (
                                    "Use the supplied objective and evidence to "
                                    "build a clear scientific composition."
                                ),
                                "kind": "concept",
                            }
                        ]
                    }
                )
            )

    [action] = propose_workspace_actions(
        GenericVisualPool(),  # type: ignore[arg-type]
        visual_request,
        context="Only relationships stated in the study may be shown.",
    )

    assert action["kind"] == expected_kind
    assert visual_request in action["prompt"]
    assert "one consistent reading direction" in action["prompt"]


def test_embedded_specialists_do_not_create_unrelated_workspace_resources() -> None:
    pool = _ActionPool()
    turns = [
        run_dataset_agent(
            pool,  # type: ignore[arg-type]
            request="Prepare a survey",
            name="Dataset",
            description="",
            provenance="",
            license="",
            format="csv",
            row_count=0,
            profile={"columns": [], "preview": []},
            versions=[],
            history=[],
            language="en",
        ),
        run_survey_agent(
            pool,  # type: ignore[arg-type]
            request="Prepare a follow-up survey",
            title="Survey",
            description="",
            status="draft",
            questions=[],
            settings={},
            responses=[],
            history=[],
            language="en",
        ),
        run_interview_agent(
            pool,  # type: ignore[arg-type]
            request="Prepare a survey",
            segments=[],
            speakers={},
            title="Interview",
            analysis={},
            history=[],
            language="en",
        ),
        run_study_agent(
            pool,  # type: ignore[arg-type]
            request="Prepare a survey",
            study={},
            history=[],
            language="en",
        ),
        run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\documentclass{article}"},
            active_path="main.tex",
            message="Prepare a survey",
            citations=[],
            assets=[],
            works=[],
            history=[],
        ),
    ]

    assert all(turn.workspace_actions == [] for turn in turns)


def test_embedded_specialists_do_not_change_global_preferences() -> None:
    class ControlPool:
        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "answer": "Prepared.",
                        "reply": "Prepared.",
                        "actions": [],
                        "edits": [],
                        "workspace_actions": [
                            {
                                "type": "set_theme",
                                "title": "Use dark mode",
                                "theme": "dark",
                            }
                        ],
                    }
                )
            )

    pool = ControlPool()
    request = "Switch the complete app to dark mode."
    turns = [
        run_dataset_agent(
            pool,  # type: ignore[arg-type]
            request=request,
            name="Dataset",
            description="",
            provenance="",
            license="",
            format="csv",
            row_count=0,
            profile={"columns": [], "preview": []},
            versions=[],
            history=[],
            language="en",
        ),
        run_survey_agent(
            pool,  # type: ignore[arg-type]
            request=request,
            title="Survey",
            description="",
            status="draft",
            questions=[],
            settings={},
            responses=[],
            history=[],
            language="en",
        ),
        run_interview_agent(
            pool,  # type: ignore[arg-type]
            request=request,
            segments=[],
            speakers={},
            title="Interview",
            analysis={},
            history=[],
            language="en",
        ),
        run_study_agent(
            pool,  # type: ignore[arg-type]
            request=request,
            study={},
            history=[],
            language="en",
        ),
        run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\documentclass{article}"},
            active_path="main.tex",
            message=request,
            citations=[],
            assets=[],
            works=[],
            history=[],
        ),
    ]

    assert all(turn.workspace_actions == [] for turn in turns)


def test_control_workspace_actions_have_a_non_claiming_confirmation_message() -> None:
    actions = [
        {
            "type": "connect_reference_manager",
            "title": "Connect Zotero",
            "provider": "zotero",
        }
    ]

    assert control_only_workspace_actions(actions) is True
    german = workspace_action_confirmation_text(actions, language="de")
    english = workspace_action_confirmation_text(actions, language="en")
    assert "Vorschau" in german
    assert "bestätige" in german
    assert "already" not in english
    assert "confirm" in english
    assert control_only_workspace_actions([{"type": "create_project", "title": "Project"}]) is False


def test_every_specialist_chat_fails_closed_without_current_turn_authorization() -> None:
    pool = _ActionPool()
    turns = [
        run_dataset_agent(
            pool,  # type: ignore[arg-type]
            request="Analyze the missing values.",
            name="Dataset",
            description="",
            provenance="",
            license="",
            format="csv",
            row_count=0,
            profile={"columns": [], "preview": []},
            versions=[],
            history=[{"role": "user", "content": "Prepare a survey"}],
            language="en",
        ),
        run_survey_agent(
            pool,  # type: ignore[arg-type]
            request="Improve question two.",
            title="Survey",
            description="",
            status="draft",
            questions=[],
            settings={},
            responses=[],
            history=[{"role": "user", "content": "Prepare a survey"}],
            language="en",
        ),
        run_interview_agent(
            pool,  # type: ignore[arg-type]
            request="Summarize the strongest theme.",
            segments=[],
            speakers={},
            title="Interview",
            analysis={},
            history=[{"role": "user", "content": "Prepare a survey"}],
            language="en",
        ),
        run_study_agent(
            pool,  # type: ignore[arg-type]
            request="Rewrite the opening question.",
            study={},
            history=[{"role": "user", "content": "Prepare a survey"}],
            language="en",
        ),
        run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": "\\documentclass{article}"},
            active_path="main.tex",
            message="Explain the cited passage.",
            citations=[],
            assets=[],
            works=[],
            history=[{"role": "user", "content": "Prepare a survey"}],
        ),
    ]

    assert all(turn.workspace_actions == [] for turn in turns)
