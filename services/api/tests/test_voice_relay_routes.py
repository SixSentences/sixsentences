"""Keyless application-boundary regressions for spoken interview relays."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient
from participant_fixtures import PARTICIPANT_INFORMATION
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from sixsentences_server.api.app import create_app
from sixsentences_server.api.voice_relay import (
    RelayAuthorizationError,
    claim_voice_relay,
    persist_relay_checkpoint,
    persist_relay_closed,
    relay_still_authorized,
)
from sixsentences_server.config import get_settings
from sixsentences_server.core import study_participation
from sixsentences_server.core.db import (
    CapacityReservationRow,
    CreditEventRow,
    InterviewRow,
    InterviewSegmentRow,
    Org,
    ProviderCostRow,
    User,
    VoiceSessionRow,
    VoiceStudyRow,
    db_session,
)
from sixsentences_server.core.entitlements import (
    release_stale_ai_actions,
    settle_stale_voice_session,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.voice.relay import RelayCheckpoint, RelayResult, RelayTurn


@pytest.fixture
def pilot(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict]:
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "test-relay-provider-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    client = TestClient(create_app())
    created = client.post(
        "/auth/register",
        json={
            "email": "relay-owner@example.org",
            "password": "StrongPass123!",
            "org_name": "Relay routes",
            "name": "Relay",
        },
    )
    assert created.status_code == 201, created.text
    client.headers["Authorization"] = f"Bearer {created.json()['token']}"
    with db_session() as session:
        org = session.scalar(select(Org))
        assert org is not None
        org.plan = "community"
    study = client.post("/voice/studies", json={"title": "Relay", "language": "en"}).json()
    prepared = client.patch(
        f"/voice/studies/{study['id']}", json={"participant_information": PARTICIPANT_INFORMATION}
    )
    assert prepared.status_code == 200, prepared.text
    if prepared.json()["participant_information_ready"] is False:
        prepared = client.patch(
            f"/voice/studies/{study['id']}",
            json={"participant_information": PARTICIPANT_INFORMATION},
        )
        assert prepared.status_code == 200, prepared.text
    started = client.post(f"/voice/studies/{study['id']}/sessions")
    assert started.status_code == 202, started.text
    return (client, started.json())


def _result(*, cost: float = 0.015, certain: bool = True) -> RelayResult:
    return RelayResult(
        observed_cost_usd=cost,
        accounted_cost_usd=cost,
        reserved_cost_usd=cost,
        input_audio_ms=10000,
        turns_started=1,
        provider_connections=1,
        reason="stopped",
        duration_ms=12000,
        meter_complete=certain,
        turns=(
            RelayTurn("interviewer", "How did it go?", 0, 4000),
            RelayTurn("participant", "The guide helped me.", 4000, 12000),
        ),
    )


def test_ticket_is_not_a_provider_credential(pilot: tuple[TestClient, dict]) -> None:
    _, payload = pilot
    assert payload["transport"] == "relay"
    assert payload["ws_url"] == f"/voice/sessions/{payload['id']}/relay"
    assert payload["token"].startswith("six_vr_")
    assert "googleapis" not in str(payload) and "test-relay-provider-key" not in str(payload)
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        assert live.provider_token_issues == 0
        assert live.relay_ticket_hash and payload["token"] not in str(live.relay_state)
        assert live.relay_ticket_hash != payload["token"]


@pytest.mark.parametrize("claimed,expired", [(False, True), (False, False), (True, True)])
def test_only_expired_unclaimed_ticket_releases_a_fresh_pilot_hold(
    pilot: tuple[TestClient, dict], claimed: bool, expired: bool
) -> None:
    """Failed handoffs cannot hold capacity for an unused 30–60 minute session."""
    _, payload = pilot
    if claimed:
        claim_voice_relay(payload["id"], payload["token"])
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        live.relay_ticket_expires_at = datetime.now(UTC) + timedelta(seconds=-1 if expired else 60)
        org_id = live.org_id
    with db_session() as session:
        release_stale_ai_actions(session, org_id)
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        assert live is not None and reservation is not None
        if expired and (not claimed):
            assert live.status == "aborted"
            assert live.duration_ms == live.cost_units == 0
            assert not live.relay_ticket_hash
            assert reservation.status == "expired"
            assert reservation.remaining_credits == 0
        else:
            assert live.status == "running"
            assert reservation.status == "active"
            assert reservation.remaining_credits > 0
        assert session.scalar(select(CreditEventRow)) is None
        assert session.scalar(select(ProviderCostRow)) is None
        release_stale_ai_actions(session, org_id)
    if expired and (not claimed):
        with pytest.raises(RelayAuthorizationError):
            claim_voice_relay(payload["id"], payload["token"])


def test_early_recovery_atomic_predicate_cannot_settle_a_claimed_ticket(
    pilot: tuple[TestClient, dict],
) -> None:
    """The terminal compare-and-set also checks no claim, beyond the caller's read."""
    _, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        assert live is not None and reservation is not None
        study = session.get(VoiceStudyRow, live.study_id)
        assert study is not None
        live.relay_ticket_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()
        settle_stale_voice_session(
            session, reservation, live, study, unclaimed_ticket_expired_before=datetime.now(UTC)
        )
        assert live.status == "running"
        assert reservation.status == "active"
        assert session.scalar(select(CreditEventRow)) is None
        assert session.scalar(select(ProviderCostRow)) is None


def test_ticket_has_one_atomic_owner(pilot: tuple[TestClient, dict]) -> None:
    _, payload = pilot

    def claim() -> bool:
        try:
            config = claim_voice_relay(payload["id"], payload["token"])
            assert config.budget_usd == pytest.approx(9.0)
            assert "test-relay-provider-key" not in repr(config)
            return True
        except RelayAuthorizationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == [False, True]


def test_policy_revision_revokes_an_active_spoken_relay(
    pilot: tuple[TestClient, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    assert relay_still_authorized(payload["id"])
    monkeypatch.setattr(
        study_participation, "VOICE_DPIA_SCOPE_VERSION", "future-spoken-relay-policy"
    )
    assert not relay_still_authorized(payload["id"])
    with pytest.raises(RelayAuthorizationError):
        persist_relay_checkpoint(payload["id"], RelayCheckpoint(0.01, 0.01, 1, 10000, 1, 1))


def test_old_session_cannot_use_a_newly_reapproved_out_of_band_scope(
    pilot: tuple[TestClient, dict],
) -> None:
    """Defense in depth: an old receipt never inherits a replacement approval."""
    _, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    with db_session() as session:
        study = session.scalar(select(VoiceStudyRow))
        assert study is not None
        study.guide = {
            "sections": [{"title": "Replacement scope", "questions": ["A new question?"]}]
        }
        information = dict(study.participant_information or {})
        information["dpia_scope_fingerprint"] = study_participation.voice_study_scope_fingerprint(
            study
        )
        information["dpia_public_scope_token"] = "f" * 32
        study.participant_information = information
        assert study_participation.voice_study_spoken_processing_ready(study)
    assert not relay_still_authorized(payload["id"])
    with pytest.raises(RelayAuthorizationError):
        persist_relay_checkpoint(payload["id"], RelayCheckpoint(0.01, 0.01, 1, 10000, 1, 1))


def test_wrong_session_and_expired_ticket_fail_before_provider(
    pilot: tuple[TestClient, dict],
) -> None:
    _, payload = pilot
    with pytest.raises(RelayAuthorizationError):
        claim_voice_relay("another-session", payload["token"])
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        live.relay_ticket_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(RelayAuthorizationError):
        claim_voice_relay(payload["id"], payload["token"])


@pytest.mark.parametrize(
    "headers,suffix",
    [
        ({}, ""),
        ({"origin": "https://evil.example"}, ""),
        ({"origin": "http://localhost:3000"}, "?token=secret"),
    ],
)
def test_websocket_origin_and_query_gate(
    pilot: tuple[TestClient, dict], headers: dict, suffix: str
) -> None:
    client, payload = pilot
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(payload["ws_url"] + suffix, headers=headers),
    ):
        pytest.fail("An untrusted origin must not establish a relay.")
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.relay_claimed_at is None


@pytest.mark.parametrize(
    "message",
    [
        {"setup": {"model": "evil"}},
        {"authenticate": {"token": "wrong"}},
        {"authenticate": {"token": "x", "extra": True}},
        ["not-an-object"],
    ],
)
def test_invalid_first_frame_has_no_provider_access(
    pilot: tuple[TestClient, dict], message: object
) -> None:
    client, payload = pilot
    with client.websocket_connect(
        payload["ws_url"], headers={"origin": "http://localhost:3000"}
    ) as websocket:
        websocket.send_json(message)
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.relay_claimed_at is None


def test_finalize_without_connect_cannot_fabricate_a_transcript(
    pilot: tuple[TestClient, dict],
) -> None:
    client, payload = pilot
    response = client.post(
        f"/voice/sessions/{payload['id']}/finalize",
        json={
            "duration_ms": 60000,
            "turns": [{"role": "participant", "text": "Fabricated", "end_ms": 60000}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "aborted"
    assert response.json()["cost_units"] == 0
    with pytest.raises(RelayAuthorizationError):
        claim_voice_relay(payload["id"], payload["token"])
    with db_session() as session:
        assert session.scalar(select(InterviewRow)) is None
        assert session.scalar(select(ProviderCostRow)) is None


def test_finalize_cannot_release_a_durably_active_relay(pilot: tuple[TestClient, dict]) -> None:
    client, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    response = client.post(f"/voice/sessions/{payload['id']}/finalize", json={"aborted": True})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_relay_closing"
    with db_session() as session:
        reservation = session.scalar(select(CapacityReservationRow))
        assert reservation is not None and reservation.status == "active"


def test_checkpoint_rechecks_user_and_reservation(pilot: tuple[TestClient, dict]) -> None:
    _, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    checkpoint = RelayCheckpoint(0.001, 0.001, 1.0, 500, 1, 1)
    persist_relay_checkpoint(payload["id"], checkpoint)
    with db_session() as session:
        user = session.scalar(select(User))
        assert user is not None
        user.is_active = False
    assert not relay_still_authorized(payload["id"])
    with pytest.raises(RelayAuthorizationError):
        persist_relay_checkpoint(payload["id"], checkpoint)
    persist_relay_closed(payload["id"], _result())
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.relay_closed_at is not None


def test_customer_meter_is_durable_internal_and_terminal_write_is_idempotent(
    pilot: tuple[TestClient, dict],
) -> None:
    client, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    checkpoint = RelayCheckpoint(0.0042, 0.0042, 1.0, 500, 1, 1, customer_observed_cost_usd=0.0012)
    persist_relay_checkpoint(payload["id"], checkpoint)
    persist_relay_checkpoint(payload["id"], checkpoint)
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        assert live.relay_state["customer_observed_cost_usd"] == 0.0012
        assert live.relay_state["observed_cost_usd"] == 0.0042
        assert live.relay_state["customer_cost_fallback_used"] is False
    result = replace(_result(), customer_observed_cost_usd=0.006)
    persist_relay_closed(payload["id"], result)
    persist_relay_closed(payload["id"], replace(result, customer_observed_cost_usd=0.007))
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        assert live.relay_state["customer_observed_cost_usd"] == 0.006
        assert live.relay_state["observed_cost_usd"] == 0.015
    response = client.post(f"/voice/sessions/{payload['id']}/finalize", json={})
    assert response.status_code == 200
    assert "customer_observed_cost_usd" not in response.text
    assert "customer_cost_fallback_used" not in response.text


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("next_cost,next_fallback", [(0.001, True), (None, True), (0.006, False)])
def test_customer_meter_cannot_regress_or_lose_its_fallback_marker(
    pilot: tuple[TestClient, dict], terminal: bool, next_cost: float | None, next_fallback: bool
) -> None:
    _, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    checkpoint = RelayCheckpoint(
        0.015,
        0.015,
        1.0,
        500,
        1,
        1,
        customer_observed_cost_usd=0.006,
        customer_cost_fallback_used=True,
    )
    persist_relay_checkpoint(payload["id"], checkpoint)
    with pytest.raises(RelayAuthorizationError):
        if terminal:
            persist_relay_closed(
                payload["id"],
                replace(
                    _result(),
                    customer_observed_cost_usd=next_cost,
                    customer_cost_fallback_used=next_fallback,
                ),
            )
        else:
            persist_relay_checkpoint(
                payload["id"],
                replace(
                    checkpoint,
                    customer_observed_cost_usd=next_cost,
                    customer_cost_fallback_used=next_fallback,
                ),
            )
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.relay_closed_at is None
        assert live.relay_state["customer_observed_cost_usd"] == 0.006
        assert live.relay_state["customer_cost_fallback_used"] is True


@pytest.mark.parametrize("cost", [-1.0, True, float("nan"), float("inf"), 0.005])
def test_customer_meter_rejects_invalid_or_above_guard_cost(
    pilot: tuple[TestClient, dict], cost: float
) -> None:
    _, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    with pytest.raises(RelayAuthorizationError):
        persist_relay_checkpoint(
            payload["id"],
            RelayCheckpoint(0.0042, 0.0042, 1.0, 500, 1, 1, customer_observed_cost_usd=cost),
        )


def test_relay_termination_diagnostics_stay_out_of_public_finalization(
    pilot: tuple[TestClient, dict],
) -> None:
    client, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    persist_relay_closed(
        payload["id"],
        replace(
            _result(),
            reason="connection",
            diagnostic_code="audio_rate_exceeded",
            diagnostic_stage="browser_receive",
        ),
    )
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None
        assert live.relay_state["diagnostic_code"] == "audio_rate_exceeded"
        assert live.relay_state["diagnostic_stage"] == "browser_receive"
    response = client.post(f"/voice/sessions/{payload['id']}/finalize", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert "diagnostic" not in response.text
    assert "audio_rate_exceeded" not in response.text


def test_websocket_then_finalize_uses_only_server_receipts(
    pilot: tuple[TestClient, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, payload = pilot

    async def fake_relay(websocket, config, *, on_checkpoint, on_closed):
        assert config.api_key == "test-relay-provider-key"
        await on_checkpoint(RelayCheckpoint(0.01, 0.01, 1, 10000, 1, 1))
        result = _result()
        await on_closed(result)
        await websocket.send_json({"relayEnd": {"reason": "time"}})
        await websocket.close()
        return result

    monkeypatch.setattr("sixsentences_server.api.voice_relay.run_gemini_relay", fake_relay)
    with client.websocket_connect(
        payload["ws_url"], headers={"origin": "http://localhost:3000"}
    ) as websocket:
        websocket.send_json({"authenticate": {"token": payload["token"]}})
        assert websocket.receive_json() == {"relayEnd": {"reason": "time"}}
    response = client.post(
        f"/voice/sessions/{payload['id']}/finalize",
        json={
            "turns": [{"role": "participant", "text": "ATTACKER", "end_ms": 1000000}],
            "duration_ms": 1000000,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    with db_session() as session:
        interview = session.scalar(select(InterviewRow))
        assert interview is not None and interview.duration_ms == 12000
        assert interview.config["source_integrity"] == "provider_transcript"
        segments = session.scalars(
            select(InterviewSegmentRow).order_by(InterviewSegmentRow.idx)
        ).all()
        assert [s.text for s in segments] == ["How did it go?", "The guide helped me."]
        cost = session.scalar(select(ProviderCostRow))
        assert cost is not None and cost.cost_usd == pytest.approx(0.015)
        assert cost.cost_source == "relay_catalog"
    again = client.post(f"/voice/sessions/{payload['id']}/finalize", json={})
    assert again.status_code == 200
    with db_session() as session:
        assert len(session.scalars(select(ProviderCostRow)).all()) == 1


@pytest.mark.parametrize(
    "turns",
    [
        (RelayTurn("participant", "x" * 4001, 0, 1000),),
        (RelayTurn("participant", "x", 1000, 0),),
        (RelayTurn("participant", "x", 0, 1000),) * 601,
    ],
)
def test_terminal_transcript_cannot_be_silently_truncated(
    pilot: tuple[TestClient, dict], turns: tuple[RelayTurn, ...]
) -> None:
    _, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    with pytest.raises(RelayAuthorizationError):
        persist_relay_closed(payload["id"], replace(_result(), turns=turns))
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        assert live is not None and live.relay_closed_at is None


@pytest.mark.parametrize(
    "phase,expected_units,expected_provider,expected_source",
    [
        ("unclaimed", 0, 0.0, None),
        ("closed", 25, 0.015, "relay_catalog"),
        ("closed_uncertain", 5, 0.003, "relay_partial"),
        ("closed_setup_only", 0, 0.0, None),
        ("worker_lost", 15000, 9.0, "relay_bound"),
    ],
)
def test_stale_relay_settlement_preserves_known_and_unknown_exposure(
    pilot: tuple[TestClient, dict],
    phase: str,
    expected_units: int,
    expected_provider: float,
    expected_source: str | None,
) -> None:
    client, payload = pilot
    if phase != "unclaimed":
        claim_voice_relay(payload["id"], payload["token"])
        if phase != "closed_setup_only":
            persist_relay_checkpoint(payload["id"], RelayCheckpoint(0.001, 0.001, 1.0, 500, 1, 1))
    if phase.startswith("closed"):
        result = _result(cost=expected_provider, certain=phase == "closed")
        if phase == "closed_uncertain":
            result = replace(result, accounted_cost_usd=1.0, reserved_cost_usd=1.0)
        elif phase == "closed_setup_only":
            result = replace(
                result,
                input_audio_ms=0,
                turns_started=0,
                turns=(),
                duration_ms=2000,
                reason="unavailable",
                diagnostic_code="setup_timeout",
            )
        persist_relay_closed(payload["id"], result)
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        assert live is not None and reservation is not None
        study = session.get(VoiceStudyRow, live.study_id)
        assert study is not None
        settle_stale_voice_session(session, reservation, live, study)
        assert live.status == "aborted" and live.cost_units == expected_units
        assert not live.relay_ticket_hash
        assert reservation.status == "expired"
        assert reservation.remaining_credits == 0
        if phase.startswith("closed"):
            assert live.relay_state["customer_meter_policy"] == "catalog_proportional_v2"
            assert live.relay_state["customer_cost_usd"] == pytest.approx(expected_provider)
        if phase == "closed_uncertain":
            assert live.relay_state["accounted_cost_usd"] == 1.0
            assert live.relay_state["reserved_cost_usd"] == 1.0
            assert live.relay_state["operator_uncertain_cost_usd"] == pytest.approx(0.997)
        assert (
            sum(event.credits for event in session.scalars(select(CreditEventRow)))
            == expected_units
        )
        provider = session.scalar(select(ProviderCostRow))
        if expected_source is None:
            assert provider is None
        else:
            assert provider is not None
            assert provider.cost_usd == pytest.approx(expected_provider)
            assert provider.cost_source == expected_source
        settle_stale_voice_session(session, reservation, live, study)
    finalized = client.post(f"/voice/sessions/{payload['id']}/finalize", json={})
    assert finalized.status_code == 200 and finalized.json()["status"] == "aborted"
    with db_session() as session:
        assert (
            sum(event.credits for event in session.scalars(select(CreditEventRow)))
            == expected_units
        )
        assert len(session.scalars(select(ProviderCostRow)).all()) == bool(expected_provider)


@pytest.mark.parametrize("observed,expected_units", [(0.0, 0), (0.003, 5), (0.015, 25)])
def test_aborted_closed_relay_charges_observed_usage_not_generation_headroom(
    pilot: tuple[TestClient, dict], observed: float, expected_units: int
) -> None:
    """Stopping early releases future allowance, never erases observed token use."""
    client, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    persist_relay_closed(
        payload["id"],
        replace(
            _result(cost=observed, certain=False),
            accounted_cost_usd=1.0,
            reserved_cost_usd=1.0,
            reason="connection",
            diagnostic_code="audio_rate_exceeded",
            diagnostic_stage="browser_receive",
        ),
    )
    response = client.post(
        f"/voice/sessions/{payload['id']}/finalize",
        json={"aborted": True, "duration_ms": 0, "turns": []},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "aborted"
    assert response.json()["cost_units"] == expected_units
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        provider = session.scalar(select(ProviderCostRow))
        assert live is not None and reservation is not None
        assert live.duration_ms == 12000
        assert live.relay_state["customer_cost_usd"] == pytest.approx(observed)
        assert live.relay_state["accounted_cost_usd"] == 1.0
        assert live.relay_state["reserved_cost_usd"] == 1.0
        assert live.relay_state["meter_complete"] is False
        assert live.relay_state["operator_uncertain_cost_usd"] == pytest.approx(1.0 - observed)
        if observed:
            assert provider is not None and provider.cost_usd == pytest.approx(observed)
            assert provider.cost_source == "relay_partial"
        else:
            assert provider is None
        assert reservation.status == "settled" and reservation.remaining_credits == 0
        assert session.scalar(select(InterviewRow)) is None
    again = client.post(f"/voice/sessions/{payload['id']}/finalize", json={"aborted": True})
    assert again.status_code == 200 and again.json()["cost_units"] == expected_units
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        assert live is not None and reservation is not None
        study = session.get(VoiceStudyRow, live.study_id)
        assert study is not None
        settle_stale_voice_session(session, reservation, live, study)
        assert len(session.scalars(select(ProviderCostRow)).all()) == bool(observed)
        assert (
            sum(event.credits for event in session.scalars(select(CreditEventRow)))
            == expected_units
        )


def test_setup_only_closed_relay_releases_both_holds_without_a_started_minute(
    pilot: tuple[TestClient, dict],
) -> None:
    """A server-confirmed failed setup is different from an admitted generation."""
    client, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    persist_relay_closed(
        payload["id"],
        replace(
            _result(cost=0.0, certain=False),
            input_audio_ms=0,
            turns_started=0,
            turns=(),
            duration_ms=2000,
            reason="unavailable",
            diagnostic_code="setup_timeout",
        ),
    )
    response = client.post(f"/voice/sessions/{payload['id']}/finalize", json={"aborted": True})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "aborted" and response.json()["cost_units"] == 0
    again = client.post(f"/voice/sessions/{payload['id']}/finalize", json={})
    assert again.status_code == 200 and again.json()["cost_units"] == 0
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        reservation = session.scalar(select(CapacityReservationRow))
        assert live is not None and reservation is not None
        assert live.duration_ms == 2000 and live.cost_units == 0
        assert live.relay_state["customer_meter_policy"] == "catalog_proportional_v2"
        assert live.relay_state["customer_cost_usd"] == 0.0
        assert live.relay_state["diagnostic_code"] == "setup_timeout"
        assert reservation.status == "settled" and reservation.remaining_credits == 0
        assert session.scalar(select(CreditEventRow)) is None
        assert session.scalar(select(ProviderCostRow)) is None
        assert session.scalar(select(InterviewRow)) is None


@pytest.mark.parametrize("stale", [False, True])
def test_closed_receipt_overrun_does_not_require_new_capacity_to_finalize(
    pilot: tuple[TestClient, dict], stale: bool
) -> None:
    client, payload = pilot
    claim_voice_relay(payload["id"], payload["token"])
    persist_relay_closed(payload["id"], _result(cost=4.501))
    with db_session() as session:
        live = session.scalar(select(VoiceSessionRow))
        session.add(CreditEventRow(org_id=live.org_id, action="synthetic_prior", credits=10000))
        session.flush()
        if stale:
            reservation = session.scalar(select(CapacityReservationRow))
            settle_stale_voice_session(
                session, reservation, live, session.get(VoiceStudyRow, live.study_id)
            )
    response = client.post(f"/voice/sessions/{payload['id']}/finalize", json={"aborted": True})
    assert response.status_code == 200, response.text
    assert response.json()["cost_units"] == 7502
    with db_session() as session:
        reservation = session.scalar(select(CapacityReservationRow))
        assert reservation.remaining_credits == 0 and reservation.status != "active"
        assert list(
            session.scalars(
                select(CreditEventRow.credits).where(CreditEventRow.action == "interview_live")
            )
        ) == [7502]


def test_queued_live_analysis_rechecks_reapproved_replacement_scope_before_egress(
    pilot: tuple[TestClient, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, payload = pilot
    queued: list[tuple] = []

    def enqueue(_background, function, *args, **_kwargs):
        queued.append((function, args))

    monkeypatch.setattr("sixsentences_server.api.app.enqueue_job", enqueue)
    claim_voice_relay(payload["id"], payload["token"])
    persist_relay_closed(payload["id"], _result())
    response = client.post(f"/voice/sessions/{payload['id']}/finalize", json={})
    assert response.status_code == 200, response.text
    assert len(queued) == 1
    with db_session() as session:
        study = session.scalar(select(VoiceStudyRow))
        assert study is not None
        study_id = study.public_id
    changed = client.patch(f"/voice/studies/{study_id}", json={"title": "New scope"})
    assert changed.status_code == 200, changed.text
    reapproved = client.patch(
        f"/voice/studies/{study_id}", json={"participant_information": PARTICIPANT_INFORMATION}
    )
    assert reapproved.status_code == 200, reapproved.text
    assert reapproved.json()["spoken_processing_ready"] is True
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda *_args, **_kwargs: pytest.fail("stale transcript reached a provider"),
    )
    function, args = queued[0]
    function(*args)
    with db_session() as session:
        interview = session.scalar(select(InterviewRow))
        reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action == "interview_analysis"
            )
        )
        assert interview is not None and interview.analysis == {}
        assert interview.config["analyzing"] is False
        assert reservation is not None and reservation.status != "active"


def test_live_analysis_holds_scope_fence_through_provider_egress(
    pilot: tuple[TestClient, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, payload = pilot
    queued: list[tuple] = []

    def enqueue(_background, function, *args, **_kwargs):
        queued.append((function, args))

    class FakePool:
        clients = {"fake": object()}

        def pinned(self, _ref):
            return self

    provider_entered = Event()
    release_provider = Event()
    mutation_started = Event()
    mutation_completed = Event()

    def analyze(_pool, **kwargs):
        provider_entered.set()
        assert release_provider.wait(5)
        return {
            "summary": "Scoped result.",
            "themes": [],
            "key_findings": [],
            "tensions": [],
            "followups": [],
            "quotes_total": 0,
            "quotes_verified": 0,
            "language": kwargs["language"],
        }

    monkeypatch.setattr("sixsentences_server.api.app.enqueue_job", enqueue)
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda _settings: FakePool())
    monkeypatch.setattr("sixsentences_server.api.app.run_interview_analysis", analyze)
    claim_voice_relay(payload["id"], payload["token"])
    persist_relay_closed(payload["id"], _result())
    response = client.post(f"/voice/sessions/{payload['id']}/finalize", json={})
    assert response.status_code == 200, response.text
    assert len(queued) == 1

    def replace_scope() -> None:
        mutation_started.set()
        with db_session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            study = session.scalar(select(VoiceStudyRow).with_for_update())
            assert study is not None
            study.title = "Replacement after provider"
        mutation_completed.set()

    function, args = queued[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        analysis_future = executor.submit(function, *args)
        assert provider_entered.wait(5)
        mutation_future = executor.submit(replace_scope)
        assert mutation_started.wait(5)
        assert not mutation_completed.wait(0.2)
        release_provider.set()
        analysis_future.result(timeout=5)
        mutation_future.result(timeout=5)
    assert mutation_completed.is_set()
    with db_session() as session:
        interview = session.scalar(select(InterviewRow))
        assert interview is not None
        assert interview.analysis["summary"] == "Scoped result."
