"""Regression tests for durable specialist-agent event ledgers."""

import asyncio
import json
import threading
import time
from typing import Any

from sqlalchemy import func, select

from sixsentences_server.agent.events import emit_agent_event
from sixsentences_server.api.app import (
    _AGENT_TURN_CANCEL_EVENTS,
    _normalize_specialist_artifacts,
    _persist_specialist_events,
    _specialist_chat_sse,
)
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    AgentEventRow,
    AgentTurnRow,
    DatasetMessageRow,
    InterviewMessageRow,
    InterviewRow,
    Org,
    ResearchDatasetRow,
    SurveyMessageRow,
    SurveyRow,
    User,
    VoiceStudyMessageRow,
    VoiceStudyRow,
    WriterDocumentRow,
    WriterMessageRow,
    db_session,
    init_db,
)
from sixsentences_server.llm.base import LLMCancelledError


def test_every_specialist_stream_persists_the_same_tool_event_contract(
    settings: Settings,
) -> None:
    """All five specialist UIs receive the same replayable call correlation."""

    init_db()
    with db_session() as session:
        org = Org(name="Normalized Specialist Tool Events")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="normalized-specialist-tools@example.test",
            password_hash="unused",
        )
        session.add(user)
        session.flush()
        org_id = org.id
        user_id = user.id

    async def collect(response: Any) -> list[dict[str, Any]]:
        bodies: list[dict[str, Any]] = []
        async for chunk in response.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            bodies.extend(
                json.loads(line.removeprefix("data: "))
                for line in text.splitlines()
                if line.startswith("data: ")
            )
        return bodies

    specialist_tools = {
        "manuscript": "manuscript.propose_edits",
        "dataset": "dataset.plan_operations",
        "interview": "interview.answer_from_transcript",
        "interview-study": "interview_study.propose_changes",
        "survey": "survey.propose_changes",
    }
    for index, (kind, tool) in enumerate(specialist_tools.items(), start=1):

        def worker(current_tool: str = tool) -> dict[str, Any]:
            emit_agent_event(
                "tool.started",
                tool=current_tool,
                label="Start specialist tool",
            )
            emit_agent_event(
                "tool.progress",
                tool=current_tool,
                label="Continue specialist tool",
            )
            emit_agent_event(
                "tool.completed",
                tool=current_tool,
                label="Specialist tool complete",
                result_count=1,
            )
            return {"answer": "Done"}

        response = _specialist_chat_sse(
            kind=kind,
            resource_id=f"normalized-{kind}-resource",
            org_id=org_id,
            user_id=user_id,
            turn_id=f"normalized_specialist_{index}",
            request_payload={"question": "Run the specialist"},
            last_event_id=0,
            worker=worker,
        )
        events = asyncio.run(collect(response))
        tool_events = [event for event in events if event["event"].startswith("tool.")]
        assert [event["lifecycle"] for event in tool_events] == [
            "started",
            "progress",
            "completed",
        ]
        assert {event["tool"] for event in tool_events} == {tool}
        assert len({event["call_id"] for event in tool_events}) == 1
        assert all("label" in event for event in tool_events)
        assert all("input" in event and "output" in event for event in tool_events)


def test_every_specialist_prompt_is_durable_before_its_worker_finishes(
    settings: Settings,
) -> None:
    """Immediate navigation may hide a stream, never its accepted user bubble."""

    init_db()
    with db_session() as session:
        org = Org(name="Durable accepted specialist prompts")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="durable-specialist-prompts@example.test",
            password_hash="unused",
        )
        session.add(user)
        session.flush()
        resources = [
            (
                "manuscript",
                WriterDocumentRow(
                    org_id=org.id,
                    title="Prompt manuscript",
                    created_by=user.id,
                ),
                WriterMessageRow,
                "document_id",
                {"message": "Revise the introduction", "active_path": "main.tex"},
            ),
            (
                "dataset",
                ResearchDatasetRow(
                    org_id=org.id,
                    name="Prompt dataset",
                    filename="prompt.csv",
                ),
                DatasetMessageRow,
                "dataset_id",
                {"question": "Summarize the variables"},
            ),
            (
                "interview",
                InterviewRow(org_id=org.id, title="Prompt interview", status="ready"),
                InterviewMessageRow,
                "interview_id",
                {"question": "Extract the key themes"},
            ),
            (
                "interview-study",
                VoiceStudyRow(org_id=org.id, title="Prompt study"),
                VoiceStudyMessageRow,
                "study_id",
                {"question": "Improve the interview guide"},
            ),
            (
                "survey",
                SurveyRow(org_id=org.id, title="Prompt survey"),
                SurveyMessageRow,
                "survey_id",
                {"question": "Review the survey wording"},
            ),
        ]
        session.add_all([resource for _, resource, *_ in resources])
        session.flush()
        org_id = org.id
        user_id = user.id
        resource_specs = [
            (
                kind,
                resource.public_id,
                message_model,
                foreign_key,
                resource.id,
                request_payload,
            )
            for kind, resource, message_model, foreign_key, request_payload in resources
        ]

    for index, (
        kind,
        resource_id,
        message_model,
        foreign_key,
        numeric_id,
        request_payload,
    ) in enumerate(resource_specs, start=1):
        worker_started = threading.Event()
        release_worker = threading.Event()

        def worker(
            started: threading.Event = worker_started,
            release: threading.Event = release_worker,
        ) -> dict[str, Any]:
            started.set()
            assert release.wait(timeout=3)
            return {"answer": "Done"}

        turn_id = f"durable_prompt_{index}_turn"
        _specialist_chat_sse(
            kind=kind,
            resource_id=resource_id,
            org_id=org_id,
            user_id=user_id,
            turn_id=turn_id,
            request_payload=request_payload,
            last_event_id=0,
            worker=worker,
        )
        assert worker_started.wait(timeout=2)
        with db_session() as session:
            messages = session.scalars(
                select(message_model).where(
                    getattr(message_model, foreign_key) == numeric_id,
                    message_model.role == "user",
                )
            ).all()
            assert len(messages) == 1
            assert messages[0].content == str(
                request_payload.get("message") or request_payload["question"]
            )
            assert messages[0].payload["turn_id"] == turn_id
            if kind == "manuscript":
                assert messages[0].payload["active_path"] == "main.tex"
        release_worker.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with db_session() as session:
                status = session.scalar(
                    select(AgentTurnRow.status).where(AgentTurnRow.turn_id == turn_id)
                )
            if status == "completed":
                break
            time.sleep(0.01)
        else:
            raise AssertionError(f"{kind} worker did not finish")

        # An idempotent reconnect must reuse the accepted prompt, not append it.
        _specialist_chat_sse(
            kind=kind,
            resource_id=resource_id,
            org_id=org_id,
            user_id=user_id,
            turn_id=turn_id,
            request_payload=request_payload,
            last_event_id=0,
            worker=lambda: (_ for _ in ()).throw(AssertionError("must not rerun")),
        )
        with db_session() as session:
            count = session.scalar(
                select(func.count(message_model.id)).where(
                    getattr(message_model, foreign_key) == numeric_id,
                    message_model.role == "user",
                )
            )
            assert count == 1


def test_specialist_worker_finishes_without_consuming_live_response(
    settings: Settings,
    monkeypatch: Any,
) -> None:
    """Leaving a page closes only its reader, never the accepted worker."""

    init_db()
    with db_session() as session:
        org = Org(name="Detached Specialist Worker")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="detached-specialist@example.test",
            password_hash="unused",
        )
        session.add(user)
        session.flush()
        org_id = org.id
        user_id = user.id

    worker_started = threading.Event()
    release_worker = threading.Event()

    def worker() -> dict[str, Any]:
        worker_started.set()
        if not release_worker.wait(timeout=3):
            raise TimeoutError("test did not release detached worker")
        emit_agent_event(
            "tool.completed",
            tool="dataset.inspect",
            label="Inspected the dataset",
            output={"rows": 4},
        )
        return {"answer": "Four rows inspected."}

    monkeypatch.setattr("sixsentences_server.api.app._AGENT_TURN_HEARTBEAT_SECONDS", 0.02)
    response = _specialist_chat_sse(
        kind="dataset",
        resource_id="detached-dataset-resource",
        org_id=org_id,
        user_id=user_id,
        turn_id="detached_turn_1234",
        request_payload={"question": "Inspect the dataset"},
        last_event_id=0,
        worker=worker,
    )
    assert response.body_iterator is not None
    assert worker_started.wait(timeout=2)

    with db_session() as session:
        turn = session.scalar(
            select(AgentTurnRow).where(AgentTurnRow.turn_id == "detached_turn_1234")
        )
        assert turn is not None
        initial_heartbeat = turn.heartbeat_at
    heartbeat_deadline = time.monotonic() + 1
    while time.monotonic() < heartbeat_deadline:
        with db_session() as session:
            current_heartbeat = session.scalar(
                select(AgentTurnRow.heartbeat_at).where(
                    AgentTurnRow.turn_id == "detached_turn_1234"
                )
            )
        if current_heartbeat is not None and current_heartbeat != initial_heartbeat:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("live specialist worker did not refresh its lease")

    # Deliberately never iterate the response body: this is the backend
    # equivalent of navigating away immediately after server acceptance.
    release_worker.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with db_session() as session:
            turn = session.scalar(
                select(AgentTurnRow).where(AgentTurnRow.turn_id == "detached_turn_1234")
            )
            if turn is not None and turn.status == "completed":
                break
        time.sleep(0.02)
    else:
        raise AssertionError("detached specialist worker did not finish")

    with db_session() as session:
        turn = session.scalar(
            select(AgentTurnRow).where(AgentTurnRow.turn_id == "detached_turn_1234")
        )
        assert turn is not None
        assert turn.status == "completed"
        assert turn.result is not None
        assert turn.result["answer"] == "Four rows inspected."
        events = session.scalars(
            select(AgentEventRow)
            .where(AgentEventRow.turn_id == turn.id)
            .order_by(AgentEventRow.sequence)
        ).all()
        assert [event.event for event in events] == [
            "turn.started",
            "tool.completed",
            "answer.completed",
            "turn.completed",
        ]


def test_specialist_artifact_envelope_is_allowlisted_and_deduplicated() -> None:
    normalized = _normalize_specialist_artifacts(
        [
            {
                "id": "figure-123",
                "title": "Exact result",
                "kind": "visual",
                "href": "/figures?figure=figure-123",
                "filename": "result.png",
                "mime_type": "image/png",
                "byte_size": 42,
                "internal_path": "/srv/private/result.png",
            },
            {
                "id": "figure-123",
                "title": "Duplicate",
                "kind": "visual",
                "href": "/figures?figure=figure-123",
            },
            {
                "id": "bad-script",
                "title": "Unsafe",
                "kind": "file",
                "href": "javascript:alert(1)",
            },
            {
                "id": "bad-route",
                "title": "Unsafe route",
                "kind": "file",
                "href": "/auth/logout",
            },
            {
                "id": "bad-traversal",
                "title": "Unsafe traversal",
                "kind": "file",
                "href": "/figures/%2e%2e/auth/logout",
            },
            {
                "id": "bad-kind",
                "title": "Unknown",
                "kind": "executable",
                "href": "/data/example",
            },
        ]
    )

    assert normalized == [
        {
            "id": "figure-123",
            "title": "Exact result",
            "kind": "visual",
            "href": "/figures?figure=figure-123",
            "filename": "result.png",
            "mime_type": "image/png",
            "byte_size": 42,
        }
    ]


def test_specialist_events_bind_to_exact_assistant_turn(settings: Settings) -> None:
    """Parallel tabs must never attach one turn's activity to another turn."""

    init_db()
    with db_session() as session:
        org = Org(name="Event Ledger")
        other_org = Org(name="Other Event Ledger")
        session.add_all([org, other_org])
        session.flush()
        survey = SurveyRow(org_id=org.id, title="Current survey")
        session.add(survey)
        session.flush()
        first = SurveyMessageRow(
            org_id=org.id,
            survey_id=survey.id,
            role="assistant",
            content="First answer",
        )
        second = SurveyMessageRow(
            org_id=org.id,
            survey_id=survey.id,
            role="assistant",
            content="Second answer",
        )
        session.add_all([first, second])
        session.flush()
        survey_id = survey.public_id
        first_id = first.id
        second_id = second.id
        org_id = org.id
        other_org_id = other_org.id

    events = [
        {
            "id": 1,
            "event": "tool.completed",
            "tool": "survey.apply_change",
            "label": "Updated the first question",
            "input": {"operation": "update_question", "question_id": "q1"},
            "before": {"title": "Do you use AI?", "type": "single_choice"},
            "after": {
                "title": "How often do you use AI?",
                "type": "single_choice",
            },
            "output": {"applied": True},
        }
    ]
    _persist_specialist_events(
        kind="survey",
        resource_id=survey_id,
        org_id=org_id,
        message_id=first_id,
        events=events,
    )

    with db_session() as session:
        first = session.get(SurveyMessageRow, first_id)
        second = session.get(SurveyMessageRow, second_id)
        assert first is not None
        assert second is not None
        assert first.payload["agent_events"] == events
        assert first.payload["agent_events"][0]["before"]["title"] == "Do you use AI?"
        assert first.payload["agent_events"][0]["after"]["title"] == ("How often do you use AI?")
        assert "agent_events" not in second.payload

    _persist_specialist_events(
        kind="survey",
        resource_id=survey_id,
        org_id=other_org_id,
        message_id=second_id,
        events=[{"id": 2, "event": "tool.failed"}],
    )
    with db_session() as session:
        second = session.get(SurveyMessageRow, second_id)
        assert second is not None
        assert "agent_events" not in second.payload


def test_specialist_events_ignore_user_messages(settings: Settings) -> None:
    """Only persisted assistant turns can own an agent event ledger."""

    init_db()
    with db_session() as session:
        org = Org(name="User Event Ledger")
        session.add(org)
        session.flush()
        survey = SurveyRow(org_id=org.id, title="Current survey")
        session.add(survey)
        session.flush()
        user_message = SurveyMessageRow(
            org_id=org.id,
            survey_id=survey.id,
            role="user",
            content="Change question one",
        )
        session.add(user_message)
        session.flush()
        survey_id = survey.public_id
        message_id = user_message.id
        org_id = org.id

    _persist_specialist_events(
        kind="survey",
        resource_id=survey_id,
        org_id=org_id,
        message_id=message_id,
        events=[{"id": 1, "event": "change.completed"}],
    )
    with db_session() as session:
        user_message = session.get(SurveyMessageRow, message_id)
        assert user_message is not None
        assert "agent_events" not in user_message.payload


def test_specialist_stream_persists_complete_operational_summary(
    settings: Settings,
) -> None:
    """Reloads retain the real actions and why the specialist stopped."""

    init_db()
    with db_session() as session:
        org = Org(name="Complete Event Ledger")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="complete-event-ledger@example.test",
            password_hash="unused",
        )
        session.add(user)
        session.flush()
        survey = SurveyRow(org_id=org.id, title="Current survey")
        session.add(survey)
        session.flush()
        org_id = org.id
        user_id = user.id
        survey_id = survey.public_id
        survey_numeric_id = survey.id

    def worker() -> dict[str, Any]:
        emit_agent_event(
            "plan.created",
            tool="survey.plan",
            label="Plan the survey update",
            detail="Inspect the survey and update the requested question.",
        )
        emit_agent_event(
            "tool.completed",
            tool="survey.inspect",
            label="Inspected the survey",
            output={"question_count": 1},
        )
        emit_agent_event(
            "change.completed",
            tool="survey.apply_change",
            operation="update_question",
            label="Updated the question",
            before={"title": "AI?"},
            after={"title": "How do you use AI?"},
            applied=True,
            index=1,
            total=1,
        )
        with db_session() as session:
            assistant = SurveyMessageRow(
                org_id=org_id,
                survey_id=survey_numeric_id,
                role="assistant",
                content="The question was updated.",
            )
            session.add(assistant)
            session.flush()
            message_id = assistant.id
        return {
            "message_id": message_id,
            "answer": "The question was updated.",
        }

    response = _specialist_chat_sse(
        kind="survey",
        resource_id=survey_id,
        org_id=org_id,
        user_id=user_id,
        turn_id="survey_turn_1234",
        request_payload={"question": "Update the question"},
        last_event_id=0,
        worker=worker,
    )

    async def collect() -> str:
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    stream = asyncio.run(collect())
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream.splitlines()
        if line.startswith("data: ")
    ]
    completed = next(event for event in events if event["event"] == "answer.completed")
    assert completed["output"] == {
        "completed_tools": 1,
        "applied_changes": 1,
        "proposed_changes": 0,
        "completed_checks": 0,
    }

    with db_session() as session:
        assistant = session.scalar(
            select(SurveyMessageRow)
            .where(SurveyMessageRow.survey_id == survey_numeric_id)
            .where(SurveyMessageRow.role == "assistant")
        )
        assert assistant is not None
        persisted = assistant.payload["agent_events"]
        assert any(event["event"] == "plan.created" for event in persisted)
        assert any(event["event"] == "change.completed" for event in persisted)
        assert any(event["event"] == "answer.completed" for event in persisted)
        assert all(event["turn_id"] == "survey_turn_1234" for event in persisted)
        turn = session.scalar(
            select(AgentTurnRow).where(AgentTurnRow.turn_id == "survey_turn_1234")
        )
        assert turn is not None
        assert turn.status == "completed"
        durable_events = session.scalars(
            select(AgentEventRow)
            .where(AgentEventRow.turn_id == turn.id)
            .order_by(AgentEventRow.sequence)
        ).all()
        assert [event.sequence for event in durable_events] == list(
            range(1, len(durable_events) + 1)
        )
        assert durable_events[-1].event == "turn.completed"

    replay = _specialist_chat_sse(
        kind="survey",
        resource_id=survey_id,
        org_id=org_id,
        user_id=user_id,
        turn_id="survey_turn_1234",
        request_payload={"question": "Update the question"},
        last_event_id=completed["id"],
        worker=lambda: (_ for _ in ()).throw(AssertionError("must not rerun")),
    )

    async def collect_replay() -> str:
        chunks: list[str] = []
        async for chunk in replay.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    replay_stream = asyncio.run(collect_replay())
    assert "event: turn.completed" in replay_stream
    assert (org_id, "survey_turn_1234") not in _AGENT_TURN_CANCEL_EVENTS


def test_terminal_worker_receipt_is_immediate_and_idempotent_on_reconnect(
    settings: Settings,
) -> None:
    init_db()
    with db_session() as session:
        org = Org(name="Terminal Worker Receipt")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="terminal-worker-receipt@example.test",
            password_hash="unused",
        )
        dataset = ResearchDatasetRow(
            org_id=org.id,
            name="Terminal dataset",
            filename="terminal.csv",
        )
        session.add_all([user, dataset])
        session.flush()
        org_id = org.id
        user_id = user.id
        dataset_id = dataset.public_id
        dataset_numeric_id = dataset.id

    async def collect(response: Any) -> str:
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    failures: tuple[tuple[str, Exception], ...] = (
        ("failed", RuntimeError("private provider failure")),
        ("cancelled", LLMCancelledError("private cancellation detail")),
    )
    for index, (status, failure) in enumerate(failures, start=1):
        turn_id = f"terminal_worker_{index}_turn"

        def worker(exception: Exception = failure) -> dict[str, Any]:
            emit_agent_event(
                "tool.completed",
                tool="dataset.inspect",
                label="Inspected the dataset",
                token="must-be-protected",
            )
            raise exception

        request_payload = {"question": f"End as {status}"}
        response = _specialist_chat_sse(
            kind="dataset",
            resource_id=dataset_id,
            org_id=org_id,
            user_id=user_id,
            turn_id=turn_id,
            request_payload=request_payload,
            last_event_id=0,
            worker=worker,
        )
        stream = asyncio.run(collect(response))
        assert f"event: turn.{status}" in stream

        replay = _specialist_chat_sse(
            kind="dataset",
            resource_id=dataset_id,
            org_id=org_id,
            user_id=user_id,
            turn_id=turn_id,
            request_payload=request_payload,
            last_event_id=0,
            worker=lambda: (_ for _ in ()).throw(AssertionError("must not rerun")),
        )
        assert f"event: turn.{status}" in asyncio.run(collect(replay))

    with db_session() as session:
        messages = session.scalars(
            select(DatasetMessageRow)
            .where(DatasetMessageRow.dataset_id == dataset_numeric_id)
            .order_by(DatasetMessageRow.id)
        ).all()
        assert len(messages) == 4
        user_messages = [message for message in messages if message.role == "user"]
        assistant_messages = [message for message in messages if message.role == "assistant"]
        assert [message.content for message in user_messages] == [
            "End as failed",
            "End as cancelled",
        ]
        assert all(message.payload.get("turn_id") for message in user_messages)
        assert {message.content for message in assistant_messages} == {
            "This agent task was stopped.",
            "This agent task could not be completed.",
        }
        for message in assistant_messages:
            events = message.payload["agent_events"]
            assert [event["event"] for event in events] == [
                "turn.started",
                "tool.completed",
                f"turn.{message.payload['terminal_status']}",
            ]
            assert events[1]["token"] == "[protected]"


def test_writer_edit_result_remains_a_proposal_in_stream(settings: Settings) -> None:
    """An unapplied Writer edit must never be synthesized as completed."""

    init_db()
    with db_session() as session:
        org = Org(name="Writer Proposal Event Ledger")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id,
            email="writer-proposal-ledger@example.test",
            password_hash="unused",
        )
        session.add(user)
        session.flush()
        org_id = org.id
        user_id = user.id

    def worker() -> dict[str, Any]:
        edit = {
            "path": "main.tex",
            "find": "Old sentence.",
            "replace": "Improved sentence.",
            "applicable": True,
        }
        emit_agent_event(
            "change.proposed",
            tool="manuscript.propose_change",
            operation="edit_source",
            label="Edit main.tex",
            before=edit["find"],
            after=edit["replace"],
            applied=False,
            index=1,
            total=1,
        )
        return {"reply": "I prepared one source edit.", "edits": [edit]}

    response = _specialist_chat_sse(
        kind="manuscript",
        resource_id="writer-proposal-document",
        org_id=org_id,
        user_id=user_id,
        turn_id="writer_proposal_1234",
        request_payload={"message": "Improve this sentence"},
        last_event_id=0,
        worker=worker,
    )

    async def collect() -> str:
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    events = [
        json.loads(line.removeprefix("data: "))
        for line in asyncio.run(collect()).splitlines()
        if line.startswith("data: ")
    ]
    changes = [event for event in events if str(event["event"]).startswith("change.")]
    assert [event["event"] for event in changes] == ["change.proposed"]
    assert changes[0]["applied"] is False
