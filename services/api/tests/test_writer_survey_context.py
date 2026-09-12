"""Survey instruments and responses linked to the manuscript assistant."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from participant_fixtures import PARTICIPANT_INFORMATION

from sixsentences_server.api.app import create_app
from sixsentences_server.core.db import Org, db_session
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus


def _authed(app: FastAPI) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": "owner@example.org",
            "password": "StrongPass123!",
            "org_name": "Acme",
            "name": "Test",
        },
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _grant_scholar(client: TestClient) -> None:
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"


def test_writer_links_survey_instrument_summary_and_response_rows(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app()
    client = _authed(app)
    _grant_scholar(client)
    survey = client.post(
        "/surveys",
        json={
            "title": "Onboarding pulse",
            "description": "A cross-sectional pulse after the first month.",
            "questions": [
                {
                    "id": "q_confidence",
                    "title": "How confident are you?",
                    "type": "scale",
                    "required": True,
                    "min": 1,
                    "max": 7,
                },
                {
                    "id": "q_support",
                    "title": "Which support helped most?",
                    "type": "single_choice",
                    "required": True,
                    "options": ["Mentoring", "Documentation"],
                },
                {
                    "id": "q_detail",
                    "title": "What made onboarding difficult?",
                    "type": "long_text",
                    "required": False,
                },
            ],
        },
    ).json()
    published = client.patch(
        f"/surveys/{survey['public_id']}",
        json={"status": "live", "participant_information": PARTICIPANT_INFORMATION},
    )
    assert published.status_code == 200, published.text
    participant = TestClient(app)
    public = participant.get(f"/public/surveys/{survey['public_id']}")
    assert public.status_code == 200, public.text
    participant_information_fingerprint = public.json()["participant_information_fingerprint"]
    for confidence, support, detail in (
        (6, "Mentoring", "Unclear ownership created friction in the first week."),
        (4, "Documentation", "The setup instructions were incomplete."),
        (5, "Mentoring", "Scheduling across time zones was difficult."),
    ):
        response = participant.post(
            f"/public/surveys/{survey['public_id']}/responses",
            json={
                "answers": {"q_confidence": confidence, "q_support": support, "q_detail": detail},
                "respondent_label": "private@example.org",
                "consent": True,
                "participant_information_fingerprint": participant_information_fingerprint,
            },
        )
        assert response.status_code == 201, response.text
    doc = client.post("/writer", json={"title": "Survey findings", "template": "blank"}).json()
    linked = client.put(
        f"/writer/{doc['public_id']}/survey-contexts",
        json={"sources": [{"survey_id": survey["public_id"], "mode": "summary"}]},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked_count"] == 1
    assert linked.json()["summary_count"] == 1
    prompts: list[str] = []

    class FakePool:
        def __init__(self) -> None:
            self.read_source = False

        def has_strong(self) -> bool:
            return True

        def pinned(self, ref: object) -> "FakePool":
            return self

        def complete(
            self, task: object, *, system: str, prompt: str, max_tokens: int
        ) -> SimpleNamespace:
            prompts.append(prompt)
            if not self.read_source:
                self.read_source = True
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "action": "tool",
                            "update": "I am reading the linked survey evidence.",
                            "tool": "read_source",
                            "arguments": {"handle": f"survey:{survey['public_id']}"},
                        }
                    ),
                    provider="fake",
                    model="fake",
                )
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "action": "finish",
                        "update": "I inspected the linked survey evidence.",
                        "final": {
                            "reply": "The survey can be reported with its exact instrument.",
                            "edits": [],
                            "visual_request": None,
                            "workspace_actions": [],
                        },
                    }
                ),
                provider="fake",
                model="fake",
            )

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    response = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={"message": "Describe the instrument and the confidence result."},
    )
    assert response.status_code == 200, response.text
    prompt = prompts[-1]
    assert "QUESTIONNAIRE IN PRESENTED ORDER" in prompt
    assert "How confident are you?" in prompt
    assert "range: 1 to 7" in prompt
    assert "Submitted responses: 3" in prompt
    assert "mean=5.0" in prompt
    assert "Unclear ownership created friction" not in prompt
    assert response.json()["survey_context"][0]["question_count"] == 3
    assert response.json()["survey_context"][0]["retrieved_response_ids"] == []
    linked_rows = client.put(
        f"/writer/{doc['public_id']}/survey-contexts",
        json={"sources": [{"survey_id": survey["public_id"], "mode": "responses"}]},
    )
    assert linked_rows.status_code == 200, linked_rows.text
    response = client.post(
        f"/writer/{doc['public_id']}/chat",
        json={"message": "What exact response mentions ownership friction?"},
    )
    assert response.status_code == 200, response.text
    assert "Unclear ownership created friction in the first week." in prompts[-1]
    assert "private@example.org" not in prompts[-1]
    provenance = response.json()["survey_context"][0]
    assert provenance["mode"] == "responses"
    assert provenance["response_count"] == 3
    assert provenance["retrieved_response_ids"]
