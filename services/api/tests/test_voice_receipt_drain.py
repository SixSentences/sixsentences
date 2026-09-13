"""Receive-only stop settlement must not authorize new provider work."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from starlette.websockets import WebSocket

from sixsentences_server.voice.relay import LiveRelayConfig, RelayCheckpoint, RelayResult, _Relay
from sixsentences_server.voice.relay_policy import RelayLimits


class _Browser:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def receive(self) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {}

    async def send_json(self, value: dict[str, Any]) -> None:
        self.sent.append(value)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class _Provider:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def emit(self, payload: dict[str, Any]) -> None:
        self.incoming.put_nowait(json.dumps(payload))

    async def send(self, value: str) -> None:
        payload = json.loads(value)
        self.sent.append(payload)
        if "setup" in payload:
            self.emit({"setupComplete": {}})

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> _Provider:
        return self

    async def __anext__(self) -> str:
        return await self.incoming.get()


class _Connection:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider

    async def __aenter__(self) -> _Provider:
        return self.provider

    async def __aexit__(self, *_args: Any) -> None:
        await self.provider.close()


class _Harness:
    def __init__(self, *, meter_timeout: float = 3.0) -> None:
        self.browser = _Browser()
        self.provider = _Provider()
        self.connections = 0
        self.results: list[RelayResult] = []
        configuration = LiveRelayConfig(
            session_id="synthetic-drain-session",
            api_key="synthetic-provider-key",
            prompt="Ask one neutral question.",
            voice="Kore",
            language="en",
            patience_ms=600,
            budget_usd=4.5,
            deadline_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        self.relay = _Relay(
            cast(WebSocket, self.browser),
            configuration,
            self.checkpoint,
            self.closed,
            self.connect,
            replace(RelayLimits(), meter_timeout_seconds=meter_timeout),
        )

    def connect(self, _url: str, _headers: dict[str, str]) -> _Connection:
        self.connections += 1
        assert self.connections == 1, "A receipt drain cannot reconnect or reauthorize."
        return _Connection(self.provider)

    async def checkpoint(self, _value: RelayCheckpoint) -> None:
        # The active-authorization writer may reject a reached deadline.
        # A closed relay must instead persist once via its terminal callback.
        assert not self.relay.stopped.is_set()

    async def closed(self, result: RelayResult) -> None:
        assert self.provider.closed
        self.results.append(result)

    async def start(self, *, phase: str = "generating") -> asyncio.Task[RelayResult]:
        task = asyncio.create_task(self.relay.run())
        await asyncio.wait_for(self.relay.ready.wait(), 1)
        # Represent one already-admitted server-owned activity, without any
        # external connection or actual microphone input in this regression.
        self.relay.budget.begin_turn(time.monotonic())
        self.relay.phase = phase
        self.relay.generation_started = time.monotonic()
        return task


def _receipt() -> dict[str, int]:
    return {"promptTokenCount": 1000, "responseTokenCount": 100, "totalTokenCount": 1100}


@pytest.mark.parametrize("reason", ["stopped", "time"])
@pytest.mark.parametrize("separate_meter", [False, True])
def test_normal_stop_receives_final_meter_without_new_work_or_conversation_time(
    reason: str, separate_meter: bool
) -> None:
    async def scenario() -> None:
        harness = _Harness()
        task = await harness.start()
        harness.relay.stop(reason)
        assert harness.relay.stopped_at is not None
        harness.relay.clock_start = harness.relay.stopped_at - 59.95
        outgoing_at_stop = list(harness.provider.sent)
        visible_at_stop = list(harness.browser.sent)
        await asyncio.sleep(0.025)
        terminal = {
            "serverContent": {
                "turnComplete": True,
                "outputTranscription": {"text": "Not heard after stopping."},
            }
        }
        if separate_meter:
            harness.provider.emit(terminal)
            harness.provider.emit({"usageMetadata": _receipt()})
        else:
            harness.provider.emit(terminal | {"usageMetadata": _receipt()})
        result = await asyncio.wait_for(task, 1)
        assert result.meter_complete
        assert result.observed_cost_usd == result.accounted_cost_usd == 0.0042
        assert result.reserved_cost_usd == 0.0042
        assert result.duration_ms == pytest.approx(59_950, abs=1)
        assert result.duration_ms < 60_000
        assert not result.turns
        assert harness.provider.sent == outgoing_at_stop
        assert harness.connections == 1
        assert harness.browser.sent[: len(visible_at_stop)] == visible_at_stop
        assert not any("serverContent" in message for message in harness.browser.sent)
        assert len(harness.results) == 1
        assert harness.browser.closed

    asyncio.run(scenario())


def test_stopping_during_input_never_sends_activity_end_to_create_an_answer() -> None:
    async def scenario() -> None:
        harness = _Harness()
        task = await harness.start(phase="input")
        outgoing_at_stop = list(harness.provider.sent)
        harness.relay.stop("stopped")
        result = await asyncio.wait_for(task, 0.5)
        assert harness.provider.sent == outgoing_at_stop
        assert not result.meter_complete
        assert result.observed_cost_usd == 0
        assert result.accounted_cost_usd == 1

    asyncio.run(scenario())


def test_partial_meter_does_not_become_final_merely_because_user_stops() -> None:
    async def scenario() -> None:
        harness = _Harness(meter_timeout=0.05)
        task = await harness.start()
        await harness.relay.handle_provider_message({"usageMetadata": _receipt()})
        harness.relay.stop("stopped")
        harness.provider.emit({"serverContent": {"turnComplete": True}})
        result = await asyncio.wait_for(task, 0.5)
        assert result.observed_cost_usd == 0.0042
        assert result.accounted_cost_usd == 1
        assert not result.meter_complete

    asyncio.run(scenario())


def test_missing_meter_remains_uncertain_and_long_configuration_cannot_extend_drain() -> None:
    async def scenario() -> None:
        harness = _Harness(meter_timeout=60)
        task = await harness.start()
        harness.relay.stop("stopped")
        result = await asyncio.wait_for(task, 3.5)
        assert not result.meter_complete
        assert result.observed_cost_usd == 0
        assert result.accounted_cost_usd == result.reserved_cost_usd == 1
        assert harness.provider.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["budget", "connection", "unavailable"])
def test_failure_shutdown_never_waits_for_or_uses_a_later_terminal_receipt(reason: str) -> None:
    async def scenario() -> None:
        harness = _Harness()
        task = await harness.start()
        harness.relay.stop(reason)
        harness.provider.emit(
            {
                "usageMetadata": _receipt(),
                "serverContent": {"turnComplete": True},
            }
        )
        result = await asyncio.wait_for(task, 0.5)
        assert not result.meter_complete
        assert result.observed_cost_usd == 0
        assert result.accounted_cost_usd == 1

    asyncio.run(scenario())


def test_cancellation_interrupts_drain_without_interrupting_terminal_persistence() -> None:
    async def scenario() -> None:
        harness = _Harness()
        task = await harness.start()
        harness.relay.stop("stopped")
        await asyncio.sleep(0.025)
        task.cancel()
        result = await asyncio.wait_for(task, 0.5)
        assert not result.meter_complete
        assert result.accounted_cost_usd == 1
        assert harness.provider.closed
        assert len(harness.results) == 1
        assert harness.browser.closed

    asyncio.run(scenario())


def test_resumption_update_during_drain_does_not_open_another_connection() -> None:
    async def scenario() -> None:
        harness = _Harness()
        task = await harness.start()
        harness.relay.stop("stopped")
        harness.provider.emit(
            {
                "sessionResumptionUpdate": {"resumable": True, "newHandle": "synthetic-handle"},
                "goAway": {"timeLeft": "2s"},
            }
        )
        result = await asyncio.wait_for(task, 0.5)
        assert harness.connections == 1
        assert len(harness.provider.sent) == 1
        assert not result.meter_complete
        assert result.accounted_cost_usd == 1

    asyncio.run(scenario())
