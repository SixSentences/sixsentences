"""Content-free routing diagnostics must not change recovery or safety policy."""

import logging
from typing import cast

import pytest

from sixsentences_server.chat.service import _decide_tool
from sixsentences_server.core.answer_output import complete_public_answer
from sixsentences_server.llm.base import (
    BudgetGovernor,
    LLMCancelledError,
    LLMConfigError,
    ModelRef,
    TaskType,
)
from sixsentences_server.llm.mock import MockClient, mock_pool
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.providers import ProviderError


def test_optional_planner_exhaustion_is_observable_without_logging_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider_detail = "SYNTHETIC_PROVIDER_BODY_MUST_NOT_BE_LOGGED"
    cause_detail = "SYNTHETIC_CAUSE_CONTENT_MUST_NOT_BE_LOGGED"
    private_prompt = "SYNTHETIC_PRIVATE_PROMPT_MUST_NOT_BE_LOGGED"

    def unavailable(_model: str, _prompt: str) -> str:
        raise ProviderError(provider_detail) from RuntimeError(cause_detail)

    pool = mock_pool(unavailable, screening_models=1)
    client = cast(MockClient, pool.clients["mock"])
    with caplog.at_level(logging.WARNING, logger="sixsentences_server.llm.pool"):
        assert _decide_tool(pool, private_prompt, "", [], [], {"read_webpage": "Read"}) is None
        disabled_before_synthesis = set(pool._unavailable_refs)
        calls_before_synthesis = len(client.calls)
        with pytest.raises(LLMConfigError):
            complete_public_answer(
                pool, system="SYNTHETIC_PRIVATE_SYSTEM", prompt=private_prompt, max_tokens=100
            )

    # Diagnostics must not reopen failed routes, retry providers or claim output.
    assert pool._unavailable_refs == disabled_before_synthesis
    assert len(client.calls) == calls_before_synthesis == 2
    assert not pool.usage
    records = [record for record in caplog.records if record.name == "sixsentences_server.llm.pool"]
    assert len(records) == 3
    assert "reason=provider_error task=chat provider=mock model=mock-strong" in records[0].message
    assert "error_class=ProviderError cause_class=RuntimeError" in records[0].message
    assert "configured_routes=2 candidates=2 unavailable_routes=0" in records[0].message
    assert "configured_routes=2 candidates=1 unavailable_routes=1" in records[1].message
    assert "reason=no_candidate_routes task=chat" in records[2].message
    assert "configured_routes=2 candidates=0 unavailable_routes=2" in records[2].message
    for record in records:
        assert record.exc_info is None
        assert record.stack_info is None
        assert provider_detail not in record.getMessage()
        assert cause_detail not in record.getMessage()
        assert private_prompt not in record.getMessage()
        assert "SYNTHETIC_PRIVATE_SYSTEM" not in record.getMessage()
        assert all(not isinstance(argument, BaseException) for argument in record.args)


def test_missing_client_is_distinct_from_exhausted_candidate_routes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ref = ModelRef("mock", "mock-not-configured")
    pool = LLMPool({}, RoutingConfig(), BudgetGovernor(limit_usd=1))
    with (
        caplog.at_level(logging.WARNING, logger="sixsentences_server.llm.pool"),
        pytest.raises(LLMConfigError),
    ):
        pool.complete(TaskType.CHAT, system="private", prompt="private", ref=ref)
    assert "reason=no_available_client" in caplog.text
    assert "configured_routes=1 candidates=1 unavailable_routes=0" in caplog.text
    assert "private" not in caplog.text
    assert not pool._unavailable_refs


def test_cancellation_does_not_become_a_route_failure_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = mock_pool()
    pool.cancel_check = lambda: True
    with (
        caplog.at_level(logging.WARNING, logger="sixsentences_server.llm.pool"),
        pytest.raises(LLMCancelledError),
    ):
        pool.complete(TaskType.CHAT, system="private", prompt="private")
    assert not [
        record for record in caplog.records if record.name == "sixsentences_server.llm.pool"
    ]
    assert not pool._unavailable_refs
