"""Extraction charges only durable results and releases unfinished holds."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.auth import AuthContext
from sixsentences_server.core.db import (
    BackgroundJobRow,
    CapacityReservationRow,
    CreditEventRow,
    ExtractionRow,
    LLMCallRow,
    Org,
    Run,
    RunEvent,
    db_session,
    init_db,
)
from sixsentences_server.core.entitlements import (
    begin_ai_action,
    charge_credits,
    release_stale_ai_actions,
)
from sixsentences_server.jobs import DatabaseWorker, terminalize_extraction_action
from sixsentences_server.llm.base import BudgetExceededError, LLMUsage

api_app = import_module("sixsentences_server.api.app")


def _batch(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, *, legacy: bool = False
) -> tuple[int, int, str, list[str], list[int]]:
    """Use a nonlinear frozen tariff to expose accidental per-row minimums."""
    del settings
    init_db()
    work_ids = ["W-EXTRACT-1", "W-EXTRACT-2", "W-EXTRACT-3"]
    costs = [0, 10, 17, 24]
    with db_session() as session:
        org = Org(name="Synthetic extraction capacity", plan="community")
        session.add(org)
        session.flush()
        run = Run(org_id=org.id, question="Synthetic evidence", status="completed")
        session.add(run)
        session.flush()
        for work_id in work_ids:
            session.add(ExtractionRow(org_id=org.id, run_id=run.id, work_id=work_id))
        action = begin_ai_action(
            session,
            org,
            action="extraction",
            resource_type="run_extraction",
            resource_id=run.id,
            run_id=run.id,
            reserve_credits=0 if legacy else costs[-1],
        )
        if legacy:
            charge_credits(session, org, action="extraction", credits=24, run_id=run.id)
        scope = (org.id, run.id, action.action_id, work_ids, costs)
    monkeypatch.setattr(api_app, "_build_pool", lambda _settings: SimpleNamespace())
    monkeypatch.setattr(
        api_app,
        "works_for_run",
        lambda *_args, **_kwargs: [SimpleNamespace(id=work_id) for work_id in work_ids],
    )
    return scope


def _result(pool, _fields, work, _pages):
    """Record a small real-ledger usage receipt without contacting a provider."""
    pool.on_usage(
        LLMUsage(
            task="extraction",
            provider="fake",
            model="synthetic",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.003,
            cost_source="provider",
            duration_ms=1,
        )
    )
    return ({"finding": {"value": work.id, "verified": True}}, "synthetic")


def _ledger(run_id: int) -> list[int]:
    with db_session() as session:
        return list(
            session.scalars(
                select(CreditEventRow.credits)
                .where(CreditEventRow.run_id == run_id, CreditEventRow.action == "extraction")
                .order_by(CreditEventRow.id)
            )
        )


@pytest.mark.parametrize("failure", ["provider", "budget"])
def test_extraction_partial_results_charge_cumulative_cost_only(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    _, run_id, action_id, work_ids, costs = _batch(settings, monkeypatch)

    def extract(pool, fields, work, pages):
        if work.id == work_ids[1]:
            _result(pool, fields, work, pages)
            if failure == "budget":
                raise BudgetExceededError("synthetic protected budget")
            raise RuntimeError("synthetic provider failure")
        return _result(pool, fields, work, pages)

    monkeypatch.setattr(api_app, "extract_for_work", extract)
    api_app._execute_extraction(
        run_id, ["finding"], action_id, work_ids=work_ids, capacity_costs=costs
    )
    assert _ledger(run_id) == ([10] if failure == "budget" else [10, 7])
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert action is not None and action.remaining_credits == 0
        assert action.status == ("paused" if failure == "budget" else "settled")
        assert session.get(Run, run_id).status == "completed"
        receipts = session.scalars(
            select(LLMCallRow).where(LLMCallRow.action_id == action_id)
        ).all()
        assert sum(row.cost_usd for row in receipts) == pytest.approx(
            0.006 if failure == "budget" else 0.009
        )


def test_hard_worker_retry_skips_durable_results_and_does_not_recharge(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run_id, action_id, work_ids, costs = _batch(settings, monkeypatch)

    class SubprocessExit(BaseException):
        """Model a hard exit beyond the ordinary exception finalizer."""

    def interrupted(pool, fields, work, pages):
        if work.id == work_ids[1]:
            raise SubprocessExit()
        return _result(pool, fields, work, pages)

    monkeypatch.setattr(api_app, "extract_for_work", interrupted)
    with pytest.raises(SubprocessExit):
        api_app._execute_extraction(
            run_id, ["finding"], action_id, work_ids=work_ids, capacity_costs=costs
        )
    assert _ledger(run_id) == [10]
    called: list[str] = []

    def resumed(pool, fields, work, pages):
        called.append(work.id)
        return _result(pool, fields, work, pages)

    monkeypatch.setattr(api_app, "extract_for_work", resumed)
    api_app._execute_extraction(
        run_id, ["finding"], action_id, work_ids=work_ids, capacity_costs=costs
    )
    api_app._execute_extraction(
        run_id, ["finding"], action_id, work_ids=work_ids, capacity_costs=costs
    )
    assert called == work_ids[1:]
    assert _ledger(run_id) == [10, 7, 7]
    with db_session() as session:
        assert session.query(ExtractionRow).filter_by(run_id=run_id, status="done").count() == 3
        assert (
            session.query(RunEvent).filter_by(run_id=run_id, event="extraction_done").count() == 1
        )


def test_unexpected_extraction_setup_error_releases_hold_without_charging(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run_id, action_id, work_ids, costs = _batch(settings, monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise OSError("synthetic local storage failure")

    monkeypatch.setattr(api_app, "works_for_run", unavailable)
    with pytest.raises(OSError):
        api_app._execute_extraction(
            run_id, ["finding"], action_id, work_ids=work_ids, capacity_costs=costs
        )
    assert _ledger(run_id) == []
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert action is not None and action.status == "failed" and (action.remaining_credits == 0)
        assert session.get(Run, run_id).status == "completed"


def test_legacy_prepaid_extraction_job_is_not_charged_again(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run_id, action_id, _, _ = _batch(settings, monkeypatch, legacy=True)
    monkeypatch.setattr(api_app, "extract_for_work", _result)
    api_app._execute_extraction(run_id, ["finding"], action_id)
    assert _ledger(run_id) == [24]


@pytest.mark.parametrize("recovery", [False, True])
def test_final_extraction_worker_failure_closes_exact_action_not_completed_parent(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, recovery: bool
) -> None:
    org_id, run_id, action_id, work_ids, costs = _batch(settings, monkeypatch)
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    now = datetime.now(UTC)
    with db_session() as session:
        other = CapacityReservationRow(
            action_id="synthetic-unrelated-action",
            org_id=org_id,
            run_id=run_id,
            resource_type="run",
            resource_id=str(run_id),
            action="search",
            reserved_credits=11,
            remaining_credits=11,
        )
        session.add(other)
        row = BackgroundJobRow(
            org_id=org_id,
            task="_execute_extraction",
            lane="documents",
            args=[run_id, ["finding"], action_id],
            kwargs={"work_ids": work_ids, "capacity_costs": costs},
            max_attempts=1,
            attempts=1 if recovery else 0,
            status="running" if recovery else "queued",
            lease_owner="dead-worker" if recovery else None,
            lease_token="expired-claim" if recovery else None,
            lease_expires_at=now - timedelta(seconds=1) if recovery else None,
        )
        session.add(row)
        session.flush()
        job_id = row.id

    def hard_failure(*_args, **_kwargs):
        raise RuntimeError("synthetic subprocess failure")

    monkeypatch.setattr("sixsentences_server.jobs._resolve_task", lambda _name: hard_failure)
    worker = DatabaseWorker(run_in_subprocess=False, lane="documents")
    if recovery:
        assert worker.recover_interrupted() == 1
    else:
        assert worker.run_once()
    terminalize_extraction_action(run_id, action_id, expected_org_id=org_id)
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        assert job is not None and job.status == "failed"
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert action is not None and action.remaining_credits == 0 and (action.status == "failed")
        unrelated = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id == "synthetic-unrelated-action"
            )
        )
        assert unrelated is not None and unrelated.status == "active"
        assert unrelated.remaining_credits == 11
        assert session.get(Run, run_id).status == "completed"
        assert (
            session.query(RunEvent).filter_by(run_id=run_id, event="extraction_failed").count() == 1
        )
    assert _ledger(run_id) == []


def test_stale_sweep_keeps_retryable_extraction_hold_but_releases_terminal_job(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, run_id, action_id, work_ids, costs = _batch(settings, monkeypatch)
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        action.created_at = datetime.now(UTC) - timedelta(hours=2)
        row = BackgroundJobRow(
            org_id=org_id,
            task="_execute_extraction",
            lane="documents",
            status="queued",
            args=[run_id, ["finding"], action_id],
            kwargs={"work_ids": work_ids, "capacity_costs": costs},
        )
        session.add(row)
        session.flush()
        release_stale_ai_actions(session, org_id)
        assert action.status == "active" and action.remaining_credits == 24
        row.status = "failed"
        session.flush()
        release_stale_ai_actions(session, org_id)
        assert action.status == "expired" and action.remaining_credits == 0
        assert session.get(Run, run_id).status == "completed"


def test_extraction_finalizer_rejects_cross_workspace_or_wrong_resource(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, run_id, action_id, _, _ = _batch(settings, monkeypatch)
    terminalize_extraction_action(run_id, action_id, expected_org_id=org_id + 1)
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert action.status == "active" and action.remaining_credits == 24
        action.resource_type = "figure"
    terminalize_extraction_action(run_id, action_id, expected_org_id=org_id)
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert action.status == "active" and action.remaining_credits == 24


def _table_client(org_id: int) -> TestClient:
    """Isolate the table serializer from unrelated authentication setup."""
    app = api_app.create_app()
    app.dependency_overrides[api_app.require_auth] = lambda: AuthContext(
        user_id=1, org_id=org_id, token_id=1, role="owner", email="synthetic@example.test"
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("action_status", "job_status", "old", "expected_running"),
    [
        ("active", "queued", False, True),
        ("active", "running", True, True),
        ("active", "queued", True, True),
        ("active", "failed", False, False),
        ("active", "completed", False, False),
        ("active", "cancelled", False, False),
        ("active", None, False, True),
        ("active", None, True, False),
        ("paused", "queued", False, False),
        ("failed", None, False, False),
        ("settled", None, False, False),
    ],
)
def test_extraction_table_reports_actual_activity_without_settling_from_get(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    action_status: str,
    job_status: str | None,
    old: bool,
    expected_running: bool,
) -> None:
    org_id, run_id, action_id, _, _ = _batch(settings, monkeypatch)
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        action.status = action_status
        action.created_at = datetime.now(UTC) - timedelta(hours=2 if old else 0)
        rows = session.scalars(
            select(ExtractionRow).where(ExtractionRow.run_id == run_id).order_by(ExtractionRow.id)
        ).all()
        rows[0].status = "done"
        rows[2].status = "failed"
        if job_status is not None:
            session.add(
                BackgroundJobRow(
                    org_id=org_id,
                    task="_execute_extraction",
                    lane="documents",
                    args=[run_id, ["finding"], action_id],
                    kwargs={},
                    status=job_status,
                )
            )
    response = _table_client(org_id).get(f"/runs/{run_id}/extraction")
    assert response.status_code == 200
    table = response.json()
    assert table["status"] == "pending"
    assert table["is_running"] is expected_running
    assert table["unfinished_count"] == 2
    assert table["failed_count"] == 1
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        assert action.status == action_status
        assert action.remaining_credits == 24
        assert session.get(Run, run_id).status == "completed"
    assert _ledger(run_id) == []


@pytest.mark.parametrize("binding", ["org", "run", "action", "type", "resource"])
def test_extraction_table_ignores_unrelated_active_reservations(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, binding: str
) -> None:
    org_id, run_id, action_id, _, _ = _batch(settings, monkeypatch)
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        other_org = Org(name="Unrelated synthetic workspace")
        session.add(other_org)
        session.flush()
        other_run = Run(org_id=org_id, question="Unrelated synthetic review", status="completed")
        session.add(other_run)
        session.flush()
        if binding == "org":
            action.org_id = other_org.id
        elif binding == "run":
            action.run_id = other_run.id
        elif binding == "action":
            action.action = "screening"
        elif binding == "type":
            action.resource_type = "run"
        else:
            action.resource_id = str(other_run.id)
        session.add(
            BackgroundJobRow(
                org_id=org_id,
                task="_execute_extraction",
                lane="documents",
                args=[run_id, ["finding"], action_id],
                kwargs={},
                status="running",
            )
        )
    response = _table_client(org_id).get(f"/runs/{run_id}/extraction")
    assert response.status_code == 200
    assert response.json()["is_running"] is False
    assert response.json()["unfinished_count"] == 3


@pytest.mark.parametrize("empty", [False, True])
def test_extraction_table_counts_failed_rows_even_with_completed_results(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, empty: bool
) -> None:
    org_id, run_id, action_id, _, _ = _batch(settings, monkeypatch)
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        action.status = "settled"
        for index, row in enumerate(
            session.scalars(
                select(ExtractionRow)
                .where(ExtractionRow.run_id == run_id)
                .order_by(ExtractionRow.id)
            )
        ):
            if empty:
                session.delete(row)
            else:
                row.status = "failed" if index == 2 else "done"
    table = _table_client(org_id).get(f"/runs/{run_id}/extraction").json()
    assert table["status"] == ("none" if empty else "done")
    assert table["is_running"] is False
    assert table["unfinished_count"] == (0 if empty else 1)
    assert table["failed_count"] == (0 if empty else 1)


def test_paused_extraction_table_can_retry_only_unfinished_rows(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, run_id, action_id, work_ids, _ = _batch(settings, monkeypatch)
    with db_session() as session:
        action = session.scalar(
            select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
        )
        action.status = "paused"
        action.remaining_credits = 0
        row = session.scalar(
            select(ExtractionRow).where(
                ExtractionRow.run_id == run_id, ExtractionRow.work_id == work_ids[0]
            )
        )
        row.status = "done"
        row.payload = {"finding": {"value": "Keep this result", "verified": True}}
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    monkeypatch.setattr(api_app, "available_specs", lambda: ["synthetic"])
    monkeypatch.setattr(
        api_app,
        "_final_decisions",
        lambda *_args: {work_id: SimpleNamespace(verdict="include") for work_id in work_ids},
    )
    client = _table_client(org_id)
    before = client.get(f"/runs/{run_id}/extraction").json()
    assert before["is_running"] is False and before["unfinished_count"] == 2
    retry = client.post(f"/runs/{run_id}/extraction", json={"force": False})
    assert retry.status_code == 202 and retry.json()["scheduled"] == 2
    after = client.get(f"/runs/{run_id}/extraction").json()
    assert after["is_running"] is True and after["unfinished_count"] == 2
    with db_session() as session:
        row = session.scalar(
            select(ExtractionRow).where(
                ExtractionRow.run_id == run_id, ExtractionRow.work_id == work_ids[0]
            )
        )
        assert row.status == "done" and row.payload["finding"]["value"] == "Keep this result"


def test_extraction_table_keeps_saved_schema_separate_from_existing_payload_columns(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, run_id, _, work_ids, _ = _batch(settings, monkeypatch)
    with db_session() as session:
        run = session.get(Run, run_id)
        run.config = {
            "extraction_schema": {
                "name": "Revised extraction",
                "fields": ["new_outcome", "new_method"],
            }
        }
        row = session.scalar(
            select(ExtractionRow).where(
                ExtractionRow.run_id == run_id, ExtractionRow.work_id == work_ids[0]
            )
        )
        row.status = "done"
        row.payload = {"old_finding": {"value": "Keep previous evidence", "verified": True}}
    table = _table_client(org_id).get(f"/runs/{run_id}/extraction").json()
    assert table["schema"]["fields"] == ["new_outcome", "new_method"]
    assert table["fields"] == ["old_finding"]
    assert table["rows"][0]["payload"]["old_finding"]["value"] == "Keep previous evidence"
