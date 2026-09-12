"""Consent, scope and transcript-only public participation tests."""

from __future__ import annotations

import base64

from conftest import AppContext, bearer, register


def participant_information() -> dict[str, object]:
    return {
        "language": "en",
        "controller_name": "Example University",
        "controller_address": "Research Street 1, Example City",
        "contact_email": "research@example.invalid",
        "data_protection_contact": "privacy@example.invalid",
        "purpose": "Understand experiences with reproducible research workflows.",
        "data_categories": "Interview answers and session timing metadata.",
        "legal_basis": "consent",
        "legal_basis_details": "Voluntary, revocable participant consent.",
        "retention_period": "Thirty days after the study closes.",
        "recipients": "The named research team and configured speech processor.",
        "international_transfers": "No transfer except the disclosed processor configuration.",
        "supervisory_authority": "The authority named in the study's full notice.",
        "privacy_notice_url": "https://privacy.example.invalid/study",
        "impact_assessment_completed": True,
        "impact_assessment_reference": "Local assessment reference DPIA-0001",
        "prohibited_use_attested": True,
        "spoken_processing_approved": True,
        "researcher_reviewed": True,
    }


def test_public_survey_requires_reviewed_notice_and_explicit_consent(
    app_context: AppContext,
) -> None:
    client = app_context.client
    account = register(client, email="survey@example.invalid", organization="Survey Lab")
    headers = bearer(account["token"])
    survey = client.post(
        "/surveys",
        headers=headers,
        json={
            "title": "Workflow survey",
            "questions": [{"id": "q1", "label": "What helped?"}],
            "participant_information": participant_information(),
        },
    )
    assert survey.status_code == 201
    published = client.patch(
        f"/surveys/{survey.json()['id']}", headers=headers, json={"published": True}
    )
    assert published.status_code == 200, published.text
    token = published.json()["public_token"]
    public = client.get(f"/public/surveys/{token}")
    assert public.status_code == 200
    assert "public_token_hash" not in public.text

    refused = client.post(
        f"/public/surveys/{token}/responses",
        json={"accepted": False, "answers": {"q1": "Nothing"}},
    )
    assert refused.status_code == 422
    submitted = client.post(
        f"/public/surveys/{token}/responses",
        json={"accepted": True, "answers": {"q1": "A visible audit trail"}},
    )
    assert submitted.status_code == 201


def test_text_and_spoken_interviews_are_scope_bound_and_audio_is_not_stored(
    app_context: AppContext,
) -> None:
    client = app_context.client
    account = register(client, email="interview@example.invalid", organization="Interview Lab")
    headers = bearer(account["token"])
    study = client.post(
        "/voice/studies",
        headers=headers,
        json={
            "title": "Research workflow interview",
            "language": "en",
            "modalities": ["text", "voice"],
            "guide": {"questions": ["What makes a workflow auditable?"]},
            "participant_information": participant_information(),
            "max_session_minutes": 15,
            "max_turns": 10,
            "retention_days": 30,
            "store_audio": False,
            "provider_disclosure": (
                "Spoken turns are sent to the processor configured by this deployment solely "
                "for transcription; this service retains only the returned transcript."
            ),
        },
    )
    assert study.status_code == 201, study.text
    published = client.patch(
        f"/voice/studies/{study.json()['id']}",
        headers=headers,
        json={"published": True},
    )
    assert published.status_code == 200, published.text
    token = published.json()["public_token"]
    scope = published.json()["scope_fingerprint"]

    public = client.get(f"/public/interviews/{token}")
    assert public.status_code == 200
    assert public.json()["participant_notice"]["scope_fingerprint"] == scope
    assert public.json()["participant_notice"]["audio_stored"] is False

    text_session = client.post(
        f"/public/interviews/{token}/sessions",
        json={
            "modality": "text",
            "accepted": True,
            "adult_confirmed": False,
            "scope_fingerprint": scope,
        },
    )
    assert text_session.status_code == 201
    text_turn = client.post(
        f"/public/interviews/{token}/sessions/{text_session.json()['id']}/turns",
        json={"content": "I need to inspect every transformation."},
    )
    assert text_turn.status_code == 200
    assert text_turn.json()["assistant"]["role"] == "assistant"

    voice_session = client.post(
        f"/public/talk/{token}/sessions",
        json={
            "modality": "voice",
            "accepted": True,
            "adult_confirmed": True,
            "scope_fingerprint": scope,
        },
    )
    assert voice_session.status_code == 201
    raw_audio = b"RIFF-test-audio-that-must-not-be-stored"
    spoken_turn = client.post(
        f"/public/interviews/{token}/sessions/{voice_session.json()['id']}/audio",
        json={
            "audio_base64": base64.b64encode(raw_audio).decode("ascii"),
            "media_type": "audio/wav",
        },
    )
    assert spoken_turn.status_code == 200, spoken_turn.text
    assert app_context.speech.received == [(raw_audio, "audio/wav")]
    assert spoken_turn.json()["participant"]["content"] == "The participant's spoken answer."
    assert spoken_turn.json()["participant"]["audio_metadata"]["stored"] is False

    private = client.get(f"/interviews/{voice_session.json()['id']}", headers=headers)
    assert private.status_code == 200
    assert private.json()["turns"][0]["content"] == "The participant's spoken answer."
    assert base64.b64encode(raw_audio).decode("ascii") not in private.text

    changed = client.patch(
        f"/voice/studies/{study.json()['id']}",
        headers=headers,
        json={"title": "Materially changed interview"},
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "draft"
    assert client.get(f"/public/interviews/{token}").status_code == 404


def test_spoken_publication_fails_closed_without_required_review(
    app_context: AppContext,
) -> None:
    client = app_context.client
    account = register(client, email="review@example.invalid", organization="Review Lab")
    headers = bearer(account["token"])
    information = participant_information()
    information["impact_assessment_completed"] = False
    study = client.post(
        "/voice/studies",
        headers=headers,
        json={
            "title": "Unreviewed interview",
            "modalities": ["voice"],
            "guide": {},
            "participant_information": information,
            "provider_disclosure": "A sufficiently detailed processor notice for the participant.",
        },
    )
    assert study.status_code == 201
    published = client.patch(
        f"/voice/studies/{study.json()['id']}",
        headers=headers,
        json={"published": True},
    )
    assert published.status_code == 422
    assert "impact assessment" in published.json()["detail"]
