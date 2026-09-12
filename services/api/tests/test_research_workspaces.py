"""Connected control, extraction, evidence and living-review workspaces."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.core.db import (
    BackgroundJobRow,
    ExtractionRow,
    Run,
    RunEvent,
    db_session,
)


def _client() -> TestClient:
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "name": "Researcher",
            "email": "connected-workspaces@example.org",
            "password": "StrongPass123!",
            "org_name": "Connected Workspaces",
        },
    )
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _project_run(client: TestClient) -> tuple[int, int]:
    project_id = client.post(
        "/projects",
        json={
            "name": "Living evidence review",
            "question": "Which methods improve evidence synthesis?",
        },
    ).json()["id"]
    run_id = client.post(
        f"/projects/{project_id}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    return (project_id, run_id)


def test_control_room_reconstructs_live_run_state(corpus) -> None:
    client = _client()
    _, run_id = _project_run(client)
    control = client.get(f"/runs/{run_id}/control-room")
    assert control.status_code == 200
    payload = control.json()
    assert payload["run_id"] == run_id
    assert payload["records"]["identified"] >= 1
    assert payload["decisions"]["total"] >= 1
    assert payload["stages"][0]["id"] == "protocol_synthesis"
    assert 0 <= payload["overall_progress"] <= 1


def test_control_room_sanitizes_worker_retry_details(corpus) -> None:
    client = _client()
    _, run_id = _project_run(client)
    with db_session() as session:
        session.add(
            BackgroundJobRow(
                task="_execute",
                args=[run_id],
                kwargs={},
                status="queued",
                attempts=1,
                max_attempts=3,
                last_error="JobTimeoutError; see worker logs",
            )
        )
    worker = client.get(f"/runs/{run_id}/control-room").json()["worker"]
    assert worker["status"] == "queued"
    assert (
        worker["last_error"]
        == "A previous attempt was interrupted. The run will retry automatically."
    )
    assert "JobTimeoutError" not in worker["last_error"]
    assert "worker logs" not in worker["last_error"]


def test_control_room_freezes_completed_stage_durations(corpus) -> None:
    client = _client()
    _, run_id = _project_run(client)
    finished_at = datetime.now(UTC) - timedelta(hours=1)
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        report_events = list(
            session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.stage == "report")
                .order_by(RunEvent.id)
            )
        )
        report_event = report_events[0] if report_events else None
        assert report_event is not None
        for duplicate in report_events[1:]:
            session.delete(duplicate)
        report_event.created_at = finished_at - timedelta(seconds=2)
        run.finished_at = finished_at
    payload = client.get(f"/runs/{run_id}/control-room").json()
    report = next(stage for stage in payload["stages"] if stage["id"] == "report")
    assert report["status"] == "completed"
    assert report["duration_seconds"] == 2


def test_extraction_schema_and_human_review_are_durable(corpus) -> None:
    client = _client()
    _, run_id = _project_run(client)
    schema = client.patch(
        f"/runs/{run_id}/extraction-schema",
        json={
            "name": "PICO outcomes",
            "fields": ["population", "intervention", "effect_size"],
            "reviewer_mode": "double",
            "instructions": "Prefer the primary endpoint.",
        },
    )
    assert schema.status_code == 200
    assert schema.json()["reviewer_mode"] == "double"
    table = client.get(f"/runs/{run_id}/extraction").json()
    assert table["schema"]["name"] == "PICO outcomes"
    assert table["schema"]["fields"] == ["population", "intervention", "effect_size"]
    assert table["summary"]["works"] == 0
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        session.add(
            ExtractionRow(
                org_id=org_id,
                run_id=run_id,
                work_id="W1",
                status="done",
                model="test",
                payload={
                    "effect_size": {
                        "value": "0.31",
                        "quote": "The primary effect size was 0.31.",
                        "page": 7,
                        "verified": True,
                        "source": "model",
                    }
                },
            )
        )
    first_review = client.patch(
        f"/runs/{run_id}/extraction/W1/review",
        json={
            "field": "effect_size",
            "round": 1,
            "verdict": "confirmed",
            "value": "0.31",
            "note": "Matches the results table.",
        },
    )
    assert first_review.status_code == 200
    assert first_review.json()["payload"]["review_status"] == "confirmed"
    second_review = client.patch(
        f"/runs/{run_id}/extraction/W1/review",
        json={
            "field": "effect_size",
            "round": 2,
            "verdict": "confirmed",
            "value": "0.28",
            "note": "Used the adjusted model.",
        },
    )
    assert second_review.status_code == 200
    assert second_review.json()["payload"]["review_status"] == "conflict"
    reviewed_table = client.get(f"/runs/{run_id}/extraction").json()
    assert reviewed_table["summary"]["works"] == 1
    assert reviewed_table["summary"]["conflicts"] == 1
    reviews = reviewed_table["rows"][0]["payload"]["effect_size"]["reviews"]
    assert [review["round"] for review in reviews] == [1, 2]


def test_run_evidence_graph_resolves_work_titles(corpus) -> None:
    client = _client()
    _, run_id = _project_run(client)
    claim = client.post(
        f"/runs/{run_id}/claims",
        json={
            "text": "Self-attention improves parallel sequence modelling.",
            "section": "Discussion",
            "confidence": "moderate",
        },
    )
    assert claim.status_code == 201
    claim_id = claim.json()["id"]
    linked = client.post(
        f"/runs/{run_id}/claims/{claim_id}/evidence",
        json={
            "target_type": "work",
            "target_id": "W1",
            "relationship": "supports",
            "locator": "Abstract",
            "verified": True,
        },
    )
    assert linked.status_code == 201
    graph = client.get(f"/runs/{run_id}/evidence-graph").json()
    assert graph["summary"]["claims"] == 1
    assert graph["summary"]["evidence"] == 1
    assert graph["summary"]["unsupported"] == 0
    assert graph["claims"][0]["impact"] == "stable"
    assert graph["evidence"][0]["title"] != "W1"
