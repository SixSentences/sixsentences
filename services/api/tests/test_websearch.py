"""Grey-literature web search: client + a pipeline stage separate from academia."""

import json

import httpx
import pytest
from sqlalchemy import select

from sixsentences_server.chat.service import _pool_web_search_runtime
from sixsentences_server.config import Settings
from sixsentences_server.connectors.websearch import (
    CONSERVATIVE_CALL_COST_USD,
    MAX_REQUESTS_PER_DISCOVERY,
    WebSearchCallBudget,
    WebSearchClient,
    WebSearchService,
    WebSource,
    categorize,
    quality_of,
    query_angles,
    scrub_query,
)
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.db import (
    Run,
    RunEvent,
    WebSourceRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.base import BudgetExceededError, BudgetGovernor, TaskType
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.pipeline.run import execute_run

_RESULTS = {
    "search_results": [
        {
            "title": "IaC Report",
            "url": "https://www.gartner.com/x",
            "snippet": "c",
        },
        {
            "title": "Terraform Standard",
            "url": "https://nist.gov/y",
            "snippet": "d",
        },
    ]
}


def test_runtime_requires_verified_processor_terms_before_websearch_egress() -> None:
    assert Settings(openrouter_api_key="configured").websearch_enabled is False
    assert (
        Settings(
            openrouter_api_key="configured",
            websearch_data_processing_confirmed=True,
        ).websearch_enabled
        is True
    )


def test_runtime_prefers_a_dedicated_openrouter_key_and_rejects_legacy_tavily() -> None:
    isolated = Settings(
        openrouter_api_key="shared-openrouter",
        websearch_api_key="dedicated-openrouter",
        websearch_data_processing_confirmed=True,
    )
    assert isolated.websearch_openrouter_api_key == "dedicated-openrouter"
    assert isolated.websearch_enabled is True

    legacy = Settings(
        openrouter_api_key="shared-openrouter",
        websearch_api_key="tvly-obsolete",
        websearch_data_processing_confirmed=True,
    )
    assert legacy.websearch_openrouter_api_key == "shared-openrouter"
    assert legacy.websearch_enabled is True


def test_client_parses_and_derives_domain() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_RESULTS)

    client = WebSearchClient("KEY", http=httpx.Client(transport=httpx.MockTransport(handler)))
    sources = client.search("terraform llm")
    assert len(sources) == 2
    assert sources[0].domain == "gartner.com" and sources[0].score == 1.0
    assert sources[1].domain == "nist.gov"
    assert requests[0].headers["authorization"] == "Bearer KEY"
    assert requests[0].headers["x-openrouter-cache"] == "false"
    payload = json.loads(requests[0].content)
    assert "api_key" not in payload
    assert payload["model"] == "perplexity/sonar"
    assert payload["max_tokens"] == 192
    assert payload["web_search_options"] == {"search_context_size": "low"}
    assert payload["provider"] == {
        "only": ["perplexity"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
        "max_price": {
            "prompt": 1.1,
            "completion": 1.1,
        },
    }
    assert requests[0].url == "https://openrouter.ai/api/v1/chat/completions"


def test_client_parses_standard_openrouter_url_citations() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Generated answer that is deliberately ignored.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "title": "NIST source",
                                        "url": "https://nist.gov/report",
                                        "content": "Public result excerpt",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = WebSearchClient(
        "KEY",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    sources = client.search("secure systems")
    assert [(source.title, source.snippet) for source in sources] == [
        ("NIST source", "Public result excerpt")
    ]


def test_live_catalog_gate_rejects_separate_web_search_price_drift_before_call() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "model_id": "perplexity/sonar",
                            "tag": "perplexity",
                            "status": 0,
                            "supported_parameters": ["web_search_options"],
                            "pricing": {
                                "prompt": "0.000001",
                                "completion": "0.000001",
                                "web_search": "0.006001",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json=_RESULTS)

    client = WebSearchClient(
        "KEY",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        enforce_live_price_gate=True,
    )

    assert client.search("secure systems") == []
    assert client.last_error_code == "price_gate_failed"
    assert [request.method for request in requests] == ["GET"]
    assert client.call_budget.used == 0


def test_live_catalog_gate_allows_current_sonar_tariffs_once_per_shared_scope() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "model_id": "perplexity/sonar",
                            "tag": "perplexity",
                            "status": 0,
                            "supported_parameters": ["web_search_options"],
                            "pricing": {
                                "prompt": "0.000001",
                                "completion": "0.000001",
                                "web_search": "0.005",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json=_RESULTS)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    shared = WebSearchCallBudget(limit=6)
    clients = [
        WebSearchClient(
            "KEY",
            http=http,
            call_budget=shared,
            enforce_live_price_gate=True,
        )
        for _ in range(2)
    ]

    assert clients[0].search("secure systems")
    assert clients[1].search("secure systems standard")
    assert [request.method for request in requests] == ["GET", "POST", "POST"]


def test_documented_annotations_take_precedence_over_compatibility_results() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "title": "Documented source",
                                        "url": "https://nist.gov/documented",
                                        "content": "Documented annotation excerpt",
                                    },
                                }
                            ]
                        }
                    }
                ],
                "search_results": [
                    {
                        "title": "Undocumented compatibility source",
                        "url": "https://example.org/compatibility",
                    }
                ],
            },
        )

    sources = WebSearchClient(
        "KEY",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    ).search("secure systems")

    assert [source.title for source in sources] == ["Documented source"]


def test_client_records_exact_provider_cost_in_central_budget_and_usage_ledger() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                **_RESULTS,
                "usage": {
                    "prompt_tokens": 91,
                    "completion_tokens": 17,
                    "cost": 0.005321,
                },
            },
        )

    pool = LLMPool({}, RoutingConfig(), BudgetGovernor(limit_usd=0.1))
    persisted = []
    pool.on_usage = persisted.append
    with _pool_web_search_runtime(pool, WebSearchCallBudget(limit=6)):
        sources = WebSearchClient(
            "KEY",
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        ).search("secure systems")

    assert sources
    assert pool.budget.spent_usd == pytest.approx(0.005321)
    assert pool.budget.calls == 1
    assert pool.budget.by_task[TaskType.WEB_SEARCH.value] == pytest.approx(0.005321)
    assert len(pool.usage) == len(persisted) == 1
    assert pool.usage[0].task == "web_search"
    assert pool.usage[0].input_tokens == 91
    assert pool.usage[0].output_tokens == 17
    assert pool.usage[0].cost_source == "provider"


def test_client_uses_conservative_cost_when_provider_omits_usage() -> None:
    budget = BudgetGovernor(limit_usd=0.1)
    usage = []
    client = WebSearchClient(
        "KEY",
        budget=budget,
        on_usage=usage.append,
        http=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=_RESULTS))
        ),
    )

    assert client.search("secure systems")
    assert budget.spent_usd == pytest.approx(CONSERVATIVE_CALL_COST_USD)
    assert usage[0].cost_usd == pytest.approx(CONSERVATIVE_CALL_COST_USD)
    assert usage[0].cost_source == "websearch_conservative_fallback"


def test_ambiguous_transport_failure_is_conservatively_accounted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("response lost after request", request=request)

    budget = BudgetGovernor(limit_usd=0.1)
    usage = []
    client = WebSearchClient(
        "KEY",
        budget=budget,
        on_usage=usage.append,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.search("secure systems") == []
    assert client.last_error_code == "connector_failed"
    assert budget.spent_usd == pytest.approx(CONSERVATIVE_CALL_COST_USD)
    assert budget.calls == 1
    assert len(usage) == 1
    assert usage[0].cost_source == "websearch_conservative_fallback"


def test_connect_failure_is_not_recorded_as_provider_spend() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    budget = BudgetGovernor(limit_usd=0.1)
    usage = []
    client = WebSearchClient(
        "KEY",
        budget=budget,
        on_usage=usage.append,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.search("secure systems") == []
    assert budget.calls == 0
    assert usage == []


def test_central_budget_refuses_search_before_provider_call() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json=_RESULTS)

    client = WebSearchClient(
        "KEY",
        budget=BudgetGovernor(limit_usd=CONSERVATIVE_CALL_COST_USD / 2),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(BudgetExceededError):
        client.search("secure systems")
    assert attempts == 0
    assert client.call_budget.used == 0


def test_shared_turn_budget_caps_calls_across_fresh_clients() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"search_results": []})

    shared = WebSearchCallBudget(limit=6)
    clients = [
        WebSearchClient(
            "KEY",
            call_budget=shared,
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        for _ in range(7)
    ]
    for index, client in enumerate(clients):
        client.search(f"safe query {index}")

    assert len(requests) == 6
    assert shared.used == 6
    assert clients[-1].last_error_code == "web_search_call_limit"


def test_client_rejects_non_openrouter_endpoint_without_sending_key() -> None:
    attempts = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(200, json=_RESULTS)

    client = WebSearchClient(
        "KEY",
        url="https://attacker.example/api/v1/chat/completions",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.search("secure systems") == []
    assert attempts["count"] == 0


def test_query_scrubbing_redacts_identifiers_before_egress() -> None:
    assert (
        scrub_query("Terraform study by person@example.org from +49 170 1234567 host 192.168.1.4")
        == "Terraform study by [email] from [phone] host [ip-address]"
    )


def test_query_scrubbing_preserves_public_research_identifiers() -> None:
    query = (
        "DOI 10.1145/1234567.1234568 arXiv:2301.12345v2 CVE-2024-12345 "
        "evidence 2010-2020 ISO 27001 2022"
    )

    assert scrub_query(query) == query


def test_query_scrubbing_redacts_ipv6_without_mistaking_times_for_addresses() -> None:
    assert (
        scrub_query(
            "IPv6 2001:4860:4860::8888, ::1, fe80::1%eth0 and "
            "::ffff:192.0.2.128 observed at 12:30:45"
        )
        == "IPv6 [ip-address], [ip-address], [ip-address] and "
        "[ip-address] observed at 12:30:45"
    )


def test_secret_shaped_query_is_blocked_without_provider_call() -> None:
    attempts = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(200, json=_RESULTS)

    client = WebSearchClient(
        "KEY",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.search("password=correct-horse-battery-staple research") == []
    assert attempts["count"] == 0


def test_client_filters_non_public_and_blocklisted_result_urls() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "search_results": [
                    {"title": "local", "url": "http://127.0.0.1/private"},
                    {"title": "paste", "url": "https://pastebin.com/leak"},
                    {"title": "safe", "url": "https://nist.gov/report"},
                ]
            },
        )

    client = WebSearchClient(
        "KEY",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert [source.title for source in client.search("secure systems")] == ["safe"]


def test_client_and_service_enforce_credit_and_request_caps() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"search_results": []})

    client = WebSearchClient(
        "KEY",
        max_credits=2,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    service = WebSearchService(client)
    service.discover([f"safe research query {index}" for index in range(20)])

    assert len(requests) == 2
    assert all(json.loads(request.content)["provider"]["zdr"] is True for request in requests)
    assert client.credits_used == 2

    fake = _MultiFake()
    WebSearchService(fake).discover([f"query {index}" for index in range(20)])
    assert len(fake.calls) == MAX_REQUESTS_PER_DISCOVERY


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (400, "connector_bad_request"),
        (401, "connector_unauthorized"),
        (403, "connector_forbidden"),
        (429, "connector_rate_limited"),
        (503, "connector_failed"),
    ],
)
def test_client_classifies_http_failures_without_exposing_provider_details(
    status_code: int,
    error_code: str,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": {"message": "sensitive provider detail"}},
        )

    client = WebSearchClient("K", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.search("safe query") == []
    assert client.last_error_code == error_code
    assert "sensitive" not in " ".join(client.failure_codes)


@pytest.mark.parametrize(
    ("error_code", "expected_calls"),
    [
        ("connector_bad_request", 1),
        ("connector_unauthorized", 1),
        ("connector_forbidden", 1),
        ("connector_rate_limited", 3),
        ("connector_failed", 3),
    ],
)
def test_discovery_stops_only_for_terminal_provider_failures(
    error_code: str,
    expected_calls: int,
) -> None:
    class FailedSearcher:
        last_error_code: str | None = None

        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, *, max_results: int = 10) -> list[WebSource]:
            del query, max_results
            self.calls += 1
            self.last_error_code = error_code
            return []

    searcher = FailedSearcher()
    service = WebSearchService(searcher)

    assert service.discover(["first query", "second query", "third query"]) == []
    assert searcher.calls == expected_calls


@pytest.mark.parametrize(
    ("status_code", "error_code", "retryable"),
    [
        (400, "connector_bad_request", False),
        (401, "connector_unauthorized", False),
        (403, "connector_forbidden", False),
        (429, "connector_rate_limited", True),
        (503, "connector_failed", True),
    ],
)
def test_chat_web_search_reports_safe_retry_policy_for_http_failures(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    error_code: str,
    retryable: bool,
) -> None:
    from sixsentences_server.chat.service import _execute_tool
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    failed_client = WebSearchClient(
        "test-key",
        http=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    status_code,
                    json={"error": "sensitive provider detail"},
                )
            )
        ),
    )
    monkeypatch.setattr(
        "sixsentences_server.chat.service.WebSearchClient",
        lambda *_args, **_kwargs: failed_client,
    )

    step, found = _execute_tool("web_search", "safe public query", "verify it")

    assert found == []
    assert step.status == "failed"
    assert step.results[0]["error_code"] == error_code
    assert step.results[0]["retryable"] is retryable
    assert "sensitive provider detail" not in json.dumps(step.results)


def test_client_fails_closed_on_malformed_response_shapes() -> None:
    payloads: list[object] = [
        [],
        {"search_results": {}},
        {
            "search_results": [
                {
                    "title": "unsafe",
                    "url": "http://127.0.0.1/report",
                }
            ]
        },
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payloads.pop(0))

    client = WebSearchClient(
        "K",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.search("safe query one") == []
    assert client.search("safe query two") == []
    assert client.search("safe query three") == []


def test_quality_boost_and_categories() -> None:
    assert quality_of("nist.gov", 0.5) > 0.5  # a .gov authority boost
    assert quality_of("randomblog.com", 0.5) == 0.5  # no boost
    assert categorize("nist.gov") == "standard/gov"
    assert categorize("arxiv.org") == "academic"
    assert categorize("medium.com") == "blog"
    assert categorize("acme.io") == "web"


def test_query_angles_are_complementary() -> None:
    angles = query_angles("terraform llm", ["evaluates generated IaC"])
    # enough angles that a per-request-capped provider can still reach the
    # 50-source grey-literature target after cross-angle dedup
    assert len(angles) == 6 and angles[0] == "terraform llm"
    assert "evaluates generated IaC" in angles
    assert len(set(angles)) == len(angles)  # no duplicate queries


def _src(
    url: str, domain: str, score: float, *, year: int | None = None, title: str = "t"
) -> WebSource:
    return WebSource(title=title, url=url, snippet="", domain=domain, score=score, year=year)


class _MultiFake:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, query: str, *, max_results: int = 10) -> list[WebSource]:
        self.calls.append(query)
        return [
            _src("https://nist.gov/x", "nist.gov", 0.6),
            _src("https://blog.io/y", "blog.io", 0.6),
        ]


def test_service_queries_angles_dedups_and_ranks_by_authority() -> None:
    fake = _MultiFake()
    sources = WebSearchService(fake).discover(["q1", "q2"], limit=10)
    assert len(fake.calls) == 2  # every angle queried
    assert len(sources) == 2  # union deduped by url across angles
    # nist.gov's authority boost lifts it above the higher-raw-score blog
    assert sources[0].domain == "nist.gov" and sources[0].category == "standard/gov"
    assert sources[0].quality > sources[1].quality


def test_service_year_window_excludes_dated_sources() -> None:
    class _Dated:
        def search(self, query: str, *, max_results: int = 10) -> list[WebSource]:
            return [
                _src("https://x/a", "x", 0.5, year=2015, title="old"),
                _src("https://x/b", "x", 0.5, year=2024, title="new"),
                _src("https://x/c", "x", 0.5, year=None, title="unknown"),
            ]

    kept = WebSearchService(_Dated()).discover(["q"], year_from=2020)
    assert [s.title for s in kept] == ["new"]


class _FakeWeb:
    def search(self, query: str, *, max_results: int = 10) -> list[WebSource]:
        return [
            WebSource(
                title="Report",
                url="https://gartner.com/x",
                snippet="s",
                domain="gartner.com",
                score=0.9,
            )
        ]


class _FailedWeb:
    last_error_code: str | None = None

    def search(self, query: str, *, max_results: int = 10) -> list[WebSource]:
        del query, max_results
        self.last_error_code = "connector_failed"
        return []


def test_web_search_stage_stores_grey_literature(corpus: DuckDBCorpus) -> None:
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="w")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="terraform iac", status="pending")
        session.add(run)
        session.flush()
        result = execute_run(
            session,
            run,
            corpus=corpus,
            query_override="learning",
            web_search=True,
            web_searcher=_FakeWeb(),
        )
        # grey literature is a separate stream, not in the academic PRISMA counts
        assert len(result.web_sources) == 1 and result.web_sources[0].domain == "gartner.com"
        rows = session.scalars(select(WebSourceRow).where(WebSourceRow.run_id == run.id)).all()
        assert len(rows) == 1
        events = [e.event for e in session.scalars(select(RunEvent)).all()]
        assert "web_search_done" in events


def test_web_search_stage_records_connector_failure_not_empty_success(
    corpus: DuckDBCorpus,
) -> None:
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="failed-web")
        session.add(project)
        session.flush()
        run = Run(
            org_id=org.id,
            project_id=project.id,
            question="terraform iac",
            status="pending",
        )
        session.add(run)
        session.flush()

        execute_run(
            session,
            run,
            corpus=corpus,
            query_override="learning",
            web_search=True,
            web_searcher=_FailedWeb(),
        )

        events = [event.event for event in session.scalars(select(RunEvent)).all()]
        assert "web_search_failed" in events
        assert "web_search_done" not in events
