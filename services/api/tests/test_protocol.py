"""Protocol synthesis heuristic: distinctive terms, not verb salads."""

from sixsentences_server.core.models import ReviewProtocol
from sixsentences_server.core.protocol import _heuristic_query, query_shape_ok, synthesize_protocol
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.pipeline.expansion import propose_variants


def test_heuristic_keeps_acronyms_and_drops_verbs() -> None:
    query = _heuristic_query("IaC Code generation using LLMs and evaluation of generated Code")
    terms = query.split(" AND ")
    assert "iac" in terms and "llms" in terms  # the topic-defining acronyms
    assert "using" not in terms and "used" not in terms
    assert len(terms) <= 3  # recall-safe: few AND clauses


def test_heuristic_deduplicates_word_forms() -> None:
    query = _heuristic_query("evaluation and evaluating generated generation pipelines")
    terms = query.split(" AND ")
    assert len(terms) == len({t[:6] for t in terms})  # no generation+generated pair


def test_synthesize_without_pool_uses_heuristic() -> None:
    protocol = synthesize_protocol("transformer attention mechanisms")
    assert protocol.synthesized_by == "heuristic"
    assert "transformer" in protocol.query_string


def test_heuristic_ignores_trend_question_filler() -> None:
    query = _heuristic_query("What is currently happening in Infrastructure as Code research?")
    terms = query.split(" AND ")
    assert "currently" not in terms and "happening" not in terms
    assert "infrastructure" in terms


def test_query_shape_budget() -> None:
    assert query_shape_ok('("a b" OR c) AND (d OR e) AND (f OR g)')  # 3 groups is fine
    assert not query_shape_ok(" AND ".join(f"t{i}" for i in range(5)))  # too many groups
    assert not query_shape_ok("(" + " OR ".join(["term"] * 80) + ")")  # too long


def test_protocol_synthesis_falls_back_when_strong_route_is_unavailable() -> None:
    def handler(model: str, prompt: str) -> str:
        if model == "mock-strong":
            raise ProviderError("strong route unavailable")
        return (
            '{"inclusion_criteria":["evaluates screening"],'
            '"exclusion_criteria":["not a literature review"],'
            '"query_string":"screening AND literature"}'
        )

    protocol = synthesize_protocol("LLM screening", mock_pool(handler))

    assert protocol.query_string == "screening AND literature"
    assert protocol.synthesized_by == "mock:mock-cheap-0"


def test_query_expansion_falls_back_when_strong_route_is_unavailable() -> None:
    def handler(model: str, prompt: str) -> str:
        if model == "mock-strong":
            raise ProviderError("strong route unavailable")
        return '{"queries":["screening OR review"]}'

    variants = propose_variants(
        ReviewProtocol(question="q", query_string="screening"),
        ["screening"],
        [],
        mock_pool(handler),
    )

    assert variants == ["screening OR review"]
