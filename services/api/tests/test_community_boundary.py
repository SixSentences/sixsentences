"""Executable boundary contract for the public community service."""

from fastapi.routing import APIRoute

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import Base
from sixsentences_server.core.plans import ALL_CAPABILITIES, COMMUNITY_PLAN, PlanTier, all_plans


def test_community_defaults_are_closed_and_neutral() -> None:
    settings = Settings(_env_file=None)

    assert settings.self_signup is False
    assert settings.gemini_enabled is False
    assert settings.websearch_enabled is False
    assert settings.app_url == "http://localhost:3000"
    assert not any(name.startswith("stripe_") for name in type(settings).model_fields)


def test_community_profile_has_every_product_capability_and_no_prices() -> None:
    assert PlanTier.__members__ == {"COMMUNITY": PlanTier.COMMUNITY}
    assert all_plans() == [COMMUNITY_PLAN]
    assert set(COMMUNITY_PLAN.capabilities) == set(ALL_CAPABILITIES)
    assert not any("price" in name for name in type(COMMUNITY_PLAN).model_fields)


def test_commercial_and_platform_routes_are_absent() -> None:
    paths = {route.path for route in create_app().routes if isinstance(route, APIRoute)}
    forbidden_prefixes = (
        "/admin",
        "/billing",
        "/features",
        "/legal/contract-actions",
        "/waitlist",
    )
    assert not [path for path in paths if path.startswith(forbidden_prefixes)]
    for prefix in (
        "/auth",
        "/runs",
        "/library",
        "/writer",
        "/surveys",
        "/interviews",
        "/voice/studies",
        "/figures",
        "/datasets",
        "/repository-analyses",
    ):
        assert any(path.startswith(prefix) for path in paths), prefix


def test_community_schema_contains_no_commercial_or_platform_tables() -> None:
    tables = set(Base.metadata.tables)
    forbidden_fragments = (
        "billing",
        "consumer_contract",
        "credit_topup",
        "dsa_",
        "feature_",
        "platform_",
        "stripe",
        "switching",
        "waitlist",
    )
    assert not [name for name in tables if any(term in name for term in forbidden_fragments)]
