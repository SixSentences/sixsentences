"""Surveys, screening frameworks and consented research workflows."""

import base64
import io
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from participant_fixtures import PARTICIPANT_INFORMATION
from PIL import Image
from sqlalchemy import select

from sixsentences_server.agent.ledger import (
    agent_turn_request_hash,
    claim_agent_turn,
    reserve_agent_turn,
)
from sixsentences_server.api.app import create_app
from sixsentences_server.chat.service import _requested_reader_work_id
from sixsentences_server.core.db import (
    AgentEventRow,
    AgentTurnRow,
    CapacityReservationRow,
    ChatMessageRow,
    CreditEventRow,
    DocumentRow,
    Org,
    Run,
    SurveyMessageRow,
    SurveyRow,
    WorkRow,
    db_session,
)
from sixsentences_server.core.entitlements import (
    begin_ai_action,
    finish_ai_action,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.surveys.service import SurveyAgentTurn, normalize_questions


def _authed(app: FastAPI, email: str, org: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={"name": "Researcher", "email": email, "password": "StrongPass123!", "org_name": org},
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def test_choice_option_strings_are_split_into_options_not_characters() -> None:
    questions = normalize_questions(
        [
            {
                "id": "industry",
                "title": "Where is AI used?",
                "type": "single_choice",
                "options": "Production & manufacturing, Administration; Research",
            },
            {
                "id": "adoption",
                "title": "Do you use AI?",
                "type": "single_choice",
                "options": "Yes\nNo",
            },
        ]
    )
    assert questions[0]["options"] == ["Production & manufacturing", "Administration", "Research"]
    assert questions[1]["options"] == ["Yes", "No"]


def test_legacy_character_options_are_repaired_without_merging_real_short_choices() -> None:
    questions = normalize_questions(
        [
            {
                "id": "legacy",
                "title": "Do you use AI?",
                "type": "single_choice",
                "options": list("Yes,No"),
            },
            {
                "id": "grade",
                "title": "Select a grade",
                "type": "single_choice",
                "options": ["A", "B", "C"],
            },
        ]
    )
    assert questions[0]["options"] == ["Yes", "No"]
    assert questions[1]["options"] == ["A", "B", "C"]


def test_survey_hosting_response_summary_and_export(settings) -> None:
    client = _authed(create_app(), "survey@example.org", "Survey Lab")
    created = client.post(
        "/surveys",
        json={
            "title": "Research workflow survey",
            "questions": [
                {
                    "id": "role",
                    "title": "Your role",
                    "type": "single_choice",
                    "required": True,
                    "options": ["Student", "Researcher"],
                },
                {
                    "id": "score",
                    "title": "Workflow fit",
                    "type": "scale",
                    "required": True,
                    "min": 1,
                    "max": 5,
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    survey_id = created.json()["public_id"]
    assert len(survey_id) == 26
    assert client.get(f"/public/surveys/{survey_id}").status_code == 404
    assert (
        client.patch(
            f"/surveys/{survey_id}",
            json={"status": "live", "participant_information": PARTICIPANT_INFORMATION},
        ).status_code
        == 200
    )
    public = TestClient(client.app)
    response = public.post(
        f"/public/surveys/{survey_id}/responses",
        json={
            "answers": {"role": "Researcher", "score": 5},
            "consent": True,
            "participant_information_fingerprint": public.get(
                f"/public/surveys/{survey_id}"
            ).json()["participant_information_fingerprint"],
        },
    )
    assert response.status_code == 201, response.text
    result = client.get(f"/surveys/{survey_id}").json()
    assert result["response_count"] == 1
    assert result["summary"]["completion_percent"] == 100.0
    assert result["summary"]["questions"][0]["counts"][1]["count"] == 1
    exported = client.get(f"/surveys/{survey_id}/export.csv")
    assert exported.status_code == 200
    assert "Researcher" in exported.text


def test_protected_survey_imports_versioned_results_into_writer(settings) -> None:
    app = create_app()
    client = _authed(app, "protected-survey@example.org", "Protected Survey Lab")
    created = client.post(
        "/surveys",
        json={
            "title": "Research practice pulse",
            "questions": [
                {
                    "id": "role",
                    "title": "Your role",
                    "type": "single_choice",
                    "required": True,
                    "options": ["Student", "Researcher"],
                },
                {"id": "comment", "title": "Comment", "type": "long_text"},
            ],
        },
    ).json()
    survey_id = created["public_id"]
    protected = client.put(f"/surveys/{survey_id}/password", json={"password": "sample-pass-123"})
    assert protected.status_code == 200, protected.text
    assert protected.json()["settings"]["password_protected"] is True
    assert "password_hash" not in protected.text
    client.patch(
        f"/surveys/{survey_id}",
        json={"status": "live", "participant_information": PARTICIPANT_INFORMATION},
    )
    public = TestClient(app)
    locked = public.get(f"/public/surveys/{survey_id}").json()
    assert locked["password_protected"] is True
    assert locked["questions"] == []
    assert (
        public.post(
            f"/public/surveys/{survey_id}/access", json={"password": "wrong-password"}
        ).status_code
        == 401
    )
    unlocked = public.post(
        f"/public/surveys/{survey_id}/access", json={"password": "sample-pass-123"}
    )
    assert unlocked.status_code == 200
    assert len(unlocked.json()["questions"]) == 2
    assert (
        public.post(
            f"/public/surveys/{survey_id}/responses",
            json={
                "answers": {"role": "Researcher", "comment": "Useful"},
                "password": "wrong-password",
            },
        ).status_code
        == 401
    )
    submitted = public.post(
        f"/public/surveys/{survey_id}/responses",
        json={
            "answers": {"role": "Researcher", "comment": "Useful"},
            "password": "sample-pass-123",
            "consent": True,
            "participant_information_fingerprint": unlocked.json()[
                "participant_information_fingerprint"
            ],
        },
    )
    assert submitted.status_code == 201, submitted.text
    writer = client.post(
        "/writer", json={"title": "Survey manuscript", "template": "article"}
    ).json()
    imported = client.post(f"/surveys/{survey_id}/dataset", json={"writer_id": writer["public_id"]})
    assert imported.status_code == 201, imported.text
    payload = imported.json()
    assert payload["version"] == 1
    assert payload["writer_linked"] is True
    assert payload["dataset"]["row_count"] == 1
    dataset_id = payload["dataset"]["public_id"]
    assert dataset_id in client.get(f"/writer/{writer['public_id']}").json()["dataset_ids"]
    assert client.get(f"/surveys/{survey_id}").json()["settings"]["result_dataset_id"] == dataset_id
    public.post(
        f"/public/surveys/{survey_id}/responses",
        json={
            "answers": {"role": "Student", "comment": "Needs examples"},
            "password": "sample-pass-123",
            "consent": True,
            "participant_information_fingerprint": unlocked.json()[
                "participant_information_fingerprint"
            ],
        },
    )
    refreshed = client.post(
        f"/surveys/{survey_id}/dataset", json={"writer_id": writer["public_id"]}
    ).json()
    assert refreshed["dataset"]["public_id"] == dataset_id
    assert refreshed["dataset"]["row_count"] == 2
    assert refreshed["version"] == 2
    assert len(client.get("/datasets").json()) == 1
    removed = client.delete(f"/surveys/{survey_id}/password")
    assert removed.status_code == 200
    assert removed.json()["settings"]["password_protected"] is False


def test_screening_frameworks_and_later_turn_paper_context(settings) -> None:
    app = create_app()
    client = TestClient(app)
    methods = client.get("/screening-methods").json()
    assert {method["id"] for method in methods} == {
        "prisma",
        "cochrane",
        "jbi",
        "campbell",
        "kitchenham",
    }
    researcher = _authed(app, "context@example.org", "Context Lab")
    me = researcher.get("/auth/me").json()
    work = WorkRecord(
        id="W4242",
        title="Agentic review workflows",
        abstract="A workflow for grounded literature review agents.",
        year=2026,
        source="fixture",
    )
    with db_session() as session:
        run = Run(
            org_id=me["org_id"],
            question="Explain agentic review workflows",
            status="completed",
            config={"mode": "ask"},
        )
        session.add_all(
            [
                run,
                WorkRow(
                    id=work.id,
                    title=work.title,
                    year=work.year,
                    payload=work.model_dump(mode="json"),
                ),
            ]
        )
        session.flush()
        session.add(
            ChatMessageRow(
                org_id=me["org_id"],
                run_id=run.id,
                role="assistant",
                content="This paper is relevant.",
                citations=[work.id],
            )
        )
        session.flush()
        assert (
            _requested_reader_work_id(
                session, run, "Mark the most important passages in the paper", []
            )
            == work.id
        )


def test_paper_figure_keeps_downloadable_source_provenance(settings) -> None:
    app = create_app()
    client = _authed(app, "figures@example.org", "Figure Lab")
    me = client.get("/auth/me").json()
    work = WorkRecord(
        id="W8080",
        title="Inspectable research figures",
        doi="10.1234/example",
        year=2026,
        source="fixture",
    )
    with db_session() as session:
        run = Run(
            org_id=me["org_id"],
            question="Show the main result",
            status="completed",
            config={"mode": "ask"},
        )
        work_row = WorkRow(
            id=work.id,
            doi=work.doi,
            title=work.title,
            year=work.year,
            payload=work.model_dump(mode="json"),
        )
        session.add_all([run, work_row])
        session.flush()
        document = DocumentRow(
            org_id=me["org_id"],
            run_id=run.id,
            work_id=work.id,
            status="retrieved",
            source="arxiv",
            legal_basis="oa_green",
            license="CC BY 4.0",
            url="https://arxiv.org/pdf/2601.00001",
            checksum="figure-source-pdf",
            byte_size=1024,
            text_status="parsed",
        )
        session.add(document)
        session.flush()
        document_id = document.id
    buffer = io.BytesIO()
    Image.new("RGB", (120, 90), "white").save(buffer, format="PNG")
    created = client.post(
        f"/documents/{document_id}/figures",
        json={
            "page": 4,
            "caption": "Model accuracy across the evaluation sets",
            "source_label": work.title,
            "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )
    assert created.status_code == 201, created.text
    figure = created.json()
    assert figure["config"]["source"]["paper_title"] == work.title
    assert figure["config"]["source"]["page"] == 4
    assert figure["config"]["source"]["doi"] == work.doi
    source = client.get(f"/figures/{figure['id']}/source")
    assert source.status_code == 200
    assert "Page: 4" in source.text
    assert "DOI: 10.1234/example" in source.text
    oa_work = WorkRecord(
        id="W8081",
        title="Payload-only provenance",
        year=2026,
        source="fixture",
        oa_url="https://example.org/read/W8081",
    )
    with db_session() as session:
        run_id_value = session.scalar(select(Run.id))
        session.add(
            WorkRow(
                id=oa_work.id,
                doi=None,
                title=oa_work.title,
                year=oa_work.year,
                payload=oa_work.model_dump(mode="json"),
            )
        )
        session.flush()
        bare = DocumentRow(
            org_id=me["org_id"],
            run_id=run_id_value,
            work_id=oa_work.id,
            status="retrieved",
            source="fixture",
            legal_basis="oa_green",
            license="CC BY 4.0",
            url="",
            checksum="figure-source-pdf-no-url",
            byte_size=1024,
            text_status="parsed",
        )
        session.add(bare)
        session.flush()
        bare_id = bare.id
    bare_created = client.post(
        f"/documents/{bare_id}/figures",
        json={"page": 1, "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii")},
    )
    assert bare_created.status_code == 201, bare_created.text
    assert bare_created.json()["config"]["source"]["url"] == "https://example.org/read/W8081"


def test_survey_agent_edits_and_reorders_questions(settings, monkeypatch) -> None:
    from sixsentences_server.llm.mock import mock_pool

    planned = {
        "answer": "I tightened the wording and put the new screener first.",
        "actions": [
            {"operation": "set_title", "value": "Research workflow pulse"},
            {
                "operation": "add_question",
                "position": 0,
                "question": {
                    "title": "Do you currently conduct research?",
                    "type": "single_choice",
                    "required": True,
                    "options": ["Yes", "No"],
                },
            },
            {
                "operation": "update_question",
                "question_id": "q1",
                "changes": {"title": "Which part of research takes the most time?"},
            },
            {"operation": "reorder_questions", "question_ids": ["q3", "q2", "q1"]},
        ],
    }
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(lambda model, prompt: json.dumps(planned)),
    )
    client = _authed(create_app(), "survey-agent@example.org", "Agent Survey Lab")
    created = client.post(
        "/surveys",
        json={
            "title": "Workflow survey",
            "questions": [
                {"id": "q1", "title": "What takes time?", "type": "long_text"},
                {"id": "q2", "title": "Your role", "type": "short_text"},
            ],
        },
    ).json()
    turn = client.post(
        f"/surveys/{created['public_id']}/chat",
        json={"question": "Improve the wording and add a screener at the beginning."},
    )
    assert turn.status_code == 200, turn.text
    payload = turn.json()
    assert payload["survey"]["title"] == "Research workflow pulse"
    assert [question["id"] for question in payload["survey"]["questions"]] == ["q3", "q2", "q1"]
    assert payload["survey"]["questions"][2]["title"].startswith("Which part")
    assert all(action["applied"] for action in payload["actions"])
    assert payload["actions"][0]["before"] == "Workflow survey"
    assert payload["actions"][0]["after"] == "Research workflow pulse"
    assert payload["actions"][1]["after"]["title"] == "Do you currently conduct research?"
    assert payload["actions"][2]["before"]["title"] == "What takes time?"
    assert payload["actions"][2]["after"]["title"] == "Which part of research takes the most time?"
    assert payload["actions"][3]["before"] == ["q3", "q1", "q2"]
    assert payload["actions"][3]["after"] == ["q3", "q2", "q1"]
    history = client.get(f"/surveys/{created['public_id']}/chat").json()
    assert history[-1]["payload"]["survey_updated"] is True
    assert history[-1]["payload"]["actions"][0]["operation"] == "set_title"


def test_stale_specialist_worker_cannot_mutate_charge_or_complete(settings, monkeypatch) -> None:
    """A replacement turn fences an already-returning mutating worker."""
    from sixsentences_server.llm.mock import mock_pool

    provider_started = threading.Event()
    release_provider = threading.Event()

    def delayed_agent(*args, **kwargs) -> SurveyAgentTurn:
        provider_started.set()
        assert release_provider.wait(timeout=3), "test did not release provider"
        return SurveyAgentTurn(
            answer="I renamed the survey.",
            actions=[{"operation": "set_title", "value": "Stale mutation"}],
        )

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(lambda model, prompt: "{}"),
    )
    monkeypatch.setattr("sixsentences_server.api.app.run_survey_agent", delayed_agent)
    monkeypatch.setattr("sixsentences_server.api.app._AGENT_TURN_HEARTBEAT_SECONDS", 60.0)
    client = _authed(create_app(), "survey-fence@example.org", "Survey Fence Lab")
    survey = client.post(
        "/surveys",
        json={
            "title": "Original title",
            "questions": [{"id": "q1", "title": "Question", "type": "short_text"}],
        },
    ).json()
    survey_id = str(survey["public_id"])
    turn_id = "survey_stale_worker_1234"
    response_holder: dict[str, Any] = {}

    def request_turn() -> None:
        response_holder["response"] = client.post(
            f"/surveys/{survey_id}/chat/stream",
            json={"turn_id": turn_id, "question": "Rename this survey", "auto_apply": True},
        )

    request_thread = threading.Thread(target=request_turn)
    request_thread.start()
    assert provider_started.wait(timeout=3)
    with db_session() as session:
        stale = session.scalar(select(AgentTurnRow).where(AgentTurnRow.turn_id == turn_id))
        assert stale is not None
        stale.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
        org_id = stale.org_id
        user_id = stale.user_id
        action = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.resource_type == "survey",
                CapacityReservationRow.resource_id == str(survey["id"]),
                CapacityReservationRow.action == "survey_chat",
            )
        )
        assert action is not None and action.status == "active"
        assert action.remaining_credits > 0
    with db_session() as session:
        replacement = reserve_agent_turn(
            session,
            org_id=org_id,
            user_id=user_id,
            resource_kind="survey",
            resource_id=survey_id,
            turn_id="survey_replacement_1234",
            request_hash=agent_turn_request_hash({"question": "Continue safely"}),
        )
        claim_agent_turn(session, replacement.row_id)
        stale_action_status = session.scalar(
            select(CapacityReservationRow.status).where(
                CapacityReservationRow.resource_type == "survey",
                CapacityReservationRow.resource_id == str(survey["id"]),
                CapacityReservationRow.action == "survey_chat",
            )
        )
        assert stale_action_status == "expired"
    release_provider.set()
    request_thread.join(timeout=3)
    assert not request_thread.is_alive()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with db_session() as session:
            action_statuses = session.scalars(
                select(CapacityReservationRow.status).where(
                    CapacityReservationRow.resource_type == "survey",
                    CapacityReservationRow.resource_id == str(survey["id"]),
                    CapacityReservationRow.action == "survey_chat",
                )
            ).all()
        if action_statuses == ["expired"]:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("stale worker action budget was not cancelled")
    with db_session() as session:
        stored_survey = session.scalar(select(SurveyRow).where(SurveyRow.public_id == survey_id))
        assert stored_survey is not None
        messages = session.scalars(
            select(SurveyMessageRow)
            .where(SurveyMessageRow.survey_id == stored_survey.id)
            .order_by(SurveyMessageRow.id)
        ).all()
        completed_events = session.scalar(
            select(AgentTurnRow).where(AgentTurnRow.turn_id == turn_id)
        )
        question_charges = session.scalars(
            select(CreditEventRow).where(
                CreditEventRow.org_id == org_id, CreditEventRow.action == "question"
            )
        ).all()
    assert stored_survey.title == "Original title"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].content == "This agent task could not be completed."
    assert completed_events is not None and completed_events.status == "failed"
    assert completed_events.result is not None
    assert "message_id" not in completed_events.result
    assert question_charges == []


def test_direct_survey_chat_blocks_reset_during_main_and_review_actions(
    settings, monkeypatch
) -> None:
    """Legacy synchronous POSTs share reset's resource/capacity guard."""
    from sixsentences_server.llm.mock import mock_pool

    provider_started = threading.Event()
    release_provider = threading.Event()

    def delayed_agent(*args, **kwargs) -> SurveyAgentTurn:
        provider_started.set()
        assert release_provider.wait(timeout=3), "test did not release provider"
        return SurveyAgentTurn(answer="The survey is unchanged.", actions=[])

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(lambda model, prompt: "{}"),
    )
    monkeypatch.setattr("sixsentences_server.api.app.run_survey_agent", delayed_agent)
    app = create_app()
    client = _authed(app, "survey-reset-race@example.org", "Survey Reset Race")
    survey = client.post(
        "/surveys",
        json={
            "title": "Reset race survey",
            "questions": [{"id": "q1", "title": "Question", "type": "short_text"}],
        },
    ).json()
    survey_id = str(survey["public_id"])
    response_holder: dict[str, Any] = {}
    turn_request = {
        "turn_id": "survey_direct_reset_race",
        "question": "Summarize the current survey",
    }

    def request_turn() -> None:
        worker = TestClient(app)
        worker.headers.update(client.headers)
        response_holder["response"] = worker.post(f"/surveys/{survey_id}/chat", json=turn_request)

    request_thread = threading.Thread(target=request_turn)
    request_thread.start()
    assert provider_started.wait(timeout=3)
    refused_main = client.delete(f"/surveys/{survey_id}/chat")
    assert refused_main.status_code == 409
    assert "still running" in refused_main.json()["detail"]
    with db_session() as session:
        main_action = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.resource_type == "survey",
                CapacityReservationRow.resource_id == str(survey["id"]),
                CapacityReservationRow.action == "survey_chat",
            )
        )
        assert main_action is not None and main_action.status == "active"
    release_provider.set()
    request_thread.join(timeout=3)
    assert not request_thread.is_alive()
    response = response_holder["response"]
    assert response.status_code == 200
    replay = client.post(f"/surveys/{survey_id}/chat", json=turn_request)
    assert replay.status_code == 200
    assert replay.json() == response.json()
    with db_session() as session:
        completed_turn = session.scalar(
            select(AgentTurnRow).where(AgentTurnRow.turn_id == turn_request["turn_id"])
        )
        assert completed_turn is not None
        terminal_events = session.scalars(
            select(AgentEventRow.event)
            .where(AgentEventRow.turn_id == completed_turn.id)
            .order_by(AgentEventRow.sequence)
        ).all()
        assert completed_turn.status == "completed"
        assert terminal_events == ["turn.started", "turn.completed"]
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        review = begin_ai_action(
            session,
            org,
            action="survey_chat_result_review",
            resource_type="survey",
            resource_id=survey["id"],
            action_id="survey-direct-reset-review",
        )
        review_action_id = review.action_id
    refused_review = client.delete(f"/surveys/{survey_id}/chat")
    assert refused_review.status_code == 409
    with db_session() as session:
        finish_ai_action(session, review_action_id)
    cleared = client.delete(f"/surveys/{survey_id}/chat")
    assert cleared.status_code == 200
    assert client.get(f"/surveys/{survey_id}/chat").json() == []


def test_survey_agent_protects_response_schema(settings, monkeypatch) -> None:
    from sixsentences_server.llm.mock import mock_pool

    planned = {
        "answer": "I will remove the question.",
        "actions": [{"operation": "delete_question", "question_id": "q1"}],
    }
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(lambda model, prompt: json.dumps(planned)),
    )
    client = _authed(create_app(), "survey-safety@example.org", "Safe Survey Lab")
    created = client.post(
        "/surveys",
        json={
            "title": "Live survey",
            "questions": [
                {"id": "q1", "title": "Role", "type": "short_text"},
                {"id": "q2", "title": "Comment", "type": "long_text"},
            ],
        },
    ).json()
    survey_id = created["public_id"]
    client.patch(
        f"/surveys/{survey_id}",
        json={"status": "live", "participant_information": PARTICIPANT_INFORMATION},
    )
    TestClient(client.app).post(
        f"/public/surveys/{survey_id}/responses",
        json={
            "answers": {"q1": "Researcher", "q2": "Useful"},
            "consent": True,
            "participant_information_fingerprint": TestClient(client.app)
            .get(f"/public/surveys/{survey_id}")
            .json()["participant_information_fingerprint"],
        },
    )
    turn = client.post(
        f"/surveys/{survey_id}/chat", json={"question": "Remove the first question."}
    ).json()
    assert len(turn["survey"]["questions"]) == 2
    assert turn["actions"][0]["applied"] is False
    assert "explicit request" in turn["actions"][0]["detail"]


def test_survey_agent_stages_proposal_until_applied(settings, monkeypatch) -> None:
    from sixsentences_server.llm.mock import mock_pool

    planned = {
        "answer": "I would tighten the wording and rename the survey.",
        "actions": [
            {"operation": "set_title", "value": "Research workflow pulse"},
            {
                "operation": "update_question",
                "question_id": "q1",
                "changes": {"title": "Which part of research takes the most time?"},
            },
        ],
    }
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(lambda model, prompt: json.dumps(planned)),
    )
    client = _authed(create_app(), "survey-proposal@example.org", "Proposal Lab")
    created = client.post(
        "/surveys",
        json={
            "title": "Workflow survey",
            "questions": [{"id": "q1", "title": "What takes time?", "type": "long_text"}],
        },
    ).json()
    survey_id = created["public_id"]
    turn = client.post(
        f"/surveys/{survey_id}/chat", json={"question": "Improve the wording.", "auto_apply": False}
    )
    assert turn.status_code == 200, turn.text
    payload = turn.json()
    assert payload["survey"]["title"] == "Workflow survey"
    assert all(action["applied"] for action in payload["actions"])
    history = client.get(f"/surveys/{survey_id}/chat").json()
    proposal = history[-1]
    assert proposal["payload"]["proposal_status"] == "pending"
    assert proposal["payload"]["survey_updated"] is False
    assert proposal["payload"]["proposed_actions"][0]["operation"] == "set_title"
    applied = client.post(f"/surveys/{survey_id}/chat/{proposal['id']}/apply")
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["survey_updated"] is True
    assert result["survey"]["title"] == "Research workflow pulse"
    assert result["survey"]["questions"][0]["title"].startswith("Which part")
    assert client.get(f"/surveys/{survey_id}").json()["title"] == "Research workflow pulse"
    resolved = client.get(f"/surveys/{survey_id}/chat").json()[-1]
    assert resolved["payload"]["proposal_status"] == "applied"
    assert resolved["payload"]["survey_updated"] is True
    again = client.post(f"/surveys/{survey_id}/chat/{proposal['id']}/apply")
    assert again.status_code == 409


def test_survey_agent_repairs_vague_wording_edits_without_schema_drift(
    settings, monkeypatch
) -> None:
    from sixsentences_server.llm.mock import mock_pool

    prompts: list[str] = []

    def respond(model: str, prompt: str) -> str:
        prompts.append(prompt)
        if "Rewrite only the awkward wording" not in prompt:
            return json.dumps(
                {"answer": "The wording could be clearer.", "actions": [], "workspace_actions": []}
            )
        return json.dumps(
            {
                "answer": "Ich habe die Formulierungen verständlicher gemacht.",
                "actions": [
                    {
                        "operation": "update_question",
                        "question_id": "q1",
                        "changes": {
                            "title": "Stimmst du der Teilnahme an dieser Umfrage zu?",
                            "type": "long_text",
                            "options": ["Vielleicht"],
                            "required": False,
                        },
                    },
                    {
                        "operation": "update_question",
                        "question_id": "q2",
                        "changes": {
                            "title": "Was war für dich beim Onboarding schwierig?",
                            "type": "single_choice",
                        },
                    },
                ],
                "workspace_actions": [],
            }
        )

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(respond)
    )
    client = _authed(create_app(), "survey-wording@example.org", "Survey Wording Lab")
    created = client.post(
        "/surveys",
        json={
            "title": "Onboarding",
            "questions": [
                {
                    "id": "q1",
                    "title": "Einwilligung",
                    "type": "single_choice",
                    "required": True,
                    "options": ["Ja", "Nein"],
                },
                {
                    "id": "q2",
                    "title": "Was war beim Onboarding schwierig?",
                    "type": "long_text",
                    "required": False,
                },
            ],
        },
    ).json()
    turn = client.post(
        f"/surveys/{created['public_id']}/chat",
        json={
            "question": "mach die fragen weniger komisch für studenten aber behalt bedeutung und typen",
            "auto_apply": True,
        },
    )
    assert turn.status_code == 200, turn.text
    questions = turn.json()["survey"]["questions"]
    assert len(prompts) == 4
    assert any("Already validated actions" in prompt for prompt in prompts)
    assert "Validated execution results" in prompts[-1]
    assert [question["id"] for question in questions] == ["q1", "q2"]
    assert [question["type"] for question in questions] == ["single_choice", "long_text"]
    assert questions[0]["options"] == ["Ja", "Nein"]
    assert questions[0]["required"] is True
    assert questions[0]["title"].startswith("Stimmst du")
    assert questions[1]["title"].startswith("Was war für dich")


def test_survey_agent_preserves_mixed_language_choice_addition(settings, monkeypatch) -> None:
    from sixsentences_server.llm.mock import mock_pool

    planned = {
        "answer": "I can add that question.",
        "actions": [
            {
                "operation": "add_question",
                "question": {
                    "title": "In welcher Studienphase befindest du dich?",
                    "type": "single_choice",
                    "options": ["Bachelor", "Master", "PhD"],
                },
            }
        ],
        "workspace_actions": [],
    }
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(lambda model, prompt: json.dumps(planned)),
    )
    client = _authed(create_app(), "survey-choice@example.org", "Survey Choice Lab")
    created = client.post(
        "/surveys",
        json={
            "title": "Study survey",
            "questions": [
                {
                    "id": "q1",
                    "title": "Einwilligung",
                    "type": "single_choice",
                    "options": ["Ja", "Nein"],
                }
            ],
        },
    ).json()
    turn = client.post(
        f"/surveys/{created['public_id']}/chat",
        json={
            "question": "add a single choice Frage zur Studienphase mit Bachelor Master PhD und Other, auf deutsch formulieren",
            "auto_apply": True,
        },
    )
    assert turn.status_code == 200, turn.text
    questions = turn.json()["survey"]["questions"]
    assert len(questions) == 2
    assert questions[1]["type"] == "single_choice"
    assert questions[1]["options"] == ["Bachelor", "Master", "PhD", "Other"]
    assert "Studienphase" in questions[1]["title"]
