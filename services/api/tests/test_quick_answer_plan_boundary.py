"""Paid discovery must remain gated when queued work starts after a downgrade."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sixsentences_server.api.app import _require_public_chat_web_search_scope
from sixsentences_server.chat.service import explicit_web_research_request


@pytest.mark.parametrize(
    "question",
    [
        "Use the saved official web sources, without new research.",
        "Nutze die vorhandenen Webquellen, ohne neue Recherche.",
        "Explain https://developer.hashicorp.com/terraform/cli/commands/plan",
    ],
)
def test_existing_sources_do_not_require_fresh_web_discovery(question: str) -> None:
    assert explicit_web_research_request(question) is False


def test_new_official_source_discovery_still_requires_web_access() -> None:
    assert explicit_web_research_request("Search the web for official Terraform documentation")


@pytest.mark.parametrize(
    "question",
    [
        "Unser fiktiver Workshop findet online statt und umfasst fünf Teilnehmende. Bitte bestätige nur kurz.",
        "Unser Workshop findet online statt. Bitte bestätige nur kurz, keine Websuche.",
        "Our workshop takes place online. Remember BLUE ORCHID 42; no web search.",
        "Bitte keine Websuche zu Terraform.",
        "Bitte nicht im Internet suchen.",
        "Suche bitte nicht online nach Terraform.",
        "Do not browse the web for this answer.",
        "Don't look it up online.",
        "Do not check the official documentation; use the saved excerpt.",
        "Bitte keine Internetrecherche und keine Websuche.",
        "No further online research; summarize the existing material.",
        "Nutze die offizielle Dokumentation, ohne erneut zu suchen.",
        "Die Website ist blau. Unsere Dokumentation enthält fünf Abschnitte.",
        "I use Google for my work. Our vendor is listed in the documentation.",
        "Explain what web search means.",
    ],
)
def test_incidental_or_negated_web_words_do_not_grant_discovery(question: str) -> None:
    assert explicit_web_research_request(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "Schau auch mal noch im Internet nach.",
        "Suche dafür bitte im Web nach aktuellen Quellen.",
        "Look it up online too, especially in the official docs.",
        "Check the official vendor documentation.",
        "Do not edit the manuscript; search the web for official Terraform documentation.",
        "Bearbeite das Manuskript nicht, aber schau im Internet nach aktuellen Quellen.",
        "I do not know the budget. Look up public workshop prices online.",
        "Websuche zu Terraform",
        "Kannst du im Internet nach Terraform suchen?",
    ],
)
def test_positive_search_with_unrelated_negation_still_requires_approval(question: str) -> None:
    assert explicit_web_research_request(question) is True


def test_narrower_intent_does_not_weaken_the_exact_query_approval_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sixsentences_server.api.app.get_settings", lambda: SimpleNamespace(websearch_enabled=True)
    )
    incidental = "Our workshop takes place online. Please remember that."
    _require_public_chat_web_search_scope(incidental, False)
    with pytest.raises(HTTPException) as injected_query:
        _require_public_chat_web_search_scope(incidental, True, "public workshop")
    assert injected_query.value.status_code == 422
    explicit = "Do not edit the manuscript; search the web for Terraform documentation."
    with pytest.raises(HTTPException) as unconfirmed:
        _require_public_chat_web_search_scope(explicit, False, "Terraform documentation")
    assert unconfirmed.value.status_code == 422
    _require_public_chat_web_search_scope(explicit, True, "Terraform documentation")
