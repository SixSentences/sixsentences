"""Single-use, database-authorized transport for spoken interview sessions.

The browser never receives a Google credential or a provider resumption handle.
Every provider connection belongs to one existing, reserved capacity action.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import re
import secrets
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from anyio import from_thread
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    CapacityReservationRow,
    Org,
    User,
    VoiceInviteRow,
    VoiceSessionRow,
    VoiceStudyRow,
    db_session,
)
from sixsentences_server.core.entitlements import EntitlementError, check_capability
from sixsentences_server.core.legal import legal_reaccept_required
from sixsentences_server.core.plans import Capability
from sixsentences_server.core.ratelimit import build_rate_limiter
from sixsentences_server.core.study_participation import (
    voice_session_scope_matches,
    voice_study_spoken_processing_ready,
)
from sixsentences_server.voice.relay import (
    LiveRelayConfig,
    RelayCheckpoint,
    RelayResult,
    run_gemini_relay,
    stop_gemini_relay,
)
from sixsentences_server.voice.service import LIVE_MODEL

RELAY_TRANSPORT = "relay"
TICKET_LIFETIME_SECONDS = 45
MAX_RELAY_CONNECTIONS = 32


class RelayAuthorizationError(Exception):
    """The stored reservation no longer authorizes provider egress."""


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def prepare_voice_relay(
    live: VoiceSessionRow,
    study: VoiceStudyRow,
    reservation: CapacityReservationRow,
    *,
    user_id: int | None = None,
) -> dict[str, str]:
    """Issue a short-lived app ticket; persist only its hash and fixed setup."""
    ticket = "six_vr_" + secrets.token_urlsafe(32)
    live.transport = RELAY_TRANSPORT
    live.relay_ticket_hash = hashlib.sha256(ticket.encode()).hexdigest()
    live.relay_ticket_expires_at = datetime.now(UTC) + timedelta(seconds=TICKET_LIFETIME_SECONDS)
    live.relay_state = {
        "version": 1,
        "user_id": user_id,
        "voice": study.voice,
        "language": study.language,
        "patience_ms": study.patience_ms,
        "budget_usd": reservation.cost_limit_usd,
    }
    return {
        "transport": RELAY_TRANSPORT,
        "token": ticket,
        # Relative to the app's configured API origin, never the caller Host.
        "ws_url": f"/voice/sessions/{live.public_id}/relay",
    }


def _authorization(
    session: Session, live: VoiceSessionRow, *, now: datetime
) -> tuple[CapacityReservationRow, datetime]:
    settings = get_settings()
    if (
        live.transport != RELAY_TRANSPORT
        or live.status != "running"
        or live.relay_closed_at is not None
        or live.model != LIVE_MODEL
        or not settings.gemini_enabled
        or not 0 < live.authorized_duration_ms <= 60 * 60_000
    ):
        raise RelayAuthorizationError
    deadline = _aware(live.started_at) + timedelta(milliseconds=live.authorized_duration_ms)
    if now >= deadline:
        raise RelayAuthorizationError
    reservation = session.scalar(
        select(CapacityReservationRow).where(
            CapacityReservationRow.org_id == live.org_id,
            CapacityReservationRow.resource_type == "voice_session",
            CapacityReservationRow.resource_id == str(live.id),
            CapacityReservationRow.action == "interview_live",
            CapacityReservationRow.status == "active",
        )
    )
    org = session.get(Org, live.org_id)
    study = session.get(VoiceStudyRow, live.study_id)
    if reservation is None or org is None or study is None or study.org_id != live.org_id:
        raise RelayAuthorizationError
    if not voice_study_spoken_processing_ready(study) or not voice_session_scope_matches(
        study,
        live.consent_snapshot,
    ):
        raise RelayAuthorizationError
    try:
        check_capability(org, Capability.RESEARCH_STUDIO)
    except EntitlementError as exc:
        raise RelayAuthorizationError from exc
    state = live.relay_state or {}
    budget = state.get("budget_usd", 0)
    if (
        isinstance(budget, bool)
        or not isinstance(budget, (int, float))
        or not math.isfinite(budget)
        or not 0 < budget <= reservation.cost_limit_usd + 0.000001
    ):
        raise RelayAuthorizationError
    if live.invite_id is not None:
        invite = session.get(VoiceInviteRow, live.invite_id)
        if (
            not settings.public_gemini_live_enabled
            or invite is None
            or invite.org_id != live.org_id
            or invite.study_id != live.study_id
            or not invite.active
            or (invite.expires_at is not None and _aware(invite.expires_at) <= now)
            or live.consent_at is None
            or not live.consent_fingerprint
        ):
            raise RelayAuthorizationError
    else:
        user_id = state.get("user_id")
        user = session.get(User, user_id) if isinstance(user_id, int) else None
        if (
            user is None
            or user.org_id != live.org_id
            or not user.is_active
            or (settings.require_email_verification and user.email_verified_at is None)
            or (settings.enforce_legal_acceptance and legal_reaccept_required(user))
        ):
            raise RelayAuthorizationError
    return reservation, deadline


def claim_voice_relay(session_id: str, ticket: str) -> LiveRelayConfig:
    """Atomically consume the ticket before opening any billable connection."""
    if not re.fullmatch(r"six_vr_[A-Za-z0-9_-]{43}", ticket):
        raise RelayAuthorizationError
    now = datetime.now(UTC)
    with db_session() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        live = session.scalar(
            select(VoiceSessionRow)
            .where(
                VoiceSessionRow.public_id == session_id,
            )
            .with_for_update()
        )
        if (
            live is None
            or live.relay_claimed_at is not None
            or live.relay_ticket_expires_at is None
            or _aware(live.relay_ticket_expires_at) <= now
            or not secrets.compare_digest(
                live.relay_ticket_hash, hashlib.sha256(ticket.encode()).hexdigest()
            )
        ):
            raise RelayAuthorizationError
        _reservation, deadline = _authorization(session, live, now=now)
        state = live.relay_state
        live.relay_claimed_at = now
        live.relay_ticket_hash = ""
        return LiveRelayConfig(
            session_id=live.public_id,
            api_key=get_settings().gemini_egress_api_key,
            prompt=live.prompt,
            voice=str(state["voice"]),
            language=str(state["language"]),
            patience_ms=int(state["patience_ms"]),
            budget_usd=float(state["budget_usd"]),
            deadline_at=deadline,
        )


def _checkpoint_values(checkpoint: RelayCheckpoint) -> dict[str, Any]:
    values = asdict(checkpoint)
    # Fields in RelayResult are deliberately not part of recurring writes.
    result = {
        key: values[key]
        for key in (
            "observed_cost_usd",
            "accounted_cost_usd",
            "reserved_cost_usd",
            "input_audio_ms",
            "turns_started",
            "provider_connections",
        )
    }
    customer_cost = checkpoint.customer_observed_cost_usd
    if customer_cost is not None:
        if (
            isinstance(customer_cost, bool)
            or not isinstance(customer_cost, (int, float))
            or not math.isfinite(customer_cost)
            or not 0 <= customer_cost <= checkpoint.observed_cost_usd
            or not isinstance(checkpoint.customer_cost_fallback_used, bool)
        ):
            raise RelayAuthorizationError
        result["customer_observed_cost_usd"] = customer_cost
        result["customer_cost_fallback_used"] = checkpoint.customer_cost_fallback_used
    return result


def _check_customer_checkpoint(previous: dict[str, Any], values: dict[str, Any]) -> None:
    """Never replace a recorded customer meter with an older or absent snapshot."""
    prior_cost = previous.get("customer_observed_cost_usd")
    if prior_cost is not None:
        cost = values.get("customer_observed_cost_usd")
        if cost is None or cost < prior_cost:
            raise RelayAuthorizationError
        if previous.get("customer_cost_fallback_used") and not values.get(
            "customer_cost_fallback_used"
        ):
            raise RelayAuthorizationError


def persist_relay_checkpoint(session_id: str, checkpoint: RelayCheckpoint) -> None:
    """Keep server receipts and active exposure durable before more egress."""
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow)
            .where(
                VoiceSessionRow.public_id == session_id,
            )
            .with_for_update()
        )
        if live is None or live.relay_claimed_at is None:
            raise RelayAuthorizationError
        _authorization(session, live, now=datetime.now(UTC))
        previous = dict(live.relay_state or {})
        values = _checkpoint_values(checkpoint)
        # Observed usage is monotonic. The current advance hold may shrink
        # only after the relay has received a complete provider turn receipt.
        if values["observed_cost_usd"] < previous.get("observed_cost_usd", 0):
            raise RelayAuthorizationError
        _check_customer_checkpoint(previous, values)
        live.relay_state = {**previous, **values}


def persist_relay_closed(session_id: str, result: RelayResult) -> None:
    """Persist the terminal provider result, even after an invite is revoked."""
    # The relay splits long provider speech at the API's fragment boundary.
    # Reject a broken internal contract instead of silently losing research
    # material or storing a transcript that HTTP finalization cannot read.
    if len(result.turns) > 600 or any(
        turn.role not in {"interviewer", "participant"}
        or not 1 <= len(turn.text) <= 4000
        or not 0 <= turn.start_ms <= turn.end_ms
        for turn in result.turns
    ):
        raise RelayAuthorizationError
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow)
            .where(
                VoiceSessionRow.public_id == session_id,
            )
            .with_for_update()
        )
        if live is None or live.relay_claimed_at is None or live.relay_closed_at is not None:
            return
        if live.status != "running":
            # A terminal HTTP transition cannot precede relay_closed_at.
            # Recovery/erasure may already have removed its authorization.
            raise RelayAuthorizationError
        state = dict(live.relay_state or {})
        values = _checkpoint_values(result)
        _check_customer_checkpoint(state, values)
        live.relay_state = {
            **state,
            **values,
            "reason": result.reason,
            "duration_ms": max(0, min(result.duration_ms, live.authorized_duration_ms)),
            "meter_complete": result.meter_complete,
            "diagnostic_code": result.diagnostic_code,
            "diagnostic_stage": result.diagnostic_stage,
            "turns": [asdict(turn) for turn in result.turns],
        }
        live.relay_closed_at = datetime.now(UTC)


def relay_still_authorized(session_id: str) -> bool:
    """Recheck revocation separately from participant traffic and provider turns."""
    with db_session() as session:
        live = session.scalar(
            select(VoiceSessionRow).where(
                VoiceSessionRow.public_id == session_id,
            )
        )
        if live is None:
            return False
        try:
            _authorization(session, live, now=datetime.now(UTC))
        except RelayAuthorizationError:
            return False
    return True


def stop_relay_before_finalize(
    session_id: str, *, org_id: int | None = None, invite_token: str | None = None
) -> None:
    """Stop a caller-owned upstream outside any SQL lock before HTTP settlement.

    This helper runs in FastAPI's synchronous handler thread. Across replicas,
    the durable active claim still fails closed instead of releasing capacity
    while a different process could be spending it.
    """
    with db_session() as session:
        query = select(VoiceSessionRow).where(VoiceSessionRow.public_id == session_id)
        if org_id is not None:
            query = query.where(VoiceSessionRow.org_id == org_id)
        elif invite_token is not None:
            query = query.join(VoiceInviteRow, VoiceInviteRow.id == VoiceSessionRow.invite_id)
            query = query.where(VoiceInviteRow.public_id == invite_token)
        else:
            raise HTTPException(404, "Session not found.")
        live = session.scalar(query)
        if live is None:
            raise HTTPException(404, "Session not found.")
        active = (
            live.transport == RELAY_TRANSPORT
            and live.relay_claimed_at is not None
            and live.relay_closed_at is None
            and live.status == "running"
        )
    if active:
        try:
            from_thread.run(stop_gemini_relay, session_id)
        except TimeoutError as exc:
            raise HTTPException(
                409,
                {
                    "code": "voice_relay_closing",
                    "message": "The interview is still finishing. Please save again in a moment.",
                },
            ) from exc


def require_relay_closed_for_settlement(live: VoiceSessionRow) -> None:
    """Never accept browser finalization as proof that provider egress stopped."""
    if live.transport != RELAY_TRANSPORT:
        return
    if live.relay_claimed_at is not None and live.relay_closed_at is None:
        raise HTTPException(
            409,
            {
                "code": "voice_relay_closing",
                "message": "The interview is still finishing. Please save again in a moment.",
            },
        )
    live.relay_ticket_hash = ""


def _origin_allowed(websocket: WebSocket) -> bool:
    settings = get_settings()
    allowed = {settings.app_url.rstrip("/")}
    allowed.update(item.strip().rstrip("/") for item in settings.cors_origins.split(","))
    origin = websocket.headers.get("origin", "")
    parsed = urlsplit(origin)
    return bool(
        origin in allowed
        and parsed.scheme in {"http", "https"}
        and parsed.netloc
        and not parsed.username
        and not parsed.password
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def install_voice_relay_routes(app: FastAPI) -> None:
    """Install the bounded app transport; authentication is its first frame."""
    connections = 0
    limiter = build_rate_limiter("voice-relay-connect", max_hits=120, window_seconds=60)

    @app.websocket("/voice/sessions/{session_id}/relay")
    async def voice_relay_socket(websocket: WebSocket, session_id: str) -> None:
        nonlocal connections
        if (
            not _origin_allowed(websocket)
            or websocket.query_params
            or not re.fullmatch(r"[a-z0-9]{10,32}", session_id)
            or connections >= MAX_RELAY_CONNECTIONS
        ):
            await websocket.close(code=1008)
            return
        connections += 1
        runner: asyncio.Task[RelayResult] | None = None
        watcher: asyncio.Task[None] | None = None
        try:
            key = websocket.client.host if websocket.client else "unknown"
            if not await run_in_threadpool(limiter.allow, key):
                await websocket.close(code=1008)
                return
            await websocket.accept()
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            if len(raw.encode()) > 512:
                raise RelayAuthorizationError
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {"authenticate"}:
                raise RelayAuthorizationError
            auth = payload["authenticate"]
            if not isinstance(auth, dict) or set(auth) != {"token"}:
                raise RelayAuthorizationError
            ticket = auth["token"]
            if not isinstance(ticket, str):
                raise RelayAuthorizationError
            config = await run_in_threadpool(claim_voice_relay, session_id, ticket)

            async def checkpoint(value: RelayCheckpoint) -> None:
                await run_in_threadpool(persist_relay_checkpoint, session_id, value)

            async def closed(value: RelayResult) -> None:
                await run_in_threadpool(persist_relay_closed, session_id, value)

            async def watch_authorization() -> None:
                while True:
                    await asyncio.sleep(2)
                    if not await run_in_threadpool(relay_still_authorized, session_id):
                        return

            runner = asyncio.create_task(
                run_gemini_relay(
                    websocket,
                    config,
                    on_checkpoint=checkpoint,
                    on_closed=closed,
                )
            )
            watcher = asyncio.create_task(watch_authorization())
            done, _pending = await asyncio.wait(
                (runner, watcher), return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done and not runner.done():
                runner.cancel()
            await runner
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            # No reflected provider text, credentials or traceback payloads.
            with contextlib.suppress(Exception):
                await websocket.close(code=1008, reason="Interview connection unavailable.")
        finally:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            if runner is not None and not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            connections -= 1
