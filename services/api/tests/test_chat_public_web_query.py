"""Exact public-query approval, privacy isolation and durable replay contracts."""

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_agent_tools import _authed

from sixsentences_server.api.app import (
    ChatRequest,
    _chat_turn_request_hash,
    _require_public_chat_web_search_scope,
    create_app,
)
from sixsentences_server.chat.service import (
    PUBLIC_WEB_QUERY_NOTICE_VERSION,
    ChatError,
    _confirmed_public_web_query,
    answer_question,
    canonical_public_web_query,
    web_search_topic_required,
)
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    ChatMessageRow,
    ChatTurnRow,
    CreditEventRow,
    Run,
    db_session,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool


@pytest.mark.parametrize(
    "user_request",
    [
        "SChau auch mal noch im Internet nach",
        "Look it up online too",
        "Search the web",
        "Can you look it up online?",
        "Check the official documentation",
        "Schau dazu bitte auch im Internet nach, insbesondere in der offiziellen Dokumentation.",
        "Schau dazu bitte auch im Internet nach, vor allem in offizieller Dokumentation.",
        "Look it up online too, especially in the official docs.",
        "Schau dazu bitte vertieft im Internet nach.",
        "Kannst du dazu vertieft recherchieren?",
        "Could you look it up online meticulously?",
    ],
)
def test_generic_followup_cannot_become_a_search_query(user_request: str) -> None:
    assert web_search_topic_required(user_request)
    with pytest.raises(ChatError, match="public topic"):
        _confirmed_public_web_query(user_request, mock_pool(), [])
    with pytest.raises(ValidationError):
        ChatRequest(question="Search the web", web_search_query=user_request)


@pytest.mark.parametrize(
    "query",
    [
        "HashiCorp Terraform plan apply",
        "RFC 9110",
        "CVE-2024-12345",
        "ISO 27001",
        "Search the web for Kubernetes instead",
    ],
)
def test_substantive_public_queries_remain_supported(query: str) -> None:
    assert not web_search_topic_required(query)
    assert canonical_public_web_query(f"  {query}  ") == query


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?",
        "x" * 401,
        "Terraform\nprivate",
        "Terraform\u202eprivate",
        "Terraform user@example.org",
        "Terraform password=secret-value",
        "schau internet",
    ],
)
def test_invalid_or_redacted_queries_fail_before_search_admission(query: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="Search the web", web_search_query=query)


def test_exact_query_accepts_only_benign_spacing_and_preserves_the_bound_text() -> None:
    query = "HashiCorp  Terraform plan apply"
    body = ChatRequest(question="Search the web", web_search_query=f" {query} ")
    assert body.web_search_query == query
    assert canonical_public_web_query("x" * 400) == "x" * 400


def test_notice_version_is_explicit_optional_and_exact() -> None:
    assert PUBLIC_WEB_QUERY_NOTICE_VERSION == "public-web-query-2026-09-04.1"
    assert ChatRequest(question="Search the web").web_search_notice_version is None
    assert (
        ChatRequest(
            question="Search the web", web_search_notice_version=None
        ).web_search_notice_version
        is None
    )
    body = ChatRequest(
        question="Search the web",
        web_search_notice_version=PUBLIC_WEB_QUERY_NOTICE_VERSION,
        web_search_public_data_confirmed=True,
        web_search_query="HashiCorp Terraform",
    )
    assert body.web_search_notice_version == PUBLIC_WEB_QUERY_NOTICE_VERSION


@pytest.mark.parametrize(
    "confirmed,query", [(False, None), (False, "Terraform plan"), (True, None)]
)
def test_notice_cannot_replace_exact_query_or_confirmation(
    confirmed: bool, query: str | None
) -> None:
    values = {
        "web_search_notice_version": PUBLIC_WEB_QUERY_NOTICE_VERSION,
        "web_search_public_data_confirmed": confirmed,
        "web_search_query": query,
    }
    with pytest.raises(ValidationError, match="exact public search terms"):
        ChatRequest(question="Search the web", **values)
    with Session() as session, pytest.raises(ChatError, match="exact public search terms"):
        answer_question(session, Run(org_id=1), mock_pool(), "Search the web", **values)


@pytest.mark.parametrize("version", ["", "unknown", "x" * 65, 1, True, [], {}])
def test_direct_service_rejects_invalid_notice_before_work(version: Any) -> None:
    with Session() as session, pytest.raises(ChatError, match="notice version"):
        answer_question(
            session, Run(org_id=1), mock_pool(), "Search the web", web_search_notice_version=version
        )


@pytest.mark.parametrize(
    "question,confirmed",
    [
        ("Search the web", False),
        ("Explain the previous answer", True),
        ("Use only existing sources; do not search the web", True),
        ("Read https://example.org/terraform", True),
    ],
)
def test_query_or_notice_cannot_grant_itself_web_intent_or_confirmation(
    monkeypatch: pytest.MonkeyPatch, question: str, confirmed: bool
) -> None:
    monkeypatch.setattr(
        "sixsentences_server.api.app.get_settings", lambda: SimpleNamespace(websearch_enabled=True)
    )
    body = ChatRequest(
        question=question,
        web_search_public_data_confirmed=confirmed,
        web_search_query="HashiCorp Terraform",
        web_search_notice_version=PUBLIC_WEB_QUERY_NOTICE_VERSION if confirmed else None,
    )
    with pytest.raises(HTTPException) as error:
        _require_public_chat_web_search_scope(
            body.question, body.web_search_public_data_confirmed, body.web_search_query
        )
    assert error.value.status_code == 422


def test_legacy_replay_hash_omits_only_the_absent_new_fields() -> None:
    old = {"question": "Search the web", "web_search_public_data_confirmed": True}
    original_hash = _chat_turn_request_hash(old, "auto")
    assert (
        original_hash
        == hashlib.sha256(
            json.dumps(
                {**old, "model": "auto"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )
    assert _chat_turn_request_hash({**old, "web_search_query": None}, "auto") == original_hash
    assert (
        _chat_turn_request_hash({**old, "web_search_notice_version": None}, "auto") == original_hash
    )
    first = _chat_turn_request_hash({**old, "web_search_query": "Terraform plan"}, "auto")
    second = _chat_turn_request_hash({**old, "web_search_query": "Terraform apply"}, "auto")
    assert first != second and first != original_hash
    declared = {
        **old,
        "web_search_query": "Terraform plan",
        "web_search_notice_version": PUBLIC_WEB_QUERY_NOTICE_VERSION,
    }
    assert _chat_turn_request_hash({**declared, "web_search_notice_version": None}, "auto") == first
    assert _chat_turn_request_hash(declared, "auto") not in {first, original_hash}
    assert _chat_turn_request_hash({**old, "selection": None}, "auto") != original_hash


@pytest.mark.parametrize("stream", [False, True])
def test_invalid_notice_is_rejected_before_a_charge_or_turn(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    client = _authed(create_app(), email=f"web-notice-{stream}@example.org", org="Notice receipt")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        run = Run(org_id=org_id, question="Terraform", status="completed")
        session.add(run)
        session.flush()
        run_id, public_id = (run.id, run.public_id)
    private_marker = "UNKNOWN_NOTICE_MUST_NOT_REFLECT"
    versions = [
        "",
        private_marker,
        "x" * 65,
        1,
        True,
        [],
        {},
        f" {PUBLIC_WEB_QUERY_NOTICE_VERSION} ",
    ]
    for version in versions:
        response = client.post(
            f"/runs/{public_id}/chat" + ("/stream" if stream else ""),
            json={
                "question": "Search the web",
                "web_search_public_data_confirmed": True,
                "web_search_query": "HashiCorp Terraform plan",
                "web_search_notice_version": version,
                "turn_id": "invalid-notice-test",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "web_search_notice_version"]
        assert "input" not in response.json()["detail"][0]
        assert private_marker not in response.text
    for confirmed, query in [(False, None), (False, "Terraform plan"), (True, None)]:
        response = client.post(
            f"/runs/{public_id}/chat" + ("/stream" if stream else ""),
            json={
                "question": "Search the web",
                "web_search_public_data_confirmed": confirmed,
                "web_search_query": query,
                "web_search_notice_version": PUBLIC_WEB_QUERY_NOTICE_VERSION,
                "turn_id": "orphan-notice-test",
            },
        )
        assert response.status_code == 422
        assert "exact public search terms" in response.text
    with db_session() as session:
        for row_type in (CreditEventRow, ChatMessageRow, ChatTurnRow):
            assert (
                session.scalar(select(func.count(row_type.id)).where(row_type.run_id == run_id))
                == 0
            )
    get_settings.cache_clear()


@pytest.mark.parametrize("stream", [False, True])
def test_generic_legacy_followup_is_rejected_before_a_charge_or_turn(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    client = _authed(create_app(), email=f"web-topic-{stream}@example.org", org="Public topic")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        run = Run(org_id=org_id, question="Terraform", status="completed")
        session.add(run)
        session.flush()
        run_id, public_id = (run.id, run.public_id)
    response = client.post(
        f"/runs/{public_id}/chat" + ("/stream" if stream else ""),
        json={
            "question": "Schau auch mal noch im Internet nach",
            "web_search_public_data_confirmed": True,
            "turn_id": "topic-missing-test",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "web_search_topic_required"
    with db_session() as session:
        for row_type in (CreditEventRow, ChatMessageRow, ChatTurnRow):
            assert (
                session.scalar(select(func.count(row_type.id)).where(row_type.run_id == run_id))
                == 0
            )
    get_settings.cache_clear()
