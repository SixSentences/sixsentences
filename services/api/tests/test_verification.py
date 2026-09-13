"""Claim-support firewall: entailment engine, claim verification, chat wiring."""

import pytest
from sqlalchemy import select

from sixsentences_server.acquisition.store import LocalDocumentStore
from sixsentences_server.chat.service import (
    _VERIFY_CLAIM_ASK,
    _claim_text_for_verification,
    _evidence_texts,
    _web_evidence_texts,
    answer_question,
)
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.db import (
    ChatMessageRow,
    DocumentRow,
    LLMCallRow,
    Run,
    WorkRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.pipeline.run import execute_run
from sixsentences_server.verification.claims import split_claims, verify_answer
from sixsentences_server.verification.nli import (
    EVIDENCE_CHARS,
    EntailmentVerdict,
    LLMEntailmentChecker,
    Support,
)


def test_compound_request_extracts_only_the_quoted_claim() -> None:
    question = "Erstelle rechts eine Vergleichstabelle. Prüfe danach die Aussage „LLMs ersetzen menschliches Screening ohne Qualitätsverlust vollständig“ und öffne den Claim Audit ebenfalls rechts."
    assert _VERIFY_CLAIM_ASK.search(question)
    assert (
        _claim_text_for_verification(question)
        == "LLMs ersetzen menschliches Screening ohne Qualitätsverlust vollständig"
    )


class _FakeChecker:
    def __init__(self, label: Support) -> None:
        self.label = label

    def check(self, claim: str, evidence: str) -> EntailmentVerdict:
        return EntailmentVerdict(self.label, "fake")


class _BatchChecker:
    def __init__(self) -> None:
        self.batches: list[list[tuple[str, str]]] = []

    def check(self, claim: str, evidence: str) -> EntailmentVerdict:
        raise AssertionError("three or more claims should use the bounded batch path")

    def check_many(self, items: list[tuple[str, str]]) -> list[EntailmentVerdict]:
        self.batches.append(items)
        return [EntailmentVerdict(Support.SUPPORTED, "batched") for _ in items]


def test_llm_entailment_reads_the_label() -> None:
    pool = mock_pool(lambda m, p: '{"label":"supported","reason":"clearly entailed"}')
    assert LLMEntailmentChecker(pool).check("c", "e").label is Support.SUPPORTED


def test_llm_entailment_malformed_is_neutral_never_supported() -> None:
    pool = mock_pool(lambda m, p: "not json at all")
    assert LLMEntailmentChecker(pool).check("c", "e").label is Support.NEUTRAL


def test_llm_entailment_provider_failure_is_neutral_and_does_not_sink_answer() -> None:

    def unavailable(model: str, prompt: str) -> str:
        raise ProviderError("temporary outage")

    verdict = LLMEntailmentChecker(mock_pool(unavailable)).check("c", "e")
    assert verdict.label is Support.NEUTRAL
    assert verdict.reason == "verifier unavailable"


def test_llm_entailment_batches_claims_with_separate_evidence() -> None:
    calls: list[str] = []

    def handler(model: str, prompt: str) -> str:
        calls.append(prompt)
        return '{"verdicts":[{"index":0,"label":"supported","reason":"first"},{"index":1,"label":"unsupported","reason":"second"},{"index":2,"label":"neutral","reason":"third"}]}'

    verdicts = LLMEntailmentChecker(mock_pool(handler)).check_many(
        [("claim one", "evidence one"), ("claim two", "evidence two"), ("claim three", "")]
    )
    assert len(calls) == 1
    assert [verdict.label for verdict in verdicts] == [
        Support.SUPPORTED,
        Support.UNSUPPORTED,
        Support.NEUTRAL,
    ]
    assert all(f'"index": {index}' in calls[0] for index in range(3))


def test_split_claims_keeps_sentences_drops_fragments() -> None:
    claims = split_claims("Transformers use attention [W1]. Short. A second real claim here [W2].")
    assert len(claims) == 2


def test_literal_documentation_urls_stay_with_their_own_list_citations() -> None:
    plan = "https://developer.hashicorp.com/terraform/cli/commands/plan"
    apply = "https://developer.hashicorp.com/terraform/cli/commands/apply"
    plan_key, apply_key = ("web:1111111111111111", "web:2222222222222222")
    answer = f"The two official HashiCorp documentation pages are:\n\n- terraform plan command reference: {plan} [{plan_key}]\n- terraform apply command reference: {apply} [{apply_key}]\n\nTerraform plan previews proposed changes [{plan_key}], whereas terraform apply executes those changes [{apply_key}]."
    report = verify_answer(
        answer,
        {plan_key: "Plan evidence", apply_key: "Apply evidence"},
        _FakeChecker(Support.NEUTRAL),
    )
    assert report.checked == 3
    assert report.flagged == 3
    assert report.verdicts[0].claim == f"- terraform plan command reference: {plan} [{plan_key}]"
    assert report.verdicts[0].evidence_ids == [plan_key]
    assert report.verdicts[1].claim == f"- terraform apply command reference: {apply} [{apply_key}]"
    assert report.verdicts[1].evidence_ids == [apply_key]
    assert report.verdicts[2].evidence_ids == [plan_key, apply_key]


def test_url_periods_do_not_split_but_prose_full_stops_still_do() -> None:
    answer = "Reference: https://docs.example.org/guide.v2?q=state.value [W1]. This is a separate supported claim [W2]."
    assert split_claims(answer) == [
        "Reference: https://docs.example.org/guide.v2?q=state.value [W1].",
        "This is a separate supported claim [W2].",
    ]


def test_numbered_source_list_keeps_unpunctuated_items_separate() -> None:
    answer = "1. First documentation reference https://docs.example.org/first [W1]\n2. Second documentation reference https://docs.example.org/second [W2]"
    claims = split_claims(answer)
    assert len(claims) == 2
    assert "[W1]" in claims[0] and "[W2]" not in claims[0]
    assert "[W2]" in claims[1] and "[W1]" not in claims[1]


def test_citations_after_full_stops_belong_to_the_preceding_claim() -> None:
    answer = (
        "Planning previews the proposed changes. [W1] Applying executes the proposed changes. [W2]"
    )
    report = verify_answer(
        answer, {"W1": "Plan evidence", "W2": "Apply evidence"}, _FakeChecker(Support.NEUTRAL)
    )
    assert report.checked == 2
    assert report.verdicts[0].claim == "Planning previews the proposed changes. [W1]"
    assert report.verdicts[0].evidence_ids == ["W1"]
    assert report.verdicts[1].claim == "Applying executes the proposed changes. [W2]"
    assert report.verdicts[1].evidence_ids == ["W2"]


def test_multi_source_evidence_budget_keeps_later_relevant_sources() -> None:
    premises: list[str] = []
    relevant = "The apply command executes the planned infrastructure changes."

    class Checker:
        def check(self, claim: str, evidence: str) -> EntailmentVerdict:
            premises.append(evidence)
            return EntailmentVerdict(Support.NEUTRAL, "uncertain")

    report = verify_answer(
        "The commands differ in previewing and executing proposed changes [W1] [W2].",
        {"W1": "Long first source " * 1000, "W2": relevant},
        Checker(),
    )
    assert len(premises) == 1 and len(premises[0]) <= EVIDENCE_CHARS
    assert "[W1]\nLong first source" in premises[0]
    assert f"[W2]\n{relevant}" in premises[0]
    assert report.verdicts[0].evidence_ids == ["W1", "W2"]
    assert report.flagged == 1


def test_multi_source_budget_includes_headers_and_separators() -> None:
    from sixsentences_server.verification.claims import _claim_evidence

    ids = [f"web:{index:016x}" for index in range(3)]
    evidence = _claim_evidence(ids, {source_id: "x" * EVIDENCE_CHARS for source_id in ids})
    assert len(evidence) == EVIDENCE_CHARS
    for source_id in ids:
        assert f"[{source_id}]\n" in evidence


def test_verify_answer_checks_only_cited_sentences() -> None:
    answer = "Transformers rely on self-attention [W1]. This framing sentence cites nothing and is skipped. Attention scales quadratically with length [W2]."
    evidence = {"W1": "The transformer is based on self-attention.", "W2": "Attention is O(n^2)."}
    report = verify_answer(answer, evidence, _FakeChecker(Support.SUPPORTED))
    assert report.checked == 2
    assert report.supported == 2 and report.flagged == 0


def test_verify_answer_flags_unsupported() -> None:
    report = verify_answer(
        "A fabricated finding [W1].", {"W1": "unrelated text"}, _FakeChecker(Support.UNSUPPORTED)
    )
    assert report.flagged == 1 and report.supported == 0


def test_verify_answer_checks_exact_web_domain_citations() -> None:
    report = verify_answer(
        "The official guide documents this behavior [Docs.Example.Org].",
        {"docs.example.org": "The official guide documents this behavior."},
        _FakeChecker(Support.SUPPORTED),
    )
    assert report.checked == 1
    assert report.verdicts[0].evidence_ids == ["docs.example.org"]


def test_verify_answer_ignores_unknown_bracket_tokens() -> None:
    report = verify_answer(
        "This claim cites an unavailable page [unknown.example].",
        {"docs.example.org": "Different evidence."},
        _FakeChecker(Support.SUPPORTED),
    )
    assert report.checked == 0


@pytest.mark.parametrize(
    "answer",
    [
        "I opened the paper in the reader here [W1].",
        "Ich habe das Paper hier rechts geöffnet [W1].",
        "Ich habe die Quelle in der Bibliothek gespeichert [W1].",
    ],
)
def test_verify_answer_does_not_fact_check_trusted_ui_actions(answer: str) -> None:
    report = verify_answer(
        answer,
        {"W1": "The paper describes a transformer architecture."},
        _FakeChecker(Support.UNSUPPORTED),
    )
    assert report.checked == 0


def test_verify_answer_batches_three_or_more_cited_claims() -> None:
    checker = _BatchChecker()
    report = verify_answer(
        "First supported finding has enough text [W1]. Second supported finding has enough text [W2]. Third supported finding has enough text [W3].",
        {"W1": "first evidence", "W2": "second evidence", "W3": "third evidence"},
        checker,
    )
    assert report.checked == 3
    assert report.supported == 3
    assert len(checker.batches) == 1
    assert [evidence for _claim, evidence in checker.batches[0]] == [
        "first evidence",
        "second evidence",
        "third evidence",
    ]


def test_web_evidence_prefers_read_pages_but_keeps_search_context() -> None:
    from sixsentences_server.chat.service import ToolStep

    evidence = _web_evidence_texts(
        [
            ToolStep(
                tool="web_search",
                query="guide",
                results=[
                    {"domain": "docs.example.org", "title": "Guide", "snippet": "Search summary"}
                ],
            ),
            ToolStep(
                tool="read_webpage",
                query="https://docs.example.org/guide",
                results=[
                    {
                        "domain": "docs.example.org",
                        "title": "Guide",
                        "excerpt": "The complete readable passage.",
                    }
                ],
            ),
        ]
    )
    assert "Search summary" in evidence["docs.example.org"]
    assert "complete readable passage" in evidence["docs.example.org"]


def test_chat_verifies_cited_claims(corpus: DuckDBCorpus) -> None:
    init_db()

    def handler(model: str, prompt: str) -> str:
        if "CLAIM:" in prompt:
            return (
                '{"label":"unsupported","reason":"date not in source"}'
                if "1990" in prompt
                else '{"label":"supported","reason":"entailed"}'
            )
        if "Sources" in prompt:
            return "Transformers rely on self-attention [W1]. They were invented in 1990 [W1]."
        if "Existing queries" in prompt:
            return '{"queries": []}'
        return '{"inclusion_criteria": [], "exclusion_criteria": [], "query_string": "transformer"}'

    pool = mock_pool(handler)
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="t")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="transformers", status="pending")
        session.add(run)
        session.flush()
        execute_run(
            session, run, corpus=corpus, pool=pool, query_override="transformer OR learning"
        )
        answer = answer_question(session, run, pool, "How do transformers work?")
        assert answer.claims_checked == 2
        assert answer.claims_supported == 1
        assert answer.claims_flagged == 1
        flagged = [c for c in answer.claim_checks if c.support != "supported"]
        assert flagged and "1990" in flagged[0].claim
        vcalls = session.scalars(
            select(LLMCallRow).where(
                LLMCallRow.run_id == run.id, LLMCallRow.task == "claim_verification"
            )
        ).all()
        assert vcalls


def test_nli_evidence_prefers_full_text_over_abstract(settings: Settings) -> None:
    init_db()
    store = LocalDocumentStore(get_settings().documents_dir)
    store.put_text("ck", "The full text reports a benchmark of exactly 1336 curated samples.")
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="p")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="q", status="running")
        session.add(run)
        session.add(WorkRow(id="W1", title="A study", year=2024))
        session.flush()
        session.add(
            DocumentRow(
                org_id=org.id,
                run_id=run.id,
                work_id="W1",
                status="retrieved",
                text_status="parsed",
                checksum="ck",
                byte_size=1,
            )
        )
        session.flush()
        works = [
            WorkRecord(id="W1", title="A study", abstract="a short abstract with no numbers"),
            WorkRecord(id="W2", title="Other", abstract="only an abstract, never acquired"),
        ]
        evidence = _evidence_texts(session, run.id, works)
        assert "1336 curated samples" in evidence["W1"]
    assert evidence["W2"] == "Other. only an abstract, never acquired"


def test_explicit_verify_claim_runs_a_visible_evidence_audit(
    settings: Settings, monkeypatch
) -> None:
    init_db()
    monkeypatch.setattr(
        "sixsentences_server.chat.service.OpenAlexClient.search", lambda *args, **kwargs: []
    )

    def handler(model: str, prompt: str) -> str:
        if "CLAIM:\n" in prompt and "SOURCES:\n" in prompt:
            return '{"verdict":"supported","confidence":"high","rationale":"The source directly states the mechanism.","evidence":[{"work_id":"W1","stance":"supports","reason":"The abstract identifies self-attention."}]}'
        if "CLAIM:\n" in prompt and "EVIDENCE:\n" in prompt:
            return '{"label":"supported","reason":"entailed"}'
        if "User request:" in prompt:
            return '{"action":"answer"}'
        return "The claim is supported by the available source [W1]."

    pool = mock_pool(handler)
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="verify")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="transformers", status="completed")
        session.add(run)
        session.add(
            WorkRow(
                id="W1",
                title="Attention is all you need",
                year=2017,
                payload=WorkRecord(
                    id="W1",
                    title="Attention is all you need",
                    abstract="The transformer is based on self-attention.",
                    year=2017,
                ).model_dump(),
            )
        )
        session.flush()
        from sixsentences_server.core.db import SourceRecordRow

        session.add(SourceRecordRow(org_id=org.id, run_id=run.id, work_id="W1", source="fixture"))
        session.flush()
        answer = answer_question(
            session, run, pool, "Verify the claim that transformers rely on self-attention."
        )
        assert "verify_claim" in answer.tools_used
        tool_rows = list(
            session.scalars(
                select(ChatMessageRow)
                .where(ChatMessageRow.run_id == run.id, ChatMessageRow.role == "tool")
                .order_by(ChatMessageRow.id)
            )
        )
        assert tool_rows[0].payload["kind"] == "tool_lifecycle"
        assert tool_rows[0].payload["lifecycle"] == "started"
        tool = next(row for row in tool_rows if row.payload.get("kind") == "claim_verification")
        assert tool.payload["started_message_id"] == tool_rows[0].id
        assert tool.payload["kind"] == "claim_verification"
        assert tool.payload["results"][0]["verdict"] == "supported"
