"""Server-clock ingress jitter tolerance retains finite audio admission bounds."""

import base64

import pytest

from sixsentences_server.voice.relay_policy import (
    PcmRateGate,
    RelayLimits,
    RelayPolicyError,
    decode_client_audio,
)


def test_two_browser_frames_slightly_over_100ms_may_arrive_together() -> None:
    rate = PcmRateGate(10.0, RelayLimits())
    # Worklet output rounds to complete render quanta, not exactly 1600 samples.
    rate.admit(3242, 10.0)
    rate.admit(3242, 10.0)
    assert rate.total_bytes == 6484
    assert rate.tokens == 32000 - 6484


def test_one_second_burst_is_finite_and_does_not_grow_while_idle() -> None:
    rate = PcmRateGate(10.0, RelayLimits())
    for _ in range(5):
        rate.admit(6400, 100.0)
    assert rate.total_bytes == 32000
    assert rate.tokens == 0
    with pytest.raises(RelayPolicyError, match="audio_rate_exceeded"):
        rate.admit(2, 100.0)
    assert rate.total_bytes == 32000


def test_jitter_allowance_does_not_raise_the_individual_frame_limit() -> None:
    limits = RelayLimits()
    assert limits.max_pcm_frame_bytes == 6400
    message = {
        "realtimeInput": {
            "audio": {
                "data": base64.b64encode(bytes(6402)).decode("ascii"),
                "mimeType": "audio/pcm;rate=16000",
            }
        }
    }
    with pytest.raises(RelayPolicyError, match="invalid_audio"):
        decode_client_audio(message, limits)


def test_tokens_replenish_only_at_the_pcm_real_time_rate() -> None:
    rate = PcmRateGate(10.0, RelayLimits())
    for _ in range(5):
        rate.admit(6400, 10.0)
    with pytest.raises(RelayPolicyError, match="audio_rate_exceeded"):
        rate.admit(3200, 10.05)
    rate.admit(3200, 10.1)
    assert rate.total_bytes == 35200
    assert rate.tokens == pytest.approx(0, abs=1e-6)


def test_sustained_double_speed_exhausts_the_allowance() -> None:
    rate = PcmRateGate(10.0, RelayLimits())
    accepted = 0
    with pytest.raises(RelayPolicyError, match="audio_rate_exceeded"):
        for index in range(100):
            # Each frame contains 100ms of audio, delivered every 50ms.
            rate.admit(3200, 10.0 + index * 0.05)
            accepted += 1
    assert accepted == 19
    assert rate.total_bytes == accepted * 3200
