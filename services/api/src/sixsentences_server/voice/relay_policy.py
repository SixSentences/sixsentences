"""Content-free admission and metering for the server-owned Live connection.

This is an enforceable application cutoff, not a promise about Google's final
invoice. A generation has advance headroom before any of its input is sent.
Unknown/unfinished generations retain that headroom instead of being refunded.
"""

from __future__ import annotations

import base64
import binascii
import math
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any


class RelayPolicyError(Exception):
    """A fixed, non-provider reason for ending a relay connection."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RelayLimits:
    """Defensive limits independent of caller-controlled WebSocket messages."""

    pcm_bytes_per_second: int = 32_000
    max_pcm_frame_bytes: int = 6_400
    max_client_message_bytes: int = 16_384
    max_provider_message_bytes: int = 1_048_576
    max_transcript_bytes_per_turn: int = 65_536
    max_turn_audio_seconds: float = 180.0
    max_turns_per_minute: int = 12
    max_session_turns: int = 720
    generation_headroom_microusd: int = 1_000_000
    setup_timeout_seconds: float = 20.0
    generation_timeout_seconds: float = 60.0
    meter_timeout_seconds: float = 3.0
    silence_timeout_seconds: float = 180.0
    checkpoint_interval_seconds: float = 5.0
    max_provider_connections: int = 8


@dataclass
class _TurnExposure:
    observed_microusd: int = 0
    customer_observed_microusd: int = 0
    reserved_microusd: int = 1_000_000
    meter_seen: bool = False
    closed: bool = False
    certain: bool = False


def _positive_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 50_000_000:
        raise RelayPolicyError("meter_unavailable")
    return value


def usage_upper_cost_microusd(payload: Any) -> int:
    """Price authenticated token counts at the highest allowed modality rate.

    Google 3.1 Flash Live: input audio $3/M (text $0.75/M), output audio
    $12/M (text and transcriptions $4.50/M). Not applying a modality or cache
    discount is deliberately conservative. Unexplained total tokens are
    charged at the output rate, never silently dropped.
    """
    if not isinstance(payload, dict):
        raise RelayPolicyError("meter_unavailable")
    prompt = _positive_count(payload.get("promptTokenCount"))
    response = _positive_count(payload.get("responseTokenCount"))
    total = _positive_count(payload.get("totalTokenCount"))
    thoughts = _positive_count(payload.get("thoughtsTokenCount", 0))
    tool_prompt = _positive_count(payload.get("toolUsePromptTokenCount", 0))
    if total < prompt + response:
        raise RelayPolicyError("meter_unavailable")
    # The Live reference defines total as prompt + response candidates and
    # exposes thinking/tool-use separately. Never assume those extra counters
    # were included; max avoids adding them twice when the provider total does
    # include them. Non-audio output is still priced at the higher audio rate.
    upper_total = max(total, prompt + response + thoughts + tool_prompt)
    return prompt * 3 + (upper_total - prompt) * 12


def _modality_cost_quarters(details: Any, total: int, *, output: bool) -> int:
    """Validate a complete TEXT/AUDIO breakdown and price in quarter-microdollars."""
    if not isinstance(details, list) or len(details) > 2:
        raise RelayPolicyError("meter_unavailable")
    rates = {"TEXT": 18 if output else 3, "AUDIO": 48 if output else 12}
    seen: set[str] = set()
    counted = 0
    cost = 0
    for detail in details:
        if not isinstance(detail, dict):
            raise RelayPolicyError("meter_unavailable")
        modality = detail.get("modality")
        if not isinstance(modality, str) or modality not in rates or modality in seen:
            raise RelayPolicyError("meter_unavailable")
        count = _positive_count(detail.get("tokenCount"))
        seen.add(modality)
        counted += count
        cost += count * rates[modality]
    if counted != total:
        raise RelayPolicyError("meter_unavailable")
    return cost


def usage_customer_cost_microusd(payload: Any) -> tuple[int, bool]:
    """Price proven modalities separately; return upper cost when detail is ambiguous.

    Gemini 3.1 Flash Live catalog, reviewed 2026-09-04:
    https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-live-preview
    Text/audio input: $0.75/$3 per million; text/audio output: $4.50/$12.
    Transcription is already output usage, not an additional inferred count.
    The model does not support caching and this relay enables no tools. Until
    separate thinking/tool/cache counters have an unambiguous modality contract,
    retain the existing conservative price for such receipts. Never infer a
    discount from missing details or subtract retained context between turns.
    """
    upper = usage_upper_cost_microusd(payload)
    try:
        prompt = _positive_count(payload.get("promptTokenCount"))
        response = _positive_count(payload.get("responseTokenCount"))
        if (
            payload["totalTokenCount"] != prompt + response
            or _positive_count(payload.get("thoughtsTokenCount", 0))
            or _positive_count(payload.get("toolUsePromptTokenCount", 0))
            or _positive_count(payload.get("cachedContentTokenCount", 0))
            or payload.get("cacheTokensDetails") not in (None, [])
            or payload.get("toolUsePromptTokensDetails") not in (None, [])
        ):
            return upper, True
        quarters = _modality_cost_quarters(
            payload.get("promptTokensDetails"),
            prompt,
            output=False,
        ) + _modality_cost_quarters(
            payload.get("responseTokensDetails"),
            response,
            output=True,
        )
    except RelayPolicyError:
        return upper, True
    # Round once per receipt; no binary floating-point pricing or rounding down.
    return (quarters + 3) // 4, False


def decode_client_audio(message: Any, limits: RelayLimits) -> bytes | None:
    """Allow only PCM16 frames or an explicit stop; reject arbitrary Gemini IO."""
    if not isinstance(message, dict):
        raise RelayPolicyError("invalid_audio")
    if message == {"type": "stop"}:
        return None
    if set(message) != {"realtimeInput"}:
        raise RelayPolicyError("invalid_audio")
    realtime = message.get("realtimeInput")
    if not isinstance(realtime, dict) or set(realtime) != {"audio"}:
        raise RelayPolicyError("invalid_audio")
    audio = realtime.get("audio")
    if not isinstance(audio, dict) or set(audio) != {"data", "mimeType"}:
        raise RelayPolicyError("invalid_audio")
    if audio.get("mimeType") != "audio/pcm;rate=16000" or not isinstance(audio.get("data"), str):
        raise RelayPolicyError("invalid_audio")
    try:
        decoded = base64.b64decode(audio["data"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RelayPolicyError("invalid_audio") from exc
    if not decoded or len(decoded) % 2 or len(decoded) > limits.max_pcm_frame_bytes:
        raise RelayPolicyError("invalid_audio")
    return decoded


class LiveRelayBudget:
    """Monotonic advance authorization with conservative incomplete receipts."""

    def __init__(self, budget_usd: float, *, limits: RelayLimits | None = None) -> None:
        self.limits = limits or RelayLimits()
        if not math.isfinite(budget_usd) or budget_usd <= 0:
            raise RelayPolicyError("capacity_reached")
        self.limit_microusd = int(Decimal(str(budget_usd)) * 1_000_000)
        self.turns: list[_TurnExposure] = []
        self.current: _TurnExposure | None = None
        self._turn_starts: deque[float] = deque()
        self._unattributed_microusd = 0
        self._unattributed_customer_microusd = 0
        self.customer_cost_fallback_used = False

    @property
    def committed_microusd(self) -> int:
        """Observed spend plus all unfinished/uncertain advance allowances."""
        return self._unattributed_microusd + sum(
            turn.observed_microusd
            if turn.closed and turn.certain
            else max(turn.observed_microusd, turn.reserved_microusd)
            for turn in self.turns
        )

    @property
    def observed_microusd(self) -> int:
        return self._unattributed_microusd + sum(turn.observed_microusd for turn in self.turns)

    @property
    def customer_observed_microusd(self) -> int:
        """Observed customer cost, never used to weaken the admission/exposure guard."""
        return self._unattributed_customer_microusd + sum(
            turn.customer_observed_microusd for turn in self.turns
        )

    @property
    def accounted_microusd(self) -> int:
        return self._unattributed_microusd + sum(
            max(turn.observed_microusd, turn.reserved_microusd)
            if turn.closed and not turn.certain
            else turn.observed_microusd
            for turn in self.turns
        )

    @property
    def meter_complete(self) -> bool:
        return not self._unattributed_microusd and all(
            turn.closed and turn.certain for turn in self.turns
        )

    def begin_turn(self, now: float, *, interrupt: bool = False) -> None:
        """Reserve before activityStart; an interrupted prior turn stays charged."""
        while self._turn_starts and now - self._turn_starts[0] >= 60:
            self._turn_starts.popleft()
        if (
            len(self._turn_starts) >= self.limits.max_turns_per_minute
            or len(self.turns) >= self.limits.max_session_turns
        ):
            raise RelayPolicyError("session_limit")
        if self.current is not None and not interrupt:
            raise RelayPolicyError("session_limit")
        if self.committed_microusd + self.limits.generation_headroom_microusd > self.limit_microusd:
            raise RelayPolicyError("capacity_reached")
        if self.current is not None:
            self.close_turn(certain=False)
        self.current = _TurnExposure(reserved_microusd=self.limits.generation_headroom_microusd)
        self.turns.append(self.current)
        self._turn_starts.append(now)

    def observe(self, payload: Any) -> None:
        """Keep each turn's greatest authenticated receipt; never trust browser usage."""
        cost = usage_upper_cost_microusd(payload)
        customer_cost, used_fallback = usage_customer_cost_microusd(payload)
        self.customer_cost_fallback_used |= used_fallback and bool(cost)
        if self.current is None:
            # A delayed terminal receipt may increase, never reduce, the last
            # completed turn. Zero setup receipts cannot create free turns.
            if self.turns:
                previous = self.turns[-1]
                previous.observed_microusd = max(previous.observed_microusd, cost)
                previous.customer_observed_microusd = max(
                    previous.customer_observed_microusd,
                    customer_cost,
                )
                if (
                    previous.observed_microusd > previous.reserved_microusd
                    or self.committed_microusd > self.limit_microusd
                ):
                    raise RelayPolicyError("capacity_reached")
            elif cost:
                # Unexpected setup usage is terminal, but a real provider
                # receipt must not disappear from durable accounting.
                self._unattributed_microusd = max(self._unattributed_microusd, cost)
                self._unattributed_customer_microusd = max(
                    self._unattributed_customer_microusd,
                    customer_cost,
                )
                raise RelayPolicyError("meter_unavailable")
            return
        self.current.observed_microusd = max(self.current.observed_microusd, cost)
        # A late detailed frame cannot retroactively discount an earlier upper
        # fallback. Repeated cumulative frames likewise never add the same turn.
        self.current.customer_observed_microusd = max(
            self.current.customer_observed_microusd,
            customer_cost,
        )
        self.current.meter_seen = True
        if (
            self.current.observed_microusd > self.current.reserved_microusd
            or self.committed_microusd > self.limit_microusd
        ):
            raise RelayPolicyError("capacity_reached")

    def close_turn(self, *, certain: bool) -> None:
        """Release headroom only for a terminal, authenticated provider receipt."""
        if self.current is None:
            return
        self.current.certain = certain and self.current.meter_seen
        self.current.closed = True
        self.current = None

    def conservative_finish(self) -> None:
        """Keep the in-flight allowance when stop/disconnect outruns final usage."""
        self.close_turn(certain=False)


class PcmRateGate:
    """Admit real-time audio with a finite one-second ingress jitter allowance.

    Network delivery can batch normally paced browser frames. This allowance
    does not change individual frame limits, the bounded queue or upstream
    playback pacing; sustained input still replenishes only at the PCM rate.
    """

    def __init__(self, now: float, limits: RelayLimits) -> None:
        self.limits = limits
        self.updated_at = now
        self.capacity = float(limits.pcm_bytes_per_second)
        self.tokens = self.capacity
        self.total_bytes = 0

    def admit(self, size: int, now: float) -> None:
        """Admit at most real-time PCM plus one second of delivery jitter."""
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.limits.pcm_bytes_per_second,
        )
        self.updated_at = now
        if size <= 0 or size > self.tokens + 1e-6:
            raise RelayPolicyError("audio_rate_exceeded")
        self.tokens = max(0.0, self.tokens - size)
        self.total_bytes += size


def usd_from_microusd(value: int) -> float:
    """Expose stable, upward-rounded monetary values to the internal ledger only."""
    return float((Decimal(value) / 1_000_000).quantize(Decimal("0.000001"), ROUND_CEILING))
