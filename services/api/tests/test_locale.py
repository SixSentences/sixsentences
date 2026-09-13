"""User-facing response language contracts."""

from sixsentences_server.core.locale import infer_response_language


def test_clear_german_followup_overrides_english_ui_preference() -> None:
    assert (
        infer_response_language(
            "fass es danach bitte erst ganz kurz für anfänger zusammen",
            "en",
        )
        == "de"
    )


def test_short_ambiguous_followup_retains_saved_preference() -> None:
    assert infer_response_language("continue", "de") == "de"


def test_explicit_language_instruction_wins_for_mixed_request() -> None:
    assert infer_response_language("Compare the papers und antworte auf deutsch", "en") == "de"


def test_german_web_followup_retains_german_with_an_english_interface() -> None:
    assert infer_response_language("SChau auch mal noch im Internet nach", "en") == "de"
    assert infer_response_language("Schau online nach, answer in English", "de") == "en"
    assert infer_response_language("Search the web for Terraform", "en") == "en"


def test_natural_specialist_questions_override_the_saved_ui_language() -> None:
    assert (
        infer_response_language(
            "Welche Aussage wurde genannt? Vergleiche die Stellen ohne etwas zu verändern.",
            "en",
        )
        == "de"
    )
    assert (
        infer_response_language(
            "Which statement was mentioned? Compare the passages without changing them.",
            "de",
        )
        == "en"
    )
