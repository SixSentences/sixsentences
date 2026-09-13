"""Offline policy and transport tests: no browser, microphone, or provider egress."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from starlette.websockets import WebSocket

from sixsentences_server.voice.relay import (
    PROVIDER_WS_URL,
    LiveRelayConfig,
    RelayCheckpoint,
    RelayResult,
    _connect_provider,
    _Relay,
    build_relay_setup,
    run_gemini_relay,
    stop_gemini_relay,
)
from sixsentences_server.voice.relay_policy import (
    LiveRelayBudget,
    PcmRateGate,
    RelayLimits,
    RelayPolicyError,
    decode_client_audio,
    usage_upper_cost_microusd,
)


def config(**overrides: Any) -> LiveRelayConfig:
    values = {
        "session_id": "relay-test-session",
        "api_key": "test-provider-key-not-real",
        "prompt": "Ask one neutral interview question at a time.",
        "voice": "Kore",
        "language": "en",
        "patience_ms": 600,
        "budget_usd": 4.5,
        "deadline_at": datetime.now(UTC) + timedelta(minutes=30),
    }
    return LiveRelayConfig(**(values | overrides))


def audio_message(*, size: int = 3200, silent: bool = False) -> dict[str, Any]:
    pcm = (b"\x00\x00" if silent else b"\x00\x30") * (size // 2)
    return {
        "realtimeInput": {
            "audio": {
                "data": base64.b64encode(pcm).decode(),
                "mimeType": "audio/pcm;rate=16000",
            }
        }
    }


def receipt(prompt: int, response: int = 0) -> dict[str, int]:
    return {
        "promptTokenCount": prompt,
        "responseTokenCount": response,
        "totalTokenCount": prompt + response,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"setup": {"model": "models/expensive"}},
        {"clientContent": {"turns": [{"parts": [{"text": "bypass"}]}]}},
        {"realtimeInput": {"text": "bypass"}},
        {"realtimeInput": {"activityStart": {}}},
        {"toolResponse": {}},
        {"sessionResumption": {"handle": "attacker"}},
        {"usageMetadata": receipt(0)},
        audio_message() | {"setup": {}},
        {"realtimeInput": {"audio": {"data": "YQ==", "mimeType": "audio/pcm;rate=16000"}}},
        {"realtimeInput": {"audio": {"data": "####", "mimeType": "audio/pcm;rate=16000"}}},
        {"realtimeInput": {"audio": {"data": "AAAA", "mimeType": "audio/pcm;rate=24000"}}},
        audio_message(size=6402),
    ],
)
def test_browser_cannot_control_setup_tools_turns_meter_or_audio_format(payload: Any) -> None:
    with pytest.raises(RelayPolicyError, match="invalid_audio"):
        decode_client_audio(payload, RelayLimits())


def test_only_bounded_audio_or_stop_is_accepted() -> None:
    assert len(decode_client_audio(audio_message(), RelayLimits()) or b"") == 3200
    assert decode_client_audio({"type": "stop"}, RelayLimits()) is None


def test_setup_is_server_owned_manual_vad_and_no_secret_in_configuration_repr() -> None:
    setup = build_relay_setup(config(), "provider-owned-checkpoint")["setup"]
    assert setup["generationConfig"]["maxOutputTokens"] == 512
    assert setup["realtimeInputConfig"]["automaticActivityDetection"] == {"disabled": True}
    assert setup["contextWindowCompression"] == {
        "slidingWindow": {"targetTokens": "8000"},
        "triggerTokens": "25000",
    }
    assert setup["sessionResumption"] == {"handle": "provider-owned-checkpoint"}
    assert "test-provider-key" not in repr(config())
    assert "Ask one neutral" not in repr(config())


def test_audio_cannot_run_faster_than_the_server_clock() -> None:
    rate = PcmRateGate(10.0, RelayLimits())
    # One second of bounded browser/network jitter, never sustained speedup.
    for _ in range(10):
        rate.admit(3200, 10.0)
    with pytest.raises(RelayPolicyError, match="audio_rate_exceeded"):
        rate.admit(3200, 10.0)
    rate.admit(3200, 10.1)


def test_actual_provider_transport_disables_proxy_and_all_redirects() -> None:
    async def scenario() -> None:
        connection = _connect_provider(PROVIDER_WS_URL, {"x-goog-api-key": "fake-key"})
        assert connection.proxy is None
        redirect = RuntimeError("redirect to an untrusted host")
        assert connection.process_redirect(redirect) is redirect

    asyncio.run(scenario())


def test_advance_headroom_is_required_before_a_turn_and_barge_in() -> None:
    budget = LiveRelayBudget(1.9)
    budget.begin_turn(1.0)
    assert budget.committed_microusd == 1_000_000
    with pytest.raises(RelayPolicyError, match="capacity_reached"):
        budget.begin_turn(2.0, interrupt=True)
    assert len(budget.turns) == 1
    budget.conservative_finish()
    assert budget.accounted_microusd == 1_000_000
    assert not budget.meter_complete


def test_barge_in_retains_unfinished_generation_exposure() -> None:
    budget = LiveRelayBudget(4.5)
    budget.begin_turn(1.0)
    budget.observe(receipt(100))
    budget.begin_turn(2.0, interrupt=True)
    assert budget.accounted_microusd == 1_000_000
    assert budget.committed_microusd == 2_000_000
    budget.observe(receipt(1000, 100))
    budget.close_turn(certain=True)
    assert budget.accounted_microusd == 1_004_200
    assert not budget.meter_complete


def test_authenticated_receipts_are_monotonic_and_unknown_counts_cost_more_not_less() -> None:
    assert usage_upper_cost_microusd(receipt(1000, 100)) == 4200
    assert (
        usage_upper_cost_microusd(
            {
                "promptTokenCount": 1000,
                "responseTokenCount": 100,
                "totalTokenCount": 1200,
            }
        )
        == 5400
    )
    budget = LiveRelayBudget(4.5)
    budget.begin_turn(1.0)
    budget.observe(receipt(1000, 100))
    budget.observe(receipt(100, 10))
    budget.close_turn(certain=True)
    assert budget.accounted_microusd == 4200
    budget.observe(receipt(2000, 200))
    assert budget.accounted_microusd == 8400


def test_separately_reported_thinking_and_tool_counts_are_not_dropped() -> None:
    separate = receipt(1000, 100) | {"thoughtsTokenCount": 50, "toolUsePromptTokenCount": 25}
    assert usage_upper_cost_microusd(separate) == 5100
    assert usage_upper_cost_microusd(separate | {"totalTokenCount": 1175}) == 5100
    assert usage_upper_cost_microusd(separate | {"totalTokenCount": 1200}) == 5400
    with pytest.raises(RelayPolicyError, match="meter_unavailable"):
        usage_upper_cost_microusd(separate | {"thoughtsTokenCount": -1})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        receipt(-1),
        receipt(True),
        receipt(1.5),
        {"promptTokenCount": 100, "responseTokenCount": 100, "totalTokenCount": 150},
        {"promptTokenCount": "100", "responseTokenCount": 0, "totalTokenCount": 100},
    ],
)
def test_invalid_provider_meter_fails_closed(payload: Any) -> None:
    with pytest.raises(RelayPolicyError, match="meter_unavailable"):
        usage_upper_cost_microusd(payload)


def test_spend_receipt_above_inflight_headroom_is_terminal_and_retained() -> None:
    budget = LiveRelayBudget(4.5)
    budget.begin_turn(0.0)
    with pytest.raises(RelayPolicyError, match="capacity_reached"):
        budget.observe(receipt(400_000))
    budget.conservative_finish()
    assert budget.observed_microusd == budget.accounted_microusd == 1_200_000


def test_unexpected_setup_spend_is_retained_and_fails_closed() -> None:
    budget = LiveRelayBudget(4.5)
    with pytest.raises(RelayPolicyError, match="meter_unavailable"):
        budget.observe(receipt(1000))
    budget.conservative_finish()
    assert budget.accounted_microusd == budget.observed_microusd == 3000
    assert not budget.meter_complete


class Browser:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    def send(self, payload: dict[str, Any]) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "text": json.dumps(payload)})


class Provider:
    def __init__(
        self,
        on_send: Callable[[dict[str, Any]], None] | None = None,
        *,
        setup_delay: float = 0,
    ) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.on_send = on_send
        self.setup_delay = setup_delay

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        self.sent.append(payload)
        if "setup" in payload:
            if self.setup_delay:
                asyncio.get_running_loop().call_later(
                    self.setup_delay, self.emit, {"setupComplete": {}}
                )
            else:
                self.emit({"setupComplete": {}})
        if self.on_send:
            self.on_send(payload)

    def emit(self, payload: dict[str, Any]) -> None:
        self.incoming.put_nowait(json.dumps(payload))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> Provider:
        return self

    async def __anext__(self) -> str:
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value


class Connection:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    async def __aenter__(self) -> Provider:
        return self.provider

    async def __aexit__(self, *_args: Any) -> None:
        await self.provider.close()


class Harness:
    def __init__(self, providers: list[Provider] | None = None) -> None:
        self.browser = Browser()
        self.providers = providers or [Provider()]
        self.connections: list[tuple[str, dict[str, str]]] = []
        self.checkpoints: list[RelayCheckpoint] = []
        self.results: list[RelayResult] = []

    def connect(self, url: str, headers: dict[str, str]) -> Connection:
        if self.connections:
            assert self.providers[len(self.connections) - 1].closed
        self.connections.append((url, headers))
        return Connection(self.providers[len(self.connections) - 1])

    async def checkpoint(self, value: RelayCheckpoint) -> None:
        self.checkpoints.append(value)

    async def closed(self, value: RelayResult) -> None:
        assert all(provider.closed for provider in self.providers[: len(self.connections)])
        assert not any("relayEnd" in message for message in self.browser.sent)
        self.results.append(value)

    def start(
        self, configuration: LiveRelayConfig | None = None, **kwargs: Any
    ) -> asyncio.Task[RelayResult]:
        return asyncio.create_task(
            run_gemini_relay(
                cast(WebSocket, self.browser),
                configuration or config(),
                on_checkpoint=kwargs.pop("on_checkpoint", self.checkpoint),
                on_closed=kwargs.pop("on_closed", self.closed),
                connect_provider=self.connect,
                **kwargs,
            )
        )


async def until(condition: Callable[[], bool], seconds: float = 2.0) -> None:
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.005)


def test_terminal_diagnostics_separate_invalid_audio_from_disconnection() -> None:
    async def scenario() -> None:
        harness = Harness()
        task = harness.start()
        await until(lambda: bool(harness.connections))
        harness.browser.send({"realtimeInput": {"text": "not an audio frame"}})
        result = await asyncio.wait_for(task, 2)
        assert result.reason == "connection"
        assert result.diagnostic_code == "invalid_audio"
        assert result.diagnostic_stage == "browser_receive"
        assert "diagnostic" not in json.dumps(harness.browser.sent)
        assert "not an audio frame" not in json.dumps(harness.browser.sent)

        disconnected = Harness()
        task = disconnected.start()
        await until(lambda: bool(disconnected.connections))
        disconnected.browser.incoming.put_nowait({"type": "websocket.disconnect"})
        result = await asyncio.wait_for(task, 2)
        assert result.diagnostic_code == "client_disconnected"
        assert result.diagnostic_stage == "browser_receive"

    asyncio.run(scenario())


def test_customer_meter_reaches_content_free_checkpoint_and_terminal_result() -> None:
    async def scenario() -> None:
        harness = Harness()
        relay = _Relay(
            cast(WebSocket, harness.browser),
            config(),
            harness.checkpoint,
            harness.closed,
            harness.connect,
            RelayLimits(),
        )
        relay.budget.begin_turn(0)
        relay.budget.observe(
            receipt(1000, 100)
            | {
                "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 1000}],
                "responseTokensDetails": [{"modality": "TEXT", "tokenCount": 100}],
            }
        )
        await relay.persist()
        assert harness.checkpoints[-1].customer_observed_cost_usd == 0.0012
        assert harness.checkpoints[-1].observed_cost_usd == 0.0042
        assert not harness.checkpoints[-1].customer_cost_fallback_used
        result = await relay._close_and_persist([])
        assert result.customer_observed_cost_usd == 0.0012
        assert result.observed_cost_usd == 0.0042
        assert result.accounted_cost_usd == 1.0
        assert "customer_observed_cost_usd" not in json.dumps(harness.browser.sent)

    asyncio.run(scenario())


def test_long_transcript_fragments_preserve_text_and_match_api_limits() -> None:
    harness = Harness()
    relay = _Relay(
        cast(WebSocket, harness.browser),
        config(),
        harness.checkpoint,
        harness.closed,
        harness.connect,
        RelayLimits(),
    )
    text = "word " * 1800
    relay.append_transcript("participant", text[:3500])
    relay.append_transcript("participant", text[3500:])
    assert [len(turn.text) for turn in relay.transcript] == [4000, 4000, 1000]
    assert "".join(turn.text for turn in relay.transcript) == text
    relay.transcript.clear()
    for index in range(600):
        relay.append_transcript("participant" if index % 2 else "interviewer", "word")
    with pytest.raises(RelayPolicyError, match="session_limit"):
        relay.append_transcript("interviewer", "one more")
    assert len(relay.transcript) == 600


def test_actual_relay_rejects_browser_setup_without_forwarding_or_echoing_provider_key() -> None:
    async def scenario() -> None:
        harness = Harness()
        task = harness.start()
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send({"setup": {"model": "models/attacker"}})
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "connection"
        assert harness.providers[0].sent == [build_relay_setup(config())]
        assert "test-provider-key" not in json.dumps(harness.browser.sent)
        assert harness.connections[0][0] == PROVIDER_WS_URL
        assert "?" not in harness.connections[0][0]

    asyncio.run(scenario())


def test_relay_stops_on_metered_exposure_before_forwarding_more_audio() -> None:
    async def scenario() -> None:
        harness = Harness()
        task = harness.start()
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send(audio_message())
        await until(lambda: any("realtimeInput" in item for item in harness.providers[0].sent))
        harness.providers[0].emit({"usageMetadata": receipt(400_000)})
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "budget"
        assert result.accounted_cost_usd == 1.2
        assert result.observed_cost_usd == 1.2
        assert harness.providers[0].closed
        assert harness.browser.sent[-1] == {"relayEnd": {"reason": "budget"}}
        assert result.input_audio_ms <= 100

    asyncio.run(scenario())


def test_relay_natural_pause_completes_a_turn_and_blocks_next_unaffordable_generation() -> None:
    async def scenario() -> None:
        provider = Provider()

        def respond(payload: dict[str, Any]) -> None:
            if "activityEnd" in payload.get("realtimeInput", {}):
                provider.emit(
                    {
                        "usageMetadata": receipt(100_000),
                        "serverContent": {
                            "inputTranscription": {"text": "My answer."},
                            "outputTranscription": {"text": "Thank you."},
                            "turnComplete": True,
                        },
                    }
                )

        provider.on_send = respond
        harness = Harness([provider])
        task = harness.start(config(budget_usd=1.2))
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send(audio_message())
        await until(
            lambda: bool(harness.checkpoints) and harness.checkpoints[-1].reserved_cost_usd == 0.3
        )
        harness.browser.send(audio_message())
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "budget"
        assert sum("activityStart" in item.get("realtimeInput", {}) for item in provider.sent) == 1
        assert result.accounted_cost_usd == 0.3
        assert [turn.text for turn in result.turns] == ["My answer.", "Thank you."]
        assert result.meter_complete

    asyncio.run(scenario())


def test_missing_final_meter_ends_session_and_retains_inflight_allowance() -> None:
    async def scenario() -> None:
        provider = Provider()

        def respond(payload: dict[str, Any]) -> None:
            if "activityEnd" in payload.get("realtimeInput", {}):
                provider.emit({"serverContent": {"turnComplete": True}})

        provider.on_send = respond
        harness = Harness([provider])
        task = harness.start(limits=replace(RelayLimits(), meter_timeout_seconds=0.05))
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send(audio_message())
        result = await asyncio.wait_for(task, 2)
        assert result.reason == "unavailable"
        assert result.accounted_cost_usd == 1.0
        assert not result.meter_complete

    asyncio.run(scenario())


def test_streaming_meter_before_turn_complete_cannot_release_headroom() -> None:
    async def scenario() -> None:
        provider = Provider()

        def respond(payload: dict[str, Any]) -> None:
            realtime = payload.get("realtimeInput", {})
            if "audio" in realtime:
                provider.emit({"usageMetadata": receipt(1000)})
            if "activityEnd" in realtime:
                provider.emit({"serverContent": {"turnComplete": True}})

        provider.on_send = respond
        harness = Harness([provider])
        task = harness.start(limits=replace(RelayLimits(), meter_timeout_seconds=0.3))
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send(audio_message())
        result = await asyncio.wait_for(task, 2)
        assert result.reason == "unavailable"
        assert result.observed_cost_usd == 0.003
        assert result.accounted_cost_usd == 1.0
        assert not result.meter_complete

    asyncio.run(scenario())


def test_delayed_interrupted_terminal_cannot_settle_a_new_generation() -> None:
    async def scenario() -> None:
        harness = Harness()
        relay = _Relay(
            cast(WebSocket, harness.browser),
            config(),
            harness.checkpoint,
            harness.closed,
            harness.connect,
            RelayLimits(),
        )
        relay.provider = harness.providers[0]
        relay.ready.set()
        relay.budget.begin_turn(1.0)
        relay.phase = "generating"
        await relay._begin_activity(2.0)
        await relay._end_activity()
        await relay.handle_provider_message(
            {
                "usageMetadata": receipt(1000),
                "serverContent": {"interrupted": True, "turnComplete": True},
            }
        )
        assert relay.phase == "generating"
        assert not relay.terminal_meter_seen
        assert relay.budget.committed_microusd == 2_000_000
        await relay.handle_provider_message(
            {
                "usageMetadata": receipt(2000),
                "serverContent": {"turnComplete": True},
            }
        )
        assert relay.phase == "settling"
        assert relay.terminal_meter_seen
        relay.budget.close_turn(certain=True)
        assert relay.budget.accounted_microusd == 1_006_000

    asyncio.run(scenario())


def test_cancel_closes_upstream_before_terminal_callback_and_retains_pending_cost() -> None:
    async def scenario() -> None:
        harness = Harness()
        task = harness.start()
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send(audio_message())
        await until(
            lambda: bool(harness.checkpoints) and harness.checkpoints[-1].turns_started == 1
        )
        task.cancel()
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "connection"
        assert result.accounted_cost_usd == 1.0
        assert len(harness.results) == 1
        assert harness.browser.closed

    asyncio.run(scenario())


def test_repeated_cancellation_cannot_interrupt_durable_terminal_write() -> None:
    async def scenario() -> None:
        harness = Harness()
        write_started, allow_write = asyncio.Event(), asyncio.Event()

        async def delayed_close(result: RelayResult) -> None:
            assert harness.providers[0].closed
            write_started.set()
            await allow_write.wait()
            await harness.closed(result)

        task = harness.start(on_closed=delayed_close)
        await until(lambda: bool(harness.browser.sent))
        task.cancel()
        await asyncio.wait_for(write_started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert not any("relayEnd" in item for item in harness.browser.sent)
        allow_write.set()
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "connection"
        assert len(harness.results) == 1
        assert harness.browser.closed

    asyncio.run(scenario())


def test_server_resumes_only_provider_checkpoint_without_leaking_it_to_browser() -> None:
    async def scenario() -> None:
        harness = Harness([Provider(), Provider()])
        task = harness.start(config(deadline_at=datetime.now(UTC) + timedelta(minutes=60)))
        await until(lambda: bool(harness.browser.sent))
        harness.providers[0].emit(
            {
                "sessionResumptionUpdate": {"resumable": True, "newHandle": "provider-resume-only"},
                "goAway": {"timeLeft": "30s"},
            }
        )
        await until(lambda: bool(harness.providers[1].sent))
        assert harness.providers[1].sent[0]["setup"]["sessionResumption"] == {
            "handle": "provider-resume-only"
        }
        assert "provider-resume-only" not in json.dumps(harness.browser.sent)
        assert await stop_gemini_relay("relay-test-session")
        result = await asyncio.wait_for(task, 1)
        assert result.provider_connections == 2
        assert result.reason == "stopped"

    asyncio.run(scenario())


def test_rotation_waits_for_setup_during_audio_pacing_and_pending_activity_end() -> None:
    async def scenario() -> None:
        first, second = Provider(), Provider(setup_delay=0.8)
        harness = Harness([first, second])
        task = harness.start()
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send(audio_message())
        # Queue a second real-time frame, then rotate while it awaits pacing.
        harness.browser.send(audio_message())
        await until(lambda: any("audio" in item.get("realtimeInput", {}) for item in first.sent))
        first.emit(
            {
                "sessionResumptionUpdate": {"resumable": True, "newHandle": "same-turn"},
                "goAway": {"timeLeft": "30s"},
            }
        )
        await until(lambda: bool(second.sent))
        await asyncio.sleep(0.7)
        assert not task.done()
        assert len(second.sent) == 1  # No audio/end before resumed setup.
        await until(
            lambda: any("activityEnd" in item.get("realtimeInput", {}) for item in second.sent)
        )
        assert sum("activityStart" in item.get("realtimeInput", {}) for item in first.sent) == 1
        assert not any("activityStart" in item.get("realtimeInput", {}) for item in second.sent)
        assert second.sent[0]["setup"]["sessionResumption"] == {"handle": "same-turn"}
        assert await stop_gemini_relay("relay-test-session")
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "stopped"
        assert result.turns_started == 1
        assert result.accounted_cost_usd == 1.0
        assert result.provider_connections == 2

    asyncio.run(scenario())


def test_goaway_waits_for_fresh_safe_checkpoint_without_resetting_turn_budget() -> None:
    async def scenario() -> None:
        first, second = Provider(), Provider()
        harness = Harness([first, second])
        task = harness.start()
        await until(lambda: bool(harness.browser.sent))
        harness.browser.send(audio_message())
        await until(
            lambda: any("activityEnd" in item.get("realtimeInput", {}) for item in first.sent)
        )
        first.emit({"sessionResumptionUpdate": {"resumable": False, "newHandle": "unsafe"}})
        first.emit({"goAway": {"timeLeft": "5s"}})
        await asyncio.sleep(0.1)
        assert not task.done() and len(harness.connections) == 1
        first.emit({"usageMetadata": receipt(1000), "serverContent": {"turnComplete": True}})
        first.emit({"sessionResumptionUpdate": {"resumable": True, "newHandle": "fresh-safe"}})
        await until(lambda: bool(second.sent))
        assert second.sent[0]["setup"]["sessionResumption"] == {"handle": "fresh-safe"}
        await until(lambda: harness.checkpoints[-1].reserved_cost_usd == 0.003)
        assert await stop_gemini_relay("relay-test-session")
        result = await asyncio.wait_for(task, 1)
        assert result.turns_started == 1
        assert result.accounted_cost_usd == 0.003
        assert result.provider_connections == 2

    asyncio.run(scenario())


def test_goaway_without_safe_checkpoint_has_a_hard_wait_deadline() -> None:
    async def scenario() -> None:
        harness = Harness()
        task = harness.start()
        await until(lambda: bool(harness.browser.sent))
        harness.providers[0].emit({"goAway": {"timeLeft": "0.05s"}})
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "connection"
        assert result.provider_connections == 1

    asyncio.run(scenario())


def test_checkpoint_failure_prevents_upstream_connection() -> None:
    async def scenario() -> None:
        harness = Harness()

        async def reject(_checkpoint: RelayCheckpoint) -> None:
            raise RuntimeError("private database detail must never reach the user")

        result = await asyncio.wait_for(harness.start(on_checkpoint=reject), 1)
        assert result.reason == "unavailable"
        assert not harness.connections
        assert "database" not in json.dumps(harness.browser.sent)

    asyncio.run(scenario())


def test_checkpoint_snapshots_and_writes_are_serialized_in_monotonic_order() -> None:
    async def scenario() -> None:
        harness = Harness()
        first_started, release_first = asyncio.Event(), asyncio.Event()
        writes: list[float] = []
        entered = 0

        async def delayed_checkpoint(value: RelayCheckpoint) -> None:
            nonlocal entered
            entered += 1
            if entered == 1:
                first_started.set()
                await release_first.wait()
            writes.append(value.observed_cost_usd)

        relay = _Relay(
            cast(WebSocket, harness.browser),
            config(),
            delayed_checkpoint,
            harness.closed,
            harness.connect,
            RelayLimits(),
        )
        first = asyncio.create_task(relay.persist())
        await asyncio.wait_for(first_started.wait(), 1)
        relay.budget.begin_turn(1.0)
        relay.budget.observe(receipt(1000))
        second = asyncio.create_task(relay.persist())
        await asyncio.sleep(0.01)
        assert entered == 1
        relay.budget.observe(receipt(2000))
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        # The waiting writer snapshots after the lock, not before it.
        assert writes == [0.0, 0.006]

    asyncio.run(scenario())


def test_server_enforces_deadline_without_waiting_for_browser_to_stop() -> None:
    async def scenario() -> None:
        harness = Harness()
        task = harness.start(config(deadline_at=datetime.now(UTC) + timedelta(seconds=0.05)))
        result = await asyncio.wait_for(task, 1)
        assert result.reason == "time"
        assert harness.providers[0].closed

    asyncio.run(scenario())
