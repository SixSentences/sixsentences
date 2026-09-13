"""Regression matrix for realistic novice requests across the workspace.

These prompts intentionally mix German and English, shorthand, typos and
implicit product language. They exercise deterministic authorization and
routing only, so the suite never spends provider tokens or depends on a
model's mood.
"""

from collections.abc import Callable

import pytest

from sixsentences_server.agent.actions import workspace_action_types_requested
from sixsentences_server.pipeline.ask import _heuristic_plan, _library_paper_request


def _has_action(action: str) -> Callable[[str], bool]:
    return lambda prompt: action in workspace_action_types_requested(prompt)


def _is_library_paper(prompt: str) -> bool:
    return _library_paper_request(prompt)


def _is_library_workspace(prompt: str) -> bool:
    return "open_library" in workspace_action_types_requested(
        prompt
    ) and not _library_paper_request(prompt)


def _is_comparison(prompt: str) -> bool:
    plan = _heuristic_plan(prompt, attached=False, web_available=True)
    return plan.compare_papers and plan.table


def _is_chart(prompt: str) -> bool:
    return _heuristic_plan(prompt, attached=False, web_available=True).chart


def _is_reader_request(prompt: str) -> bool:
    return _heuristic_plan(prompt, attached=False, web_available=True).show_paper


def _is_save_request(prompt: str) -> bool:
    return _heuristic_plan(prompt, attached=False, web_available=True).save_paper


PROMPT_MATRIX: tuple[tuple[str, str, Callable[[str], bool]], ...] = (
    # Stored Library papers: direct tenant data, never a public search.
    (
        "library paper de",
        "Kannst du ein Paper aus meiner Library öffnen, egal welches?",
        _is_library_paper,
    ),
    ("library pdf typo", "öffne irgendeine pdf aus meiner Biblothek pls", _is_library_paper),
    ("library paper en", "Open any paper from my library.", _is_library_paper),
    ("library document typo", "zeig mir eines meiner dokumente aus der libary", _is_library_paper),
    ("library study", "Lies eine Studie aus meiner Bibliothek.", _is_library_paper),
    # Navigation remains distinct from selecting a random document.
    ("library workspace de", "Öffne bitte meine Bibliothek.", _is_library_workspace),
    ("library workspace en", "Take me to my research library.", _is_library_workspace),
    ("library workspace typo", "kannst du meine libary aufmachen", _is_library_workspace),
    # Scientific visuals.
    (
        "visual architecture",
        "Erstelle ein Architekturdiagramm für meinen RAG-Ansatz.",
        _has_action("create_visual"),
    ),
    ("visual flow typo", "mach mir n ablaufdiagarmm für die methode", _has_action("create_visual")),
    (
        "visual figure en",
        "Create a publication-ready figure of this pipeline.",
        _has_action("create_visual"),
    ),
    (
        "visual concept",
        "Bau eine wissenschaftliche Grafik, die das Konzept erklärt.",
        _has_action("create_visual"),
    ),
    (
        "visual thesis",
        "Ich brauche eine Abbildung für meine Masterarbeit.",
        _has_action("create_visual"),
    ),
    # Surveys.
    (
        "survey direct",
        "Erstelle eine Umfrage zur KI-Nutzung im Studium.",
        _has_action("create_survey"),
    ),
    ("survey typo", "bau mir ne umfarge mit 10 fragen", _has_action("create_survey")),
    (
        "survey feedback",
        "I need a questionnaire to collect participant feedback.",
        _has_action("create_survey"),
    ),
    (
        "survey implicit",
        "Sammle Bewertungen von Studierenden zu dem Kurs.",
        _has_action("create_survey"),
    ),
    (
        "survey bachelor",
        "Mach einen Fragebogen für meine Bachelorarbeit.",
        _has_action("create_survey"),
    ),
    # AI interviews.
    (
        "interview guide",
        "Erstelle ein KI Interview mit einem Leitfaden.",
        _has_action("create_ai_interview"),
    ),
    (
        "interview study",
        "Set up an AI-led interview study for 20 participants.",
        _has_action("create_ai_interview"),
    ),
    ("interview typo", "mach ne interviewstudie zu homeoffice", _has_action("create_ai_interview")),
    (
        "interview questions",
        "Bau mir einen Interviewleitfaden und hoste das Interview.",
        _has_action("create_ai_interview"),
    ),
    # Manuscripts.
    (
        "manuscript direct",
        "Erstelle ein Manuskript für meine Seminararbeit.",
        _has_action("create_manuscript"),
    ),
    (
        "manuscript en",
        "Create a writing project for this journal paper.",
        _has_action("create_manuscript"),
    ),
    ("manuscript typo", "mach daraus ein manuskrpit", _has_action("create_manuscript")),
    (
        "manuscript thesis",
        "Ich will daraus eine wissenschaftliche Arbeit schreiben.",
        _has_action("create_manuscript"),
    ),
    # Systematic reviews.
    ("review slr", "Starte eine SLR zu LLMs im Abstract Screening.", _has_action("start_review")),
    (
        "review full de",
        "Mach eine systematische Literaturrecherche zu Terraform Security.",
        _has_action("start_review"),
    ),
    (
        "review en",
        "Start a systematic literature review on AI tutoring.",
        _has_action("start_review"),
    ),
    ("review typo", "starte ne systematsiche literaturrecherche dazu", _has_action("start_review")),
    (
        "review german",
        "Erstelle einen systematischen Review mit PRISMA.",
        _has_action("start_review"),
    ),
    # Data Hub.
    ("data dataset", "Öffne ein Dataset im Data Hub für diese CSV.", _has_action("open_data_hub")),
    ("data german", "Leg dafür einen Datensatz im Datenhub an.", _has_action("open_data_hub")),
    (
        "data typo",
        "mach das im datahub auf damit ich es analysieren kann",
        _has_action("open_data_hub"),
    ),
    # Transcript upload/import.
    ("transcript de", "Lade dieses Interview Transkript hoch.", _has_action("upload_interview")),
    (
        "recording en",
        "Import this audio recording as an interview transcript.",
        _has_action("upload_interview"),
    ),
    (
        "transcript typo",
        "ich will die aufname transkribieren und importieren",
        _has_action("upload_interview"),
    ),
    # Structured evidence comparisons.
    ("table overview", "Gib mir einen Überblick über die 8 wichtigsten Paper.", _is_comparison),
    ("table direct", "Vergleiche diese fünf Studien in einer Tabelle.", _is_comparison),
    ("table matrix", "Create an evidence matrix for ten current papers.", _is_comparison),
    ("table novice", "Kannst du die Paper mal übersichtlich gegenüberstellen?", _is_comparison),
    ("table typo", "mach ne vergleichstabelle aus den studien", _is_comparison),
    # Analytical charts stay inline rather than opening Visual Lab.
    ("chart years", "Zeige die gefundenen Publikationen nach Jahr als Diagramm.", _is_chart),
    ("chart prisma", "Render the PRISMA funnel as a chart.", _is_chart),
    ("chart venues", "Mach ein Diagramm der häufigsten Journals.", _is_chart),
    # Reader and verified highlights.
    ("reader pdf", "Zeig mir das PDF des relevantesten Papers.", _is_reader_request),
    ("reader highlights", "Markier mir die wichtigsten Stellen im Paper.", _is_reader_request),
    ("reader typo", "can u open the fulltext and hightlight the results", _is_reader_request),
    # Explicit durable saves.
    ("save library de", "Speichere das Paper in meiner Bibliothek.", _is_save_request),
    ("save library en", "Add this article to my Library.", _is_save_request),
)


def test_prompt_matrix_contains_exactly_fifty_realistic_requests() -> None:
    assert len(PROMPT_MATRIX) == 50
    assert len({name for name, _, _ in PROMPT_MATRIX}) == 50


@pytest.mark.parametrize(("name", "prompt", "expectation"), PROMPT_MATRIX)
def test_realistic_prompt_routes_to_the_expected_native_capability(
    name: str,
    prompt: str,
    expectation: Callable[[str], bool],
) -> None:
    assert expectation(prompt), f"{name!r} was misrouted: {prompt!r}"


WORKSPACE_CONTROL_MATRIX: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("theme white", "Stell bitte auf Whitemode um", ("set_theme",)),
    ("theme dark", "Mach die komplette App dunkel", ("set_theme",)),
    ("theme workspace wording", "Switch the workspace to light mode", ("set_theme",)),
    ("theme system", "Nimm wieder das System Theme", ("set_theme",)),
    ("theme typo", "wechsle bidde in den darkmode", ("set_theme",)),
    (
        "theme settings navigation",
        "Kannst du die Darstellungs-Einstellungen öffnen?",
        ("open_settings",),
    ),
    ("theme information", "Was ist der Dark Mode?", ()),
    ("theme metaphor", "Diese Antwort bitte etwas dunkler formulieren", ()),
    ("theme return", "switch back to white mode pls", ("set_theme",)),
    ("theme novice", "mach die UI hell", ("set_theme",)),
    ("language german", "Stell die Systemsprache auf Deutsch", ("set_language",)),
    ("language english", "Switch the app language to English", ("set_language",)),
    ("language implicit app", "Mach die App auf Deutsch", ("set_language",)),
    (
        "language interface",
        "change interface language to German",
        ("set_language",),
    ),
    (
        "language settings navigation",
        "Öffne die Spracheinstellungen",
        ("open_settings",),
    ),
    ("language current turn", "Antworte nur bei dieser Frage auf Deutsch", ()),
    ("language translation", "Übersetze den Absatz ins Englische", ()),
    (
        "language typo",
        "systmsprache bitte auf englisch stellen",
        ("set_language",),
    ),
    (
        "preference concise",
        "Antworte ab jetzt immer kurz und direkt",
        ("update_assistant_preferences",),
    ),
    (
        "preference academic",
        "Make the assistant always academic and thorough",
        ("update_assistant_preferences",),
    ),
    (
        "preference critical",
        "Stell die KI auf kritisch",
        ("update_assistant_preferences",),
    ),
    (
        "preference prose",
        "Antworten standardmäßig als Fließtext",
        ("update_assistant_preferences",),
    ),
    (
        "preference all chats",
        "Für alle Chats bitte strukturiert und ausführlich antworten",
        ("update_assistant_preferences",),
    ),
    (
        "preference custom",
        "Ab jetzt keine Gedankenstriche verwenden",
        ("update_assistant_preferences",),
    ),
    ("preference one turn", "Mach diese eine Antwort kurz", ()),
    (
        "preference general",
        "Kannst du generell eher erklärend antworten?",
        ("update_assistant_preferences",),
    ),
    (
        "preference adaptive",
        "Set the default response format to adaptive",
        ("update_assistant_preferences",),
    ),
    (
        "preference mixed",
        "KI Verhalten auf akademisch und ausgewogen einstellen",
        ("update_assistant_preferences",),
    ),
    ("settings api keys", "Öffne meine API-Key Einstellungen", ("open_settings",)),
    ("settings webhooks", "Bring mich zu den Webhooks", ("open_settings",)),
    ("settings usage", "Zeig mir die Nutzung und meinen Plan", ("open_settings",)),
    ("settings team", "Öffne die Team Einstellungen", ("open_settings",)),
    ("settings 2fa", "Bring mich zur 2FA Einrichtung", ("open_settings",)),
    ("settings legal", "Open the legal settings", ("open_settings",)),
    ("settings destructive", "Lösch meinen Account sofort", ()),
    ("settings account", "Zeig die Kontoeinstellungen", ("open_settings",)),
    (
        "connector zotero",
        "Connecte mein Zotero",
        ("connect_reference_manager",),
    ),
    (
        "connector setup",
        "Zotero bitte einrichten",
        ("connect_reference_manager",),
    ),
    (
        "connector library wording",
        "Link my Zotero library",
        ("connect_reference_manager",),
    ),
    (
        "connector citavi sync",
        "Synchronisiere Citavi mit dem Workspace",
        ("connect_reference_manager",),
    ),
    (
        "connector citavi import",
        "Import my Citavi project",
        ("connect_reference_manager",),
    ),
    ("connector information", "Was ist Zotero und wie funktioniert es?", ()),
    (
        "project thesis",
        "Erstelle ein neues Projekt für meine Masterarbeit",
        ("create_project",),
    ),
    (
        "project named",
        "Leg bitte n Projekt namens Thesis an",
        ("create_project",),
    ),
    (
        "project workspace",
        "Create a workspace for the screening study",
        ("create_project",),
    ),
    (
        "project organize",
        "organisier die Quellen in einem neuen Projekt",
        ("create_project",),
    ),
    (
        "project implicit",
        "Ich will die Paper in einem Projekt bündeln",
        ("create_project",),
    ),
    ("project information", "Was ist ein Projekt?", ()),
    ("project rename", "Benenne mein Projekt um", ("manage_resource",)),
    ("project delete", "Lösche das aktuelle Projekt", ("manage_resource",)),
)


def test_workspace_control_matrix_contains_fifty_realistic_edge_cases() -> None:
    assert len(WORKSPACE_CONTROL_MATRIX) == 50
    assert len({name for name, _, _ in WORKSPACE_CONTROL_MATRIX}) == 50


@pytest.mark.parametrize(("name", "prompt", "expected"), WORKSPACE_CONTROL_MATRIX)
def test_workspace_controls_are_routed_without_unsafe_false_positives(
    name: str,
    prompt: str,
    expected: tuple[str, ...],
) -> None:
    assert workspace_action_types_requested(prompt) == expected, (
        f"{name!r} was misrouted: {prompt!r}"
    )
