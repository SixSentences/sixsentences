"""Narrow numeric-fabrication regression cases for wording-only rewrites."""

import pytest

from sixsentences_server.writer.assistant import (
    _project_only_rewrite_request,
    _rewrite_numeric_facts,
    _validated_writer_edits,
    _wording_only_rewrite_request,
)


def _validate(source: str, replacement: str, request: str | None) -> dict:
    """Validate a single proposal without running a provider or applying it."""

    return _validated_writer_edits(
        {"edits": [{"path": "main.tex", "find": source, "replace": replacement}]},
        project_files={"main.tex": source},
        active_path="main.tex",
        known_citations=set(),
        known_assets=set(),
        allow_new_references=False,
        rewrite_request=request,
    )[0]


def test_aggregate_rewrite_cannot_invent_individual_task_numbers() -> None:
    """The browser repro only provides totals, not a per-task outcome map."""

    source = "Of five fictional tasks, three succeeded and two failed."
    result = _validate(
        source,
        "Tasks 1, 3 and 5 succeeded, while tasks 2 and 4 failed.",
        "Rewrite this paragraph more clearly, preserving its meaning.",
    )
    assert result["applicable"] is False
    assert "unsupported numeric details: 1, 4" in result["integrity_errors"][0]


@pytest.mark.parametrize(
    "source",
    [
        "Of five tasks, three succeeded and two failed.",
        "Von fünf Aufgaben waren drei erfolgreich und zwei erfolglos.",
    ],
)
def test_existing_number_words_can_be_rewritten_as_digits(source: str) -> None:
    """A different numeric notation must not look like a new research fact."""

    result = _validate(source, "5 tasks: 3 succeeded; 2 failed.", "Rewrite the paragraph.")
    assert result["applicable"] is True


def test_explicit_author_correction_can_introduce_a_number() -> None:
    """The author's current request may supply corrected information."""

    result = _validate(
        "The study had 5 tasks.",
        "The study had 6 tasks.",
        "Correct the count to 6.",
    )
    assert result["applicable"] is True


def test_ordinary_prose_is_not_treated_as_a_new_numeric_literal() -> None:
    """Articles and ordering language should not reject an otherwise valid edit."""

    result = _validate(
        "Die Studie erklärt das Ergebnis.",
        "Zuerst folgt eine Erklärung.",
        "Formuliere um.",
    )
    assert result["applicable"] is True


def test_measurements_are_not_stripped_as_layout_units() -> None:
    """Research measurements and English 'in' remain protected numeric facts."""

    assert _rewrite_numeric_facts("12 cm; 50 in this group.") == {"12", "50"}
    result = _validate(
        "The sample measured 12 cm.",
        "The sample measured 18 cm.",
        "Rewrite the sentence.",
    )
    assert result["applicable"] is False


def test_non_rewrite_drafting_keeps_existing_validation_contract() -> None:
    """This narrow guard must not block separately grounded drafting workflows."""

    result = _validate("Study results follow.", "The linked study reports 42 results.", None)
    assert result["applicable"] is True


_BROWSER_HISTORY_REWRITE_REQUEST = (
    "In main.tex rewrite only the Observations section as two concise sentences, "
    "preserving exactly five fictional tasks, three successes, and two failures. "
    "Explicitly label the observations synthetic, not research evidence. "
    "Do not change other sections or add sources. "
    "Return an edit proposal for me to apply."
)

_ADDITIONAL_REWRITE_VARIANT = (
    "In main.tex, rewrite only the Observations paragraph into two clear sentences. "
    "Keep the exact fictional counts: five tasks, three successes, two failures. "
    "Explicitly state that these are synthetic QA data, not a research finding. "
    "Leave the Purpose section, title and author unchanged. "
    "Propose the edit; do not publish or create sources."
)


@pytest.mark.parametrize(
    "prompt",
    [
        pytest.param(_BROWSER_HISTORY_REWRITE_REQUEST, id="actual-browser-history"),
        pytest.param(_ADDITIONAL_REWRITE_VARIANT, id="additional-phrasing-variant"),
    ],
)
def test_rewrite_scope_protects_existing_synthetic_data(prompt: str) -> None:
    """Protect the observed history request and a separately constructed variant."""

    assert _wording_only_rewrite_request(prompt) is True
    # The evidence/tool classifier is deliberately unchanged by this guard.
    assert _project_only_rewrite_request(prompt) is False
    result = _validate(
        "Of five fictional tasks, three succeeded and two failed.",
        "Tasks 1, 3 and 5 succeeded, while tasks 2 and 4 failed.",
        prompt if _wording_only_rewrite_request(prompt) else None,
    )
    assert result["applicable"] is False


@pytest.mark.parametrize(
    "prompt",
    [
        "Rewrite the paragraph, preserving its meaning. Do not search the web.",
        "Rewrite this sentence without adding new facts. Do not use external sources.",
        "Rewrite the paragraph and preserve the existing data values. Do not create sources.",
        "Rewrite the section, preserving exactly 5 fictional tasks. "
        "Do not change sections or add sources.",
        "Formuliere diesen Absatz um und behalte die Zahlen bei. Nutze keine neuen Quellen.",
        "Überarbeite den Abschnitt ohne neue Fakten. Suche nicht nach Studien.",
    ],
)
def test_explicit_fidelity_scope_accepts_research_prohibitions(prompt: str) -> None:
    """A restriction on outside research does not veto source-preserving editing."""

    assert _wording_only_rewrite_request(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "Rewrite the paragraph and preserve its meaning. Search the web for current statistics.",
        "Rewrite the paragraph and keep the existing counts. Use the linked interview evidence.",
        "Rewrite the paragraph without inventing details. Include numbers from the uploaded dataset.",
        "Rewrite the paragraph and preserve facts. Do not create sources, but search the web.",
        "Rewrite the paragraph and preserve facts. Do not search the web, use the linked study.",
        "Formuliere den Absatz um, behalte die Zahlen bei und nutze die neue Studie.",
        "Rewrite the paragraph based on the linked study. Preserve the existing facts.",
        "Rewrite the paragraph and preserve facts. Use the latest data.",
        _BROWSER_HISTORY_REWRITE_REQUEST + " Search the web for additional evidence.",
        _BROWSER_HISTORY_REWRITE_REQUEST + " Research the latest studies for additional evidence.",
        "Rewrite the section preserving exactly five tasks. "
        "Do not change other sections, but add sources.",
    ],
)
def test_positive_evidence_requests_keep_separate_grounding_scope(prompt: str) -> None:
    """New evidence may legitimately contain values absent from the old paragraph."""

    assert _wording_only_rewrite_request(prompt) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "Create a section with three examples, preserving the existing facts.",
        "Rewrite the paragraph with a new hypothetical worked example.",
        "Explain how to rewrite the paragraph while preserving its meaning.",
        "Do not rewrite the paragraph. Preserve its facts.",
        "Rewrite the paragraph; do not preserve the old counts.",
        "Rewrite the paragraph; do not preserve exactly five tasks.",
    ],
)
def test_fidelity_scope_requires_an_explicit_fact_preserving_rewrite(prompt: str) -> None:
    """Generation, explanation and forbidden edits are not wording-only proposals."""

    assert _wording_only_rewrite_request(prompt) is False
