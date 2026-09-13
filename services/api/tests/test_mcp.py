"""MCP endpoint: protocol handshake, plan gate and literature tools."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sixsentences_server.api.app import create_app
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus


def _client(app: FastAPI, email: str, org: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Agent"},
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _rpc(client: TestClient, method: str, params: dict | None = None, rpc_id: int = 1):
    return client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
    )


def test_mcp_literature_tools(corpus: DuckDBCorpus) -> None:
    client = _client(create_app(), "mcp2@example.org", "MCP Lab 2")
    found = _rpc(
        client,
        "tools/call",
        {"name": "search_literature", "arguments": {"query": "transformer", "limit": 5}},
    )
    assert found.status_code == 200
    text = found.json()["result"]["content"][0]["text"]
    assert "W1" in text
    bad = _rpc(client, "tools/call", {"name": "search_literature", "arguments": {"query": "((("}})
    assert bad.json()["result"]["isError"] is True
    project = client.post("/projects", json={"name": "mcp"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": "W1", "verdict": "include", "reason": "seed include"}],
    )
    runs = _rpc(client, "tools/call", {"name": "list_runs", "arguments": {}})
    assert "transformers" in runs.json()["result"]["content"][0]["text"]
    record = _rpc(client, "tools/call", {"name": "get_run_record", "arguments": {"run_id": run_id}})
    record_text = record.json()["result"]["content"][0]["text"]
    assert "records_identified" in record_text
    assert "seed include" in record_text
    bib = _rpc(
        client,
        "tools/call",
        {"name": "get_bibliography", "arguments": {"run_id": run_id, "format": "bibtex"}},
    )
    assert "@" in bib.json()["result"]["content"][0]["text"]
