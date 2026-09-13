"""Community entitlements expose features without commercial tiers."""

from sixsentences_server.core.db import Org, db_session, init_db
from sixsentences_server.core.entitlements import capacity_summary, check_capability
from sixsentences_server.core.plans import ALL_CAPABILITIES, COMMUNITY_PLAN


def test_every_capability_is_available(settings) -> None:  # type: ignore[no-untyped-def]
    init_db()
    with db_session() as session:
        org = Org(name="Community workspace")
        session.add(org)
        session.flush()
        for capability in ALL_CAPABILITIES:
            check_capability(org, capability)
        summary = capacity_summary(session, org)

    assert summary["profile"] == "community"
    assert summary["workflow_units_limit"] is None
    assert summary["provider_budget_usd"] == COMMUNITY_PLAN.monthly_ai_cost_usd
