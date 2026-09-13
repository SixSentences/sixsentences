"""Ask mode, agentic chat tools, and run reports (API level)."""

import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sixsentences_server.agent.search_query import query_requires_formulation
from sixsentences_server.api.app import create_app
from sixsentences_server.chat.service import (
    _SAVE_PAPER_ASK,
    ToolStep,
    _arxiv_works_from_web_step,
    _execute_tool,
    _extension_allowed,
    _latest_reader_work_id,
    _library_inventory_request,
    _paper_discovery_next_decision,
    _paper_discovery_request_context,
    _read_paper_step,
    _remove_false_tool_incapacity,
    _requested_library_result_count,
    _research_tool_call_limits,
    _strip_internal_tool_syntax,
    _tool_call_limits,
    explicit_paper_discovery_request,
    paper_discovery_constraints,
    paper_discovery_followup_query,
    paper_discovery_has_clear_match,
    paper_discovery_satisfying_works,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.pipeline.ask import (
    AskPlan,
    _align_plan_with_workspace_outcome,
    _heuristic_plan,
    _library_paper_request,
    _lock_ask_for_finalization,
    _persist_ask_sources,
    _quick_search,
    _reader_target,
    _reconcile_primary_preprint,
    _requested_comparison_count,
    _requested_source_count,
    _search_query,
    _stream_ask_completion,
    _web_search_query,
)


def test_cancelled_quick_answer_cannot_finalize_a_stale_worker() -> None:
    """A late inline worker must not overwrite a committed Stop request."""
    from sixsentences_server.core.db import Org, Run, db_session
    from sixsentences_server.llm.base import LLMCancelledError

    with db_session() as session:
        org = Org(name=f"Quick Answer Cancellation {uuid4().hex}")
        session.add(org)
        session.flush()
        run = Run(
            org_id=org.id,
            question="Explain retrieval augmented generation",
            status="running",
            config={"mode": "ask"},
        )
        session.add(run)
        session.flush()
        run_id = run.id
    with db_session() as cancellation:
        persisted = cancellation.get(Run, run_id)
        assert persisted is not None
        persisted.status = "cancelled"
    with db_session() as stale_worker:
        stale = stale_worker.get(Run, run_id)
        assert stale is not None
        with pytest.raises(LLMCancelledError, match="before finalization"):
            _lock_ask_for_finalization(stale_worker, stale)
    with db_session() as verification:
        persisted = verification.get(Run, run_id)
        assert persisted is not None
        assert persisted.status == "cancelled"


def test_final_answer_removes_false_browsing_incapacity_and_future_preamble() -> None:
    answer = "I'll search for additional authoritative sources. I cannot perform live web searches in this environment. The available studies report heterogeneous sensitivity."
    assert (
        _remove_false_tool_incapacity(answer, language="en")
        == "I am using the sources already available in this conversation for this answer. The available studies report heterogeneous sensitivity."
    )


def test_final_answer_localizes_false_browsing_incapacity_replacement() -> None:
    answer = "Ich kann hier nicht im Internet suchen. Die vorhandenen Quellen reichen aus."
    assert (
        _remove_false_tool_incapacity(answer, language="de")
        == "Für diese Antwort nutze ich die bereits in diesem Gespräch verfügbaren Quellen. Die vorhandenen Quellen reichen aus."
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            "The paper is open above. Unfortunately, I cannot open additional papers in a reader panel as that functionality is not available in this environment. The cited passages support the explanation.",
            "The paper is open above. The cited passages support the explanation.",
        ),
        (
            "Das Paper ist geöffnet. Ich kann in dieser Umgebung keine weiteren PDFs im Reader anzeigen, weil die Funktion nicht verfügbar ist. Die markierten Stellen tragen die Erklärung.",
            "Das Paper ist geöffnet. Die markierten Stellen tragen die Erklärung.",
        ),
    ],
)
def test_final_answer_removes_blanket_reader_incapacity(answer: str, expected: str) -> None:
    assert _remove_false_tool_incapacity(answer, language="de") == expected


def test_final_answer_preserves_specific_open_access_failure() -> None:
    answer = "I could not open The Example Study because no open-access PDF was found. Its abstract is still available."
    assert _remove_false_tool_incapacity(answer, language="en") == answer


def test_reader_target_prefers_half_remembered_named_paper() -> None:
    requested = WorkRecord(
        id="W-ATTENTION",
        title="Attention Is All You Need",
        year=2017,
        arxiv_id="1706.03762",
        pdf_url="https://arxiv.org/pdf/1706.03762",
    )
    related = WorkRecord(
        id="W-INFORMER",
        title="Informer: Beyond Efficient Transformer for Long Sequence Time-Series Forecasting",
        year=2021,
        arxiv_id="2012.07436",
        pdf_url="https://arxiv.org/pdf/2012.07436",
        abstract="Informer is a Transformer model using attention mechanisms while avoiding recurrent neural networks for time-series forecasting.",
    )
    picked = _reader_target(
        "hab von so nem attention paper gehört, transformer ganz ohne rnn glaub. findest du mir bitte das orginal und öffne es rechts",
        [related, requested],
    )
    assert picked is requested


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


def _grant_web_search_access(client: TestClient) -> None:
    """Grant the paid discovery capability only to positive web fixtures."""
    from sixsentences_server.core.db import Org, db_session

    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        org.plan = "community"


def _handler_answering(text: str):
    """Mock LLM: valid entailment JSON for claim checks, else the given text."""

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        return text

    return handler


def test_interrupted_stream_records_one_aborted_attempt() -> None:

    class Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def emit(self, _stage, event: str, payload=None) -> None:
            self.events.append((event, payload or {}))

    class InterruptedPool:
        def complete(self, _task, *, on_delta, **_kwargs):
            on_delta("A visible partial answer " * 4)
            raise ProviderError("mock: stream interrupted after output began")

    recorder = Recorder()
    with pytest.raises(ProviderError, match="stream interrupted"):
        _stream_ask_completion(
            InterruptedPool(), recorder, system="system", prompt="prompt", max_tokens=100, sources=1
        )
    assert [event for event, _payload in recorder.events] == [
        "ask_answer_started",
        "ask_answer_aborted",
    ]
    attempt_ids = {payload["attempt_id"] for _event, payload in recorder.events}
    assert len(attempt_ids) == 1


def test_interrupted_stream_restarts_with_a_distinct_fallback_attempt() -> None:

    class Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def emit(self, _stage, event: str, payload=None) -> None:
            self.events.append((event, payload or {}))

    class FallbackPool:
        def complete(self, _task, *, on_delta, on_stream_reset, **_kwargs):
            on_delta("discarded draft " * 6)
            on_stream_reset()
            on_delta("Grounded fallback answer.")
            return SimpleNamespace(text="Grounded fallback answer.", reasoning=None)

    recorder = Recorder()
    response = _stream_ask_completion(
        FallbackPool(), recorder, system="system", prompt="prompt", max_tokens=100, sources=1
    )
    assert response.text == "Grounded fallback answer."
    events = [event for event, _payload in recorder.events]
    assert events == [
        "ask_answer_started",
        "ask_answer_aborted",
        "ask_answer_started",
        "ask_answer_delta",
        "ask_answer_finalizing",
    ]
    first_attempt = recorder.events[0][1]["attempt_id"]
    second_attempt = recorder.events[2][1]["attempt_id"]
    assert first_attempt != second_attempt
    assert recorder.events[1][1]["attempt_id"] == first_attempt
    assert recorder.events[3][1]["attempt_id"] == second_attempt
    assert "discarded draft" not in str(recorder.events)


def test_quick_search_prefers_seminal_source_for_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        WorkRecord(
            id="W1199IEW",
            title="Transformer Architecture and Attention Mechanisms: A Review",
            abstract="Transformer architecture and attention mechanisms.",
            year=2023,
            cited_by_count=200,
            work_type="article",
        ),
        WorkRecord(
            id="W-PRIMARY",
            title="Attention Is All You Need",
            abstract="A new network architecture based solely on attention mechanisms.",
            year=2017,
            cited_by_count=6000,
            work_type="conference-paper",
        ),
        WorkRecord(id="W-3", title="Transformer Applications", cited_by_count=100),
        WorkRecord(id="W-4", title="Efficient Attention", cited_by_count=80),
        WorkRecord(id="W-5", title="Transformer Survey", cited_by_count=50),
    ]

    class FakeOpenAlex:
        def __init__(self, **kwargs) -> None:
            pass

        def search(self, query: str, *, limit: int, **_kwargs) -> list[WorkRecord]:
            return candidates[:limit]

    monkeypatch.setattr("sixsentences_server.pipeline.ask.OpenAlexClient", FakeOpenAlex)
    works, source = _quick_search(
        "What is the Transformer architecture?",
        "Transformer architecture attention mechanism",
        limit=5,
    )
    assert source == "openalex-live"
    assert works[0].id == "W-PRIMARY"


def test_quick_search_does_not_accept_a_longer_title_as_the_named_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []
    misleading = [
        WorkRecord(
            id=f"W-MISLEADING-{index}",
            title="Attention Is All You Need: utilizing attention in AI-enabled drug discovery"
            if index == 0
            else f"Unrelated transformer application {index}",
            year=2024,
        )
        for index in range(5)
    ]
    original = WorkRecord(
        id="W-ORIGINAL", title="Attention Is All You Need", year=2017, cited_by_count=100000
    )

    class FakeOpenAlex:
        def __init__(self, **kwargs) -> None:
            pass

        def search(self, query: str, *, limit: int, **_kwargs) -> list[WorkRecord]:
            queries.append(query)
            return [original] if query == "Attention Is All You Need" else misleading[:limit]

    monkeypatch.setattr("sixsentences_server.pipeline.ask.OpenAlexClient", FakeOpenAlex)
    works, source = _quick_search(
        "Find the original attention paper without recurrent networks",
        '"Attention Is All You Need" transformer Vaswani 2017',
        limit=5,
    )
    assert source == "openalex-live"
    assert queries == [
        '"Attention Is All You Need" transformer Vaswani 2017',
        "Attention Is All You Need",
    ]
    assert works[0].id == "W-ORIGINAL"


def test_quick_search_never_uses_raw_user_sentence_as_top_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeOpenAlex:
        def __init__(self, **kwargs) -> None:
            pass

        def search(self, query: str, *, limit: int, **_kwargs) -> list[WorkRecord]:
            calls.append(query)
            return [WorkRecord(id=f"W{len(calls)}", title=query)]

    monkeypatch.setattr("sixsentences_server.pipeline.ask.OpenAlexClient", FakeOpenAlex)
    raw_request = "Kannst du bidde mal im Intenet nach Terraform IaC Sicherheit suchen???"
    _quick_search(raw_request, '("infrastructure as code" OR Terraform) AND security', limit=8)
    assert calls == [
        '("infrastructure as code" OR Terraform) AND security',
        "infrastructure as code",
    ]
    assert raw_request not in calls


def test_search_query_writers_optimize_academic_and_web_intent() -> None:
    prompts: list[str] = []

    def handler(model: str, prompt: str) -> str:
        prompts.append(prompt)
        if "Intenet" in prompt and len(prompts) == 1:
            return '("infrastructure as code" OR Terraform) AND security'
        return "Terraform security official guidance 2026"

    pool = mock_pool(handler)
    request = "Kannst du bidde mal im Intenet nach Terraform Security suchen?"
    assert _search_query(request, pool) == '("infrastructure as code" OR Terraform) AND security'
    web_query = _web_search_query(request, pool)
    assert web_query == "HashiCorp Terraform security official guidance 2026"
    assert all("Research request:" in prompt for prompt in prompts)


def test_search_query_writer_fallback_extracts_topics_instead_of_raw_request() -> None:

    class BrokenPool:
        def complete(self, *args, **kwargs):
            raise RuntimeError("planner unavailable")

    request = "Kannst du bitte online nach Terraform IaC Sicherheit suchen?"
    query = _web_search_query(request, BrokenPool())
    assert query != request
    assert "bitte" not in query.casefold()
    assert "terraform" in query.casefold() or "iac" in query.casefold()


def test_search_query_writer_never_turns_hashicorp_terraform_into_planetary_terraforming() -> None:
    pool = mock_pool(
        lambda _model, _prompt: '"terraforming" AND "artificial intelligence" AND benchmark'
    )
    request = "Zeig mir ein paar Paper zu Terraform und KI und den gängigen Benchmark."
    query = _search_query(request, pool)
    assert "terraforming" not in query.casefold()
    assert "infrastructure as code" in query.casefold()
    assert "benchmark" in query.casefold()


@pytest.mark.parametrize(
    "question",
    [
        "Gibt es dazu ein Paper?",
        "Was ist Terraform im IaC Bereich, gibts da nicht auch ein Ppaer?",
        "Find me a research paper about Terraform infrastructure as code.",
        "Suche bitte eine aktuelle Studie hierzu.",
        "ich hab von sonem attention paper gehört das mit transformer ohne rnn glaub. findest du bitte das orginal und zeig es rechts",
        "vergleich es nur mit BERT und GPT 3, auf deutsch und mit belegen",
    ],
)
def test_explicit_paper_discovery_handles_natural_language_and_typos(question: str) -> None:
    assert explicit_paper_discovery_request(question)


def test_multi_paper_constraints_are_extracted_from_novice_german() -> None:
    question = "Suche bitte drei aktuelle Primärarbeiten ab 2024 zu effizienter Attention, lies die Primärquellen und vergleich nur belegte Laufzeitwerte."
    constraints = paper_discovery_constraints(question)
    assert constraints.requested_count == 3
    assert constraints.minimum_year == 2024
    assert constraints.primary_only is True
    assert constraints.read_sources is True
    assert explicit_paper_discovery_request(question)


def test_paper_count_after_object_and_recent_intent_are_preserved() -> None:
    constraints = paper_discovery_constraints(
        "Such nach mehr Papern, irgendwie 10 Stück, und mach eine Tabelle."
    )
    recent = paper_discovery_constraints(
        "Zeig mir ein aktuelles Paper zu soziotechnischen Systemen."
    )
    assert constraints.requested_count == 10
    assert recent.minimum_year == 2022


@pytest.mark.parametrize(
    "question",
    [
        "Kannst du mir mal ein paar Paper zu Terraform und KI zeigen?",
        "Show me several papers about AI-assisted infrastructure as code.",
        "Gib mir einige aktuelle Studien dazu.",
    ],
)
def test_vague_multi_paper_request_gets_a_useful_default_set(question: str) -> None:
    assert paper_discovery_constraints(question).requested_count == 5


def test_deeper_search_carries_topic_and_previous_count_constraint() -> None:
    current = "Mach nochmal eine tiefere Scholar Suche."
    refinement = "Okay such nach mehr Papern iwie 10 Stück und mach ne Tabelle."
    initial = "Zeig mir ein aktuelles Paper im Bereich Sozitehcnische Systeme und markiere die wichtigsten Stellen."
    session = SimpleNamespace(
        scalars=lambda _query: SimpleNamespace(all=lambda: [current, refinement, initial]),
        get=lambda _model, _run_id: None,
    )
    context = _paper_discovery_request_context(session, 43, current)
    constraints = paper_discovery_constraints(context)
    assert initial in context
    assert refinement in context
    assert context.endswith(f"Current authoritative request: {current}")
    assert constraints.requested_count == 10
    assert constraints.minimum_year == 2022


def test_more_papers_followup_keeps_the_original_scientific_topic() -> None:
    current = "Okay such nach mehr Papern iwie 10 Stück und mach ne Tabelle."
    initial = "Zeig mir ein aktuelles Paper im Bereich Sozitehcnische Systeme und markiere die wichtigsten Stellen."
    session = SimpleNamespace(
        scalars=lambda _query: SimpleNamespace(all=lambda: [current, initial]),
        get=lambda _model, _run_id: None,
    )
    context = _paper_discovery_request_context(session, 44, current)
    assert initial in context
    assert context.endswith(f"Current authoritative request: {current}")
    assert paper_discovery_constraints(context).requested_count == 10


def test_misspelled_sociotechnical_topic_rejects_unrelated_existing_pdf() -> None:
    question = "Zeig mir ein aktuelles Paper im Bereich Sozitehcnische Systeme und markiere die wichtigsten Stellen."
    works = [
        WorkRecord(
            id="W-SOCIO",
            title="A Sociotechnical Systems Approach to Responsible AI",
            abstract="We study the joint design of social and technical systems.",
            year=2025,
        ),
        WorkRecord(
            id="W-CAMERA",
            title="Radiation Resistant Camera System for Plasma Diagnostics",
            abstract="A camera for radiation-heavy fusion experiments.",
            year=2025,
        ),
    ]
    assert [work.id for work in paper_discovery_satisfying_works(question, works)] == ["W-SOCIO"]


def test_named_original_paper_is_an_exact_author_year_discovery() -> None:
    question = "nee halt, zeig mir zuerst das originale paper von lewis 2020 rechts und markier die wichtigsten stellen"
    constraints = paper_discovery_constraints(question)
    assert explicit_paper_discovery_request(question)
    assert constraints.foundational_only is True
    assert constraints.target_author == "lewis"
    assert constraints.target_year == 2020


def test_named_original_paper_gate_rejects_a_related_reader_candidate() -> None:
    question = "Zeig mir das originale Paper von Lewis 2020 rechts."
    works = [
        WorkRecord(
            id="W-RAG-FAILURES",
            title="Seven Failure Points When Engineering a Retrieval Augmented Generation System",
            abstract="A later RAG engineering paper.",
            year=2024,
            authors=["Andreas Madsen"],
        ),
        WorkRecord(
            id="W-RAG-ORIGINAL",
            title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
            abstract="We introduce retrieval-augmented generation.",
            year=2020,
            authors=["Patrick Lewis", "Ethan Perez"],
        ),
    ]
    assert [work.id for work in paper_discovery_satisfying_works(question, works)] == [
        "W-RAG-ORIGINAL"
    ]


def test_novice_correction_carries_the_prior_topic_without_reusing_itself() -> None:
    question = "nee halt, zeig mir zuerst das originale paper von lewis 2020 rechts und markier die wichtigsten stellen"
    prior = "ich brauch nen gründlichen überblick zu RAG, such bitte wissenschaftliche quellen und erklär mir was das eig genau macht"
    session = SimpleNamespace(
        scalars=lambda _query: SimpleNamespace(all=lambda: [question, prior]),
        get=lambda _model, _run_id: None,
    )
    context = _paper_discovery_request_context(session, 41, question)
    assert context.count(question) == 1
    assert prior in context
    assert context.endswith(f"Current authoritative request: {question}")


def test_novice_correction_falls_back_to_the_initial_run_question() -> None:
    question = "nee halt, zeig mir das originale paper von lewis 2020 rechts"
    prior = "ich brauch nen überblick zu RAG und wissenschaftliche quellen"
    session = SimpleNamespace(
        scalars=lambda _query: SimpleNamespace(all=lambda: [question]),
        get=lambda _model, _run_id: SimpleNamespace(question=prior),
    )
    context = _paper_discovery_request_context(session, 42, question)
    assert prior in context
    assert context.count(question) == 1


def test_named_original_first_query_is_exact_and_excludes_ui_instructions() -> None:
    question = "Prior topic context only: Ich brauche einen Überblick zu RAG.\nCurrent authoritative request: nee halt, zeig mir zuerst das originale Paper von Lewis 2020 rechts und markier die wichtigsten Stellen"
    pool = mock_pool(lambda _model, _prompt: '"Lewis 2020" "rechts" paper')
    decision = _paper_discovery_next_decision(
        question, [], [], pool, {"find_papers": "search scholarly sources"}
    )
    assert decision is not None
    assert decision["query"] == '"Lewis" AND 2020 AND "RAG"'
    assert "rechts" not in decision["query"].casefold()


def test_arxiv_web_hit_becomes_a_reader_capable_work() -> None:
    step = ToolStep(
        tool="web_search",
        query="Lewis 2020 RAG arXiv",
        reason="resolving the canonical primary paper from an exact web source",
        results=[
            {
                "title": "Retrieval-Augmented Generation for Knowledge-Intensive ...",
                "url": "https://arxiv.org/abs/2005.11401",
                "snippet": "# Title:Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks\n\nComments: Accepted at NeurIPS 2020",
            }
        ],
    )
    works = _arxiv_works_from_web_step(
        "Zeig das originale Paper von Lewis 2020 rechts und markier die wichtigsten Stellen", step
    )
    assert len(works) == 1
    work = works[0]
    assert work.id == "ARXIV:2005.11401"
    assert work.title == "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    assert work.year == 2020
    assert work.authors == ["Lewis"]
    assert work.pdf_url == "https://export.arxiv.org/pdf/2005.11401"
    assert work.oa_status == "green"


def test_multi_paper_completion_gate_rejects_old_reviews_and_unrelated_hits() -> None:
    question = "Find three primary papers from 2024 on efficient attention."
    works = [
        WorkRecord(
            id="W-GOOD",
            title="Efficient Attention with Linear Memory",
            abstract="A primary benchmark of efficient attention runtime and memory.",
            year=2024,
            work_type="article",
        ),
        WorkRecord(
            id="W-OLD",
            title="Efficient Attention with Linear Memory",
            abstract="The original efficient attention method.",
            year=2021,
            work_type="article",
        ),
        WorkRecord(
            id="W1199IEW",
            title="A Survey of Efficient Attention Methods",
            abstract="A review of efficient attention.",
            year=2025,
            work_type="review",
        ),
        WorkRecord(
            id="W-WRONG",
            title="Government Attention Allocation",
            abstract="An urban policy study.",
            year=2025,
            work_type="article",
        ),
    ]
    assert [work.id for work in paper_discovery_satisfying_works(question, works)] == ["W-GOOD"]


def test_paper_discovery_recovery_query_targets_named_entity_not_raw_message() -> None:
    question = "Was ist Terraform im IaC Bereich, gibts da nicht auch ein Ppaer?"
    query = paper_discovery_followup_query(
        question, ['("infrastructure as code" OR Terraform) AND concept']
    )
    assert query == '"Terraform"'
    assert question not in query
    assert paper_discovery_has_clear_match(
        question, [WorkRecord(id="W-TF", title="Terraform: Automating Infrastructure as a Service")]
    )
    assert not paper_discovery_has_clear_match(
        question, [WorkRecord(id="W-X", title="Cloud security practices in enterprises")]
    )
    assert (
        paper_discovery_followup_query(
            question, ['("infrastructure as code" OR Terraform) AND concept', '"Terraform"']
        )
        == '"Terraform" AND "IaC"'
    )


def test_paper_discovery_recovery_ignores_beginner_filler_words() -> None:
    question = "ich hab von sonem attention paper gehört wo transformer ohne rnn auskommen glaub. findest du bitte das orginal paper"
    query = paper_discovery_followup_query(question, [])
    assert query not in {'"ich"', '"hab"', '"ich" AND "hab"'}
    assert "ich" not in query.casefold()
    assert "hab" not in query.casefold()
    assert any(term in query.casefold() for term in ("attention", "transformer", "rnn"))


def test_half_remembered_paper_is_clear_when_title_and_abstract_context_agree() -> None:
    work = WorkRecord(
        id="W170603762",
        title="Attention Is All You Need",
        abstract="The Transformer relies entirely on self-attention and dispenses with recurrence in neural sequence modelling.",
    )
    assert paper_discovery_has_clear_match(
        "hab von so nem attention paper gehört, transformer ganz ohne rnn glaub", [work]
    )


def test_novice_title_hint_stops_redundant_paper_recovery() -> None:
    work = WorkRecord(id="W170603762", title="Attention Is All You Need", abstract=None)
    assert paper_discovery_has_clear_match(
        "hab von so nem attention paper gehört, transformer ganz ohne rnn glaub", [work]
    )


@pytest.mark.parametrize(
    ("query", "user_request", "expected"),
    [
        (
            "Kannst du bitte im Internet nach Terraform Sicherheit suchen",
            "Kannst du bitte im Internet nach Terraform Sicherheit suchen?",
            True,
        ),
        (
            "Please find me current official Terraform security guidance",
            "Please find me current official Terraform security guidance",
            True,
        ),
        (
            '("infrastructure as code" OR Terraform) AND security',
            "Kannst du bitte nach Terraform Sicherheit suchen?",
            False,
        ),
        ("PaperBanana", "Zeig mir das PaperBanana Paper", False),
        ('"vergleiche"', "Vergleiche das Paper mit zwei späteren Arbeiten", True),
        ("compare AND relevant", "Compare those with the relevant follow-up work", True),
        (
            '"RAG" AND "nein"',
            "Nein stopp, vergiss den Vergleich und öffne nur das originale RAG Paper.",
            True,
        ),
    ],
)
def test_search_query_guard_detects_raw_requests(
    query: str, user_request: str, expected: bool
) -> None:
    assert query_requires_formulation(query, user_request) is expected


def test_search_query_guard_rejects_requested_output_fields_as_search_concepts() -> None:
    request = "Finde die ursprünglichen RAG Methodenarbeiten und nenne jeweils Jahr und Venue."
    assert query_requires_formulation(
        '"retrieval augmented generation" AND year AND venue', request
    )
    assert not query_requires_formulation(
        '"retrieval augmented generation" AND foundational method', request
    )


def test_paper_recovery_query_ignores_correction_control_words() -> None:
    request = "nein stopp, vergiss den Vergleich und öffne nur das originale RAG Paper rechts"
    query = paper_discovery_followup_query(
        request, ['"Retrieval-Augmented Generation" Lewis et al']
    )
    assert "rag" in query.casefold()
    assert "nein" not in query.casefold()
    assert "stopp" not in query.casefold()
    assert "vergiss" not in query.casefold()
    assert query == '"Retrieval-Augmented Generation" AND "RAG"'


def test_internal_tool_markup_never_reaches_user_answer() -> None:
    leaked = '<tool_call>paper_search {"query": "retrieval augmented generation"}</tool_call>'
    cleaned = _strip_internal_tool_syntax(leaked, language="de")
    assert "tool_call" not in cleaned
    assert "paper_search" not in cleaned
    assert "Antwort" in cleaned


def test_primary_preprint_reconciles_exact_arxiv_metadata(monkeypatch: pytest.MonkeyPatch) -> None:

    class FakeArxiv:
        def __init__(self, **kwargs) -> None:
            pass

        def resolve_exact_title(self, title: str) -> SimpleNamespace:
            return SimpleNamespace(arxiv_id="1706.03762", title=title, year=2017)

    monkeypatch.setattr("sixsentences_server.pipeline.ask.ArxivClient", FakeArxiv)
    reconciled = _reconcile_primary_preprint(
        WorkRecord(
            id="W-PRIMARY",
            title="Attention Is All You Need",
            year=2025,
            cited_by_count=6000,
            work_type="preprint",
        )
    )
    assert reconciled.year == 2017
    assert reconciled.arxiv_id == "1706.03762"
    assert reconciled.doi == "10.48550/arXiv.1706.03762"
    assert reconciled.pdf_url == "https://export.arxiv.org/pdf/1706.03762"


def test_primary_preprint_reconciles_when_openalex_type_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing OpenAlex type must not preserve a recent deposit year."""

    class FakeArxiv:
        def __init__(self, **kwargs) -> None:
            pass

        def resolve_exact_title(self, title: str) -> SimpleNamespace:
            return SimpleNamespace(arxiv_id="1706.03762", title=title, year=2017)

    monkeypatch.setattr("sixsentences_server.pipeline.ask.ArxivClient", FakeArxiv)
    reconciled = _reconcile_primary_preprint(
        WorkRecord(
            id="W-PRIMARY-MISSING-TYPE",
            title="Attention Is All You Need",
            year=2025,
            cited_by_count=6000,
            work_type=None,
        )
    )
    assert reconciled.year == 2017
    assert reconciled.arxiv_id == "1706.03762"


def test_ask_sources_persist_parents_before_provenance(settings) -> None:
    from sqlalchemy import text

    from sixsentences_server.core.db import Org, Run, SourceRecordRow, WorkRow, db_session, init_db

    init_db()
    with db_session() as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
        org = Org(name="strict-ask-storage")
        session.add(org)
        session.flush()
        run = Run(org_id=org.id, question="question", status="running")
        session.add(run)
        session.flush()
        records = [
            WorkRecord(id="W-NEW-1", title="First", source="openalex"),
            WorkRecord(id="W-NEW-2", title="Second", source="openalex"),
            WorkRecord(id="W-NEW-1", title="First duplicate", source="openalex"),
        ]
        _persist_ask_sources(session, run, records, attached_ids=set())
        _persist_ask_sources(session, run, records, attached_ids=set())
        assert session.query(WorkRow).count() == 2
        assert session.query(SourceRecordRow).count() == 2


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is Terraform?", (12, 32)),
        ("Recherchiere das bitte gründlich.", (20, 40)),
        ("Give me an in-depth review of the evidence.", (20, 40)),
        ("Dann such halt mal tiefer.", (20, 40)),
        ("Mach nochmal eine tiefere Scholar Suche.", (20, 40)),
        ("Such nach Papern, irgendwie 10 Stück.", (20, 40)),
    ],
)
def test_tool_call_limits_follow_requested_research_depth(
    question: str, expected: tuple[int, int]
) -> None:
    assert _tool_call_limits(question) == expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({}, (12, 32)),
        ({"explicit_paper_discovery": True}, (32, 48)),
        ({"explicit_web_research": True}, (32, 48)),
        ({"substantive_research_refinement": True}, (32, 48)),
    ],
)
def test_explicit_research_gets_a_high_adaptive_search_ceiling(
    flags: dict[str, bool], expected: tuple[int, int]
) -> None:
    assert (
        _research_tool_call_limits(
            "Find current benchmark evidence.",
            explicit_paper_discovery=flags.get("explicit_paper_discovery", False),
            explicit_web_research=flags.get("explicit_web_research", False),
            substantive_research_refinement=flags.get("substantive_research_refinement", False),
        )
        == expected
    )


def test_scholarly_search_collects_a_broad_candidate_page(monkeypatch) -> None:
    observed: dict[str, int] = {}

    class FakeOpenAlex:
        def __init__(self, **_kwargs) -> None:
            pass

        def search(self, _query: str, *, limit: int) -> list[WorkRecord]:
            observed["limit"] = limit
            return []

    monkeypatch.setattr("sixsentences_server.chat.service.OpenAlexClient", FakeOpenAlex)
    _execute_tool("find_papers", "sociotechnical systems", "broad scholarly search")
    assert observed["limit"] == 24


def test_deep_multi_paper_request_can_use_a_fourth_distinct_search_pass() -> None:
    question = "Such tiefer nach 10 aktuellen Papern zu sociotechnical systems."
    steps = [
        ToolStep(tool="find_papers", query=f"sociotechnical systems angle {index}", results=[])
        for index in range(3)
    ]
    pool = mock_pool(lambda _model, _prompt: '"sociotechnical systems" AND organizations')
    decision = _paper_discovery_next_decision(
        question, steps, [], pool, {"find_papers": "search scholarly sources"}
    )
    assert decision is not None
    assert decision["tool"] == "find_papers"


def test_research_cannot_stop_before_three_distinct_scholarly_searches() -> None:
    question = "Find current papers about AI assisted code review."
    steps = [
        ToolStep(tool="find_papers", query=f"AI assisted code review angle {index}", results=[])
        for index in range(2)
    ]
    pool = mock_pool(lambda _model, _prompt: '"AI code review" AND evaluation')
    decision = _paper_discovery_next_decision(
        question, steps, [], pool, {"find_papers": "search scholarly sources"}
    )
    assert decision is not None
    assert decision["tool"] == "find_papers"
    assert decision["query"] not in {step.query for step in steps}


def test_research_stops_issuing_searches_at_the_high_adaptive_ceiling() -> None:
    question = "Research current evidence about AI assisted code review."
    steps = [
        ToolStep(tool="find_papers", query=f"AI assisted code review angle {index}", results=[])
        for index in range(32)
    ]
    decision = _paper_discovery_next_decision(
        question, steps, [], mock_pool(), {"find_papers": "search scholarly sources"}
    )
    assert decision is None


def test_novice_paper_overview_selects_a_structured_eight_paper_artifact() -> None:
    plan = _heuristic_plan(
        "Gib mir einen Überblick über die wichtigsten Paper in diesem Bereich.",
        attached=False,
        web_available=False,
    )
    assert plan.academic_search is True
    assert plan.compare_papers is True
    assert plan.table is True
    assert plan.compare_count == 8
    assert plan.chart is False


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Zeig mir die 15 gerade wichtigsten Papern im LLM-Bereich.", 15),
        ("Compare twenty current studies on abstract screening.", 20),
        ("Gib mir einen Überblick über die Paper in diesem Bereich.", 8),
        ("Vergleiche diese Paper.", 3),
    ],
)
def test_comparison_count_preserves_explicit_overview_size(question: str, expected: int) -> None:
    assert _requested_comparison_count(question) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is Terraform?", 8),
        ("Give me an overview of the papers in this field.", 24),
        ("Recherchiere das bitte umfassend.", 50),
        ("Find the 75 most relevant papers on this topic.", 75),
        ("Find 250 papers on this topic.", 100),
    ],
)
def test_source_count_adapts_to_the_requested_breadth(question: str, expected: int) -> None:
    assert _requested_source_count(question) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Welche Paper habe ich zum Thema LLMs in meiner Library?", 20),
        ("Gib mir einen Überblick über meine Terraform-Paper.", 50),
        ("Zeig mir genau 7 Paper aus meiner Library.", 7),
        ("List all papers in my library.", 200),
        ("Zeig mir 500 gespeicherte Paper.", 200),
    ],
)
def test_library_result_count_adapts_to_the_requested_breadth(question: str, expected: int) -> None:
    assert _requested_library_result_count(question) == expected


def test_web_search_promotes_a_found_arxiv_page_into_answer_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    source = SimpleNamespace(
        title="Terraform: Enabling Reproducible Infrastructure",
        url="https://arxiv.org/abs/2401.01234",
        domain="arxiv.org",
        snippet="The original Terraform paper.",
        category="preprint",
        quality="primary",
    )
    monkeypatch.setattr(
        "sixsentences_server.chat.service.WebSearchService.discover",
        lambda self, queries, limit: [source],
    )
    resolved = WorkRecord(
        id="W-TERRAFORM",
        title="Terraform: Enabling Reproducible Infrastructure",
        year=2024,
        arxiv_id="2401.01234",
    )
    monkeypatch.setattr(
        "sixsentences_server.chat.service.harvest_works",
        lambda sources, oa, limit: SimpleNamespace(works=[resolved]),
    )
    step, works = _execute_tool("web_search", "original Terraform paper", "find it")
    assert step.results[0]["url"] == "https://arxiv.org/abs/2401.01234"
    assert works == [resolved]


def test_generic_scientific_graphic_routes_to_visual_lab_not_year_chart() -> None:
    plan = _heuristic_plan(
        "Suche aktuelle Entwicklungen zu Terraform und mach daraus eine wissenschaftliche Grafik.",
        attached=False,
        web_available=True,
    )
    assert plan.chart is False
    assert plan.chart_kind is None


def test_explicit_publication_timeline_selects_the_matching_native_chart() -> None:
    plan = _heuristic_plan(
        "Zeige die gefundenen Publikationen nach Jahr als Diagramm.",
        attached=False,
        web_available=False,
    )
    assert plan.chart is True
    assert plan.chart_kind == "works_by_year"


def test_standalone_workspace_outcome_skips_unrelated_retrieval() -> None:
    request = "Ich will strukturiertes Feedback von 30 Studierenden einsammeln, mach das bitte direkt benutzbar."
    plan = _heuristic_plan(request, attached=False, web_available=True)
    assert plan.academic_search is True
    assert plan.web_search is True
    aligned = _align_plan_with_workspace_outcome(request, plan, workspace_types=("create_survey",))
    assert aligned.academic_search is False
    assert aligned.web_search is False


def test_compound_workspace_outcome_keeps_requested_paper_search() -> None:
    request = "Suche fünf Paper zu LLM-Screening und erstelle daraus eine wissenschaftliche Grafik."
    plan = _heuristic_plan(request, attached=False, web_available=True)
    aligned = _align_plan_with_workspace_outcome(request, plan, workspace_types=("create_visual",))
    assert aligned.academic_search is True


def test_visual_workspace_ignores_inferred_paper_reader_detour() -> None:
    request = "Create a conceptual scientific flow diagram of a systematic review from identification to inclusion. Let me review the visual brief first."
    plan = AskPlan(
        academic_search=True,
        web_search=False,
        search_query="systematic review flow diagram",
        web_query="",
        chart=False,
        chart_kind=None,
        cite=False,
        show_paper=True,
        save_paper=False,
        compare_papers=False,
        table=False,
        compare_count=3,
        source_count=8,
        table_columns=[],
        planner="model",
    )
    aligned = _align_plan_with_workspace_outcome(request, plan, workspace_types=("create_visual",))
    assert aligned.academic_search is False
    assert aligned.show_paper is False
    assert aligned.search_query == ""


def test_first_quick_answer_turn_prepares_a_native_visual_without_feature_jargon(
    monkeypatch: pytest.MonkeyPatch, settings: object
) -> None:
    del settings
    prompts: list[str] = []

    def handler(model: str, prompt: str) -> str:
        del model
        prompts.append(prompt)
        if "Web search available:" in prompt:
            return '{"academic_search": false, "web_search": false, "search_query": "", "web_query": "", "chart": false, "cite": false, "show_paper": false, "save_paper": false, "compare_papers": false, "table": false, "compare_count": 3, "table_columns": []}'
        if prompt.startswith("Current user request:"):
            return '{"workspace_actions":[{"type":"create_visual","title":"Multimodal routing flow","prompt":"Show multimodal inputs, shared embeddings, retrieval and expert routing as a publication-ready scientific flow.","kind":"flow","aspect_ratio":"4:3","resolution":"2k","review_passes":1}]}'
        return "I prepared the complete scientific visual brief for confirmation."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="intent@example.org", org="Intent")
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Erstelle mir eine wissenschaftliche Abbildung, die den multimodalen Routing-Ablauf verständlich macht.",
            "mode": "ask",
        },
    ).json()["id"]
    history = client.get(f"/runs/{run_id}/chat").json()
    cards = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "workspace_action"
    ]
    assert len(cards) == 1
    assert cards[0]["payload"]["workspace_actions"][0]["type"] == "create_visual"
    assert not any(
        message["payload"].get("tool") == "show_chart"
        for message in history
        if message["role"] == "tool"
    )
    final_prompts = [prompt for prompt in prompts if "A complete editable action preview" in prompt]
    assert len(final_prompts) == 1
    assert "No source retrieval was needed for this outcome" in final_prompts[0]
    assert "Say so honestly and suggest how to rephrase" not in final_prompts[0]


def test_first_quick_answer_workspace_control_is_deterministic_and_provider_free(
    monkeypatch: pytest.MonkeyPatch, settings: object
) -> None:
    del settings
    provider_prompts: list[str] = []

    def handler(model: str, prompt: str) -> str:
        del model
        provider_prompts.append(prompt)
        return (
            "Zotero is already connected. Use the citation card below this answer to finish setup."
        )

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app(), email="initial-control@example.org", org="Initial control")
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Bitte öffne Zotero in meinen Integrationen.",
            "mode": "ask",
            "language": "de",
        },
    ).json()["id"]
    history = client.get(f"/runs/{run_id}/chat").json()
    assert provider_prompts == []
    [tool_message] = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "workspace_action"
    ]
    [action] = tool_message["payload"]["workspace_actions"]
    assert action["type"] == "connect_reference_manager"
    assert action["provider"] == "zotero"
    assert action["requires_confirmation"] is True
    assistant = next(message for message in history if message["role"] == "assistant")
    assert "editable preview" in assistant["content"]
    assert "confirm the action" in assistant["content"]
    assert "Zitierkarte" not in assistant["content"]
    assert not any(
        message["payload"].get("tool") in {"find_papers", "web_search", "cite"}
        for message in history
        if message["role"] == "tool"
    )


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (None, False),
        ({"action": "tool", "tool": "web_search"}, False),
        (
            {
                "action": "tool",
                "tool": "web_search",
                "continue_research": True,
                "extension_reason": "insufficient_evidence",
            },
            True,
        ),
        (
            {
                "action": "tool",
                "tool": "read_webpage",
                "continue_research": True,
                "extension_reason": "new_direction",
            },
            True,
        ),
        (
            {
                "action": "tool",
                "tool": "web_search",
                "continue_research": "true",
                "extension_reason": "insufficient_evidence",
            },
            False,
        ),
        (
            {
                "action": "tool",
                "tool": "web_search",
                "continue_research": True,
                "extension_reason": "just_in_case",
            },
            False,
        ),
    ],
)
def test_tool_budget_extension_requires_an_explicit_valid_reason(
    decision: dict[str, object] | None, expected: bool
) -> None:
    assert _extension_allowed(decision) is expected


def test_ask_mode_returns_grounded_chat_answer(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(
            _handler_answering("Direct answer with evidence [W1]. A full search digs deeper.")
        ),
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda question, primary_query, **kwargs: (
            [WorkRecord(id="W1", title="Transformers", abstract="self-attention", year=2017)],
            "test-source",
        ),
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    response = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "What is happening in IaC right now?", "mode": "ask"},
    )
    assert response.status_code == 202
    run = client.get(f"/runs/{response.json()['id']}").json()
    assert run["status"] == "completed"
    assert run["config"]["mode"] == "ask"
    assert run["config"]["language"] == "en"
    assert run["config"]["model"] == "gemini-3.5-flash"
    assert "git_revision" not in run["config"]
    assert run["prisma"] is None
    chat = client.get(f"/runs/{run['id']}/chat").json()
    assert chat[-1]["role"] == "assistant"
    assert chat[-1]["citations"] == ["W1"]
    assert chat[-1]["payload"]["kind"] == "ask_answer"
    assert chat[-1]["payload"]["claims"]["checked"] == 1
    events = client.get(f"/runs/{run['id']}/events").json()
    names = [event["event"] for event in events]
    assert "ask_answer_started" in names
    assert "ask_answer_delta" in names
    assert "ask_answer_finalizing" in names
    assert "ask_answer_verifying" in names
    answer_events = [
        event
        for event in events
        if event["event"] in {"ask_answer_started", "ask_answer_delta", "ask_answer_finalizing"}
    ]
    attempt_ids = {event["payload"].get("attempt_id") for event in answer_events}
    assert len(attempt_ids) == 1
    assert None not in attempt_ids
    streamed = "".join(
        event["payload"]["delta"] for event in events if event["event"] == "ask_answer_delta"
    )
    assert streamed == "Direct answer with evidence [W1]. A full search digs deeper."


def test_ask_mode_keeps_answer_when_claim_verifier_is_unavailable(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(_handler_answering("Grounded answer [W1].")),
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda question, primary_query, **kwargs: (
            [WorkRecord(id="W1", title="Transformers", abstract="self-attention")],
            "test-source",
        ),
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask.verify_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("verifier unavailable")),
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    response = client.post(
        f"/projects/{project['id']}/runs", json={"question": "Explain transformers", "mode": "ask"}
    )
    assert response.status_code == 202, response.text
    run = client.get(f"/runs/{response.json()['id']}").json()
    assert run["status"] == "completed"
    chat = client.get(f"/runs/{run['id']}/chat").json()
    assert chat[-1]["content"] == "Grounded answer [W1]."
    assert chat[-1]["payload"]["verification"] == {"status": "unavailable"}


@pytest.mark.parametrize(
    "question",
    [
        "Was ist PaperBanana genau? Wenn es ein Paper gibt, zeig es mir und markier die wichtigsten Stellen.",
        "How does PaperBanana create publication-ready academic illustrations?",
    ],
)
def test_ask_mode_opens_a_discovered_central_paper(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    """An explicit reader wish and a planner-approved proactive explanation
    both bridge a discovered paper into the existing split-reader payload."""
    selected: list[str] = []

    def handler(model: str, prompt: str) -> str:
        if "Web search available:" in prompt and "Attached document:" in prompt:
            return '{"academic_search": false, "web_search": false, "search_query": "PaperBanana academic illustration", "web_query": "", "chart": false, "cite": false, "show_paper": true}'
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        return "PaperBanana uses a multi-agent figure pipeline [W260123265]."

    works = [
        WorkRecord(id="W10", title="Banana crop yield forecasting", year=2024),
        WorkRecord(
            id="W260123265",
            title="PaperBanana: Automating Academic Illustration for AI Scientists",
            abstract="A reference-driven multi-agent illustration framework.",
            year=2026,
            arxiv_id="2601.23265",
            oa_status="green",
        ),
        WorkRecord(id="W11", title="Academic chart design", year=2025),
        WorkRecord(id="W12", title="Visual agents for science", year=2025),
        WorkRecord(id="W13", title="Research communication systems", year=2024),
    ]

    def fake_show_paper(session, run, pool, work_id, focus, reason):
        selected.append(work_id)
        step = ToolStep(
            tool="show_paper",
            query=work_id,
            reason=reason,
            results=[
                {
                    "id": work_id,
                    "title": works[1].title,
                    "document_id": 77,
                    "page_count": 24,
                    "highlights": [
                        {
                            "page": 2,
                            "quote": "PaperBanana coordinates reference retrieval, planning, rendering, and refinement.",
                            "note": "Summarises the central pipeline.",
                        }
                    ],
                }
            ],
        )
        panel = {
            "kind": "paper",
            "document_id": 77,
            "work_id": work_id,
            "title": works[1].title,
            "page_count": 24,
            "highlights": step.results[0]["highlights"],
            "legal_basis": "oa_green",
            "license": "arxiv",
            "verified": True,
        }
        return (step, panel)

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda question, primary_query, **kwargs: (works, "openalex-live"),
    )
    monkeypatch.setattr("sixsentences_server.pipeline.ask._show_paper_step", fake_show_paper)
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    response = client.post(
        f"/projects/{project['id']}/runs", json={"question": question, "mode": "ask"}
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    history = client.get(f"/runs/{run_id}/chat").json()
    paper = next(
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "paper"
    )
    assert selected == ["W260123265"]
    assert paper["payload"]["tool"] == "show_paper"
    assert paper["payload"]["document_id"] == 77
    assert paper["payload"]["highlights"][0]["page"] == 2
    assert history[-1]["role"] == "assistant"


def test_ask_mode_recovers_an_arxiv_pdf_when_index_metadata_is_stale(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named arXiv page must repair a corpus hit before the reader opens."""
    from sixsentences_server.core.db import WorkRow, db_session

    stale = WorkRecord(
        id="W170603762", title="Attention Is All You Need", year=2017, doi="10.65215/stale-record"
    )
    opened: list[str] = []

    def handler(model: str, prompt: str) -> str:
        if "Web search available:" in prompt and "Attached document:" in prompt:
            return '{"academic_search":true,"web_search":false,"search_query":"Attention Is All You Need","web_query":"","chart":false,"cite":false,"show_paper":true,"save_paper":false}'
        if "User request:" in prompt:
            return '{"action":"answer"}'
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        return "The paper is open in the reader [W170603762]."

    def fake_live(session, run, tool, query, reason, iteration, event_sink=None, **_kwargs):
        assert tool == "web_search"
        return (
            ToolStep(
                tool=tool,
                query=query,
                reason=reason,
                iteration=iteration,
                results=[
                    {
                        "title": "[1706.03762] Attention Is All You Need",
                        "url": "https://arxiv.org/abs/1706.03762",
                        "domain": "arxiv.org",
                        "description": "Attention Is All You Need",
                    }
                ],
            ),
            [],
        )

    def fake_show(session, run, pool, work_id, focus, reason):
        repaired = session.get(WorkRow, work_id)
        assert repaired is not None
        assert repaired.payload["arxiv_id"] == "1706.03762"
        opened.append(work_id)
        result = {
            "id": work_id,
            "title": repaired.title,
            "document_id": 71,
            "page_count": 15,
            "highlights": [{"page": 1, "quote": "Verified passage", "note": "Key point"}],
        }
        return (
            ToolStep(tool="show_paper", query=work_id, reason=reason, results=[result]),
            {"kind": "paper", **result},
        )

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    from sixsentences_server.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda question, primary_query, **kwargs: ([stale], "corpus"),
    )
    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool_live", fake_live)
    monkeypatch.setattr("sixsentences_server.pipeline.ask._show_paper_step", fake_show)
    client = _authed(create_app())
    _grant_web_search_access(client)
    created = client.post(
        "/runs",
        json={
            "question": "Find Attention Is All You Need on the internet, open the PDF and highlight the key passages",
            "mode": "ask",
            "web_search_public_data_confirmed": True,
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    with db_session() as session:
        repaired = session.get(WorkRow, stale.id)
        assert repaired is not None and repaired.payload["arxiv_id"] == "1706.03762"
    assert opened == [stale.id]
    tools = [
        message["payload"].get("tool")
        for message in client.get(f"/runs/{run_id}/chat").json()
        if message["role"] == "tool"
    ]
    assert "show_paper" in tools


def test_ask_mode_rejects_unknown_mode(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    response = client.post(
        f"/projects/{project['id']}/runs", json={"question": "x", "mode": "banana"}
    )
    assert response.status_code == 422


def test_cancel_marks_an_interrupted_quick_answer_terminal(corpus: DuckDBCorpus) -> None:
    """Ask mode has no pipeline checkpoints, so cancellation must release the
    workspace concurrency slot immediately even when its worker disappeared."""
    from datetime import UTC, datetime, timedelta

    from sixsentences_server.core.db import BackgroundJobRow, Run, db_session
    from sixsentences_server.core.db import Project as ProjectRow

    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    with db_session() as session:
        project_row = session.get(ProjectRow, project["id"])
        assert project_row is not None
        run = Run(
            org_id=project_row.org_id,
            project_id=project_row.id,
            question="interrupted quick answer",
            status="running",
            config={"mode": "ask"},
        )
        session.add(run)
        session.flush()
        job = BackgroundJobRow(
            org_id=run.org_id,
            task="_execute_ask",
            lane="chat",
            args=[run.id, "auto"],
            kwargs={},
            status="running",
            lease_owner="interactive-worker",
            lease_token="active-quick-answer",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        session.add(job)
        session.flush()
        job_id = job.id
        public_id = run.public_id
    response = client.post(f"/runs/{public_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    cancelled = client.get(f"/runs/{public_id}").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] is not None
    with db_session() as session:
        job = session.get(BackgroundJobRow, job_id)
        assert job is not None
        assert job.status == "cancelled"
        assert job.lease_owner is None
    events = client.get(f"/runs/{public_id}/events").json()
    assert [event["event"] for event in events].count("run_cancelled") == 1
    assert client.post(f"/runs/{public_id}/cancel").status_code == 200


def test_failed_quick_answer_can_retry_without_duplicate_question_charge(
    monkeypatch: pytest.MonkeyPatch, corpus: DuckDBCorpus
) -> None:
    from sqlalchemy import func, select

    import sixsentences_server.api.app as app_module
    from sixsentences_server.core.db import (
        CapacityReservationRow,
        ChatMessageRow,
        CreditEventRow,
        Org,
        Run,
        db_session,
    )

    client = _authed(create_app(), email="retry-ask@example.org", org="Retry Ask")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        run = Run(
            org_id=org.id,
            question="Explain retrieval augmented generation",
            status="failed",
            error="The AI assistant is unavailable.",
            config={"mode": "ask", "model": "deepseek-v4-flash"},
        )
        session.add(run)
        session.flush()
        session.add(
            CreditEventRow(
                org_id=org.id,
                run_id=run.id,
                action="question",
                model="deepseek-v4-flash",
                credits=1,
            )
        )
        session.add(
            ChatMessageRow(
                org_id=org.id,
                run_id=run.id,
                role="tool",
                content="partial attempt",
                payload={"kind": "status", "status": "running"},
            )
        )
        session.flush()
        public_id = run.public_id
    scheduled: list[tuple[object, ...]] = []

    def fake_enqueue(background: object, task: object, *args: object, **kwargs: object) -> int:
        scheduled.append(args)
        return 81

    monkeypatch.setattr(app_module, "enqueue_job", fake_enqueue)
    response = client.post(f"/runs/{public_id}/retry")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    assert len(scheduled) == 1
    retried = client.get(f"/runs/{public_id}").json()
    assert retried["status"] == "pending"
    assert retried["error"] is None
    with db_session() as session:
        run = session.scalar(select(Run).where(Run.public_id == public_id))
        assert run is not None
        assert run.config["retry_count"] == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(ChatMessageRow)
                .where(ChatMessageRow.run_id == run.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CreditEventRow)
                .where(CreditEventRow.run_id == run.id, CreditEventRow.action == "question")
            )
            == 1
        )
        reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id == run.config["cost_action_id"]
            )
        )
        assert reservation is not None and reservation.status == "active"


def test_web_quick_answer_retry_requires_a_fresh_confirmation_before_charge(
    corpus: DuckDBCorpus,
) -> None:
    """The original attempt's acknowledgement never authorizes a retry."""
    from sqlalchemy import func, select

    from sixsentences_server.core.db import (
        CapacityReservationRow,
        ChatMessageRow,
        Org,
        Run,
        db_session,
    )

    client = _authed(create_app(), email="retry-web-scope@example.org", org="Retry Web Scope")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        run = Run(
            org_id=org.id,
            question="Search the web for current official guidance",
            status="failed",
            error="The answer could not be completed.",
            config={
                "mode": "ask",
                "model": "deepseek-v4-flash",
                "web_search": True,
                "web_search_public_data_confirmed": True,
            },
        )
        session.add(run)
        session.flush()
        session.add(
            ChatMessageRow(
                org_id=org.id,
                run_id=run.id,
                role="tool",
                content="preserved failed attempt",
                payload={"kind": "status", "status": "failed"},
            )
        )
        session.flush()
        public_id = run.public_id
        run_id = run.id
    response = client.post(f"/runs/{public_id}/retry")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "web_search_public_scope_confirmation_required"
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.config["web_search_public_data_confirmed"] is True
        assert (
            session.scalar(
                select(func.count(ChatMessageRow.id)).where(ChatMessageRow.run_id == run_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count(CapacityReservationRow.id)).where(
                    CapacityReservationRow.resource_type == "run",
                    CapacityReservationRow.resource_id == str(run_id),
                )
            )
            == 0
        )


def test_retry_refuses_search_and_nonfailed_runs(corpus: DuckDBCorpus) -> None:
    from sixsentences_server.core.db import Org, Run, db_session

    client = _authed(create_app(), email="retry-guard@example.org", org="Retry Guard")
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        org = session.get(Org, org_id)
        assert org is not None
        search = Run(org_id=org.id, question="search", status="failed", config={"mode": "search"})
        active_ask = Run(org_id=org.id, question="ask", status="running", config={"mode": "ask"})
        session.add_all([search, active_ask])
        session.flush()
        search_id = search.public_id
        active_id = active_ask.public_id
    assert client.post(f"/runs/{search_id}/retry").status_code == 409
    assert client.post(f"/runs/{active_id}/retry").status_code == 409


@pytest.mark.parametrize(
    "question",
    [
        "Kannst du mal ein Paper aus meiner Library öffnen, egal welches?",
        "Öffne irgendeine PDF aus meiner Bibliothek.",
        "Open any paper from my library.",
        "Zeig mir eines meiner Dokumente aus der Libary.",
        "Lies eine Studie aus meiner Biblothek.",
    ],
)
def test_opening_a_stored_library_paper_is_a_direct_internal_intent(question: str) -> None:
    assert _library_paper_request(question)
    plan = _heuristic_plan(question, attached=True, web_available=True)
    assert plan.academic_search is False


def test_generic_library_navigation_is_not_misread_as_opening_a_random_paper() -> None:
    assert not _library_paper_request("Öffne meine Bibliothek.")


@pytest.mark.parametrize(
    "question",
    [
        "Was hba ich für Paper zum Thema LLMs in meiner Library?",
        "Welche Transformer-Studien habe ich in meiner Bibliothek?",
        "List my papers about systematic reviews from my library.",
        "Gibt es in meiner Libary PDFs zu Terraform?",
        "How many AI papers are available in my library?",
    ],
)
def test_library_inventory_questions_are_direct_internal_intents(question: str) -> None:
    assert _library_inventory_request(question)


@pytest.mark.parametrize(
    "question",
    [
        "Was ist eine wissenschaftliche Bibliothek?",
        "Speichere dieses Paper in meiner Library.",
        "Welche Paper gibt es zu LLMs?",
        "Öffne meine Bibliothek.",
        "Zeig mir eines meiner Dokumente aus der Libary.",
        "Show any paper from my library.",
    ],
)
def test_non_inventory_questions_do_not_search_private_library(question: str) -> None:
    assert not _library_inventory_request(question)


@pytest.mark.parametrize(
    "question",
    [
        "Speicher genau dieses Paper bitte auch in meine libary.",
        "Kannst du das PDF behalten?",
        "Save this paper for me.",
        "Füg die Studie zu meiner Biblothek hinzu.",
    ],
)
def test_save_paper_intent_tolerates_novice_wording_and_typos(question: str) -> None:
    assert _SAVE_PAPER_ASK.search(question)


def test_latest_reader_work_id_recovers_legacy_and_current_tool_payloads(
    corpus: DuckDBCorpus,
) -> None:
    from sqlalchemy import select

    from sixsentences_server.core.db import ChatMessageRow, Run, User, db_session

    _authed(create_app())
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "owner@example.org"))
        assert user is not None
        run = Run(
            org_id=user.org_id, question="Open a paper", status="completed", config={"mode": "ask"}
        )
        session.add(run)
        session.flush()
        session.add(
            ChatMessageRow(
                org_id=user.org_id,
                run_id=run.id,
                role="tool",
                content="Opened the paper",
                payload={
                    "tool": "show_paper",
                    "query": "W170603762",
                    "results": [{"id": "W170603762"}],
                },
            )
        )
        session.flush()
        assert _latest_reader_work_id(session, run.id) == "W170603762"


def test_quick_answer_searches_private_library_by_topic_without_public_retrieval(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select

    from sixsentences_server.core.db import DocumentRow, User, WorkRow, db_session

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(
            _handler_answering("In deiner Library passt dieses Paper: [W990001].")
        ),
    )

    def fail_external_search(*_args, **_kwargs):
        raise AssertionError("a Library inventory request must stay inside the tenant")

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", fail_external_search)
    client = _authed(create_app())
    _authed(create_app(), email="other@example.org", org="Other workspace")
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "owner@example.org"))
        other_user = session.scalar(select(User).where(User.email == "other@example.org"))
        assert user is not None
        assert other_user is not None
        records = [
            WorkRecord(
                id="W990001",
                title="Reliable Screening with Large Language Models",
                year=2025,
                abstract="An evaluation of LLM-assisted screening.",
            ),
            WorkRecord(
                id="W990002",
                title="Visual SLAM for Mobile Robots",
                year=2024,
                abstract="A robotics mapping system.",
            ),
        ]
        for index, record in enumerate(records):
            checksum = str(index + 1) * 64
            session.add(
                WorkRow(
                    id=record.id,
                    title=record.title,
                    year=record.year,
                    payload=record.model_dump(mode="json"),
                )
            )
            session.add(
                DocumentRow(
                    org_id=user.org_id,
                    run_id=None,
                    work_id=record.id,
                    status="retrieved",
                    source="upload",
                    legal_basis="user_supplied",
                    content_type="application/pdf",
                    checksum=checksum,
                    byte_size=42,
                    storage_path=f"blobs/{checksum[:2]}/{checksum}",
                    text_status="parsed",
                )
            )
        private_record = WorkRecord(
            id="W990003",
            title="Large Language Models in a Different Workspace",
            year=2026,
            abstract="This tenant-private paper must never cross workspace boundaries.",
        )
        session.add(
            WorkRow(
                id=private_record.id,
                title=private_record.title,
                year=private_record.year,
                payload=private_record.model_dump(mode="json"),
            )
        )
        session.add(
            DocumentRow(
                org_id=other_user.org_id,
                run_id=None,
                work_id=private_record.id,
                status="retrieved",
                source="upload",
                legal_basis="user_supplied",
                content_type="application/pdf",
                checksum="3" * 64,
                byte_size=42,
                storage_path="blobs/33/" + "3" * 64,
                text_status="parsed",
            )
        )
    created = client.post(
        "/runs",
        json={"question": "Was hba ich für Paper zum Thema LLMs in meiner Library?", "mode": "ask"},
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    messages = client.get(f"/runs/{run_id}/chat").json()
    tools = [message for message in messages if message["role"] == "tool"]
    assert [message["payload"]["tool"] for message in tools] == ["search_library"]
    assert [result["id"] for result in tools[0]["payload"]["results"]] == ["W990001"]
    assistant = next(message for message in reversed(messages) if message["role"] == "assistant")
    assert "no access" not in assistant["content"].lower()
    assert assistant["citations"] == ["W990001"]
    events = client.get(f"/runs/{run_id}/events").json()
    planned = next(event for event in events if event["event"] == "ask_planned")
    assert planned["payload"]["planner"] == "library"
    assert planned["payload"]["academic_search"] is False
    follow_up = client.post(
        f"/runs/{run_id}/chat",
        json={"question": "Welche LLM Paper sind jetzt in meiner Bibliothek?"},
    )
    assert follow_up.status_code == 200, follow_up.text
    assert follow_up.json()["tools_used"] == ["search_library"]
    assert follow_up.json()["citations"] == [{"id": "W990001", "title": records[0].title}]


def test_library_delete_resolves_internally_and_requires_confirmation(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select

    from sixsentences_server.core.db import DocumentRow, User, WorkRow, db_session

    def handler(model: str, prompt: str) -> str:
        del model
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            return '{"action":"tool","tool":"search_library","query":"Attention Is All You Need","reason":"find the target"}'
        return "I found the exact Library paper and prepared the safe confirmation."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )

    def fail_external_search(*_args, **_kwargs):
        raise AssertionError("managing a Library paper must not search public sources")

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", fail_external_search)
    client = _authed(create_app(), email="delete-library@example.org", org="Delete Library")
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "delete-library@example.org"))
        assert user is not None
        work = WorkRecord(
            id="WATTENTION",
            title="Attention Is All You Need",
            year=2017,
            abstract="The original Transformer paper.",
        )
        session.add(
            WorkRow(
                id=work.id, title=work.title, year=work.year, payload=work.model_dump(mode="json")
            )
        )
        session.add(
            DocumentRow(
                org_id=user.org_id,
                run_id=None,
                work_id=work.id,
                status="retrieved",
                source="upload",
                legal_basis="user_supplied",
                content_type="application/pdf",
                checksum="b" * 64,
                byte_size=42,
                storage_path="blobs/bb/" + "b" * 64,
                text_status="parsed",
            )
        )
    created = client.post(
        "/runs", json={"question": "Welche Paper sind in meiner Library?", "mode": "ask"}
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    response = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "lösch das Attention is All You Need Paper aus meiner libary, aber zeig mir vorher genau welches und mach noch nichts ohne meine bestätigung"
        },
    )
    assert response.status_code == 200, response.text
    assert "find_papers" not in response.json()["tools_used"]
    assert "web_search" not in response.json()["tools_used"]
    messages = client.get(f"/runs/{run_id}/chat").json()
    action_messages = [
        message
        for message in messages
        if message["role"] == "tool" and message["payload"].get("kind") == "workspace_action"
    ]
    assert len(action_messages) == 1, messages
    [action] = action_messages[0]["payload"]["workspace_actions"]
    assert action["type"] == "manage_resource"
    assert action["operation"] == "delete"
    assert action["resource_type"] == "library_paper"
    assert action["requires_confirmation"] is True


def test_quick_answer_opens_a_stored_library_paper_without_scholarly_search(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select

    from sixsentences_server.chat.service import ToolStep
    from sixsentences_server.core.db import DocumentRow, User, WorkRow, db_session

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(_handler_answering("I opened the stored paper [WLIB].")),
    )

    def fail_external_search(*_args, **_kwargs):
        raise AssertionError("a Library request must not call the scholarly index")

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", fail_external_search)

    def fake_show(session, run, pool, work_id, focus, reason):
        document = session.scalar(
            select(DocumentRow).where(DocumentRow.run_id == run.id, DocumentRow.work_id == work_id)
        )
        assert document is not None
        result = {
            "id": work_id,
            "title": "Stored Library Study",
            "document_id": document.id,
            "page_count": 3,
            "highlights": [],
        }
        return (
            ToolStep(tool="show_paper", query=work_id, reason=reason, results=[result]),
            {"kind": "paper", **result},
        )

    monkeypatch.setattr("sixsentences_server.pipeline.ask._show_paper_step", fake_show)
    client = _authed(create_app())
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "owner@example.org"))
        assert user is not None
        work = WorkRecord(
            id="WLIB",
            title="Stored Library Study",
            year=2025,
            abstract="A stored paper used to verify direct Library routing.",
        )
        session.add(
            WorkRow(
                id=work.id, title=work.title, year=work.year, payload=work.model_dump(mode="json")
            )
        )
        session.add(
            DocumentRow(
                org_id=user.org_id,
                run_id=None,
                work_id=work.id,
                status="retrieved",
                source="upload",
                legal_basis="user_supplied",
                content_type="application/pdf",
                checksum="a" * 64,
                byte_size=42,
                storage_path="blobs/aa/" + "a" * 64,
                text_status="parsed",
            )
        )
    created = client.post(
        "/runs",
        json={
            "question": "Kannst du mal ein Paper aus meiner Library öffnen, egal welches?",
            "mode": "ask",
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    messages = client.get(f"/runs/{run_id}/chat").json()
    tools = [message for message in messages if message["role"] == "tool"]
    assert [message["payload"]["tool"] for message in tools] == ["show_paper"]
    assert tools[0]["payload"]["kind"] == "paper"
    events = client.get(f"/runs/{run_id}/events").json()
    planned = next(event for event in events if event["event"] == "ask_planned")
    assert planned["payload"]["planner"] == "library"
    assert planned["payload"]["academic_search"] is False


def test_chat_uses_tools_and_records_steps(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action": "answer"}'
            return '{"action": "tool", "tool": "find_papers", "query": "iac llm evaluation", "reason": "user wants newer papers"}'
        return "A newer paper exists [W9]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    fake_step = ToolStep(
        tool="find_papers",
        query="iac llm evaluation",
        results=[{"id": "W9", "title": "New paper", "year": 2026}],
    )
    fake_work = WorkRecord(id="W9", title="New paper", abstract="LLMs for IaC.", year=2026)
    monkeypatch.setattr(
        "sixsentences_server.chat.service._execute_tool",
        lambda tool, query, reason: (fake_step, [fake_work]),
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    answer = client.post(
        f"/runs/{run_id}/chat", json={"question": "Find newer papers on this"}
    ).json()
    assert answer["tools_used"] == ["find_papers"] * 3
    assert answer["citations"] == [{"id": "W9", "title": "New paper"}]
    history = client.get(f"/runs/{run_id}/chat").json()
    roles = [m["role"] for m in history]
    assert roles[-1] == "assistant"
    tool_msg = next(
        message
        for message in reversed(history)
        if message["role"] == "tool" and message["payload"].get("tool") == "find_papers"
    )
    assert tool_msg["payload"]["tool"] == "find_papers"
    assert tool_msg["payload"]["results"][0]["id"] == "W9"
    assert "iac llm evaluation" in tool_msg["content"]
    work_log = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "agent_work"
    ]
    agent_events = [message["payload"]["agent_event"]["event"] for message in work_log]
    assert agent_events[0] == "plan.created"
    assert "agent.update" in agent_events
    assert agent_events[-1] == "checkpoint.completed"
    assert "agent_update" not in answer["tools_used"]


def test_follow_up_can_explicitly_forbid_new_research(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A novice's latest tool constraint must override paper-like wording."""
    routed = False

    def handler(model: str, prompt: str) -> str:
        nonlocal routed
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            routed = True
            assert "find_papers:" not in prompt
            assert "web_search:" not in prompt
            return '{"action": "answer"}'
        return "The current paper uses self-attention throughout the architecture [W1]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "no-new-search"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    response = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "explain mir die Architektur von diesem Paper auf deutsch, aber bitte nichts neu suchen"
        },
    )
    assert response.status_code == 200, response.text
    assert routed is True
    assert response.json()["tools_used"] == []


def test_substantive_follow_up_refreshes_the_research_boundary(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A narrower evidence request must not silently reuse a broader search."""

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "Research request:" in prompt:
            return "large language model title abstract screening peer reviewed"
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action": "answer"}'
            return '{"action": "tool", "tool": "find_papers", "query": "llm screening overview", "reason": "ground the overview"}'
        return "The evidence concerns title and abstract screening [W9]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    queries: list[str] = []

    def execute(tool: str, query: str, reason: str) -> tuple[ToolStep, list[WorkRecord]]:
        queries.append(query)
        work = WorkRecord(
            id="W9",
            title="LLM title and abstract screening",
            abstract="Peer reviewed screening evidence.",
            year=2026,
        )
        return (
            ToolStep(tool=tool, query=query, results=[{"id": work.id, "title": work.title}]),
            [work],
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", execute)
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "research-refinement"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    first = client.post(
        f"/runs/{run_id}/chat", json={"question": "Give me an overview of LLM screening"}
    )
    assert first.status_code == 200, first.text
    before = len(queries)
    correction = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "ne fokus bitte nur title und abstract screening und nur peer reviewed, erklär es weiter auf deutsch"
        },
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["tools_used"] == ["find_papers"] * 3
    refinement_queries = queries[before:]
    assert len(refinement_queries) == 3
    assert len({" ".join(query.casefold().split()) for query in refinement_queries}) == 3
    assert any("title abstract screening" in query.casefold() for query in refinement_queries)


def test_semantic_follow_up_inherits_context_and_keeps_the_search_floor(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terse follow-up needs no magic keyword to receive full research depth."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    routing_prompts: list[str] = []
    formulated_queries = iter(
        (
            "Terraform infrastructure as code primary documentation",
            "Terraform infrastructure as code independent evaluation",
        )
    )

    def handler(model: str, prompt: str) -> str:
        del model
        if "Web search available:" in prompt:
            return '{"academic_search": false, "web_search": false, "search_query": "", "web_query": "", "chart": false, "cite": false, "show_paper": false}'
        if prompt.startswith("Research request:"):
            assert "Terraform" in prompt
            return next(formulated_queries)
        if "User request:" in prompt:
            routing_prompts.append(prompt)
            if "Mach das bitte ordentlich" in prompt:
                assert "Earlier in this conversation:" in prompt
                assert "Was ist Terraform genau?" in prompt
                if "Observations from earlier tool iterations" not in prompt:
                    return '{"action":"tool","tool":"web_search","query":"Terraform infrastructure as code overview","reason":"ground the requested explanation"}'
            return '{"action":"answer"}'
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        return "Terraform is an infrastructure-as-code tool."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    queries: list[str] = []

    def execute(tool: str, query: str, reason: str):
        assert tool == "web_search"
        queries.append(query)
        return (
            ToolStep(
                tool=tool,
                query=query,
                reason=reason,
                results=[
                    {
                        "title": f"Terraform source {len(queries)}",
                        "url": f"https://example.org/terraform/{len(queries)}",
                        "domain": "example.org",
                        "snippet": "Terraform infrastructure as code evidence.",
                    }
                ],
            ),
            [],
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", execute)
    client = _authed(create_app(), email="semantic-followup@example.org", org="Semantic")
    _grant_web_search_access(client)
    run_id = client.post(
        "/runs", json={"question": "Was ist Terraform genau?", "mode": "ask"}
    ).json()["id"]
    response = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "Mach das bitte ordentlich, geh genauer darauf ein und suche dafür im Web.",
            "web_search_public_data_confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["tools_used"] == ["web_search"] * 3
    assert len(queries) == 3
    assert len({" ".join(query.casefold().split()) for query in queries}) == 3
    assert any("Was ist Terraform genau?" in prompt for prompt in routing_prompts)


def test_paper_refinement_keeps_the_original_topic_and_primary_constraint(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terse novice correction must not search its constraint as a new topic."""

    def handler(model: str, prompt: str) -> str:
        del model
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        if "Research request:" in prompt:
            assert "RAG" in prompt
            assert "keine Surveys" in prompt
            return '"retrieval augmented generation" AND original method'
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action":"answer"}'
            return '{"action":"tool","tool":"find_papers","query":"retrieval augmented generation original paper","reason":"find the original methods"}'
        return "The original methods are listed with year and venue [W-RAG-1] [W-RAG-2]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    queries: list[str] = []

    def execute(tool: str, query: str, reason: str):
        assert tool == "find_papers"
        queries.append(query)
        works = [
            WorkRecord(
                id="W-RAG-1",
                title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                abstract="The original retrieval augmented generation method.",
                year=2020,
                work_type="article",
            ),
            WorkRecord(
                id="W-RAG-2",
                title="REALM: Retrieval-Augmented Language Model Pre-Training",
                abstract="A primary retrieval augmented language model method.",
                year=2020,
                work_type="article",
            ),
            WorkRecord(
                id="W-RAG-3",
                title="A Survey of Retrieval-Augmented Generation",
                abstract="A secondary overview.",
                year=2024,
                work_type="review",
            ),
        ]
        return (
            ToolStep(
                tool=tool,
                query=query,
                reason=reason,
                results=[{"id": work.id, "title": work.title} for work in works],
            ),
            works,
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", execute)
    client = _authed(create_app(), "rag-refinement@example.org", "RAG refinement")
    project = client.post("/projects", json={"name": "rag"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "RAG", "query": "retrieval augmented generation"},
    ).json()["id"]
    first = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "finde bitte drei Originalarbeiten zu RAG, lies mindestens zwei davon genauer"
        },
    )
    assert first.status_code == 200, first.text
    queries.clear()
    correction = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "kleine korrektur: wirklich nur die ursprünglichen Methodenarbeiten, keine Surveys, und nenne jeweils Jahr und Venue"
        },
    )
    assert correction.status_code == 200, correction.text
    assert queries
    assert all("rag" in query.casefold() or "retrieval" in query.casefold() for query in queries)
    assert "survey" not in " ".join(correction.json()["answer"].casefold().split())


def test_named_paper_comparison_follow_up_retrieves_the_named_works(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A novice comparison request must not answer from one open paper alone."""

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "User request:" in prompt:
            return '{"action": "answer"}'
        return "BERT and GPT-3 extend the original Transformer in different ways [W1001] [W1002]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    calls: list[str] = []

    def fake_execute(tool: str, query: str, reason: str):
        assert tool == "find_papers"
        calls.append(query)
        works = [
            WorkRecord(id="W1001", title="BERT: Pre-training of Deep Bidirectional Transformers"),
            WorkRecord(id="W1002", title="Language Models are Few-Shot Learners"),
        ]
        return (
            ToolStep(
                tool="find_papers",
                query=query,
                reason=reason,
                results=[{"id": work.id, "title": work.title} for work in works],
            ),
            works,
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_execute)
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "comparison"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    answer = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "vergleich das paper bitte nur mit BERT und GPT 3, auf deutsch und mit belegen"
        },
    )
    assert answer.status_code == 200, answer.text
    assert len(calls) == 3
    assert all("vergleich" not in query.casefold() for query in calls)
    assert any("bert" in query.casefold() for query in calls)
    assert any("gpt" in query.casefold() for query in calls)
    used_tools = answer.json()["tools_used"]
    assert used_tools.count("find_papers") >= 3
    assert set(used_tools) <= {"find_papers", "web_search"}
    assert {citation["id"] for citation in answer.json()["citations"]} == {"W1001", "W1002"}
    history = client.get(f"/runs/{run_id}/chat").json()
    agent_events = [
        message["payload"]["agent_event"]["event"]
        for message in history
        if message["role"] == "tool"
        and message["payload"].get("kind") == "agent_work"
        and ("agent_event" in message["payload"])
    ]
    assert agent_events[0] == "plan.created"
    assert "agent.update" in agent_events
    assert agent_events[-1] == "checkpoint.completed"


def test_multi_paper_follow_up_searches_until_contract_is_satisfied_and_reads_sources(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A requested count, year and evidence type are retrieval requirements."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    formulated: list[str] = []

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"the value is in the cited source"}'
        if "Research request:" in prompt:
            if "Prioritise benchmark" in prompt:
                formulated.append("benchmark")
                return '"efficient attention" AND (latency OR memory) AND 2024'
            formulated.append("methods")
            return '"efficient attention" AND (linear OR sparse) AND 2024'
        if "User request:" in prompt:
            if "Observations from earlier tool iterations" in prompt:
                return '{"action":"answer"}'
            return '{"action":"tool","tool":"find_papers","query":"efficient attention 2024","reason":"locate current primary work"}'
        return "The three qualifying primary papers report distinct efficiency evidence [W1101] [W1102] [W1103]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    calls: list[str] = []
    web_calls = 0

    def fake_execute(tool: str, query: str, reason: str):
        nonlocal web_calls
        if tool == "web_search":
            web_calls += 1
            return (ToolStep(tool=tool, query=query, reason=reason, results=[]), [])
        assert tool == "find_papers"
        calls.append(query)
        rounds = [
            [
                WorkRecord(
                    id="W1101",
                    title="Efficient Attention Kernels for Long Sequences",
                    abstract="Primary benchmarks report efficient attention latency and memory.",
                    year=2024,
                    work_type="article",
                ),
                WorkRecord(
                    id="W1198",
                    title="Urban Policy Attention",
                    abstract="A study of public administration.",
                    year=2025,
                    work_type="article",
                ),
            ],
            [
                WorkRecord(
                    id="W1102",
                    title="Linear Attention at Inference Time",
                    abstract="Primary evaluation of efficient attention runtime and memory.",
                    year=2025,
                    work_type="article",
                ),
                WorkRecord(
                    id="W1199",
                    title="A Survey of Efficient Attention",
                    abstract="Review of efficient attention methods.",
                    year=2025,
                    work_type="review",
                ),
            ],
            [
                WorkRecord(
                    id="W1103",
                    title="Sparse Attention with Bounded Memory",
                    abstract="Primary efficient attention benchmarks for runtime and memory.",
                    year=2024,
                    work_type="article",
                )
            ],
        ]
        works = rounds[len(calls) - 1]
        return (
            ToolStep(
                tool=tool,
                query=query,
                reason=reason,
                results=[{"id": work.id, "title": work.title, "year": work.year} for work in works],
            ),
            works,
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_execute)
    client = _authed(create_app())
    _grant_web_search_access(client)
    project = client.post("/projects", json={"name": "efficient-attention"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    response = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "Search the web for three primary papers from 2024 on efficient attention, read the primary sources and compare only supported runtime and memory values.",
            "web_search_public_data_confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 3
    assert web_calls == 1
    assert formulated.count("benchmark") >= 1
    assert formulated.count("methods") >= 2
    used_tools = response.json()["tools_used"]
    assert used_tools.count("find_papers") == 3
    assert used_tools.count("web_search") == 1
    assert used_tools.count("read_paper") == 3
    assert {citation["id"] for citation in response.json()["citations"]} == {
        "W1101",
        "W1102",
        "W1103",
    }


def test_empty_foundational_search_recovers_without_querying_requested_output_fields(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A novice correction must recover from empty searches before answering."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    formulated = 0

    def handler(model: str, prompt: str) -> str:
        nonlocal formulated
        del model
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        if "Research request:" in prompt:
            formulated += 1
            if formulated == 1:
                return '"retrieval augmented generation" AND year AND venue'
            return '"retrieval augmented generation" AND foundational method'
        if "User request:" in prompt:
            return '{"action":"answer"}'
        return "The originating method paper is the 2020 NeurIPS paper [W1700]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    queries: list[str] = []
    tools: list[str] = []

    def fake_execute(tool: str, query: str, reason: str):
        assert tool in {"find_papers", "web_search"}
        tools.append(tool)
        queries.append(query)
        if len(queries) < 3:
            return (ToolStep(tool=tool, query=query, reason=reason, results=[]), [])
        work = WorkRecord(
            id="W1700",
            title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
            abstract="We introduce retrieval augmented generation for knowledge intensive NLP.",
            year=2020,
            venue="NeurIPS",
            work_type="article",
        )
        return (
            ToolStep(
                tool=tool,
                query=query,
                reason=reason,
                results=[{"id": work.id, "title": work.title, "year": work.year}],
            ),
            [work],
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_execute)
    client = _authed(create_app())
    _grant_web_search_access(client)
    project = client.post("/projects", json={"name": "rag-foundations"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "retrieval augmented generation", "query": "RAG"},
    ).json()["id"]
    response = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "finde bitte im Web die ursprüngliche Methodenarbeit zu RAG, keine Surveys, und nenne Jahr und Venue",
            "web_search_public_data_confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    assert 3 <= len(queries) <= 15
    assert tools[:3] == ["find_papers", "find_papers", "web_search"]
    assert set(tools) <= {"find_papers", "web_search"}
    assert all("year" not in query.casefold() for query in queries)
    assert all("venue" not in query.casefold() for query in queries)
    assert response.json()["tools_used"] == tools
    assert response.json()["citations"] == [
        {"id": "W1700", "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"}
    ]


def test_exact_arxiv_recovery_opens_and_highlights_the_requested_paper(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canonical arXiv web hit must become a reader, not just prose."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()

    def handler(model: str, prompt: str) -> str:
        del model
        if prompt.startswith("Research request:"):
            return "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks arXiv"
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"the reader opened the source"}'
        if "User request:" in prompt:
            return '{"action":"answer"}'
        return "The original paper is open in the reader [ARXIV:2005.11401]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    calls: list[str] = []

    def fake_execute(tool: str, query: str, reason: str):
        calls.append(tool)
        if tool == "web_search":
            return (
                ToolStep(
                    tool=tool,
                    query=query,
                    reason=reason,
                    results=[
                        {
                            "title": "Retrieval-Augmented Generation for Knowledge-Intensive ...",
                            "url": "https://arxiv.org/abs/2005.11401",
                            "snippet": "# Title:Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks\n\nComments: Accepted at NeurIPS 2020",
                        }
                    ],
                ),
                [],
            )
        return (ToolStep(tool=tool, query=query, reason=reason, results=[]), [])

    opened: list[str] = []

    def fake_show(session, run, pool, work_id: str, focus: str, reason: str, acquirer=None):
        del session, run, pool, focus, acquirer
        opened.append(work_id)
        highlights = [{"page": 1, "quote": "We introduce RAG.", "note": "Core method"}]
        return (
            ToolStep(
                tool="show_paper",
                query=work_id,
                reason=reason,
                results=[{"id": work_id, "page_count": 12, "highlights": highlights}],
            ),
            {"kind": "paper", "work_id": work_id, "page_count": 12, "highlights": highlights},
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_execute)
    monkeypatch.setattr("sixsentences_server.chat.service._show_paper_step", fake_show)
    client = _authed(create_app(), email="arxiv-reader@example.org", org="Arxiv Reader")
    _grant_web_search_access(client)
    project = client.post("/projects", json={"name": "rag-reader"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "RAG", "query": "retrieval augmented generation"},
    ).json()["id"]
    response = client.post(
        f"/runs/{run_id}/chat",
        json={
            "question": "Suche im Web das originale Paper von Lewis 2020, zeig es rechts und markier die wichtigsten Stellen",
            "web_search_public_data_confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    assert calls == ["web_search", "find_papers", "find_papers"]
    assert opened == ["ARXIV:2005.11401"], response.json()
    assert response.json()["tools_used"] == [
        "web_search",
        "show_paper",
        "find_papers",
        "find_papers",
    ]
    history = client.get(f"/runs/{run_id}/chat").json()
    reader = next(
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "paper"
    )
    assert reader["payload"]["work_id"] == "ARXIV:2005.11401"
    assert reader["payload"]["highlights"][0]["quote"] == "We introduce RAG."


def test_terse_followup_opens_the_paper_named_in_the_previous_answer(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A definite follow-up must not search the literal words 'lade Paper'."""
    from sixsentences_server.core.db import ChatMessageRow, Run, WorkRow, db_session

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(_handler_answering("I opened the discussed paper.")),
    )

    def fail_search(*_args, **_kwargs):
        raise AssertionError("the discussed paper must open without a new search")

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fail_search)
    opened: list[str] = []

    def fake_show(session, run, pool, work_id, focus, reason, acquirer=None):
        del session, run, pool, focus, reason, acquirer
        opened.append(work_id)
        result = {
            "id": work_id,
            "title": "TerraMetrics: An Open Source Tool for IaC Quality Metrics",
            "document_id": 42,
            "page_count": 9,
            "highlights": [],
        }
        return (
            ToolStep(tool="show_paper", query=work_id, results=[result]),
            {
                "kind": "paper",
                "work_id": work_id,
                "document_id": 42,
                "page_count": 9,
                "highlights": [],
            },
        )

    monkeypatch.setattr("sixsentences_server.chat.service._show_paper_step", fake_show)
    client = _authed(create_app(), email="context-reader@example.org", org="Context Reader")
    project = client.post("/projects", json={"name": "terraform-reader"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "Was ist Terraform?", "query": "Terraform IaC"},
    ).json()["id"]
    with db_session() as session:
        session.add(
            WorkRow(
                id="W4399631545",
                title="TerraMetrics: An Open Source Tool for IaC Quality Metrics",
                year=2024,
                payload=WorkRecord(
                    id="W4399631545",
                    title="TerraMetrics: An Open Source Tool for IaC Quality Metrics",
                    year=2024,
                ).model_dump(mode="json"),
            )
        )
        run = session.get(Run, run_id)
        assert run is not None
        session.add(
            ChatMessageRow(
                org_id=run.org_id,
                run_id=run_id,
                role="assistant",
                content="Ein passendes aktuelles Paper ist [W4399631545] TerraMetrics: An Open Source Tool for IaC Quality Metrics.",
                citations=[],
                payload={},
            )
        )
    response = client.post(
        f"/runs/{run_id}/chat", json={"question": "Lade das Paper und zeig es mir mal an"}
    )
    assert response.status_code == 200, response.text
    assert opened == ["W4399631545"]
    assert response.json()["tools_used"] == ["show_paper"]


def test_completed_run_gets_a_closing_summary_message(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:

    def handler(model: str, prompt: str) -> str:
        if "PRISMA counts:" in prompt:
            return json.dumps(
                {
                    "summary": "Search finished. A work awaits eligibility review: [W1].",
                    "complete": True,
                }
            )
        return _handler_answering("Search finished. A work awaits eligibility review: [W1].")(
            model, prompt
        )

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    chat = client.get(f"/runs/{run_id}/chat").json()
    assert chat and chat[0]["role"] == "assistant"
    assert chat[0]["payload"]["kind"] == "completion_summary"
    assert chat[0]["citations"] == ["W1"]


def test_report_synthesis_cache_and_force(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    first = client.post(f"/runs/{run_id}/report", json={})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["report"]["prisma"]["records_identified"] == 1
    assert body["report"]["sections"]["executive_summary"]
    assert "PRISMA 2020" in body["report"]["methods"]
    assert "model" not in body
    assert "model" not in body["report"]
    again = client.post(f"/runs/{run_id}/report", json={})
    assert again.json()["id"] == body["id"]
    fresh = client.post(f"/runs/{run_id}/report", json={"force": True})
    assert fresh.json()["id"] != body["id"]
    got = client.get(f"/runs/{run_id}/report")
    assert got.status_code == 200 and got.json()["id"] == fresh.json()["id"]
    assert "model" not in got.json()
    assert "model" not in got.json()["report"]


def test_ask_mode_searches_the_web_and_attaches_a_requested_chart(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quick answer renders a chart only for an explicit data dimension."""
    from sixsentences_server.chat.service import ToolStep
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(
            _handler_answering("Terraform IaC is moving fast [W1] [example.org].")
        ),
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda question, primary_query, **kwargs: (
            [
                WorkRecord(id="W1", title="IaC with LLMs", year=2024),
                WorkRecord(id="W2", title="Terraform repair", year=2025),
            ],
            "test-source",
        ),
    )
    monkeypatch.setattr(
        "sixsentences_server.chat.service._execute_tool",
        lambda tool, query, reason: (
            ToolStep(
                tool="web_search",
                query=query,
                reason=reason,
                results=[
                    {
                        "title": "IaC trends",
                        "url": "https://example.org/iac",
                        "domain": "example.org",
                        "snippet": "…",
                        "category": "web",
                    }
                ],
            ),
            [],
        ),
    )
    client = _authed(create_app())
    _grant_web_search_access(client)
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Suche im Web nach Terraform IaC Entwicklungen und zeige die gefundenen Publikationen nach Jahr als Diagramm.",
            "mode": "ask",
            "web_search_public_data_confirmed": True,
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    history = client.get(f"/runs/{run_id}/chat").json()
    roles = [m["role"] for m in history]
    assert roles[-2:] == ["assistant", "tool"]
    search_tools = [
        message["payload"]["tool"]
        for message in history
        if message["role"] == "tool" and message["payload"]["tool"] in {"find_papers", "web_search"}
    ]
    assert len(search_tools) >= 3
    assert {"find_papers", "web_search"} <= set(search_tools)
    chart = history[-1]
    assert chart["payload"]["tool"] == "show_chart"
    assert chart["payload"]["kind"] == "ui"
    assert "works-by-year" in chart["payload"]["resource"]["uri"]


def test_ask_mode_comparison_builds_table_before_optional_paper_actions(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A requested evidence table is not misrouted to a generic chart."""

    def handler(model: str, prompt: str) -> str:
        if "Web search available:" in prompt and "Attached document:" in prompt:
            return '{"academic_search": true, "web_search": false, "search_query": "\\"LLM screening\\" AND reliability", "web_query": "", "chart": true, "cite": false, "show_paper": false, "save_paper": false, "compare_papers": true, "table": true, "table_columns": ["dataset", "human baseline", "limitation"]}'
        if "Columns to extract (besides Paper):" in prompt:
            return '{"columns":["Paper","dataset","human baseline","limitation"],"rows":[["Study A","Dataset A","Reviewer A","Small sample"],["Study B","Dataset B","Reviewer B","Single domain"]]}'
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        return "The structured comparison shows different evaluation settings [W1] [W2]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda question, primary_query, **kwargs: (
            [
                WorkRecord(
                    id="W1",
                    title="Study A",
                    abstract="Dataset A used Reviewer A. The sample was small.",
                    year=2025,
                ),
                WorkRecord(
                    id="W2",
                    title="Study B",
                    abstract="Dataset B used Reviewer B in a single domain.",
                    year=2025,
                ),
            ],
            "test-source",
        ),
    )
    client = _authed(create_app(), "comparison@example.org", "Comparison")
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Compare the two papers in a table with dataset, human baseline and limitation. Use only details in the available sources.",
            "mode": "ask",
        },
    ).json()["id"]
    history = client.get(f"/runs/{run_id}/chat").json()
    tools = [message["payload"]["tool"] for message in history if message["role"] == "tool"]
    assert tools == ["find_papers", "find_papers", "find_papers", "extract_data"]
    table = next(message for message in history if message["payload"].get("tool") == "extract_data")
    assert table["payload"]["kind"] == "ui"
    assert table["payload"]["table"]["title"] == "Evidence comparison"
    assert len(table["payload"]["table"]["rows"]) == 2
    assert "Evidence comparison" in table["payload"]["resource"]["text"]
    assert 'title="W1">' in table["payload"]["resource"]["text"]
    assert 'title="W2">' in table["payload"]["resource"]["text"]
    assert "show_chart" not in tools


def test_read_paper_step_returns_real_parsed_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading a paper means parsing the stored PDF, not returning metadata."""
    work = WorkRecord(id="W-FULLTEXT", title="A Full Text Study", year=2025)
    document = SimpleNamespace(id=77, checksum="c" * 64)
    monkeypatch.setattr(
        "sixsentences_server.chat.service._ensure_paper_document",
        lambda *_args, **_kwargs: (work, document, None, False),
    )
    monkeypatch.setattr(
        "sixsentences_server.chat.service.LocalDocumentStore",
        lambda _path: SimpleNamespace(get=lambda _checksum: b"%PDF-real-bytes"),
    )
    monkeypatch.setattr(
        "sixsentences_server.chat.service.extract_page_texts",
        lambda _content: ["Page one evidence", "Page two reports 38 ms and 2.1 GB"],
    )
    step, pages = _read_paper_step(
        SimpleNamespace(),
        SimpleNamespace(id=1, org_id=1),
        work.id,
        "the user asked to read the source",
    )
    assert step.status == "completed"
    assert step.results == [
        {
            "id": work.id,
            "title": work.title,
            "document_id": 77,
            "page_count": 2,
            "full_text_available": True,
        }
    ]
    assert pages == ["Page one evidence", "Page two reports 38 ms and 2.1 GB"]


def test_ask_mode_constrained_comparison_searches_broadly_and_reads_each_source(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Count, date, source type and reading wishes are end-to-end contracts."""
    del corpus
    formulated: list[str] = []

    def handler(model: str, prompt: str) -> str:
        del model
        if "Web search available:" in prompt and "Attached document:" in prompt:
            return '{"academic_search":true,"web_search":false,"search_query":"\\"efficient attention\\" AND (runtime OR memory)","web_query":"","chart":false,"cite":false,"show_paper":false,"save_paper":false,"compare_papers":true,"table":true,"compare_count":3,"table_columns":["runtime","memory","limitation"]}'
        if "Research request:" in prompt:
            query = (
                '"efficient attention" AND implementation AND 2024'
                if not formulated
                else '"efficient attention" AND benchmark AND memory AND 2024'
            )
            formulated.append(query)
            return query
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"reported on the cited PDF page"}'
        return "Three qualifying primary studies were found and their PDFs were read before comparison [W-EA-1] [W-EA-2] [W-EA-3]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    search_calls: list[tuple[str, int]] = []

    def fake_search(question: str, query: str, *, limit: int, **_kwargs):
        del question
        search_calls.append((query, limit))
        if len(search_calls) == 1:
            return (
                [
                    WorkRecord(
                        id="W-EA-1",
                        title="Efficient Attention Kernel Runtime Benchmarks",
                        abstract="Primary efficient attention runtime and memory evaluation.",
                        year=2024,
                        work_type="article",
                    ),
                    WorkRecord(
                        id="W-OLD",
                        title="Efficient Attention Survey",
                        abstract="A secondary review of efficient attention.",
                        year=2023,
                        work_type="review",
                    ),
                ],
                "test-source",
            )
        return (
            [
                WorkRecord(
                    id="W-EA-2",
                    title="Linear Efficient Attention Inference Evaluation",
                    abstract="Primary efficient attention inference runtime evaluation.",
                    year=2025,
                    work_type="article",
                ),
                WorkRecord(
                    id="W-EA-3",
                    title="Sparse Efficient Attention Memory Implementation",
                    abstract="Primary efficient attention memory and runtime benchmarks.",
                    year=2024,
                    work_type="article",
                ),
            ],
            "test-source",
        )

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", fake_search)
    read_calls: list[str] = []

    def fake_read(session, run, work_id: str, reason: str, acquirer=None):
        del session, run, reason, acquirer
        read_calls.append(work_id)
        index = len(read_calls)
        return (
            ToolStep(
                tool="read_paper",
                query=work_id,
                reason="read the requested primary source",
                results=[
                    {
                        "id": work_id,
                        "title": f"Study {index}",
                        "document_id": 100 + index,
                        "page_count": 2,
                        "full_text_available": True,
                    }
                ],
            ),
            [
                f"Methods for {work_id}",
                f"Results for {work_id}: runtime {20 + index} ms, memory {index}.2 GB.",
            ],
        )

    monkeypatch.setattr("sixsentences_server.pipeline.ask._read_paper_step", fake_read)
    extracted_evidence: dict[str, str] = {}

    def fake_extract(pool, works, columns, evidence, **_kwargs):
        del pool, columns
        extracted_evidence.update(evidence)
        return {
            "columns": ["Paper", "runtime", "memory", "limitation"],
            "rows": [
                [work.id, f"{21 + index} ms", f"{index + 1}.2 GB", "not reported"]
                for index, work in enumerate(works[:3])
            ],
        }

    monkeypatch.setattr("sixsentences_server.pipeline.ask._extract_data_table", fake_extract)
    client = _authed(create_app(), "primary-set@example.org", "Primary set")
    response = client.post(
        "/runs",
        json={
            "question": "Find three primary papers from 2024 on efficient attention, read the primary sources and compare only supported runtime and memory values.",
            "mode": "ask",
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    assert len(search_calls) == 3
    assert search_calls[0][0] != search_calls[1][0]
    assert all((limit >= 12 for _, limit in search_calls))
    assert set(read_calls) == {"W-EA-1", "W-EA-2", "W-EA-3"}
    assert set(extracted_evidence) == set(read_calls)
    assert all("Results for" in evidence for evidence in extracted_evidence.values())
    history = client.get(f"/runs/{run_id}/chat").json()
    tools = [message["payload"]["tool"] for message in history if message["role"] == "tool"]
    assert tools == [
        "find_papers",
        "find_papers",
        "find_papers",
        "read_paper",
        "read_paper",
        "read_paper",
        "extract_data",
    ]
    table = next(message for message in history if message["payload"].get("tool") == "extract_data")
    assert len(table["payload"]["table"]["rows"]) == 3
    events = client.get(f"/runs/{run_id}/events").json()
    reading = [event for event in events if event["event"] == "ask_source_reading"]
    assert len(reading) == 3
    assert [event["payload"]["work_id"] for event in reading] == read_calls


def test_quick_answer_honours_a_fifteen_paper_overview_end_to_end(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    del corpus

    def handler(model: str, prompt: str) -> str:
        del model
        if "Web search available:" in prompt and "Attached document:" in prompt:
            return '{"academic_search": true, "web_search": false, "search_query": "\\"large language models\\" AND advances", "web_query": "", "chart": false, "cite": false, "show_paper": false, "save_paper": false, "compare_papers": true, "table": true, "compare_count": 5, "table_columns": ["Title", "Year", "Approach", "Main finding", "Limitation"]}'
        if "The academic search returned" in prompt:
            return '{"enough": true, "refined_query": ""}'
        if "Columns to extract (besides Paper):" in prompt:
            ids = re.findall("\\[(W\\d+)\\]", prompt)
            return json.dumps(
                {
                    "rows": [
                        {
                            "work_id": work_id,
                            "relevance": "direct",
                            "values": {
                                "Approach": f"Approach {work_id}",
                                "Main finding": f"Finding {work_id}",
                                "Limitation": f"Limitation {work_id}",
                            },
                        }
                        for work_id in ids
                    ]
                }
            )
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        return "Die Tabelle enthält die 15 ausgewählten Paper."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda question, primary_query, **kwargs: (
            [
                WorkRecord(
                    id=f"W{20000 + index}",
                    title=f"Large language model advance {index}",
                    abstract=f"Approach and finding for paper {index}.",
                    year=2026 - index % 4,
                    cited_by_count=100 - index,
                )
                for index in range(20)
            ],
            "test-source",
        ),
    )
    client = _authed(create_app(), "fifteen@example.org", "Fifteen")
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Schau nach den 15 gerade wichtigsten Papern im LLM-Bereich und stelle sie übersichtlich dar.",
            "mode": "ask",
        },
    ).json()["id"]
    history = client.get(f"/runs/{run_id}/chat").json()
    search = next(
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("tool") == "find_papers"
    )
    table = next(
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("tool") == "extract_data"
    )
    assert len(search["payload"]["results"]) == 15
    assert len(table["payload"]["table"]["rows"]) == 15
    assert table["payload"]["results"][0]["selection"]["requested"] == 15
    assert table["payload"]["table"]["columns"] == [
        "Paper",
        "Year",
        "Approach",
        "Main finding",
        "Limitation",
    ]
    assert not any(
        message["payload"].get("tool") == "read_paper"
        for message in history
        if message["role"] == "tool"
    )


def test_move_run_between_projects(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    source = client.post("/projects", json={"name": "source"}).json()
    target = client.post("/projects", json={"name": "target"}).json()
    run_id = client.post(
        f"/projects/{source['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    client.patch(f"/runs/{run_id}", json={"title": "keep me"})
    moved = client.patch(f"/runs/{run_id}", json={"project_id": target["id"]})
    assert moved.status_code == 200
    assert moved.json()["project_id"] == target["id"]
    assert moved.json()["title"] == "keep me"
    assert client.patch(f"/runs/{run_id}", json={"project_id": 99999}).status_code == 404
    runs = client.get(f"/runs?project_id={target['id']}").json()
    assert [r["id"] for r in runs] == [run_id]


def test_report_refused_for_ask_conversations(corpus: DuckDBCorpus) -> None:
    from sqlalchemy import select

    from sixsentences_server.core.db import CapacityReservationRow, CreditEventRow, Run, db_session

    client = _authed(create_app())
    project = client.post("/projects", json={"name": "demo"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "quick one", "mode": "ask"}
    ).json()["id"]
    response = client.post(f"/runs/{run_id}/report", json={})
    assert response.status_code == 409
    assert "ask" in response.json()["detail"]
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run is not None and run.status == "failed"
        assert (
            session.scalars(
                select(CreditEventRow).where(
                    CreditEventRow.run_id == run.id, CreditEventRow.action == "question"
                )
            ).all()
            == []
        )
        reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id == run.config["cost_action_id"]
            )
        )
        assert reservation is not None
        assert reservation.status == "failed"
        assert reservation.remaining_credits == 0


def test_the_planner_reads_intent_where_keywords_fail(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'schau mal was es im netz dazu gibt' names no web keyword — the model
    routing step still sends the quick answer to the live web."""
    from sixsentences_server.chat.service import ToolStep
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    get_settings.cache_clear()

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if prompt.startswith("Research request:"):
            return "IaC security official evidence"
        if "Web search available:" in prompt:
            return '{"academic_search": true, "web_search": true, "search_query": "\\"infrastructure as code\\" AND security", "web_query": "IaC security current practice", "chart": false, "cite": false, "show_paper": false}'
        return "IaC security is active [W1] [example.org]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    searched_queries: list[str] = []

    def fake_search(question: str, primary_query: str, **kwargs):
        searched_queries.append(primary_query)
        return ([WorkRecord(id="W1", title="IaC security", year=2025)] * 4, "test-source")

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", fake_search)
    web_calls: list[str] = []

    def fake_tool(tool: str, query: str, reason: str):
        web_calls.append(query)
        return (
            ToolStep(
                tool="web_search",
                query=query,
                reason=reason,
                results=[
                    {
                        "title": "t",
                        "url": "https://example.org/x",
                        "domain": "example.org",
                        "snippet": "s",
                        "category": "web",
                    }
                ],
            ),
            [],
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_tool)
    client = _authed(create_app())
    _grant_web_search_access(client)
    run_id = client.post(
        "/runs",
        json={
            "question": "IaC security, schau mal was es im netz dazu gibt",
            "mode": "ask",
            "web_search_public_data_confirmed": True,
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    assert searched_queries[0] == '"infrastructure as code" AND security'
    assert len(searched_queries) == 2
    assert len(set(searched_queries)) == 2
    assert len(web_calls) == 1
    assert "iac" in web_calls[0].casefold()
    assert "security" in web_calls[0].casefold()
    assert web_calls[0] != "IaC security current practice"
    history = client.get(f"/runs/{run_id}/chat").json()
    tools = [m["payload"]["tool"] for m in history if m["role"] == "tool"]
    assert "web_search" in tools
    events = client.get(f"/runs/{run_id}/events").json()
    planned = next(e for e in events if e["event"] == "ask_planned")
    assert planned["payload"]["planner"] == "model"
    assert planned["payload"]["web_search"] is True


def test_explicit_official_sources_survive_a_planner_web_search_miss(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A compound request for scholarly and official sources must execute
    both evidence paths even when the routing model forgets the web arm."""
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    get_settings.cache_clear()

    def handler(model: str, prompt: str) -> str:
        if "Web search available:" in prompt:
            return '{"academic_search": true, "web_search": false, "search_query": "Terraform infrastructure as code", "web_query": "", "chart": false, "cite": false, "show_paper": false}'
        if prompt.startswith("Research request:"):
            return "Terraform official documentation infrastructure as code"
        if "User request:" in prompt:
            return '{"action":"answer"}'
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"ok"}'
        return "Terraform is an infrastructure-as-code system [W1] [developer.hashicorp.com]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    monkeypatch.setattr(
        "sixsentences_server.pipeline.ask._quick_search",
        lambda *args, **kwargs: (
            [WorkRecord(id="W1", title="Terraform in infrastructure operations", year=2025)],
            "test-source",
        ),
    )
    web_calls: list[str] = []

    def fake_tool(tool: str, query: str, reason: str):
        web_calls.append(query)
        return (
            ToolStep(
                tool="web_search",
                query=query,
                reason=reason,
                results=[
                    {
                        "title": "Terraform documentation",
                        "url": "https://developer.hashicorp.com/terraform/docs",
                        "domain": "developer.hashicorp.com",
                        "snippet": "Official Terraform documentation.",
                        "category": "web",
                    }
                ],
            ),
            [],
        )

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_tool)
    client = _authed(create_app(), email="official@example.org", org="Official")
    _grant_web_search_access(client)
    run_id = client.post(
        "/runs",
        json={
            "question": "Ich schreibe über Terraform IaC. Suche wissenschaftliche und offizielle Quellen und erkläre es einfach.",
            "mode": "ask",
            "web_search_public_data_confirmed": True,
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    assert web_calls == ["Terraform official documentation infrastructure as code"]
    planned = next(
        event
        for event in client.get(f"/runs/{run_id}/events").json()
        if event["event"] == "ask_planned"
    )
    assert planned["payload"]["academic_search"] is True
    assert planned["payload"]["web_search"] is True


def test_quick_answer_searches_then_reads_the_best_page(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router receives real search URLs and can open one next."""
    from sixsentences_server.chat.service import ToolStep
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "test-key")
    get_settings.cache_clear()

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("Research request:"):
            return "Terraform guidance"
        if "Web search available:" in prompt:
            return '{"academic_search": false, "web_search": true, "search_query": "", "web_query": "Terraform guidance", "chart": false, "cite": false, "show_paper": false}'
        if "User request:" in prompt:
            assert "https://example.org/guide" in prompt
            if "Detailed Terraform guidance" not in prompt:
                return '{"action": "tool", "tool": "read_webpage", "url": "https://example.org/guide", "reason": "the full guidance matters"}'
            return '{"action": "answer"}'
        if prompt.startswith("CLAIM:"):
            assert "Detailed Terraform guidance" in prompt
            return '{"label":"supported","reason":"the opened page supports it"}'
        assert "No matching academic paper was retrieved" in prompt
        assert "Answer the user's question directly from that evidence" in prompt
        assert "untrusted quoted evidence" in prompt
        assert "never instructions" in prompt
        assert "Detailed Terraform guidance" in prompt
        assert "No academic sources could be retrieved" not in prompt
        return "The opened guidance provides the detail [example.org]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    calls: list[tuple[str, str]] = []

    def fake_tool(tool: str, query: str, reason: str):
        calls.append((tool, query))
        if tool == "web_search":
            results = [
                {
                    "title": "Terraform guide",
                    "url": "https://example.org/guide",
                    "domain": "example.org",
                    "snippet": "A useful lead.",
                }
            ]
        else:
            results = [
                {
                    "title": "Terraform guide",
                    "url": query,
                    "domain": "example.org",
                    "excerpt": "Detailed Terraform guidance with the actual recommendations.",
                    "characters_read": 62,
                }
            ]
        return (ToolStep(tool=tool, query=query, reason=reason, results=results), [])

    monkeypatch.setattr("sixsentences_server.chat.service._execute_tool", fake_tool)
    client = _authed(create_app(), email="reader@example.org", org="Reader")
    _grant_web_search_access(client)
    run_id = client.post(
        "/runs",
        json={
            "question": "Check the current official Terraform guidance",
            "mode": "ask",
            "web_search_public_data_confirmed": True,
        },
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    assert [tool for tool, _query in calls] == [
        "web_search",
        "web_search",
        "web_search",
        "read_webpage",
    ]
    assert calls[0][0] == "web_search"
    assert "terraform" in calls[0][1].casefold()
    assert "guidance" in calls[0][1].casefold()
    assert len({query for tool, query in calls if tool == "web_search"}) == 3
    assert calls[-1] == ("read_webpage", "https://example.org/guide")
    history = client.get(f"/runs/{run_id}/chat").json()
    agent_rows = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("kind") == "agent_work"
    ]
    assert agent_rows[0]["payload"]["agent_event"]["event"] == "plan.created"
    assert any(
        row["payload"]["agent_event"]["event"] == "checkpoint.completed" for row in agent_rows
    )
    tool_rows = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("tool") != "agent_update"
    ]
    assert [row["payload"]["tool"] for row in tool_rows] == [
        "web_search",
        "web_search",
        "web_search",
        "read_webpage",
    ]
    assert [row["payload"]["iteration"] for row in tool_rows] == [1, 2, 3, 4]
    answer = next(message for message in history if message["role"] == "assistant")
    assert answer["content"] == "The opened guidance provides the detail [example.org]."
    assert answer["payload"]["sources_considered"] == 0
    assert answer["payload"]["web_sources_considered"] == 4


def test_a_thin_first_search_triggers_one_visible_refined_pass(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent reflects after a thin retrieval: one refined query from a
    different angle, logged as a find_papers tool step — then it answers."""

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label": "supported", "reason": "ok"}'
        if "independent search angle 2" in prompt:
            return '"paper mill" OR "research integrity"'
        if "independent search angle 3" in prompt:
            return '"tortured phrases" AND detection benchmark'
        if "The academic search returned" in prompt:
            return '{"enough": true, "refined_query": ""}'
        if "Web search available:" in prompt:
            return '{"academic_search": true, "web_search": false, "search_query": "\\"tortured phrases\\"", "web_query": "", "chart": false, "cite": false, "show_paper": false}'
        return "Little is published [W7]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    calls: list[str] = []

    def fake_search(question: str, primary_query: str, **kwargs):
        calls.append(primary_query)
        if len(calls) == 1:
            return ([], "none")
        return ([WorkRecord(id="W7", title="Paper mills", year=2024)], "test-source")

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", fake_search)
    client = _authed(create_app())
    run_id = client.post(
        "/runs", json={"question": "tortured phrases forschung?", "mode": "ask"}
    ).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    assert calls == [
        '"tortured phrases"',
        '"paper mill" OR "research integrity"',
        '"tortured phrases" AND detection benchmark',
    ]
    history = client.get(f"/runs/{run_id}/chat").json()
    retrieval_steps = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"]["tool"] == "find_papers"
    ]
    assert len(retrieval_steps) == 3
    assert any(
        result["id"] == "W7"
        for retry in retrieval_steps[1:]
        for result in retry["payload"]["results"]
    )
    events = client.get(f"/runs/{run_id}/events").json()
    done = next(e for e in events if e["event"] == "ask_retrieval_done")
    assert done["payload"]["refined_round"] is True
    assert client.get(f"/runs/{run_id}/chat").json()[-1]["citations"] == ["W7"]


def test_explicit_paper_request_runs_distinct_searches_until_title_match(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = "Was ist Terraform im IaC Bereich genau? Schau mal nach, gibts da nicht auch ein Ppaer dazu?"

    def handler(model: str, prompt: str) -> str:
        if prompt.startswith("CLAIM:"):
            return '{"label":"supported","reason":"the title is in the evidence"}'
        if "Web search available:" in prompt:
            return '{"academic_search":true,"web_search":false,"search_query":"infrastructure as code Terraform concept","web_query":"","chart":false,"cite":false,"show_paper":false}'
        return "A primary Terraform paper was located [W999]."

    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool", lambda settings: mock_pool(handler)
    )
    calls: list[str] = []

    def fake_search(user_question: str, primary_query: str, **kwargs):
        calls.append(primary_query)
        if len(calls) == 1:
            return (
                [
                    WorkRecord(id=f"W-{index}", title=f"Cloud operations study {index}")
                    for index in range(8)
                ],
                "test-source",
            )
        return (
            [
                WorkRecord(
                    id="W999",
                    title="Terraform: Automating Infrastructure as a Service",
                    year=2022,
                    abstract="Terraform infrastructure-as-code workflows.",
                )
            ],
            "test-source",
        )

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", fake_search)
    client = _authed(create_app(), email="terraform@example.org", org="Terraform")
    response = client.post("/runs", json={"question": question, "mode": "ask"})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    assert len(calls) == 3
    assert calls[0] == "infrastructure as code Terraform concept"
    assert len(set(calls)) == 3
    history = client.get(f"/runs/{run_id}/chat").json()
    searches = [
        message
        for message in history
        if message["role"] == "tool" and message["payload"].get("tool") == "find_papers"
    ]
    assert len(searches) == 3
    assert any(
        result["id"] == "W999" for search in searches for result in search["payload"]["results"]
    )
    events = client.get(f"/runs/{run_id}/events").json()
    queued = next(event for event in events if event["event"] == "ask_queued")
    assert (
        queued["payload"]["label"] == "Planning a focused multi-pass search for the requested paper"
    )
    answer = next(message for message in reversed(history) if message["role"] == "assistant")
    assert answer["citations"] == ["W999"]


def test_ask_mode_answers_capability_questions_without_searching(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Was kannst du?' from the home composer is a product question: the
    quick-answer pipeline skips retrieval entirely and answers from the
    self-knowledge, with zero sources considered."""
    searched = {"any": False}

    def no_search(question: str, primary_query: str):
        searched["any"] = True
        return ([], "test-source")

    monkeypatch.setattr("sixsentences_server.pipeline.ask._quick_search", no_search)
    monkeypatch.setattr(
        "sixsentences_server.api.app._build_pool",
        lambda settings: mock_pool(
            _handler_answering(
                "SixSentences_ runs audited searches, screens papers, writes LaTeX and draws figures."
            )
        ),
    )
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "cap-ask"}).json()
    response = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "Was kannst du als System alles?", "mode": "ask"},
    )
    assert response.status_code == 202
    run = client.get(f"/runs/{response.json()['id']}").json()
    assert run["status"] == "completed"
    assert searched["any"] is False
    chat = client.get(f"/runs/{run['id']}/chat").json()
    assert chat[-1]["role"] == "assistant"
    assert "audited searches" in chat[-1]["content"]
    assert chat[-1]["payload"]["sources_considered"] == 0
