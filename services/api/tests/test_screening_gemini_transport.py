"""Actual screening callers use native JSON requests without losing recall or costs."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from sixsentences_server.core.models import ReviewProtocol, Verdict, WorkRecord
from sixsentences_server.llm.base import BudgetGovernor, ModelRef, TaskType, cost_usd
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.screening.ensemble import screen_ensemble
from sixsentences_server.screening.fulltext import screen_full_text
from sixsentences_server.screening.reviewer import UNVALIDATED_REVIEWER_REASON, screen_single

_REFS = [
    ModelRef("gemini", "gemini-3.1-flash-lite"),
    ModelRef("gemini", "gemini-3.5-flash-lite"),
]
_WORK = WorkRecord(
    id="synthetic-json-screening",
    title="Synthetic screening fixture",
    abstract="This study empirically evaluates LLM-generated Terraform.",
)
_PROTOCOL = ReviewProtocol(
    question="Do LLMs generate correct infrastructure as code?",
    query_string="terraform",
    inclusion_criteria=["empirically evaluates LLM-generated Terraform"],
)
_QUOTE = "empirically evaluates LLM-generated Terraform"
_MALFORMED = [
    ("[]", "STOP"),
    ("null", "STOP"),
    ('{"verdict":"exclude",', "MAX_TOKENS"),
]


def _verdict(verdict: str) -> str:
    return json.dumps({"verdict": verdict, "reason": "Synthetic criterion", "quote": _QUOTE})


@contextmanager
def _native_pool(
    answer: Callable[[str, str], tuple[str, str]],
) -> Iterator[tuple[LLMPool, list[tuple[str, dict[str, Any]]]]]:
    requests: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.url.path.endswith(":generateContent")
        model = request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent")
        body = json.loads(request.content)
        requests.append((model, body))
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert "tools" not in body and "cachedContent" not in body
        prompt = body["contents"][0]["parts"][0]["text"]
        text, finish_reason = answer(model, prompt)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": finish_reason,
                        "content": {"parts": [{"text": text}]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 30,
                    "thoughtsTokenCount": 2,
                    "totalTokenCount": 132,
                },
            },
        )

    with httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        transport=httpx.MockTransport(handler),
    ) as http:
        pool = LLMPool(
            clients={"gemini": GeminiClient(api_key="inert-test-key", http=http, max_retries=0)},
            routing=RoutingConfig(adjudication=_REFS[1], screening=list(_REFS)),
            budget=BudgetGovernor(limit_usd=1.0),
        )
        yield pool, requests


def _assert_accounted_once(pool: LLMPool, requests: list[tuple[str, dict[str, Any]]]) -> None:
    assert pool.budget.calls == len(pool.usage) == len(requests)
    assert all(usage.input_tokens == 100 and usage.output_tokens == 32 for usage in pool.usage)
    assert pool.budget.spent_usd == pytest.approx(
        sum(cost_usd(model, 100, 32) for model, _ in requests)
    )


@pytest.mark.parametrize("ref", _REFS)
def test_single_reviewer_requests_json_through_native_transport(ref: ModelRef) -> None:
    with _native_pool(lambda model, prompt: (_verdict("include"), "STOP")) as (pool, requests):
        decision = screen_single(_WORK, _PROTOCOL, pool, ref)

        assert decision.verdict is Verdict.INCLUDE
        assert decision.quote == _QUOTE
        assert requests[0][0] == ref.model
        assert requests[0][1]["generationConfig"]["maxOutputTokens"] == 300
        system = requests[0][1]["systemInstruction"]["parts"][0]["text"]
        assert "independent reviewers" not in system
        _assert_accounted_once(pool, requests)


def test_conflict_adjudicator_also_requests_native_json() -> None:
    def answer(model: str, prompt: str) -> tuple[str, str]:
        verdict = (
            "include"
            if "Distinct model reviewer votes" in prompt
            else ("include" if model == _REFS[0].model else "exclude")
        )
        return _verdict(verdict), "STOP"

    with _native_pool(answer) as (pool, requests):
        outcome = screen_ensemble(_WORK, _PROTOCOL, pool, list(_REFS))

        assert outcome.adjudicated
        assert outcome.final.verdict is Verdict.INCLUDE
        assert len(requests) == 3
        assert [usage.task for usage in pool.usage].count(TaskType.ADJUDICATION.value) == 1
        assert all(body["generationConfig"]["maxOutputTokens"] == 300 for _, body in requests)
        _assert_accounted_once(pool, requests)


def test_fulltext_reviewers_request_native_json() -> None:
    answer = json.dumps(
        {
            "criteria": [
                {
                    "index": 0,
                    "applies": "yes",
                    "quote": _QUOTE,
                    "reason": "Synthetic criterion",
                }
            ]
        }
    )
    with _native_pool(lambda model, prompt: (answer, "STOP")) as (pool, requests):
        assessment = screen_full_text(_WORK, _PROTOCOL, _WORK.abstract or "", pool)

        assert assessment.verdict is Verdict.INCLUDE
        assert assessment.quote_verified
        assert len(assessment.criteria) == 1
        assert len(requests) == 2
        assert all(body["generationConfig"]["maxOutputTokens"] == 400 for _, body in requests)
        assert all(usage.task == TaskType.FULL_TEXT_SCREENING.value for usage in pool.usage)
        _assert_accounted_once(pool, requests)


@pytest.mark.parametrize("answer", _MALFORMED)
def test_invalid_native_json_retains_record_and_accounted_votes(answer: tuple[str, str]) -> None:
    with _native_pool(lambda model, prompt: answer) as (pool, requests):
        outcome = screen_ensemble(_WORK, _PROTOCOL, pool, list(_REFS))

        assert outcome.final.verdict is Verdict.UNSURE
        assert len(outcome.votes) == 2
        assert all(vote.verdict is Verdict.UNSURE for vote in outcome.votes)
        assert all(vote.reason == UNVALIDATED_REVIEWER_REASON for vote in outcome.votes)
        assert all(vote.quote is None for vote in outcome.votes)
        assert pool.precision_refs() == list(reversed(_REFS))
        # Bad JSON on one record must not disable the model for the next record.
        again = screen_single(_WORK, _PROTOCOL, pool, _REFS[0])
        assert again.verdict is Verdict.UNSURE
        assert len(requests) == 3
        _assert_accounted_once(pool, requests)


@pytest.mark.parametrize("invalid", _MALFORMED)
def test_invalid_native_adjudication_is_unsure_without_duplicate_costs(
    invalid: tuple[str, str],
) -> None:
    def answer(model: str, prompt: str) -> tuple[str, str]:
        if "Distinct model reviewer votes" in prompt:
            return invalid
        return _verdict("include" if model == _REFS[0].model else "exclude"), "STOP"

    with _native_pool(answer) as (pool, requests):
        outcome = screen_ensemble(_WORK, _PROTOCOL, pool, list(_REFS))

        assert outcome.adjudicated
        assert outcome.final.verdict is Verdict.UNSURE
        assert outcome.final.reason == UNVALIDATED_REVIEWER_REASON
        assert outcome.final.quote is None
        assert len(requests) == 3
        _assert_accounted_once(pool, requests)


@pytest.mark.parametrize("answer", _MALFORMED)
def test_invalid_native_fulltext_is_unsure_without_invented_criteria(
    answer: tuple[str, str],
) -> None:
    with _native_pool(lambda model, prompt: answer) as (pool, requests):
        assessment = screen_full_text(_WORK, _PROTOCOL, _WORK.abstract or "", pool)

        assert assessment.verdict is Verdict.UNSURE
        assert assessment.reason == UNVALIDATED_REVIEWER_REASON
        assert assessment.criteria == []
        assert assessment.quote is None and not assessment.quote_verified
        assert len(requests) == 2
        assert pool.precision_refs() == list(reversed(_REFS))
        _assert_accounted_once(pool, requests)


def test_non_structured_provider_error_is_not_converted_into_an_unsure_vote() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        transport=httpx.MockTransport(handler),
    ) as http:
        pool = LLMPool(
            clients={"gemini": GeminiClient(api_key="inert-test-key", http=http, max_retries=0)},
            routing=RoutingConfig(screening=list(_REFS)),
            budget=BudgetGovernor(limit_usd=1.0),
        )
        with pytest.raises(ProviderError):
            screen_single(_WORK, _PROTOCOL, pool, _REFS[0])
        assert pool.budget.calls == len(pool.usage) == 1
