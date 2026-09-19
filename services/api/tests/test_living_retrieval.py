"""Living-review regressions for native multi-source retrieval."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import sixsentences_server.api.app as app_module
from sixsentences_server.api.app import create_app
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    ProtocolRow,
    Run,
    RunEvent,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
    db_session,
)
from sixsentences_server.core.models import ReviewProtocol, WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus


def _authed(app: object, email: str, org: str) -> TestClient:
    client = TestClient(app)  # type: ignore[arg-type]
    body = {
        "email": email,
        "password": "StrongPass123!",
        "org_name": org,
        "name": "Test",
    }
    token = client.post("/auth/register", json=body).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def _stub_run_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[object, tuple[object, ...], dict[str, object]]]:
    scheduled: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def fake_enqueue(
        background: object,
        task: object,
        *args: object,
        **kwargs: object,
    ) -> int:
        scheduled.append((task, args, kwargs))
        return 1

    monkeypatch.setattr(app_module, "enqueue_job", fake_enqueue)
    return scheduled


def _attach_protocol(
    session: Session,
    run: Run,
    query: str = "intervention outcomes",
) -> ReviewProtocol:
    protocol = ReviewProtocol(
        question=run.question,
        query_string=query,
        inclusion_criteria=["Human intervention studies"],
        exclusion_criteria=["Animal-only studies"],
    )
    row = ProtocolRow(
        org_id=run.org_id,
        project_id=run.project_id,
        version=1,
        payload=protocol.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    run.protocol_id = row.id
    return protocol


def test_living_pubmed_source_defaults_from_baseline_and_controls_refresh(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIX_PUBMED_ENABLED", "1")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", "living-refresh@example.org")
    get_settings.cache_clear()
    scheduled = _stub_run_queue(monkeypatch)
    client = _authed(create_app(), "living-pubmed@lab.org", "Living PubMed")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        baseline = Run(
            org_id=org_id,
            question="Does the intervention improve outcomes?",
            status="completed",
            config={
                "mode": "search",
                "living": True,
                "pubmed": True,
                "query": "intervention outcomes",
                "screen": False,
            },
        )
        session.add(baseline)
        session.flush()
        baseline_protocol = _attach_protocol(session, baseline)
        baseline_public_id = baseline.public_id

    workspace = client.get(f"/runs/{baseline_public_id}/living")
    assert workspace.status_code == 200
    assert "pubmed" in workspace.json()["watch_sources"]
    partial_update = client.put(
        f"/runs/{baseline_public_id}/living/settings",
        json={"enabled": True},
    )
    assert partial_update.status_code == 200
    assert "pubmed" in partial_update.json()["watch_sources"]

    enabled_refresh = client.post(
        f"/runs/{baseline_public_id}/living/refresh",
        json={"scope": "delta"},
    )
    assert enabled_refresh.status_code == 202, enabled_refresh.text
    with db_session() as session:
        child = session.scalar(
            select(Run).where(Run.public_id == enabled_refresh.json()["public_id"])
        )
        assert child is not None
        assert child.config["pubmed"] is True
        assert child.config["live"] is True
        assert child.config["screen"] is True
        assert child.config["snowball"] is True
        assert child.config["query"] == baseline_protocol.query_string
        child.status = "completed"

    saved = client.put(
        f"/runs/{baseline_public_id}/living/settings",
        json={
            "enabled": True,
            "cadence": "monthly",
            "auto_screen": False,
            "notify": True,
            "watch_sources": ["citations", "retractions"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert "pubmed" not in saved.json()["watch_sources"]

    disabled_refresh = client.post(
        f"/runs/{baseline_public_id}/living/refresh",
        json={"scope": "delta"},
    )
    assert disabled_refresh.status_code == 202, disabled_refresh.text
    with db_session() as session:
        child = session.scalar(
            select(Run).where(Run.public_id == disabled_refresh.json()["public_id"])
        )
        assert child is not None
        assert child.config["pubmed"] is False
        assert child.config["live"] is False
        assert child.config["screen"] is False
        assert child.config["snowball"] is False
    assert len(scheduled) == 2
    for _task, _args, kwargs in scheduled:
        assert kwargs["frozen_protocol"] == baseline_protocol.model_dump(mode="json")
    get_settings.cache_clear()


def test_living_refresh_is_single_flight_and_advances_only_after_completion(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled = _stub_run_queue(monkeypatch)
    client = _authed(create_app(), "living-single-flight@lab.org", "Living Single Flight")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        baseline = Run(
            org_id=org_id,
            question="Does the intervention improve outcomes?",
            status="completed",
            config={"mode": "search", "living": True},
        )
        session.add(baseline)
        session.flush()
        _attach_protocol(session, baseline)
        baseline_public_id = baseline.public_id

    first = client.post(
        f"/runs/{baseline_public_id}/living/refresh",
        json={"scope": "delta"},
    )
    second = client.post(
        f"/runs/{baseline_public_id}/living/refresh",
        json={"scope": "full"},
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["public_id"] == second.json()["public_id"]
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert second.json()["scope"] == "delta"
    assert len(scheduled) == 1

    with db_session() as session:
        baseline = session.scalar(select(Run).where(Run.public_id == baseline_public_id))
        child = session.scalar(select(Run).where(Run.public_id == first.json()["public_id"]))
        assert baseline is not None and child is not None
        monitor = baseline.config["living_monitor"]
        assert monitor["active_refresh_run"] == child.public_id
        assert "last_checked_at" not in monitor
        assert "last_refresh_run" not in monitor
        child.status = "completed"
        child.finished_at = datetime.now(UTC)
        app_module._record_living_refresh_completion(session, child)

    with db_session() as session:
        baseline = session.scalar(select(Run).where(Run.public_id == baseline_public_id))
        child = session.scalar(select(Run).where(Run.public_id == first.json()["public_id"]))
        assert baseline is not None and child is not None
        app_module._record_living_refresh_completion(session, child)
        monitor = baseline.config["living_monitor"]
        assert "active_refresh_run" not in monitor
        assert monitor["last_refresh_run"] == first.json()["public_id"]
        assert datetime.fromisoformat(monitor["last_checked_at"]).tzinfo is not None
        completion_events = session.scalars(
            select(RunEvent).where(
                RunEvent.run_id == baseline.id,
                RunEvent.event == "living_refresh_completed",
            )
        ).all()
        assert len(completion_events) == 1
        previous_checked = monitor["last_checked_at"]
        delayed_child = Run(
            org_id=org_id,
            question=baseline.question,
            status="completed",
            finished_at=datetime.fromisoformat(previous_checked) - timedelta(minutes=1),
            config={"living_refresh_baseline": baseline.public_id},
        )
        session.add(delayed_child)
        session.flush()
        app_module._record_living_refresh_completion(session, delayed_child)
        assert baseline.config["living_monitor"]["last_checked_at"] == previous_checked
        assert baseline.config["living_monitor"]["last_refresh_run"] == first.json()["public_id"]


def test_living_workspace_uses_only_completed_children_for_inclusion_deltas(
    corpus: DuckDBCorpus,
) -> None:
    client = _authed(create_app(), "living-terminal-delta@lab.org", "Living Terminal Delta")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        baseline = Run(
            org_id=org_id,
            question="A living review",
            status="completed",
            config={"mode": "search", "living": True},
        )
        session.add(baseline)
        session.flush()
        work = WorkRecord(id="W9001", title="Baseline included work")
        session.add(
            WorkRow(
                id=work.id,
                title=work.title,
                payload=work.model_dump(mode="json"),
            )
        )
        session.flush()
        session.add(
            SourceRecordRow(
                org_id=org_id,
                run_id=baseline.id,
                work_id=work.id,
                source="openalex",
            )
        )
        session.add(
            ScreeningDecisionRow(
                org_id=org_id,
                run_id=baseline.id,
                work_id=work.id,
                reviewer="human:test",
                verdict="include",
                reason="eligible",
            )
        )
        child = Run(
            org_id=org_id,
            question=baseline.question,
            status="pending",
            config={
                "mode": "search",
                "parent_run": baseline.public_id,
                "living_refresh_baseline": baseline.public_id,
            },
        )
        session.add(child)
        session.flush()
        baseline_public_id = baseline.public_id
        child_public_id = child.public_id

    pending = client.get(f"/runs/{baseline_public_id}/living")
    assert pending.status_code == 200
    assert pending.json()["checks"][0]["status"] == "pending"
    assert pending.json()["checks"][0]["removed_includes"] == []
    assert pending.json()["last_checked_at"] is None

    with db_session() as session:
        child = session.scalar(select(Run).where(Run.public_id == child_public_id))
        assert child is not None
        child.status = "failed"
        child.finished_at = datetime.now(UTC)
    failed = client.get(f"/runs/{baseline_public_id}/living")
    assert failed.json()["checks"][0]["removed_includes"] == []
    assert failed.json()["last_checked_at"] is None

    with db_session() as session:
        child = session.scalar(select(Run).where(Run.public_id == child_public_id))
        assert child is not None
        child.status = "completed"
    completed = client.get(f"/runs/{baseline_public_id}/living")
    assert [item["work_id"] for item in completed.json()["checks"][0]["removed_includes"]] == [
        "W9001"
    ]
    assert completed.json()["last_checked_at"] is not None
