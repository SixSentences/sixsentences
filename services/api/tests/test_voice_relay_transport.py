"""Run the relay over a real WebSocket, on the loopback interface, offline.

`test_voice_relay.py` covers the policy layer thoroughly against an injected
stub socket — rate limits, budget, receipts, language and voice gates — and
`test_actual_provider_transport_disables_proxy_and_all_redirects` pins the class
shape of the real transport. None of it opens a socket, so framing, close codes
and backpressure were never exercised. That gap became concrete in #138, which
took `websockets` from 16.1.1 to 17.1 on the library carrying live interview
audio: the suite passed, and said nothing about the layer the major version
changed.

Everything here talks to a `websockets` server bound to 127.0.0.1 on a port the
kernel picks. No provider, no credential, no outbound connection, no opt-in
marker. The relay still believes it is dialling `PROVIDER_WS_URL`: the connector
asserts the URL it is handed and then opens the real `_connect_provider`
transport — `NoRedirectConnect` subclass included — against the local port.

Async scenarios use `asyncio.run` inside a synchronous test, as the rest of this
suite does, so no test-runner plugin is needed.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from starlette.websockets import WebSocket
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from sixsentences_server.voice.relay import (
    PROVIDER_WS_URL,
    LiveRelayConfig,
    RelayCheckpoint,
    RelayResult,
    _connect_provider,
    run_gemini_relay,
)
from sixsentences_server.voice.relay_policy import RelayLimits

ProviderHandler = Callable[[ServerConnection], Awaitable[None]]


class Browser:
    """The participant side of the relay, with the WebSocket surface it uses."""

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

    def kinds(self) -> list[str]:
        return [next(iter(message), "") for message in self.sent]


@dataclass
class Run:
    """One completed relay run and everything observable about it."""

    browser: Browser
    result: RelayResult | None = None
    checkpoints: list[RelayCheckpoint] = field(default_factory=list)

    @property
    def terminal(self) -> RelayResult:
        assert self.result is not None
        return self.result


def config(session_id: str, **overrides: Any) -> LiveRelayConfig:
    values: dict[str, Any] = {
        "session_id": session_id,
        "api_key": "transport-test-key-not-real",
        "prompt": "Ask one neutral interview question at a time.",
        "voice": "Kore",
        "language": "en",
        "patience_ms": 200,
        "budget_usd": 4.5,
        "deadline_at": datetime.now(UTC) + timedelta(minutes=30),
    }
    return LiveRelayConfig(**(values | overrides))


def audio(size: int = 3200) -> dict[str, Any]:
    """One voiced PCM16 frame, in the only shape the relay accepts."""

    pcm = b"\x00\x30" * (size // 2)
    return {
        "realtimeInput": {
            "audio": {"data": base64.b64encode(pcm).decode(), "mimeType": "audio/pcm;rate=16000"}
        }
    }


def receipt(prompt: int, response: int = 0) -> dict[str, int]:
    return {
        "promptTokenCount": prompt,
        "responseTokenCount": response,
        "totalTokenCount": prompt + response,
    }


@asynccontextmanager
async def local_provider(
    handler: ProviderHandler,
    *,
    process_request: Callable[[ServerConnection, Request], Response | None] | None = None,
    max_queue: int = 16,
) -> AsyncIterator[str]:
    """Serve one throwaway provider on the loopback interface.

    `close_timeout` is the test harness's own patience, not the relay's. A
    provider that has stopped reading never processes the relay's close frame,
    and waiting the library's default ten seconds for a handshake that cannot
    complete would add ten seconds to a suite for nothing.
    """

    async with serve(
        handler,
        "127.0.0.1",
        0,
        process_request=process_request,
        max_queue=max_queue,
        close_timeout=1,
    ) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


async def until(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    """Wait for something to become true, instead of guessing how long it takes."""

    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.005)


async def drive(
    url: str,
    *,
    session_id: str,
    browser: Browser | None = None,
    limits: RelayLimits | None = None,
    configuration: LiveRelayConfig | None = None,
    timeout: float = 20.0,
) -> Run:
    """Run one relay against a local provider and return what happened."""

    run = Run(browser=browser or Browser())

    def connect(requested: str, headers: dict[str, str]) -> Any:
        # The relay must not be told it is talking to anything but the provider.
        assert requested == PROVIDER_WS_URL
        return _connect_provider(url, headers)

    async def on_checkpoint(value: RelayCheckpoint) -> None:
        run.checkpoints.append(value)

    async def on_closed(value: RelayResult) -> None:
        run.result = value

    run.result = await asyncio.wait_for(
        run_gemini_relay(
            cast(WebSocket, run.browser),
            configuration or config(session_id),
            on_checkpoint=on_checkpoint,
            on_closed=on_closed,
            connect_provider=connect,
            limits=limits or RelayLimits(setup_timeout_seconds=2.0),
        ),
        timeout=timeout,
    )
    return run


async def answer_setup(connection: ServerConnection) -> dict[str, Any]:
    """Consume the relay's setup frame and complete the handshake."""

    setup = json.loads(await connection.recv())
    assert isinstance(setup, dict)
    await connection.send(json.dumps({"setupComplete": {}}))
    return cast(dict[str, Any], setup)


def test_a_whole_turn_survives_the_real_transport() -> None:
    """Framing and JSON round trip over a socket rather than an in-process stub."""

    async def scenario() -> None:
        browser = Browser()
        received: list[dict[str, Any]] = []

        async def provider(connection: ServerConnection) -> None:
            received.append(await answer_setup(connection))
            async for raw in connection:
                message = json.loads(raw)
                received.append(message)
                if "activityEnd" in message.get("realtimeInput", {}):
                    await connection.send(
                        json.dumps(
                            {
                                "serverContent": {
                                    "outputTranscription": {"text": "Tell me about your method."},
                                    "turnComplete": True,
                                }
                            }
                        )
                    )
                    await connection.send(json.dumps({"usageMetadata": receipt(120, 60)}))
            # Stay connected: the participant, not the provider, ends this one.
            await connection.wait_closed()

        def turn_complete() -> bool:
            return any(
                message.get("serverContent", {}).get("turnComplete") is True
                for message in browser.sent
            )

        async def participant() -> None:
            for _ in range(4):
                browser.send(audio())
                await asyncio.sleep(0.02)
            # The relay ends the activity after its own patience window, the
            # provider answers, and only then is there a turn to stop after.
            await until(turn_complete, seconds=10.0)
            browser.send({"type": "stop"})

        async with local_provider(provider) as url:
            speaking = asyncio.create_task(participant())
            try:
                run = await drive(url, session_id="transport-happy-path", browser=browser)
            finally:
                speaking.cancel()

        # The provider was configured by the relay, never by the browser.
        assert "setup" in received[0]
        instruction = received[0]["setup"]["systemInstruction"]["parts"][0]["text"]
        assert instruction.startswith("Ask one")
        # Audio arrived as PCM frames, and the turn was ended by the server.
        assert any("audio" in message.get("realtimeInput", {}) for message in received[1:])
        assert any("activityEnd" in message.get("realtimeInput", {}) for message in received[1:])

        assert run.terminal.reason == "stopped"
        assert [turn.text for turn in run.terminal.turns] == ["Tell me about your method."]
        assert run.terminal.observed_cost_usd > 0
        assert "setupComplete" in run.browser.kinds()
        assert run.browser.closed is True

    asyncio.run(scenario())


def test_a_provider_that_closes_mid_stream_ends_the_participant_session() -> None:
    """A clean close upstream must become a defined end downstream, not a hang."""

    async def scenario() -> None:
        async def provider(connection: ServerConnection) -> None:
            await answer_setup(connection)
            await asyncio.sleep(0.05)
            await connection.close(1000, "done")

        async with local_provider(provider) as url:
            run = await drive(url, session_id="transport-clean-close")

        assert run.terminal.reason == "connection"
        assert run.terminal.diagnostic_code == "connection"
        assert run.terminal.diagnostic_stage == "provider_receive"
        # The participant is told, and their socket is closed rather than left open.
        assert "relayEnd" in run.browser.kinds()
        assert run.browser.closed is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("label", "close_code"),
    [("internal error", 1011), ("policy violation", 1008)],
)
def test_an_unexpected_close_code_still_ends_the_session(label: str, close_code: int) -> None:
    """An abnormal close is defined and bounded — but see the diagnostic below."""

    async def scenario() -> None:
        async def provider(connection: ServerConnection) -> None:
            await answer_setup(connection)
            await asyncio.sleep(0.05)
            await connection.close(close_code, label)

        async with local_provider(provider) as url:
            run = await drive(url, session_id=f"transport-close-{close_code}")

        # What matters to the participant holds: a defined end, promptly, with
        # the browser socket closed.
        assert run.terminal.reason == "unavailable"
        assert run.terminal.diagnostic_stage == "provider_receive"
        assert "relayEnd" in run.browser.kinds()
        assert run.browser.closed is True
        # What the operator is told does not. `websockets` raises
        # `ConnectionClosedError`, which is not a `RelayPolicyError`, so the
        # catch-all in `_Relay.run` claims it and reports a provider
        # disconnection as an internal failure of this deployment. Pinned as it
        # behaves today rather than changed here; see the follow-up issue linked
        # from the pull request.
        assert run.terminal.diagnostic_code == "internal_failure"

    asyncio.run(scenario())


def test_a_connection_dropped_without_a_close_frame_ends_the_session() -> None:
    """No close handshake at all: the socket simply disappears."""

    async def scenario() -> None:
        async def provider(connection: ServerConnection) -> None:
            await answer_setup(connection)
            await asyncio.sleep(0.05)
            connection.transport.abort()

        async with local_provider(provider) as url:
            run = await drive(url, session_id="transport-abort")

        assert run.terminal.reason == "unavailable"
        assert run.terminal.diagnostic_stage == "provider_receive"
        assert run.terminal.diagnostic_code == "internal_failure"  # as above
        assert "relayEnd" in run.browser.kinds()
        assert run.browser.closed is True

    asyncio.run(scenario())


def test_a_participant_cannot_outrun_the_ingress_allowance() -> None:
    """Ingress is bounded at the relay, over the real transport.

    The token bucket and the bounded audio queue are unit-tested in
    `test_voice_relay.py`. What this adds is that they still hold when the
    upstream is a socket rather than an in-process object, and that overrunning
    them ends the session cleanly instead of wedging it.

    The burst is queued before the relay starts, so the outcome does not depend
    on how promptly a loaded runner wakes a sleeping task: forty frames is
    128 kB against an allowance of 32 kB plus one second of refill, and no
    scheduling delay makes that fit.
    """

    async def scenario() -> None:
        browser = Browser()
        for _ in range(40):
            browser.send(audio())

        async def provider(connection: ServerConnection) -> None:
            await answer_setup(connection)
            await connection.wait_closed()

        async with local_provider(provider) as url:
            run = await drive(url, session_id="transport-ingress", browser=browser)

        assert run.terminal.reason == "connection"
        assert run.terminal.diagnostic_code == "audio_rate_exceeded"
        assert run.terminal.diagnostic_stage == "browser_receive"
        # Refused rather than absorbed: the rest of the burst is still queued,
        # and almost none of it reached the provider.
        assert browser.incoming.qsize() > 0
        assert run.terminal.input_audio_ms < 2_000
        assert run.browser.closed is True

    asyncio.run(scenario())


def test_a_provider_that_stops_reading_is_only_noticed_at_the_session_deadline() -> None:
    """The upstream accepts the connection, answers setup, and then goes deaf.

    Measured rather than assumed, and the measurement is the point. With the
    participant still speaking, nothing in the relay notices: `watchdog` ends an
    activity only after `patience_ms` of silence, applies
    `generation_timeout_seconds` only once a generation has started, and applies
    `silence_timeout_seconds` only while idle. A speaking participant keeps
    `last_voice` fresh, so the only limit that ever applies is the session
    deadline.

    Nor does the transport push back in time to matter. Run at ten times real
    time while writing this, the relay handed 8.5 MB to a socket nobody was
    draining, across twenty-two seconds, without `send` ever blocking. Loopback
    buffers are generous — but a thirty-minute session at real time is the same
    shape, and the memory is held in a process serving other interviews.

    The deadline is shortened here so the bounded outcome can be asserted in a
    test. What it pins is that the session does end and the participant is told
    — not that the gap is acceptable. See the follow-up issue linked from the
    pull request.
    """

    async def scenario() -> None:
        browser = Browser()

        async def deaf(connection: ServerConnection) -> None:
            # Answer setup, then never call `recv` again and never answer.
            await connection.send(json.dumps({"setupComplete": {}}))
            await connection.wait_closed()

        async def participant() -> None:
            # Real-time pacing: the bucket never empties, so nothing but the
            # deadline can end this.
            for _ in range(40):
                browser.send(audio())
                await asyncio.sleep(0.1)

        async with local_provider(deaf, max_queue=1) as url:
            speaking = asyncio.create_task(participant())
            try:
                run = await drive(
                    url,
                    session_id="transport-deaf-provider",
                    browser=browser,
                    configuration=config(
                        "transport-deaf-provider",
                        deadline_at=datetime.now(UTC) + timedelta(seconds=1.5),
                    ),
                    timeout=25.0,
                )
            finally:
                speaking.cancel()

        # A defined end, and the participant is told and disconnected.
        assert run.terminal.reason == "time"
        assert run.terminal.diagnostic_code == "time"
        assert "relayEnd" in run.browser.kinds()
        assert run.browser.closed is True
        # Everything the participant said up to the deadline was forwarded to a
        # provider that answered nothing. No turn was ever completed.
        assert run.terminal.input_audio_ms > 0
        assert run.terminal.turns == ()

    asyncio.run(scenario())


def test_the_api_key_is_never_re_sent_to_a_redirect_target() -> None:
    """The end-to-end form of the `NoRedirectConnect` guarantee."""

    async def scenario() -> None:
        followed: list[Headers] = []

        async def unreachable(connection: ServerConnection) -> None:
            raise AssertionError("the relay must not reach a redirect target")

        def record(connection: ServerConnection, request: Request) -> Response | None:
            followed.append(request.headers)
            return None

        async with local_provider(unreachable, process_request=record) as target:
            port = target.rsplit(":", maxsplit=1)[1]

            def redirect(connection: ServerConnection, request: Request) -> Response:
                return Response(302, "Found", Headers({"Location": f"ws://127.0.0.1:{port}/moved"}))

            async with local_provider(unreachable, process_request=redirect) as url:
                run = await drive(url, session_id="transport-redirect")

        # Nothing reached the target: no handshake, and therefore no API key.
        assert followed == []
        assert run.terminal.reason == "unavailable"
        assert run.terminal.diagnostic_stage == "provider_receive"
        assert run.browser.closed is True

    asyncio.run(scenario())


def test_a_provider_frame_above_the_transport_limit_ends_the_session() -> None:
    """`_connect_provider` caps `max_size`; an oversized frame must not be parsed."""

    async def scenario() -> None:
        async def oversized(connection: ServerConnection) -> None:
            await connection.send(json.dumps({"setupComplete": {}}))
            await connection.send(json.dumps({"serverContent": {"pad": "x" * 2_000_000}}))
            await connection.wait_closed()

        async with local_provider(oversized) as url:
            run = await drive(url, session_id="transport-oversize")

        assert run.terminal.reason in {"connection", "unavailable"}
        assert run.terminal.diagnostic_stage == "provider_receive"
        assert run.browser.closed is True

    asyncio.run(scenario())
