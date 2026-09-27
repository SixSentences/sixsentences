"""Contract and data-boundary tests for optional TypeSafe Jev assistance."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from sixsentences_server.core.models import ReviewProtocol, ScreeningDecision, WorkRecord
from sixsentences_server.screening.jev import (
    JEV_API_URL,
    JEV_MODEL,
    JevAdvisorySignal,
    JevClient,
    JevConfigurationError,
    JevDataBoundaryError,
    JevProviderError,
    JevResponseError,
)

API_KEY = "ts-secret-that-must-never-leak"


def _work() -> WorkRecord:
    return WorkRecord(
        id="private-work-id",
        doi="10.1000/private-doi",
        title="Remote care for adults with asthma",
        abstract="A randomized study compared remote care with standard care.",
        year=2025,
        venue="Private Venue Marker",
        authors=["Private Author Marker"],
        source="private-source-marker",
    )


def _protocol() -> ReviewProtocol:
    return ReviewProtocol(
        question="Private protocol question marker",
        inclusion_criteria=["Participants are adults with asthma"],
        exclusion_criteria=["The publication is an editorial"],
        query_string="private-query-marker",
    )


def _answer(choice: str, *, confidence: float = 0.95) -> dict[str, object]:
    if choice in {"met", "not_met", "unclear"}:
        options = ("met", "not_met", "unclear")
    else:
        options = ("applies", "does_not_apply", "unclear")
    probabilities = {option: 0.025 for option in options}
    probabilities[choice] = 0.95
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def _valid_response(
    request: httpx.Request,
    *,
    inclusion_choice: str = "met",
    exclusion_choice: str = "does_not_apply",
    inclusion_confidence: float = 0.95,
    exclusion_confidence: float = 0.95,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    payload = json.loads(request.content)
    answers: dict[str, dict[str, object]] = {}
    for question_id in payload["questions"]:
        if question_id.startswith("inclusion_"):
            answers[question_id] = _answer(
                inclusion_choice,
                confidence=inclusion_confidence,
            )
        else:
            answers[question_id] = _answer(
                exclusion_choice,
                confidence=exclusion_confidence,
            )
    return httpx.Response(
        200,
        json={
            "model": JEV_MODEL,
            "answers": answers,
            "usage": {"input_tokens": 321, "output_tokens": 42},
        },
        headers=headers,
        request=request,
    )


def _client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> JevClient:
    return JevClient(
        API_KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_payload_is_fixed_and_contains_only_permitted_work_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _valid_response(request, headers={"x-request-id": "jev-request-123"})

    ticks = iter((10.0, 10.123))
    result = _client(handler, monotonic=lambda: next(ticks)).evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == JEV_API_URL
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    payload = json.loads(request.content)
    assert payload["model"] == "jev-1.13.0"
    assert payload["state"] == {
        "title": _work().title,
        "abstract": _work().abstract,
    }
    serialized = request.content.decode("utf-8")
    for forbidden in (
        "private-work-id",
        "10.1000/private-doi",
        "Private Venue Marker",
        "Private Author Marker",
        "private-source-marker",
        "Private protocol question marker",
        "private-query-marker",
    ):
        assert forbidden not in serialized
    assert set(payload["questions"]) == {"inclusion_0000", "exclusion_0000"}
    assert payload["questions"]["inclusion_0000"]["type"] == "choice"
    assert set(payload["questions"]["inclusion_0000"]["criteria"]) == {
        "met",
        "not_met",
        "unclear",
    }
    assert set(payload["questions"]["exclusion_0000"]["criteria"]) == {
        "applies",
        "does_not_apply",
        "unclear",
    }
    assert result.mode == "shadow"
    assert result.authoritative is False
    assert result.request_id == "jev-request-123"
    assert result.latency_ms == 123
    assert result.usage.input_tokens == 321
    assert not isinstance(result, ScreeningDecision)
    result_json = result.model_dump_json()
    assert _work().title not in result_json
    assert _work().abstract not in result_json
    assert _protocol().inclusion_criteria[0] not in result_json


@pytest.mark.parametrize(
    "request_id",
    ("", " contains spaces ", "contains/slash", "x" * 201, "line\nbreak"),
)
def test_untrusted_request_ids_are_not_persisted(request_id: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _valid_response(request, headers={"x-request-id": request_id})

    result = _client(handler).evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
    )

    assert result.request_id is None


def test_public_bibliographic_gate_fails_before_http() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("the public-data gate must run before HTTP")

    with pytest.raises(JevDataBoundaryError):
        _client(handler).evaluate(
            _work(),
            _protocol(),
            public_bibliographic_data_confirmed=False,
        )

    assert called is False


def test_local_input_limits_fail_closed_without_truncation_or_http() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("oversized input must fail before HTTP")

    cases = (
        (_work().model_copy(update={"title": "t" * 8_001}), _protocol()),
        (_work().model_copy(update={"abstract": "a" * 30_001}), _protocol()),
        (
            _work(),
            _protocol().model_copy(update={"inclusion_criteria": ["c" * 8_001]}),
        ),
    )
    for work, protocol in cases:
        with pytest.raises(JevConfigurationError, match="size limit"):
            _client(handler).evaluate(
                work,
                protocol,
                public_bibliographic_data_confirmed=True,
            )

    assert called is False


def test_api_key_is_trimmed_but_never_rendered() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _valid_response(request)

    adapter = JevClient(
        f"  {API_KEY}\n",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter.evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
    )

    assert requests[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in repr(adapter)


def test_all_high_confidence_eligible_answers_produce_include_signal() -> None:
    result = _client(_valid_response).evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
    )

    assert result.signal is JevAdvisorySignal.INCLUDE
    assert result.minimum_confidence == 0.75


def test_json_integer_probabilities_and_confidence_are_valid_numbers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        answers: dict[str, dict[str, object]] = {}
        for question_id in payload["questions"]:
            if question_id.startswith("inclusion_"):
                choices = {"met": 1, "not_met": 0, "unclear": 0}
                selected = "met"
            else:
                choices = {"applies": 0, "does_not_apply": 1, "unclear": 0}
                selected = "does_not_apply"
            answers[question_id] = {
                "type": "choice",
                "choice": selected,
                "probabilities": choices,
                "confidence": 1,
            }
        return httpx.Response(
            200,
            json={
                "model": JEV_MODEL,
                "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            request=request,
        )

    result = _client(handler).evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
    )

    assert result.signal is JevAdvisorySignal.INCLUDE


@pytest.mark.parametrize(
    ("inclusion_choice", "exclusion_choice"),
    (("not_met", "does_not_apply"), ("met", "applies")),
)
def test_high_confidence_ineligibility_produces_exclude_signal(
    inclusion_choice: str,
    exclusion_choice: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _valid_response(
            request,
            inclusion_choice=inclusion_choice,
            exclusion_choice=exclusion_choice,
        )

    result = _client(handler).evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
    )

    assert result.signal is JevAdvisorySignal.EXCLUDE


@pytest.mark.parametrize(
    ("inclusion_choice", "inclusion_confidence"),
    (("unclear", 0.99), ("met", 0.74)),
)
def test_unclear_or_low_confidence_answer_produces_unsure_signal(
    inclusion_choice: str,
    inclusion_confidence: float,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _valid_response(
            request,
            inclusion_choice=inclusion_choice,
            inclusion_confidence=inclusion_confidence,
            exclusion_choice="applies",
        )

    result = _client(handler).evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
        minimum_confidence=0.75,
    )

    assert result.signal is JevAdvisorySignal.UNSURE


@pytest.mark.parametrize(
    "malformation",
    (
        "wrong_model",
        "missing_answer",
        "extra_answer",
        "wrong_options",
        "invalid_probability_sum",
        "extra_field",
        "negative_usage",
    ),
)
def test_malformed_response_is_rejected(malformation: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        valid = _valid_response(request)
        payload = valid.json()
        if malformation == "wrong_model":
            payload["model"] = "jev-latest"
        elif malformation == "missing_answer":
            payload["answers"].pop("inclusion_0000")
        elif malformation == "extra_answer":
            payload["answers"]["not_requested"] = _answer("met")
        elif malformation == "wrong_options":
            payload["answers"]["inclusion_0000"]["probabilities"] = {
                "met": 0.95,
                "not_met": 0.05,
            }
        elif malformation == "invalid_probability_sum":
            payload["answers"]["inclusion_0000"]["probabilities"]["met"] = 0.80
        elif malformation == "extra_field":
            payload["answers"]["inclusion_0000"]["reason"] = "unsupported prose"
        elif malformation == "negative_usage":
            payload["usage"]["input_tokens"] = -1
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(JevResponseError, match="invalid response"):
        _client(handler).evaluate(
            _work(),
            _protocol(),
            public_bibliographic_data_confirmed=True,
        )


def test_only_429_and_529_are_retried_with_bounded_backoff() -> None:
    statuses = iter((429, 529, 200))
    sleeps: list[float] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        status = next(statuses)
        if status == 200:
            return _valid_response(request)
        return httpx.Response(status, request=request)

    result = _client(handler, sleep=sleeps.append, max_retries=2).evaluate(
        _work(),
        _protocol(),
        public_bibliographic_data_confirmed=True,
    )

    assert attempts == 3
    assert sleeps == [0.25, 0.5]
    assert result.retries == 2


def test_other_http_failures_are_not_retried_or_exposed() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text=f"raw upstream body {API_KEY}", request=request)

    with pytest.raises(JevProviderError) as raised:
        _client(handler, sleep=sleeps.append).evaluate(
            _work(),
            _protocol(),
            public_bibliographic_data_confirmed=True,
        )

    assert attempts == 1
    assert sleeps == []
    assert raised.value.status_code == 500
    assert API_KEY not in str(raised.value)
    assert API_KEY not in repr(raised.value)


def test_transport_error_and_client_repr_do_not_leak_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"raw transport detail {API_KEY}", request=request)

    adapter = _client(handler)
    assert API_KEY not in repr(adapter)

    with pytest.raises(JevProviderError) as raised:
        adapter.evaluate(
            _work(),
            _protocol(),
            public_bibliographic_data_confirmed=True,
        )

    assert API_KEY not in str(raised.value)
    assert API_KEY not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_hashes_are_deterministic_and_change_with_input_or_schema() -> None:
    def evaluate(work: WorkRecord, protocol: ReviewProtocol) -> tuple[str, str]:
        result = _client(_valid_response).evaluate(
            work,
            protocol,
            public_bibliographic_data_confirmed=True,
        )
        return result.input_sha256, result.schema_sha256

    baseline = evaluate(_work(), _protocol())
    assert evaluate(_work(), _protocol()) == baseline
    changed_work = _work().model_copy(update={"abstract": "A different public abstract."})
    changed_protocol = _protocol().model_copy(
        update={"inclusion_criteria": ["Participants are children with asthma"]}
    )
    assert evaluate(changed_work, _protocol())[0] != baseline[0]
    assert evaluate(changed_work, _protocol())[1] == baseline[1]
    assert evaluate(_work(), changed_protocol)[0] == baseline[0]
    assert evaluate(_work(), changed_protocol)[1] != baseline[1]
