"""Keyless concurrent admission, settlement and shared-sink regressions."""

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import httpx
import pytest

from sixsentences_server.config import Settings
from sixsentences_server.core.db import LLMCallRow, Org, db_session, init_db
from sixsentences_server.core.entitlements import (
    action_ai_spend,
    attach_action_usage_sink,
    begin_ai_action,
)
from sixsentences_server.llm.base import (
    BudgetExceededError,
    BudgetGovernor,
    LLMCancelledError,
    LLMResponse,
    LLMUsage,
    ModelRef,
    TaskType,
    cost_usd,
)
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.pool import _GLOBAL_USER_PROSE_STYLE, LLMPool, RoutingConfig
from sixsentences_server.llm.privacy import PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL
from sixsentences_server.llm.providers import ProviderError

_MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")
_REFS = tuple(ModelRef("gemini", model) for model in _MODELS)
_INPUT_BOUND = len(("s" + _GLOBAL_USER_PROSE_STYLE).encode()) + len(b"p") + 256
_CAP = 300


def _payload(*, input_tokens: int = 20, output_tokens: int = 10) -> dict[str, object]:
    return {
        "candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}],
        "usageMetadata": {
            "serviceTier": "standard",
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
            "totalTokenCount": input_tokens + output_tokens,
        },
    }


def _pool(
    handler: Callable[[httpx.Request], httpx.Response], limit: float, *, retries: int = 0
) -> LLMPool:
    client = GeminiClient(
        api_key="inert-test-key",
        max_retries=retries,
        sleeper=lambda _: None,
        http=httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            transport=httpx.MockTransport(handler),
        ),
    )
    return LLMPool(
        {"gemini": client},
        RoutingConfig(synthesis=_REFS[0], adjudication=_REFS[1], screening=list(_REFS)),
        BudgetGovernor(limit_usd=limit),
    )


def _complete(pool: LLMPool, ref: ModelRef = _REFS[0]) -> LLMResponse:
    return pool.complete(TaskType.SCREENING, system="s", prompt="p", ref=ref, max_tokens=_CAP)


def test_atomic_reservation_counts_inflight_and_settles_once() -> None:
    budget = BudgetGovernor(limit_usd=1)
    with budget.reserve(0.75) as token:
        assert budget.reserved_usd == 0.75
        with pytest.raises(BudgetExceededError), budget.reserve(0.3):
            pytest.fail("must not admit a second request over the shared cap")
        with pytest.raises(BudgetExceededError):
            budget.check(0.3)
        budget.record(
            TaskType.CHAT,
            LLMResponse("done", _MODELS[0], "gemini", 0, 0, cost_usd=0.1),
            reservation=token,
        )
        assert budget.reserved_usd == 0
        assert budget.spent_usd == 0.1
        with pytest.raises(ValueError, match="not active"):
            budget.record(
                TaskType.CHAT,
                LLMResponse("duplicate", _MODELS[0], "gemini", 0, 0, cost_usd=0.1),
                reservation=token,
            )
        with budget.reserve(0.8):
            assert budget.reserved_usd == 0.8
    assert budget.reserved_usd == 0
    assert budget.calls == 1


def test_reported_overrun_is_recorded_not_clipped_or_hidden() -> None:
    budget = BudgetGovernor(limit_usd=0.5)
    with budget.reserve(0.4) as token:
        budget.record(
            TaskType.SCREENING,
            LLMResponse("done", _MODELS[0], "gemini", 0, 0, cost_usd=0.7),
            reservation=token,
        )
    assert budget.reserved_usd == 0
    assert budget.spent_usd == 0.7
    with pytest.raises(BudgetExceededError), budget.reserve(0):
        pytest.fail("actual overrun must stop subsequent dispatch")


def test_parallel_pinned_pools_cannot_both_spend_the_same_remaining_budget() -> None:
    started, release = (Event(), Event())
    attempts: list[str] = []
    projected = [cost_usd(model, _INPUT_BOUND, _CAP) for model in _MODELS]

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) == 1:
            started.set()
            assert release.wait(timeout=3)
        return httpx.Response(200, json=_payload())

    pool = _pool(handler, max(projected) + min(projected) / 2)
    first = pool.pinned(ModelRef("gemini", PRIVATE_DEFAULT_MODEL))
    second = pool.pinned(ModelRef("gemini", PRIVATE_PREMIUM_MODEL))
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(_complete, first, _REFS[0])
        try:
            assert started.wait(timeout=3)
            assert pool.budget.reserved_usd == pytest.approx(projected[0])
            with pytest.raises(BudgetExceededError):
                executor.submit(_complete, second, _REFS[1]).result(timeout=3)
            assert len(attempts) == 1
        finally:
            release.set()
        pending.result(timeout=3)
    assert pool.budget.reserved_usd == 0
    _complete(second, _REFS[1])
    assert len(attempts) == pool.budget.calls == 2
    assert pool.budget.spent_usd == pytest.approx(sum(cost_usd(m, 20, 10) for m in _MODELS))
    assert pool.budget.spent_usd <= pool.budget.limit_usd
    assert pool.budget.reserved_usd == 0


def test_parallel_native_reviewers_and_shared_database_sink_remain_correct(
    settings: Settings,
) -> None:
    barrier = Barrier(2)

    def handler(_request: httpx.Request) -> httpx.Response:
        barrier.wait(timeout=3)
        return httpx.Response(200, json=_payload())

    init_db()
    with db_session() as session:
        org = Org(name="Synthetic parallel screening", plan="community")
        session.add(org)
        session.flush()
        action = begin_ai_action(
            session, org, action="question", resource_type="chat", resource_id="parallel"
        )
        pool = _pool(handler, action.cost_limit_usd)
        attach_action_usage_sink(
            session,
            pool,
            org_id=org.id,
            action_id=action.action_id,
            resource_type="chat",
            resource_id="parallel",
        )
        sink = pool.on_usage
        assert sink is not None
        sink_guard = Lock()

        def guarded_sink(usage: LLMUsage) -> None:
            assert sink_guard.acquire(blocking=False), "shared persistence callbacks overlap"
            try:
                pool.budget.check(0)
                time.sleep(0.001)
                sink(usage)
            finally:
                sink_guard.release()

        pool.on_usage = guarded_sink
        views = [
            pool.pinned(ModelRef("gemini", model))
            for model in (PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL)
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(
                executor.map(
                    lambda index: _complete(views[index % 2], _REFS[index % 2]), range(120)
                )
            )
        session.flush()
        rows = session.query(LLMCallRow).filter_by(action_id=action.action_id).all()
        expected = 60 * sum(cost_usd(model, 20, 10) for model in _MODELS)
        assert len(rows) == len(pool.usage) == pool.budget.calls == 120
        assert {row.provider for row in rows} == {"gemini"}
        assert {row.cost_source for row in rows} == {"gemini_usage"}
        assert sum(row.cost_usd for row in rows) == pytest.approx(expected)
        assert action_ai_spend(session, action.action_id) == pytest.approx(expected)
        assert pool.budget.spent_usd == pytest.approx(expected)
        assert pool.budget.reserved_usd == 0


@pytest.mark.parametrize("failure", ["connect", "429", "400", "unexpected"])
def test_unbilled_failures_and_internal_retries_release_the_single_hold(failure: str) -> None:
    attempts = 0
    projected = cost_usd(_MODELS[0], _INPUT_BOUND, _CAP)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert pool.budget.reserved_usd == pytest.approx(projected)
        if failure == "connect":
            raise httpx.ConnectError("inert connection failure")
        if failure == "unexpected":
            raise RuntimeError("inert adapter failure")
        return httpx.Response(int(failure))

    pool = _pool(handler, projected, retries=2)
    with pytest.raises(RuntimeError if failure == "unexpected" else ProviderError):
        _complete(pool)
    assert attempts == (3 if failure in {"connect", "429"} else 1)
    assert pool.budget.reserved_usd == 0
    assert pool.budget.spent_usd == pool.budget.calls == 0
    assert pool.usage == []


@pytest.mark.parametrize("allow_fallback", [False, True])
def test_billable_failure_settles_before_the_fallback_checks_its_own_ceiling(
    allow_fallback: bool,
) -> None:
    projected = [cost_usd(model, _INPUT_BOUND, _CAP) for model in _MODELS]
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) == 1:
            assert pool.budget.reserved_usd == pytest.approx(projected[0])
            return httpx.Response(503)
        assert pool.budget.spent_usd == pytest.approx(projected[0])
        assert pool.budget.reserved_usd == pytest.approx(projected[1])
        return httpx.Response(200, json=_payload())

    pool = _pool(handler, sum(projected) + (1e-05 if allow_fallback else -1e-05))
    pool.routing.screening = []
    if allow_fallback:
        pool.complete(TaskType.CHAT, system="s", prompt="p", max_tokens=_CAP)
    else:
        with pytest.raises(BudgetExceededError):
            pool.complete(TaskType.CHAT, system="s", prompt="p", max_tokens=_CAP)
    assert len(attempts) == (2 if allow_fallback else 1)
    assert pool.usage[0].cost_source == "uncertain"
    assert pool.budget.spent_usd == pytest.approx(
        projected[0] + (cost_usd(_MODELS[1], 20, 10) if allow_fallback else 0)
    )
    assert pool.budget.reserved_usd == 0


def test_stream_cancellation_keeps_usage_and_releases_unused_reservation() -> None:
    cancelled = Event()
    payload = _payload()
    pool = _pool(lambda _: httpx.Response(200, text=f"data: {json.dumps(payload)}\n\n"), 1)
    with pytest.raises(LLMCancelledError):
        pool.complete(
            TaskType.SCREENING,
            system="s",
            prompt="p",
            ref=_REFS[0],
            max_tokens=_CAP,
            on_delta=lambda _: cancelled.set(),
            cancel_check=cancelled.is_set,
        )
    assert pool.budget.calls == len(pool.usage) == 1
    assert pool.budget.spent_usd == pytest.approx(cost_usd(_MODELS[0], 20, 10))
    assert pool.budget.reserved_usd == 0
    assert pool.usage[0].cost_source == "gemini_usage"


def test_already_cancelled_call_never_reserves_or_dispatches() -> None:
    pool = _pool(lambda _: pytest.fail("cancelled call must not dispatch"), 1)
    with pytest.raises(LLMCancelledError):
        pool.complete(TaskType.CHAT, system="s", prompt="p", cancel_check=lambda: True)
    assert pool.budget.calls == pool.budget.spent_usd == pool.budget.reserved_usd == 0


def test_sink_failure_keeps_observed_spend_without_leaking_a_reservation() -> None:
    pool = _pool(lambda _: httpx.Response(200, json=_payload()), 1)

    def fail_sink(_usage: LLMUsage) -> None:
        raise RuntimeError("inert persistence failure")

    pool.on_usage = fail_sink
    with pytest.raises(RuntimeError, match="persistence failure"):
        _complete(pool)
    assert pool.budget.calls == len(pool.usage) == 1
    assert pool.budget.spent_usd == pytest.approx(cost_usd(_MODELS[0], 20, 10))
    assert pool.budget.reserved_usd == 0


@pytest.mark.parametrize(
    "task", [TaskType.SCREENING, TaskType.ADJUDICATION, TaskType.FULL_TEXT_SCREENING]
)
def test_invalid_screening_json_is_accounted_without_disabling_or_retrying_model(
    task: TaskType,
) -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        payload = _payload()
        if len(attempts) == 1:
            payload["candidates"] = [{"content": {"parts": [{"text": "[]"}]}}]
        return httpx.Response(200, json=payload)

    pool = _pool(handler, 1)
    with pytest.raises(ProviderError) as error:
        pool.complete(task, system="s", prompt="p", max_tokens=_CAP, json_response=True)
    assert error.value.diagnostic_code == "invalid_structured_response"
    assert len(attempts) == pool.budget.calls == len(pool.usage) == 1
    assert pool.usage[0].cost_source == "gemini_usage"
    assert pool._unavailable_refs == set()
    assert pool.budget.reserved_usd == 0
    pool.complete(task, system="s", prompt="p", max_tokens=_CAP, json_response=True)
    assert attempts[1] == attempts[0]
    assert pool.budget.calls == 2
    assert pool.budget.reserved_usd == 0


def test_interactive_invalid_json_keeps_existing_accounted_fallback() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        payload = _payload()
        if len(attempts) == 1:
            payload["candidates"] = [{"content": {"parts": [{"text": "[]"}]}}]
        return httpx.Response(200, json=payload)

    pool = _pool(handler, 1)
    pool.routing.screening = []
    pool.complete(TaskType.CHAT, system="s", prompt="p", max_tokens=_CAP, json_response=True)
    assert len(attempts) == pool.budget.calls == 2
    assert attempts[0] != attempts[1]
    assert _REFS[0] in pool._unavailable_refs
    assert pool.budget.spent_usd == pytest.approx(sum(cost_usd(m, 20, 10) for m in _MODELS))
    assert pool.budget.reserved_usd == 0


def test_reservation_releases_on_base_exception() -> None:
    budget = BudgetGovernor(limit_usd=1)
    with pytest.raises(KeyboardInterrupt), budget.reserve(0.9):
        raise KeyboardInterrupt
    assert budget.reserved_usd == budget.spent_usd == budget.calls == 0
