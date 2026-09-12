"""MCP-UI resources in chat: charts, clarify forms, and the tool loop."""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sixsentences_server.api.app import create_app
from sixsentences_server.chat.ui import (
    clarify_form,
    data_table,
    verdict_chart,
    works_by_year_chart,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool


def _register(client: TestClient, email: str, org: str) -> str:
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _authed(app: FastAPI, email: str = "ui@example.org", org: str = "UI Lab") -> TestClient:
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {_register(client, email, org)}"
    return client


def test_works_by_year_chart_is_selfcontained_html() -> None:
    works = [WorkRecord(id=f"W{i}", title=f"t{i}", year=2020 + (i % 3)) for i in range(9)]
    resource = works_by_year_chart(works, run_id=7)
    assert resource is not None
    assert resource.uri == "ui://sixsentences/run/7/works-by-year"
    assert resource.mime_type == "text/html"
    assert "2020" in resource.text and "2022" in resource.text
    assert "ui-size-change" in resource.text  # auto-resize contract
    assert "data-prompt" in resource.text  # bars are clickable
    assert "http://" not in resource.text and "https://" not in resource.text


def test_works_by_year_chart_needs_two_years() -> None:
    works = [WorkRecord(id="W1", title="t", year=2021)]
    assert works_by_year_chart(works, run_id=1) is None


def test_works_by_year_chart_uses_distinct_integer_axis_ticks() -> None:
    works = [
        WorkRecord(id="W1", title="a", year=2020),
        WorkRecord(id="W2", title="b", year=2020),
        WorkRecord(id="W3", title="c", year=2021),
    ]
    resource = works_by_year_chart(works, run_id=1)

    assert resource is not None
    ticks = re.findall(r'text-anchor="end">(\d+)</text>', resource.text)
    assert ticks == ["1", "2"]


def test_verdict_chart_counts_render() -> None:
    resource = verdict_chart({"include": 3, "unsure": 2, "exclude": 10, "unscreened": 1}, run_id=4)
    assert "Included" in resource.text and "Excluded" in resource.text
    assert resource.payload()["kind"] == "ui"
    assert resource.payload()["resource"]["mimeType"] == "text/html"


def test_tables_in_one_conversation_have_distinct_stable_resources() -> None:
    first = data_table(
        "Methods",
        ["Paper", "Method"],
        [["[W1] Study", "Interview"]],
        run_id=7,
    )
    repeated = data_table(
        "Methods",
        ["Paper", "Method"],
        [["[W1] Study", "Interview"]],
        run_id=7,
    )
    second = data_table(
        "Outcomes",
        ["Paper", "Outcome"],
        [["[W1] Study", "Improved recall"]],
        run_id=7,
    )

    assert first.uri == repeated.uri
    assert first.uri != second.uri
    assert first.uri.startswith("ui://sixsentences/run/7/table/")


def test_top_cited_and_prisma_funnel_charts() -> None:
    from sixsentences_server.chat.ui import prisma_funnel_chart, top_cited_chart

    works = [
        WorkRecord(id=f"W{i}", title=f"Paper {i}", cited_by_count=i * 10, year=2020)
        for i in range(1, 5)
    ]
    cited = top_cited_chart(works, run_id=3)
    assert cited is not None and "Most cited works" in cited.text
    assert "Paper 4" in cited.text  # the leader renders
    assert top_cited_chart([WorkRecord(id="W1", title="t")], run_id=3) is None

    funnel = prisma_funnel_chart(
        {
            "records_identified": 100,
            "duplicates_removed": 20,
            "records_screened": 80,
            "included": 7,
            "studies_included": 0,
        },
        run_id=3,
    )
    assert funnel is not None and "PRISMA flow" in funnel.text
    assert "Records identified" in funnel.text and ">100<" in funnel.text
    assert prisma_funnel_chart({}, run_id=3) is None  # no counts, no chart


def test_prisma_funnel_rejects_chronologically_impossible_counts() -> None:
    from sixsentences_server.chat.ui import prisma_funnel_chart

    assert (
        prisma_funnel_chart(
            {
                "records_identified": 100,
                "duplicates_removed": 10,
                "records_screened": 95,
                "studies_included": 12,
            },
            run_id=9,
        )
        is None
    )


def test_followups_form_offers_clickable_prompts() -> None:
    from sixsentences_server.chat.ui import followups_form

    resource = followups_form(["Which works matter most?", "What was excluded?"], run_id=5)
    assert 'data-prompt="Which works matter most?"' in resource.text
    assert "Where to next?" in resource.text


def test_chat_suggest_followups_returns_chip_form(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return (
                '{"action": "tool", "tool": "suggest_followups", "reason": "asked", '
                '"questions": ["Which works matter most?", "What did screening exclude?", '
                '"Are there newer papers?"]}'
            )
        return "unused"

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    answer = client.post(
        f"/runs/{run_id}/chat", json={"question": "was soll ich als nächstes fragen?"}
    ).json()
    assert answer["tools_used"] == ["suggest_followups"]
    history = client.get(f"/runs/{run_id}/chat").json()
    assert history[-2]["payload"]["kind"] == "agent_work"
    tool_msg = history[-1]
    assert tool_msg["payload"]["kind"] == "ui"
    assert "Which works matter most?" in tool_msg["payload"]["resource"]["text"]


def test_chat_read_paper_reports_missing_full_text_honestly(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action": "answer"}'
            return '{"action": "tool", "tool": "read_paper", "work_id": "W1", "reason": "deep"}'
        return "The paper introduces self-attention [W1]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    answer = client.post(
        f"/runs/{run_id}/chat", json={"question": "Tell me everything about W1"}
    ).json()
    assert answer["tools_used"] == ["read_paper"]
    history = client.get(f"/runs/{run_id}/chat").json()
    tool_msg = history[-2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["payload"]["tool"] == "read_paper"
    assert tool_msg["payload"]["results"][0]["id"] == "W1"
    assert tool_msg["payload"]["status"] == "failed"
    assert tool_msg["content"] == "Tried to read the paper's full text"


def test_clarify_form_escapes_and_posts_prompt() -> None:
    resource = clarify_form(
        [{"question": "Which <scope>?", "options": ["Cloud & IaC", "On-prem"]}], run_id=9
    )
    assert "Which &lt;scope&gt;?" in resource.text  # escaped
    assert "Cloud &amp; IaC" in resource.text
    assert '"prompt"' in resource.text and "To clarify:" in resource.text


def test_chat_clarify_returns_form_without_answer(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return (
                '{"action": "tool", "tool": "clarify", "reason": "ambiguous", '
                '"questions": [{"question": "Which field?", '
                '"options": ["ML", "Security"]}]}'
            )
        return "unused"

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    answer = client.post(f"/runs/{run_id}/chat", json={"question": "Tell me more"}).json()
    assert answer["tools_used"] == ["clarify"]
    assert answer["answer"] == ""  # the form is the turn's output

    history = client.get(f"/runs/{run_id}/chat").json()
    assert history[-2]["payload"]["kind"] == "agent_work"
    tool_msg = history[-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["payload"]["kind"] == "ui"
    assert tool_msg["payload"]["resource"]["uri"].startswith("ui://sixsentences/run/")
    assert "Which field?" in tool_msg["payload"]["resource"]["text"]


def test_explicit_chart_wish_survives_a_lazy_router(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A German 'Grafik' request renders a chart even when the routing model
    answers with prose or picks nothing (the bug: truncated decision JSON)."""

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            return "Ich würde einfach direkt antworten, ohne Tool."  # no JSON at all
        return "Die Grafik zeigt die Verteilung."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    answer = client.post(
        f"/runs/{run_id}/chat",
        json={"question": "erstelle mal ne grafik zu den screening verdicts"},
    ).json()
    assert answer["tools_used"] == ["show_chart"]
    history = client.get(f"/runs/{run_id}/chat").json()
    tool_msg = history[-2]
    assert tool_msg["role"] == "tool" and tool_msg["payload"]["kind"] == "ui"


def test_decision_json_survives_prose_wrapping() -> None:
    from sixsentences_server.chat.service import _extract_json_object

    assert _extract_json_object('Sure thing!\n{"action": "answer"} Hope that helps.') == {
        "action": "answer"
    }
    assert _extract_json_object('```json\n{"action": "answer"}\n```') == {"action": "answer"}
    assert _extract_json_object("no json here") is None
    assert _extract_json_object('{"action": "tool", "tool": "web_se') is None  # truncated


def test_chat_chart_step_then_answer(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action": "answer"}'
            return (
                '{"action": "tool", "tool": "show_chart", '
                '"chart": "verdicts", "reason": "distribution"}'
            )
        return "The verdicts split as charted."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    answer = client.post(
        f"/runs/{run_id}/chat", json={"question": "Show me the verdict distribution"}
    ).json()
    assert answer["tools_used"] == ["show_chart"]
    assert "charted" in answer["answer"]

    history = client.get(f"/runs/{run_id}/chat").json()
    roles = [m["role"] for m in history]
    assert roles[-3:] == ["user", "tool", "assistant"]
    tool_msg = history[-2]
    assert tool_msg["payload"]["kind"] == "ui"
    assert tool_msg["payload"]["size"] == "wide"
    assert "Screening verdicts" in tool_msg["payload"]["resource"]["text"]


def test_chart_scope_filters_to_included_works(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select

    from sixsentences_server.core.db import (
        Run,
        ScreeningDecisionRow,
        SourceRecordRow,
        WorkRow,
        db_session,
    )

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action": "answer"}'
            return (
                '{"action": "tool", "tool": "show_chart", '
                '"chart": "works_by_year", "scope": "included", '
                '"reason": "the user asked for included studies only"}'
            )
        return "The chart contains only included studies."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "scope demo"}).json()
    run_public_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "screening", "query": "screening"},
    ).json()["id"]

    with db_session() as session:
        run = (
            session.get(Run, run_public_id)
            if isinstance(run_public_id, int)
            else session.scalar(select(Run).where(Run.public_id == run_public_id))
        )
        assert run is not None
        records = [
            ("W-Y2021", 2021, "include"),
            ("W-Y2022", 2022, "include"),
            ("W-Y2018", 2018, "exclude"),
        ]
        for work_id, year, verdict in records:
            session.add(
                WorkRow(
                    id=work_id,
                    title=f"Study from {year}",
                    year=year,
                    payload={"id": work_id, "title": f"Study from {year}", "year": year},
                )
            )
            session.flush()  # Provenance and decisions reference this work.
            session.add(
                SourceRecordRow(
                    org_id=run.org_id,
                    run_id=run.id,
                    work_id=work_id,
                    source="test",
                )
            )
            session.add(
                ScreeningDecisionRow(
                    org_id=run.org_id,
                    run_id=run.id,
                    work_id=work_id,
                    reviewer="test",
                    verdict=verdict,
                    reason="fixture",
                )
            )

    answer = client.post(
        f"/runs/{run_public_id}/chat",
        json={"question": "Chart the included studies by publication year"},
    ).json()
    assert answer["tools_used"] == ["show_chart"]
    history = client.get(f"/runs/{run_public_id}/chat").json()
    tool_msg = history[-2]
    result = tool_msg["payload"]["results"][0]
    assert result["scope"] == "included"
    assert result["works"] == 2
    assert result["by_year"] == {"2021": 1, "2022": 1}
    html = tool_msg["payload"]["resource"]["text"]
    assert "Included works by publication year" in html
    assert "2018" not in html


def test_markdown_tables_become_cards(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Markdown table in the answer leaves the prose and returns as an
    interactive card whose source chips reuse the thread's numbering."""
    table_answer = (
        "Both tools are covered by the sources [W1] [W2].\n\n"
        "Terraform vs. CloudFormation, the comparison\n\n"
        "---\n\n"
        "| Criterion | Terraform | CloudFormation |\n"
        "|---|---|---|\n"
        "| Type | Open source [W1] | AWS native [W2] |\n"
        "| State | State files | Managed by AWS |\n\n"
        "---\n\n"
        "The table rests on the comparison study [W1]."
    )

    def handler(_model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return table_answer

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="tables@example.org", org="Tables Lab")
    project = client.post("/projects", json={"name": "tab"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    answer = client.post(f"/runs/{run_id}/chat", json={"question": "Compare the two tools"}).json()
    assert "|" not in answer["answer"]  # the raw pipes are gone from prose
    assert "comparison study" in answer["answer"]  # surrounding prose stays

    history = client.get(f"/runs/{run_id}/chat").json()
    agent_updates = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "agent_work"
    ]
    assert agent_updates
    output_history = [
        message for message in history if message["payload"].get("kind") != "agent_work"
    ]
    assert [m["role"] for m in output_history][-3:] == ["user", "assistant", "tool"]
    card = output_history[-1]
    assert card["payload"]["tool"] == "make_table"
    assert card["payload"]["kind"] == "ui"
    assert card["payload"]["table"]["columns"] == [
        "Criterion",
        "Terraform",
        "CloudFormation",
    ]
    assert card["payload"]["table"]["rows"][0][0] == "Type"
    assert card["payload"]["results"][0]["title"] == (
        "Terraform vs. CloudFormation, the comparison"
    )
    html_text = card["payload"]["resource"]["text"]
    assert "Terraform vs. CloudFormation, the comparison" in html_text
    assert "CloudFormation" in html_text and "State files" in html_text
    assert "wchip" in html_text  # cell citations render as numbered chips
    assert ">1<" in html_text  # W1 keeps the thread's number 1


def test_chat_tables_can_be_edited_and_restored(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    table_answer = (
        "Evidence matrix\n\n"
        "| Paper | Method |\n"
        "|---|---|\n"
        "| Study A | Interview |\n"
        "| Study B | Survey |"
    )

    def handler(_model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return table_answer

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="edit-tables@example.org", org="Edit Tables")
    project = client.post("/projects", json={"name": "tables"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]
    response = client.post(f"/runs/{run_id}/chat", json={"question": "Build a table"})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Table"
    card = next(
        item
        for item in client.get(f"/runs/{run_id}/chat").json()
        if item["role"] == "tool" and item["payload"].get("tool") == "make_table"
    )

    edited = client.patch(
        f"/runs/{run_id}/chat/{card['id']}/table",
        json={
            "title": "Reviewed evidence matrix",
            "columns": ["Paper", "Method", "Decision"],
            "rows": [["Study A", "Interview", "Include"]],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 1
    assert edited.json()["original"]["rows"] == [
        ["Study A", "Interview"],
        ["Study B", "Survey"],
    ]
    persisted = next(
        item for item in client.get(f"/runs/{run_id}/chat").json() if item["id"] == card["id"]
    )
    assert persisted["payload"]["table"]["title"] == "Reviewed evidence matrix"
    assert persisted["payload"]["results"][0]["columns"] == [
        "Paper",
        "Method",
        "Decision",
    ]

    restored = client.post(f"/runs/{run_id}/chat/{card['id']}/table/reset")
    assert restored.status_code == 200, restored.text
    assert restored.json()["title"] == "Evidence matrix"
    assert restored.json()["rows"][1] == ["Study B", "Survey"]
    assert restored.json()["revision"] == 2


def test_chat_table_edits_validate_shape_and_tenant(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(
            lambda _model, prompt: (
                '{"action": "answer"}'
                if "User request:" in prompt
                else ("Results\n\n| Paper | Result |\n|---|---|\n| A | B |\n| C | D |")
            )
        ),
    )
    client = _authed(create_app(), email="shape@example.org", org="Shape Tables")
    project = client.post("/projects", json={"name": "tables"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]
    response = client.post(f"/runs/{run_id}/chat", json={"question": "Build a table"})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Table"
    card = next(
        item
        for item in client.get(f"/runs/{run_id}/chat").json()
        if item["role"] == "tool" and item["payload"].get("tool") == "make_table"
    )

    ragged = client.patch(
        f"/runs/{run_id}/chat/{card['id']}/table",
        json={"title": "Bad", "columns": ["A", "B"], "rows": [["only one"]]},
    )
    assert ragged.status_code == 422
    other = _authed(create_app(), email="other-shape@example.org", org="Other Shape")
    assert (
        other.patch(
            f"/runs/{run_id}/chat/{card['id']}/table",
            json={"title": "No", "columns": ["A"], "rows": []},
        ).status_code
        == 404
    )


def test_multiple_markdown_tables_become_separate_cards(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    table_answer = (
        "Two views help here.\n\n"
        "Methods\n\n"
        "| Paper | Method |\n"
        "|---|---|\n"
        "| Study A [W1] | Interview |\n\n"
        "Outcomes\n\n"
        "| Paper | Outcome |\n"
        "|---|---|\n"
        "| Study A [W1] | Faster completion |\n"
    )

    def handler(_model: str, prompt: str) -> str:
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return table_answer

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="many-tables@example.org", org="Many Tables")
    project = client.post("/projects", json={"name": "tab"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    client.post(f"/runs/{run_id}/chat", json={"question": "Show both views"})
    history = client.get(f"/runs/{run_id}/chat").json()
    cards = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("tool") == "make_table"
    ]

    assert [card["payload"]["table"]["title"] for card in cards] == [
        "Methods",
        "Outcomes",
    ]
    assert cards[0]["payload"]["resource"]["uri"] != cards[1]["payload"]["resource"]["uri"]


def test_capability_questions_skip_the_tools(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'What can you do' is a product question: no tool routing, no search —
    the answer comes straight from the self-knowledge section."""
    saw_decision = {"any": False}

    def handler(_model: str, prompt: str) -> str:
        if "User request:" in prompt:
            saw_decision["any"] = True
            return '{"action": "answer"}'
        return "I run audited searches, screen papers, write and draw figures."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="cap@example.org", org="Cap Lab")
    project = client.post("/projects", json={"name": "cap"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer"},
    ).json()["id"]

    answer = client.post(f"/runs/{run_id}/chat", json={"question": "Was kannst du alles?"}).json()
    assert saw_decision["any"] is False  # the tool router never ran
    assert answer["tools_used"] == []
    assert "audited searches" in answer["answer"]
