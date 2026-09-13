"""Ensemble screening decision rules (recall stage + adjudication + guards)."""

from sixsentences_server.core.models import ReviewProtocol, Verdict, WorkRecord
from sixsentences_server.llm.base import ModelRef
from sixsentences_server.llm.mock import MockClient, mock_pool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.screening.ensemble import screen_ensemble

PROTOCOL = ReviewProtocol(question="q", query_string="q")
WORK = WorkRecord(
    id="W1",
    title="Systematic screening paper",
    abstract="A relevant research abstract.",
)


def _vote(verdict: str, quote: str = "Systematic screening paper") -> str:
    return f'{{"verdict": "{verdict}", "reason": "r", "quote": "{quote}"}}'


def test_or_rule_any_include_wins_without_conflict() -> None:
    pool = mock_pool(lambda model, prompt: _vote("include"), screening_models=3)
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())
    assert outcome.final.verdict is Verdict.INCLUDE
    assert not outcome.adjudicated
    assert len(outcome.votes) == 3


def test_conflict_goes_to_adjudicator() -> None:
    def handler(model: str, prompt: str) -> str:
        if "Distinct model reviewer votes" in prompt:  # adjudication call
            return _vote("exclude")
        return _vote("include") if model.endswith("0") else _vote("exclude")

    pool = mock_pool(handler, screening_models=2)
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())
    assert outcome.adjudicated
    assert outcome.final.verdict is Verdict.EXCLUDE
    assert outcome.final.reviewer.startswith("adjudicator:")


def test_conflict_falls_back_when_strong_route_is_unavailable() -> None:
    def handler(model: str, prompt: str) -> str:
        if "Distinct model reviewer votes" in prompt:
            if model == "mock-strong":
                raise ProviderError("strong route unavailable")
            return _vote("include")
        return _vote("include") if model.endswith("0") else _vote("exclude")

    pool = mock_pool(handler, screening_models=2)
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())

    assert outcome.adjudicated
    assert outcome.final.verdict is Verdict.INCLUDE
    assert outcome.final.reviewer == "adjudicator:mock:mock-cheap-0"


def test_conflict_is_conservatively_retained_when_all_adjudicators_fail() -> None:
    def handler(model: str, prompt: str) -> str:
        if "Distinct model reviewer votes" in prompt:
            raise ProviderError("route unavailable")
        return _vote("include") if model.endswith("0") else _vote("exclude")

    pool = mock_pool(handler, screening_models=2)
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())

    assert outcome.final.verdict is Verdict.INCLUDE
    assert outcome.final.reviewer == "adjudicator:fallback"


def test_unanimous_exclude_needs_two_reviewers() -> None:
    pool = mock_pool(lambda model, prompt: _vote("exclude"), screening_models=2)
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())
    assert outcome.final.verdict is Verdict.EXCLUDE
    assert not outcome.adjudicated


def test_unverified_unanimous_exclude_advances_as_unsure() -> None:
    pool = mock_pool(lambda model, prompt: _vote("exclude", ""), screening_models=2)
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())

    assert outcome.final.verdict is Verdict.UNSURE
    assert "not supported" in outcome.final.reason


def test_single_model_exclude_downgrades_to_unsure() -> None:
    pool = mock_pool(lambda model, prompt: _vote("exclude"), screening_models=1)
    # remove the strong route so no adjudication can rescue the policy check
    pool.routing.adjudication = None
    pool.routing.synthesis = None
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())
    assert outcome.final.verdict is Verdict.UNSURE
    assert "single-model exclude" in outcome.final.reason


def test_malformed_response_degrades_to_unsure() -> None:
    pool = mock_pool(lambda model, prompt: "not json at all", screening_models=2)
    pool.routing.adjudication = None
    pool.routing.synthesis = None
    outcome = screen_ensemble(WORK, PROTOCOL, pool, pool.screening_refs())
    assert outcome.final.verdict is Verdict.UNSURE


def test_duplicate_model_refs_cannot_establish_unanimous_exclusion() -> None:
    pool = mock_pool(lambda model, prompt: _vote("exclude"), screening_models=1)
    ref = pool.screening_refs()[0]
    duplicate = ModelRef(provider=ref.provider, model=ref.model)

    outcome = screen_ensemble(WORK, PROTOCOL, pool, [ref, duplicate, ref])

    assert outcome.final.verdict is Verdict.UNSURE
    assert "single-model exclude" in outcome.final.reason
    assert len(outcome.votes) == 1
    assert not outcome.adjudicated
    client = pool.clients["mock"]
    assert isinstance(client, MockClient)
    assert len(client.calls) == 1


def test_duplicate_refs_do_not_inflate_distinct_reviewer_votes() -> None:
    pool = mock_pool(lambda model, prompt: _vote("exclude"), screening_models=2)
    first, second = pool.screening_refs()

    outcome = screen_ensemble(WORK, PROTOCOL, pool, [first, second, first, second])

    assert outcome.final.verdict is Verdict.EXCLUDE
    assert [vote.reviewer for vote in outcome.votes] == [str(first), str(second)]
    client = pool.clients["mock"]
    assert isinstance(client, MockClient)
    assert len(client.calls) == 2


def test_failed_distinct_reviewer_leaves_only_one_exclusion_vote() -> None:
    def handler(model: str, prompt: str) -> str:
        if model.endswith("1"):
            raise ProviderError("reviewer unavailable")
        return _vote("exclude")

    pool = mock_pool(handler, screening_models=2)
    first, second = pool.screening_refs()

    outcome = screen_ensemble(WORK, PROTOCOL, pool, [first, first, second])

    assert outcome.final.verdict is Verdict.UNSURE
    assert len(outcome.votes) == 1
    assert not outcome.adjudicated
