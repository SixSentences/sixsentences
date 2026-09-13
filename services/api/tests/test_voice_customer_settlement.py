"""Customer settlement keeps observed usage distinct from operator exposure."""

from copy import deepcopy
from typing import Any

import pytest

from sixsentences_server.voice.settlement import (
    customer_relay_cost,
    observed_relay_cost,
    proportional_voice_credits,
    relay_customer_receipt,
    relay_has_admitted_work,
)


def _meter(**updates: Any) -> dict[str, Any]:
    """Return a synthetic, content-free terminal server receipt."""
    return {
        "observed_cost_usd": 0.003,
        "accounted_cost_usd": 1.0,
        "reserved_cost_usd": 1.0,
        "input_audio_ms": 500,
        "turns_started": 1,
        "provider_connections": 1,
        "meter_complete": False,
        "diagnostic_code": "audio_rate_exceeded",
        **updates,
    }


@pytest.mark.parametrize("observed", [0, 0.003, 1.2])
def test_observed_usage_is_not_replaced_or_capped_by_advance_headroom(observed: float) -> None:
    meter = _meter(observed_cost_usd=observed)
    assert observed_relay_cost(meter) == observed
    receipt = relay_customer_receipt(meter)
    assert receipt["customer_cost_usd"] == observed
    assert receipt["operator_uncertain_cost_usd"] == pytest.approx(max(0.0, 1.0 - observed))


@pytest.mark.parametrize(
    "value", [None, True, False, "0.003", -0.01, float("nan"), float("inf"), float("-inf")]
)
def test_invalid_observed_receipts_are_not_silently_refunded(value: Any) -> None:
    meter = _meter(observed_cost_usd=value)
    for helper in (observed_relay_cost, relay_has_admitted_work, relay_customer_receipt):
        with pytest.raises(ValueError):
            helper(meter)


def test_missing_observed_receipt_does_not_mean_zero_usage() -> None:
    meter = _meter()
    del meter["observed_cost_usd"]
    with pytest.raises(ValueError):
        observed_relay_cost(meter)


@pytest.mark.parametrize(
    "observed,audio_ms,turns,expected",
    [
        (0.0, 0, 0, False),
        (0.0, 1, 0, True),
        (0.0, 0, 1, True),
        (0.003, 0, 0, True),
    ],
)
def test_setup_only_is_free_but_either_generation_audio_or_metered_usage_is_work(
    observed: float,
    audio_ms: int,
    turns: int,
    expected: bool,
) -> None:
    assert (
        relay_has_admitted_work(
            _meter(
                observed_cost_usd=observed,
                input_audio_ms=audio_ms,
                turns_started=turns,
            )
        )
        is expected
    )


@pytest.mark.parametrize("field", ["turns_started", "input_audio_ms"])
@pytest.mark.parametrize("value", [None, True, -1, 0.0, "0"])
def test_invalid_activity_receipts_cannot_be_classified_as_failed_setup(
    field: str,
    value: Any,
) -> None:
    with pytest.raises(ValueError):
        relay_has_admitted_work(_meter(observed_cost_usd=0, **{field: value}))


@pytest.mark.parametrize("value", [None, True, "1.0", -1, float("nan"), float("inf")])
def test_invalid_operator_exposure_cannot_be_hidden_by_customer_annotation(value: Any) -> None:
    with pytest.raises(ValueError):
        relay_customer_receipt(_meter(accounted_cost_usd=value))


def test_customer_annotation_preserves_raw_receipts_and_is_idempotent() -> None:
    meter = _meter()
    original = deepcopy(meter)
    receipt = relay_customer_receipt(meter)
    assert meter == original and receipt is not meter
    assert all(receipt[key] == value for key, value in original.items())
    assert receipt["customer_meter_policy"] == "catalog_proportional_v2"
    assert receipt["customer_cost_usd"] == 0.003
    assert receipt["operator_uncertain_cost_usd"] == pytest.approx(0.997)
    assert relay_customer_receipt(receipt) == receipt


@pytest.mark.parametrize("customer", [None, 0.0, 0.001, 0.003])
def test_customer_modality_cost_is_separate_from_the_legacy_upper_receipt(customer: float) -> None:
    meter = _meter(customer_observed_cost_usd=customer)
    expected = 0.003 if customer is None else customer
    assert customer_relay_cost(meter) == expected
    receipt = relay_customer_receipt(meter)
    assert receipt["observed_cost_usd"] == 0.003
    assert receipt["accounted_cost_usd"] == 1.0
    assert receipt["customer_cost_usd"] == expected


@pytest.mark.parametrize(
    "customer", [True, False, "0.001", -0.01, 0.004, float("nan"), float("inf")]
)
def test_invalid_modality_cost_cannot_be_silently_refunded(customer: Any) -> None:
    with pytest.raises(ValueError):
        customer_relay_cost(_meter(customer_observed_cost_usd=customer))


@pytest.mark.parametrize(
    "observed,credits,budget,expected",
    [
        (0.0, 4500, 6.75, 0),
        (0.026046, 4500, 6.75, 18),
        (0.0002389828, 4500, 6.75, 1),
        (0.003, 7500, 4.5, 5),
        (0.015, 7500, 4.5, 25),
        (0.15, 10000, 15.0, 100),
        (6.751, 4500, 6.75, 4501),
    ],
)
def test_capacity_is_cumulative_catalog_cost_not_started_minutes(
    observed: float,
    credits: int,
    budget: float,
    expected: int,
) -> None:
    assert (
        proportional_voice_credits(
            observed,
            reserved_credits=credits,
            cost_limit_usd=budget,
        )
        == expected
    )


@pytest.mark.parametrize(
    "observed,credits,budget",
    [
        (-1, 4500, 6.75),
        (True, 4500, 6.75),
        (float("nan"), 4500, 6.75),
        (0, 0, 6.75),
        (0, True, 6.75),
        (0, 4500.0, 6.75),
        (0, 4500, 0),
        (0, 4500, float("inf")),
        (0, 4500, "6.75"),
    ],
)
def test_invalid_capacity_snapshot_is_not_zero_pricing(
    observed: Any,
    credits: Any,
    budget: Any,
) -> None:
    with pytest.raises(ValueError):
        proportional_voice_credits(observed, reserved_credits=credits, cost_limit_usd=budget)
