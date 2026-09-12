"""Machine-checked Core boundary and open-web contract parity."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

from starlette.routing import WebSocketRoute

from sixsentences_server.api.app import create_app

SERVICE_ROOT = Path(__file__).resolve().parents[1]
PARITY_PATH = SERVICE_ROOT / "contracts/community-parity.json"
WEB_CHECK_PATH = SERVICE_ROOT / "scripts/check_web_contracts.py"


def _digest(operations: set[str]) -> str:
    serialized = "".join(f"{operation}\n" for operation in sorted(operations))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _application_operations() -> tuple[set[str], int]:
    app = create_app()
    operations = {
        f"{method} {route.path}"
        for route in app.routes
        for method in (getattr(route, "methods", ()) or ())
    }
    websockets = sum(isinstance(route, WebSocketRoute) for route in app.routes)
    return operations, websockets


def _web_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("community_web_contracts", WEB_CHECK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _method_counts(operations: set[str]) -> dict[str, int]:
    counts = Counter(operation.split(" ", 1)[0] for operation in operations)
    return dict(sorted(counts.items()))


def test_core_to_community_boundary_is_exact() -> None:
    parity = json.loads(PARITY_PATH.read_text(encoding="utf-8"))
    community, websockets = _application_operations()
    excluded = set(parity["excluded_operations"])

    assert len(community) == parity["community"]["http_operations"] == 400
    assert websockets == parity["community"]["websockets"] == 1
    assert _method_counts(community) == parity["community"]["methods"]
    assert _digest(community) == parity["community"]["contract_sha256"]
    assert not community & excluded
    assert parity["community"]["additions"] == []
    assert parity["community"]["noncommercial_gaps"] == []

    reconstructed_core = community | excluded
    assert len(excluded) == 63
    assert len(reconstructed_core) == parity["core"]["http_operations"] == 463
    assert _method_counts(reconstructed_core) == parity["core"]["methods"]
    assert _digest(reconstructed_core) == parity["core"]["contract_sha256"]


def test_web_contract_parser_classifies_reconnects_and_direct_transports() -> None:
    checker = _web_checker()
    synthetic = """
      request(projectId === null ? "/runs" : `/projects/${projectId}/runs`, {
        method: "POST", body,
      });
      streamChatRequest(
        `/runs/${id}/chat/stream`,
        `/runs/${id}/chat/turns/${turnId}/events/stream`,
        turnId, body, onEvent,
      );
      fetch(`${API_URL}/documents/citations/export`, { method: "POST" });
      return `${API_URL}/runs/${runId}/events/stream?ticket=${ticket}`;
    """
    contracts = {
        f"{item.method} {item.path}" for item in checker.extract_client_contracts(synthetic)
    }

    assert contracts == {
        "GET /runs/{}/chat/turns/{}/events/stream",
        "GET /runs/{}/events/stream",
        "POST /documents/citations/export",
        "POST /projects/{}/runs",
        "POST /runs",
        "POST /runs/{}/chat/stream",
    }
    assert "POST /runs/{}/chat/turns/{}/events/stream" not in contracts


def test_checked_open_web_has_zero_community_gaps_when_present() -> None:
    """Exercise the real monorepo client after this service is integrated."""

    client_path = Path(__file__).resolve().parents[3] / "apps/web/src/lib/api.ts"
    if not client_path.is_file():
        return
    parity: dict[str, Any] = json.loads(PARITY_PATH.read_text(encoding="utf-8"))
    expected = parity["open_web"]
    source = client_path.read_text(encoding="utf-8")
    checker = _web_checker()
    required, _implemented, missing = checker.compare(source)
    central = checker.extract_central_client_contracts(source)

    assert hashlib.sha256(source.encode()).hexdigest() == expected["source_sha256"]
    assert len(central) == expected["central_http_operations"] == 334
    assert (
        _method_counts({f"{item.method} {item.path}" for item in central})
        == expected["central_methods"]
    )
    assert checker.contract_sha256(central) == expected["central_contract_sha256"]
    assert len(required) == expected["all_api_transport_operations"] == 349
    assert (
        _method_counts({f"{item.method} {item.path}" for item in required})
        == expected["all_api_transport_methods"]
    )
    assert checker.contract_sha256(required) == expected["all_api_transport_contract_sha256"]
    assert missing == set()
