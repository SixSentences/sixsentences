"""The browsable ledger must not turn unfinished candidates into final output."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    Run,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
    db_session,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.reporting.exports import works_for_run


def _run_client(
    status: str, config: dict[str, object], *, decisions: bool = False
) -> tuple[TestClient, str]:
    client = TestClient(create_app())
    registered = client.post(
        "/auth/register",
        json={
            "email": "selection@example.org",
            "password": "StrongPass123!",
            "org_name": "Selection fixture",
            "name": "Test",
        },
    )
    assert registered.status_code == 201, registered.text
    client.headers["Authorization"] = f"Bearer {registered.json()['token']}"
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        run = Run(
            org_id=org_id,
            question="Synthetic transformer screening",
            status=status,
            config=config,
        )
        session.add(run)
        session.flush()
        for number, verdict in enumerate(("include", "unsure", "exclude"), start=1):
            work = WorkRecord(
                id=f"selection-{number}",
                title=f"Synthetic transformer study {number}",
                cited_by_count=number,
            )
            session.add(WorkRow(id=work.id, title=work.title, payload=work.model_dump()))
            session.flush()
            session.add(
                SourceRecordRow(org_id=org_id, run_id=run.id, work_id=work.id, source="fixture")
            )
            if decisions:
                session.add(
                    ScreeningDecisionRow(
                        org_id=org_id,
                        run_id=run.id,
                        work_id=work.id,
                        reviewer="human:fixture",
                        verdict=verdict,
                        reason="Synthetic decision",
                    )
                )
        public_id = run.public_id
    return client, public_id


@pytest.mark.parametrize(
    "status", ["pending", "running", "awaiting_protocol_approval", "paused", "cancelled", "failed"]
)
@pytest.mark.parametrize("persisted", [False, True])
def test_incomplete_runs_preserve_candidates_without_final_selection(
    settings: Settings, status: str, persisted: bool
) -> None:
    config: dict[str, object] = {"screen": True, "paper_limit": 1}
    if persisted:
        config.update(paper_selection_ids=["selection-1"], paper_selection_finalized=True)
    client, run_id = _run_client(status, config)

    response = client.get(f"/runs/{run_id}/works")
    assert response.status_code == 200
    data = response.json()
    assert data["identified_total"] == data["total"] == 3
    assert data["evidence_counts"]["unscreened"] == 3
    assert data["selected_total"] == data["result_evidence_counts"]["retained"] == 0
    assert all(not work["selected"] for work in data["works"])
    assert all(work["evidence_state"] == "unscreened" for work in data["works"])


@pytest.mark.parametrize("limit", [0, 10])
def test_api_finalized_empty_selection_never_falls_back_to_candidates(
    settings: Settings, limit: int
) -> None:
    client, run_id = _run_client(
        "completed",
        {"paper_limit": limit, "paper_selection_ids": [], "paper_selection_finalized": True},
    )
    data = client.get(f"/runs/{run_id}/works").json()
    assert data["total"] == 3
    assert data["selected_total"] == 0
    assert all(not work["selected"] for work in data["works"])


def test_completed_selection_uses_real_non_excluded_ids_and_separate_counts(
    settings: Settings,
) -> None:
    client, run_id = _run_client(
        "completed",
        {
            "paper_limit": 3,
            "paper_selection_ids": ["selection-1", "selection-3", "not-in-this-run"],
            "paper_selection_finalized": True,
        },
        decisions=True,
    )
    data = client.get(f"/runs/{run_id}/works").json()
    assert data["total"] == 3
    assert data["selected_total"] == 1
    assert data["evidence_counts"]["unsure"] == 1
    assert data["evidence_counts"]["exclude"] == 1
    assert data["result_evidence_counts"]["confirmed_include"] == 1
    assert data["result_evidence_counts"]["unsure"] == 0
    assert {work["id"] for work in data["works"] if work["selected"]} == {"selection-1"}


def test_completed_legacy_selection_matches_bounded_export_working_set(
    settings: Settings,
) -> None:
    client, run_id = _run_client("completed", {"paper_limit": 1})
    with db_session() as session:
        run = session.scalar(select(Run).where(Run.public_id == run_id))
        assert run is not None
        expected = {work.id for work in works_for_run(session, run.id, org_id=run.org_id)}
    data = client.get(f"/runs/{run_id}/works").json()
    assert data["total"] == 3
    assert data["selected_total"] == 1
    assert {work["id"] for work in data["works"] if work["selected"]} == expected
    # A completed discovery run may select candidates, never claim they were screened.
    assert data["result_evidence_counts"]["unscreened"] == 1
    assert data["result_evidence_counts"]["retained"] == 0
