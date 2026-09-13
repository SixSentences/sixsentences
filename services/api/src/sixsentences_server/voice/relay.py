"""Server-owned Gemini Live transport with bounded audio and advance admission.

No Gemini credential, setup surface, or resumption handle crosses the browser
boundary. The relay enforces an application spending cutoff with conservative
in-flight headroom. It does not claim an exact, provider-enforced invoice cap.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import sys
import time
from array import array
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from starlette.websockets import WebSocket, WebSocketDisconnect

from sixsentences_server.voice.relay_policy import (
    LiveRelayBudget,
    PcmRateGate,
    RelayLimits,
    RelayPolicyError,
    decode_client_audio,
    usd_from_microusd,
)
from sixsentences_server.voice.service import (
    LIVE_CONTEXT_TARGET_TOKENS,
    LIVE_CONTEXT_TRIGGER_TOKENS,
    LIVE_MAX_OUTPUT_TOKENS,
    LIVE_MODEL,
    LIVE_SESSION_MAX_MINUTES,
    VOICES,
)

PROVIDER_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


@dataclass(frozen=True)
class LiveRelayConfig:
    """Immutable, database-authorized session data; never browser configuration."""

    session_id: str
    api_key: str = field(repr=False)
    prompt: str = field(repr=False)
    voice: str
    language: str
    patience_ms: int
    budget_usd: float
    deadline_at: datetime


@dataclass(frozen=True)
class RelayTurn:
    """A transcript fragment received from the authenticated provider socket."""

    role: str
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class RelayCheckpoint:
    """Content-free progress for a durable, monotonic accounting checkpoint."""

    observed_cost_usd: float
    accounted_cost_usd: float
    reserved_cost_usd: float
    input_audio_ms: int
    turns_started: int
    provider_connections: int
    # None preserves the conservative meaning of historical/older receipts.
    customer_observed_cost_usd: float | None = field(default=None, kw_only=True)
    customer_cost_fallback_used: bool = field(default=False, kw_only=True)


@dataclass(frozen=True)
class RelayResult(RelayCheckpoint):
    """Terminal relay facts; callers must not replace them with browser claims."""

    reason: str
    duration_ms: int
    turns: tuple[RelayTurn, ...]
    meter_complete: bool
    # Internal fixed-code diagnostics, never provider messages or browser copy.
    diagnostic_code: str = ""
    diagnostic_stage: str = ""


class ProviderSocket(Protocol):
    """Small transport surface, injectable for deterministic offline tests."""

    async def send(self, message: str) -> None: ...
    async def close(self) -> None: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


class ProviderConnection(Protocol):
    async def __aenter__(self) -> ProviderSocket: ...
    async def __aexit__(self, *args: Any) -> None: ...


ProviderConnector = Callable[[str, dict[str, str]], ProviderConnection]
CheckpointCallback = Callable[[RelayCheckpoint], Awaitable[None]]
ClosedCallback = Callable[[RelayResult], Awaitable[None]]


def _connect_provider(url: str, headers: dict[str, str]) -> ProviderConnection:
    # Imported lazily so policy tests do not need an optional network transport.
    from websockets.asyncio.client import connect

    class NoRedirectConnect(connect):
        # The API key must never follow a provider or proxy redirect.
        def process_redirect(self, exc: Exception) -> Exception:
            return exc

    return NoRedirectConnect(
        url,
        additional_headers=headers,
        proxy=None,
        open_timeout=15,
        close_timeout=3,
        max_size=1_048_576,
        max_queue=8,
        ping_interval=20,
        ping_timeout=20,
    )


def build_relay_setup(config: LiveRelayConfig, resume_handle: str = "") -> dict[str, Any]:
    """Only the relay may configure generation, activity, or provider resumption."""
    if (
        config.voice not in VOICES
        or config.language not in {"de", "en"}
        or not config.prompt.strip()
        or len(config.prompt.encode("utf-8")) > 65_536
        or not config.api_key
    ):
        raise RelayPolicyError("unavailable")
    language = "de-DE" if config.language == "de" else "en-US"
    return {
        "setup": {
            "model": LIVE_MODEL,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "maxOutputTokens": LIVE_MAX_OUTPUT_TOKENS,
                "thinkingConfig": {"thinkingLevel": "minimal"},
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config.voice}},
                    "languageCode": language,
                },
            },
            "systemInstruction": {"parts": [{"text": config.prompt}]},
            "inputAudioTranscription": {
                "languageCodes": [language],
                "mode": "VERBATIM",
            },
            "outputAudioTranscription": {
                "languageCodes": [language],
                "mode": "VERBATIM",
            },
            "realtimeInputConfig": {
                "automaticActivityDetection": {"disabled": True},
                "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
                "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",
            },
            "contextWindowCompression": {
                "slidingWindow": {"targetTokens": str(LIVE_CONTEXT_TARGET_TOKENS)},
                "triggerTokens": str(LIVE_CONTEXT_TRIGGER_TOKENS),
            },
            "sessionResumption": {"handle": resume_handle} if resume_handle else {},
        }
    }


def _rms(pcm: bytes) -> float:
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768


_ACTIVE_RELAYS: dict[str, _Relay] = {}


async def stop_gemini_relay(session_id: str, *, timeout: float = 8.0) -> bool:
    """Stop and drain a locally owned relay before accepting HTTP finalization."""
    relay = _ACTIVE_RELAYS.get(session_id)
    if relay is None:
        return False
    relay.stop("stopped")
    await asyncio.wait_for(relay.finished.wait(), timeout=timeout)
    return True


class _Relay:
    def __init__(
        self,
        client: WebSocket,
        config: LiveRelayConfig,
        on_checkpoint: CheckpointCallback,
        on_closed: ClosedCallback,
        connector: ProviderConnector,
        limits: RelayLimits,
    ) -> None:
        self.client = client
        self.config = config
        self.on_checkpoint = on_checkpoint
        self.on_closed = on_closed
        self.connector = connector
        self.limits = limits
        self.clock_start = time.monotonic()
        deadline = config.deadline_at
        if deadline.tzinfo is None:
            raise RelayPolicyError("unavailable")
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        self.deadline = self.clock_start + min(LIVE_SESSION_MAX_MINUTES * 60, remaining)
        self.budget = LiveRelayBudget(config.budget_usd, limits=limits)
        self.rate = PcmRateGate(self.clock_start, limits)
        self.stopped = asyncio.Event()
        self.finished = asyncio.Event()
        self.ready = asyncio.Event()
        self.policy_lock = asyncio.Lock()
        self.persist_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.client_send_lock = asyncio.Lock()
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=20)
        self.provider: ProviderSocket | None = None
        self.connections = 0
        self.reason = "connection"
        self.stopped_at: float | None = None
        self.receipt_drain_requested = False
        self.diagnostic_code = ""
        self.diagnostic_stage = "relay"
        self.phase = "idle"
        self.last_voice = self.clock_start
        self.activity_bytes = 0
        self.transcript_bytes = 0
        self.forwarded_audio_bytes = 0
        self.generation_started = 0.0
        self.completed_at = 0.0
        self.terminal_meter_seen = False
        self.pending_interrupted_terminals = 0
        self.setup_started = self.clock_start
        self.last_checkpoint = self.clock_start
        self.resume_handle = ""
        self.go_away_deadline: float | None = None
        self.transcript: list[RelayTurn] = []
        self.prefix: deque[bytes] = deque(maxlen=2)

    def stop(self, reason: str) -> None:
        if self.stopped.is_set():
            return
        self.reason = reason
        self.stopped_at = time.monotonic()
        # Only keep receiving a response that was already authorized and
        # started. Ending an input activity here could trigger new generation
        # after the participant stopped, so an input-only turn never drains.
        self.receipt_drain_requested = bool(
            reason in {"stopped", "time"}
            and self.phase in {"generating", "settling"}
            and self.budget.current is not None
            and self.provider is not None
            and self.ready.is_set()
        )
        self.stopped.set()
        self.ready.set()

    def checkpoint(self) -> RelayCheckpoint:
        return RelayCheckpoint(
            observed_cost_usd=usd_from_microusd(self.budget.observed_microusd),
            accounted_cost_usd=usd_from_microusd(self.budget.accounted_microusd),
            reserved_cost_usd=usd_from_microusd(self.budget.committed_microusd),
            input_audio_ms=self.forwarded_audio_bytes * 1000 // self.limits.pcm_bytes_per_second,
            turns_started=len(self.budget.turns),
            provider_connections=self.connections,
            customer_observed_cost_usd=usd_from_microusd(
                self.budget.customer_observed_microusd,
            ),
            customer_cost_fallback_used=self.budget.customer_cost_fallback_used,
        )

    async def persist(self) -> None:
        # Build the snapshot only after acquiring the write-order lock. A slow
        # periodic checkpoint must not commit behind a newer usage receipt and
        # trip the durable monotonic-meter guard.
        async with self.persist_lock:
            await self.on_checkpoint(self.checkpoint())
            self.last_checkpoint = time.monotonic()

    async def send_client(self, payload: dict[str, Any]) -> None:
        async with self.client_send_lock:
            await self.client.send_json(payload)

    async def send_provider(self, payload: dict[str, Any]) -> None:
        # A provider rotation may begin after the audio task's readiness check
        # or while it waits for pacing. Wait for the same resumed conversation;
        # the independent setup/session watchdog still bounds this wait.
        while not self.stopped.is_set():
            await self.ready.wait()
            async with self.send_lock:
                if self.stopped.is_set():
                    break
                if self.provider is None or not self.ready.is_set():
                    continue
                await self.provider.send(json.dumps(payload, separators=(",", ":")))
                return
        raise RelayPolicyError("connection")

    async def receive_client(self) -> None:
        while not self.stopped.is_set():
            event = await self.client.receive()
            if event.get("type") == "websocket.disconnect":
                self.diagnostic_code = "client_disconnected"
                self.diagnostic_stage = "browser_receive"
                self.stop("connection")
                return
            raw = event.get("text")
            if (
                not isinstance(raw, str)
                or len(raw.encode("utf-8")) > self.limits.max_client_message_bytes
            ):
                raise RelayPolicyError("invalid_audio")
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise RelayPolicyError("invalid_audio") from exc
            pcm = decode_client_audio(payload, self.limits)
            if pcm is None:
                self.stop("stopped")
                return
            now = time.monotonic()
            self.rate.admit(len(pcm), now)
            if now >= self.deadline:
                self.stop("time")
                return
            try:
                self.audio_queue.put_nowait(pcm)
            except asyncio.QueueFull as exc:
                raise RelayPolicyError("audio_rate_exceeded") from exc

    async def _begin_activity(self, now: float) -> None:
        async with self.policy_lock:
            interrupt = self.phase == "generating"
            self.budget.begin_turn(now, interrupt=interrupt)
            if interrupt:
                self.pending_interrupted_terminals += 1
            # Durably register headroom BEFORE even activityStart can trigger
            # provider work or interrupt an already-authorized generation.
            await self.persist()
            self.activity_bytes = 0
            self.transcript_bytes = 0
            self.phase = "input"
            self.last_voice = now
            self.completed_at = 0.0
            self.terminal_meter_seen = False
        await self.send_provider({"realtimeInput": {"activityStart": {}}})

    async def _send_pcm(self, pcm: bytes) -> None:
        maximum = int(self.limits.max_turn_audio_seconds * self.limits.pcm_bytes_per_second)
        if self.activity_bytes + len(pcm) > maximum:
            raise RelayPolicyError("session_limit")
        await self.send_provider(
            {
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(pcm).decode("ascii"),
                        "mimeType": "audio/pcm;rate=16000",
                    }
                }
            }
        )
        self.activity_bytes += len(pcm)
        self.forwarded_audio_bytes += len(pcm)

    async def process_audio(self) -> None:
        next_send = time.monotonic()
        while not self.stopped.is_set():
            pcm = await self.audio_queue.get()
            await self.ready.wait()
            if self.stopped.is_set():
                return
            # Do not let a delayed browser burst accelerate upstream audio.
            await asyncio.sleep(max(0.0, next_send - time.monotonic()))
            await self.ready.wait()
            if self.stopped.is_set():
                return
            next_send = time.monotonic() + len(pcm) / self.limits.pcm_bytes_per_second
            voiced = _rms(pcm) >= 0.018
            now = time.monotonic()
            if self.phase == "settling":
                # A short bounded hold gives the terminal usage frame time to
                # arrive before another response is authorized.
                while self.phase == "settling" and not self.stopped.is_set():
                    await asyncio.sleep(0.025)
                if self.stopped.is_set():
                    return
            if self.phase in {"idle", "generating"}:
                if not voiced:
                    self.prefix.append(pcm)
                    continue
                await self._begin_activity(now)
                for previous in self.prefix:
                    await self._send_pcm(previous)
                self.prefix.clear()
            if self.phase == "input":
                if voiced:
                    self.last_voice = now
                await self._send_pcm(pcm)

    async def _end_activity(self) -> None:
        if self.phase != "input" or not self.ready.is_set():
            return
        self.phase = "generating"
        self.generation_started = time.monotonic()
        await self.send_provider({"realtimeInput": {"activityEnd": {}}})

    def append_transcript(self, role: str, text: Any) -> None:
        if not isinstance(text, str):
            raise RelayPolicyError("meter_unavailable")
        self.transcript_bytes += len(text.encode("utf-8"))
        if self.transcript_bytes > self.limits.max_transcript_bytes_per_turn:
            raise RelayPolicyError("session_limit")
        if not text:
            return
        elapsed = max(0, int((time.monotonic() - self.clock_start) * 1000))
        # Match the API's durable VoiceTurn contract without dropping the
        # tail of a long provider transcription or creating invalid rows.
        while text:
            if (
                self.transcript
                and self.transcript[-1].role == role
                and len(self.transcript[-1].text) < 4000
            ):
                prior = self.transcript[-1]
                available = 4000 - len(prior.text)
                self.transcript[-1] = RelayTurn(
                    role, prior.text + text[:available], prior.start_ms, elapsed
                )
                text = text[available:]
                continue
            if len(self.transcript) >= 600:
                raise RelayPolicyError("session_limit")
            self.transcript.append(RelayTurn(role, text[:4000], elapsed, elapsed))
            text = text[4000:]

    async def handle_provider_message(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            raise RelayPolicyError("connection")
        if "error" in payload:
            raise RelayPolicyError("unavailable")
        content = payload.get("serverContent")
        interrupted_terminal = bool(
            self.pending_interrupted_terminals
            and isinstance(content, dict)
            and content.get("turnComplete") is True
        )
        if "usageMetadata" in payload:
            async with self.policy_lock:
                self.budget.observe(payload["usageMetadata"])
                if not self.pending_interrupted_terminals and (
                    self.phase == "settling"
                    or (
                        self.phase == "generating"
                        and isinstance(content, dict)
                        and content.get("turnComplete") is True
                    )
                ):
                    self.terminal_meter_seen = True
                # A stopped relay admits no work. In the short receive-only
                # drain, the final callback owns durable settlement; recurring
                # checkpoints must not extend an expired authorization.
                if not self.stopped.is_set():
                    await self.persist()
        resume = payload.get("sessionResumptionUpdate")
        if isinstance(resume, dict):
            handle = resume.get("newHandle")
            self.resume_handle = (
                handle
                if resume.get("resumable") is True
                and isinstance(handle, str)
                and len(handle) <= 8192
                else ""
            )
        if "setupComplete" in payload:
            self.ready.set()
            if not self.stopped.is_set():
                await self.send_client({"setupComplete": {}})
                await self.send_client({"relayState": {"phase": "live"}})
        content = payload.get("serverContent")
        if isinstance(content, dict):
            # Content is provider-originated, but restrict forwarding to the
            # exact audio/transcript surface. Tools and operational fields are
            # not relayed to participants.
            safe_content: dict[str, Any] = {}
            if content.get("interrupted") is True:
                safe_content["interrupted"] = True
            for name, role in (
                ("inputTranscription", "participant"),
                ("outputTranscription", "interviewer"),
            ):
                transcription = content.get(name)
                if (
                    not self.stopped.is_set()
                    and isinstance(transcription, dict)
                    and "text" in transcription
                ):
                    self.append_transcript(role, transcription["text"])
                    safe_content[name] = {"text": transcription["text"]}
            model_turn = content.get("modelTurn")
            if isinstance(model_turn, dict):
                parts = model_turn.get("parts", [])
                if not isinstance(parts, list) or len(parts) > 64:
                    raise RelayPolicyError("connection")
                safe_parts = []
                for part in parts:
                    inline = part.get("inlineData") if isinstance(part, dict) else None
                    if not isinstance(inline, dict):
                        continue
                    data = inline.get("data")
                    mime = inline.get("mimeType")
                    if (
                        not isinstance(data, str)
                        or not isinstance(mime, str)
                        or mime not in {"audio/pcm;rate=24000", "audio/pcm"}
                        or len(data) > self.limits.max_provider_message_bytes
                    ):
                        raise RelayPolicyError("connection")
                    safe_parts.append({"inlineData": {"data": data, "mimeType": mime}})
                if safe_parts:
                    safe_content["modelTurn"] = {"parts": safe_parts}
            if interrupted_terminal:
                # An interrupted generation emits its own terminal message,
                # possibly after the next input has already ended. Its old
                # allowance remains reserved; never finish the new turn with
                # that delayed old receipt. Ambiguous receipts above may only
                # increase the current conservative maximum, not release it.
                self.pending_interrupted_terminals -= 1
            elif content.get("turnComplete") is True and self.phase == "generating":
                self.phase = "settling"
                self.completed_at = time.monotonic()
                safe_content["turnComplete"] = True
            if safe_content and not self.stopped.is_set():
                await self.send_client({"serverContent": safe_content})
        if "goAway" in payload:
            # Google may temporarily mark an in-progress generation as not
            # resumable. Its GoAway announces a deadline, not an immediate
            # disconnect: wait briefly for a fresh safe checkpoint instead of
            # throwing away the current response or using an old handle.
            away = payload["goAway"]
            duration = away.get("timeLeft") if isinstance(away, dict) else None
            if not isinstance(duration, str) or not duration.endswith("s"):
                raise RelayPolicyError("connection")
            try:
                seconds = float(duration[:-1])
            except ValueError as exc:
                raise RelayPolicyError("connection") from exc
            if not math.isfinite(seconds) or seconds <= 0:
                raise RelayPolicyError("connection")
            deadline = time.monotonic() + min(30.0, seconds)
            self.go_away_deadline = min(self.go_away_deadline or deadline, deadline)
        if self.go_away_deadline is not None and self.resume_handle:
            self.go_away_deadline = None
            return True
        return False

    async def run_provider(self) -> None:
        handle = ""
        while not self.stopped.is_set():
            if self.connections >= self.limits.max_provider_connections:
                raise RelayPolicyError("connection")
            self.ready.clear()
            self.setup_started = time.monotonic()
            if self.connections:
                await self.send_client({"relayState": {"phase": "reconnecting"}})
            self.connections += 1
            await self.persist()
            async with self.connector(
                PROVIDER_WS_URL, {"x-goog-api-key": self.config.api_key}
            ) as provider:
                self.provider = provider
                await provider.send(json.dumps(build_relay_setup(self.config, handle)))
                self.resume_handle = ""
                async for raw in provider:
                    if self.stopped.is_set() and not self.receipt_drain_requested:
                        return
                    if len(raw) > self.limits.max_provider_message_bytes:
                        raise RelayPolicyError("connection")
                    try:
                        payload = json.loads(raw)
                    except (ValueError, TypeError, UnicodeError) as exc:
                        raise RelayPolicyError("connection") from exc
                    if await self.handle_provider_message(payload):
                        break
                async with self.send_lock:
                    self.ready.clear()
                    self.provider = None
            # Only a safe provider-originated checkpoint may reopen the same
            # session. No fresh-session fallback and no browser-supplied handle.
            if self.stopped.is_set():
                return
            if not self.resume_handle:
                raise RelayPolicyError("connection")
            handle = self.resume_handle

    async def watchdog(self) -> None:
        while not self.stopped.is_set():
            now = time.monotonic()
            if now >= self.deadline:
                self.stop("time")
                return
            if self.go_away_deadline is not None and now >= self.go_away_deadline:
                raise RelayPolicyError("connection")
            if (
                not self.ready.is_set()
                and now - self.setup_started >= self.limits.setup_timeout_seconds
            ):
                raise RelayPolicyError("connection")
            if self.phase == "input" and now - self.last_voice >= min(
                3.0, max(0.6, self.config.patience_ms / 1000)
            ):
                await self._end_activity()
            if (
                self.phase == "generating"
                and now - self.generation_started >= self.limits.generation_timeout_seconds
            ):
                raise RelayPolicyError("meter_unavailable")
            if self.phase == "settling":
                current = self.budget.current
                elapsed = now - self.completed_at
                if (
                    current is not None
                    and current.meter_seen
                    and self.terminal_meter_seen
                    and elapsed >= 0.2
                ):
                    async with self.policy_lock:
                        self.budget.close_turn(certain=True)
                        self.phase = "idle"
                        await self.persist()
                elif elapsed >= self.limits.meter_timeout_seconds:
                    raise RelayPolicyError("meter_unavailable")
            if (
                self.phase == "idle"
                and now - self.last_voice >= self.limits.silence_timeout_seconds
            ):
                self.stop("time")
                return
            if now - self.last_checkpoint >= self.limits.checkpoint_interval_seconds:
                await self.persist()
            await asyncio.sleep(0.05)

    async def run(self) -> RelayResult:
        tasks: list[asyncio.Task[Any]] = []
        stage = "relay"
        try:
            build_relay_setup(self.config)
            if self.deadline <= time.monotonic():
                raise RelayPolicyError("time")
            tasks = [
                asyncio.create_task(task(), name=name)
                for name, task in (
                    ("browser_receive", self.receive_client),
                    ("audio_send", self.process_audio),
                    ("provider_receive", self.run_provider),
                    ("watchdog", self.watchdog),
                )
            ]
            tasks.append(asyncio.create_task(self.stopped.wait(), name="stop_signal"))
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                stage = task.get_name()
                task.result()
        except RelayPolicyError as exc:
            self.diagnostic_code = (
                exc.reason
                if exc.reason
                in {
                    "capacity_reached",
                    "session_limit",
                    "time",
                    "unavailable",
                    "meter_unavailable",
                    "connection",
                    "invalid_audio",
                    "audio_rate_exceeded",
                }
                else "policy_failure"
            )
            self.diagnostic_stage = stage
            self.stop(
                "budget"
                if exc.reason in {"capacity_reached", "session_limit"}
                else "time"
                if exc.reason == "time"
                else "unavailable"
                if exc.reason in {"unavailable", "meter_unavailable"}
                else "connection"
            )
        except WebSocketDisconnect:
            self.diagnostic_code = "client_disconnected"
            self.diagnostic_stage = stage
            self.stop("connection")
        except asyncio.CancelledError:
            self.diagnostic_code = "cancelled"
            self.diagnostic_stage = stage
            self.stop("connection")
        except Exception:
            # No provider exception text, payload, URL, or key is logged or
            # returned to the participant. A persistence failure is terminal.
            self.diagnostic_code = "internal_failure"
            self.diagnostic_stage = stage
            self.stop("unavailable")
        finally:
            if self.stopped_at is None:
                self.stopped_at = time.monotonic()
            if self.diagnostic_code:
                # Security, budget, transport and cancellation failures keep
                # their immediate shutdown behavior, even in a stop race.
                self.receipt_drain_requested = False
            self.stopped.set()
            self.ready.set()
            # Revocation and route shutdown may both cancel this task. Neither
            # may interrupt closing the provider or the durable terminal write.
            cleanup = asyncio.create_task(self._close_and_persist(tasks))
            while True:
                try:
                    result = await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    # Revocation/task cancellation can interrupt a graceful
                    # receipt drain, but never the durable terminal write.
                    self.receipt_drain_requested = False
                    for task in tasks:
                        task.cancel()
                    if cleanup.done():
                        raise
        return result

    async def _drain_provider_receipt(self, provider_task: asyncio.Task[Any]) -> None:
        """Receive an already-started turn's terminal meter for at most 3s.

        This sends no input, end/start activity, setup or resumption message.
        An absent, interrupted or ambiguous terminal meter remains uncertain.
        """
        deadline = time.monotonic() + min(3.0, self.limits.meter_timeout_seconds)
        while self.receipt_drain_requested and time.monotonic() < deadline:
            if provider_task.done() and (
                provider_task.cancelled() or provider_task.exception() is not None
            ):
                if not self.diagnostic_code:
                    self.diagnostic_code = "receipt_drain_failed"
                    self.diagnostic_stage = "provider_receive"
                return
            current = self.budget.current
            if current is None:
                return
            terminal_seen = (
                self.phase == "settling" and current.meter_seen and self.terminal_meter_seen
            )
            if terminal_seen and time.monotonic() - self.completed_at >= 0.2:
                async with self.policy_lock:
                    self.budget.close_turn(certain=True)
                    self.phase = "idle"
                return
            if provider_task.done() and not terminal_seen:
                return
            await asyncio.sleep(max(0.0, min(0.025, deadline - time.monotonic())))

    async def _close_and_persist(self, tasks: list[asyncio.Task[Any]]) -> RelayResult:
        provider_task = next(
            (task for task in tasks if task.get_name() == "provider_receive"), None
        )
        can_drain = self.receipt_drain_requested and provider_task is not None
        # Stop ingress, audio forwarding and admission before considering any
        # final receipt. The existing upstream receiver is the sole survivor.
        admission_tasks = [task for task in tasks if not can_drain or task is not provider_task]
        for task in admission_tasks:
            task.cancel()
        await asyncio.gather(*admission_tasks, return_exceptions=True)
        if can_drain and provider_task is not None:
            await self._drain_provider_receipt(provider_task)
        self.receipt_drain_requested = False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.provider is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.provider.close(), timeout=3)
        self.budget.conservative_finish()
        checkpoint = self.checkpoint()
        stopped_at = self.stopped_at if self.stopped_at is not None else time.monotonic()
        result = RelayResult(
            **checkpoint.__dict__,
            reason=self.reason,
            # Receipt waiting is not additional participant conversation time.
            duration_ms=max(0, int((stopped_at - self.clock_start) * 1000)),
            turns=tuple(self.transcript),
            meter_complete=self.budget.meter_complete,
            diagnostic_code=self.diagnostic_code or self.reason,
            diagnostic_stage=self.diagnostic_stage,
        )
        try:
            # Persist terminal usage and trusted transcript before telling
            # the browser that the conversation has ended successfully.
            await self.on_closed(result)
            if self.reason != "stopped":
                with contextlib.suppress(Exception):
                    await self.send_client({"relayEnd": {"reason": self.reason}})
        finally:
            with contextlib.suppress(Exception):
                await self.client.close(code=1000)
            self.finished.set()
        return result


async def run_gemini_relay(
    client: WebSocket,
    config: LiveRelayConfig,
    *,
    on_checkpoint: CheckpointCallback,
    on_closed: ClosedCallback,
    connect_provider: ProviderConnector | None = None,
    limits: RelayLimits | None = None,
) -> RelayResult:
    """Run one already-authenticated, durably claimed browser relay.

    The API must atomically claim the session before calling this function.
    The process registry additionally prevents duplicate local owners and
    lets authenticated HTTP finalization stop the upstream first.
    """
    if config.session_id in _ACTIVE_RELAYS:
        raise RelayPolicyError("connection")
    relay = _Relay(
        client,
        config,
        on_checkpoint,
        on_closed,
        connect_provider or _connect_provider,
        limits or RelayLimits(),
    )
    _ACTIVE_RELAYS[config.session_id] = relay
    try:
        return await relay.run()
    finally:
        _ACTIVE_RELAYS.pop(config.session_id, None)
