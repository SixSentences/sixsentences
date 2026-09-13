"""Frozen voice pricing, observed automatic analysis and retry-safe settlement."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from sixsentences_server.config import Settings
from sixsentences_server.core.db import (
    BackgroundJobRow,
    CapacityReservationRow,
    CreditEventRow,
    InterviewRow,
    InterviewSegmentRow,
    LLMCallRow,
    Org,
    db_session,
    init_db,
)
from sixsentences_server.core.entitlements import (
    begin_ai_action,
    charge_credits,
    release_stale_ai_actions,
)
from sixsentences_server.llm.base import LLMUsage

api_app = import_module("sixsentences_server.api.app")


def _analysis_action(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, *, proportional: bool = True
) -> tuple[int, int, str]:
    """Only the automatic first analysis has a server-frozen parent tariff."""
    del settings
    init_db()
    with db_session() as session:
        org = Org(name="Synthetic proportional voice analysis", plan="community")
        session.add(org)
        session.flush()
        interview = InterviewRow(
            org_id=org.id, title="Synthetic speech", kind="live", status="ready"
        )
        session.add(interview)
        session.flush()
        action = begin_ai_action(
            session,
            org,
            action="interview_analysis",
            resource_type="interview",
            resource_id=interview.id,
            reserve_credits=1000 if proportional else 0,
        )
        action.cost_limit_usd = 1.5
        pricing = {
            "policy": "catalog_proportional_v2",
            "voice_action_id": "synthetic-parent",
            "reserved_credits": 4500,
            "cost_limit_usd": 6.75,
        }
        interview.config = {
            "analyzing": True,
            "active_stage": "analyze",
            "revision": 1,
            "analysis_action_id": action.action_id,
            "pipeline": [{"id": "analyze", "status": "running"}],
            **(
                {
                    "voice_capacity_pricing": pricing,
                    "voice_analysis_pricing": {**pricing, "action_id": action.action_id},
                }
                if proportional
                else {}
            ),
        }
        if not proportional:
            charge_credits(session, org, action="interview", credits=46)
        session.add(
            InterviewSegmentRow(
                org_id=org.id,
                interview_id=interview.id,
                idx=1,
                speaker="S2",
                text="A synthetic participant response.",
                end_ms=1000,
            )
        )
        scope = (org.id, interview.id, action.action_id)
    monkeypatch.setattr(api_app, "_build_pool", lambda _settings: SimpleNamespace())
    return scope


def _usage(cost: float) -> LLMUsage:
    return LLMUsage(
        task="interview_analysis",
        provider="fake",
        model="synthetic",
        input_tokens=10,
        output_tokens=5,
        cost_usd=cost,
        cost_source="catalog",
        duration_ms=1,
    )


@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed", None])
def test_stale_analysis_preserves_active_jobs_and_settles_terminal_orphans(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    org_id, interview_id, action_id = _analysis_action(settings, monkeypatch)
    with db_session() as session:
        action = session.scalar(select(CapacityReservationRow))
        action.created_at = datetime.now(UTC) - timedelta(hours=2)
        session.add(
            LLMCallRow(
                org_id=org_id,
                action_id=action_id,
                resource_type="interview",
                resource_id=str(interview_id),
                task="interview_analysis",
                provider="fake",
                model="synthetic",
                cost_usd=0.0002389828,
            )
        )
        if status is not None:
            session.add(
                BackgroundJobRow(
                    org_id=org_id,
                    task="_execute_interview_analysis",
                    status=status,
                    args=[interview_id, "auto", action_id],
                )
            )
    with db_session() as session:
        release_stale_ai_actions(session, org_id)
        release_stale_ai_actions(session, org_id)
        action = session.scalar(select(CapacityReservationRow))
        running = status in {"queued", "running"}
        assert action.status == ("active" if running else "failed")
        assert action.remaining_credits == (1000 if running else 0)
        assert sum(session.scalars(select(CreditEventRow.credits))) == (0 if running else 1)


def test_pool_setup_failure_releases_hold_and_preserves_already_recorded_spend(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, interview_id, action_id = _analysis_action(settings, monkeypatch)
    with db_session() as session:
        session.add(
            LLMCallRow(
                org_id=org_id,
                action_id=action_id,
                resource_type="interview",
                resource_id=str(interview_id),
                task="interview_analysis",
                provider="fake",
                model="synthetic",
                cost_usd=0.0002389828,
            )
        )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("synthetic pool setup failure")

    monkeypatch.setattr(api_app, "_build_scoped_pool", unavailable)
    api_app._execute_interview_analysis(interview_id, "auto", action_id)
    api_app._execute_interview_analysis(interview_id, "auto", action_id)
    with db_session() as session:
        action = session.scalar(select(CapacityReservationRow))
        assert action.status == "failed" and action.remaining_credits == 0
        assert sum(session.scalars(select(CreditEventRow.credits))) == 1
        assert session.get(InterviewRow, interview_id).status == "ready"


def test_unrelated_prepaid_analysis_is_not_repriced(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, interview_id, action_id = _analysis_action(settings, monkeypatch, proportional=False)

    def analyze(pool, **_kwargs):
        pool.on_usage(_usage(0.0002389828))
        return {"summary": "Synthetic result", "quotes_verified": 0, "quotes_total": 0}

    monkeypatch.setattr(api_app, "run_interview_analysis", analyze)
    api_app._execute_interview_analysis(interview_id, "auto", action_id)
    with db_session() as session:
        assert list(session.scalars(select(CreditEventRow.credits))) == [46]
        assert "voice_analysis_pricing" not in session.get(InterviewRow, interview_id).config
