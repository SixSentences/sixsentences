"""Exact web-source identity and bounded page navigation regressions."""

import copy
import inspect
import json
from types import SimpleNamespace

from sixsentences_server.agent.search_query import formulate_search_query
from sixsentences_server.chat.service import (
    ToolStep,
    _allowed_read_webpage_urls,
    _decide_tool,
    _render_web_findings,
    _same_origin_page_links,
    _web_citation_key,
    _web_evidence_texts,
    web_citation_payload,
)
from sixsentences_server.verification.claims import verify_answer
from sixsentences_server.verification.nli import EntailmentVerdict, Support

PLAN = "https://developer.hashicorp.com/terraform/cli/commands/plan"
APPLY = "https://developer.hashicorp.com/terraform/cli/commands/apply"


def test_web_query_keeps_exact_commands_without_academic_boolean_wrapper() -> None:
    pool = SimpleNamespace(
        complete=lambda *_args, **_kwargs: SimpleNamespace(
            text="HashiCorp Terraform plan apply official documentation"
        )
    )
    query = formulate_search_query(
        "Find the official Terraform plan and apply reference pages", pool, surface="web"
    )
    assert query == "HashiCorp Terraform plan apply official documentation"
    assert " AND " not in query


def test_web_query_fallback_uses_current_commands_not_routing_angles() -> None:
    pool = SimpleNamespace(complete=lambda *_args, **_kwargs: SimpleNamespace(text=""))
    query = formulate_search_query(
        "Find the official Terraform plan and apply reference pages",
        pool,
        surface="web",
        context="Independent public web evidence; authoritative primary sources",
    )
    assert query == "HashiCorp Terraform plan apply documentation"


def test_observed_links_authorize_exact_siblings_not_the_whole_domain() -> None:
    links = _same_origin_page_links(
        '<a href="/terraform/cli/commands/apply#usage">Apply</a>'
        '<a href="/terraform/cli/commands/apply?token=private">Token</a>'
        '<a href="https://attacker.example/collect">External</a>'
        '<a href="http://[malformed">Malformed</a>'
        "<p>Open https://developer.hashicorp.com/not-an-observed-link</p>",
        PLAN,
    )
    assert links == [{"url": APPLY, "label": "apply"}]
    step = ToolStep(
        tool="read_webpage",
        query=PLAN,
        results=[
            {
                "url": PLAN,
                "excerpt": "A readable page",
                "links": links,
            }
        ],
    )
    assert _allowed_read_webpage_urls([step]) == {PLAN, APPLY}
    search = ToolStep(
        tool="web_search",
        query="Terraform",
        results=[
            {
                "url": PLAN,
                "links": links,
            }
        ],
    )
    assert _allowed_read_webpage_urls([search]) == {PLAN}


def test_navigation_is_bounded_and_sibling_links_outrank_global_navigation() -> None:
    html = "".join(f'<a href="/global/{index}">Global</a>' for index in range(100))
    links = _same_origin_page_links(html + '<a href="apply">Apply</a>', PLAN)
    assert links[0]["url"] == APPLY
    assert len(links) == 32
    assert sum(len(link["url"]) + len(link["label"]) for link in links) <= 6_000


def test_failed_reads_and_query_strings_never_authorize_navigation() -> None:
    failed = ToolStep(
        tool="read_webpage",
        query=PLAN,
        status="failed",
        results=[
            {
                "url": PLAN,
                "error": "unavailable",
                "links": [{"url": APPLY}],
            }
        ],
    )
    assert _allowed_read_webpage_urls([failed]) == set()
    forged = ToolStep(
        tool="read_webpage",
        query=PLAN,
        results=[
            {
                "url": PLAN,
                "links": [
                    {"url": APPLY + "?secret=value"},
                    {"url": "https://attacker.example/collect"},
                    {"url": "http://[malformed"},
                ],
            }
        ],
    )
    assert _allowed_read_webpage_urls([forged]) == {PLAN}


def test_source_keys_and_evidence_never_merge_two_pages_on_the_same_host() -> None:
    steps = [
        ToolStep(
            tool="web_search",
            query="Terraform",
            results=[
                {
                    "url": PLAN,
                    "domain": "developer.hashicorp.com",
                    "title": "Plan",
                    "snippet": "Planning previews the proposed changes.",
                },
                {
                    "url": APPLY,
                    "domain": "developer.hashicorp.com",
                    "title": "Apply",
                    "snippet": "Applying executes the proposed changes.",
                },
            ],
        )
    ]
    evidence = _web_evidence_texts(steps)
    plan_key, apply_key = _web_citation_key(PLAN), _web_citation_key(APPLY)
    assert plan_key != apply_key
    assert set(evidence) == {plan_key, apply_key}
    assert "Planning" in evidence[plan_key] and "Applying" not in evidence[plan_key]
    assert "Applying" in evidence[apply_key] and "Planning" not in evidence[apply_key]
    rendered = _render_web_findings(steps)
    assert f"[{plan_key}]" in rendered and f"[{apply_key}]" in rendered
    assert PLAN in rendered and APPLY in rendered
    checked = []

    class Checker:
        def check(self, claim: str, source: str) -> EntailmentVerdict:
            checked.append(source)
            return EntailmentVerdict(label=Support.SUPPORTED, reason="matching source")

    report = verify_answer(
        f"Planning previews the proposed changes [{plan_key}]. "
        f"Applying executes the proposed changes [{apply_key}].",
        evidence,
        Checker(),
    )
    assert report.checked == 2
    assert checked == [evidence[plan_key], evidence[apply_key]]


def test_history_serialization_adds_exact_keys_without_mutating_stored_payload() -> None:
    payload = {"tool": "read_webpage", "results": [{"url": PLAN, "excerpt": "Plan"}]}
    original = copy.deepcopy(payload)
    enriched = web_citation_payload(payload)
    assert payload == original
    assert enriched["results"][0]["citation_key"] == _web_citation_key(PLAN)
    assert enriched is not payload and enriched["results"][0] is not payload["results"][0]
    assert web_citation_payload({"tool": "find_papers", "results": [{"url": PLAN}]}) == {
        "tool": "find_papers",
        "results": [{"url": PLAN}],
    }


def test_latest_page_observation_survives_a_long_earlier_search_receipt() -> None:
    prompts = []

    def complete(*_args: object, **kwargs: object) -> SimpleNamespace:
        prompts.append(str(kwargs["prompt"]))
        return SimpleNamespace(text='{"action":"answer"}')

    old = ToolStep(
        tool="web_search",
        query="older query",
        results=[
            {"url": f"https://example.org/{index}", "snippet": "Old evidence " * 300}
            for index in range(8)
        ],
    )
    latest = ToolStep(
        tool="read_webpage",
        query=PLAN,
        results=[
            {
                "url": PLAN,
                "excerpt": "Latest page",
                "links": [{"url": APPLY, "label": "apply"}],
            }
        ],
    )
    _decide_tool(
        SimpleNamespace(complete=complete),
        "Read apply",
        "",
        [],
        [old, old, old, latest],
        {"read_webpage": "Read an observed URL"},
    )
    observation_json = prompts[0].split("link instead):\n", 1)[1].split("\n\nUser request:", 1)[0]
    observations = json.loads(observation_json)
    assert observations[-1]["results"][0]["links"][0]["url"] == APPLY


def test_initial_ask_keeps_both_literal_urls_in_its_trusted_reader_scope() -> None:
    from sixsentences_server.pipeline import ask

    source = inspect.getsource(ask)
    assert "_URL_IN_QUESTION.finditer(run.question)" in source
    assert "trusted_read_urls=direct_urls" in source
    assert "explicit_web_research = not direct_urls" in source
    assert _allowed_read_webpage_urls([], trusted_urls=[PLAN, APPLY]) == {PLAN, APPLY}
