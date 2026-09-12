"""Public-boundary and web-contract audit tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"community_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_service_tree_contains_no_credentials_or_hosted_commercial_runtime() -> None:
    audit = _load("audit_boundary")
    service_root = Path(__file__).parents[1]
    assert audit.audit(service_root) == []

    runtime = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((service_root / "src").rglob("*.py"))
    ).casefold()
    prohibited = (
        "stripe",
        "billing_portal",
        "checkout_session",
        "subscription_plan",
        "price_catalog",
        "topup",
        "waitlist",
        "hosted_operator",
    )
    assert [token for token in prohibited if token in runtime] == []


def test_contract_parser_preserves_methods_and_parameterizes_paths() -> None:
    contracts = _load("check_web_contracts")
    source = """
      request<Item>(`/items/${encodeURIComponent(id)}?view=full`);
      request<{ ok: true }>("/items", { method: "POST", body: {} });
      streamSpecialistRequest<Reply>(`/items/${id}/chat/stream`, body);
    """
    assert contracts.extract_client_contracts(source) == {
        contracts.Contract("GET", "/items/{}"),
        contracts.Contract("POST", "/items"),
        contracts.Contract("POST", "/items/{}/chat/stream"),
    }


def test_current_web_contract_gap_is_measured_and_cannot_be_called_complete() -> None:
    contracts = _load("check_web_contracts")
    repository = Path(__file__).parents[3]
    source = (repository / "apps/web/src/lib/api.ts").read_text(encoding="utf-8")
    required, implemented, missing = contracts.compare(source)
    assert len(required) >= 300
    assert len(required & implemented) >= 50
    assert contracts.Contract("POST", "/writer/{}/chat") in missing
    assert contracts.Contract("GET", "/runs/{}/control-room") in missing
    assert contracts.Contract("GET", "/health") not in missing
