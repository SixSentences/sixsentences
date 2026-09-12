"""Solo privacy, durability and bounded synthesis tests for Companion Brainstorm."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

import sixsentences_server.api.app as appmod
import sixsentences_server.interviews.live as livemod
import sixsentences_server.jobs as jobs
from sixsentences_server.api.app import _privacy_export_tables, create_app
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.auth import add_member, authenticate, create_api_key
from sixsentences_server.core.db import (
    BackgroundJobRow,
    BrainstormProjectDocumentRow,
    BrainstormProjectSynthesisRow,
    CapacityReservationRow,
    CreditEventRow,
    InterviewRow,
    LiveCompanionAskRow,
    LiveCompanionSegmentRow,
    LiveCompanionSessionRow,
    Org,
    Project,
    User,
    db_session,
    get_engine,
)
from sixsentences_server.interviews.live import BrainstormResult, ProjectBrainstormResult

PASSWORD = "StrongPass123!"


def _client(settings: Settings) -> tuple[TestClient, str]:
    del settings
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "email": "brainstorm-owner@example.org",
            "password": PASSWORD,
            "org_name": "Brainstorm workspace",
            "name": "Owner",
        },
    )
    assert response.status_code == 201, response.text
    token = str(response.json()["token"])
    client.headers["Authorization"] = f"Bearer {token}"
    org_id = int(client.get("/auth/me").json()["org_id"])
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"
    return (client, token)


def _create_brainstorm(
    client: TestClient, *, client_session_id: str = "brainstorm-session-0001"
) -> dict[str, Any]:
    response = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": client_session_id,
            "title": "Product thoughts",
            "purpose": "brainstorm",
            "language": "auto",
        },
    )
    assert response.status_code == 201, response.text
    payload = cast(dict[str, Any], response.json())
    assert payload["web_url"].endswith(f"/brainstorming?session={payload['id']}")
    return payload


def _append(
    client: TestClient, session_id: str, *, event_id: str, text: str, start_ms: int
) -> dict[str, Any]:
    response = client.post(
        f"/interviews/live/sessions/{session_id}/segments",
        json={
            "segments": [
                {
                    "client_event_id": event_id,
                    "channel": "microphone",
                    "speaker": "Me",
                    "start_ms": start_ms,
                    "end_ms": start_ms + 1000,
                    "text": text,
                    "is_final": True,
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _result_for(segments: list[Any], language: str) -> BrainstormResult:
    first = segments[0]
    label = "Forschungsassistent" if language == "de" else "Research assistant"
    return BrainstormResult.model_validate(
        {
            "summary": f"{label}: turn the spoken notes into a focused product.",
            "themes": [{"title": label, "description": "The central product direction."}],
            "ideas": [],
            "open_questions": [],
            "decisions": [],
            "next_steps": [],
            "evidence": [
                {
                    "kind": "summary",
                    "index": 0,
                    "segment_id": first.client_event_id,
                    "quote": first.text,
                },
                {
                    "kind": "themes",
                    "index": 0,
                    "segment_id": first.client_event_id,
                    "quote": first.text,
                },
            ],
        }
    )


def test_project_evidence_excludes_voice_interviews_from_secondary_ai_context(
    settings: Settings,
) -> None:
    client, _token = _client(settings)
    project_id = client.post("/projects", json={"name": "Consent boundary"}).json()["id"]
    created = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": "secondary-use-boundary",
            "title": "Project companion",
            "purpose": "brainstorm",
            "project_id": project_id,
        },
    )
    assert created.status_code == 201, created.text
    with db_session() as session:
        companion = session.scalar(
            select(LiveCompanionSessionRow).where(
                LiveCompanionSessionRow.public_id == created.json()["id"]
            )
        )
        assert companion is not None
        voice_interview = InterviewRow(
            org_id=companion.org_id,
            project_id=project_id,
            title="Participant interview",
            kind="live",
            status="ready",
            model="models/future-live-provider",
            analysis={"summary": "PRIVATE VOICE PARTICIPANT MATERIAL"},
            config={"study_id": "voice-study-id"},
        )
        imported_interview = InterviewRow(
            org_id=companion.org_id,
            project_id=project_id,
            title="Researcher-provided interview",
            kind="upload",
            status="ready",
            analysis={"summary": "Reusable imported evidence"},
        )
        session.add_all([voice_interview, imported_interview])
        session.flush()
        evidence = livemod._project_evidence(session, companion)
        source_ids = {item["id"] for item in evidence}
        assert imported_interview.public_id in source_ids
        assert voice_interview.public_id not in source_ids
        assert "PRIVATE VOICE PARTICIPANT MATERIAL" not in json.dumps(evidence)


def test_brainstorm_purpose_consent_replay_and_input_channel_boundary(settings: Settings) -> None:
    client, _token = _client(settings)
    missing_consent = client.post(
        "/interviews/live/sessions",
        json={"client_session_id": "conversation-no-consent", "title": "Meeting"},
    )
    assert missing_consent.status_code == 422
    assert missing_consent.json()["detail"]["code"] == "conversation_consent_required"
    contradictory = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": "brainstorm-with-consent",
            "title": "Private thoughts",
            "purpose": "brainstorm",
            "consent": {
                "participants_notified": True,
                "notice_text": "Participants were notified.",
            },
        },
    )
    assert contradictory.status_code == 422
    assert contradictory.json()["detail"]["code"] == "brainstorm_is_solo"
    created = _create_brainstorm(client)
    assert created["purpose"] == "brainstorm"
    assert created["consent"] is None
    assert created["last_segment_sequence"] == 0
    replay = _create_brainstorm(client)
    assert replay["id"] == created["id"]
    conflict = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": "brainstorm-session-0001",
            "title": "Product thoughts",
            "purpose": "conversation",
            "consent": {
                "participants_notified": True,
                "notice_text": "Participants were notified.",
            },
        },
    )
    assert conflict.status_code == 409
    mixed = client.post(
        f"/interviews/live/sessions/{created['id']}/segments",
        json={
            "segments": [
                {
                    "client_event_id": "brain-mic-0001",
                    "channel": "microphone",
                    "speaker": "Me",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "A private thought.",
                    "is_final": True,
                },
                {
                    "client_event_id": "brain-system-0001",
                    "channel": "system",
                    "speaker": "Computer",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "This must never be accepted.",
                    "is_final": True,
                },
            ]
        },
    )
    assert mixed.status_code == 409
    assert mixed.json()["detail"]["code"] == "brainstorm_microphone_only"
    with db_session() as session:
        assert session.scalar(select(func.count()).select_from(LiveCompanionSegmentRow)) == 0
    accepted = _append(
        client,
        created["id"],
        event_id="brain-mic-0002",
        text="Build a private research assistant.",
        start_ms=0,
    )
    cutoff = int(accepted["last_event_sequence"])
    ordinary_ask = client.post(
        f"/interviews/live/sessions/{created['id']}/ask",
        json={"client_request_id": "wrong-mode-ask", "question": "What did I say?"},
    )
    assert ordinary_ask.status_code == 409
    assert ordinary_ask.json()["detail"]["code"] == "session_not_conversation"
    assert cutoff > 0
    typed = client.post(
        f"/interviews/live/sessions/{created['id']}/segments",
        json={
            "segments": [
                {
                    "client_event_id": "brain-typed-0001",
                    "channel": "typed",
                    "speaker": "Me",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "text": "A thought entered directly in the browser.",
                    "is_final": True,
                }
            ]
        },
    )
    assert typed.status_code == 200, typed.text
    assert typed.json()["accepted"] == 1
    segment_events = [
        item["payload"]
        for item in client.get(f"/interviews/live/sessions/{created['id']}/events").json()["events"]
        if item["type"] == "segment"
    ]
    assert segment_events[-1]["channel"] == "typed"
    assert segment_events[-1]["text"] == "A thought entered directly in the browser."


def test_typed_input_does_not_expand_conversation_capture_contract(settings: Settings) -> None:
    client, _token = _client(settings)
    created = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": "conversation-typed-boundary",
            "title": "Meeting",
            "purpose": "conversation",
            "consent": {
                "participants_notified": True,
                "notice_text": "Participants were notified.",
            },
        },
    )
    assert created.status_code == 201, created.text
    response = client.post(
        f"/interviews/live/sessions/{created.json()['id']}/segments",
        json={
            "segments": [
                {
                    "client_event_id": "conversation-typed-0001",
                    "channel": "typed",
                    "speaker": "Me",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "This must not become interview evidence.",
                    "is_final": True,
                }
            ]
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "typed_input_requires_brainstorm"
    with db_session() as session:
        assert session.scalar(select(func.count()).select_from(LiveCompanionSegmentRow)) == 0


def test_exact_old_brainstorm_segment_is_creator_scoped(settings: Settings) -> None:
    client, _token = _client(settings)
    created = _create_brainstorm(client, client_session_id="exact-old-segment-session-0001")
    _append(
        client,
        created["id"],
        event_id="exact-old-segment-0001",
        text="Introduction, Methodology, Results, Discussion.",
        start_ms=0,
    )
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "brainstorm-owner@example.org"))
        assert owner is not None
        add_member(session, owner.org_id, "exact-segment-member@example.org", PASSWORD, "member")
        member_token = authenticate(session, "exact-segment-member@example.org", PASSWORD)
    response = client.get(
        f"/interviews/live/sessions/{created['id']}/segments/exact-old-segment-0001"
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "id",
        "sequence",
        "channel",
        "speaker",
        "start_ms",
        "end_ms",
        "text",
        "is_final",
        "created_at",
    }
    assert response.json()["text"] == "Introduction, Methodology, Results, Discussion."
    member_client = TestClient(client.app)
    member_client.headers["Authorization"] = f"Bearer {member_token}"
    foreign = member_client.get(
        f"/interviews/live/sessions/{created['id']}/segments/exact-old-segment-0001"
    )
    assert foreign.status_code == 404


def test_brainstorm_receipt_history_is_newest_first_and_hard_bounded(settings: Settings) -> None:
    client, _token = _client(settings)
    created = _create_brainstorm(client)
    with db_session() as session:
        live_session = session.scalar(
            select(LiveCompanionSessionRow).where(
                LiveCompanionSessionRow.public_id == created["id"]
            )
        )
        assert live_session is not None
        for index in range(105):
            session.add(
                LiveCompanionAskRow(
                    org_id=live_session.org_id,
                    user_id=live_session.user_id,
                    session_id=live_session.id,
                    kind="brainstorm",
                    client_request_id=f"bounded-receipt-{index:04d}",
                    question="Structure this brainstorm.",
                    request_sha256=f"{index:064x}",
                    context_through_sequence=1,
                    context_snapshot={"output_language": "en", "schema_version": 1},
                    action_id=f"bounded-action-{index:04d}",
                    status="failed",
                    answer="",
                    error="Unavailable.",
                    error_code="brainstorm_unavailable",
                    sequence=0,
                )
            )
    default_page = client.get(f"/interviews/live/sessions/{created['id']}/brainstorms")
    assert default_page.status_code == 200, default_page.text
    default_items = default_page.json()["brainstorms"]
    assert len(default_items) == 100
    assert default_items[0]["client_request_id"] == "bounded-receipt-0104"
    assert default_items[-1]["client_request_id"] == "bounded-receipt-0005"
    latest_page = client.get(f"/interviews/live/sessions/{created['id']}/brainstorms?limit=3")
    assert [item["client_request_id"] for item in latest_page.json()["brainstorms"]] == [
        "bounded-receipt-0104",
        "bounded-receipt-0103",
        "bounded-receipt-0102",
    ]
    assert (
        client.get(f"/interviews/live/sessions/{created['id']}/brainstorms?limit=501").status_code
        == 422
    )


def test_session_purpose_filter_is_applied_before_the_result_limit(settings: Settings) -> None:
    client, _token = _client(settings)
    created = _create_brainstorm(client)
    appended = _append(
        client,
        created["id"],
        event_id="purpose-filter-segment-0001",
        text="Keep this older brainstorm visible.",
        start_ms=0,
    )
    cutoff = int(appended["last_event_sequence"])
    with db_session() as session:
        brainstorm = session.scalar(
            select(LiveCompanionSessionRow).where(
                LiveCompanionSessionRow.public_id == created["id"]
            )
        )
        assert brainstorm is not None
        brainstorm.status = "completed"
        for index in range(100):
            session.add(
                LiveCompanionSessionRow(
                    org_id=brainstorm.org_id,
                    user_id=brainstorm.user_id,
                    client_session_id=f"newer-conversation-{index:04d}",
                    title=f"Newer conversation {index}",
                    purpose="conversation",
                    status="completed",
                    consent={"participants_notified": True},
                )
            )
    brainstorm_page = client.get("/interviews/live/sessions?purpose=brainstorm&limit=1")
    assert brainstorm_page.status_code == 200, brainstorm_page.text
    assert [item["id"] for item in brainstorm_page.json()["sessions"]] == [created["id"]]
    assert brainstorm_page.json()["total"] == 1
    brainstorm_summary = brainstorm_page.json()["sessions"][0]
    assert brainstorm_summary["last_segment_sequence"] == cutoff
    assert brainstorm_summary["completed_through_sequence"] == cutoff
    live_selects: list[str] = []

    def record_live_select(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = statement.lstrip().lower()
        if normalized.startswith("select") and "live_companion_" in normalized:
            live_selects.append(normalized)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record_live_select)
    try:
        conversation_page = client.get("/interviews/live/sessions?purpose=conversation&limit=100")
    finally:
        event.remove(engine, "before_cursor_execute", record_live_select)
    assert conversation_page.status_code == 200, conversation_page.text
    assert len(conversation_page.json()["sessions"]) == 100
    assert all(item["purpose"] == "conversation" for item in conversation_page.json()["sessions"])
    assert len(live_selects) == 3
    assert client.get("/interviews/live/sessions?purpose=other").status_code == 422


def test_brainstorm_complete_is_private_durable_idempotent_and_accounted(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    created = _create_brainstorm(client)
    first = _append(
        client,
        created["id"],
        event_id="brain-segment-0001",
        text="I want a research assistant that keeps my evidence organised.",
        start_ms=0,
    )
    _append(
        client,
        created["id"],
        event_id="brain-segment-0002",
        text="The next step is a small daily capture workflow.",
        start_ms=1000,
    )
    session_state = client.get(f"/interviews/live/sessions/{created['id']}").json()
    cutoff = int(session_state["last_segment_sequence"])
    assert cutoff > int(first["last_event_sequence"])
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(
        livemod,
        "_structure_brainstorm",
        lambda _pool, *, output_language, segments, **_kwargs: _result_for(
            segments, output_language
        ),
    )
    request = {
        "client_request_id": "brainstorm-structure-0001",
        "output_language": "de",
        "context_through_sequence": cutoff,
        "schema_version": 1,
    }
    completed = client.post(f"/interviews/live/sessions/{created['id']}/complete", json=request)
    assert completed.status_code == 202, completed.text
    first_receipt = completed.json()["brainstorm"]
    assert completed.json()["session"]["completed_through_sequence"] == cutoff
    assert completed.json()["session"]["interview_id"] is None
    recovered = client.get(
        f"/interviews/live/sessions/{created['id']}/brainstorms/{first_receipt['id']}"
    )
    assert recovered.status_code == 200, recovered.text
    payload = recovered.json()
    assert payload["status"] == "completed"
    assert payload["schema_version"] == 1
    assert payload["output_language"] == "de"
    assert payload["result"]["themes"][0]["title"] == "Forschungsassistent"
    assert payload["result"]["evidence"][0]["segment_id"] == "brain-segment-0001"
    replay = client.post(f"/interviews/live/sessions/{created['id']}/complete", json=request)
    assert replay.status_code == 200
    assert replay.json()["brainstorm"]["id"] == first_receipt["id"]
    assert client.get(f"/interviews/live/sessions/{created['id']}/asks").json() == []
    summary = client.get(f"/interviews/live/sessions/{created['id']}").json()
    assert summary["ask_count"] == 0
    event_types = {
        item["type"]
        for item in client.get(f"/interviews/live/sessions/{created['id']}/events").json()["events"]
    }
    assert "brainstorm" in event_types
    assert "ask" not in event_types
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "brainstorm-owner@example.org"))
        assert owner is not None
        add_member(session, owner.org_id, "brainstorm-member@example.org", PASSWORD, "member")
        member_token = authenticate(session, "brainstorm-member@example.org", PASSWORD)
        member = session.scalar(select(User).where(User.email == "brainstorm-member@example.org"))
        assert member is not None
        member_tables, _inventory = _privacy_export_tables(session, user=member)
        assert member_tables.get("live_companion_sessions", []) == []
        assert member_tables.get("live_companion_segments", []) == []
        assert member_tables.get("live_companion_asks", []) == []
        assert session.scalar(select(func.count()).select_from(InterviewRow)) == 0
        receipts = session.scalars(select(LiveCompanionAskRow)).all()
        assert len(receipts) == 1 and receipts[0].kind == "brainstorm"
        assert receipts[0].answer == ""
        assert receipts[0].result["summary"]
        reservations = session.scalars(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action == "live_companion_brainstorm"
            )
        ).all()
        assert len(reservations) == 1
        assert reservations[0].status == "settled"
        assert session.scalar(select(func.count()).select_from(CreditEventRow)) == 1
    member_client = TestClient(client.app)
    member_client.headers["Authorization"] = f"Bearer {member_token}"
    assert member_client.get(f"/interviews/live/sessions/{created['id']}").status_code == 404
    assert (
        member_client.get(f"/interviews/live/sessions/{created['id']}/brainstorms").status_code
        == 404
    )
    assert member_client.get("/interviews").json() == []


def test_brainstorm_grounding_filter_and_complete_coverage_chunking() -> None:
    segment = LiveCompanionSegmentRow(
        client_event_id="segment-ground-0001",
        speaker="Me",
        text="The decision is to test the local PDF workflow next.",
        sequence=1,
        payload_sha256="test",
        channel="microphone",
        start_ms=0,
        end_ms=1,
        is_final=True,
    )
    filtered = livemod._validated_brainstorm_result(
        {
            "summary": "Test the local PDF workflow.",
            "themes": [],
            "ideas": [{"title": "Invented", "description": "Not grounded."}],
            "open_questions": [],
            "decisions": ["Test the local PDF workflow next."],
            "next_steps": [],
            "evidence": [
                {
                    "kind": "summary",
                    "index": 0,
                    "segment_id": "segment-ground-0001",
                    "quote": "test the local PDF workflow next",
                },
                {
                    "kind": "ideas",
                    "index": 0,
                    "segment_id": "invented-segment",
                    "quote": "Not grounded",
                },
                {
                    "kind": "decisions",
                    "index": 0,
                    "segment_id": "segment-ground-0001",
                    "quote": "The decision is to test the local PDF workflow next.",
                },
            ],
        },
        [segment],
    )
    assert filtered is not None
    assert filtered.ideas == []
    assert filtered.decisions == ["Test the local PDF workflow next."]
    rows: list[LiveCompanionSegmentRow] = [
        cast(
            LiveCompanionSegmentRow,
            SimpleNamespace(
                client_event_id=f"segment-{index:08d}", speaker="Me", text="x", sequence=index + 1
            ),
        )
        for index in range(livemod.MAX_BRAINSTORM_SEGMENTS)
    ]
    chunks = livemod._brainstorm_chunks(rows)
    assert 1 < len(chunks) <= livemod.BRAINSTORM_MAX_CHUNKS
    assert sum(len(chunk) for chunk in chunks) == livemod.MAX_BRAINSTORM_SEGMENTS
    assert [row.client_event_id for chunk in chunks for row in chunk] == [
        row.client_event_id for row in rows
    ]

    class Pool:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.seen_ids: list[str] = []

        def complete_json(self, _task: Any, *, prompt: str, **_kwargs: Any) -> Any:
            self.prompts.append(prompt)
            data = json.loads(prompt.split("\nDATA:\n", 1)[1])
            if "segments" in data:
                self.seen_ids.extend(item["i"] for item in data["segments"])
                source = data["segments"][0]
            else:
                source_evidence = data["chunk_structures"][0]["evidence"][0]
                source = {"i": source_evidence["segment_id"], "t": source_evidence["quote"]}
            result = {
                "summary": "A bounded complete synthesis.",
                "themes": [{"title": "Coverage", "description": "All chunks were mapped."}],
                "ideas": [],
                "open_questions": [],
                "decisions": [],
                "next_steps": [],
                "evidence": [
                    {
                        "kind": "summary",
                        "index": 0,
                        "segment_id": source["i"],
                        "quote": source["t"],
                    },
                    {"kind": "themes", "index": 0, "segment_id": source["i"], "quote": source["t"]},
                ],
            }
            return SimpleNamespace(text=json.dumps(result))

    pool = Pool()
    result = livemod._structure_brainstorm(
        cast(Any, pool), title="Long brain dump", output_language="en", segments=rows
    )
    assert result is not None
    assert pool.seen_ids == [row.client_event_id for row in rows]
    assert all(len(prompt) <= livemod.BRAINSTORM_MAP_PROMPT_CHARS for prompt in pool.prompts)


@pytest.mark.parametrize("repair_valid", [True, False])
def test_short_qa_brainstorm_repairs_receipts_without_weakening_grounding(
    repair_valid: bool,
) -> None:
    raw = "Launch QA only: compare two onboarding instructions with five synthetic participants. Primary outcome: task completion. Keep the original wording unchanged. No real participant data is included."
    segment = cast(
        LiveCompanionSegmentRow,
        SimpleNamespace(
            client_event_id="qa-brainstorm-segment-0001", speaker="Me", text=raw, sequence=1
        ),
    )

    class Pool:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def complete_json(self, _task: Any, **kwargs: Any) -> Any:
            self.requests.append(kwargs)
            receipts = [
                {
                    "kind": kind,
                    "index": 0,
                    "segment_id": segment.client_event_id,
                    "quote": raw if repair_valid else "Five real participants completed the study.",
                }
                for kind in ("summary", "ideas")
            ]
            if len(self.requests) == 1:
                receipts = receipts[1:]
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "summary": "Compare two onboarding instructions using five synthetic participants.",
                        "ideas": [
                            {
                                "title": "Synthetic onboarding comparison",
                                "description": "Use task completion as the primary outcome; no real participant data is included.",
                            }
                        ],
                        "evidence": receipts,
                    }
                )
            )

    pool = Pool()
    result = livemod._structure_brainstorm(
        cast(Any, pool), title="Launch QA", output_language="en", segments=[segment]
    )
    assert len(pool.requests) == 2
    assert pool.requests[0]["prompt"] == pool.requests[1]["prompt"]
    assert "VALIDATION REPAIR" in pool.requests[1]["system"]
    assert segment.text == raw
    if repair_valid:
        assert result is not None
        assert result.ideas[0].title == "Synthetic onboarding comparison"
        assert all(receipt.quote in raw for receipt in result.evidence)
    else:
        assert result is None


def test_complete_and_explicit_post_share_pending_receipt_then_allow_restructure(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    created = _create_brainstorm(client)
    appended = _append(
        client,
        created["id"],
        event_id="brain-race-segment-0001",
        text="First make the research capture flow simple.",
        start_ms=0,
    )
    cutoff = int(appended["last_event_sequence"])
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(
        livemod,
        "_structure_brainstorm",
        lambda _pool, *, output_language, segments, **_kwargs: _result_for(
            segments, output_language
        ),
    )
    explicit = client.post(
        f"/interviews/live/sessions/{created['id']}/brainstorms",
        json={
            "client_request_id": "brainstorm-explicit-0001",
            "output_language": "de",
            "context_through_sequence": cutoff,
            "schema_version": 1,
        },
    )
    assert explicit.status_code == 202
    competing = client.post(
        f"/interviews/live/sessions/{created['id']}/brainstorms",
        json={
            "client_request_id": "brainstorm-competing-0001",
            "output_language": "de",
            "context_through_sequence": cutoff,
            "schema_version": 1,
        },
    )
    assert competing.status_code == 409
    assert competing.json()["detail"]["code"] == "brainstorm_in_progress"
    assert competing.json()["detail"]["brainstorm"]["id"] == explicit.json()["id"]
    completed = client.post(
        f"/interviews/live/sessions/{created['id']}/complete",
        json={
            "client_request_id": "brainstorm-explicit-0001",
            "output_language": "de",
            "context_through_sequence": cutoff,
            "schema_version": 1,
        },
    )
    assert completed.status_code == 202
    assert completed.json()["brainstorm"]["id"] == explicit.json()["id"]
    with db_session() as session:
        assert session.scalar(select(func.count()).select_from(LiveCompanionAskRow)) == 1
        assert session.scalar(select(func.count()).select_from(CapacityReservationRow)) == 1
        job = session.scalar(select(BackgroundJobRow))
        assert job is not None
        assert job.timeout_seconds == 900
        assert job.status == "queued"
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="chat")
    assert worker.run_once()
    first_result = client.get(
        f"/interviews/live/sessions/{created['id']}/brainstorms/{explicit.json()['id']}"
    ).json()
    assert first_result["status"] == "completed"
    restructured = client.post(
        f"/interviews/live/sessions/{created['id']}/brainstorms",
        json={
            "client_request_id": "brainstorm-restructure-0001",
            "output_language": "en",
            "context_through_sequence": cutoff,
            "schema_version": 1,
        },
    )
    assert restructured.status_code == 202
    assert restructured.json()["id"] != explicit.json()["id"]
    assert worker.run_once()
    second_result = client.get(
        f"/interviews/live/sessions/{created['id']}/brainstorms/{restructured.json()['id']}"
    ).json()
    assert second_result["status"] == "completed"
    assert second_result["output_language"] == "en"
    assert second_result["result"]["themes"][0]["title"] == "Research assistant"
    with db_session() as session:
        receipts = session.scalars(
            select(LiveCompanionAskRow).order_by(LiveCompanionAskRow.id)
        ).all()
        assert len(receipts) == 2
        assert receipts[0].action_id != receipts[1].action_id
        assert session.scalar(select(func.count()).select_from(CreditEventRow)) == 2
    pending = client.post(
        f"/interviews/live/sessions/{created['id']}/brainstorms",
        json={
            "client_request_id": "brainstorm-cancel-0001",
            "output_language": "de",
            "context_through_sequence": cutoff,
            "schema_version": 1,
        },
    )
    assert pending.status_code == 202
    cancelled = client.post(
        f"/interviews/live/sessions/{created['id']}/brainstorms/{pending.json()['id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "failed"
    assert cancelled.json()["error_code"] == "brainstorm_cancelled"
    with db_session() as session:
        receipt = session.scalar(
            select(LiveCompanionAskRow).where(LiveCompanionAskRow.public_id == pending.json()["id"])
        )
        assert receipt is not None
        reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id == receipt.action_id
            )
        )
        job = session.scalar(select(BackgroundJobRow).where(BackgroundJobRow.args == [receipt.id]))
        assert reservation is not None and reservation.status == "cancelled"
        assert job is not None and job.status == "cancelled"
    deleted = client.delete(f"/interviews/live/sessions/{created['id']}")
    assert deleted.status_code == 200
    with db_session() as session:
        assert session.scalar(select(func.count()).select_from(LiveCompanionAskRow)) == 0
        assert session.scalar(select(func.count()).select_from(LiveCompanionSegmentRow)) == 0


def test_brainstorm_cutoff_and_character_limit_fail_atomically(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    monkeypatch.setattr(livemod, "MAX_BRAINSTORM_TRANSCRIPT_CHARS", 20)
    created = _create_brainstorm(client)
    appended = _append(
        client, created["id"], event_id="brain-limit-segment-0001", text="accepted", start_ms=0
    )
    cutoff = int(appended["last_event_sequence"])
    assert appended["transcript_char_count"] == len("accepted")
    monkeypatch.setattr(livemod, "MAX_BRAINSTORM_SEGMENTS", 1)
    too_many = client.post(
        f"/interviews/live/sessions/{created['id']}/segments",
        json={
            "segments": [
                {
                    "client_event_id": "brain-limit-count-0002",
                    "channel": "microphone",
                    "speaker": "Me",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "text": "x",
                    "is_final": True,
                }
            ]
        },
    )
    assert too_many.status_code == 413
    assert too_many.json()["detail"]["code"] == "brainstorm_too_many_segments"
    monkeypatch.setattr(livemod, "MAX_BRAINSTORM_SEGMENTS", 100)
    rejected = client.post(
        f"/interviews/live/sessions/{created['id']}/segments",
        json={
            "segments": [
                {
                    "client_event_id": "brain-limit-segment-0002",
                    "channel": "microphone",
                    "speaker": "Me",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "text": "1234567",
                    "is_final": True,
                },
                {
                    "client_event_id": "brain-limit-segment-0003",
                    "channel": "microphone",
                    "speaker": "Me",
                    "start_ms": 2000,
                    "end_ms": 3000,
                    "text": "7654321",
                    "is_final": True,
                },
            ]
        },
    )
    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "brainstorm_too_large"
    state = client.get(f"/interviews/live/sessions/{created['id']}").json()
    assert state["last_segment_sequence"] == cutoff
    assert state["transcript_char_count"] == len("accepted")
    no_body = client.post(f"/interviews/live/sessions/{created['id']}/complete")
    assert no_body.status_code == 422
    state = client.get(f"/interviews/live/sessions/{created['id']}").json()
    assert state["status"] == "recording"
    assert client.get(f"/interviews/live/sessions/{created['id']}/brainstorms").json() == {
        "brainstorms": []
    }
    wrong_cutoff = client.post(
        f"/interviews/live/sessions/{created['id']}/complete",
        json={
            "client_request_id": "brainstorm-wrong-cutoff",
            "output_language": "de",
            "context_through_sequence": cutoff + 1,
            "schema_version": 1,
        },
    )
    assert wrong_cutoff.status_code == 409
    assert wrong_cutoff.json()["detail"]["code"] == "brainstorm_cutoff_mismatch"
    with db_session() as session:
        assert session.scalar(select(func.count()).select_from(LiveCompanionAskRow)) == 0
        assert session.scalar(select(func.count()).select_from(CapacityReservationRow)) == 0


def test_malformed_brainstorm_is_safe_and_new_request_can_retry(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    created = _create_brainstorm(client)
    appended = _append(
        client,
        created["id"],
        event_id="brain-malformed-segment-0001",
        text="Keep a grounded overview of these thoughts.",
        start_ms=0,
    )
    cutoff = int(appended["last_event_sequence"])
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(livemod, "_structure_brainstorm", lambda *_args, **_kwargs: None)
    failed_request = {
        "client_request_id": "brainstorm-malformed-0001",
        "output_language": "de",
        "context_through_sequence": cutoff,
        "schema_version": 1,
    }
    accepted = client.post(
        f"/interviews/live/sessions/{created['id']}/brainstorms", json=failed_request
    )
    assert accepted.status_code == 202
    failed = client.get(
        f"/interviews/live/sessions/{created['id']}/brainstorms/{accepted.json()['id']}"
    ).json()
    assert failed["status"] == "failed"
    assert failed["result"] is None
    assert failed["error_code"] == "brainstorm_result_not_grounded"
    assert "SimpleNamespace" not in str(failed)
    with db_session() as session:
        first = session.scalar(
            select(LiveCompanionAskRow).where(
                LiveCompanionAskRow.public_id == accepted.json()["id"]
            )
        )
        assert first is not None
        first_action_id = first.action_id
        assert session.scalar(select(func.count()).select_from(CreditEventRow)) == 0
        reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id == first.action_id
            )
        )
        assert reservation is not None and reservation.status == "settled"
    monkeypatch.setattr(
        livemod,
        "_structure_brainstorm",
        lambda _pool, *, output_language, segments, **_kwargs: _result_for(
            segments, output_language
        ),
    )
    retry = client.post(
        f"/interviews/live/sessions/{created['id']}/brainstorms",
        json={
            "client_request_id": "brainstorm-malformed-retry-0001",
            "output_language": "en",
            "context_through_sequence": cutoff,
            "schema_version": 1,
        },
    )
    assert retry.status_code == 202
    recovered = client.get(
        f"/interviews/live/sessions/{created['id']}/brainstorms/{retry.json()['id']}"
    ).json()
    assert recovered["status"] == "completed"
    assert recovered["output_language"] == "en"
    with db_session() as session:
        second = session.scalar(
            select(LiveCompanionAskRow).where(LiveCompanionAskRow.public_id == retry.json()["id"])
        )
        assert second is not None
        assert second.action_id != first_action_id
        assert session.scalar(select(func.count()).select_from(CreditEventRow)) == 1


def _completed_project_brainstorm(
    client: TestClient, *, project_id: int | None, suffix: str, text: str
) -> dict[str, Any]:
    response = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": f"project-brainstorm-{suffix}",
            "title": f"Session {suffix}",
            "purpose": "brainstorm",
            "project_id": project_id,
            "language": "de",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    _append(client, created["id"], event_id=f"project-segment-{suffix}", text=text, start_ms=0)
    state = client.get(f"/interviews/live/sessions/{created['id']}").json()
    completed = client.post(
        f"/interviews/live/sessions/{created['id']}/complete",
        json={
            "client_request_id": f"project-structure-{suffix}",
            "output_language": "de",
            "context_through_sequence": state["last_segment_sequence"],
            "schema_version": 1,
        },
    )
    assert completed.status_code == 202, completed.text
    recovered = client.get(
        f"/interviews/live/sessions/{created['id']}/brainstorms/{completed.json()['brainstorm']['id']}"
    ).json()
    assert recovered["status"] == "completed"
    return client.get(f"/interviews/live/sessions/{created['id']}").json()


def _cross_session_result(
    selected_session_ids: list[str], segment_text: dict[tuple[str, str], str]
) -> ProjectBrainstormResult:
    evidence = [
        {"session_id": session_id, "segment_id": segment_id, "quote": text}
        for (session_id, segment_id), text in segment_text.items()
    ]
    evidence_by_session = {item["session_id"]: item for item in evidence}
    return ProjectBrainstormResult.model_validate(
        {
            "title": "Project thinking",
            "summary": "The sessions remain distinct until evidence supports a link.",
            "summary_evidence": [evidence[0]],
            "clusters": [
                {
                    "id": f"cluster-{index}",
                    "title": f"Thought {index}",
                    "summary": "A standalone grounded thought.",
                    "session_ids": [session_id],
                    "evidence": [evidence_by_session[session_id]],
                }
                for index, session_id in enumerate(selected_session_ids, start=1)
            ],
            "connections": [],
            "unconnected_cluster_ids": [
                f"cluster-{index}" for index in range(1, len(selected_session_ids) + 1)
            ],
            "source_session_ids": selected_session_ids,
        }
    )


def test_project_brainstorm_document_is_grounded_revisioned_private_and_idempotent(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    project_id = client.post("/projects", json={"name": "Presentation"}).json()["id"]
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(
        livemod,
        "_structure_brainstorm",
        lambda _pool, *, output_language, segments, **_kwargs: _result_for(
            segments, output_language
        ),
    )
    first = _completed_project_brainstorm(
        client,
        project_id=project_id,
        suffix="alpha-0001",
        text="Use a clear opening for the presentation.",
    )
    second = _completed_project_brainstorm(
        client,
        project_id=project_id,
        suffix="beta-0002",
        text="Keep the evaluation reflection separate for now.",
    )
    document_url = f"/interviews/live/brainstorm-projects/{project_id}/document"
    empty_document = client.get(document_url)
    assert empty_document.status_code == 200, empty_document.text
    assert empty_document.json()["revision"] == 0
    manual = client.patch(
        document_url,
        json={"manual_markdown": "# My framing\nDo not replace this.", "expected_revision": 0},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["revision"] == 1
    monkeypatch.setattr(
        livemod,
        "_structure_project_brainstorm",
        lambda _pool, *, selected_session_ids, segment_text, **_kwargs: _cross_session_result(
            selected_session_ids, segment_text
        ),
    )
    request = {
        "client_request_id": "project-synthesis-0001",
        "output_language": "de",
        "session_ids": [first["id"], second["id"]],
        "include_all_completed": False,
        "expected_document_revision": 1,
        "schema_version": 1,
    }
    accepted = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses", json=request
    )
    assert accepted.status_code == 202, accepted.text
    receipt_id = accepted.json()["id"]
    receipt = client.get(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses/{receipt_id}"
    ).json()
    assert receipt["status"] == "completed"
    assert receipt["mode"] == "selected"
    assert receipt["result"]["connections"] == []
    assert set(receipt["result"]["unconnected_cluster_ids"]) == {"cluster-1", "cluster-2"}
    assert all(
        set(item) == {"session_id", "title", "brainstorm_id", "context_through_sequence"}
        for item in receipt["source_sessions"]
    )
    document = client.get(document_url).json()
    assert document["revision"] == 2
    assert document["manual_markdown"] == "# My framing\nDo not replace this."
    assert document["manual_revision"] == 1
    assert document["update_available"] is False
    assert {item["session_id"] for item in document["source_sessions"]} == {
        first["id"],
        second["id"],
    }
    assert all(
        set(item) == {"session_id", "title", "brainstorm_id", "context_through_sequence"}
        for item in document["source_sessions"]
    )
    replay = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses", json=request
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == receipt_id
    page = client.get(f"/interviews/live/brainstorm-projects/{project_id}/syntheses").json()
    assert page["total"] == 1
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "brainstorm-owner@example.org"))
        assert owner is not None
        member = add_member(
            session, owner.org_id, "project-private-member@example.org", PASSWORD, "member"
        )
        member_token = authenticate(session, "project-private-member@example.org", PASSWORD)
        companion_key = create_api_key(
            session, owner, "Project least privilege", scopes=("companion:read", "companion:write")
        )
        member_tables, _ = _privacy_export_tables(session, user=member)
        assert member_tables.get("brainstorm_project_documents", []) == []
        assert member_tables.get("brainstorm_project_syntheses", []) == []
    member_client = TestClient(client.app)
    member_client.headers["Authorization"] = f"Bearer {member_token}"
    assert member_client.get(document_url).json()["result"] is None
    companion = TestClient(client.app)
    companion.headers["Authorization"] = f"Bearer {companion_key}"
    assert companion.get(document_url).status_code == 403
    assert (
        companion.post(
            f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
            json={**request, "client_request_id": "paired-denied-0001"},
        ).status_code
        == 403
    )
    document_before_paired_move = client.get(document_url).json()
    paired_move = companion.patch(
        f"/interviews/live/sessions/{first['id']}", json={"project_id": None}
    )
    assert paired_move.status_code == 403
    assert client.get(document_url).json() == document_before_paired_move
    assert client.get(f"/interviews/live/sessions/{first['id']}").json()["project_id"] == project_id


def test_completed_brainstorm_filing_preserves_existing_document_and_fences_sources(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    project_id = client.post("/projects", json={"name": "Filed thoughts"}).json()["id"]
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(
        livemod,
        "_structure_brainstorm",
        lambda _pool, *, output_language, segments, **_kwargs: _result_for(
            segments, output_language
        ),
    )
    first = _completed_project_brainstorm(
        client,
        project_id=project_id,
        suffix="file-first-0001",
        text="Keep the existing project idea.",
    )
    second = _completed_project_brainstorm(
        client, project_id=project_id, suffix="file-second-0002", text="Keep a second project idea."
    )
    monkeypatch.setattr(
        livemod,
        "_structure_project_brainstorm",
        lambda _pool, *, selected_session_ids, segment_text, **_kwargs: _cross_session_result(
            selected_session_ids, segment_text
        ),
    )
    document_url = f"/interviews/live/brainstorm-projects/{project_id}/document"
    revision = client.get(document_url).json()["revision"]
    created = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
        json={
            "client_request_id": "filing-base-synthesis-0001",
            "output_language": "de",
            "session_ids": [first["id"], second["id"]],
            "include_all_completed": False,
            "expected_document_revision": revision,
            "schema_version": 1,
        },
    )
    assert created.status_code == 202, created.text
    before = client.get(document_url).json()
    extra = _completed_project_brainstorm(
        client, project_id=None, suffix="file-extra-0003", text="A later independent thought."
    )
    filed = client.patch(
        f"/interviews/live/sessions/{extra['id']}", json={"project_id": project_id}
    )
    assert filed.status_code == 200, filed.text
    after = client.get(document_url).json()
    assert after["result"] == before["result"]
    assert after["source_sessions"] == before["source_sessions"]
    assert after["manual_markdown"] == before["manual_markdown"]
    assert after["update_available"] is True
    assert after["revision"] == before["revision"]
    manual_write = client.patch(
        document_url,
        json={
            "manual_markdown": "concurrent edit remains revision-safe",
            "expected_revision": before["revision"],
        },
    )
    assert manual_write.status_code == 200, manual_write.text
    after = manual_write.json()
    refreshed = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
        json={
            "client_request_id": "filing-refresh-synthesis-0001",
            "output_language": "de",
            "session_ids": [],
            "include_all_completed": True,
            "expected_document_revision": after["revision"],
            "schema_version": 1,
        },
    )
    assert refreshed.status_code == 202, refreshed.text
    replaced = client.get(document_url).json()
    assert len(replaced["source_sessions"]) == 3
    assert replaced["update_available"] is False
    assert replaced["result"] != before["result"]
    blocked_move = client.patch(
        f"/interviews/live/sessions/{first['id']}", json={"project_id": None}
    )
    assert blocked_move.status_code == 409
    assert blocked_move.json()["detail"]["code"] == "brainstorm_session_already_synthesized"
    challenge = blocked_move.json()["detail"]["challenge"]
    confirmed_move = client.patch(
        f"/interviews/live/sessions/{first['id']}",
        json={
            "project_id": None,
            "invalidate_project_document": True,
            "invalidation_challenge": challenge,
        },
    )
    assert confirmed_move.status_code == 200, confirmed_move.text
    invalidated = client.get(document_url).json()
    assert invalidated["result"] is None
    assert invalidated["source_sessions"] == []
    assert invalidated["manual_markdown"] == replaced["manual_markdown"]


def test_project_brainstorm_bounds_and_active_job_unique_invariant(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    project_id = client.post("/projects", json={"name": "Bounded"}).json()["id"]
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(
        livemod,
        "_structure_brainstorm",
        lambda _pool, *, output_language, segments, **_kwargs: _result_for(
            segments, output_language
        ),
    )
    first = _completed_project_brainstorm(
        client, project_id=project_id, suffix="bound-first-0001", text="123456"
    )
    second = _completed_project_brainstorm(
        client, project_id=project_id, suffix="bound-second-0002", text="abcdef"
    )
    monkeypatch.setattr(livemod, "MAX_PROJECT_BRAINSTORM_INPUT_CHARS", 10)
    too_large = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
        json={
            "client_request_id": "bounded-too-large-0001",
            "output_language": "de",
            "session_ids": [first["id"], second["id"]],
            "include_all_completed": False,
            "expected_document_revision": 0,
            "schema_version": 1,
        },
    )
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "project_brainstorm_too_large"
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "brainstorm-owner@example.org"))
        project = session.get(Project, project_id)
        assert owner is not None and project is not None
        document = BrainstormProjectDocumentRow(
            org_id=owner.org_id,
            user_id=owner.id,
            project_id=project.id,
            revision=0,
            result={},
            source_sessions=[],
        )
        session.add(document)
        session.flush()
        common = {
            "org_id": owner.org_id,
            "user_id": owner.id,
            "project_id": project.id,
            "document_id": document.id,
            "request_sha256": "a" * 64,
            "mode": "selected",
            "output_language": "de",
            "base_revision": 0,
            "context_snapshot": {},
            "base_credits": 1,
            "status": "pending",
            "result": {},
            "error": "",
            "error_code": "",
        }
        session.add(
            BrainstormProjectSynthesisRow(
                **common,
                client_request_id="forced-overlap-one",
                action_id="forced-overlap-action-one",
            )
        )
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                BrainstormProjectSynthesisRow(
                    **common,
                    client_request_id="forced-overlap-two",
                    action_id="forced-overlap-action-two",
                )
            )
            session.flush()


def test_structural_terms_preserve_exact_spoken_labels_without_invention() -> None:
    raw = "Introduction, Methodology, Results, Discussion"
    segment = LiveCompanionSegmentRow(
        org_id=1,
        user_id=1,
        session_id=1,
        client_event_id="structural-segment-0001",
        payload_sha256="a" * 64,
        sequence=1,
        channel="microphone",
        speaker="Me",
        start_ms=0,
        end_ms=1000,
        text=raw,
        is_final=True,
    )

    def payload(summary: str) -> dict[str, Any]:
        return {
            "summary": summary,
            "themes": [{"title": "Präsentationsstruktur", "description": "Vier Abschnitte."}],
            "ideas": [],
            "open_questions": [],
            "decisions": [],
            "next_steps": [],
            "evidence": [
                {
                    "kind": "summary",
                    "index": 0,
                    "segment_id": segment.client_event_id,
                    "quote": raw,
                },
                {"kind": "themes", "index": 0, "segment_id": segment.client_event_id, "quote": raw},
            ],
        }

    protected = livemod._protected_structural_terms([segment])
    accepted = livemod._validated_brainstorm_result(
        payload(raw), [segment], protected_terms=protected
    )
    assert accepted is not None
    substituted = livemod._validated_brainstorm_result(
        payload("Production, Automation, Results, Discleption"),
        [segment],
        protected_terms=protected,
    )
    assert substituted is None
    absent_segment = segment.__class__(
        **{
            key: value
            for key, value in segment.__dict__.items()
            if not key.startswith("_") and key != "text"
        },
        text="Vier klar getrennte Teile.",
    )
    for invented_summary in ("Introduction und Results", "introduction und results"):
        invented = livemod._validated_brainstorm_result(
            payload(invented_summary),
            [absent_segment],
            protected_terms=livemod._protected_structural_terms([absent_segment]),
        )
        assert invented is None


def test_project_connections_require_evidence_from_every_named_cluster() -> None:
    session_ids = ["session-alpha", "session-beta", "session-gamma"]
    segment_text = {
        ("session-alpha", "segment-alpha"): "Alpha evidence.",
        ("session-beta", "segment-beta"): "Beta evidence.",
        ("session-gamma", "segment-gamma"): "Gamma evidence.",
    }
    evidence = {
        session_id: {
            "session_id": session_id,
            "segment_id": f"segment-{label}",
            "quote": f"{label.title()} evidence.",
        }
        for session_id, label in (
            ("session-alpha", "alpha"),
            ("session-beta", "beta"),
            ("session-gamma", "gamma"),
        )
    }
    payload: dict[str, Any] = {
        "title": "Separated clusters",
        "summary": "Three grounded sessions.",
        "summary_evidence": [evidence["session-alpha"]],
        "clusters": [
            {
                "id": "cluster-a",
                "title": "A",
                "summary": "Alpha and beta.",
                "session_ids": ["session-alpha", "session-beta"],
                "evidence": [evidence["session-alpha"], evidence["session-beta"]],
            },
            {
                "id": "cluster-b",
                "title": "B",
                "summary": "Gamma.",
                "session_ids": ["session-gamma"],
                "evidence": [evidence["session-gamma"]],
            },
        ],
        "connections": [
            {
                "title": "Unsupported bridge",
                "description": "This names B without evidence from B.",
                "cluster_ids": ["cluster-a", "cluster-b"],
                "evidence": [evidence["session-alpha"], evidence["session-beta"]],
            }
        ],
        "unconnected_cluster_ids": [],
        "source_session_ids": session_ids,
    }
    assert (
        livemod._validated_project_brainstorm_result(
            payload, selected_session_ids=session_ids, segment_text=segment_text
        )
        is None
    )
    payload["connections"] = []
    valid = livemod._validated_project_brainstorm_result(
        payload, selected_session_ids=session_ids, segment_text=segment_text
    )
    assert valid is not None
    assert valid.connections == []
    assert valid.unconnected_cluster_ids == ["cluster-a", "cluster-b"]


def test_project_connections_reject_trivial_exact_substrings() -> None:
    session_ids = ["session-short-a", "session-short-b"]
    segment_text = {
        ("session-short-a", "segment-short-a"): ".",
        ("session-short-b", "segment-short-b"): "a",
    }
    first = {"session_id": session_ids[0], "segment_id": "segment-short-a", "quote": "."}
    second = {"session_id": session_ids[1], "segment_id": "segment-short-b", "quote": "a"}
    payload: dict[str, Any] = {
        "title": "Short thoughts",
        "summary": "Two short raw thoughts remain separate.",
        "summary_evidence": [first],
        "clusters": [
            {
                "id": "short-a",
                "title": "A",
                "summary": "A short thought.",
                "session_ids": [session_ids[0]],
                "evidence": [first],
            },
            {
                "id": "short-b",
                "title": "B",
                "summary": "Another short thought.",
                "session_ids": [session_ids[1]],
                "evidence": [second],
            },
        ],
        "connections": [
            {
                "title": "Forced",
                "description": "Trivial substrings cannot support this relationship.",
                "cluster_ids": ["short-a", "short-b"],
                "evidence": [first, second],
            }
        ],
        "unconnected_cluster_ids": [],
        "source_session_ids": session_ids,
    }
    assert (
        livemod._validated_project_brainstorm_result(
            payload, selected_session_ids=session_ids, segment_text=segment_text
        )
        is None
    )
    payload["connections"] = []
    valid = livemod._validated_project_brainstorm_result(
        payload, selected_session_ids=session_ids, segment_text=segment_text
    )
    assert valid is not None
    assert valid.unconnected_cluster_ids == ["short-a", "short-b"]


def _synthesized_project(
    client: TestClient, monkeypatch: Any, *, suffix: str
) -> tuple[int, dict[str, Any], dict[str, Any], str]:
    monkeypatch.setattr(
        appmod, "_build_scoped_pool", lambda *_args, **_kwargs: SimpleNamespace(cancel_check=None)
    )
    monkeypatch.setattr(
        livemod,
        "_structure_brainstorm",
        lambda _pool, *, output_language, segments, **_kwargs: _result_for(
            segments, output_language
        ),
    )
    project_id = client.post("/projects", json={"name": f"Project {suffix}"}).json()["id"]
    first_created = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": f"project-brainstorm-{suffix}-first",
            "title": f"Session {suffix} first",
            "purpose": "brainstorm",
            "project_id": project_id,
            "language": "de",
        },
    )
    assert first_created.status_code == 201, first_created.text
    first_id = first_created.json()["id"]
    _append(
        client,
        first_id,
        event_id=f"project-segment-{suffix}-first-a",
        text="A first grounded product thought.",
        start_ms=0,
    )
    _append(
        client,
        first_id,
        event_id=f"project-segment-{suffix}-first-b",
        text="A later grounded detail has the preferred cutoff.",
        start_ms=1000,
    )
    first_state = client.get(f"/interviews/live/sessions/{first_id}").json()
    first_completed = client.post(
        f"/interviews/live/sessions/{first_id}/complete",
        json={
            "client_request_id": f"project-structure-{suffix}-first",
            "output_language": "de",
            "context_through_sequence": first_state["last_segment_sequence"],
            "schema_version": 1,
        },
    )
    assert first_completed.status_code == 202, first_completed.text
    first = client.get(f"/interviews/live/sessions/{first_id}").json()
    second = _completed_project_brainstorm(
        client,
        project_id=project_id,
        suffix=f"{suffix}-second",
        text="A second grounded research thought.",
    )
    monkeypatch.setattr(
        livemod,
        "_structure_project_brainstorm",
        lambda _pool, *, selected_session_ids, segment_text, **_kwargs: _cross_session_result(
            selected_session_ids, segment_text
        ),
    )
    document_url = f"/interviews/live/brainstorm-projects/{project_id}/document"
    revision = client.get(document_url).json()["revision"]
    response = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
        json={
            "client_request_id": f"initial-project-synthesis-{suffix}",
            "output_language": "de",
            "session_ids": [first["id"], second["id"]],
            "include_all_completed": False,
            "expected_document_revision": revision,
            "schema_version": 1,
        },
    )
    assert response.status_code == 202, response.text
    assert (
        client.get(
            f"/interviews/live/brainstorm-projects/{project_id}/syntheses/{response.json()['id']}"
        ).json()["status"]
        == "completed"
    )
    return (project_id, first, second, document_url)


def test_project_document_freshness_tracks_new_versions_and_late_completion(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    project_id, first, _second, document_url = _synthesized_project(
        client, monkeypatch, suffix="freshness-0001"
    )
    assert client.get(document_url).json()["update_available"] is False
    first_segment_sequence = next(
        event["sequence"]
        for event in client.get(f"/interviews/live/sessions/{first['id']}/events").json()["events"]
        if event["type"] == "segment"
    )
    lower_cutoff = client.post(
        f"/interviews/live/sessions/{first['id']}/brainstorms",
        json={
            "client_request_id": "later-created-lower-cutoff-0001",
            "output_language": "de",
            "context_through_sequence": first_segment_sequence,
            "schema_version": 1,
        },
    )
    assert lower_cutoff.status_code == 202, lower_cutoff.text
    assert client.get(document_url).json()["update_available"] is False
    newer = client.post(
        f"/interviews/live/sessions/{first['id']}/brainstorms",
        json={
            "client_request_id": "newer-individual-version-0001",
            "output_language": "de",
            "context_through_sequence": first["last_segment_sequence"],
            "schema_version": 1,
        },
    )
    assert newer.status_code == 202, newer.text
    assert client.get(document_url).json()["update_available"] is True
    document = client.get(document_url).json()
    refreshed = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
        json={
            "client_request_id": "freshness-refresh-project-0001",
            "output_language": "de",
            "session_ids": [],
            "include_all_completed": True,
            "expected_document_revision": document["revision"],
            "schema_version": 1,
        },
    )
    assert refreshed.status_code == 202, refreshed.text
    assert client.get(document_url).json()["update_available"] is False
    recording = client.post(
        "/interviews/live/sessions",
        json={
            "client_session_id": "prestructured-recording-0001",
            "title": "Prestructured",
            "purpose": "brainstorm",
            "project_id": project_id,
            "language": "de",
        },
    ).json()
    appended = _append(
        client,
        recording["id"],
        event_id="prestructured-segment-0001",
        text="A grounded thought becomes eligible only after completion.",
        start_ms=0,
    )
    completion_payload = {
        "client_request_id": "prestructured-receipt-0001",
        "output_language": "de",
        "context_through_sequence": appended["last_event_sequence"],
        "schema_version": 1,
    }
    structured = client.post(
        f"/interviews/live/sessions/{recording['id']}/brainstorms", json=completion_payload
    )
    assert structured.status_code == 202, structured.text
    assert client.get(document_url).json()["update_available"] is False
    completed = client.post(
        f"/interviews/live/sessions/{recording['id']}/complete", json=completion_payload
    )
    assert completed.status_code == 200, completed.text
    assert client.get(document_url).json()["update_available"] is True
    moved_out = client.patch(
        f"/interviews/live/sessions/{recording['id']}", json={"project_id": None}
    )
    assert moved_out.status_code == 200, moved_out.text
    assert client.get(document_url).json()["update_available"] is False
    delete_candidate = _completed_project_brainstorm(
        client,
        project_id=project_id,
        suffix="freshness-delete-candidate-0001",
        text="A removable unincorporated grounded thought.",
    )
    assert client.get(document_url).json()["update_available"] is True
    deleted_candidate = client.request(
        "DELETE", f"/interviews/live/sessions/{delete_candidate['id']}"
    )
    assert deleted_candidate.status_code == 200, deleted_candidate.text
    assert client.get(document_url).json()["update_available"] is False


def test_session_delete_challenge_cleans_current_pending_and_historical_sources(
    settings: Settings, monkeypatch: Any
) -> None:
    client, _token = _client(settings)
    project_id, first, _second, document_url = _synthesized_project(
        client, monkeypatch, suffix="delete-0001"
    )
    document = client.get(document_url).json()
    manual = client.patch(
        document_url,
        json={
            "manual_markdown": "# Keep this human note",
            "expected_revision": document["revision"],
        },
    )
    assert manual.status_code == 200, manual.text
    monkeypatch.setattr(livemod, "_structure_project_brainstorm", lambda *_args, **_kwargs: None)
    failed = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
        json={
            "client_request_id": "failed-before-session-delete-0001",
            "output_language": "de",
            "session_ids": [],
            "include_all_completed": True,
            "expected_document_revision": manual.json()["revision"],
            "schema_version": 1,
        },
    )
    assert failed.status_code == 202, failed.text
    assert (
        client.get(
            f"/interviews/live/brainstorm-projects/{project_id}/syntheses/{failed.json()['id']}"
        ).json()["status"]
        == "failed"
    )
    retry = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses/{failed.json()['id']}/retry",
        json={
            "client_request_id": "retry-before-session-delete-0001",
            "expected_document_revision": manual.json()["revision"],
        },
    )
    assert retry.status_code == 202, retry.text
    assert (
        client.get(
            f"/interviews/live/brainstorm-projects/{project_id}/syntheses/{retry.json()['id']}"
        ).json()["status"]
        == "failed"
    )
    monkeypatch.setattr(
        appmod, "_execute_project_brainstorm_synthesis_inline", lambda _synthesis_id: None
    )
    pending = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses",
        json={
            "client_request_id": "pending-before-session-delete-0001",
            "output_language": "de",
            "session_ids": [],
            "include_all_completed": True,
            "expected_document_revision": manual.json()["revision"],
            "schema_version": 1,
        },
    )
    assert pending.status_code == 202, pending.text
    first_attempt = client.request("DELETE", f"/interviews/live/sessions/{first['id']}")
    assert first_attempt.status_code == 409, first_attempt.text
    detail = first_attempt.json()["detail"]
    assert detail["code"] == "brainstorm_session_delete_requires_cleanup"
    assert detail["challenge"]["affected_document_count"] == 1
    assert detail["challenge"]["affected_synthesis_count"] == 4
    assert detail["challenge"]["pending_synthesis_count"] == 1
    cancelled = client.post(
        f"/interviews/live/brainstorm-projects/{project_id}/syntheses/{pending.json()['id']}/cancel"
    )
    assert cancelled.status_code == 200, cancelled.text
    stale = client.request(
        "DELETE",
        f"/interviews/live/sessions/{first['id']}",
        json={"confirm_project_cleanup": True, "cleanup_challenge": detail["challenge"]},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "brainstorm_delete_challenge_stale"
    challenge = stale.json()["detail"]["challenge"]
    deleted = client.request(
        "DELETE",
        f"/interviews/live/sessions/{first['id']}",
        json={"confirm_project_cleanup": True, "cleanup_challenge": challenge},
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/interviews/live/sessions/{first['id']}").status_code == 404
    preserved = client.get(document_url).json()
    assert preserved["manual_markdown"] == "# Keep this human note"
    assert preserved["result"] is None
    assert preserved["source_sessions"] == []
    history = client.get(f"/interviews/live/brainstorm-projects/{project_id}/syntheses").json()
    assert history["total"] == 0
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "brainstorm-owner@example.org"))
        assert owner is not None
        tables, _inventory = _privacy_export_tables(session, user=owner)
        assert tables.get("brainstorm_project_syntheses", []) == []
        exported_documents = tables.get("brainstorm_project_documents", [])
        assert exported_documents[0]["manual_markdown"] == "# Keep this human note"
        assert exported_documents[0]["result"] == {}


def test_project_delete_requires_interactive_workspace_owner(settings: Settings) -> None:
    client, _token = _client(settings)
    project_id = client.post("/projects", json={"name": "Shared private project"}).json()["id"]
    with db_session() as session:
        owner = session.scalar(select(User).where(User.email == "brainstorm-owner@example.org"))
        assert owner is not None
        add_member(session, owner.org_id, "project-delete-member@example.org", PASSWORD, "member")
        member_token = authenticate(session, "project-delete-member@example.org", PASSWORD)
    member_client = TestClient(client.app)
    member_client.headers["Authorization"] = f"Bearer {member_token}"
    member_document = member_client.get(
        f"/interviews/live/brainstorm-projects/{project_id}/document"
    )
    assert member_document.status_code == 200, member_document.text
    member_delete = member_client.delete(f"/projects/{project_id}")
    assert member_delete.status_code == 403
    assert member_delete.json()["detail"]["code"] == "project_delete_owner_required"
    key = client.post(
        "/auth/api-keys", json={"name": "project deleter", "scopes": ["research:write"]}
    )
    assert key.status_code == 201, key.text
    robot = TestClient(client.app)
    robot.headers["Authorization"] = f"Bearer {key.json()['api_key']}"
    api_delete = robot.delete(f"/projects/{project_id}")
    assert api_delete.status_code == 403
    assert api_delete.json()["detail"]["code"] == "api_key_endpoint_forbidden"
    assert client.get("/projects").status_code == 200
    assert client.delete(f"/projects/{project_id}").status_code == 200
