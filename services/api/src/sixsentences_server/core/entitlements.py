"""Enforce community resource and provider-safety limits at the API boundary.

Every limit trip raises `EntitlementError` with an explicit machine-readable code.
Feature gates come from the community profile; consumption is a local capacity meter.
Usage is summed live from the workspace's active capacity ledger rows, so
enforcement is always against live state. Usage windows renew from the workspace anchor.

Charging discipline: a scope-aware reservation is checked and recorded before
work starts; volume-priced parts settle continuously against that reservation.
Provider spend is governed by a persistent action id, so a retry, resume or
worker restart never resets the economic safety boundary.
"""

import calendar
import math
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Numeric, func, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    BackgroundJobRow,
    BrainstormProjectSynthesisRow,
    CapacityReservationRow,
    CreditEventRow,
    DatasetVersionRow,
    DocumentRow,
    FigureRow,
    InterviewRow,
    LiveCompanionAskRow,
    LLMCallRow,
    Org,
    ProviderCostRow,
    ResearchDatasetRow,
    Run,
    SurveyResponseRow,
    User,
    VoiceSessionRow,
    VoiceStudyRow,
    WriterAssetRow,
    WriterSourceRow,
    WriterTemplateOriginRow,
)
from sixsentences_server.core.plans import (
    Capability,
    Plan,
    capacity_profile,
    get_plan,
    screening_credits,
)
from sixsentences_server.llm.base import LLMUsage
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.ops.status import require_storage_capacity
from sixsentences_server.voice.service import (
    LIVE_ACTION_BUDGET_FACTOR,
    LIVE_COST_EPSILON_USD,
    affordable_live_minutes,
)
from sixsentences_server.voice.settlement import (
    customer_relay_cost,
    proportional_voice_credits,
    relay_customer_receipt,
)

if TYPE_CHECKING:
    from sixsentences_server.llm.base import BudgetGovernor

_ACTIVE_STATES = ("pending", "running", "awaiting_protocol_approval")


@dataclass(frozen=True)
class AIActionPolicy:
    """Economic and user-facing contract for one provider-backed action."""

    label: str
    budget_factor: float


AI_ACTION_POLICIES: dict[str, AIActionPolicy] = {
    "agent_tools": AIActionPolicy("Agent research", 0.25),
    "dataset_chat": AIActionPolicy("Dataset analysis", 0.25),
    "dataset_chat_result_review": AIActionPolicy("Dataset result review", 0.1),
    "document_translation": AIActionPolicy("Document translation", 1.0),
    "document_vision": AIActionPolicy("Document reading", 0.5),
    "extraction": AIActionPolicy("Evidence extraction", 1.0),
    "figure": AIActionPolicy("Scientific figures", 1.0),
    "interview": AIActionPolicy("Interview transcription", 1.0),
    "interview_analysis": AIActionPolicy("Interview analysis", 0.5),
    "interview_chat": AIActionPolicy("Interview analysis", 0.25),
    "live_companion_ask": AIActionPolicy("Live companion answer", 0.25),
    "live_companion_brainstorm": AIActionPolicy("Brainstorm structure", 1.0),
    "interview_live": AIActionPolicy("Live interviews", LIVE_ACTION_BUDGET_FACTOR),
    "interview_text_turn": AIActionPolicy("Live interview turns", 0.25),
    "protocol_regeneration": AIActionPolicy("Protocol drafts", 0.5),
    "question": AIActionPolicy("Answers & chat", 0.25),
    "repository_analysis": AIActionPolicy("Repository analysis", 0.5),
    "repository_manuscript_prose": AIActionPolicy("Repository manuscript prose", 0.25),
    "report_synthesis": AIActionPolicy("Research reports", 0.5),
    "run_chat": AIActionPolicy("Review analysis", 0.25),
    "screening": AIActionPolicy("AI screening", 1.0),
    "search": AIActionPolicy("Research searches", 1.0),
    "survey_chat": AIActionPolicy("Survey analysis", 0.25),
    "survey_chat_result_review": AIActionPolicy("Survey result review", 0.1),
    "voice_study_chat": AIActionPolicy("Interview study analysis", 0.25),
    "voice_study_chat_result_review": AIActionPolicy("Interview study result review", 0.1),
    "writer_chat": AIActionPolicy("Writing assistance", 0.25),
    "writer_chat_result_review": AIActionPolicy("Writing result review", 0.1),
    "interview_chat_result_review": AIActionPolicy("Transcript result review", 0.1),
    "writer_slides": AIActionPolicy("Presentation drafting", 0.5),
}
_LEGACY_FEATURE_CAPABILITY = {
    "chat": Capability.CORE_WORKSPACE,
    "live_search": Capability.DISCOVERY_PACK,
    "full_text_acquisition": Capability.DEEP_REVIEW,
    "full_text_screening": Capability.DEEP_REVIEW,
    "web_search": Capability.DEEP_REVIEW,
    "api_access": Capability.AUTOMATION,
    "living_reviews": Capability.AUTOMATION,
}


class EntitlementError(RuntimeError):
    """An actionable limit category, independent of translated display wording."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "entitlement_limit",
    ) -> None:
        super().__init__(message)
        self.code = code


def plan_for_org(org: Org) -> Plan:
    return get_plan(org.plan)


def _anchored_month(org: Org, year: int, month: int) -> datetime:
    anchor = org.capacity_cycle_anchor or org.created_at
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + (month - 1) + offset
    return (absolute // 12, absolute % 12 + 1)


def usage_cycle(org: Org, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the active monthly operator-managed usage window."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    start = _anchored_month(org, current.year, current.month)
    if start > current:
        year, month = _shift_month(current.year, current.month, -1)
        start = _anchored_month(org, year, month)
    end_year, end_month = _shift_month(start.year, start.month, 1)
    return (start, _anchored_month(org, end_year, end_month))


def _usage_cycle_for_org_id(session: Session, org_id: int) -> tuple[datetime, datetime]:
    org = session.get(Org, org_id)
    if org is None:
        raise EntitlementError("The workspace no longer exists.")
    return usage_cycle(org)


def capacity_budget_for_org(org: Org, plan: Plan | None = None) -> int | None:
    """Return the local workflow-unit ceiling; ``None`` means unmetered."""
    return (plan or plan_for_org(org)).monthly_credits


def ai_cost_budget_for_org(org: Org, plan: Plan | None = None) -> float:
    """Return the recurring operator-owned provider-spend ceiling."""
    return (plan or plan_for_org(org)).monthly_ai_cost_usd


def ai_cost_remaining_for_org(session: Session, org: Org, plan: Plan | None = None) -> float:
    """Return uncommitted provider-spend capacity in the current window."""
    resolved = plan or plan_for_org(org)
    return max(0.0, ai_cost_budget_for_org(org, resolved) - monthly_ai_spend(session, org.id))


def credits_used_this_month(session: Session, org_id: int) -> int:
    cycle_start, _ = _usage_cycle_for_org_id(session, org_id)
    return (
        session.scalar(
            select(func.coalesce(func.sum(CreditEventRow.credits), 0)).where(
                CreditEventRow.org_id == org_id,
                CreditEventRow.created_at >= cycle_start,
            )
        )
        or 0
    )


def credits_reserved(session: Session, org_id: int) -> int:
    """Capacity committed to active work but not yet converted into usage."""
    release_stale_ai_actions(session, org_id)
    return int(
        session.scalar(
            select(func.coalesce(func.sum(CapacityReservationRow.remaining_credits), 0)).where(
                CapacityReservationRow.org_id == org_id,
                CapacityReservationRow.status == "active",
            )
        )
        or 0
    )


def settle_stale_voice_session(
    session: Session,
    reservation: CapacityReservationRow,
    voice: VoiceSessionRow,
    study: VoiceStudyRow,
    *,
    now: datetime | None = None,
    unclaimed_ticket_expired_before: datetime | None = None,
) -> None:
    """Settle an orphaned voice session without freeing unknown provider exposure.

    Relay sessions use durable server receipts after a confirmed close, or
    retain their full reservation when a worker dies without a final receipt.
    Unclaimed relay tickets incur no provider exposure. Historical direct
    credentials retain their full authorized estimate because their use cannot
    be independently reconciled. None of these estimates is a provider invoice.
    """
    if reservation.status != "active" or voice.status != "running":
        return
    terminal_conditions = [
        VoiceSessionRow.id == voice.id,
        VoiceSessionRow.status == "running",
    ]
    if unclaimed_ticket_expired_before is not None:
        terminal_conditions.extend(
            [
                VoiceSessionRow.transport == "relay",
                VoiceSessionRow.relay_claimed_at.is_(None),
                VoiceSessionRow.relay_ticket_expires_at <= unclaimed_ticket_expired_before,
            ]
        )
    claimed_result = cast(
        CursorResult[Any],
        session.execute(
            update(VoiceSessionRow)
            .where(*terminal_conditions)
            .values(status="settling")
            .execution_options(synchronize_session=False)
        ),
    )
    claimed = claimed_result.rowcount
    if claimed != 1:
        return
    voice.status = "settling"
    locked_reservation = session.scalar(
        select(CapacityReservationRow)
        .where(CapacityReservationRow.id == reservation.id)
        .with_for_update()
    )
    if locked_reservation is None or locked_reservation.status != "active":
        voice.status = "aborted"
        voice.ended_at = now or datetime.now(UTC)
        voice.error = "The session authorization had already ended."
        return
    reservation = locked_reservation
    org = session.get(Org, reservation.org_id)
    if org is None:
        finish_ai_action(session, reservation.action_id, status="expired")
        voice.status = "aborted"
        voice.ended_at = now or datetime.now(UTC)
        return
    from sixsentences_server.voice.service import LIVE_MINUTE_COST_USD

    if reservation.action == "interview_live":
        authorized_minutes = (
            max(1, -(-voice.authorized_duration_ms // 60000))
            if voice.authorized_duration_ms > 0
            else max(
                1,
                min(
                    study.max_session_minutes,
                    affordable_live_minutes(reservation.cost_limit_usd),
                ),
            )
        )
        duration_ms = authorized_minutes * 60000
        cost_units = reservation.reserved_credits
        provider_credential_count = max(1, min(2, voice.provider_token_issues))
        provider_cost = authorized_minutes * LIVE_MINUTE_COST_USD * provider_credential_count
        provider_source = "estimated_cap"
        if voice.transport == "relay":
            meter = voice.relay_state or {}
            voice.relay_ticket_hash = ""
            if voice.relay_claimed_at is None:
                duration_ms, cost_units, provider_cost = (0, 0, 0.0)
            elif voice.relay_closed_at is not None:
                duration_ms = min(duration_ms, max(0, int(meter.get("duration_ms", 0))))
                provider_cost = customer_relay_cost(meter)
                cost_units = proportional_voice_credits(
                    provider_cost,
                    reserved_credits=reservation.reserved_credits,
                    cost_limit_usd=reservation.cost_limit_usd,
                )
                provider_source = (
                    "relay_catalog" if meter.get("meter_complete") else "relay_partial"
                )
                voice.relay_state = relay_customer_receipt(meter)
                provider_credential_count = max(1, int(meter.get("provider_connections", 0)))
            else:
                provider_cost = max(
                    reservation.cost_limit_usd,
                    float(meter.get("reserved_cost_usd", 0)),
                    float(meter.get("accounted_cost_usd", 0)),
                )
                provider_source = "relay_bound"
        if provider_cost:
            record_provider_cost(
                session,
                org_id=org.id,
                action_id=reservation.action_id,
                resource_type="voice_session",
                resource_id=voice.id,
                task="interview_live",
                provider="google",
                model=voice.model,
                cost_usd=provider_cost,
                cost_source=provider_source,
                request_count=provider_credential_count,
            )
    else:
        authorized_minutes = (
            max(1, -(-voice.authorized_duration_ms // 60000))
            if voice.authorized_duration_ms > 0
            else study.max_session_minutes
        )
        started_at = voice.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        settled_at = now or datetime.now(UTC)
        duration_ms = min(
            authorized_minutes * 60000,
            max(0, int((settled_at - started_at).total_seconds() * 1000)),
        )
        per_turn_units = max(1, reservation.reserved_credits // authorized_minutes)
        cost_units = max(0, voice.text_turns_claimed) * per_turn_units
    if voice.transport == "relay" and voice.relay_closed_at is not None:
        charge_credits(
            session,
            org,
            credits=cost_units,
            action=reservation.action,
            model=voice.model,
        )
    else:
        consume_action_capacity(
            session,
            org,
            action_id=reservation.action_id,
            credits=cost_units,
            action=reservation.action,
            model=voice.model,
        )
    settled_at = now or datetime.now(UTC)
    voice.status = "aborted"
    voice.ended_at = settled_at
    voice.duration_ms = duration_ms
    voice.cost_units = cost_units
    voice.error = "The session ended without a result; its authorized capacity was settled."
    finish_ai_action(session, reservation.action_id, status="expired")


def live_analysis_pricing(interview: InterviewRow, action_id: str) -> dict[str, Any] | None:
    """Return only the server-frozen price for this automatic live analysis."""
    pricing = (interview.config or {}).get("voice_analysis_pricing")
    if not isinstance(pricing, dict) or pricing.get("action_id") != action_id:
        return None
    if pricing.get("policy") != "catalog_proportional_v2":
        raise ValueError("Unknown live analysis capacity pricing policy")
    proportional_voice_credits(
        0.0,
        reserved_credits=pricing["reserved_credits"],
        cost_limit_usd=pricing["cost_limit_usd"],
    )
    return pricing


def settle_live_analysis_capacity(
    session: Session, interview: InterviewRow, action_id: str, *, status: str
) -> bool:
    """Book durable observed analysis spend once, then release its unused hold.

    False means this is an unrelated, already prepaid analysis. Settlement is
    not new admission: already incurred provider cost remains counted even if
    an in-flight response exceeded the authorized allowance.
    """
    pricing = live_analysis_pricing(interview, action_id)
    if pricing is None:
        return False
    org = session.scalar(select(Org).where(Org.id == interview.org_id).with_for_update())
    if org is None:
        return True
    reservation = session.scalar(
        select(CapacityReservationRow)
        .where(
            CapacityReservationRow.org_id == org.id,
            CapacityReservationRow.action_id == action_id,
            CapacityReservationRow.action == "interview_analysis",
            CapacityReservationRow.resource_type == "interview",
            CapacityReservationRow.resource_id == str(interview.id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reservation is None or reservation.status != "active":
        return True
    observed = action_ai_spend(session, action_id)
    credits = proportional_voice_credits(
        observed,
        reserved_credits=pricing["reserved_credits"],
        cost_limit_usd=pricing["cost_limit_usd"],
    )
    charge_credits(session, org, action="interview", credits=credits, model="auto")
    finish_ai_action(session, action_id, status=status)
    interview.config = {
        **dict(interview.config or {}),
        "voice_analysis_pricing": {
            **pricing,
            "settled_cost_usd": observed,
            "settled_credits": credits,
        },
    }
    return True


def release_stale_ai_actions(session: Session, org_id: int) -> None:
    """Recover reservations orphaned by a process or browser crash.

    Active runs are intentionally not timed out here because exhaustive
    reviews can legitimately run for many hours. Terminal runs close
    immediately; synchronous surfaces get a one-hour crash window; realtime
    voice sessions use their own recorded session state and a conservative
    two-hour upper bound.
    """
    now = datetime.now(UTC)
    rows = session.scalars(
        select(CapacityReservationRow).where(
            CapacityReservationRow.org_id == org_id,
            CapacityReservationRow.status == "active",
        )
    ).all()
    for row in rows:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age_seconds = (now - created).total_seconds()
        if row.resource_type == "run" and row.run_id is not None:
            run = session.get(Run, row.run_id)
            if run is None or run.status in {"completed", "failed", "cancelled"}:
                finish_ai_action(session, row.action_id, status="expired")
            continue
        if row.resource_type == "run_extraction":
            extraction_jobs = session.scalars(
                select(BackgroundJobRow).where(
                    BackgroundJobRow.org_id == org_id,
                    BackgroundJobRow.task == "_execute_extraction",
                )
            ).all()
            matching_jobs = [
                job
                for job in extraction_jobs
                if len(job.args or []) >= 3
                and str(job.args[0]) == row.resource_id
                and (job.args[2] == row.action_id)
            ]
            if any(job.status in {"queued", "running"} for job in matching_jobs):
                continue
            if matching_jobs or age_seconds > 3600:
                finish_ai_action(session, row.action_id, status="expired")
            continue
        if row.action == "interview_analysis" and row.resource_type == "interview":
            interview = (
                session.get(InterviewRow, int(row.resource_id))
                if row.resource_id.isdigit()
                else None
            )
            if (
                interview is not None
                and interview.org_id == org_id
                and (live_analysis_pricing(interview, row.action_id) is not None)
            ):
                jobs = session.scalars(
                    select(BackgroundJobRow).where(
                        BackgroundJobRow.org_id == org_id,
                        BackgroundJobRow.task == "_execute_interview_analysis",
                    )
                ).all()
                matching_jobs = [
                    job
                    for job in jobs
                    if len(job.args or []) >= 3
                    and str(job.args[0]) == row.resource_id
                    and (job.args[2] == row.action_id)
                ]
                if any(job.status in {"queued", "running"} for job in matching_jobs):
                    continue
                if matching_jobs or age_seconds > 3600:
                    settle_live_analysis_capacity(
                        session, interview, row.action_id, status="failed"
                    )
                    if (interview.config or {}).get("analysis_action_id") == row.action_id:
                        interview.config = {
                            **dict(interview.config or {}),
                            "analyzing": False,
                            "active_stage": "",
                        }
                continue
        if row.resource_type == "voice_session":
            voice = (
                session.get(VoiceSessionRow, int(row.resource_id))
                if row.resource_id.isdigit()
                else None
            )
            study = session.get(VoiceStudyRow, voice.study_id) if voice is not None else None
            if (
                voice is not None
                and study is not None
                and (voice.org_id == org_id)
                and (study.org_id == org_id)
                and (voice.status == "running")
                and (voice.transport == "relay")
                and (voice.relay_claimed_at is None)
                and (voice.relay_ticket_expires_at is not None)
            ):
                locked_voice = session.scalar(
                    select(VoiceSessionRow)
                    .where(VoiceSessionRow.id == voice.id, VoiceSessionRow.org_id == org_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if locked_voice is not None:
                    voice = locked_voice
                    expires_at = voice.relay_ticket_expires_at
                    if expires_at is not None and expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                    if (
                        voice.status == "running"
                        and voice.transport == "relay"
                        and (voice.relay_claimed_at is None)
                        and (expires_at is not None)
                        and (expires_at <= now)
                    ):
                        settle_stale_voice_session(
                            session,
                            row,
                            voice,
                            study,
                            now=now,
                            unclaimed_ticket_expired_before=now,
                        )
                        continue
            if voice is not None and study is not None:
                if row.action == "interview_live":
                    authorized_minutes = (
                        max(1, -(-voice.authorized_duration_ms // 60000))
                        if voice.authorized_duration_ms > 0
                        else max(
                            1,
                            min(
                                study.max_session_minutes,
                                affordable_live_minutes(row.cost_limit_usd),
                            ),
                        )
                    )
                else:
                    authorized_minutes = (
                        max(1, -(-voice.authorized_duration_ms // 60000))
                        if voice.authorized_duration_ms > 0
                        else study.max_session_minutes
                    )
                live_timeout_seconds = authorized_minutes * 60 + 15 * 60
            else:
                live_timeout_seconds = 2 * 60 * 60
            if voice is None or study is None or voice.status != "running":
                finish_ai_action(session, row.action_id, status="expired")
            elif age_seconds > live_timeout_seconds:
                settle_stale_voice_session(session, row, voice, study, now=now)
            continue
        if row.action in {"live_companion_ask", "live_companion_brainstorm"}:
            project_synthesis = session.scalar(
                select(BrainstormProjectSynthesisRow).where(
                    BrainstormProjectSynthesisRow.action_id == row.action_id
                )
            )
            if project_synthesis is not None:
                if project_synthesis.status != "pending":
                    finish_ai_action(session, row.action_id, status="expired")
                    continue
                active_job = False
                jobs = session.scalars(
                    select(BackgroundJobRow).where(
                        BackgroundJobRow.org_id == row.org_id,
                        BackgroundJobRow.task == "_execute_project_brainstorm_synthesis",
                        BackgroundJobRow.status.in_(("queued", "running")),
                    )
                ).all()
                for job in jobs:
                    try:
                        if int((job.args or [None])[0]) == project_synthesis.id:
                            active_job = True
                            break
                    except (TypeError, ValueError, IndexError):
                        continue
                if active_job:
                    continue
                if age_seconds > 3600:
                    project_synthesis.status = "failed"
                    project_synthesis.error_code = "project_brainstorm_unavailable"
                    project_synthesis.error = (
                        "The project brainstorm could not be synthesized. Retry this selection."
                    )
                    project_synthesis.finished_at = now
                    finish_ai_action(session, row.action_id, status="expired")
                continue
            ask = session.scalar(
                select(LiveCompanionAskRow).where(LiveCompanionAskRow.action_id == row.action_id)
            )
            if ask is None or ask.status != "pending":
                finish_ai_action(session, row.action_id, status="expired")
                continue
            active_job = False
            jobs = session.scalars(
                select(BackgroundJobRow).where(
                    BackgroundJobRow.org_id == row.org_id,
                    BackgroundJobRow.task == "_execute_live_companion_ask",
                    BackgroundJobRow.status.in_(("queued", "running")),
                )
            ).all()
            for job in jobs:
                try:
                    if int((job.args or [None])[0]) == ask.id:
                        active_job = True
                        break
                except (TypeError, ValueError, IndexError):
                    continue
            if active_job:
                continue
            if age_seconds > 3600:
                ask.status = "failed"
                is_brainstorm = ask.kind == "brainstorm"
                ask.error_code = "brainstorm_unavailable" if is_brainstorm else "answer_unavailable"
                ask.error = (
                    "The brainstorm could not be structured. Try structuring it again."
                    if is_brainstorm
                    else "The live answer could not be generated. Try a new question."
                )
                finish_ai_action(session, row.action_id, status="expired")
            continue
        timeout = 86400 if row.resource_type == "interview" else 3600
        if age_seconds > timeout:
            finish_ai_action(session, row.action_id, status="expired")


def credits_remaining(session: Session, org: Org) -> int | None:
    """None = unlimited. Active reservations are unavailable to new work."""
    budget = capacity_budget_for_org(org)
    if budget is None:
        return None
    return budget - credits_used_this_month(session, org.id) - credits_reserved(session, org.id)


def check_credits(session: Session, org: Org, needed: int) -> Plan:
    """Enforce an optional local workflow-unit ceiling."""
    plan = plan_for_org(org)
    budget = capacity_budget_for_org(org, plan)
    if budget is None:
        return plan
    available = max(
        0,
        budget - credits_used_this_month(session, org.id) - credits_reserved(session, org.id),
    )
    if available < needed:
        raise EntitlementError(
            f"{plan.name}: the operator-defined workflow capacity is exhausted.",
            code="capacity_exhausted",
        )
    return plan


def charge_credits(
    session: Session,
    org: Org,
    *,
    action: str,
    credits: int,
    run_id: int | None = None,
    model: str = "auto",
) -> None:
    """Append a local workflow-usage event."""
    if credits > 0:
        session.add(
            CreditEventRow(
                org_id=org.id,
                run_id=run_id,
                action=action,
                model=model,
                credits=credits,
            )
        )


def begin_ai_action(
    session: Session,
    org: Org,
    *,
    action: str,
    resource_type: str,
    resource_id: str | int,
    run_id: int | None = None,
    reserve_credits: int = 0,
    action_id: str | None = None,
) -> CapacityReservationRow:
    """Create or reopen one durable economic boundary for a user action."""
    policy = AI_ACTION_POLICIES.get(action)
    if policy is None:
        raise ValueError(f"unregistered AI action: {action}")
    session.scalar(select(Org.id).where(Org.id == org.id).with_for_update())
    resolved_action_id = action_id or secrets.token_urlsafe(18)
    existing = session.scalar(
        select(CapacityReservationRow).where(CapacityReservationRow.action_id == resolved_action_id)
    )
    if existing is not None:
        if (
            existing.org_id != org.id
            or existing.action != action
            or existing.resource_type != resource_type
            or (existing.resource_id != str(resource_id))
        ):
            raise ValueError("AI action id is already bound to a different resource")
        return existing
    plan = plan_for_org(org)
    release_stale_ai_actions(session, org.id)
    active_actions = int(
        session.scalar(
            select(func.count())
            .select_from(CapacityReservationRow)
            .where(
                CapacityReservationRow.org_id == org.id,
                CapacityReservationRow.status == "active",
            )
        )
        or 0
    )
    if active_actions >= plan.max_concurrent_ai_actions:
        raise EntitlementError(
            f"{plan.name}: {plan.max_concurrent_ai_actions} AI actions are already active. Wait for one to finish before starting another.",
            code="concurrency_limit",
        )
    if action == "figure":
        active_figures = active_ai_actions(session, org.id, action="figure")
        if active_figures >= plan.max_concurrent_figures:
            raise EntitlementError(
                f"{plan.name}: {plan.max_concurrent_figures} figure renders are already active. Wait for one to finish before starting another.",
                code="concurrency_limit",
            )
    check_credits(session, org, max(0, reserve_credits))
    monthly_left = max(
        0.0,
        ai_cost_remaining_for_org(session, org, plan) - ai_cost_reserved(session, org.id),
    )
    operator_cap = max(0.0, get_settings().llm_budget_usd)
    action_ceiling = max(0.02, plan.max_ai_cost_per_action_usd * policy.budget_factor)
    cost_limit = min(action_ceiling, monthly_left, operator_cap)
    if cost_limit <= 0:
        raise EntitlementError(
            f"{plan.name}: monthly AI capacity is already committed. Wait for active work to finish.",
            code="capacity_exhausted",
        )
    row = CapacityReservationRow(
        action_id=resolved_action_id,
        org_id=org.id,
        run_id=run_id,
        resource_type=resource_type,
        resource_id=str(resource_id),
        action=action,
        reserved_credits=max(0, reserve_credits),
        remaining_credits=max(0, reserve_credits),
        cost_limit_usd=cost_limit,
        status="active",
    )
    session.add(row)
    session.flush()
    return row


def reactivate_ai_action(session: Session, org: Org, action_id: str) -> CapacityReservationRow:
    """Reopen a provider-paused action with a fresh incremental cost ceiling.

    User-requested pauses keep their reservation active and return unchanged.
    Provider-paused actions have already spent their previous ceiling, so the
    new ceiling extends from durable spend instead of erasing or double-counting
    the action's history.
    """
    row = session.scalar(
        select(CapacityReservationRow).where(
            CapacityReservationRow.action_id == action_id,
            CapacityReservationRow.org_id == org.id,
        )
    )
    if row is None:
        raise EntitlementError("This AI action no longer exists.")
    if _search_capacity_pricing(session, row) is not None:
        session.scalar(select(Org.id).where(Org.id == org.id).with_for_update())
        row = session.scalar(
            select(CapacityReservationRow)
            .where(
                CapacityReservationRow.action_id == action_id,
                CapacityReservationRow.org_id == org.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise EntitlementError("This AI action no longer exists.")
    if row.status == "active":
        return row
    if row.status != "paused":
        raise EntitlementError(f"This AI action cannot resume from {row.status}.")
    plan = plan_for_org(org)
    release_stale_ai_actions(session, org.id)
    active_actions = int(
        session.scalar(
            select(func.count())
            .select_from(CapacityReservationRow)
            .where(
                CapacityReservationRow.org_id == org.id,
                CapacityReservationRow.status == "active",
            )
        )
        or 0
    )
    if active_actions >= plan.max_concurrent_ai_actions:
        raise EntitlementError(
            f"{plan.name}: {plan.max_concurrent_ai_actions} AI actions are already active. Wait for one to finish before resuming this work.",
            code="concurrency_limit",
        )
    policy = AI_ACTION_POLICIES[row.action]
    monthly_left = max(
        0.0,
        ai_cost_remaining_for_org(session, org, plan) - ai_cost_reserved(session, org.id),
    )
    incremental_limit = min(
        max(0.02, plan.max_ai_cost_per_action_usd * policy.budget_factor),
        monthly_left,
        max(0.0, get_settings().llm_budget_usd),
    )
    if incremental_limit <= 0:
        raise EntitlementError(
            f"{plan.name}: monthly AI capacity cannot resume this action yet.",
            code="capacity_exhausted",
        )
    pricing = _search_capacity_pricing(session, row)
    if pricing is not None:
        incremental_limit, units = _search_capacity_reserve(
            session, org, incremental_limit, pricing
        )
        if incremental_limit <= 0:
            raise EntitlementError(
                "Research capacity is used up. Add capacity before resuming.",
                code="capacity_exhausted",
            )
        row.reserved_credits += units
        row.remaining_credits = units
    row.cost_limit_usd = action_ai_spend(session, action_id) + incremental_limit
    row.status = "active"
    row.released_at = None
    session.flush()
    return row


def _search_capacity_units(cost_usd: float | Decimal, pricing: dict[str, Any]) -> int:
    """Convert cumulative provider cost at the immutable run-start ratio."""
    units = pricing.get("capacity_budget")
    dollars = pricing.get("provider_budget_usd")
    if (
        pricing.get("policy") != "provider_cost_v1"
        or (units is not None and (type(units) is not int or units <= 0))
        or isinstance(dollars, bool)
        or (not isinstance(dollars, (int, float)))
        or (not math.isfinite(dollars))
        or (dollars <= 0)
        or (not math.isfinite(cost_usd))
        or (cost_usd < 0)
    ):
        raise ValueError("Invalid search capacity pricing snapshot")
    if units is None:
        return 0
    return int(
        (Decimal(str(cost_usd)) * units / Decimal(str(dollars))).to_integral_value(
            rounding=ROUND_CEILING
        )
    )


def _search_capacity_pricing(
    session: Session, row: CapacityReservationRow
) -> dict[str, Any] | None:
    if row.action != "search" or row.resource_type != "run" or row.run_id is None:
        return None
    run = session.get(Run, row.run_id)
    if run is None or run.org_id != row.org_id or row.resource_id != str(run.id):
        return None
    pricing = (run.config or {}).get("search_capacity_pricing")
    if pricing is None:
        return None
    if not isinstance(pricing, dict) or pricing.get("action_id") != row.action_id:
        raise ValueError("Search capacity pricing does not match its action")
    _search_capacity_units(0.0, pricing)
    return pricing


def _search_capacity_reserve(
    session: Session, org: Org, cost_limit_usd: float, pricing: dict[str, Any]
) -> tuple[float, int]:
    budget = capacity_budget_for_org(org)
    if budget is None:
        return (cost_limit_usd, 0)
    available = max(
        0,
        budget - credits_used_this_month(session, org.id) - credits_reserved(session, org.id),
    )
    affordable = float(
        Decimal(available)
        * Decimal(str(pricing["provider_budget_usd"]))
        / Decimal(pricing["capacity_budget"])
    )
    limit = min(cost_limit_usd, affordable)
    return (limit, min(available, _search_capacity_units(limit, pricing)))


def configure_search_capacity(
    session: Session, org: Org, run: Run, row: CapacityReservationRow
) -> None:
    """Freeze cost-proportional pricing for a newly authorized search only."""
    if row.action != "search" or row.run_id != run.id or row.org_id != org.id:
        raise ValueError("Search reservation ownership mismatch")
    if (run.config or {}).get("search_capacity_pricing") is not None:
        raise ValueError("Search pricing is already frozen")
    pricing = {
        "policy": "provider_cost_v1",
        "action_id": row.action_id,
        "capacity_budget": capacity_budget_for_org(org),
        "provider_budget_usd": ai_cost_budget_for_org(org),
    }
    _search_capacity_units(0.0, pricing)
    limit, units = _search_capacity_reserve(session, org, row.cost_limit_usd, pricing)
    if limit <= 0:
        raise EntitlementError("Research capacity is used up.", code="capacity_exhausted")
    row.cost_limit_usd = limit
    row.reserved_credits = row.remaining_credits = units
    run.config = {
        **dict(run.config or {}),
        "search_capacity_pricing": pricing,
        "cost_action_id": row.action_id,
        "capacity_reserved_units": units,
    }


def search_capacity_estimate(session: Session, org: Org) -> int:
    """Estimate the maximum authorization, not a non-refundable search fee."""
    plan = plan_for_org(org)
    pricing = {
        "policy": "provider_cost_v1",
        "capacity_budget": capacity_budget_for_org(org),
        "provider_budget_usd": ai_cost_budget_for_org(org),
    }
    _search_capacity_units(0.0, pricing)
    limit = max(
        0.0,
        min(
            plan.max_ai_cost_per_action_usd,
            get_settings().llm_budget_usd,
            ai_cost_remaining_for_org(session, org) - ai_cost_reserved(session, org.id),
        ),
    )
    return _search_capacity_reserve(session, org, limit, pricing)[1]


def settle_search_capacity(session: Session, run: Run) -> int:
    """Charge cumulative metered search cost once, including interrupted work.

    This records already incurred work, not a new admission. Late provider
    receipts remain chargeable after cancellation without reopening the hold.
    """
    action_id = str((run.config or {}).get("cost_action_id") or "")
    if (run.config or {}).get("search_capacity_pricing") is None:
        return 0
    org = session.scalar(select(Org).where(Org.id == run.org_id).with_for_update())
    row = session.scalar(
        select(CapacityReservationRow)
        .where(
            CapacityReservationRow.action_id == action_id,
            CapacityReservationRow.org_id == run.org_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if org is None or row is None:
        raise ValueError("Missing search capacity reservation")
    pricing = _search_capacity_pricing(session, row)
    if pricing is None:
        raise ValueError("Missing search capacity pricing")
    observed = sum(
        (
            session.scalar(
                select(func.coalesce(func.sum(sql_cast(model.cost_usd, Numeric(24, 12))), 0)).where(
                    model.action_id == action_id
                )
            )
            or Decimal(0)
            for model in (LLMCallRow, ProviderCostRow)
        ),
        Decimal(0),
    )
    due = _search_capacity_units(observed, pricing)
    already = int(
        session.scalar(
            select(func.coalesce(func.sum(CreditEventRow.credits), 0)).where(
                CreditEventRow.org_id == run.org_id,
                CreditEventRow.run_id == run.id,
                CreditEventRow.action == "search",
            )
        )
        or 0
    )
    delta = max(0, due - already)
    if delta:
        charge_credits(
            session,
            org,
            action="search",
            credits=delta,
            run_id=run.id,
            model="provider_cost_v1",
        )
        if row.status == "active":
            row.remaining_credits = max(0, row.remaining_credits - delta)
    return delta


def consume_action_capacity(
    session: Session,
    org: Org,
    *,
    action_id: str,
    credits: int,
    action: str,
    run_id: int | None = None,
    model: str = "auto",
) -> None:
    """Settle actual work against its reservation, then ordinary capacity."""
    if credits <= 0:
        return
    reservation = session.scalar(
        select(CapacityReservationRow).where(
            CapacityReservationRow.action_id == action_id,
            CapacityReservationRow.org_id == org.id,
        )
    )
    covered = min(max(0, reservation.remaining_credits) if reservation is not None else 0, credits)
    if reservation is not None:
        reservation.remaining_credits -= covered
    extra = credits - covered
    try:
        if extra:
            check_credits(session, org, extra)
    except EntitlementError:
        if reservation is not None:
            reservation.remaining_credits += covered
        raise
    charge_credits(session, org, action=action, credits=credits, run_id=run_id, model=model)


def finish_ai_action(session: Session, action_id: str, *, status: str = "settled") -> None:
    """Release unused capacity while retaining the auditable action row."""
    row = session.scalar(
        select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
    )
    if row is None:
        return
    if _search_capacity_pricing(session, row) is not None:
        run = session.get(Run, row.run_id)
        assert run is not None
        settle_search_capacity(session, run)
    if row.status != "active":
        return
    row.remaining_credits = 0
    row.status = status
    row.released_at = datetime.now(UTC)


def monthly_ai_spend(session: Session, org_id: int) -> float:
    """Provider spend across every AI-backed feature in the current cycle."""
    cycle_start, _ = _usage_cycle_for_org_id(session, org_id)
    llm = float(
        session.scalar(
            select(func.coalesce(func.sum(LLMCallRow.cost_usd), 0.0)).where(
                LLMCallRow.org_id == org_id, LLMCallRow.created_at >= cycle_start
            )
        )
        or 0.0
    )
    other = float(
        session.scalar(
            select(func.coalesce(func.sum(ProviderCostRow.cost_usd), 0.0)).where(
                ProviderCostRow.org_id == org_id,
                ProviderCostRow.created_at >= cycle_start,
            )
        )
        or 0.0
    )
    return llm + other


def action_ai_spend(session: Session, action_id: str) -> float:
    llm = float(
        session.scalar(
            select(func.coalesce(func.sum(LLMCallRow.cost_usd), 0.0)).where(
                LLMCallRow.action_id == action_id
            )
        )
        or 0.0
    )
    other = float(
        session.scalar(
            select(func.coalesce(func.sum(ProviderCostRow.cost_usd), 0.0)).where(
                ProviderCostRow.action_id == action_id
            )
        )
        or 0.0
    )
    return llm + other


def ai_cost_reserved(session: Session, org_id: int) -> float:
    """Unspent dollar ceilings held by active actions in this workspace."""
    rows = session.scalars(
        select(CapacityReservationRow).where(
            CapacityReservationRow.org_id == org_id,
            CapacityReservationRow.status == "active",
        )
    ).all()
    return sum(
        max(0.0, row.cost_limit_usd - action_ai_spend(session, row.action_id)) for row in rows
    )


def action_budget(session: Session, action_id: str) -> "BudgetGovernor":
    """Rehydrate the same budget after any process boundary."""
    from sixsentences_server.llm.base import BudgetGovernor

    row = session.scalar(
        select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
    )
    if row is None:
        raise EntitlementError("This AI action no longer has an active budget.")
    return BudgetGovernor(
        limit_usd=row.cost_limit_usd, spent_usd=action_ai_spend(session, action_id)
    )


def attach_action_usage_sink(
    session: Session | None,
    pool: LLMPool | None,
    *,
    org_id: int,
    action_id: str,
    resource_type: str,
    resource_id: str | int,
    run_id: int | None = None,
) -> None:
    """Attribute every text-model call to the central action ledger."""
    if pool is None:
        return

    def _sink(usage: LLMUsage) -> None:
        row = LLMCallRow(
            org_id=org_id,
            run_id=run_id,
            action_id=action_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            task=usage.task,
            provider=usage.provider,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
            cost_source=usage.cost_source,
            duration_ms=usage.duration_ms,
        )
        if session is not None:
            pass
            session.add(row)
            if run_id is not None and resource_type == "run":
                run = session.get(Run, run_id)
                if run is not None and (run.config or {}).get("cost_action_id") == action_id:
                    settle_search_capacity(session, run)
            return
        from sixsentences_server.core.db import db_session

        with db_session() as usage_session:
            pass
            usage_session.add(row)
            if run_id is not None and resource_type == "run":
                run = usage_session.get(Run, run_id)
                if run is not None and (run.config or {}).get("cost_action_id") == action_id:
                    settle_search_capacity(usage_session, run)

    pool.on_usage = _sink


def check_action_cost(session: Session, action_id: str, projected_cost_usd: float) -> None:
    """Guard a direct image/audio request before it reaches the provider."""
    row = session.scalar(
        select(CapacityReservationRow).where(CapacityReservationRow.action_id == action_id)
    )
    if row is None or row.status != "active":
        raise EntitlementError("This AI action is no longer active.")
    if (
        action_ai_spend(session, action_id) + max(0.0, projected_cost_usd)
        > row.cost_limit_usd + LIVE_COST_EPSILON_USD
    ):
        raise EntitlementError(
            "This action reached its protected AI cost limit. Choose a lighter model or start a smaller action.",
            code="action_capacity_limit",
        )


def record_provider_cost(
    session: Session,
    *,
    org_id: int,
    action_id: str,
    resource_type: str,
    resource_id: str | int,
    task: str,
    provider: str,
    model: str,
    cost_usd: float,
    run_id: int | None = None,
    cost_source: str = "provider",
    request_count: int = 1,
) -> None:
    pass
    session.add(
        ProviderCostRow(
            org_id=org_id,
            run_id=run_id,
            action_id=action_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            task=task,
            provider=provider,
            model=model,
            cost_usd=max(0.0, cost_usd),
            cost_source=cost_source,
            request_count=max(1, request_count),
        )
    )
    if run_id is not None and resource_type == "run":
        run = session.get(Run, run_id)
        if run is not None and (run.config or {}).get("cost_action_id") == action_id:
            settle_search_capacity(session, run)


def question_cost(
    multiplier: float = 1,
    *,
    plan: Plan | str = "free",
    input_chars: int = 0,
    context_size: int | None = None,
    output_chars: int = 0,
) -> int:
    """Capacity for one answer, adapted to model, prompt and paper context."""
    profile = capacity_profile(plan)
    prompt_factor = min(max(input_chars, 0) / 4000, 1.0) * 0.35
    context_factor = min(max(context_size or 0, 0) / 100, 1.0) * 0.5
    output_factor = min(max(output_chars - 1200, 0) / 6000, 1.0) * 0.45
    return max(
        1,
        round(
            profile.question
            * max(multiplier, 1)
            * (1 + prompt_factor + context_factor + output_factor)
        ),
    )


def question_settlement_cost(
    multiplier: float = 1,
    *,
    plan: Plan | str = "free",
    input_chars: int = 0,
    requested_context_size: int | None = None,
    sources_considered: int = 0,
    output_chars: int = 0,
) -> int:
    """Settle actual answer depth beyond the pre-charged question baseline."""
    reserved = question_cost(
        multiplier,
        plan=plan,
        input_chars=input_chars,
        context_size=requested_context_size,
    )
    actual_context = max(requested_context_size or 0, max(sources_considered, 0))
    actual = question_cost(
        multiplier,
        plan=plan,
        input_chars=input_chars,
        context_size=actual_context,
        output_chars=output_chars,
    )
    return max(0, actual - reserved)


_LIGHT_AGENT_TOOLS = {
    "cite",
    "clarify",
    "export_works",
    "recall_history",
    "show_chart",
    "start_search",
    "suggest_followups",
}
_READING_AGENT_TOOLS = {
    "read_paper",
    "read_webpage",
    "save_paper",
    "search_in_document",
    "show_paper",
}
_ANALYSIS_AGENT_TOOLS = {
    "compare_papers",
    "extract_data",
    "translate_passage",
    "verify_claim",
}


def agent_tool_cost(
    tool_calls: int | Iterable[str], multiplier: float = 1, *, plan: Plan | str = "free"
) -> int:
    """Settle the extra work of an agentic answer after it finishes.

    The base question reservation covers routing plus synthesis. Each tool
    observation causes another routing pass and more context. Later calls are
    progressively more expensive because they carry the accumulated evidence.
    Reading and analysis tools also weigh more than local UI-only actions.
    """
    if isinstance(tool_calls, int):
        tools: list[str | None] = [None] * max(0, tool_calls)
    else:
        tools = [str(tool) for tool in tool_calls]
    if not tools:
        return 0
    weighted_depth = 0.0
    for index, tool in enumerate(tools):
        if tool in _LIGHT_AGENT_TOOLS:
            tool_weight = 0.08
        elif tool in _READING_AGENT_TOOLS:
            tool_weight = 0.28
        elif tool in _ANALYSIS_AGENT_TOOLS:
            tool_weight = 0.34
        else:
            tool_weight = 0.18
        depth_factor = 1.0 if index < 4 else 1.25 if index < 10 else 1.6
        weighted_depth += tool_weight * depth_factor
    profile = capacity_profile(plan)
    return max(1, round(profile.question * max(multiplier, 1) * weighted_depth))


def search_cost(
    multiplier: float = 1,
    *,
    plan: Plan | str = "free",
    screen: bool = False,
    live: bool = False,
    snowball: bool = False,
    semantic: bool = False,
    acquire: bool = False,
    full_text: bool = False,
    web_search: bool = False,
    exhaustive: bool = False,
    document_count: int = 0,
    import_count: int = 0,
) -> int:
    """Scope-aware search reservation.

    The base covers protocol, retrieval and synthesis. Optional workflow arms
    add their own impact; actual screening volume settles separately. The
    selected premium model only affects synthesis-sized work. High-volume
    screening and extraction stay on the economical task route, so applying
    the full chat multiplier to an entire review would misprice it badly.
    """
    base = capacity_profile(plan).search
    synthesis_model_factor = 1.0 + 0.15 * (max(multiplier, 1) - 1)
    scope = (
        (0.12 if screen else 0)
        + (0.08 if live else 0)
        + (0.22 if snowball else 0)
        + (0.18 if semantic else 0)
        + (0.15 if acquire else 0)
        + (0.2 if full_text else 0)
        + (0.15 if web_search else 0)
        + (0.08 if exhaustive else 0)
        + min(max(document_count, 0) * 0.02, 0.2)
        + min(max(import_count, 0) * 0.03, 0.15)
    )
    return max(1, round(base * (synthesis_model_factor + scope)))


def figure_cost(plan: Plan | str = "free") -> int:
    return capacity_profile(plan).figure


def extraction_cost(works: int, plan: Plan | str = "free") -> int:
    return max(0, works) * capacity_profile(plan).extraction_work


def interview_minutes_cost(duration_ms: int, plan: Plan | str = "free") -> int:
    """Transcription plus the first analysis, per started audio minute."""
    minutes = max(1, -(-max(0, duration_ms) // 60000))
    return minutes * capacity_profile(plan).interview_minute


def interview_analysis_cost(plan: Plan | str = "free") -> int:
    """One re-analysis pass: a large structured call over the transcript."""
    return capacity_profile(plan).question * 2


def interview_live_minutes_cost(duration_ms: int, plan: Plan | str = "free") -> int:
    """A live AI-led session, per started conversation minute."""
    minutes = max(1, -(-max(0, duration_ms) // 60000))
    return minutes * capacity_profile(plan).interview_live_minute


def capacity_summary(session: Session, org: Org) -> dict[str, Any]:
    """Return local workflow usage and provider-safety headroom."""
    plan = plan_for_org(org)
    consumed = credits_used_this_month(session, org.id)
    reserved = credits_reserved(session, org.id)
    budget = capacity_budget_for_org(org, plan)
    cycle_start, cycle_end = usage_cycle(org)
    rows = session.execute(
        select(CreditEventRow.action, func.coalesce(func.sum(CreditEventRow.credits), 0))
        .where(CreditEventRow.org_id == org.id, CreditEventRow.created_at >= cycle_start)
        .group_by(CreditEventRow.action)
        .order_by(func.sum(CreditEventRow.credits).desc())
    ).all()
    breakdown = [
        {
            "action": action,
            "label": AI_ACTION_POLICIES[action].label
            if action in AI_ACTION_POLICIES
            else action.replace("_", " ").title(),
            "units": int(units),
        }
        for action, units in rows
    ]
    workflow_remaining = None if budget is None else max(0, budget - consumed - reserved)
    provider_budget = ai_cost_budget_for_org(org, plan)
    provider_reserved = ai_cost_reserved(session, org.id)
    provider_spent = monthly_ai_spend(session, org.id)
    provider_remaining = max(0.0, provider_budget - provider_spent - provider_reserved)
    provider_percent = (
        max(0.0, provider_remaining / provider_budget * 100) if provider_budget > 0 else 0.0
    )
    status = (
        "critical" if provider_percent <= 10 else "low" if provider_percent <= 25 else "healthy"
    )
    return {
        "profile": plan.tier.value,
        "workflow_units_used": consumed,
        "workflow_units_reserved": reserved,
        "workflow_units_limit": budget,
        "workflow_units_remaining": workflow_remaining,
        "provider_budget_usd": provider_budget,
        "provider_spent_usd": provider_spent,
        "provider_reserved_usd": provider_reserved,
        "provider_remaining_usd": provider_remaining,
        "capacity_percent_used": round(100.0 - provider_percent, 1),
        "capacity_percent_remaining": round(provider_percent, 1),
        "capacity_status": status,
        "resets_at": cycle_end.isoformat(),
        "breakdown": breakdown,
    }


def settle_screening_credits(session: Session, run: Run, works_screened: int) -> int:
    """Continuously settle true screening volume against the run reservation."""
    if (run.config or {}).get("search_capacity_pricing") is not None:
        return settle_search_capacity(session, run)
    org = session.get(Org, run.org_id)
    assert org is not None
    plan = plan_for_org(org)
    due = screening_credits(works_screened, plan)
    already = (
        session.scalar(
            select(func.coalesce(func.sum(CreditEventRow.credits), 0)).where(
                CreditEventRow.run_id == run.id, CreditEventRow.action == "screening"
            )
        )
        or 0
    )
    delta = due - already
    if delta <= 0:
        return 0
    reservation = session.scalar(
        select(CapacityReservationRow)
        .where(
            CapacityReservationRow.run_id == run.id,
            CapacityReservationRow.status == "active",
        )
        .order_by(CapacityReservationRow.id.desc())
    )
    if reservation is None:
        check_credits(session, org, delta)
        charge_credits(
            session,
            org,
            action="screening",
            credits=delta,
            run_id=run.id,
            model="ensemble",
        )
    else:
        consume_action_capacity(
            session,
            org,
            action_id=reservation.action_id,
            credits=delta,
            action="screening",
            run_id=run.id,
            model="ensemble",
        )
    return delta


def active_runs(session: Session, org_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.org_id == org_id, Run.status.in_(_ACTIVE_STATES))
        )
        or 0
    )


def active_ai_actions(session: Session, org_id: int, *, action: str | None = None) -> int:
    """Count live provider-backed work after recovering orphaned reservations."""
    release_stale_ai_actions(session, org_id)
    statement = (
        select(func.count())
        .select_from(CapacityReservationRow)
        .where(
            CapacityReservationRow.org_id == org_id,
            CapacityReservationRow.status == "active",
        )
    )
    if action is not None:
        statement = statement.where(CapacityReservationRow.action == action)
    return int(session.scalar(statement) or 0)


def check_survey_questions(org: Org, question_count: int) -> Plan:
    """Enforce the editable survey shape before it reaches public collection."""
    plan = plan_for_org(org)
    if question_count > plan.max_survey_questions:
        raise EntitlementError(
            f"{plan.name}: surveys support up to {plan.max_survey_questions} questions.",
            code="resource_limit",
        )
    return plan


def check_survey_response_available(session: Session, org: Org, survey_id: int) -> Plan:
    """Bound one public survey independently of request throttling."""
    plan = plan_for_org(org)
    used = int(
        session.scalar(
            select(func.count())
            .select_from(SurveyResponseRow)
            .where(SurveyResponseRow.survey_id == survey_id)
        )
        or 0
    )
    if used >= plan.max_survey_responses_per_survey:
        raise EntitlementError(
            f"{plan.name}: this survey has reached its {plan.max_survey_responses_per_survey}-response limit.",
            code="resource_limit",
        )
    return plan


def check_dataset_import(org: Org, *, byte_size: int, row_count: int) -> Plan:
    """Reject oversized dataset payloads before files or database rows persist."""
    plan = plan_for_org(org)
    if byte_size > plan.max_dataset_upload_bytes:
        limit_mb = plan.max_dataset_upload_bytes // (1024 * 1024)
        raise EntitlementError(
            f"{plan.name}: dataset uploads are limited to {limit_mb} MB.",
            code="resource_limit",
        )
    if row_count > plan.max_dataset_rows:
        raise EntitlementError(
            f"{plan.name}: datasets are limited to {plan.max_dataset_rows:,} rows.",
            code="resource_limit",
        )
    return plan


def check_interview_upload(org: Org, *, byte_size: int, duration_ms: int) -> Plan:
    """Bound audio memory and transcription duration before queueing work."""
    plan = plan_for_org(org)
    if byte_size > plan.max_interview_upload_bytes:
        limit_mb = plan.max_interview_upload_bytes // (1024 * 1024)
        raise EntitlementError(
            f"{plan.name}: interview uploads are limited to {limit_mb} MB.",
            code="resource_limit",
        )
    if duration_ms > plan.max_interview_duration_ms:
        limit_minutes = plan.max_interview_duration_ms // 60000
        raise EntitlementError(
            f"{plan.name}: one interview can be up to {limit_minutes} minutes long.",
            code="resource_limit",
        )
    return plan


def check_figure_slot(session: Session, org: Org) -> Plan:
    """Protect image-provider and worker concurrency independently of spend."""
    plan = plan_for_org(org)
    active = active_ai_actions(session, org.id, action="figure")
    if active >= plan.max_concurrent_figures:
        raise EntitlementError(
            f"{plan.name}: {plan.max_concurrent_figures} figure renders are already active. Wait for one to finish before starting another.",
            code="concurrency_limit",
        )
    return plan


def seats_used(session: Session, org_id: int) -> int:
    return session.scalar(select(func.count()).select_from(User).where(User.org_id == org_id)) or 0


def _sum_bytes(session: Session, model: type[Any], org_id: int) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(model.byte_size), 0)).where(model.org_id == org_id)
        )
        or 0
    )


def library_documents_used(session: Session, org_id: int) -> int:
    """Count distinct stored PDFs, not acquisition-ledger duplicates."""
    return int(
        session.scalar(
            select(func.count(func.distinct(DocumentRow.checksum))).where(
                DocumentRow.org_id == org_id,
                DocumentRow.checksum.is_not(None),
                DocumentRow.storage_path.is_not(None),
            )
        )
        or 0
    )


def workspace_storage_used(session: Session, org_id: int) -> int:
    """Persistent bytes attributable to one workspace.

    Documents are content-addressed, so duplicate ledger rows for the same PDF
    count once. Dataset current files and immutable versions are both physical
    files and therefore both count. Database-only manuscript text is excluded.
    """
    document_rows = session.execute(
        select(DocumentRow.checksum, func.max(DocumentRow.byte_size))
        .where(
            DocumentRow.org_id == org_id,
            DocumentRow.checksum.is_not(None),
            DocumentRow.storage_path.is_not(None),
        )
        .group_by(DocumentRow.checksum)
    ).all()
    document_bytes = sum((int(byte_size or 0) for _checksum, byte_size in document_rows))
    return document_bytes + sum(
        _sum_bytes(session, model, org_id)
        for model in (
            WriterAssetRow,
            WriterSourceRow,
            FigureRow,
            ResearchDatasetRow,
            DatasetVersionRow,
            InterviewRow,
            WriterTemplateOriginRow,
        )
    )


def storage_summary(session: Session, org: Org) -> dict[str, int | float | None]:
    plan = plan_for_org(org)
    used = workspace_storage_used(session, org.id)
    limit = plan.max_storage_bytes
    return {
        "storage_bytes_used": used,
        "storage_bytes_limit": limit,
        "storage_percent_used": round(min(100.0, used / limit * 100), 1) if limit else 0.0,
        "library_documents_used": library_documents_used(session, org.id),
        "library_documents_limit": plan.max_library_documents,
    }


def check_storage_available(
    session: Session,
    org: Org,
    incoming_bytes: int,
    *,
    document_checksum: str | None = None,
) -> Plan:
    """Gate one persistent file before it is written.

    ``incoming_bytes`` may be a replacement delta. A smaller replacement is
    always allowed so a workspace can recover after a downgrade.
    """
    settings = get_settings()
    require_storage_capacity(settings.data_dir, settings.storage_reserve_bytes, incoming_bytes)
    if incoming_bytes > 0:
        session.scalar(select(Org.id).where(Org.id == org.id).with_for_update())
    plan = plan_for_org(org)
    delta = incoming_bytes
    is_new_document = False
    if document_checksum is not None:
        duplicate = session.scalar(
            select(DocumentRow.id).where(
                DocumentRow.org_id == org.id,
                DocumentRow.checksum == document_checksum,
                DocumentRow.storage_path.is_not(None),
            )
        )
        is_new_document = duplicate is None
        if not is_new_document:
            delta = 0
        if (
            is_new_document
            and plan.max_library_documents is not None
            and (library_documents_used(session, org.id) >= plan.max_library_documents)
        ):
            raise EntitlementError(
                f"{plan.name}: PDF library limit ({plan.max_library_documents}) reached",
                code="resource_limit",
            )
    limit = plan.max_storage_bytes
    if limit is not None and delta > 0:
        projected = workspace_storage_used(session, org.id) + delta
        if projected > limit:
            limit_mb = max(1, round(limit / (1024 * 1024)))
            raise EntitlementError(
                f"{plan.name}: workspace storage limit ({limit_mb:,} MB) reached",
                code="resource_limit",
            )
    return plan


def check_can_ask(
    session: Session,
    org: Org,
    *,
    multiplier: float = 1,
    input_chars: int = 0,
    context_size: int | None = None,
) -> Plan:
    """Gate one AI question (a quick answer or a chat message)."""
    plan = plan_for_org(org)
    return check_credits(
        session,
        org,
        question_cost(multiplier, plan=plan, input_chars=input_chars, context_size=context_size),
    )


def check_can_create_run(
    session: Session,
    org: Org,
    *,
    live: bool,
    acquire: bool,
    full_text: bool,
    web_search: bool = False,
    ask: bool = False,
    multiplier: float = 1,
    screen: bool = False,
    snowball: bool = False,
    semantic: bool = False,
    exhaustive: bool = False,
    input_chars: int = 0,
    context_size: int | None = None,
    document_count: int = 0,
    import_count: int = 0,
    expert_search: bool = False,
    gate_protocol: bool = False,
) -> Plan:
    session.scalar(select(Org.id).where(Org.id == org.id).with_for_update())
    plan = plan_for_org(org)
    cost = (
        question_cost(multiplier, plan=plan, input_chars=input_chars, context_size=context_size)
        if ask
        else 1
    )
    check_credits(session, org, cost)
    if active_runs(session, org.id) >= plan.max_concurrent_runs:
        raise EntitlementError(
            f"{plan.name}: {plan.max_concurrent_runs} concurrent run(s) already active",
            code="concurrency_limit",
        )
    if (live or snowball or semantic) and (not plan.includes(Capability.DISCOVERY_PACK)):
        raise EntitlementError(
            f"{plan.name} does not include live, semantic and citation discovery",
            code="feature_not_in_plan",
        )
    if expert_search and (not plan.includes(Capability.EXPERT_SEARCH)):
        raise EntitlementError(
            f"{plan.name} does not include expert search controls and imports",
            code="feature_not_in_plan",
        )
    if acquire and (not plan.includes(Capability.DEEP_REVIEW)):
        raise EntitlementError(
            f"{plan.name} does not include full-text acquisition",
            code="feature_not_in_plan",
        )
    if full_text and (not plan.includes(Capability.DEEP_REVIEW)):
        raise EntitlementError(
            f"{plan.name} does not include full-text screening",
            code="feature_not_in_plan",
        )
    if web_search and (not plan.includes(Capability.DEEP_REVIEW)):
        raise EntitlementError(
            f"{plan.name} does not include grey-literature web search",
            code="feature_not_in_plan",
        )
    if gate_protocol and (not plan.includes(Capability.DEEP_REVIEW)):
        raise EntitlementError(
            f"{plan.name} does not include protocol approval gates",
            code="feature_not_in_plan",
        )
    return plan


def effective_works_cap(plan: Plan, requested: int) -> int:
    if plan.max_works_per_run is None:
        return requested
    return min(requested, plan.max_works_per_run)


def check_can_add_member(session: Session, org: Org) -> Plan:
    session.scalar(select(Org.id).where(Org.id == org.id).with_for_update())
    plan = plan_for_org(org)
    if not plan.includes(Capability.TEAM_REVIEW):
        raise EntitlementError(
            f"{plan.name} is a personal workspace; collaboration starts on Cohort",
            code="feature_not_in_plan",
        )
    if plan.max_seats is not None and seats_used(session, org.id) >= plan.max_seats:
        raise EntitlementError(
            f"{plan.name}: seat limit ({plan.max_seats}) reached", code="resource_limit"
        )
    return plan


def check_capability(org: Org, capability: Capability | str) -> Plan:
    """Enforce one stable capability bundle for the current workspace."""
    plan = plan_for_org(org)
    try:
        resolved = Capability(capability)
    except ValueError as exc:
        raise ValueError(f"unknown product capability: {capability!r}") from exc
    if not plan.includes(resolved):
        raise EntitlementError(
            f"{plan.name} does not include {resolved.value.replace('_', ' ')}",
            code="feature_not_in_plan",
        )
    return plan


def check_feature(org: Org, feature: str) -> Plan:
    """Backward-compatible gate for legacy boolean feature names."""
    capability = _LEGACY_FEATURE_CAPABILITY.get(feature)
    if capability is None:
        raise ValueError(f"unknown legacy plan feature: {feature!r}")
    return check_capability(org, capability)
