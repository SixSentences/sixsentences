"""Cross-feature fail-closed contracts for model-authored research output."""

from __future__ import annotations

import json
from types import SimpleNamespace

from sixsentences_server.agent.events import agent_event_sink
from sixsentences_server.datasets.service import run_dataset_agent
from sixsentences_server.interviews.analysis import run_interview_agent
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.surveys.service import run_survey_agent
from sixsentences_server.voice.agent import run_study_agent
from sixsentences_server.writer.assistant import run_assistant_turn


class _FixedPool:
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(text=self.text, provider="fake", model="grounding-test")


class _SequencePool:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return SimpleNamespace(text=response, provider="fake", model="grounding-test")


class _CapturingSequencePool(_SequencePool):
    def __init__(self, *responses: str) -> None:
        super().__init__(*responses)
        self.requests: list[dict[str, object]] = []

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.requests.append(dict(kwargs))
        return super().complete(*args, **kwargs)


class _CapturingPool(_FixedPool):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.prompts: list[str] = []

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(str(kwargs.get("prompt") or ""))
        return SimpleNamespace(text=self.text, provider="fake", model="grounding-test")


class _PinnedRecoveryPool:
    def __init__(self, recovered: str) -> None:
        self.recovered = recovered
        self.primary_calls = 0
        self.pinned_models: list[str] = []

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        self.primary_calls += 1
        return SimpleNamespace(text="invalid", provider="fake", model="selected-model")

    def pinned(self, model: object) -> _FixedPool:
        self.pinned_models.append(str(model))
        return _FixedPool(self.recovered)


class _FailedPrimaryWithPinnedRecoveryPool(_PinnedRecoveryPool):
    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        self.primary_calls += 1
        raise ProviderError("selected provider unavailable")


def test_empty_dataset_explains_the_missing_prerequisite_without_model_arithmetic() -> None:
    pool = _SequencePool("this response must never be used")

    turn = run_dataset_agent(
        pool,  # type: ignore[arg-type]
        request=(
            "mach da mal ne schöne grafik mit den ergebnissen und sag was "
            "rauskommt, nimm zur not beispielwerte"
        ),
        name="Empty pilot",
        description="",
        provenance="",
        license="",
        format="",
        row_count=0,
        profile={"columns": [], "preview": []},
        versions=[],
        history=[],
        language="de",
    )

    assert pool.calls == 0
    assert turn.actions == []
    assert turn.workspace_actions == []
    assert "noch keine Zeilen oder Spalten" in turn.answer
    assert "Beispielwerte" in turn.answer


def test_dataset_missingness_request_uses_server_owned_analysis_action() -> None:
    payload = {
        "answer": "I will calculate missingness from the complete imported dataset.",
        "actions": [
            {
                "operation": "run_analysis",
                "kind": "missingness",
                "definition": {},
                "name": "Missingness check",
            }
        ],
        "workspace_actions": [{"type": "open_data_hub", "title": "Wrong duplicate workspace"}],
    }
    turn = run_dataset_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="check ma bitte wie viele werte fehlen in den importierten daten",
        name="Pilot",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=4,
        profile={
            "columns": [{"name": "age", "type": "number"}],
            "preview": [{"age": 21}, {"age": None}],
        },
        versions=[{"version": 1}],
        history=[],
        language="de",
    )

    assert turn.actions == [payload["actions"][0]]
    assert turn.workspace_actions == []


def test_dataset_missingness_request_is_repaired_when_model_omits_action() -> None:
    payload = {
        "answer": "Im Feld age fehlt ein Wert.",
        "actions": [],
        "workspace_actions": [],
    }
    turn = run_dataset_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="prüf mla die fehlenden werte und sag nur was wirklich drin steht",
        name="Pilot",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=4,
        profile={
            "columns": [{"name": "age", "type": "number"}],
            "preview": [{"age": 21}, {"age": None}],
        },
        versions=[{"version": 1}],
        history=[],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "run_analysis",
            "kind": "missingness",
            "definition": {},
            "name": "Missingness",
        }
    ]


def test_dataset_open_exploration_runs_grounded_descriptive_analysis() -> None:
    payload = {
        "answer": "Ich prüfe die vorhandenen Daten.",
        "actions": [],
        "workspace_actions": [],
    }
    turn = run_dataset_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="was fällt dir an den daten auf? nur echte werte bitte",
        name="Pilot",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=4,
        profile={
            "columns": [
                {"name": "participant", "type": "text"},
                {"name": "score", "type": "number"},
            ],
            "preview": [
                {"participant": "P1", "score": 4},
                {"participant": "P2", "score": 8},
            ],
        },
        versions=[{"version": 1}],
        history=[],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "run_analysis",
            "kind": "descriptive",
            "definition": {"column": "score"},
            "name": "score overview",
        }
    ]


def test_dataset_latest_median_and_no_chart_correction_wins() -> None:
    payload = {
        "answer": "Ich verwende den Median.",
        "actions": [
            {
                "operation": "create_chart",
                "x_column": "group",
                "y_column": "score",
                "kind": "bar",
                "title": "Score by group",
            }
        ],
        "workspace_actions": [],
    }
    turn = run_dataset_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="ne nimm median statt mittelwert und keine grafik bevor die analyse stimmt",
        name="Pilot",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=4,
        profile={
            "columns": [
                {"name": "participant", "type": "text"},
                {"name": "age", "type": "number"},
                {"name": "score", "type": "number"},
                {"name": "group", "type": "text"},
            ],
            "preview": [],
        },
        versions=[{"version": 1}],
        history=[{"role": "user", "content": "mach ein diagramm vom score nach gruppe"}],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "run_analysis",
            "kind": "group_summary",
            "definition": {
                "group_by": "group",
                "value_column": "score",
                "metric": "median",
            },
            "name": "Median score by group",
        }
    ]


def test_dataset_existing_rows_group_comparison_is_repaired_when_model_omits_action() -> None:
    payload = {
        "answer": "Ich vergleiche nur die bereits importierten Zeilen.",
        "actions": [],
        "workspace_actions": [],
    }
    turn = run_dataset_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request=(
            "nutz die importierten zeilen um die gruppen zu vergleichen, "
            "keinen neuen datensatz anlegen"
        ),
        name="Pilot",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=4,
        profile={
            "columns": [
                {"name": "participant", "type": "text"},
                {"name": "score", "type": "number"},
                {"name": "group", "type": "text"},
            ],
            "preview": [],
        },
        versions=[{"version": 1}],
        history=[],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "run_analysis",
            "kind": "group_summary",
            "definition": {
                "group_by": "group",
                "value_column": "score",
                "metric": "mean",
            },
            "name": "Mean score by group",
        }
    ]
    assert turn.workspace_actions == []


def test_survey_delete_all_request_emits_explicit_safe_refusal_action() -> None:
    payload = {
        "answer": "Das kann ich nicht sicher anwenden.",
        "actions": [],
        "workspace_actions": [],
    }
    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="lösch alle bisherigen fragen, es gibt schon antworten",
        title="Pilot",
        description="",
        status="live",
        questions=[
            {"id": "q1", "title": "Einwilligung", "type": "single_choice"},
            {"id": "q2", "title": "Kommentar", "type": "long_text"},
        ],
        settings={},
        responses=[{"answers": {"q1": "Ja", "q2": "Hilfreich"}}],
        history=[],
        language="de",
    )

    assert turn.actions == [{"operation": "replace_questions", "questions": []}]


def test_open_survey_consumes_a_misrouted_creation_proposal() -> None:
    questions = [
        {
            "title": f"KI Frage {index}",
            "type": "long_text",
            "required": index < 2,
            "options": [],
        }
        for index in range(1, 7)
    ]
    payload = {
        "answer": "Ich habe die sechs Fragen vorbereitet.",
        "actions": [],
        "workspace_actions": [
            {
                "type": "create_survey",
                "title": "KI in der Industrie",
                "description": "Einsatz und Wirkung von KI in Industrieunternehmen.",
                "questions": questions,
            }
        ],
    }

    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="Erstelle mir mal pls ne Survey zum Thema KI in der Industy egal was 6 fragen",
        title="Untitled",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert turn.workspace_actions == []
    assert [action["operation"] for action in turn.actions] == [
        "set_title",
        "set_description",
        "replace_questions",
    ]
    assert len(turn.actions[-1]["questions"]) == 6


def test_open_interview_study_consumes_a_misrouted_creation_proposal() -> None:
    sections = [
        {
            "title": f"Topic {index}",
            "question": f"How does AI affect industrial process {index}?",
            "probes": ["Can you give a concrete example?"],
            "must_cover": True,
        }
        for index in range(1, 7)
    ]
    payload = {
        "answer": "I prepared the guide.",
        "actions": [],
        "workspace_actions": [
            {
                "type": "create_ai_interview",
                "title": "AI in Industry",
                "language": "en",
                "sections": sections,
            }
        ],
    }

    turn = run_study_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="Build an AI interview about AI in industry with six questions",
        study={"title": "Untitled", "guide": {"sections": []}},
        history=[],
        language="en",
    )

    assert turn.workspace_actions == []
    assert [action["operation"] for action in turn.actions] == [
        "rename",
        "set_guide",
        "set_persona",
    ]
    assert len(turn.actions[1]["sections"]) == 6


def test_open_survey_rewrites_current_questions_instead_of_creating_a_new_survey() -> None:
    payload = {
        "answer": "Ich habe die bestehenden Fragen lockerer formuliert.",
        "actions": [
            {
                "operation": "update_question",
                "question_id": "q1",
                "changes": {"title": "Wie oft nutzt du KI im Studium?"},
            },
            {
                "operation": "update_question",
                "question_id": "q2",
                "changes": {"title": "Was bringt dir KI beim Lernen?"},
            },
        ],
        "workspace_actions": [
            {
                "type": "create_survey",
                "title": "Wrong duplicate survey",
                "questions": [],
            }
        ],
    }

    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="Pass mal die Sprache auf bisschen umgangssprachlicher an pls",
        title="KI im Studium",
        description="",
        status="draft",
        questions=[
            {
                "id": "q1",
                "title": "Wie häufig verwenden Sie KI im Studium?",
                "type": "single_choice",
                "options": ["Täglich", "Wöchentlich", "Nie"],
            },
            {
                "id": "q2",
                "title": "Welche Vorteile ergeben sich für Sie?",
                "type": "long_text",
                "options": [],
            },
        ],
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert [action["question_id"] for action in turn.actions] == ["q1", "q2"]
    assert turn.actions[0]["changes"] == {"title": "Wie oft nutzt du KI im Studium?"}
    assert turn.workspace_actions == []


def test_open_interview_updates_six_topics_in_the_current_guide() -> None:
    sections = [
        {
            "title": f"Thema {index}",
            "question": f"Wie erlebst du KI im Studienalltag {index}?",
            "probes": ["Kannst du ein Beispiel nennen?"],
            "must_cover": True,
        }
        for index in range(1, 7)
    ]
    payload = {
        "answer": "Ich habe den vorhandenen Leitfaden auf sechs Themen erweitert.",
        "actions": [{"operation": "set_guide", "sections": sections}],
        "workspace_actions": [
            {
                "type": "create_ai_interview",
                "title": "Wrong duplicate study",
                "sections": sections,
            }
        ],
    }

    turn = run_study_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request=(
            "Erstelle mal weiter Sachen iom Interview uns pass das bestehende auch an "
            "soll um KI im Studium gehen 6 Themen"
        ),
        study={
            "title": "KI-Nutzung im Studium",
            "guide": {
                "sections": [
                    {
                        "title": "Einstieg",
                        "question": "Wie nutzt du KI aktuell?",
                        "probes": [],
                    }
                ]
            },
        },
        history=[],
        language="de",
    )

    assert [action["operation"] for action in turn.actions] == ["set_guide"]
    assert len(turn.actions[0]["sections"]) == 6
    assert turn.workspace_actions == []


def test_survey_full_replacement_converts_additions_into_atomic_replacement() -> None:
    payload = {
        "answer": "I prepared the three requested questions.",
        "actions": [
            {
                "operation": "add_question",
                "position": 0,
                "question": {
                    "title": "Alter",
                    "type": "short_text",
                    "required": False,
                },
            },
            {
                "operation": "add_question",
                "position": 1,
                "question": {
                    "title": "Rolle",
                    "type": "single_choice",
                    "required": True,
                    "options": ["Student", "Doktorand", "Forschende Person"],
                },
            },
            {
                "operation": "add_question",
                "position": 2,
                "question": {
                    "title": "Was hat beim Onboarding gefehlt?",
                    "type": "long_text",
                    "required": False,
                },
            },
        ],
        "workspace_actions": [],
    }
    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="Mach genau diese drei Fragen und die alte Frage komplett ersetzen.",
        title="Pilot",
        description="",
        status="draft",
        questions=[{"id": "q1", "title": "Old question", "type": "long_text"}],
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "replace_questions",
            "questions": [action["question"] for action in payload["actions"]],
        }
    ]


def test_survey_repairs_an_incomplete_plan_for_an_explicit_final_count() -> None:
    initial = {
        "answer": "I updated the current question.",
        "actions": [
            {
                "operation": "update_question",
                "question_id": "q1",
                "changes": {"title": "Nutzen Sie KI?"},
            }
        ],
        "workspace_actions": [],
    }
    checkpoint = {
        "status": "complete",
        "summary": "The first edit is valid.",
        "missing_actions": [],
    }
    repaired = {
        "answer": "Ich habe die offene Umfrage auf genau drei Fragen gebracht.",
        "actions": [
            {
                "operation": "replace_questions",
                "questions": [
                    {
                        "title": "Nutzen Sie KI?",
                        "type": "single_choice",
                        "options": "Ja, Nein",
                    },
                    {
                        "title": "Wo setzen Sie KI ein?",
                        "type": "multiple_choice",
                        "options": "Produktion; Verwaltung; Forschung",
                    },
                    {
                        "title": "Wie reif ist die Nutzung?",
                        "type": "scale",
                        "min": 1,
                        "max": 5,
                    },
                ],
            }
        ],
        "workspace_actions": [],
    }

    pool = _SequencePool(json.dumps(initial), json.dumps(checkpoint), json.dumps(repaired))
    turn = run_survey_agent(
        pool,  # type: ignore[arg-type]
        request=(
            "mach diese umfrage bitte auf genau 3 fragen zu KI in der industrie. "
            "frage 1 single choice mit Ja und Nein, frage 2 multiple choice und "
            "frage 3 skala 1 bis 5. nix neues anlegen"
        ),
        title="Pilot",
        description="",
        status="draft",
        questions=[{"id": "q1", "title": "Old question", "type": "long_text"}],
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert pool.calls == 3
    assert len(turn.actions) == 1
    assert turn.actions[0]["operation"] == "replace_questions"
    assert len(turn.actions[0]["questions"]) == 3
    assert turn.workspace_actions == []


def test_survey_novice_rename_changes_only_one_exact_option() -> None:
    questions = [
        {
            "id": "q1",
            "title": "Alter",
            "type": "short_text",
            "required": False,
            "options": [],
        },
        {
            "id": "q2",
            "title": "Rolle",
            "type": "single_choice",
            "required": True,
            "options": ["Student", "Doktorand", "Forschende Person"],
        },
    ]
    misleading_payload = {
        "answer": "I updated it.",
        "actions": [
            {
                "operation": "update_question",
                "question_id": "q1",
                "changes": {"title": "Promovierende Person"},
            }
        ],
        "workspace_actions": [],
    }
    turn = run_survey_agent(
        _FixedPool(json.dumps(misleading_payload)),  # type: ignore[arg-type]
        request="ne korrektur: nenn Doktorand bitte Promovierende Person, sonst nix ändern",
        title="Pilot",
        description="",
        status="draft",
        questions=questions,
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "update_question",
            "question_id": "q2",
            "changes": {"options": ["Student", "Promovierende Person", "Forschende Person"]},
        }
    ]


def test_survey_keeps_identity_disabled_for_contradictory_anonymity_request() -> None:
    payload = {
        "answer": "I enabled identity collection as requested.",
        "actions": [{"operation": "set_collect_identity", "value": True}],
        "workspace_actions": [],
    }

    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="mach die umfrage anonym aber sammle trotzdem namen und mail von allen",
        title="Pilot",
        description="",
        status="draft",
        questions=[],
        settings={"collect_identity": False},
        responses=[],
        history=[],
        language="de",
    )

    assert turn.actions == [{"operation": "set_collect_identity", "value": False}]
    assert "bleibt deshalb ausgeschaltet" in turn.answer


def test_survey_compiles_explicit_rating_control_when_model_omits_it() -> None:
    payload = {
        "answer": "I updated the form.",
        "actions": [],
        "workspace_actions": [],
    }
    questions = [{"id": "q1", "title": "Einwilligung", "type": "single_choice"}]

    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="füge ganz am ende eine rating frage von 1 bis 5 zur zufriedenheit hinzu",
        title="Pilot",
        description="",
        status="draft",
        questions=questions,
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "add_question",
            "position": 1,
            "question": {
                "title": "Wie zufrieden sind Sie?",
                "description": "",
                "type": "rating",
                "required": False,
                "options": [],
                "min": None,
                "max": None,
            },
        }
    ]


def test_survey_compiles_latest_scale_replacement_and_position() -> None:
    payload = {
        "answer": "I changed the scale.",
        "actions": [],
        "workspace_actions": [],
    }
    questions = [
        {"id": "q1", "title": "Einwilligung", "type": "single_choice"},
        {"id": "q2", "title": "Welche Tools?", "type": "multiple_choice"},
        {"id": "q3", "title": "Wie zufrieden sind Sie?", "type": "rating"},
    ]

    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request=(
            "ne doch scale 0 bis 10 und direkt nach der Einwilligung, "
            "die alte rating frage ersetzen"
        ),
        title="Pilot",
        description="",
        status="draft",
        questions=questions,
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    replacement = turn.actions[0]["questions"]
    assert turn.actions[0]["operation"] == "replace_questions"
    assert [question["type"] for question in replacement] == [
        "single_choice",
        "scale",
        "multiple_choice",
    ]
    assert replacement[1]["min"] == 0
    assert replacement[1]["max"] == 10


def test_survey_removes_unrequested_choice_options_from_model_action() -> None:
    payload = {
        "answer": "I changed question two.",
        "actions": [
            {
                "operation": "update_question",
                "question_id": "q2",
                "changes": {
                    "type": "multiple_choice",
                    "options": [
                        "ChatGPT",
                        "Gemini",
                        "Claude",
                        "Sonstiges",
                        "Keines der genannten",
                    ],
                },
            }
        ],
        "workspace_actions": [],
    }
    questions = [
        {"id": "q1", "title": "Einwilligung", "type": "single_choice"},
        {"id": "q2", "title": "Welche KI Tools nutzen Sie?", "type": "short_text"},
    ]

    turn = run_survey_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="mach frage 2 zu ner mehrauswhal mit ChatGPT Gemini Claude und Sonstiges",
        title="Pilot",
        description="",
        status="draft",
        questions=questions,
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert turn.actions == [
        {
            "operation": "update_question",
            "question_id": "q2",
            "changes": {
                "type": "multiple_choice",
                "options": ["ChatGPT", "Gemini", "Claude", "Sonstiges"],
            },
        }
    ]


def test_interview_agent_recovers_exact_segment_for_named_topic() -> None:
    payload = {
        "answer": "P1 says mentoring reduced uncertainty.",
        "quotes": [
            {
                "segment": 2,
                "text": "Mentoring reduced uncertainty.",
            }
        ],
        "workspace_actions": [],
    }
    segments = [
        {
            "idx": 1,
            "speaker": "S1",
            "start_ms": 0,
            "end_ms": 30_000,
            "text": "Wie hast du das Onboarding erlebt?",
        },
        {
            "idx": 2,
            "speaker": "S2",
            "start_ms": 30_000,
            "end_ms": 60_000,
            "text": (
                "Das wöchentliche Mentoring hat meine Unsicherheit in den ersten "
                "Wochen deutlich reduziert."
            ),
        },
    ]
    turn = run_interview_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="was hat P1 übers mentoring gesagt? brauch die genaue stelle mit zeit",
        segments=segments,
        speakers={"S1": "Interviewer", "S2": "P1"},
        title="Onboarding interview",
        analysis={},
        history=[],
        language="de",
    )

    assert turn.quotes == [
        {
            "segment": 2,
            "text": segments[1]["text"],
            "verified": True,
            "start_ms": 30_000,
            "timestamp": "00:30",
            "speaker": "S2",
        }
    ]


def test_interview_agent_replaces_unsupported_claim_with_clear_missing_evidence() -> None:
    payload = {
        "answer": "P1 considered the payment adequate.",
        "quotes": [{"segment": 2, "text": "The payment was adequate."}],
        "workspace_actions": [],
    }
    turn = run_interview_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request="und was sagt sie zur bezahlung? bitte nix dazuerfinden",
        segments=[
            {
                "idx": 1,
                "speaker": "S2",
                "start_ms": 0,
                "end_ms": 30_000,
                "text": "Das Mentoring war hilfreich.",
            }
        ],
        speakers={"S2": "P1"},
        title="Onboarding interview",
        analysis={},
        history=[],
        language="de",
    )

    assert turn.quotes == []
    assert turn.answer == (
        "Im Transkript gibt es dazu keine verifizierbare Stelle. "
        "Ich kann deshalb keine belegte Aussage dazu machen."
    )


def test_interview_agent_rejects_single_transcript_generalization() -> None:
    payload = {
        "answer": "Alle Teilnehmenden profitieren von Mentoring.",
        "quotes": [{"segment": 1, "text": "Das Mentoring war hilfreich."}],
        "workspace_actions": [],
    }
    turn = run_interview_agent(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        request=(
            "verallgemeinere das auf alle teilnehmer aber sag nichts was nicht im "
            "einzelnen transcript steht"
        ),
        segments=[
            {
                "idx": 1,
                "speaker": "S2",
                "start_ms": 0,
                "end_ms": 30_000,
                "text": "Das Mentoring war hilfreich.",
            }
        ],
        speakers={"S2": "P1"},
        title="Onboarding interview",
        analysis={},
        history=[],
        language="de",
    )

    assert turn.answer == (
        "Dieses einzelne Interview belegt nur die Aussagen dieser Person. "
        "Ich kann daraus keine Aussage über alle Teilnehmenden verallgemeinern."
    )
    assert len(turn.quotes) == 1
    assert turn.quotes[0]["verified"] is True


def test_specialist_format_recovery_uses_reliable_internal_formatter() -> None:
    recovered = json.dumps(
        {
            "answer": "No responses have been submitted yet.",
            "actions": [],
            "workspace_actions": [],
        }
    )
    pool = _PinnedRecoveryPool(recovered)

    turn = run_survey_agent(
        pool,  # type: ignore[arg-type]
        request="sag kurz ob schon jemand geantwortet hat, nix ändern",
        title="Pilot",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert pool.primary_calls == 1
    assert pool.pinned_models == ["gemini:gemini-3.5-flash"]
    assert turn.answer == "No responses have been submitted yet."
    assert turn.actions == []
    assert turn.workspace_actions == []


def test_writer_blocks_new_unknown_citations_and_figure_files() -> None:
    source = "\\documentclass{article}\n\\begin{document}\nText.\n\\end{document}"
    anchor = "Text."
    payload = {
        "reply": "I prepared four reviewable edits.",
        "edits": [
            {
                "path": "main.tex",
                "find": anchor,
                "replace": "Claim. \\citep{invented2026}",
            },
            {
                "path": "main.tex",
                "find": anchor,
                "replace": "Claim. \\citep{verified2026}",
            },
            {
                "path": "main.tex",
                "find": anchor,
                "replace": "\\includegraphics{missing.png}",
            },
            {
                "path": "main.tex",
                "find": anchor,
                "replace": "\\includegraphics{verified-figure}",
            },
        ],
        "visual_request": None,
        "workspace_actions": [],
    }

    turn = run_assistant_turn(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message="Improve the manuscript and use the linked evidence and figure.",
        citations=[{"key": "verified2026", "title": "Verified source"}],
        assets=["verified-figure.png"],
        works=[],
        history=[],
    )

    assert turn.edits == []
    assert "Your manuscript is unchanged" in turn.reply
    assert "Select the passage or name the section" in turn.reply
    assert "source anchor" not in turn.reply


def test_writer_local_rewrite_cannot_add_an_unrequested_known_citation() -> None:
    payload = {
        "reply": "I rewrote the selected sentence.",
        "edits": [
            {
                "path": "main.tex",
                "find": "This sentence needs work.",
                "replace": "This sentence is clearer \\citep{verified2026}.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }

    turn = run_assistant_turn(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        project_files={"main.tex": "This sentence needs work."},
        active_path="main.tex",
        message="Rewrite this sentence more clearly.",
        citations=[{"key": "verified2026", "title": "Verified source"}],
        assets=[],
        works=[],
        history=[],
        selection={
            "kind": "source",
            "path": "main.tex",
            "line": 1,
            "quote": "This sentence needs work.",
        },
    )

    assert turn.edits == []
    assert "Your manuscript is unchanged" in turn.reply
    assert "Select the passage or name the section" in turn.reply
    assert "source anchor" not in turn.reply


def test_writer_refuses_an_unsupported_quantitative_visual() -> None:
    payload = {
        "reply": "I prepared the chart.",
        "edits": [],
        "visual_request": {
            "prompt": "Draw a bar chart comparing Alpha and Beta performance.",
            "kind": "plot",
            "aspect_ratio": "4:3",
            "resolution": "2k",
            "review_passes": 1,
        },
        "workspace_actions": [],
    }

    turn = run_assistant_turn(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        project_files={"main.tex": "No exact quantitative results are reported."},
        active_path="main.tex",
        message="Create a bar chart comparing Alpha and Beta.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert turn.visual_request is None
    assert turn.workspace_actions == []
    assert "at least two exact labelled values" in turn.reply


def test_specialist_agents_discard_unstructured_model_text() -> None:
    raw = "The model says 73 percent, although no source material proves that."
    pool = _FixedPool(raw)

    dataset = run_dataset_agent(
        pool,  # type: ignore[arg-type]
        request="Summarize the available data.",
        name="Dataset",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=1,
        profile={"columns": [{"name": "value", "type": "number"}], "preview": []},
        versions=[],
        history=[],
        language="en",
    )
    survey = run_survey_agent(
        pool,  # type: ignore[arg-type]
        request="Summarize the responses.",
        title="Survey",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="en",
    )
    interview = run_interview_agent(
        pool,  # type: ignore[arg-type]
        request="Summarize what was said.",
        segments=[],
        speakers={},
        title="Interview",
        analysis={},
        history=[],
        language="en",
    )
    study = run_study_agent(
        pool,  # type: ignore[arg-type]
        request="Explain the current interview design.",
        study={},
        history=[],
        language="en",
    )
    writer = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\documentclass{article}"},
        active_path="main.tex",
        message="Summarize the manuscript.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    turns = [dataset, survey, interview, study, writer]
    answers = [getattr(turn, "answer", getattr(turn, "reply", "")) for turn in turns]
    assert all(raw not in answer for answer in answers)
    assert all("workspace was not changed" in answer.lower() for answer in answers)
    assert all("structured response" not in answer for answer in answers)
    assert dataset.actions == []
    assert survey.actions == []
    assert interview.quotes == []
    assert study.actions == []
    assert writer.edits == []
    assert all(turn.workspace_actions == [] for turn in turns)


def test_writer_keeps_a_complete_reply_from_truncated_json_without_actions() -> None:
    raw = (
        '{"reply":"I inspected the requested section and kept the manuscript '
        'unchanged because the edit payload was incomplete.","edits":['
    )

    turn = run_assistant_turn(
        _FixedPool(raw),  # type: ignore[arg-type]
        project_files={"main.tex": "\\documentclass{article}\n\\begin{document}\nText."},
        active_path="main.tex",
        message="Summarize the manuscript without changing it.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert turn.reply == (
        "I inspected the requested section and kept the manuscript unchanged "
        "because the edit payload was incomplete."
    )
    assert turn.edits == []
    assert turn.visual_request is None
    assert turn.workspace_actions == []


def test_survey_recovers_a_beginner_request_without_inventing_workspace_actions() -> None:
    recovered = {
        "answer": "I prepared the four requested questions in order.",
        "actions": [
            {
                "operation": "replace_questions",
                "questions": [
                    {
                        "title": "How old are you?",
                        "type": "short_text",
                        "required": True,
                        "options": [],
                    },
                    {
                        "title": "How often do you use AI?",
                        "type": "single_choice",
                        "required": True,
                        "options": ["Never", "Rarely", "Often", "Daily"],
                    },
                    {
                        "title": "Which tools do you use?",
                        "type": "multiple_choice",
                        "required": False,
                        "options": ["ChatGPT", "Gemini", "Claude", "Other"],
                    },
                    {
                        "title": "Why do you use these tools?",
                        "type": "long_text",
                        "required": False,
                        "options": [],
                    },
                ],
            }
        ],
        "workspace_actions": [],
    }
    pool = _SequencePool(
        "I will prepare that now, but forgot the JSON contract.",
        json.dumps(recovered),
    )

    turn = run_survey_agent(
        pool,  # type: ignore[arg-type]
        request=(
            "mach ne umfrage für bachelor studenten. erst alter, dann wie oft "
            "mit Nie Selten Oft Täglich als eine auswahl, dann ChatGPT Gemini "
            "Claude Sonstiges als mehrfachauswahl und zum schluss warum als "
            "lange antwort. alter und häufigkeit pflicht. noch nich veröffentlichen"
        ),
        title="Untitled",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    # Primary response, structured recovery and the bounded completion checkpoint.
    # The checkpoint may not invalidate the already verified recovered proposal.
    assert pool.calls == 3
    assert turn.answer == "I prepared the four requested questions in order."
    assert turn.workspace_actions == []
    assert len(turn.actions) == 1
    questions = turn.actions[0]["questions"]
    assert [question["type"] for question in questions] == [
        "short_text",
        "single_choice",
        "multiple_choice",
        "long_text",
    ]
    assert questions[1]["options"] == ["Never", "Rarely", "Often", "Daily"]
    assert questions[2]["options"] == ["ChatGPT", "Gemini", "Claude", "Other"]
    assert [question["required"] for question in questions] == [True, True, False, False]


def test_specialist_recovery_does_not_turn_existing_evidence_into_new_artifacts() -> None:
    request = (
        "nutze die bereits importierten interviewdaten und schreib daraus den "
        "ergebnisabschnitt, kein neues interview erstellen"
    )
    recovered = {
        "answer": "I can only use evidence available in this workspace.",
        "actions": [],
        "quotes": [],
        "workspace_actions": [
            {
                "type": "create_ai_interview",
                "title": "Wrong new interview",
                "sections": [],
            }
        ],
    }

    dataset = run_dataset_agent(
        _SequencePool("invalid", json.dumps(recovered)),  # type: ignore[arg-type]
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
        language="de",
    )
    survey = run_survey_agent(
        _SequencePool("invalid", json.dumps(recovered)),  # type: ignore[arg-type]
        request=request,
        title="Survey",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="de",
    )
    interview = run_interview_agent(
        _SequencePool("invalid", json.dumps(recovered)),  # type: ignore[arg-type]
        request=request,
        segments=[],
        speakers={},
        title="Interview",
        analysis={},
        history=[],
        language="de",
    )
    study = run_study_agent(
        _SequencePool("invalid", json.dumps(recovered)),  # type: ignore[arg-type]
        request=request,
        study={},
        history=[],
        language="de",
    )

    assert dataset.workspace_actions == []
    assert survey.workspace_actions == []
    assert interview.workspace_actions == []
    assert study.workspace_actions == []


def test_specialist_chats_recover_when_the_selected_provider_fails() -> None:
    recovered = json.dumps(
        {
            "answer": "I used only the material in the open workspace.",
            "actions": [],
            "workspace_actions": [],
        }
    )
    dataset_pool = _FailedPrimaryWithPinnedRecoveryPool(recovered)
    survey_pool = _FailedPrimaryWithPinnedRecoveryPool(recovered)
    study_pool = _FailedPrimaryWithPinnedRecoveryPool(recovered)

    dataset = run_dataset_agent(
        dataset_pool,  # type: ignore[arg-type]
        request="erklär mir die vorhandenen daten, nix neues anlegen",
        name="Pilot",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=1,
        profile={"columns": [{"name": "score", "type": "number"}], "preview": []},
        versions=[],
        history=[],
        language="de",
    )
    survey = run_survey_agent(
        survey_pool,  # type: ignore[arg-type]
        request="erklär nur die bestehende umfrage, nix verändern",
        title="Pilot survey",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="de",
    )
    study = run_study_agent(
        study_pool,  # type: ignore[arg-type]
        request="erklär nur den vorhandenen leitfaden, nix verändern",
        study={"title": "Pilot study", "guide": []},
        history=[],
        language="de",
    )

    assert dataset.answer == "I used only the material in the open workspace."
    assert survey.answer == "I used only the material in the open workspace."
    assert study.answer == "I used only the material in the open workspace."
    assert dataset.actions == survey.actions == study.actions == []
    assert dataset_pool.primary_calls == survey_pool.primary_calls == study_pool.primary_calls == 1
    assert dataset_pool.pinned_models
    assert survey_pool.pinned_models
    assert study_pool.pinned_models


def test_specialist_progress_copy_stays_focused_on_the_active_task() -> None:
    answer_payload = json.dumps(
        {
            "answer": "I checked the open workspace.",
            "actions": [],
            "workspace_actions": [],
        }
    )
    interview_payload = json.dumps(
        {
            "answer": "The participant described repeatable deployments.",
            "quotes": [
                {
                    "segment": 1,
                    "text": "Terraform made deployments repeatable.",
                }
            ],
            "workspace_actions": [],
        }
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        run_interview_agent(
            _SequencePool("invalid", interview_payload),  # type: ignore[arg-type]
            request="What did the participant say about Terraform?",
            segments=[
                {
                    "idx": 1,
                    "start_ms": 0,
                    "speaker": "S1",
                    "text": "Terraform made deployments repeatable.",
                }
            ],
            speakers={"S1": "Participant"},
            title="Terraform interview",
            analysis={},
            history=[],
            language="en",
        )
        run_dataset_agent(
            _SequencePool("invalid", answer_payload),  # type: ignore[arg-type]
            request="Summarize the current dataset.",
            name="Pilot data",
            description="",
            provenance="",
            license="",
            format="csv",
            row_count=1,
            profile={"columns": [{"name": "score", "type": "number"}], "preview": []},
            versions=[],
            history=[],
            language="en",
        )
        run_survey_agent(
            _SequencePool("invalid", answer_payload),  # type: ignore[arg-type]
            request="Summarize the current survey.",
            title="Pilot survey",
            description="",
            status="draft",
            questions=[],
            settings={},
            responses=[],
            history=[],
            language="en",
        )
        run_study_agent(
            _SequencePool("invalid", answer_payload),  # type: ignore[arg-type]
            request="Explain the current interview guide.",
            study={"title": "Pilot study", "guide": {"sections": []}},
            history=[],
            language="en",
        )

    visible_copy = " ".join(
        str(event.get(field) or "") for event in events for field in ("label", "detail")
    ).casefold()
    assert "matching the request to supporting passages" in visible_copy
    assert "matching the requested analyses and charts" in visible_copy
    assert "drafting the requested questions, wording and settings" in visible_copy
    assert "shape the requested topics, questions and settings" in visible_copy
    for internal_phrase in (
        "not creating",
        "working only inside",
        "validated editor actions",
        "server-owned",
        "the model may",
        "format repair",
        "first response",
        "first proposal",
        "discarding unverified",
        "recovering an answer",
        "another survey",
        "second study",
        "extra survey",
    ):
        assert internal_phrase not in visible_copy


def test_exact_count_followups_describe_the_target_not_an_internal_failed_draft() -> None:
    survey_pool = _CapturingSequencePool(
        json.dumps(
            {
                "answer": "I updated the wording.",
                "actions": [
                    {
                        "operation": "update_question",
                        "question_id": "q1",
                        "changes": {"title": "Do you use Terraform?"},
                    }
                ],
                "workspace_actions": [],
            }
        ),
        json.dumps(
            {
                "status": "complete",
                "summary": "The requested wording is represented.",
                "missing_actions": [],
            }
        ),
        json.dumps(
            {
                "answer": "I prepared exactly two questions.",
                "actions": [
                    {
                        "operation": "replace_questions",
                        "questions": [
                            {"title": "Do you use Terraform?", "type": "short_text"},
                            {"title": "Why?", "type": "long_text"},
                        ],
                    }
                ],
                "workspace_actions": [],
            }
        ),
    )
    study_sections = [
        {
            "title": title,
            "question": question,
            "probes": ["Can you give an example?"],
            "must_cover": True,
        }
        for title, question in (
            ("Current use", "How do you use Terraform today?"),
            ("Challenges", "What makes Terraform difficult?"),
        )
    ]
    study_pool = _CapturingSequencePool(
        json.dumps(
            {
                "answer": "I drafted the guide.",
                "actions": [{"operation": "set_guide", "sections": study_sections[:1]}],
                "workspace_actions": [],
            }
        ),
        json.dumps(
            {
                "answer": "I prepared exactly two topics.",
                "actions": [{"operation": "set_guide", "sections": study_sections}],
                "workspace_actions": [],
            }
        ),
        json.dumps(
            {
                "status": "complete",
                "summary": "The requested guide is represented.",
                "missing_actions": [],
            }
        ),
    )
    events: list[dict[str, object]] = []

    with agent_event_sink(events.append):
        survey = run_survey_agent(
            survey_pool,  # type: ignore[arg-type]
            request="Change the open survey to exactly 2 questions.",
            title="Terraform survey",
            description="",
            status="draft",
            questions=[{"id": "q1", "title": "Old question", "type": "long_text"}],
            settings={},
            responses=[],
            history=[],
            language="en",
        )
        study = run_study_agent(
            study_pool,  # type: ignore[arg-type]
            request="Change the open guide to exactly 2 topics.",
            study={"title": "Terraform study", "guide": {"sections": []}},
            history=[],
            language="en",
        )

    assert len(survey.actions[0]["questions"]) == 2
    assert len(study.actions[0]["sections"]) == 2
    survey_prompt = str(survey_pool.requests[2].get("prompt") or "")
    study_prompt = str(study_pool.requests[1].get("prompt") or "")
    assert "Target the open survey represented below" in survey_prompt
    assert "Target the open study represented below" in study_prompt
    prompt_copy = f"{survey_prompt}\n{study_prompt}".casefold()
    assert "first validated" not in prompt_copy
    assert "never create" not in prompt_copy

    visible_copy = " ".join(
        str(event.get(field) or "") for event in events for field in ("label", "detail")
    ).casefold()
    assert "preparing a complete 2-question draft" in visible_copy
    assert "preparing a complete 2-topic guide" in visible_copy
    assert "first proposal" not in visible_copy
    assert "incomplete proposal" not in visible_copy
    assert "extra survey" not in visible_copy
    assert "second study" not in visible_copy


def test_writer_extracts_schema_json_wrapped_in_reasoning_prose() -> None:
    payload = {
        "reply": "I used the linked interview evidence in the draft.",
        "edits": [
            {
                "path": "main.tex",
                "find": "\\documentclass{article}",
                "replace": "\\documentclass{article}\nDraft finding from Interview A.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _FixedPool("I will format the answer now.\n```json\n" + json.dumps(payload) + "\n```")

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\documentclass{article}"},
        active_path="main.tex",
        message="Use the linked interview data to draft the findings.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        interview_evidence=[{"title": "Interview A", "analysis": {"summary": "Finding"}}],
    )

    assert turn.reply.startswith("I used the linked interview evidence in the draft.")
    assert "has not been applied" in turn.reply
    assert turn.edits[0]["applicable"] is True
    assert turn.workspace_actions == []


def test_writer_accepts_safe_defaults_when_optional_schema_fields_are_omitted() -> None:
    pool = _FixedPool(
        json.dumps(
            {
                "reply": "I inspected the linked evidence and kept the source unchanged.",
                "edits": [],
            }
        )
    )

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\documentclass{article}"},
        active_path="main.tex",
        message="Explain what the linked interview says without editing the manuscript.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        interview_evidence=[{"title": "Interview A", "analysis": {"summary": "Finding"}}],
    )

    assert turn.reply == "I inspected the linked evidence and kept the source unchanged."
    assert turn.edits == []
    assert turn.visual_request is None
    assert turn.workspace_actions == []


def test_writer_drops_noop_edits_from_the_review_queue() -> None:
    source = "\\section{Related Work}\nKeep this section unchanged."
    payload = {
        "reply": "I changed only the requested title.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Keep this section unchanged.",
                "replace": "Keep this section unchanged.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }

    turn = run_assistant_turn(
        _FixedPool(json.dumps(payload)),  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message="Lass diesen Abschnitt unverändert.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert turn.edits == []


def test_writer_recovers_a_transient_failure_on_the_selected_provider() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    recovered = {
        "reply": "I drafted the finding from the linked interview.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": (
                    "The linked interview indicates that mentoring reduced the "
                    "participant's uncertainty. This is evidence from one interview "
                    "and is not a population-level conclusion."
                ),
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }

    class TransientSelectedPool:
        def __init__(self) -> None:
            self.calls = 0

        def pinned(self, model: object) -> TransientSelectedPool:
            raise AssertionError(f"the selected model must not be overridden: {model}")

        def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("selected provider unavailable")
            return SimpleNamespace(
                text=json.dumps(recovered),
                provider="fake",
                model="selected-model",
            )

    pool = TransientSelectedPool()

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message=(
            "nimm die intzerview daten die ich hier importiert hab und schreib "
            "damit den ergebnisteil. kein neues interview erstellen"
        ),
        citations=[],
        assets=[],
        works=[],
        history=[],
        interview_evidence=[
            {
                "title": "P1 onboarding",
                "analysis": {"summary": "Mentoring reduced uncertainty."},
            }
        ],
    )

    assert pool.calls == 2
    assert turn.reply.startswith("I drafted the finding from the linked interview.")
    assert "has not been applied" in turn.reply
    assert len(turn.edits) == 1
    assert turn.edits[0]["applicable"] is True
    assert turn.workspace_actions == []


def test_writer_retries_when_a_clear_evidence_draft_request_omits_the_edit() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    deferred = {
        "reply": "I can draft this from the one linked interview.",
        "edits": [],
        "visual_request": None,
        "workspace_actions": [],
    }
    completed = {
        "reply": "I drafted the bounded finding from the linked interview.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": (
                    "The linked interview indicates that onboarding remained "
                    "unclear for the participant. Because this finding draws on "
                    "one interview, it is not evidence of a broader pattern."
                ),
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool(json.dumps(deferred), json.dumps(completed))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message=(
            "nimm die interview sachen die ich hier verknüpft hab und schreib "
            "mir daraus hier nen kurzen ergebnisteil. bitte kein neues interview machen"
        ),
        citations=[],
        assets=[],
        works=[],
        history=[],
        interview_evidence=[
            {
                "title": "P1 onboarding",
                "analysis": {"summary": "Onboarding remained unclear."},
            }
        ],
    )

    assert pool.calls == 2
    assert turn.reply.startswith("I drafted the bounded finding from the linked interview.")
    assert "has not been applied" in turn.reply
    assert len(turn.edits) == 1
    assert turn.edits[0]["applicable"] is True
    assert turn.workspace_actions == []


def test_writer_retries_when_a_clear_deletion_request_omits_the_edit() -> None:
    source = "\\section{Results}\nThis result should be removed.\n\\section{Discussion}"
    deferred = {
        "reply": "I prepared the requested deletion for your review.",
        "edits": [],
        "visual_request": None,
        "workspace_actions": [],
    }
    completed = {
        "reply": "I prepared an exact deletion proposal for your review.",
        "edits": [
            {
                "path": "main.tex",
                "find": "\\section{Results}\nThis result should be removed.",
                "replace": "",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool(json.dumps(deferred), json.dumps(completed))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message="lösch den ergebnisteil, aber wende die änderung noch nicht automatisch an",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert pool.calls == 2
    assert turn.reply.startswith("I prepared an exact deletion proposal for your review.")
    assert "has not been applied" in turn.reply
    assert len(turn.edits) == 1
    assert turn.edits[0]["find"] == "\\section{Results}\nThis result should be removed."
    assert turn.edits[0]["replace"] == ""
    assert turn.edits[0]["applicable"] is True


def test_writer_prepares_a_bounded_deletion_when_provider_keeps_deferring() -> None:
    source = (
        "\\section{Introduction}\nKeep this.\n"
        "\\section{Results}\nRemove this result.\n"
        "\\subsection{Sensitivity analysis}\nRemove this too.\n"
        "\\section{Discussion}\nKeep this discussion."
    )
    deferred = {
        "reply": "Ich warte auf Ihre Bestätigung, bevor ich den Vorschlag mache.",
        "edits": [],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _FixedPool(json.dumps(deferred))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message=(
            "lösch den ganzen ergebnisteil, aber erst nachdem ich den konkreten "
            "vorschlag bestätigt habe. jetzt noch nichts direkt anwenden"
        ),
        citations=[],
        assets=[],
        works=[],
        history=[],
        response_language="de",
    )

    assert len(turn.edits) == 1
    assert turn.edits[0]["applicable"] is True
    assert turn.edits[0]["replace"] == ""
    assert turn.edits[0]["find"] == (
        "\\section{Results}\nRemove this result.\n"
        "\\subsection{Sensitivity analysis}\nRemove this too.\n"
    )
    assert "Noch wurde nichts verändert" in turn.reply
    assert "bestätige" in turn.reply


def test_writer_gives_mutation_requests_exact_anchors_on_the_first_call() -> None:
    source = "\\section{Results}\n\nPlaceholder findings."
    payload = {
        "reply": "I drafted the finding from the linked interview.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": "One interview supports this bounded finding.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _CapturingPool(json.dumps(payload))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message="nutz das verknüpfte interview und schreib die ergebnisse hier rein",
        citations=[],
        assets=[],
        works=[],
        history=[],
        interview_evidence=[{"title": "P1", "analysis": {"summary": "Finding"}}],
    )

    assert len(pool.prompts) == 1
    assert "SAFE EDIT ANCHORS" in pool.prompts[0]
    assert '"path": "main.tex"' in pool.prompts[0]
    assert '"find": "Placeholder findings."' in pool.prompts[0]
    assert turn.edits[0]["applicable"] is True


def test_writer_does_not_force_an_edit_for_a_question_or_negative_instruction() -> None:
    answer = {
        "reply": "This passage is an interpretation rather than a direct quotation.",
        "edits": [],
        "visual_request": None,
        "workspace_actions": [],
    }
    question_pool = _SequencePool(json.dumps(answer), "must not be called")
    negative_pool = _SequencePool(json.dumps(answer), "must not be called")
    kwargs = {
        "project_files": {"main.tex": "\\section{Results}\nPlaceholder findings."},
        "active_path": "main.tex",
        "citations": [],
        "assets": [],
        "works": [],
        "history": [],
    }

    question = run_assistant_turn(
        question_pool,  # type: ignore[arg-type]
        message="Welche Aussage ist Interpretation und welche steht direkt im Transkript?",
        **kwargs,  # type: ignore[arg-type]
    )
    negative = run_assistant_turn(
        negative_pool,  # type: ignore[arg-type]
        message="Schreib noch nichts um, erklär mir nur die Interviewdaten.",
        **kwargs,  # type: ignore[arg-type]
    )

    assert question_pool.calls == 1
    assert negative_pool.calls == 1
    assert question.edits == []
    assert negative.edits == []


def test_writer_repairs_a_nonexistent_edit_anchor_without_fuzzy_applying_it() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    invalid = {
        "reply": "I drafted the result.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Findings from participant P1.",
                "replace": "A grounded result.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    repaired = {
        "reply": "I drafted the result with an exact source anchor.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": "A grounded result from one linked interview.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool(json.dumps(invalid), json.dumps(repaired))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message="Schreib aus dem verknüpften Interview einen Ergebnisteil hier rein.",
        citations=[],
        assets=[],
        works=[],
        history=[],
        interview_evidence=[{"title": "P1", "analysis": {"summary": "A bounded finding."}}],
    )

    assert pool.calls == 2
    assert turn.reply.startswith("I drafted the result with an exact source anchor.")
    assert "has not been applied" in turn.reply
    assert len(turn.edits) == 1
    assert turn.edits[0]["find"] == "Placeholder findings."
    assert turn.edits[0]["applicable"] is True


def test_writer_repairs_missing_interview_quote_attribution() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    ungrounded = {
        "reply": "I rewrote the finding in English.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": "Weekly mentoring reduced uncertainty.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    quote = "Das wöchentliche Mentoring hat meine Unsicherheit reduziert."
    repaired = {
        "reply": "I preserved the exact interview attribution.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": ("P1 described mentoring as helpful: ``" + quote + "'' (P1, 00:30)."),
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool(json.dumps(ungrounded), json.dumps(repaired))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message=(
            "Rewrite the finding in academic English, but keep the German "
            "verbatim quote with speaker and timestamp. Dont create a new interview."
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
    )

    assert pool.calls == 2
    assert quote in turn.edits[0]["replace"]
    assert "P1" in turn.edits[0]["replace"]
    assert "00:30" in turn.edits[0]["replace"]


def test_writer_preserves_the_quote_in_the_edit_that_actually_applies() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    quote = "Das wöchentliche Mentoring hat meine Unsicherheit reduziert."
    overlapping = {
        "reply": "I preserved the interview quote.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": f"P1 said: ``{quote}'' (P1, 00:30).",
            },
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _FixedPool(json.dumps(overlapping))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message=(
            "Rewrite the result in English but keep the German verbatim quote "
            "with speaker and timestamp."
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
    )

    projected = source
    for edit in turn.edits:
        if edit["applicable"] and projected.count(edit["find"]) == 1:
            projected = projected.replace(edit["find"], edit["replace"], 1)
    assert [edit["applicable"] for edit in turn.edits] == [True]
    assert quote in projected
    assert "P1" in projected
    assert "00:30" in projected


def test_writer_does_not_let_an_older_quote_mask_a_corrupted_rewrite() -> None:
    quote = "Das wöchentliche Mentoring hat meine Unsicherheit reduziert."
    target = f"P1 said: ``{quote}'' (P1, 00:30)."
    source = "\\section{Notes}\n" + quote + "\n\\section{Results}\n" + target
    corrupted = {
        "reply": "I rewrote the finding in English.",
        "edits": [
            {
                "path": "main.tex",
                "find": target,
                "replace": (
                    'P1 said: ``Das w\\"ochterliche Mentoring hat meine '
                    "Unsicherheit reduziert.'' (P1, 00:30)."
                ),
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _FixedPool(json.dumps(corrupted))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message=(
            "Rewrite the result in English but keep the German verbatim quote "
            "with speaker and timestamp."
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
    )

    assert len(turn.edits) == 1
    assert turn.edits[0]["applicable"] is True
    assert quote in turn.edits[0]["replace"]
    assert "P1" in turn.edits[0]["replace"]
    assert "00:30" in turn.edits[0]["replace"]


def test_writer_does_not_require_verbatim_quote_when_only_attribution_is_requested() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    grounded = {
        "reply": "I retained the requested source attribution.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": (
                    "Weekly mentoring reduced the participant's initial uncertainty (P1, 00:30)."
                ),
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool(json.dumps(grounded))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": source},
        active_path="main.tex",
        message=(
            "Überarbeite den Befund anhand der vorhandenen Antwort. "
            "Behalte Sprecher und Zeitangabe bei."
        ),
        citations=[],
        assets=[],
        works=[],
        history=[],
        interview_evidence=[
            {
                "title": "P1",
                "passages": [
                    {
                        "speaker": "P1",
                        "start_ms": 30_000,
                        "end_ms": 60_000,
                        "text": "Das wöchentliche Mentoring half mir sehr.",
                    }
                ],
            }
        ],
    )

    assert pool.calls == 1
    assert turn.edits[0]["applicable"] is True
    assert "P1" in turn.edits[0]["replace"]
    assert "00:30" in turn.edits[0]["replace"]


def test_writer_safely_adds_missing_requested_interview_labels() -> None:
    source = "\\section{Results}\nPlaceholder findings."
    missing_labels = {
        "reply": "I grounded the finding in the linked interview.",
        "edits": [
            {
                "path": "main.tex",
                "find": "Placeholder findings.",
                "replace": "Weekly mentoring reduced initial uncertainty.",
            }
        ],
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool(json.dumps(missing_labels), json.dumps(missing_labels))

    events: list[dict[str, object]] = []
    with agent_event_sink(events.append):
        turn = run_assistant_turn(
            pool,  # type: ignore[arg-type]
            project_files={"main.tex": source},
            active_path="main.tex",
            message=(
                "Überarbeite den Befund anhand der vorhandenen Antwort. "
                "Behalte Sprecher und Zeitangabe bei."
            ),
            citations=[],
            assets=[],
            works=[],
            history=[],
            interview_evidence=[
                {
                    "title": "P1",
                    "analysis_text": "Weekly mentoring reduced initial uncertainty.",
                    "passages": [
                        {
                            "speaker": "P1",
                            "start_ms": 30_000,
                            "end_ms": 60_000,
                            "text": "Das wöchentliche Mentoring half mir sehr.",
                        }
                    ],
                }
            ],
        )

    assert 1 < pool.calls <= 10
    assert any(
        event.get("event") == "checkpoint.failed"
        and event.get("tool") == "manuscript.verify_completion"
        and "interview" in str(event.get("detail") or "").casefold()
        for event in events
    )
    assert not any(
        event.get("event") == "checkpoint.completed"
        and event.get("tool") == "manuscript.verify_completion"
        for event in events
    )
    assert turn.edits[0]["applicable"] is True
    assert turn.edits[0]["replace"].endswith("(P1, 00:30)")
    assert turn.workspace_actions == []


def test_writer_retries_invalid_structure_without_accepting_unverified_edits() -> None:
    recovered = {
        "reply": "I prepared a grounded response without applying changes.",
        "edits": "not a verified edit list",
        "visual_request": None,
        "workspace_actions": [],
    }
    pool = _SequencePool("not valid structured output", json.dumps(recovered))

    turn = run_assistant_turn(
        pool,  # type: ignore[arg-type]
        project_files={"main.tex": "\\documentclass{article}"},
        active_path="main.tex",
        message="Summarize the linked evidence for this manuscript.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert pool.calls == 2
    assert turn.reply == "I prepared a grounded response without applying changes."
    assert turn.edits == []


def test_specialists_recover_a_useful_answer_without_accepting_actions() -> None:
    """A third, answer-only pass must degrade safely across every specialist."""

    invalid = "I forgot the required JSON contract."
    dataset = run_dataset_agent(
        _SequencePool(invalid, invalid, '{"answer":"The available rows do not prove a trend."}'),  # type: ignore[arg-type]
        request="Summarize only what the imported rows support.",
        name="Dataset",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=1,
        profile={"columns": [{"name": "value", "type": "number"}], "preview": []},
        versions=[],
        history=[],
        language="en",
    )
    survey = run_survey_agent(
        _SequencePool(invalid, invalid, '{"answer":"No responses are available yet."}'),  # type: ignore[arg-type]
        request="Summarize the responses without changing the survey.",
        title="Survey",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="en",
    )
    interview = run_interview_agent(
        _SequencePool(invalid, invalid, '{"answer":"The transcript is empty."}'),  # type: ignore[arg-type]
        request="Summarize the imported transcript.",
        segments=[],
        speakers={},
        title="Interview",
        analysis={},
        history=[],
        language="en",
    )
    study = run_study_agent(
        _SequencePool(invalid, invalid, '{"answer":"The guide has no questions yet."}'),  # type: ignore[arg-type]
        request="Explain the current interview design without editing it.",
        study={},
        history=[],
        language="en",
    )
    writer = run_assistant_turn(
        _SequencePool(
            invalid,
            invalid,
            '{"reply":"The linked evidence is insufficient for a supported finding."}',
        ),  # type: ignore[arg-type]
        project_files={"main.tex": "\\documentclass{article}"},
        active_path="main.tex",
        message="Summarize the linked evidence without changing the manuscript.",
        citations=[],
        assets=[],
        works=[],
        history=[],
    )

    assert dataset.answer == "The available rows do not prove a trend."
    assert survey.answer == "No responses are available yet."
    assert interview.answer == (
        "The transcript contains no verifiable passage about that. "
        "I therefore cannot make a supported claim about it."
    )
    assert study.answer == "The guide has no questions yet."
    assert writer.reply == "The linked evidence is insufficient for a supported finding."
    assert dataset.actions == []
    assert survey.actions == []
    assert interview.quotes == []
    assert study.actions == []
    assert writer.edits == []
    assert all(turn.workspace_actions == [] for turn in (dataset, survey, interview, study, writer))


def test_structured_failure_message_respects_german_workspace_language() -> None:
    turn = run_survey_agent(
        _FixedPool("keine strukturierte Antwort"),  # type: ignore[arg-type]
        request="Fasse die Antworten zusammen.",
        title="Umfrage",
        description="",
        status="draft",
        questions=[],
        settings={},
        responses=[],
        history=[],
        language="de",
    )

    assert "nicht geklappt" in turn.answer
    assert "strukturierte Antwort" not in turn.answer
    assert "Workspace wurde nicht verändert" in turn.answer
