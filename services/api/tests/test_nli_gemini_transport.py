"""Native claim verification has structured, bounded and fully metered output."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from sixsentences_server.llm.base import BudgetGovernor, ModelRef, TaskType, cost_usd
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.verification.nli import (
    NLI_BATCH_OUTPUT_TOKENS,
    NLI_SINGLE_OUTPUT_TOKENS,
    LLMEntailmentChecker,
    Support,
    _parse,
    _parse_batch,
)


@contextmanager
def _native_pool(
    answer: Callable[[dict[str, Any]], str],
    *,
    budget: float = 1.0,
) -> Iterator[tuple[LLMPool, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "generativelanguage.googleapis.com"
        model = request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent")
        body = json.loads(request.content)
        requests.append(body)
        config = body["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert body["serviceTier"] == "standard"
        assert "tools" not in body and "cachedContent" not in body
        system = body["systemInstruction"]["parts"][0]["text"]
        prompt = body["contents"][0]["parts"][0]["text"]
        # Admission must reserve the actual transmitted output cap, not the
        # small expected response size used by economic scenario estimates.
        expected_reserve = cost_usd(
            model,
            len(system.encode()) + len(prompt.encode()) + 256,
            config["maxOutputTokens"],
        )
        assert pool.budget.reserved_usd == pytest.approx(expected_reserve)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": answer(body)}]}}],
                "usageMetadata": {
                    "promptTokenCount": 400,
                    "candidatesTokenCount": 36,
                    "thoughtsTokenCount": 72,
                    "totalTokenCount": 508,
                    "serviceTier": "standard",
                },
            },
        )

    with httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        transport=httpx.MockTransport(transport),
    ) as http:
        pool = LLMPool(
            clients={"gemini": GeminiClient(api_key="inert-test-key", http=http, max_retries=0)},
            routing=RoutingConfig(
                synthesis=ModelRef("gemini", "gemini-3.1-pro-preview"),
                adjudication=ModelRef("gemini", "gemini-3.5-flash-lite"),
                screening=[
                    ModelRef("gemini", "gemini-3.1-flash-lite"),
                    ModelRef("gemini", "gemini-3.5-flash-lite"),
                ],
            ),
            budget=BudgetGovernor(limit_usd=budget),
        )
        yield pool, requests


def _assert_actual_cost(pool: LLMPool, requests: list[dict[str, Any]]) -> None:
    assert pool.budget.calls == len(pool.usage) == len(requests)
    assert pool.budget.reserved_usd == 0
    assert all(item.task == TaskType.CLAIM_VERIFICATION.value for item in pool.usage)
    assert all(
        item.output_tokens == 108 and item.cost_source == "gemini_usage" for item in pool.usage
    )
    assert pool.budget.spent_usd == pytest.approx(
        sum(cost_usd(item.model, 400, 108) for item in pool.usage)
    )


@pytest.mark.parametrize("label", ["supported", "unsupported", "neutral"])
def test_native_single_claim_has_room_for_thinking_and_keeps_actual_verdict(label: str) -> None:
    def answer(body: dict[str, Any]) -> str:
        assert body["generationConfig"]["maxOutputTokens"] == NLI_SINGLE_OUTPUT_TOKENS == 1024
        return json.dumps({"label": label, "reason": "A complete paired-evidence verdict."})

    with _native_pool(answer) as (pool, requests):
        result = LLMEntailmentChecker(pool).check(
            "Planning previews changes without applying them.",
            "The plan command does not actually carry out the proposed changes.",
        )
        assert result.label == Support(label)
        assert len(requests) == 1
        assert pool.usage[0].model == "gemini-3.5-flash"  # never economical Lite screening
        _assert_actual_cost(pool, requests)


def test_native_batch_uses_object_contract_and_preserves_each_paired_verdict() -> None:
    def answer(body: dict[str, Any]) -> str:
        assert body["generationConfig"]["maxOutputTokens"] == NLI_BATCH_OUTPUT_TOKENS == 2048
        return json.dumps(
            {
                "verdicts": [
                    {"index": index, "label": "supported", "reason": f"Source {index}."}
                    for index in range(4)
                ]
            }
        )

    with _native_pool(answer) as (pool, requests):
        verdicts = LLMEntailmentChecker(pool).check_many(
            [(f"Claim {index}", f"Evidence {index}") for index in range(4)]
        )
        assert len(verdicts) == 4 and all(item.label == Support.SUPPORTED for item in verdicts)
        assert len(requests) == 1
        _assert_actual_cost(pool, requests)


@pytest.mark.parametrize(
    "text",
    [
        '{"label":"supported",',
        "null",
        "[]",
        '{"label":true,"reason":"invalid"}',
        '{"label":"supported","reason":[]}',
    ],
)
def test_invalid_native_output_stays_neutral_and_every_attempt_is_paid(text: str) -> None:
    with _native_pool(lambda _body: text) as (pool, requests):
        result = LLMEntailmentChecker(pool).check("A claim", "Its paired evidence")
        assert result.label == Support.NEUTRAL
        assert 1 <= len(requests) <= 2
        _assert_actual_cost(pool, requests)


def test_insufficient_budget_blocks_larger_reserve_before_any_request() -> None:
    with _native_pool(lambda _body: "unreachable", budget=0.00001) as (pool, requests):
        result = LLMEntailmentChecker(pool).check("A claim", "Its paired evidence")
        assert result.label == Support.NEUTRAL
        assert not requests and not pool.usage
        assert pool.budget.spent_usd == pool.budget.reserved_usd == 0


@pytest.mark.parametrize(
    "text",
    [
        "null",
        "[]",
        '"supported"',
        '{"label":true,"reason":"not a label"}',
        '{"label":"supported","reason":{}}',
        '{"label":"supported"}',
    ],
)
def test_verdict_schema_never_coerces_malformed_json_into_support(text: str) -> None:
    assert _parse(text).label == Support.NEUTRAL


@pytest.mark.parametrize("indices", [[0, 0], [False], ["0"], [2], [None]])
def test_batch_indices_cannot_rebind_or_overwrite_evidence(indices: list[Any]) -> None:
    response = json.dumps(
        {
            "verdicts": [
                {"index": index, "label": "supported", "reason": "Duplicate or malformed index."}
                for index in indices
            ]
        }
    )
    assert all(item.label == Support.NEUTRAL for item in _parse_batch(response, 2))
