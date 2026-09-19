"""API surface: auth, tenancy, projects, runs with audit-log access."""

import re
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from sixsentences_server.api.app import (
    _confirmed_protocol_web_search,
    _public_review_reason,
    _public_run_config,
    _public_run_error,
    create_app,
)
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.auth import _API_KEY_ROUTE_SCOPES
from sixsentences_server.core.db import (
    DocumentRow,
    Org,
    ProtocolRow,
    Run,
    RunEvent,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
    db_session,
)
from sixsentences_server.core.models import ReviewProtocol
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus


def _register(client: TestClient, email: str, org: str) -> str:
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _authed(app: FastAPI, email: str = "owner@example.org", org: str = "Acme") -> TestClient:
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {_register(client, email, org)}"
    return client


def test_public_run_config_is_an_explicit_allowlist() -> None:
    assert _public_run_config(
        {
            "mode": "ask",
            "language": "de",
            "model": "internal-model",
            "git_revision": "internal-revision",
            "future_internal_trace": "must-not-leak",
        }
    ) == {"mode": "ask", "language": "de", "model": "internal-model"}


def test_public_run_error_never_exposes_durable_worker_diagnostics() -> None:
    leaked = "no corpus at /srv/sixsentences/corpus; run `six corpus sync` first"
    public = _public_run_error(leaked)
    assert (
        public
        == "Something went wrong while running this search. Please try again; if it keeps failing, contact support."
    )
    assert "/srv/" not in public
    assert "six corpus sync" not in public
    assert _public_run_error(None) is None


def test_api_key_allowlist_contains_only_real_non_delete_routes(corpus: DuckDBCorpus) -> None:
    app = create_app()
    registered = {
        (method, route.path) for route in app.routes for method in getattr(route, "methods", set())
    }
    assert set(_API_KEY_ROUTE_SCOPES).issubset(registered)
    assert all((method != "DELETE" for method, _path in _API_KEY_ROUTE_SCOPES))


def test_public_openapi_corrects_stream_and_binary_contracts(corpus: DuckDBCorpus) -> None:
    schema = TestClient(create_app()).get("/public-api/openapi.json").json()
    stream = schema["paths"]["/runs/{run_id}/events/stream"]["get"]
    assert all(parameter["name"] != "ticket" for parameter in stream["parameters"])
    assert set(stream["responses"]["200"]["content"]) == {"text/event-stream"}
    pdf = schema["paths"]["/documents/{document_id}/file"]["get"]
    assert pdf["responses"]["200"]["content"]["application/pdf"]["schema"] == {
        "type": "string",
        "format": "binary",
    }


def test_models_exposes_secret_free_pubmed_runtime_capability(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIX_PUBMED_ENABLED", "1")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", "operator-private@example.org")
    monkeypatch.setenv("SIX_PUBMED_API_KEY", "must-not-be-public")
    get_settings.cache_clear()
    client = _authed(create_app(), email="pubmed-models@lab.org", org="PubMed Models")

    response = client.get("/models")

    assert response.status_code == 200
    assert response.json()["runtime_capabilities"]["pubmed"] is True
    assert "operator-private@example.org" not in response.text
    assert "must-not-be-public" not in response.text
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("email", "api_key"),
    [
        ("invalid contact@example.org", ""),
        ("operator@example.org", "invalid key"),
    ],
)
def test_models_rejects_constructor_incompatible_pubmed_configuration(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
    email: str,
    api_key: str,
) -> None:
    monkeypatch.setenv("SIX_PUBMED_ENABLED", "1")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", email)
    monkeypatch.setenv("SIX_PUBMED_API_KEY", api_key)
    get_settings.cache_clear()
    client = _authed(create_app(), email="pubmed-invalid@lab.org", org="PubMed Invalid")

    response = client.get("/models")

    assert response.status_code == 200
    assert response.json()["runtime_capabilities"]["pubmed"] is False
    get_settings.cache_clear()


def test_readiness_protects_emergency_disk_reserve(
    corpus: DuckDBCorpus, settings: Settings
) -> None:
    settings.storage_reserve_bytes = 2**63
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "degraded"}
    assert client.get("/health/ready").status_code == 503


def test_unauthenticated_requests_are_rejected(corpus: DuckDBCorpus) -> None:
    client = TestClient(create_app())
    assert client.get("/projects").status_code == 401
    assert client.get("/runs/1").status_code == 401
    assert client.post("/projects", json={"name": "x"}).status_code == 401


def test_self_signup_disabled_closes_register_but_keeps_account_recovery(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closed registration does not lock existing users out of their accounts."""
    client = _authed(create_app())
    monkeypatch.setenv("SIX_SELF_SIGNUP", "0")
    monkeypatch.setattr("sixsentences_server.api.app.send_email", lambda *args, **kwargs: None)
    get_settings.cache_clear()
    fresh = TestClient(create_app())
    denied = fresh.post(
        "/auth/register",
        json={"email": "x@example.org", "password": "StrongPass123!", "org_name": "X", "name": "X"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Registration is disabled by this deployment."
    assert fresh.post("/auth/forgot", json={"email": "owner@example.org"}).status_code == 200
    login = fresh.post(
        "/auth/login", json={"email": "owner@example.org", "password": "StrongPass123!"}
    )
    assert login.status_code == 200
    assert client.get("/auth/me").json()["email"] == "owner@example.org"


def test_run_lifecycle_with_auth(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    me = client.get("/auth/me").json()
    assert me["role"] == "owner" and me["email"] == "owner@example.org"
    project = client.post("/projects", json={"name": "demo"}).json()
    response = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["prisma"]["records_identified"] == 1
    assert run["config"]["snowball_rounds"] == 1
    assert "corpus_version" not in run
    assert run["config"]["model"] == "gemini-3.5-flash"
    assert "git_revision" not in run["config"]
    events = client.get(f"/runs/{run_id}/events").json()
    assert [e["event"] for e in events][-1] == "run_completed"
    usage = client.get(f"/runs/{run_id}/usage").json()
    assert usage["run_id"] == run_id
    methods = client.get(f"/runs/{run_id}/methods").json()["methods"]
    assert "PRISMA 2020" in methods and "1 records" in methods


def test_run_404_when_authenticated(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    assert client.get("/runs/99999").status_code == 404


def test_cross_tenant_run_is_hidden(corpus: DuckDBCorpus) -> None:
    app = create_app()
    tenant_a = _authed(app, email="a@example.org", org="A")
    project = tenant_a.post("/projects", json={"name": "demo"}).json()
    run_id = tenant_a.post(
        f"/projects/{project['id']}/runs", json={"question": "t", "query": "transformer"}
    ).json()["id"]
    tenant_b = _authed(app, email="b@example.org", org="B")
    assert tenant_b.get(f"/runs/{run_id}").status_code == 404
    assert tenant_b.get(f"/runs/{run_id}/events").status_code == 404
    assert tenant_b.get(f"/runs/{run_id}/documents").status_code == 404
    assert tenant_b.get("/projects").json() == []


def test_workspace_lists_apply_bounded_limits(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    for index in range(4):
        created = client.post("/projects", json={"name": f"Project {index}"})
        assert created.status_code == 200
    newest = client.get("/projects", params={"limit": 2})
    assert newest.status_code == 200
    assert [item["name"] for item in newest.json()] == ["Project 3", "Project 2"]
    minimum = client.get("/projects", params={"limit": 0})
    assert minimum.status_code == 200
    assert len(minimum.json()) == 1


def test_events_stream_is_sse_and_tenant_scoped(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="a@acme.org", org="Acme")
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    resp = client.get(f"/runs/{run_id}/events/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: run_completed" in body
    assert 'event: done\ndata: {"status": "completed"}' in body
    other = _authed(app, email="b@other.org", org="Other")
    assert other.get(f"/runs/{run_id}/events/stream").status_code == 404


def test_cancelled_run_closes_the_sse_stream_immediately(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="cancel-stream@acme.org", org="Cancel Stream")
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.status = "cancelled"
        session.add(
            RunEvent(
                org_id=run.org_id,
                run_id=run.id,
                stage="run",
                event="run_cancelled",
                payload={"status": "cancelled"},
            )
        )
    response = client.get(f"/runs/{run_id}/events/stream")
    assert response.status_code == 200
    assert "event: run_cancelled" in response.text
    assert 'event: done\ndata: {"status": "cancelled"}' in response.text


def test_ask_run_exposes_progress_before_the_worker_claims_it(
    corpus: DuckDBCorpus, monkeypatch
) -> None:
    monkeypatch.setattr("sixsentences_server.api.app.enqueue_job", lambda *args, **kwargs: 1)
    client = _authed(create_app(), email="queued-ask@acme.org", org="Queued Ask")
    created = client.post("/runs", json={"question": "What can SixSentences do?", "mode": "ask"})
    assert created.status_code == 202
    events = client.get(f"/runs/{created.json()['id']}/events").json()
    assert events == [
        {
            "id": events[0]["id"],
            "stage": "report",
            "event": "ask_queued",
            "payload": {
                "phase": "preparing",
                "label": "Understanding the question and planning the evidence path",
            },
            "created_at": events[0]["created_at"],
        }
    ]


def test_sse_one_use_ticket_and_cors(corpus: DuckDBCorpus, monkeypatch) -> None:
    app = create_app()
    client = _authed(app, email="s@lab.org", org="S")
    project = client.post("/projects", json={"name": "s"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "t", "query": "transformer"}
    ).json()["id"]
    token = client.headers["Authorization"].removeprefix("Bearer ")
    ticket_response = client.post(f"/runs/{run_id}/events/ticket")
    assert ticket_response.status_code == 200
    assert ticket_response.headers["cache-control"] == "no-store"
    ticket = ticket_response.json()["ticket"]
    assert ticket.startswith("six_st_")
    assert ticket_response.json()["expires_in"] == 120
    bare = TestClient(app)
    streamed = bare.get(f"/runs/{run_id}/events/stream?ticket={ticket}")
    assert streamed.status_code == 200 and "event: done" in streamed.text
    assert bare.get(f"/runs/{run_id}/events/stream?ticket={ticket}").status_code == 401
    public_id = client.get(f"/runs/{run_id}").json()["public_id"]
    public_ticket = client.post(f"/runs/{public_id}/events/ticket").json()["ticket"]
    public_stream = bare.get(f"/runs/{public_id}/events/stream?ticket={public_ticket}")
    assert public_stream.status_code == 200 and "event: done" in public_stream.text
    assert bare.get(f"/runs/{run_id}/events/stream?token={token}").status_code == 401
    assert bare.get(f"/runs/{run_id}/events/stream").status_code == 401
    second_run = client.post(
        f"/projects/{project['id']}/runs", json={"question": "second", "query": "second"}
    ).json()["id"]
    scoped_ticket = client.post(f"/runs/{run_id}/events/ticket").json()["ticket"]
    assert bare.get(f"/runs/{second_run}/events/stream?ticket={scoped_ticket}").status_code == 401
    assert bare.get(f"/runs/{run_id}/events/stream?ticket={scoped_ticket}").status_code == 200
    monkeypatch.setenv("SIX_CORS_ORIGINS", "https://app.example")
    get_settings.cache_clear()
    resp = TestClient(create_app()).get("/health", headers={"Origin": "https://app.example"})
    assert resp.headers["access-control-allow-origin"] == "https://app.example"
    get_settings.cache_clear()


def test_documents_endpoint_lists_the_acquisition_ledger(corpus: DuckDBCorpus) -> None:
    app = create_app()
    owner = _authed(app, email="a@acme.org", org="Acme")
    project = owner.post("/projects", json={"name": "demo"}).json()
    run_id = owner.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        session.add(
            DocumentRow(
                org_id=run.org_id,
                run_id=run.id,
                work_id="W1",
                status="retrieved",
                source="arxiv",
                legal_basis="oa_green",
                text_status="parsed",
                checksum="abc",
                byte_size=10,
            )
        )
    docs = owner.get(f"/runs/{run_id}/documents").json()
    assert len(docs) == 1
    assert docs[0]["work_id"] == "W1"
    assert docs[0]["status"] == "retrieved" and docs[0]["legal_basis"] == "oa_green"


def test_security_headers_and_input_limits(corpus: DuckDBCorpus) -> None:
    app = create_app()
    resp = TestClient(app).get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive, nosnippet"
    authed = _authed(app)
    project = authed.post("/projects", json={"name": "x"}).json()
    huge = authed.post(f"/projects/{project['id']}/runs", json={"question": "x" * 5000})
    assert huge.status_code == 422
    assert authed.post("/projects", json={"name": "n" * 500}).status_code == 422
    bad = TestClient(app).post(
        "/auth/register", json={"email": "a@b.org", "password": "short", "org_name": "x"}
    )
    assert bad.status_code == 422
    weak = TestClient(app).post(
        "/auth/register",
        json={
            "email": "weak@example.org",
            "password": "onlylowercase",
            "org_name": "Weak",
            "name": "Researcher",
        },
    )
    assert weak.status_code == 400
    assert "choose a stronger password" in weak.json()["detail"]


def test_cors_wildcard_does_not_reflect_credentials(corpus: DuckDBCorpus, monkeypatch) -> None:
    monkeypatch.setenv("SIX_CORS_ORIGINS", "*")
    get_settings.cache_clear()
    resp = TestClient(create_app()).get("/health", headers={"Origin": "https://evil.example"})
    assert resp.headers.get("access-control-allow-origin") == "*"
    assert resp.headers.get("access-control-allow-credentials") != "true"
    get_settings.cache_clear()


def test_cors_allowlist_rejects_an_untrusted_origin(corpus: DuckDBCorpus, monkeypatch) -> None:
    monkeypatch.setenv("SIX_CORS_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    client = TestClient(create_app())
    allowed = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    rejected = client.options(
        "/auth/login",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers
    get_settings.cache_clear()


def test_chat_stream_emits_live_deltas_and_persists_answer(
    corpus: DuckDBCorpus, monkeypatch
) -> None:
    """The SSE channel is live UX; stored chat remains the reconnect fallback."""
    from sixsentences_server.llm.mock import mock_pool

    def handler(model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return '{"action":"answer"}'
        if "Sources (cite by id" in prompt:
            return "The available evidence supports a focused answer [W1]."
        if "Existing queries" in prompt:
            return '{"queries":[]}'
        return '{"inclusion_criteria":[],"exclusion_criteria":[],"query_string":"transformer"}'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="stream@acme.org", org="Stream")
    project = client.post("/projects", json={"name": "d"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    response = client.post(
        f"/runs/{run_id}/chat/stream", json={"question": "What does the evidence show?"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: turn.started" in response.text
    assert "event: answer.delta" in response.text
    assert "event: turn.completed" in response.text
    assert response.text.index("event: answer.delta") < response.text.index("event: turn.completed")
    history = client.get(f"/runs/{run_id}/chat").json()
    assert history[-1]["role"] == "assistant"
    assert "focused answer" in history[-1]["content"]
    run = client.get(f"/runs/{run_id}").json()
    assert run["config"]["chat_model"] == "gemini-3.5-flash"


def test_sync_chat_persists_the_authoritative_body_turn_id(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The non-streaming API shares the same stable message-correlation contract."""
    from sixsentences_server.core.db import ChatMessageRow
    from sixsentences_server.llm.mock import mock_pool

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        if "User request:" in prompt:
            return '{"action":"answer"}'
        if "Sources (cite by id" in prompt:
            return "A synchronous grounded answer [W1]."
        if "Existing queries" in prompt:
            return '{"queries":[]}'
        return '{"inclusion_criteria":[],"exclusion_criteria":[],"query_string":"transformer"}'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="sync-turn@acme.org", org="Sync Turn")
    project = client.post("/projects", json={"name": "d"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    with db_session() as session:
        existing_ids = set(
            session.scalars(select(ChatMessageRow.id).where(ChatMessageRow.run_id == run_id)).all()
        )
    turn_id = "sync-chat-turn-0001"
    response = client.post(
        f"/runs/{run_id}/chat",
        json={"turn_id": turn_id, "question": "What does the evidence show?"},
    )
    assert response.status_code == 200, response.text
    with db_session() as session:
        rows = list(
            session.scalars(
                select(ChatMessageRow)
                .where(ChatMessageRow.run_id == run_id, ChatMessageRow.id.not_in(existing_ids))
                .order_by(ChatMessageRow.id)
            ).all()
        )
    assert [row.role for row in rows if row.role != "tool"] == ["user", "assistant"]
    assert all((row.payload or {}).get("turn_id") == turn_id for row in rows)


def test_successful_chat_turn_persists_one_turn_id_on_every_message_payload(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User, progress, tool and final rows share the accepted stream turn."""
    from sixsentences_server.chat.service import ToolStep
    from sixsentences_server.core.db import ChatMessageRow
    from sixsentences_server.llm.mock import mock_pool

    turn_id = "persisted-chat-turn-0001"
    query_calls = 0

    def handler(model: str, prompt: str) -> str:
        nonlocal query_calls
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        if prompt.startswith("Research request:"):
            query_calls += 1
            return f"transformer evidence angle {query_calls + 1}"
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action":"answer"}'
            return '{"action":"tool","tool":"find_papers","query":"transformer evidence angle 1","reason":"check independent scholarly evidence"}'
        if "Sources (cite by id" in prompt:
            return "The evidence supports a grounded conclusion [W1]."
        if "Existing queries" in prompt:
            return '{"queries":[]}'
        return '{"inclusion_criteria":[],"exclusion_criteria":[],"query_string":"transformer"}'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.chat.service._execute_tool",
        lambda tool, query, reason: (
            ToolStep(
                tool=tool,
                query=query,
                reason=reason,
                results=[{"id": "W1", "title": f"Evidence for {query}"}],
            ),
            [],
        ),
    )
    client = _authed(create_app(), email="turn-payload@acme.org", org="Turn Payload")
    project = client.post("/projects", json={"name": "d"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    with db_session() as session:
        existing_ids = set(
            session.scalars(select(ChatMessageRow.id).where(ChatMessageRow.run_id == run_id)).all()
        )
    response = client.post(
        f"/runs/{run_id}/chat/stream",
        json={
            "turn_id": turn_id,
            "question": "Investigate transformer evidence from several angles.",
        },
    )
    assert response.status_code == 200, response.text
    assert "event: turn.completed" in response.text
    with db_session() as session:
        rows = list(
            session.scalars(
                select(ChatMessageRow)
                .where(ChatMessageRow.run_id == run_id, ChatMessageRow.id.not_in(existing_ids))
                .order_by(ChatMessageRow.id)
            ).all()
        )
    assert rows
    assert all((row.payload or {}).get("turn_id") == turn_id for row in rows)
    assert rows[0].role == "user"
    assert rows[-1].role == "assistant"
    assert any((row.payload or {}).get("kind") == "agent_work" for row in rows)
    tool_row = next(row for row in rows if (row.payload or {}).get("tool") == "find_papers")
    assert tool_row.payload["results"][0]["id"] == "W1"
    assert rows[-1].payload["tools_used"] == ["find_papers"] * 3


def test_chat_turn_stop_before_start_is_durable_idempotent_and_tenant_scoped(
    corpus: DuckDBCorpus, monkeypatch
) -> None:
    """A stop arriving before the stream creates a safe tombstone.

    Reusing the same client turn id must replay cancellation without running
    tools, charging capacity or exposing the turn to another tenant.
    """
    from sixsentences_server.core.db import ChatTurnRow, CreditEventRow
    from sixsentences_server.llm.mock import mock_pool

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: mock_pool())
    app = create_app()
    client = _authed(app, email="pre-stop@acme.org", org="Pre Stop")
    project = client.post("/projects", json={"name": "d"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    turn_id = "pre-stop-turn-0001"
    history_before = client.get(f"/runs/{run_id}/chat").json()
    with db_session() as session:
        charges_before = session.scalar(
            select(func.count(CreditEventRow.id)).where(CreditEventRow.run_id == run_id)
        )
    first = client.post(f"/runs/{run_id}/chat/turns/{turn_id}/stop")
    second = client.post(f"/runs/{run_id}/chat/turns/{turn_id}/stop")
    replay = client.post(
        f"/runs/{run_id}/chat/stream", json={"turn_id": turn_id, "question": "Do not execute this."}
    )
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.json()["status"] == "cancelled"
    assert replay.status_code == 200
    assert "event: turn.cancelled" in replay.text
    assert client.get(f"/runs/{run_id}/chat").json() == history_before
    with db_session() as session:
        charges_after = session.scalar(
            select(func.count(CreditEventRow.id)).where(CreditEventRow.run_id == run_id)
        )
        turn = session.scalar(
            select(ChatTurnRow).where(ChatTurnRow.run_id == run_id, ChatTurnRow.turn_id == turn_id)
        )
        assert turn is not None
        assert turn.status == "cancelled"
        assert turn.active_slot is None
    assert charges_after == charges_before
    outsider = _authed(app, email="outsider@acme.org", org="Other Tenant")
    assert outsider.post(f"/runs/{run_id}/chat/turns/{turn_id}/stop").status_code == 404


@pytest.mark.parametrize("stop_after_publication", [False, True])
def test_chat_turn_stop_interrupts_active_provider_without_fallback(
    corpus: DuckDBCorpus, monkeypatch, stop_after_publication: bool
) -> None:
    """Cancel private drafts, but retain validated prose already published."""
    from sixsentences_server.chat.service import answer_question
    from sixsentences_server.core.db import ChatMessageRow, ChatTurnRow
    from sixsentences_server.llm.mock import mock_pool

    provider_started = threading.Event()
    public_answer_ready = threading.Event()
    release_public_answer = threading.Event()

    def pause_after_publication(*args, **kwargs):
        original_sink = kwargs["delta_sink"]

        def publish_then_pause(text: str) -> None:
            original_sink(text)
            public_answer_ready.set()
            assert release_public_answer.wait(timeout=5)

        kwargs["delta_sink"] = publish_then_pause
        return answer_question(*args, **kwargs)

    if stop_after_publication:
        monkeypatch.setattr("sixsentences_server.api.app.answer_question", pause_after_publication)

    def handler(model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return '{"action":"answer"}'
        if "Sources (cite by id" in prompt:
            provider_started.set()
            return "A deliberately long streamed answer. " * 300
        if "Existing queries" in prompt:
            return '{"queries":[]}'
        return '{"inclusion_criteria":[],"exclusion_criteria":[],"query_string":"transformer"}'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(
            handler, stream_delay_seconds=0 if stop_after_publication else 0.02
        ),
    )
    app = create_app()
    client = _authed(app, email="live-stop@acme.org", org="Live Stop")
    worker_client = TestClient(app)
    worker_client.headers["Authorization"] = client.headers["Authorization"]
    project = client.post("/projects", json={"name": "d"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    turn_id = "live-stop-turn-0001"
    result: dict[str, object] = {}

    def run_stream() -> None:
        result["response"] = worker_client.post(
            f"/runs/{run_id}/chat/stream",
            json={"turn_id": turn_id, "question": "Explain the evidence in detail."},
        )

    thread = threading.Thread(target=run_stream)
    thread.start()
    try:
        boundary = public_answer_ready if stop_after_publication else provider_started
        assert boundary.wait(timeout=5)
        stop_started = time.monotonic()
        stopped = client.post(f"/runs/{run_id}/chat/turns/{turn_id}/stop")
    finally:
        release_public_answer.set()
        thread.join(timeout=2)
    assert stopped.status_code == 200
    assert stopped.json()["status"] in {"cancel_requested", "cancelled"}
    assert not thread.is_alive()
    assert time.monotonic() - stop_started < 2
    response = result["response"]
    assert isinstance(response, type(stopped))
    assert "event: turn.cancelled" in response.text
    assert "event: turn.completed" not in response.text
    assert "event: turn.failed" not in response.text
    with db_session() as session:
        turn = session.scalar(
            select(ChatTurnRow).where(ChatTurnRow.run_id == run_id, ChatTurnRow.turn_id == turn_id)
        )
        assert turn is not None
        assert turn.status == "cancelled"
        assert turn.active_slot is None
        partial = session.scalar(
            select(ChatMessageRow).where(
                ChatMessageRow.run_id == run_id,
                ChatMessageRow.role == "assistant",
                ChatMessageRow.payload["turn_id"].as_string() == turn_id,
            )
        )
        assert partial is not None
        if stop_after_publication:
            assert partial.content.startswith("A deliberately long streamed answer")
            assert partial.payload == {"turn_id": turn_id, "cancelled": True, "partial": True}
            assert turn.answer == {"answer": partial.content, "cancelled": True, "partial": True}
        else:
            assert "deliberately long streamed" not in partial.content
            assert not partial.payload.get("partial")
    history = client.get(f"/runs/{run_id}/chat").json()
    if stop_after_publication:
        assert history[-1]["content"].startswith("A deliberately long streamed answer")
        assert history[-1]["payload"]["partial"] is True
    else:
        assert "event: answer.delta" not in response.text
        assert all("deliberately long streamed" not in row["content"] for row in history)


def test_chat_turn_allows_only_one_active_turn_per_run(corpus: DuckDBCorpus, monkeypatch) -> None:
    """A second client cannot start work while the run slot is occupied."""
    from sixsentences_server.llm.mock import mock_pool

    provider_started = threading.Event()
    release_provider = threading.Event()

    def handler(model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return '{"action":"answer"}'
        if "Sources (cite by id" in prompt:
            provider_started.set()
            assert release_provider.wait(timeout=5)
            return "The first answer completes after the concurrency check [W1]."
        if "Existing queries" in prompt:
            return '{"queries":[]}'
        return '{"inclusion_criteria":[],"exclusion_criteria":[],"query_string":"transformer"}'

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    app = create_app()
    client = _authed(app, email="one-turn@acme.org", org="One Turn")
    worker_client = TestClient(app)
    worker_client.headers["Authorization"] = client.headers["Authorization"]
    project = client.post("/projects", json={"name": "d"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    first_result: dict[str, object] = {}

    def run_first() -> None:
        first_result["response"] = worker_client.post(
            f"/runs/{run_id}/chat/stream",
            json={"turn_id": "single-active-turn-1", "question": "First answer"},
        )

    thread = threading.Thread(target=run_first)
    thread.start()
    try:
        assert provider_started.wait(timeout=5)
        second = client.post(
            f"/runs/{run_id}/chat/stream",
            json={"turn_id": "single-active-turn-2", "question": "Second answer"},
        )
        assert second.status_code == 409
        assert second.json()["detail"]["turn_id"] == "single-active-turn-1"
    finally:
        release_provider.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    first = first_result["response"]
    assert isinstance(first, type(second))
    assert "event: turn.completed" in first.text


def test_api_key_issue_and_use(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app)
    created = client.post("/auth/api-keys", json={"name": "ci"})
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    key = created.json()["api_key"]
    assert key.startswith("six_sk_")
    assert created.json()["scopes"] == ["research:read"]
    fresh = TestClient(app)
    fresh.headers["Authorization"] = f"Bearer {key}"
    assert fresh.get("/projects").status_code == 200
    forbidden = fresh.get("/auth/me")
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "api_key_endpoint_forbidden"
    listed = client.get("/auth/api-keys").json()[0]
    assert listed["scopes"] == ["research:read"]
    assert listed["expires_at"] is not None
    key_id = listed["id"]
    client.delete(f"/auth/api-keys/{key_id}")
    assert fresh.get("/projects").status_code == 401


def test_api_keys_cannot_manage_the_account(corpus: DuckDBCorpus) -> None:
    """API keys drive research; workspace administration needs a session."""
    app = create_app()
    client = _authed(app)
    key = client.post("/auth/api-keys", json={"name": "ci", "scopes": ["research:write"]}).json()[
        "api_key"
    ]
    robot = TestClient(app)
    robot.headers["Authorization"] = f"Bearer {key}"
    assert robot.post("/projects", json={"name": "from-the-key"}).status_code == 200
    blocked = [
        robot.post(
            "/orgs/current/users",
            json={"email": "b@x.org", "password": "StrongPass123!", "role": "member"},
        ),
        robot.post("/orgs/current/users", json={"email": "x@y.z", "password": "StrongPass123!"}),
        robot.post("/auth/api-keys", json={"name": "another"}),
        robot.get("/auth/api-keys"),
        robot.delete("/auth/api-keys/1"),
        robot.get("/orgs/current/users"),
        robot.post("/webhooks", json={"url": "https://example.org/hook", "events": []}),
        robot.get("/webhooks"),
        robot.delete("/webhooks/1"),
        robot.get("/writer"),
        robot.get("/surveys"),
        robot.get("/figures"),
        robot.delete("/projects/1"),
    ]
    for response in blocked:
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "api_key_endpoint_forbidden"
    assert client.get("/auth/api-keys").status_code == 200
    assert client.post("/webhooks", json={"url": "https://example.org/h"}).status_code == 201


def test_member_management_is_owner_only(corpus: DuckDBCorpus) -> None:
    app = create_app()
    owner = _authed(app)
    created = owner.post(
        "/orgs/current/users",
        json={"email": "member@example.org", "password": "StrongPass123!", "role": "member"},
    )
    assert created.status_code == 201
    member = TestClient(app)
    login = member.post(
        "/auth/login", json={"email": "member@example.org", "password": "StrongPass123!"}
    )
    member.headers["Authorization"] = f"Bearer {login.json()['token']}"
    forbidden = member.post(
        "/orgs/current/users",
        json={"email": "x@example.org", "password": "StrongPass123!", "role": "member"},
    )
    assert forbidden.status_code == 403
    assert member.get("/orgs/current/users").status_code == 200
    owner_key = owner.post("/auth/api-keys", json={"name": "owner key"})
    assert owner_key.status_code == 201
    owner_key_id = owner.get("/auth/api-keys").json()[0]["id"]
    assert member.get("/auth/api-keys").json() == []
    assert member.delete(f"/auth/api-keys/{owner_key_id}").status_code == 404
    assert member.get("/webhooks").status_code == 403
    assert member.post("/webhooks", json={"url": "https://example.org/h"}).status_code == 403


def test_human_review_loop_over_both_stages(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="rev@lab.org", org="Lab")
    project = client.post("/projects", json={"name": "r"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "t", "query": "transformer OR learning", "screen": True},
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    queue = client.get(f"/runs/{run_id}/queue?limit=100").json()["items"]
    assert queue and all(item["stage"] == "title_abstract" for item in queue)
    work_id = queue[0]["work_id"]
    resp = client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": work_id, "verdict": "include", "reason": "clearly relevant"}],
    )
    assert resp.status_code == 201
    refreshed_queue = client.get(f"/runs/{run_id}/queue?limit=100").json()["items"]
    assert work_id not in [i["work_id"] for i in refreshed_queue]
    decisions = {d["work_id"]: d for d in client.get(f"/runs/{run_id}/decisions").json()}
    assert decisions[work_id]["by"] == "human" and decisions[work_id]["verdict"] == "include"
    others = [d for wid, d in decisions.items() if wid != work_id]
    assert others and all(d["by"] == "model" for d in others)


def test_zotero_sync_validates_and_is_tenant_scoped(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="z@lab.org", org="Z")
    project = client.post("/projects", json={"name": "z"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "t", "query": "transformer"}
    ).json()["id"]
    bad = client.post(
        f"/runs/{run_id}/zotero", json={"api_key": "k", "library_type": "nope", "library_id": "1"}
    )
    assert bad.status_code == 400
    other = _authed(app, email="o@other.org", org="O")
    resp = other.post(
        f"/runs/{run_id}/zotero", json={"api_key": "k", "library_type": "user", "library_id": "1"}
    )
    assert resp.status_code == 404


def test_screening_calibration_endpoint(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="cal@lab.org", org="Cal")
    project = client.post("/projects", json={"name": "c"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "t", "query": "transformer OR learning", "screen": True},
    ).json()["id"]
    queue = client.get(f"/runs/{run_id}/queue?limit=100").json()["items"]
    seeds = [{"work_id": item["work_id"], "included": True} for item in queue]
    report = client.post(f"/runs/{run_id}/calibration", json=seeds).json()
    assert report["recall"] == 1.0
    assert report["evaluated"] == len(seeds)
    assert report["verdict"] == "insufficient_overlap"


def test_protocol_gate_pauses_then_resumes(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "gate"}).json()
    created = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "gate_protocol": True}
    )
    assert created.status_code == 202 and created.json()["gated"] is True
    run_id = created.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "awaiting_protocol_approval"
    draft = client.get(f"/runs/{run_id}/protocol").json()
    assert draft["run_status"] == "awaiting_protocol_approval"
    assert "query_string" in draft["protocol"]
    approved = client.post(f"/runs/{run_id}/protocol/approve", json={"query_string": "transformer"})
    assert approved.status_code == 202
    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["prisma"]["records_identified"] == 1
    assert client.post(f"/runs/{run_id}/protocol/approve", json={}).status_code == 409
    events = [e["event"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert "protocol_gate_opened" in events and "protocol_approved" in events


def test_search_creation_rolls_back_if_durable_enqueue_fails(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queue failure must not expose an orphan run or capacity hold."""
    import sixsentences_server.api.app as app_module
    from sixsentences_server.core.db import CapacityReservationRow

    client = _authed(create_app(), email="queue-rollback@lab.org", org="Queue Rollback")
    project = client.post("/projects", json={"name": "atomic search"}).json()
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        runs_before = int(
            session.scalar(select(func.count()).select_from(Run).where(Run.org_id == org_id)) or 0
        )
        reservations_before = int(
            session.scalar(
                select(func.count())
                .select_from(CapacityReservationRow)
                .where(CapacityReservationRow.org_id == org_id)
            )
            or 0
        )

    def failing_enqueue(*args: object, **kwargs: object) -> int:
        assert kwargs.get("session") is not None
        raise RuntimeError("synthetic queue failure")

    monkeypatch.setattr(app_module, "enqueue_job", failing_enqueue)
    with pytest.raises(RuntimeError, match="synthetic queue failure"):
        client.post(
            f"/projects/{project['id']}/runs",
            json={"question": "atomic?", "query": "transformer"},
        )

    with db_session() as session:
        assert (
            session.scalar(select(func.count()).select_from(Run).where(Run.org_id == org_id))
            == runs_before
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CapacityReservationRow)
                .where(CapacityReservationRow.org_id == org_id)
            )
            == reservations_before
        )


def test_protocol_approval_rolls_back_if_durable_enqueue_fails(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval and its resume outbox row are one atomic transition."""
    import sixsentences_server.api.app as app_module

    client = _authed(
        create_app(),
        email="approval-rollback@lab.org",
        org="Approval Rollback",
    )
    project = client.post("/projects", json={"name": "approval"}).json()
    created = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Which transformer studies qualify?",
            "query": "transformer",
            "gate_protocol": True,
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "awaiting_protocol_approval"

    def failing_enqueue(*args: object, **kwargs: object) -> int:
        assert kwargs.get("session") is not None
        raise RuntimeError("synthetic approval queue failure")

    monkeypatch.setattr(app_module, "enqueue_job", failing_enqueue)
    with pytest.raises(RuntimeError, match="synthetic approval queue failure"):
        client.post(f"/runs/{run_id}/protocol/approve", json={})

    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == "awaiting_protocol_approval"


@pytest.mark.parametrize(
    ("edit_field", "edited_criteria"),
    [
        ("inclusion_criteria", ["Reports a public benchmark result"]),
        ("exclusion_criteria", ["Contains no public benchmark result"]),
    ],
)
def test_web_protocol_gate_requires_fresh_confirmation_for_edited_scope(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
    edit_field: str,
    edited_criteria: list[str],
) -> None:
    """Run creation consent cannot authorize later protocol edits."""
    import sixsentences_server.api.app as app_module

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-openrouter-web-key")
    get_settings.cache_clear()
    client = _authed(
        create_app(),
        email=f"protocol-web-scope-{edit_field}@lab.org",
        org=f"Protocol Web Scope {edit_field}",
    )
    project = client.post("/projects", json={"name": "web gate"}).json()
    created = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "public transformer benchmarks",
            "query": "transformer",
            "web_search": True,
            "web_search_public_data_confirmed": True,
            "gate_protocol": True,
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "awaiting_protocol_approval"
    scheduled: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def fake_enqueue(background: object, task: object, *args: object, **kwargs: object) -> int:
        scheduled.append((task, args, kwargs))
        return 1

    monkeypatch.setattr(app_module, "enqueue_job", fake_enqueue)
    rejected = client.post(f"/runs/{run_id}/protocol/approve", json={edit_field: edited_criteria})
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "web_search_public_scope_confirmation_required"
    assert scheduled == []
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == "awaiting_protocol_approval"
        assert "web_search_protocol_scope_sha256" not in run.config
    approved = client.post(
        f"/runs/{run_id}/protocol/approve",
        json={edit_field: edited_criteria, "web_search_public_data_confirmed": True},
    )
    assert approved.status_code == 202, approved.text
    assert len(scheduled) == 1
    _task, args, _kwargs = scheduled[0]
    approved_protocol = ReviewProtocol.model_validate(args[1])
    scheduled_config = dict(args[2])
    assert getattr(approved_protocol, edit_field) == edited_criteria
    assert _confirmed_protocol_web_search(
        scheduled_config, question="public transformer benchmarks", protocol=approved_protocol
    )
    changed_after_confirmation = approved_protocol.model_copy(
        update={edit_field: ["A different criterion"]}
    )
    assert not _confirmed_protocol_web_search(
        scheduled_config,
        question="public transformer benchmarks",
        protocol=changed_after_confirmation,
    )
    resume_calls: list[dict[str, object]] = []

    def fake_resume_run(*args: object, **kwargs: object) -> None:
        resume_calls.append(kwargs)

    monkeypatch.setattr(app_module, "resume_run", fake_resume_run)
    app_module._resume(run_id, changed_after_confirmation.model_dump(mode="json"), scheduled_config)
    assert resume_calls and resume_calls[0]["web_search"] is False
    with db_session() as session:
        skipped = session.scalar(
            select(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.event == "web_search_skipped"
            )
        )
        assert skipped is not None
        assert skipped.payload["resume_scope"] == "scholarly_sources_only"
    get_settings.cache_clear()


def test_protocol_metadata_hides_internal_model_route(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app(), email="private-route@lab.org", org="Private Route")
    project = client.post("/projects", json={"name": "gate"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "gate_protocol": True}
    ).json()["id"]
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None and run.protocol_id is not None
        protocol = session.get(ProtocolRow, run.protocol_id)
        assert protocol is not None
        protocol.payload = {
            **dict(protocol.payload),
            "synthesized_by": "openrouter:deepseek/deepseek-v4-flash",
        }
        created = session.scalar(
            select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.event == "protocol_created")
        )
        assert created is not None
        created.payload = {
            **dict(created.payload),
            "synthesized_by": "openrouter:deepseek/deepseek-v4-flash",
            "corpus_size": 20000,
            "corpus_version": "private-snapshot",
        }
    protocol_json = client.get(f"/runs/{run_id}/protocol").json()["protocol"]
    assert protocol_json["synthesized_by"] == "automated"
    events_json = client.get(f"/runs/{run_id}/events").json()
    serialized = str(events_json).lower()
    assert "openrouter" not in serialized
    assert "deepseek" not in serialized
    assert "private-snapshot" not in serialized
    assert "20000" not in serialized


def test_control_room_uses_live_workload_and_keeps_future_stages_waiting(
    corpus: DuckDBCorpus,
) -> None:
    client = _authed(create_app(), email="control@lab.org", org="Control")
    org_id = client.get("/auth/me").json()["org_id"]
    now = datetime.now(UTC)
    with db_session() as session:
        run = Run(
            org_id=org_id,
            status="running",
            question="q",
            config={
                "screen": True,
                "snowball": True,
                "snowball_rounds": 1,
                "acquire": True,
                "full_text": True,
                "web_search": True,
                "paper_limit": 40,
            },
            created_at=now - timedelta(minutes=10),
        )
        session.add(run)
        session.flush()
        run_id = run.id
        for index in range(40):
            work_id = f"WCONTROL{index}"
            session.add(
                WorkRow(
                    id=work_id,
                    title=f"Control work {index}",
                    payload={"id": work_id, "title": f"Control work {index}"},
                )
            )
            session.flush()
            session.add(
                SourceRecordRow(
                    org_id=org_id,
                    run_id=run_id,
                    work_id=work_id,
                    source="corpus",
                    corpus_version="test",
                )
            )
            if index < 20:
                session.add(
                    ScreeningDecisionRow(
                        org_id=org_id,
                        run_id=run_id,
                        work_id=work_id,
                        reviewer="ensemble-or",
                        verdict="include",
                        reason="relevant",
                    )
                )
        event_rows = [
            ("protocol_synthesis", "protocol_created", {}, -590),
            ("query_compilation", "queries_compiled", {}, -580),
            ("retrieval", "dedup_done", {}, -570),
            ("integrity", "peer_review_filter", {}, -560),
            ("ranking", "ranking_done", {}, -550),
            ("screening_title_abstract", "screening_started", {"total": 40, "pending": 40}, -120),
            ("screening_title_abstract", "screening_progress", {"completed": 10, "total": 40}, -60),
            ("screening_title_abstract", "screening_progress", {"completed": 20, "total": 40}, 0),
        ]
        for stage, event, payload, seconds in event_rows:
            session.add(
                RunEvent(
                    org_id=org_id,
                    run_id=run_id,
                    stage=stage,
                    event=event,
                    payload=payload,
                    created_at=now + timedelta(seconds=seconds),
                )
            )
    control = client.get(f"/runs/{run_id}/control-room").json()
    stages = {stage["id"]: stage for stage in control["stages"]}
    assert control["current_stage"] == "screening_title_abstract"
    assert stages["screening_title_abstract"]["status"] == "active"
    assert stages["screening_title_abstract"]["progress"] == pytest.approx(0.5)
    for stage in ("snowball", "acquisition", "screening_full_text", "web_search", "report"):
        assert stages[stage]["status"] == "waiting"
        assert stages[stage]["progress"] == 0
    assert control["eta_scope"] == "stage"
    assert control["eta_open_ended"] is True
    assert 100 <= control["eta_seconds"] <= 140


def test_control_room_does_not_complete_future_stages_after_failure(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app(), email="failed-control@lab.org", org="Failed Control")
    org_id = client.get("/auth/me").json()["org_id"]
    now = datetime.now(UTC)
    with db_session() as session:
        run = Run(
            org_id=org_id,
            status="failed",
            question="q",
            config={
                "screen": True,
                "acquire": True,
                "full_text": True,
                "web_search": True,
                "paper_limit": 50,
            },
            created_at=now - timedelta(minutes=4),
            finished_at=now,
        )
        session.add(run)
        session.flush()
        run_id = run.id
        for index in range(50):
            work_id = f"WFAILED{index}"
            session.add(
                WorkRow(
                    id=work_id,
                    title=f"Failed work {index}",
                    payload={"id": work_id, "title": f"Failed work {index}"},
                )
            )
            session.flush()
            session.add(
                SourceRecordRow(
                    org_id=org_id,
                    run_id=run_id,
                    work_id=work_id,
                    source="corpus",
                    corpus_version="test",
                )
            )
        session.add(
            ScreeningDecisionRow(
                org_id=org_id,
                run_id=run_id,
                work_id="WFAILED0",
                reviewer="ensemble-unanimous",
                verdict="exclude",
                reason="irrelevant",
            )
        )
        event_rows = [
            ("protocol_synthesis", "protocol_created", {}),
            ("query_compilation", "queries_compiled", {}),
            ("retrieval", "dedup_done", {}),
            ("integrity", "integrity_signals_done", {}),
            ("ranking", "ranking_done", {}),
            ("web_search", "web_search_done", {"returned": 50}),
            ("screening_title_abstract", "screening_started", {"total": 50}),
            ("screening_title_abstract", "screening_progress", {"completed": 1, "total": 50}),
        ]
        for offset, (stage, event, payload) in enumerate(event_rows):
            session.add(
                RunEvent(
                    org_id=org_id,
                    run_id=run_id,
                    stage=stage,
                    event=event,
                    payload=payload,
                    created_at=now - timedelta(seconds=20 - offset),
                )
            )
    control = client.get(f"/runs/{run_id}/control-room").json()
    stages = {stage["id"]: stage for stage in control["stages"]}
    assert control["current_stage"] == "screening_title_abstract"
    assert control["overall_progress"] < 1
    assert stages["screening_title_abstract"]["status"] == "failed"
    assert stages["screening_title_abstract"]["progress"] == pytest.approx(0.02)
    assert stages["web_search"]["status"] == "completed"
    for stage in ("acquisition", "screening_full_text", "report"):
        assert stages[stage]["status"] == "waiting"
        assert stages[stage]["progress"] == 0


def test_control_room_keeps_optional_web_search_failure_visible_after_run_completion(
    corpus: DuckDBCorpus,
) -> None:
    client = _authed(create_app(), email="web-failure-control@lab.org", org="Web Failure")
    org_id = client.get("/auth/me").json()["org_id"]
    now = datetime.now(UTC)
    with db_session() as session:
        run = Run(
            org_id=org_id,
            status="completed",
            question="current standards",
            config={"web_search": True, "screen": False},
            created_at=now - timedelta(minutes=1),
            finished_at=now,
        )
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(
            RunEvent(
                org_id=org_id,
                run_id=run_id,
                stage="web_search",
                event="web_search_failed",
                payload={"queries_attempted": 1},
                created_at=now - timedelta(seconds=30),
            )
        )
    control = client.get(f"/runs/{run_id}/control-room").json()
    stages = {stage["id"]: stage for stage in control["stages"]}
    assert stages["web_search"]["status"] == "failed"
    assert stages["web_search"]["progress"] == 0
    assert control["overall_progress"] < 1


def test_api_paper_limit_bounds_output_without_capping_screening(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app(), email="final-limit@lab.org", org="Final Limit")
    project = client.post("/projects", json={"name": "limit semantics"}).json()
    created = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "transformer learning",
            "query": "transformer OR learning",
            "screen": True,
            "paper_limit": 1,
            "exhaustive": False,
        },
    )
    assert created.status_code == 202
    run_id = created.json()["public_id"]
    detail = client.get(f"/runs/{run_id}").json()
    assert detail["config"]["paper_limit"] == 1
    assert detail["config"]["screen_limit"] == 0
    events = client.get(f"/runs/{run_id}/events").json()
    started = next(event for event in events if event["event"] == "screening_started")
    assert started["payload"]["total"] > 1
    finalized = next(event for event in events if event["event"] == "output_set_finalized")
    assert finalized["payload"]["screened"] > 1
    assert finalized["payload"]["selected"] <= 1


def test_screening_queue_and_human_decisions(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "queue"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "learning", "query": "learning", "screen": True},
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    queue = client.get(f"/runs/{run_id}/queue?limit=100").json()["items"]
    assert len(queue) > 1
    first_page = client.get(f"/runs/{run_id}/queue?limit=1").json()
    assert first_page == {"items": queue[:1], "total": len(queue), "offset": 0, "limit": 1}
    second_page = client.get(f"/runs/{run_id}/queue?limit=1&offset=1").json()
    assert second_page["items"] == queue[1:2]
    assert second_page["total"] == len(queue)
    assert client.get(f"/runs/{run_id}/queue?limit=101").status_code == 422
    stale_work_id = queue[0]["work_id"]
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        session.add(
            ScreeningDecisionRow(
                org_id=run.org_id,
                run_id=run.id,
                work_id=stale_work_id,
                reviewer="screening:test",
                verdict="include",
                reason="The later pass resolved the ambiguity.",
            )
        )
    current_queue = client.get(f"/runs/{run_id}/queue?limit=100").json()["items"]
    assert stale_work_id not in {item["work_id"] for item in current_queue}
    work_id = current_queue[0]["work_id"]
    resp = client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": work_id, "verdict": "include", "reason": "clearly relevant"}],
    )
    assert resp.status_code == 201 and resp.json()["recorded"] == 1
    events = [e["event"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert "human_decisions_recorded" in events
    refreshed_queue = client.get(f"/runs/{run_id}/queue?limit=100").json()["items"]
    assert work_id not in {item["work_id"] for item in refreshed_queue}
    bad = client.post(f"/runs/{run_id}/decisions", json=[{"work_id": work_id, "verdict": "maybe"}])
    assert bad.status_code == 400


def test_public_review_reason_hides_internal_routing() -> None:
    raw = '{"verdict":"exclude","reason":"The DeepSeek reviewer found no eligible population."} You are the senior adjudicator.'
    assert _public_review_reason(raw) == "The automated reviewer found no eligible population."
    truncated = (
        '{"verdict":"exclude","reason":"No eligible outcome was reported.","quote":"incomplete'
    )
    assert _public_review_reason(truncated) == "No eligible outcome was reported."


def test_list_runs_newest_first_and_tenant_scoped(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app)
    project = client.post("/projects", json={"name": "demo"}).json()
    first = client.post(
        f"/projects/{project['id']}/runs", json={"question": "one", "query": "transformer"}
    ).json()["id"]
    second = client.post(
        f"/projects/{project['id']}/runs", json={"question": "two", "query": "transformer"}
    ).json()["id"]
    runs = client.get("/runs").json()
    assert [r["id"] for r in runs] == [second, first]
    assert runs[0]["question"] == "two" and runs[0]["status"] == "completed"
    assert runs[0]["created_at"]
    assert runs[0]["prisma"]["records_identified"] == 1
    assert client.get("/runs", params={"project_id": project["id"]}).json() == runs
    assert client.get("/runs", params={"project_id": project["id"] + 99}).json() == []
    other = _authed(app, email="other@example.org", org="Other")
    assert other.get("/runs").json() == []


def test_run_detail_carries_config_and_timestamps(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "t", "query": "transformer", "screen": True},
    ).json()["id"]
    run = client.get(f"/runs/{run_id}").json()
    assert run["project_id"] == project["id"]
    assert run["config"]["screen"] is True
    assert run["created_at"] and run["finished_at"]


def test_run_works_ranked_json(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app)
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformer architectures", "query": "transformer", "screen": True},
    ).json()["id"]
    payload = client.get(f"/runs/{run_id}/works").json()
    assert payload["total"] == 1
    top = payload["works"][0]
    assert top["rank"] == 1 and top["id"] == "W1"
    assert set(top["signals"]) == {"relevance", "impact", "recency"}
    assert 0 < top["score"] <= 1
    assert "relevance" in top["explanation"]
    assert top["verdict"] == "unsure"
    assert top["verdict_by"] == "model"
    assert client.get(f"/runs/{run_id}/works", params={"offset": 99}).json()["works"] == []
    client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": "W1", "verdict": "include", "reason": "on topic"}],
    )
    top = client.get(f"/runs/{run_id}/works").json()["works"][0]
    assert top["verdict"] == "include" and top["verdict_by"] == "human"
    other = _authed(app, email="b@other.org", org="OtherOrg")
    assert other.get(f"/runs/{run_id}/works").status_code == 404


def test_run_works_filters_preserve_global_relevance_rank(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "filters"}).json()
    created = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "transformer learning screening",
            "query": "transformer OR learning OR screening",
            "screen": True,
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    all_works = client.get(f"/runs/{run_id}/works", params={"limit": 20}).json()["works"]
    assert len(all_works) == 3
    rank_by_id = {work["id"]: work["rank"] for work in all_works}
    client.post(
        f"/runs/{run_id}/decisions",
        json=[
            {"work_id": "W1", "verdict": "include", "reason": "eligible"},
            {"work_id": "W3", "verdict": "exclude", "reason": "ineligible"},
        ],
    )
    included = client.get(
        f"/runs/{run_id}/works", params={"verdict": "include", "limit": 20}
    ).json()["works"]
    excluded = client.get(
        f"/runs/{run_id}/works", params={"verdict": "exclude", "limit": 20}
    ).json()["works"]
    unsure = client.get(f"/runs/{run_id}/works", params={"verdict": "unsure", "limit": 20}).json()[
        "works"
    ]
    assert [(work["id"], work["rank"]) for work in included] == [("W1", rank_by_id["W1"])]
    assert [(work["id"], work["rank"]) for work in excluded] == [("W3", rank_by_id["W3"])]
    assert [(work["id"], work["rank"]) for work in unsure] == [("W2", rank_by_id["W2"])]


def test_unavailable_web_search_fails_before_run_or_capacity_charge(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "gate"}).json()
    org_id = client.get("/auth/me").json()["org_id"]
    from sixsentences_server.core.db import CreditEventRow

    with db_session() as session:
        runs_before = session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id))
        charges_before = session.scalar(
            select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
        )
    response = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "t", "query": "transformer", "gate_protocol": True, "web_search": True},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "web_search_unavailable"
    assert (
        client.post("/usage/estimate", json={"mode": "search", "web_search": True}).status_code
        == 409
    )
    with db_session() as session:
        assert session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id)) == runs_before
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
            )
            == charges_before
        )
    get_settings.cache_clear()


def test_unavailable_pubmed_fails_before_run_or_capacity_charge(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIX_PUBMED_ENABLED", "0")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", "")
    get_settings.cache_clear()
    client = _authed(create_app(), email="pubmed-gate@lab.org", org="PubMed Gate")
    project = client.post("/projects", json={"name": "pubmed gate"}).json()
    org_id = client.get("/auth/me").json()["org_id"]
    from sixsentences_server.core.db import CreditEventRow

    with db_session() as session:
        runs_before = session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id))
        charges_before = session.scalar(
            select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
        )
    response = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "screening interventions", "pubmed": True},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "pubmed_unavailable"
    assert (
        client.post("/usage/estimate", json={"mode": "search", "pubmed": True}).status_code == 409
    )
    with db_session() as session:
        assert session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id)) == runs_before
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
            )
            == charges_before
        )
    get_settings.cache_clear()


def test_quick_answer_rejects_pubmed_consistently_with_usage_estimate(
    corpus: DuckDBCorpus,
) -> None:
    client = _authed(create_app(), email="pubmed-ask@lab.org", org="PubMed Ask")

    created = client.post(
        "/runs",
        json={"mode": "ask", "question": "Summarize the evidence", "pubmed": True},
    )
    estimated = client.post(
        "/usage/estimate",
        json={"mode": "ask", "pubmed": True},
    )

    assert created.status_code == 422
    assert estimated.status_code == 422
    assert "only available for search runs" in created.text
    assert "only available for search runs" in estimated.text


def test_pubmed_resume_paths_fail_closed_when_runtime_is_disabled(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested database arm cannot disappear from a resumed review."""
    monkeypatch.setenv("SIX_PUBMED_ENABLED", "0")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", "")
    get_settings.cache_clear()
    client = _authed(create_app(), email="pubmed-resume@lab.org", org="PubMed Resume")
    org_id = client.get("/auth/me").json()["org_id"]
    protocol = ReviewProtocol(question="pubmed", query_string="pubmed")
    with db_session() as session:
        paused = Run(
            org_id=org_id,
            question="paused PubMed review",
            status="paused",
            config={"mode": "search", "pubmed": True},
        )
        gated = Run(
            org_id=org_id,
            question="gated PubMed review",
            status="awaiting_protocol_approval",
            config={"mode": "search", "pubmed": True},
        )
        session.add_all([paused, gated])
        session.flush()
        protocol_row = ProtocolRow(
            org_id=org_id,
            project_id=None,
            payload=protocol.model_dump(mode="json"),
            version=1,
        )
        session.add(protocol_row)
        session.flush()
        gated.protocol_id = protocol_row.id
        paused_public_id = paused.public_id
        gated_public_id = gated.public_id

    paused_response = client.post(f"/runs/{paused_public_id}/resume")
    gated_response = client.post(f"/runs/{gated_public_id}/protocol/approve", json={})

    assert paused_response.status_code == 409
    assert paused_response.json()["detail"]["code"] == "pubmed_unavailable"
    assert gated_response.status_code == 409
    assert gated_response.json()["detail"]["code"] == "pubmed_unavailable"
    with db_session() as session:
        paused = session.scalar(select(Run).where(Run.public_id == paused_public_id))
        gated = session.scalar(select(Run).where(Run.public_id == gated_public_id))
        assert paused is not None and paused.status == "paused"
        assert gated is not None and gated.status == "awaiting_protocol_approval"
    get_settings.cache_clear()


def test_queued_pubmed_run_fails_closed_if_runtime_is_disabled_before_execution(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment change cannot silently mutate an accepted review."""
    import sixsentences_server.api.app as app_module

    monkeypatch.setenv("SIX_PUBMED_ENABLED", "0")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", "")
    get_settings.cache_clear()
    client = _authed(create_app(), email="pubmed-worker@lab.org", org="PubMed Worker")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        run = Run(
            org_id=org_id,
            question="queued PubMed review",
            status="pending",
            config={"mode": "search", "pubmed": True},
        )
        session.add(run)
        session.flush()
        run_id = run.id

    app_module._execute(
        run_id,
        query=None,
        live=False,
        pubmed=True,
        screen=False,
        paper_limit=0,
        screen_limit=0,
        exhaustive=True,
    )

    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.config["pubmed"] is True
        assert run.error is not None and "became unavailable" in run.error
        blocked = session.scalars(
            select(RunEvent).where(
                RunEvent.run_id == run_id,
                RunEvent.event == "pubmed_search_blocked",
            )
        ).all()
        assert len(blocked) == 1
    get_settings.cache_clear()


def test_pubmed_resume_workers_return_to_resumable_state_if_flag_turns_off(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment race blocks egress without terminally failing a checkpoint."""
    import sixsentences_server.api.app as app_module

    monkeypatch.setenv("SIX_PUBMED_ENABLED", "0")
    monkeypatch.setenv("SIX_PUBMED_EMAIL", "")
    get_settings.cache_clear()
    client = _authed(
        create_app(),
        email="pubmed-worker-resume@lab.org",
        org="PubMed Worker Resume",
    )
    org_id = client.get("/auth/me").json()["org_id"]
    protocol = ReviewProtocol(question="pubmed", query_string="pubmed")
    with db_session() as session:
        protocol_row = ProtocolRow(
            org_id=org_id,
            project_id=None,
            payload=protocol.model_dump(mode="json"),
            version=1,
        )
        session.add(protocol_row)
        session.flush()
        gated = Run(
            org_id=org_id,
            question="gated PubMed review",
            status="running",
            protocol_id=protocol_row.id,
            config={"mode": "search", "pubmed": True},
        )
        paused = Run(
            org_id=org_id,
            question="paused PubMed review",
            status="paused",
            protocol_id=protocol_row.id,
            config={"mode": "search", "pubmed": True},
        )
        session.add_all([gated, paused])
        session.flush()
        gated_id = gated.id
        paused_id = paused.id

    def forbidden_pipeline(*args: object, **kwargs: object) -> None:
        pytest.fail("the disabled PubMed resume reached the research pipeline")

    monkeypatch.setattr(app_module, "resume_run", forbidden_pipeline)
    monkeypatch.setattr(app_module, "execute_run", forbidden_pipeline)
    app_module._resume(gated_id, protocol.model_dump(mode="json"), {"pubmed": True})
    app_module._resume_paused(paused_id)

    with db_session() as session:
        gated = session.get(Run, gated_id)
        paused = session.get(Run, paused_id)
        assert gated is not None and gated.status == "awaiting_protocol_approval"
        assert paused is not None and paused.status == "paused"
        assert gated.error is None and gated.finished_at is None
        assert paused.error is None and paused.finished_at is None
        blocked = session.scalars(
            select(RunEvent).where(
                RunEvent.run_id.in_([gated_id, paused_id]),
                RunEvent.event == "pubmed_resume_blocked",
            )
        ).all()
        assert {
            (event.run_id, event.payload["resume_status"], event.payload["resumable"])
            for event in blocked
        } == {
            (gated_id, "awaiting_protocol_approval", True),
            (paused_id, "paused", True),
        }
    get_settings.cache_clear()


def test_web_search_requires_explicit_public_data_confirmation_before_charge(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    client = _authed(create_app(), email="web-scope@lab.org", org="Web Scope")
    org_id = client.get("/auth/me").json()["org_id"]
    from sixsentences_server.core.db import CreditEventRow

    with db_session() as session:
        runs_before = session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id))
        charges_before = session.scalar(
            select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
        )
    response = client.post(
        "/runs", json={"question": "Current public NIST guidance", "web_search": True}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "web_search_public_scope_confirmation_required"
    with db_session() as session:
        assert session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id)) == runs_before
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
            )
            == charges_before
        )
    get_settings.cache_clear()


def test_quick_answer_web_intent_requires_confirmation_before_charge(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ask planner cannot silently turn an unconfirmed prompt into Sonar egress."""
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    client = _authed(create_app(), email="quick-web-scope@lab.org", org="Quick Web Scope")
    org_id = client.get("/auth/me").json()["org_id"]
    from sixsentences_server.core.db import CreditEventRow

    with db_session() as session:
        runs_before = session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id))
        charges_before = session.scalar(
            select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
        )
    response = client.post(
        "/runs", json={"question": "Search the web for current public NIST guidance", "mode": "ask"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "web_search_public_scope_confirmation_required"
    with db_session() as session:
        assert session.scalar(select(func.count(Run.id)).where(Run.org_id == org_id)) == runs_before
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.org_id == org_id)
            )
            == charges_before
        )
    get_settings.cache_clear()


@pytest.mark.parametrize("stream", [False, True])
def test_chat_web_intent_requires_per_turn_confirmation_before_charge(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    """Missing turn consent fails before a row, model call or capacity charge exists."""
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    client = _authed(
        create_app(), email=f"chat-web-scope-{stream}@lab.org", org=f"Chat Web Scope {stream}"
    )
    org_id = client.get("/auth/me").json()["org_id"]
    from sixsentences_server.core.db import ChatMessageRow, ChatTurnRow, CreditEventRow

    with db_session() as session:
        run = Run(
            org_id=org_id,
            question="A completed scholarly review",
            status="completed",
            config={"mode": "search"},
        )
        session.add(run)
        session.flush()
        public_id = run.public_id
        run_id = run.id

    def should_not_answer(*args: object, **kwargs: object) -> None:
        raise AssertionError("an unconfirmed web turn must not reach the agent")

    monkeypatch.setattr("sixsentences_server.api.app.answer_question", should_not_answer)
    body = {"question": "Search the web for the latest official guidance"}
    path = f"/runs/{public_id}/chat"
    if stream:
        path += "/stream"
        body["turn_id"] = "unconfirmed-web-turn"
    response = client.post(path, json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "web_search_public_scope_confirmation_required"
    with db_session() as session:
        assert (
            session.scalar(
                select(func.count(CreditEventRow.id)).where(CreditEventRow.run_id == run_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count(ChatMessageRow.id)).where(ChatMessageRow.run_id == run_id)
            )
            == 0
        )
        assert (
            session.scalar(select(func.count(ChatTurnRow.id)).where(ChatTurnRow.run_id == run_id))
            == 0
        )
    get_settings.cache_clear()


def test_free_quick_answer_keeps_the_bounded_direct_url_reader(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading an exact user-provided page is not a new web-discovery search."""
    queued: list[int] = []
    monkeypatch.setattr(
        "sixsentences_server.api.app.enqueue_job",
        lambda background, target, run_id, *args, **kwargs: queued.append(run_id),
    )
    client = _authed(create_app(), email="free-direct-url@lab.org", org="Direct URL")
    response = client.post(
        "/runs",
        json={
            "mode": "ask",
            "question": "Explain https://developer.hashicorp.com/terraform/cli/commands/plan",
        },
    )
    assert response.status_code == 202, response.text
    assert queued == [response.json()["id"]]
    with db_session() as session:
        row = session.get(Run, response.json()["id"])
        assert row is not None and row.config["web_search"] is False


def test_legacy_resume_paths_disable_unconfirmed_web_search(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both pause and protocol resumes fail closed for legacy web-enabled rows."""
    import sixsentences_server.api.app as app_module

    client = _authed(create_app(), email="legacy-web-resume@lab.org", org="Legacy Web Resume")
    org_id = client.get("/auth/me").json()["org_id"]
    protocol = ReviewProtocol(question="legacy", query_string="legacy")
    with db_session() as session:
        paused = Run(
            org_id=org_id,
            question="legacy paused run",
            status="paused",
            config={"mode": "search", "web_search": True},
        )
        gated = Run(
            org_id=org_id,
            question="legacy gated run",
            status="awaiting_protocol_approval",
            config={"mode": "search", "web_search": True},
        )
        session.add_all([paused, gated])
        session.flush()
        row = ProtocolRow(
            org_id=org_id, project_id=None, payload=protocol.model_dump(mode="json"), version=1
        )
        session.add(row)
        session.flush()
        gated.protocol_id = row.id
        paused_public_id = paused.public_id
        gated_public_id = gated.public_id
    scheduled: list[tuple[object, tuple[object, ...]]] = []

    def fake_enqueue(background: object, task: object, *args: object, **kwargs: object) -> int:
        scheduled.append((task, args))
        return 1

    monkeypatch.setattr(app_module, "enqueue_job", fake_enqueue)
    assert client.post(f"/runs/{paused_public_id}/resume").status_code == 202
    assert client.post(f"/runs/{gated_public_id}/protocol/approve", json={}).status_code == 202
    assert scheduled[0][0] is app_module._resume_paused
    assert scheduled[1][0] is app_module._resume
    assert scheduled[1][1][-1]["web_search"] is False
    with db_session() as session:
        paused = session.scalar(select(Run).where(Run.public_id == paused_public_id))
        gated = session.scalar(select(Run).where(Run.public_id == gated_public_id))
        assert paused is not None and paused.config["web_search"] is False
        assert gated is not None and gated.config["web_search"] is False
        skipped = session.scalars(
            select(RunEvent).where(
                RunEvent.run_id.in_([paused.id, gated.id]), RunEvent.event == "web_search_skipped"
            )
        ).all()
        assert len(skipped) == 2


def test_living_refresh_does_not_inherit_web_search_confirmation(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unattended refresh stays scholarly-only and records why."""
    import sixsentences_server.api.app as app_module

    client = _authed(create_app(), email="living-web-scope@lab.org", org="Living Web Scope")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        baseline = Run(
            org_id=org_id,
            question="A living review",
            status="completed",
            config={
                "mode": "search",
                "living": True,
                "web_search": True,
                "web_search_public_data_confirmed": True,
            },
        )
        session.add(baseline)
        session.flush()
        protocol = ProtocolRow(
            org_id=org_id,
            project_id=None,
            version=1,
            payload=ReviewProtocol(
                question=baseline.question,
                query_string="living review",
            ).model_dump(mode="json"),
        )
        session.add(protocol)
        session.flush()
        baseline.protocol_id = protocol.id
        baseline_public_id = baseline.public_id
    scheduled: list[tuple[object, ...]] = []

    def fake_enqueue(background: object, task: object, *args: object, **kwargs: object) -> int:
        scheduled.append(args)
        return 1

    monkeypatch.setattr(app_module, "enqueue_job", fake_enqueue)
    response = client.post(f"/runs/{baseline_public_id}/living/refresh", json={"scope": "delta"})
    assert response.status_code == 202, response.text
    refresh_public_id = response.json()["public_id"]
    with db_session() as session:
        refresh = session.scalar(select(Run).where(Run.public_id == refresh_public_id))
        assert refresh is not None
        assert refresh.config["web_search"] is False
        assert refresh.config["web_search_public_data_confirmed"] is False
        assert refresh.config["living_refresh_web_search_skipped"] is True
        baseline = session.scalar(select(Run).where(Run.public_id == baseline_public_id))
        assert baseline is not None
        event = session.scalar(
            select(RunEvent).where(
                RunEvent.run_id == baseline.id, RunEvent.event == "living_refresh_started"
            )
        )
        assert event is not None
        assert event.payload["web_search"] == "requires_fresh_confirmation"
    assert scheduled


def test_project_and_run_lifecycle_rename_delete(
    corpus: DuckDBCorpus, enforced_foreign_keys: None
) -> None:
    app = create_app()
    client = _authed(app)
    project = client.post("/projects", json={"name": "temp"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "t", "query": "transformer"}
    ).json()["id"]
    assert (
        client.patch(f"/runs/{run_id}", json={"title": "My thesis search"}).json()["title"]
        == "My thesis search"
    )
    listed = client.get("/runs").json()[0]
    assert listed["title"] == "My thesis search" and listed["question"] == "t"
    assert client.patch(f"/runs/{run_id}", json={"title": ""}).json()["title"] is None
    assert (
        client.patch(f"/projects/{project['id']}", json={"name": "Thesis"}).json()["name"]
        == "Thesis"
    )
    assert client.delete(f"/runs/{run_id}").json()["deleted"] == run_id
    assert client.get(f"/runs/{run_id}").status_code == 404
    assert client.get("/runs").json() == []
    run2 = client.post(
        f"/projects/{project['id']}/runs", json={"question": "u", "query": "transformer"}
    ).json()["id"]
    deleted = client.delete(f"/projects/{project['id']}").json()
    assert deleted["runs_deleted"] == 1
    assert client.get(f"/runs/{run2}").status_code == 404
    assert client.get("/projects").json() == []
    other = _authed(app, email="x@other.org", org="XOrg")
    assert other.patch(f"/runs/{run_id}", json={"title": "nope"}).status_code == 404
    assert other.delete(f"/projects/{project['id']}").status_code == 404


def test_protocol_regenerate_at_the_gate(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "gate2"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformer attention", "gate_protocol": True},
    ).json()["id"]
    first = client.get(f"/runs/{run_id}/protocol").json()["protocol"]
    regenerated = client.post(f"/runs/{run_id}/protocol/regenerate")
    assert regenerated.status_code == 200
    assert regenerated.json()["protocol"]["question"] == first["question"]
    events = [e["event"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert "protocol_regenerated" in events
    client.post(f"/runs/{run_id}/protocol/approve", json={})
    assert client.post(f"/runs/{run_id}/protocol/regenerate").status_code == 409


def test_forgot_and_reset_endpoints(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> None:
    import sixsentences_server.api.app as api_module

    sent: list[object] = []
    monkeypatch.setattr(get_settings(), "smtp_host", "mail")
    monkeypatch.setattr(
        api_module, "send_email", lambda recipient, rendered, settings: sent.append(rendered)
    )
    client = TestClient(create_app())
    client.post(
        "/auth/register",
        json={
            "email": "amnesia@lab.org",
            "password": "OldPassword123!",
            "org_name": "Amnesia",
            "name": "Amy",
        },
    )
    assert client.post("/auth/forgot", json={"email": "ghost@lab.org"}).status_code == 200
    assert sent == []
    assert client.post("/auth/forgot", json={"email": "amnesia@lab.org"}).status_code == 200
    assert len(sent) == 1
    reset_url = re.search("https?://\\S+/reset\\?token=(\\S+)", sent[0].text)
    assert reset_url is not None
    token = reset_url.group(1)
    bogus = client.post("/auth/reset", json={"token": "bogus", "password": "whatever12"})
    assert bogus.status_code == 400
    good = client.post("/auth/reset", json={"token": token, "password": "newpassword1"})
    assert good.status_code == 200
    assert (
        client.post(
            "/auth/login", json={"email": "amnesia@lab.org", "password": "newpassword1"}
        ).status_code
        == 200
    )


def test_delete_all_runs_wipes_history_but_keeps_projects(
    corpus: DuckDBCorpus, enforced_foreign_keys: None
) -> None:
    from sixsentences_server.core.db import DocumentAnnotationRow

    client = _authed(create_app())
    project = client.post("/projects", json={"name": "keepme"}).json()
    client.post(f"/projects/{project['id']}/runs", json={"question": "one", "query": "t"})
    second = client.post("/runs", json={"question": "two", "mode": "ask"}).json()
    assert len(client.get("/runs").json()) == 2
    with db_session() as session:
        run = session.get(Run, int(second["id"]))
        assert run is not None
        session.add(WorkRow(id="WDELETEALL", title="Annotated paper", year=2025))
        session.flush()
        document = DocumentRow(
            org_id=run.org_id,
            run_id=run.id,
            work_id="WDELETEALL",
            status="retrieved",
            checksum="delete-all-checksum",
            storage_path="blobs/delete-all-checksum",
            text_status="parsed",
        )
        session.add(document)
        session.flush()
        session.add(
            DocumentAnnotationRow(
                org_id=run.org_id,
                document_id=document.id,
                source="user",
                page=1,
                quote="A stable annotated passage.",
                note="Keep track of this note.",
            )
        )
    wiped = client.delete("/runs")
    assert wiped.status_code == 200 and wiped.json()["deleted_runs"] == 2
    assert client.get("/runs").json() == []
    assert [p["name"] for p in client.get("/projects").json()] == ["keepme"]


def test_document_translation_is_page_scoped_and_cached(
    corpus: DuckDBCorpus, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from sixsentences_server.acquisition.store import LocalDocumentStore

    client = _authed(create_app(), email="translator@lab.org", org="Translation Lab")
    org_id = client.get("/auth/me").json()["org_id"]
    store = LocalDocumentStore(settings.documents_dir)
    checksum, storage_path = store.put(b"%PDF-1.4 deterministic translation fixture")
    with db_session() as session:
        session.add(WorkRow(id="WTRANSLATE", title="Translation paper", year=2026))
        session.flush()
        document = DocumentRow(
            org_id=org_id,
            work_id="WTRANSLATE",
            status="retrieved",
            content_type="application/pdf",
            checksum=checksum,
            storage_path=storage_path,
            text_status="parsed",
        )
        session.add(document)
        session.flush()
        document_id = document.id

    class FakePool:
        def complete(self, task: object, *, system: str, prompt: str, max_tokens: int):
            assert "German" in system
            assert "SOURCE PAGE 1" in prompt
            assert "Evidence remains uncertain." in prompt
            return SimpleNamespace(
                text="Die Evidenz bleibt unsicher.", provider="fake", model="translation-test"
            )

    monkeypatch.setattr(
        "sixsentences_server.api.app.extract_page_texts",
        lambda content, max_pages=60: ["Evidence remains uncertain."],
    )
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    translated = client.post(
        f"/documents/{document_id}/translation", json={"page": 1, "language": "de"}
    )
    assert translated.status_code == 200, translated.text
    assert translated.json() == {
        "page": 1,
        "language": "de",
        "text": "Die Evidenz bleibt unsicher.",
        "cached": False,
    }
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: None)
    cached = client.post(
        f"/documents/{document_id}/translation", json={"page": 1, "language": "de"}
    )
    assert cached.status_code == 200
    assert cached.json()["cached"] is True
    assert cached.json()["text"] == "Die Evidenz bleibt unsicher."


def test_full_document_translation_is_queued_and_reports_progress(
    corpus: DuckDBCorpus, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO

    import pypdf

    import sixsentences_server.api.app as app_module
    from sixsentences_server.acquisition.store import LocalDocumentStore

    client = _authed(create_app(), email="full-translator@lab.org", org="Full Translation")
    org_id = client.get("/auth/me").json()["org_id"]
    source = BytesIO()
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    writer.write(source)
    checksum, storage_path = LocalDocumentStore(settings.documents_dir).put(source.getvalue())
    with db_session() as session:
        session.add(WorkRow(id="WFULLTRANSLATE", title="Complete translation", year=2026))
        session.flush()
        document = DocumentRow(
            org_id=org_id,
            work_id="WFULLTRANSLATE",
            status="retrieved",
            content_type="application/pdf",
            checksum=checksum,
            storage_path=storage_path,
            text_status="parsed",
        )
        session.add(document)
        session.flush()
        document_id = document.id
    scheduled: list[tuple[object, ...]] = []

    def fake_enqueue(background: object, task: object, *args: object, **kwargs: object) -> int:
        scheduled.append(args)
        return 73

    monkeypatch.setattr(app_module, "enqueue_job", fake_enqueue)
    queued = client.post(f"/documents/{document_id}/translations", json={"language": "de"})
    assert queued.status_code == 202, queued.text
    assert queued.json() == {
        "status": "queued",
        "language": "de",
        "page_count": 2,
        "completed_pages": 0,
        "percent": 0,
        "ready": False,
        "pages_without_text": [],
        "error": None,
        "updated_at": queued.json()["updated_at"],
    }
    assert scheduled == [(document_id, org_id, "de")]
    status = client.get(f"/documents/{document_id}/translations/de")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert status.json()["page_count"] == 2
    assert client.get(f"/documents/{document_id}/translations/de/file").status_code == 404


def test_full_document_translation_task_resumes_cached_pages(
    corpus: DuckDBCorpus, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO

    import pypdf

    import sixsentences_server.api.app as app_module
    from sixsentences_server.acquisition.store import LocalDocumentStore
    from sixsentences_server.documents.translation import read_manifest, translation_paths

    app = create_app()
    client = _authed(app, email="resume-translator@lab.org", org="Resume Translation")
    org_id = client.get("/auth/me").json()["org_id"]
    source = BytesIO()
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    writer.write(source)
    checksum, storage_path = LocalDocumentStore(settings.documents_dir).put(source.getvalue())
    with db_session() as session:
        session.add(WorkRow(id="WRESUMETRANSLATE", title="Resumable translation", year=2026))
        session.flush()
        document = DocumentRow(
            org_id=org_id,
            work_id="WRESUMETRANSLATE",
            status="retrieved",
            content_type="application/pdf",
            checksum=checksum,
            storage_path=storage_path,
            text_status="parsed",
        )
        session.add(document)
        session.flush()
        document_id = document.id
    monkeypatch.setattr(
        app_module,
        "extract_page_texts_preserving_paragraphs",
        lambda content, max_pages=400: ["First source page.", "Second source page."],
    )
    translated_pages: list[int] = []

    def fake_translate(session: object, **kwargs: object) -> str:
        page = int(kwargs["page_number"])
        translated_pages.append(page)
        return f"Translated page {page}."

    monkeypatch.setattr(app_module, "_translate_document_page_text", fake_translate)
    monkeypatch.setattr(
        app_module, "render_translated_pdf", lambda **kwargs: b"%PDF-1.4 translated reading edition"
    )
    paths = translation_paths(
        settings.documents_dir,
        checksum=checksum,
        org_id=org_id,
        document_id=document_id,
        language="de",
    )
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.page(1, "de").write_text("Already translated.", encoding="utf-8")
    app_module._execute_document_translation(document_id, org_id, "de")
    assert translated_pages == [2]
    assert paths.pdf.read_bytes() == b"%PDF-1.4 translated reading edition"
    manifest = read_manifest(paths.manifest)
    assert manifest is not None
    assert manifest["status"] == "completed"
    assert manifest["completed_pages"] == 2


def test_document_translation_retains_identifiers_dropped_by_provider() -> None:
    import sixsentences_server.api.app as app_module

    translated = app_module._preserve_missing_translation_identifiers(
        "The source is available at https://example.org/paper and doi:10.1234/example.2026.",
        "Die Quelle ist online verfügbar.",
        language="de",
    )
    assert "Quellenkennungen aus dem Original:" in translated
    assert "https://example.org/paper" in translated
    assert "10.1234/example.2026" in translated
    assert (
        app_module._preserve_missing_translation_identifiers(
            "See arXiv:2601.12345.", "Siehe arXiv:2601.12345.", language="de"
        )
        == "Siehe arXiv:2601.12345."
    )


def test_translated_reading_edition_loads_graphics_support() -> None:
    from sixsentences_server.documents.translation import translated_latex

    source = translated_latex(
        title="Complete translated paper",
        language="de",
        language_name="German",
        translated_pages=["Vollständig übersetzter Inhalt."],
        pages_without_text=[],
    )
    assert "\\usepackage{graphicx}" in source


def test_account_deletion_needs_the_password_and_wipes_the_workspace(
    corpus: DuckDBCorpus, enforced_foreign_keys: None
) -> None:
    from sixsentences_server.core.db import CreditEventRow, LLMCallRow

    app = create_app()
    client = _authed(app, email="leaver@lab.org", org="Leavers")
    client.post("/runs", json={"question": "so long", "mode": "ask"})
    with db_session() as session:
        org_id = session.scalars(select(Org).where(Org.name == "Leavers")).one().id
    wrong = client.request("DELETE", "/auth/account", json={"password": "not-it"})
    assert wrong.status_code == 403
    gone = client.request("DELETE", "/auth/account", json={"password": "StrongPass123!"})
    assert gone.status_code == 200 and gone.json()["deleted"] == "workspace"
    assert client.get("/auth/me").status_code == 401
    with db_session() as session:
        for model in (Run, CreditEventRow, LLMCallRow):
            leftover = session.scalar(
                select(func.count()).select_from(model).where(model.org_id == org_id)
            )
            assert leftover == 0, f"{model.__tablename__} left {leftover} orphaned rows"
    fresh = TestClient(app)
    assert (
        fresh.post(
            "/auth/login", json={"email": "leaver@lab.org", "password": "StrongPass123!"}
        ).status_code
        == 401
    )


def test_the_library_lists_stored_files_across_runs_deduplicated(corpus: DuckDBCorpus) -> None:
    """GET /documents is the workspace library: stored files only, one entry
    per file even when attached to several chats, searchable by title."""
    app = create_app()
    client = _authed(app, email="lib@lab.org", org="Lib")
    project = client.post("/projects", json={"name": "d"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "t", "query": "transformer"}
    ).json()["id"]
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        from sixsentences_server.core.db import WorkRow

        session.add(WorkRow(id="W900", title="Stored transformer study", year=2024))
        session.add(WorkRow(id="W901", title="Unstored side note", year=2020))
        session.flush()
        for run_ref in (run.id, None):
            session.add(
                DocumentRow(
                    org_id=run.org_id,
                    run_id=run_ref,
                    work_id="W900",
                    status="retrieved",
                    content_type="application/pdf",
                    checksum="samefile",
                    storage_path="blobs/samefile",
                    byte_size=10,
                    text_status="parsed",
                )
            )
        session.add(
            DocumentRow(
                org_id=run.org_id,
                run_id=run.id,
                work_id="W901",
                status="not_retrieved",
                byte_size=0,
            )
        )
    library = client.get("/documents").json()
    assert [d["work_id"] for d in library] == ["W900"]
    entry = library[0]
    assert entry["title"] == "Stored transformer study" and entry["year"] == 2024
    assert entry["has_file"] is True
    filed = client.patch(
        f"/documents/{entry['id']}", json={"project_id": project["id"], "folder": "Methods"}
    )
    assert filed.status_code == 200
    refreshed = client.get("/documents").json()[0]
    assert (refreshed["project_name"], refreshed["folder"]) == ("d", "Methods")
    note = client.post(
        f"/documents/{entry['id']}/annotations",
        json={"page": 2, "quote": "A sufficiently long verified passage.", "note": "Key method"},
    )
    assert note.status_code == 201 and note.json()["author"] == "lib@lab.org"
    ai_note = client.post(
        f"/documents/{entry['id']}/annotations",
        json={
            "page": 3,
            "quote": "Another sufficiently long passage from the assistant.",
            "note": "AI finding",
            "source": "assistant",
            "color": "amber",
        },
    )
    assert ai_note.status_code == 201 and ai_note.json()["author"] == "SixSentences AI"
    page_comment = client.post(
        f"/documents/{entry['id']}/annotations",
        json={"page": 1, "quote": "", "note": "Check the introduction", "color": "blue"},
    )
    assert page_comment.status_code == 201
    assert page_comment.json()["quote"] == ""
    edited_comment = client.patch(
        f"/documents/{entry['id']}/annotations/{page_comment.json()['id']}",
        json={"note": "Introduction checked", "color": "rose"},
    )
    assert edited_comment.status_code == 200
    assert (edited_comment.json()["note"], edited_comment.json()["color"]) == (
        "Introduction checked",
        "rose",
    )
    blank = client.post(
        f"/documents/{entry['id']}/annotations", json={"page": 1, "quote": "", "note": ""}
    )
    assert blank.status_code == 422
    assert len(client.get(f"/documents/{entry['id']}/annotations").json()) == 3
    deleted = client.delete(f"/documents/{entry['id']}/annotations/{note.json()['id']}")
    assert deleted.status_code == 204
    remaining_notes = client.get(f"/documents/{entry['id']}/annotations").json()
    assert [item["id"] for item in remaining_notes] == [
        page_comment.json()["id"],
        ai_note.json()["id"],
    ]
    assert client.get("/documents?q=transformer%20study").json()[0]["work_id"] == "W900"
    assert client.get("/documents?q=nomatch").json() == []
    removed = client.delete(f"/documents/{entry['id']}")
    assert removed.status_code == 204
    assert client.get("/documents").json() == []
    other = _authed(app, email="other@lab.org", org="OtherLib")
    assert other.get("/documents").json() == []


def test_library_bulk_actions_link_sources_and_delete_atomically(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="bulk@lab.org", org="Bulk Library")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        session.add_all(
            [
                WorkRow(
                    id="WBULK1",
                    doi="10.1000/bulk-one",
                    title="First linked Library paper",
                    year=2024,
                    payload={"authors": ["Doe, Jane"]},
                ),
                WorkRow(
                    id="WBULK2",
                    title="Second linked Library paper",
                    year=2025,
                    payload={"authors": ["Smith, Kai"]},
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                DocumentRow(
                    org_id=org_id,
                    work_id="WBULK1",
                    status="retrieved",
                    content_type="application/pdf",
                    checksum="bulk-one",
                    storage_path="blobs/bulk-one",
                    byte_size=10,
                    text_status="parsed",
                ),
                DocumentRow(
                    org_id=org_id,
                    work_id="WBULK2",
                    status="retrieved",
                    content_type="application/pdf",
                    checksum="bulk-two",
                    storage_path="blobs/bulk-two",
                    byte_size=11,
                    text_status="parsed",
                ),
            ]
        )
    library = client.get("/documents").json()
    document_ids = [document["id"] for document in library]
    manuscript = client.post(
        "/writer", json={"title": "Evidence manuscript", "template": "blank"}
    ).json()
    linked = client.post(
        f"/writer/{manuscript['public_id']}/sources/library", json={"document_ids": document_ids}
    )
    assert linked.status_code == 201, linked.text
    assert (linked.json()["created"], linked.json()["skipped"]) == (2, 0)
    sources = client.get(f"/writer/{manuscript['public_id']}/sources").json()
    assert {source["source_document_id"] for source in sources} == set(document_ids)
    assert {source["title"] for source in sources} == {
        "First linked Library paper",
        "Second linked Library paper",
    }
    assert any("Doe, Jane" in source["authors"] for source in sources)
    repeated = client.post(
        f"/writer/{manuscript['public_id']}/sources/library", json={"document_ids": document_ids}
    ).json()
    assert (repeated["created"], repeated["skipped"]) == (0, 2)
    partial_link = client.post(
        f"/writer/{manuscript['public_id']}/sources/library",
        json={"document_ids": [document_ids[0], 999999]},
    )
    assert partial_link.status_code == 404
    assert len(client.get(f"/writer/{manuscript['public_id']}/sources").json()) == 2
    partial_delete = client.post(
        "/documents/bulk-delete", json={"document_ids": [document_ids[0], 999999]}
    )
    assert partial_delete.status_code == 404
    assert len(client.get("/documents").json()) == 2
    other = _authed(app, email="bulk-other@lab.org", org="Other Bulk")
    forbidden = other.post("/documents/bulk-delete", json={"document_ids": document_ids})
    assert forbidden.status_code == 404
    assert len(client.get("/documents").json()) == 2
    deleted = client.post("/documents/bulk-delete", json={"document_ids": document_ids})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 2
    assert client.get("/documents").json() == []
    retained_sources = client.get(f"/writer/{manuscript['public_id']}/sources").json()
    assert len(retained_sources) == 2
    assert {source["source_document_id"] for source in retained_sources} == {None}
    citations = client.get(f"/writer/{manuscript['public_id']}/citations").json()
    assert {citation["title"] for citation in citations} >= {
        "First linked Library paper",
        "Second linked Library paper",
    }


def test_onboarding_is_stamped_once_and_reported(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="newbie@lab.org", org="Newbies")
    assert client.get("/auth/me").json()["onboarded"] is False
    done = client.post("/auth/onboarded")
    assert done.status_code == 200 and done.json()["onboarded"] is True
    assert client.get("/auth/me").json()["onboarded"] is True
    from sixsentences_server.core.db import User, db_session

    uid = client.get("/auth/me").json()["user_id"]
    with db_session() as session:
        first_stamp = session.get(User, uid).onboarded_at
    client.post("/auth/onboarded")
    with db_session() as session:
        assert session.get(User, uid).onboarded_at == first_stamp
    key = client.post("/auth/api-keys", json={"name": "robot"}).json()["api_key"]
    robot = TestClient(app)
    robot.headers["Authorization"] = f"Bearer {key}"
    assert robot.post("/auth/onboarded").status_code == 403


def test_user_language_preference_is_persisted(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app, email="language@lab.org", org="Languages")
    assert client.get("/auth/me").json()["language"] == "en"
    changed = client.patch("/auth/preferences", json={"language": "de"})
    assert changed.status_code == 200
    assert changed.json() == {
        "language": "de",
        "assistant_preferences": {
            "detail": "balanced",
            "tone": "academic",
            "format": "adaptive",
            "custom_instructions": "",
        },
    }
    assert client.get("/auth/me").json()["language"] == "de"
    assert client.patch("/auth/preferences", json={"language": "fr"}).status_code == 422
    assistant = {
        "detail": "thorough",
        "tone": "critical",
        "format": "structured",
        "custom_instructions": "Assume I know standard ML terminology.",
    }
    personalized = client.patch("/auth/preferences", json={"assistant_preferences": assistant})
    assert personalized.status_code == 200
    assert personalized.json()["assistant_preferences"] == assistant
    assert client.get("/auth/me").json()["assistant_preferences"] == assistant
    assert (
        client.patch(
            "/auth/preferences",
            json={"assistant_preferences": {**assistant, "tone": "unrestricted"}},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/auth/preferences",
            json={"assistant_preferences": {**assistant, "custom_instructions": "x" * 801}},
        ).status_code
        == 422
    )
    key = client.post("/auth/api-keys", json={"name": "robot"}).json()["api_key"]
    robot = TestClient(app)
    robot.headers["Authorization"] = f"Bearer {key}"
    assert robot.patch("/auth/preferences", json={"language": "en"}).status_code == 403
    assert (
        robot.patch("/auth/preferences", json={"assistant_preferences": assistant}).status_code
        == 403
    )
