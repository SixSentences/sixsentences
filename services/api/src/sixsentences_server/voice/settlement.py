"""Customer usage after a server-owned relay has durably closed.

Advance generation allowances protect admission, but are not observed usage.
The operator retains uncertain exposure in the private relay receipt and bears
the difference after a confirmed close. Legacy or unclosed sessions must not
use these helpers to infer that an unused credential was free.
"""

import math
from decimal import ROUND_CEILING, Decimal
from typing import Any


def observed_relay_cost(meter: dict[str, Any]) -> float:
    """Return authenticated, catalog-priced usage, never unspent headroom.

    This is a token-receipt estimate, not a final provider invoice. Invalid or
    absent server receipts must be investigated, not silently treated as zero.
    """
    value = meter.get("observed_cost_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Missing relay usage receipt")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid relay usage receipt")
    return float(value)


def customer_relay_cost(meter: dict[str, Any]) -> float:
    """Use the authenticated modality price, or the legacy observed upper price."""
    observed = observed_relay_cost(meter)
    value = meter.get("customer_observed_cost_usd")
    if value is None:
        return observed
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or value > observed
    ):
        raise ValueError("Invalid relay customer usage receipt")
    return float(value)


def proportional_voice_credits(
    cost_usd: float,
    *,
    reserved_credits: int,
    cost_limit_usd: float,
) -> int:
    """Price observed work at its frozen authorization ratio, once per action."""
    if (
        type(reserved_credits) is not int
        or reserved_credits <= 0
        or isinstance(cost_limit_usd, bool)
        or not isinstance(cost_limit_usd, (int, float))
        or not math.isfinite(cost_limit_usd)
        or cost_limit_usd <= 0
        or isinstance(cost_usd, bool)
        or not isinstance(cost_usd, (int, float))
        or not math.isfinite(cost_usd)
        or cost_usd < 0
    ):
        raise ValueError("Invalid voice capacity pricing snapshot")
    # Decimal strings avoid charging an extra unit at exact catalog boundaries.
    return int(
        (
            Decimal(str(cost_usd)) * reserved_credits / Decimal(str(cost_limit_usd))
        ).to_integral_value(rounding=ROUND_CEILING)
    )


def relay_has_admitted_work(meter: dict[str, Any]) -> bool:
    """A setup-only failed connection does not consume a conversation minute."""
    observed = observed_relay_cost(meter)
    counts = []
    for key in ("turns_started", "input_audio_ms"):
        value = meter.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("Invalid relay activity receipt")
        counts.append(value)
    return observed > 0 or any(counts)


def relay_customer_receipt(meter: dict[str, Any]) -> dict[str, Any]:
    """Annotate customer settlement without overwriting operator exposure."""
    observed = customer_relay_cost(meter)
    accounted = meter.get("accounted_cost_usd")
    if (
        isinstance(accounted, bool)
        or not isinstance(accounted, (int, float))
        or not math.isfinite(accounted)
        or accounted < 0
    ):
        raise ValueError("Invalid relay exposure receipt")
    return {
        **meter,
        "customer_meter_policy": "catalog_proportional_v2",
        "customer_cost_usd": observed,
        "operator_uncertain_cost_usd": max(0.0, float(accounted) - observed),
    }
