"""Synthetic catalog-meter regressions; no provider requests or audio required."""

from typing import Any

import pytest

from sixsentences_server.voice.relay_policy import (
    LiveRelayBudget,
    RelayPolicyError,
    usage_customer_cost_microusd,
    usage_upper_cost_microusd,
)


def _receipt(factor: int = 1) -> dict[str, Any]:
    return {
        "promptTokenCount": 3000 * factor,
        "responseTokenCount": 300 * factor,
        "totalTokenCount": 3300 * factor,
        "promptTokensDetails": [
            {"modality": "TEXT", "tokenCount": 1000 * factor},
            {"modality": "AUDIO", "tokenCount": 2000 * factor},
        ],
        "responseTokensDetails": [
            {"modality": "TEXT", "tokenCount": 100 * factor},
            {"modality": "AUDIO", "tokenCount": 200 * factor},
        ],
    }


def test_catalog_prices_all_four_modalities_without_changing_upper_guard() -> None:
    # $0.00075 + $0.006 + $0.00045 + $0.0024. Transcript tokens are
    # already in TEXT output; never infer and add another transcription cost.
    assert usage_customer_cost_microusd(_receipt()) == (9600, False)
    assert usage_upper_cost_microusd(_receipt()) == 12_600


def test_fractional_microdollars_round_up_once_per_receipt() -> None:
    payload = {
        "promptTokenCount": 1,
        "responseTokenCount": 1,
        "totalTokenCount": 2,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 1}],
        "responseTokensDetails": [{"modality": "TEXT", "tokenCount": 1}],
    }
    assert usage_customer_cost_microusd(payload) == (6, False)


@pytest.mark.parametrize(
    "details",
    [
        None,
        [],
        {},
        "TEXT",
        [None],
        [{"modality": "IMAGE", "tokenCount": 3000}],
        [{"modality": "text", "tokenCount": 3000}],
        [{"modality": "TEXT", "tokenCount": 2999}],
        [{"modality": "TEXT", "tokenCount": 3001}],
        [{"modality": "TEXT", "tokenCount": True}],
        [{"modality": "TEXT", "tokenCount": 3000.0}],
        [{"modality": "TEXT", "tokenCount": -1}],
        [{"modality": "TEXT", "tokenCount": 50_000_001}],
        [{"modality": "TEXT", "tokenCount": 1000}, {"modality": "TEXT", "tokenCount": 2000}],
        [
            {"modality": "TEXT", "tokenCount": 1000},
            {"modality": "AUDIO", "tokenCount": 2000},
            {"modality": "VIDEO", "tokenCount": 0},
        ],
    ],
)
def test_incomplete_invalid_or_unsupported_modality_details_use_upper(details: Any) -> None:
    payload = _receipt() | {"promptTokensDetails": details}
    assert usage_customer_cost_microusd(payload) == (12_600, True)


@pytest.mark.parametrize(
    "extra",
    [
        {"responseTokensDetails": None},
        {"totalTokenCount": 3400},
        {"thoughtsTokenCount": 50},
        {"toolUsePromptTokenCount": 50},
        {"cachedContentTokenCount": 100},
        {"cachedContentTokenCount": False},
        {"cacheTokensDetails": [{"modality": "TEXT", "tokenCount": 100}]},
        {"toolUsePromptTokensDetails": [{"modality": "TEXT", "tokenCount": 100}]},
    ],
)
def test_ambiguous_separate_counters_never_receive_an_invented_discount(extra: dict) -> None:
    payload = _receipt() | extra
    assert usage_customer_cost_microusd(payload) == (usage_upper_cost_microusd(payload), True)


def test_invalid_aggregate_still_fails_closed_instead_of_using_a_discount() -> None:
    with pytest.raises(RelayPolicyError, match="meter_unavailable"):
        usage_customer_cost_microusd(_receipt() | {"totalTokenCount": 1})


def test_cumulative_and_late_receipts_are_idempotent_but_context_rebills_per_turn() -> None:
    budget = LiveRelayBudget(4.5)
    budget.begin_turn(0)
    budget.observe(_receipt())
    budget.observe(_receipt())
    budget.close_turn(certain=True)
    assert budget.customer_observed_microusd == 9600
    budget.begin_turn(1)
    budget.observe(_receipt(2))
    budget.observe(_receipt())
    budget.close_turn(certain=True)
    # New generation includes retained context. It is not a session-total delta.
    assert budget.customer_observed_microusd == 28_800
    assert budget.observed_microusd == budget.accounted_microusd == 37_800
    budget.observe(_receipt(3))
    budget.observe(_receipt(3))
    assert budget.customer_observed_microusd == 38_400
    assert budget.observed_microusd == 50_400
    assert not budget.customer_cost_fallback_used


def test_late_details_cannot_reduce_an_earlier_conservative_customer_receipt() -> None:
    budget = LiveRelayBudget(4.5)
    budget.begin_turn(0)
    payload = _receipt()
    del payload["promptTokensDetails"]
    budget.observe(payload)
    budget.observe(_receipt())
    budget.close_turn(certain=True)
    budget.observe(_receipt())
    assert budget.customer_observed_microusd == budget.observed_microusd == 12_600
    assert budget.customer_cost_fallback_used


def test_incomplete_turn_retains_guard_headroom_not_unobserved_customer_cost() -> None:
    budget = LiveRelayBudget(4.5)
    budget.begin_turn(0)
    budget.observe(_receipt())
    budget.conservative_finish()
    assert budget.customer_observed_microusd == 9600
    assert budget.observed_microusd == 12_600
    assert budget.accounted_microusd == budget.committed_microusd == 1_000_000
    assert not budget.meter_complete


def test_lower_customer_cost_cannot_bypass_upper_admission_cutoff() -> None:
    budget = LiveRelayBudget(4.5)
    budget.begin_turn(0)
    payload = {
        "promptTokenCount": 400_000,
        "responseTokenCount": 0,
        "totalTokenCount": 400_000,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 400_000}],
        "responseTokensDetails": [],
    }
    with pytest.raises(RelayPolicyError, match="capacity_reached"):
        budget.observe(payload)
    assert budget.customer_observed_microusd == 300_000
    assert budget.observed_microusd == 1_200_000


def test_unattributed_usage_is_preserved_in_both_meters_before_shutdown() -> None:
    budget = LiveRelayBudget(4.5)
    with pytest.raises(RelayPolicyError, match="meter_unavailable"):
        budget.observe(_receipt())
    assert budget.customer_observed_microusd == 9600
    assert budget.observed_microusd == 12_600


def test_zero_setup_receipt_does_not_mark_a_nonexistent_paid_fallback() -> None:
    budget = LiveRelayBudget(4.5)
    budget.observe({"promptTokenCount": 0, "responseTokenCount": 0, "totalTokenCount": 0})
    assert budget.customer_observed_microusd == budget.observed_microusd == 0
    assert not budget.customer_cost_fallback_used
