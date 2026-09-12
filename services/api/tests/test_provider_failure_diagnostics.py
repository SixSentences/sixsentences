"""Safe provider metadata preserves failure status without changing recovery."""

import json
import logging
from collections.abc import Callable, Iterator
from typing import cast

import httpx
import pytest

from sixsentences_server.llm.base import TaskType
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.llm.providers import (
    OpenAICompatClient,
    ProviderError,
    provider_failure_diagnostics,
)

_MODEL = "deepseek/deepseek-v4-flash"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAICompatClient:
    return OpenAICompatClient(
        provider="openrouter",
        base_url="https://provider.example/v1",
        api_key="synthetic-test-key",
        http=httpx.Client(
            base_url="https://provider.example/v1", transport=httpx.MockTransport(handler)
        ),
        max_retries=1,
        sleeper=lambda _delay: None,
        jitter=lambda delay: delay,
    )


def _request(client: OpenAICompatClient, stream: bool) -> None:
    if stream:
        client.complete_stream(
            _MODEL, system="private", prompt="private", on_delta=lambda _delta: None
        )
    else:
        client.complete(_MODEL, system="private", prompt="private")


def test_legacy_message_constructor_and_diagnostic_validation() -> None:
    error = ProviderError("private detail HTTP 429")
    assert str(error) == "private detail HTTP 429"
    assert error.args == ("private detail HTTP 429",)
    # Message contents cannot create diagnostic facts.
    assert provider_failure_diagnostics(error) == ("provider_error", None)
    assert provider_failure_diagnostics(None) == ("none", None)
    assert provider_failure_diagnostics(RuntimeError("HTTP 401")) == ("unknown_error", None)


@pytest.mark.parametrize("status", [True, 99, 600, "429", None])
def test_invalid_status_or_code_never_reaches_diagnostics(status: object) -> None:
    error = ProviderError(
        "private", diagnostic_code="private-response-body", http_status=cast(int, status)
    )
    assert provider_failure_diagnostics(error) == ("provider_error", None)
    # Revalidate at the log boundary even if a third-party caller mutated fields.
    error.diagnostic_code = "private-response-body"
    error.http_status = cast(int, status)
    assert provider_failure_diagnostics(error) == ("provider_error", None)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [401, 404, 429, 503])
def test_http_status_survives_terminal_recovery_without_changing_retries(
    stream: bool, status: int
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, text="SYNTHETIC_PRIVATE_PROVIDER_BODY")

    client = _client(handler)
    with pytest.raises(ProviderError) as caught:
        _request(client, stream)
    error = caught.value
    if status in {401, 404}:
        assert len(requests) == 1
        assert provider_failure_diagnostics(error) == ("permanent_http", status)
    else:
        assert len(requests) == 2
        assert provider_failure_diagnostics(error) == ("retry_exhausted", status)
        assert provider_failure_diagnostics(error.__cause__) == ("transient_http", status)
    # Diagnostic changes must not loosen the existing OpenRouter privacy route.
    for request in requests:
        policy = json.loads(request.content)["provider"]
        assert policy["zdr"] is True
        assert policy["data_collection"] == "deny"


@pytest.mark.parametrize("stream", [False, True])
def test_read_timeout_retains_typed_cause_and_existing_no_same_route_retry(stream: bool) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_TRANSPORT_DETAIL", request=request)

    client = _client(handler)
    with pytest.raises(ProviderError) as caught:
        _request(client, stream)
    assert len(requests) == 1
    assert provider_failure_diagnostics(caught.value) == ("retry_exhausted", None)
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
    assert provider_failure_diagnostics(caught.value.__cause__) == ("read_timeout", None)


class _InterruptedStream(httpx.SyncByteStream):
    """Emit a visible answer prefix, then simulate a connection failure."""

    def __init__(self, request: httpx.Request) -> None:
        self.request = request

    def __iter__(self) -> Iterator[bytes]:
        event = {"choices": [{"delta": {"content": "A visible answer prefix."}}]}
        yield f"data: {json.dumps(event)}\n\n".encode()
        raise httpx.ReadError("SYNTHETIC_PRIVATE_STREAM_DETAIL", request=self.request)


def test_started_stream_keeps_fail_fast_policy_and_safe_interruption_code() -> None:
    requests: list[httpx.Request] = []
    visible: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_InterruptedStream(request))

    client = _client(handler)
    with pytest.raises(ProviderError) as caught:
        client.complete_stream(_MODEL, system="private", prompt="private", on_delta=visible.append)
    assert len(requests) == 1
    assert visible == ["A visible answer prefix."]
    assert "after output began" in str(caught.value)
    assert provider_failure_diagnostics(caught.value) == ("stream_interrupted", None)
    assert provider_failure_diagnostics(caught.value.__cause__) == ("transport_error", None)


@pytest.mark.parametrize(
    ("payload", "json_response", "expected_code"),
    [
        ({"choices": []}, False, "unexpected_response_shape"),
        ({"choices": [{"message": {"content": ""}}]}, False, "empty_response"),
        (
            {"choices": [{"message": {"content": "not JSON"}}]},
            True,
            "invalid_structured_response",
        ),
    ],
)
def test_terminal_response_validation_retains_its_safe_cause(
    payload: dict[str, object], json_response: bool, expected_code: str
) -> None:
    client = _client(lambda _request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError) as caught:
        client.complete(_MODEL, system="private", prompt="private", json_response=json_response)
    assert provider_failure_diagnostics(caught.value) == ("retry_exhausted", 200)
    assert provider_failure_diagnostics(caught.value.__cause__) == (expected_code, 200)


def test_pool_logs_only_allowlisted_codes_and_status_not_exception_contents(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = "SYNTHETIC_PROVIDER_RESPONSE_PROMPT_OR_KEY"

    def unavailable(_model: str, _prompt: str) -> str:
        cause = ProviderError(private, diagnostic_code="transient_http", http_status=429)
        raise ProviderError(private, diagnostic_code="retry_exhausted", http_status=429) from cause

    pool = mock_pool(unavailable, screening_models=1)
    with (
        caplog.at_level(logging.WARNING, logger="sixsentences_server.llm.pool"),
        pytest.raises(ProviderError) as caught,
    ):
        pool.complete(TaskType.CHAT, system=private, prompt=private)

    assert provider_failure_diagnostics(caught.value) == ("routes_exhausted", 429)
    assert provider_failure_diagnostics(caught.value.__cause__) == ("retry_exhausted", 429)
    records = [record for record in caplog.records if record.name == "sixsentences_server.llm.pool"]
    assert len(records) == 2
    assert len(pool._unavailable_refs) == 2
    for record in records:
        assert "diagnostic_code=retry_exhausted http_status=429 cause_code=transient_http" in (
            record.getMessage()
        )
        assert private not in record.getMessage()
        assert record.exc_info is None
        assert record.stack_info is None
        assert all(not isinstance(argument, BaseException) for argument in record.args)
