"""Prospective, mode-bound participant notices and immutable exact receipts."""

from copy import deepcopy
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from participant_fixtures import PARTICIPANT_INFORMATION
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.core import study_participation
from sixsentences_server.core.db import SurveyResponseRow, db_session
from sixsentences_server.core.study_participation import (
    notice_fingerprint,
    participant_information_gaps,
    participant_notice,
    participation_snapshot,
    voice_participant_information_gaps,
)


def test_information_completeness_does_not_invent_a_legal_basis() -> None:
    assert participant_information_gaps({})
    assert participant_information_gaps({**PARTICIPANT_INFORMATION, "legal_basis": ""})
    assert participant_information_gaps({**PARTICIPANT_INFORMATION, "researcher_reviewed": False})
    assert participant_information_gaps(
        {**PARTICIPANT_INFORMATION, "privacy_notice_url": "javascript:bad"}
    )
    assert participant_information_gaps(
        {**PARTICIPANT_INFORMATION, "privacy_notice_url": "https://[bad"}
    )
    assert participant_information_gaps(
        {**PARTICIPANT_INFORMATION, "contact_email": "x" * 65 + "@example.org"}
    )
    assert not participant_information_gaps(PARTICIPANT_INFORMATION)


def test_participant_information_requires_an_explicit_notice_language() -> None:
    """The language of a mandatory notice is a fact about the participants."""

    assert "participant information language" in participant_information_gaps(
        {**PARTICIPANT_INFORMATION, "language": ""}
    )
    for language in ("de", "en"):
        assert not participant_information_gaps({**PARTICIPANT_INFORMATION, "language": language})
        notice = participant_notice(
            {**PARTICIPANT_INFORMATION, "language": language}, mode="survey"
        )
        assert notice["language"] == language
    # A voice study states its own language, and that statement is part of the
    # scope its DPIA approval is bound to, so it counts as the stated language.
    assert not participant_information_gaps(
        {**PARTICIPANT_INFORMATION, "language": ""}, study_language="de"
    )


def test_ai_interview_requires_a_documented_dpia_decision() -> None:
    generic = {
        key: value
        for key, value in PARTICIPANT_INFORMATION.items()
        if key not in {"dpia_status", "dpia_reference", "ai_interview_scope_attested"}
    }
    assert not participant_information_gaps(generic)
    assert voice_participant_information_gaps(generic, expected_scope_fingerprint="a" * 64) == (
        "completed data protection impact assessment",
        "DPIA decision reference and reasoning",
        "AI interview prohibited-use attestation",
        "DPIA approval bound to the current study scope",
    )
    assert voice_participant_information_gaps(
        {**generic, "dpia_status": "completed", "dpia_reference": "too short"},
        expected_scope_fingerprint="a" * 64,
    ) == (
        "DPIA decision reference and reasoning",
        "AI interview prohibited-use attestation",
        "DPIA approval bound to the current study scope",
    )
    approved = {
        **PARTICIPANT_INFORMATION,
        "dpia_scope_fingerprint": "a" * 64,
        "dpia_public_scope_token": "c" * 32,
    }
    assert not voice_participant_information_gaps(approved, expected_scope_fingerprint="a" * 64)
    assert voice_participant_information_gaps(approved, expected_scope_fingerprint="b" * 64) == (
        "DPIA approval bound to the current study scope",
    )


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("mode", ["text", "live"])
def test_ai_interview_notice_discloses_analysis_and_prohibited_uses(
    language: str, mode: str
) -> None:
    notice = participant_notice(PARTICIPANT_INFORMATION, mode=mode, language=language)
    processing = next(
        section["body"]
        for section in notice["sections"]
        if section["title"] in {"Verarbeitung durch SixSentences", "Processing by SixSentences"}
    )
    expected = "biometrische Identifikation" if language == "de" else "biometric identification"
    assert expected in processing
    assert ("menschliche Prüfung" if language == "de" else "human review") in processing


@pytest.mark.parametrize("language", ["de", "en"])
def test_layered_live_notice_requires_operator_disclosure_and_audio_choice(language: str) -> None:
    notice = participant_notice(
        PARTICIPANT_INFORMATION, mode="live", language=language, recording=True
    )
    assert notice["requires_consent"] is True
    assert notice["audio_declaration"]
    assert len(notice["sections"]) >= 7
    complete = str(notice)
    expected = "Instanzbetreiber" if language == "de" else "instance operator"
    assert expected in complete
    assert ("Aufbewahrung" if language == "de" else "retention") in complete
    written = participant_notice(PARTICIPANT_INFORMATION, mode="text", language=language)
    assert written["audio_declaration"] == ""
    assert len(written["sections"]) == 6


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("mode", ["survey", "text", "live"])
def test_every_participant_mode_discloses_the_community_operator_boundary(
    language: str,
    mode: Literal["survey", "text", "live"],
) -> None:
    notice = participant_notice(PARTICIPANT_INFORMATION, mode=mode, language=language)
    processing = notice["sections"][4]["body"]
    assert notice["version"] == "2026-09-12.1"
    assert (
        "dafür ist das Forschungsteam verantwortlich"
        if language == "de"
        else "the research team is responsible"
    ) in processing
    assert ("Instanz" if language == "de" else "instance") in processing
    assert ("Datenschutzerklärung" if language == "de" else "privacy notice") in processing
    assert "Google" not in processing
    assert "OpenRouter" not in processing


def test_nonconsent_basis_is_not_presented_or_stored_as_consent() -> None:
    info = {**PARTICIPANT_INFORMATION, "legal_basis": "public_task"}
    notice = participant_notice(info, mode="survey")
    assert notice["requires_consent"] is False
    assert "not consent" in notice["declaration"]
    receipt = participation_snapshot({"notice": notice}, consent=False, mode="survey")
    assert receipt["participation_confirmed"] is True
    assert receipt["consent_given"] is False
    before = deepcopy(receipt)
    notice["core"][0] = "Changed later"
    assert receipt == before


def test_historical_provider_notice_receipt_is_not_relabelled() -> None:
    legacy = participant_notice(PARTICIPANT_INFORMATION, mode="text")
    legacy["version"] = "2026-09-04.1"
    legacy["sections"][4]["body"] = "Historical operator-controlled provider notice."
    receipt = participation_snapshot({"notice": legacy}, consent=True, mode="text")
    before = deepcopy(receipt)

    current = participant_notice(PARTICIPANT_INFORMATION, mode="text")
    assert current["version"] == "2026-09-12.1"
    assert notice_fingerprint({"notice": current}) != receipt["fingerprint"]
    assert receipt == before
    assert receipt["surface"]["notice"]["version"] == "2026-09-04.1"
    assert receipt["surface"]["notice"]["sections"][4]["body"] == (
        "Historical operator-controlled provider notice."
    )


@pytest.mark.parametrize("basis", ["consent", "public_task"])
def test_survey_information_gate_stale_forms_and_exact_receipt(  # type: ignore[no-untyped-def]
    settings,
    basis,
    monkeypatch,
) -> None:
    app = create_app()
    owner = TestClient(app)
    registration = owner.post(
        "/auth/register",
        json={
            "email": "study-owner@example.org",
            "password": "StrongPass123!",
            "name": "Researcher",
            "org_name": "Synthetic Lab",
        },
    )
    assert registration.status_code == 201, registration.text
    owner.headers["Authorization"] = f"Bearer {registration.json()['token']}"
    created = owner.post(
        "/surveys",
        json={
            "title": "Synthetic survey",
            "questions": [
                {"id": "q1", "title": "Your experience", "type": "short_text"},
            ],
        },
    ).json()
    url = f"/surveys/{created['public_id']}"
    public_url = f"/public{url}"
    assert owner.patch(url, json={"status": "live"}).status_code == 409
    assert owner.patch(url, json={"description": "Drafts stay editable"}).status_code == 200
    assert (
        owner.patch(
            url,
            json={
                "participant_information": {**PARTICIPANT_INFORMATION, "legal_basis": basis},
                "status": "live",
            },
        ).status_code
        == 200
    )
    public = TestClient(app)
    shown = public.get(public_url).json()
    body = {
        "answers": {"q1": "A useful workflow"},
        "participant_information_fingerprint": shown["participant_information_fingerprint"],
    }
    if basis == "consent":
        assert public.post(f"{public_url}/responses", json=body).status_code == 422
        body["consent"] = True
    assert public.post(f"{public_url}/responses", json=body).status_code == 201
    with db_session() as session:
        row = session.scalar(select(SurveyResponseRow))
        assert row is not None
        receipt = deepcopy(row.participant_information_snapshot)
    assert receipt["surface"]["notice"] == shown["participant_notice"]
    assert receipt["consent_given"] is (basis == "consent")
    assert receipt["fingerprint"] == notice_fingerprint(receipt["surface"])
    # A provider-notice revision invalidates a displayed form without changing
    # the exact words/version already accepted by an earlier participant.
    monkeypatch.setattr(study_participation, "PARTICIPANT_INFORMATION_VERSION", "2099-01-01.test")
    refreshed = public.get(public_url).json()
    assert refreshed["participant_notice"]["version"] == "2099-01-01.test"
    assert (
        refreshed["participant_information_fingerprint"]
        != body["participant_information_fingerprint"]
    )
    assert public.post(f"{public_url}/responses", json=body).status_code == 409
    with db_session() as session:
        rows = session.scalars(select(SurveyResponseRow)).all()
        assert len(rows) == 1 and rows[0].participant_information_snapshot == receipt
    changed = {**PARTICIPANT_INFORMATION, "purpose": "A different study purpose"}
    assert owner.patch(url, json={"participant_information": changed}).status_code == 200
    assert public.post(f"{public_url}/responses", json=body).status_code == 409
    with db_session() as session:
        row = session.scalar(select(SurveyResponseRow))
        assert row is not None
        assert row.participant_information_snapshot == receipt
    assert owner.patch(url, json={"participant_information": {}}).status_code == 200
    assert public.get(public_url).status_code == 409
    assert owner.get(url).json()["response_count"] == 1
