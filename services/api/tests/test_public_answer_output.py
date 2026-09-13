"""Provider-free contracts for the final public-answer boundary."""

from collections.abc import Callable
from typing import Any, cast

import pytest

from sixsentences_server.core.answer_output import (
    PublicAnswerError,
    complete_public_answer,
    contains_internal_tool_syntax,
)
from sixsentences_server.llm.base import LLMCancelledError, LLMResponse
from sixsentences_server.llm.pool import LLMPool


class DraftPool:
    def __init__(self, drafts: list[str | Exception]) -> None:
        self.drafts = iter(drafts)
        self.requests: list[dict[str, Any]] = []

    def complete(self, _task: Any, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        draft = next(self.drafts)
        if isinstance(draft, Exception):
            raise draft
        sink = cast(Callable[[str], None], kwargs["on_delta"])
        for offset in range(0, len(draft), 3):
            sink(draft[offset : offset + 3])
        return LLMResponse(draft, "mock", "mock", 1, 1)


@pytest.mark.parametrize(
    "text",
    [
        '<invoke name="web_search"><parameter name="query">Terraform</parameter></invoke>',
        '<function_calls><invoke name="web_search"',
        '<tool_call>{"query":"Terraform"}</tool_call>',
        '&lt;invoke name="web_search"&gt;Terraform&lt;/invoke&gt;',
        'Useful prose.\n<invoke name="web_search">',
        '<parameter name="query">Terraform</parameter>',
    ],
)
def test_internal_envelopes_are_detected_even_when_truncated(text: str) -> None:
    assert contains_internal_tool_syntax(text)


def test_repair_reuses_original_evidence_without_executing_invocations() -> None:
    leaked = '<invoke name="web_search"><parameter name="query">Terraform</parameter></invoke>'
    answer = "Plan previews proposed changes; apply carries them out. [WEB-1] [WEB-2]"
    prompt = "Question and already retrieved HashiCorp evidence [WEB-1] [WEB-2]."
    pool = DraftPool([leaked, answer])

    response = complete_public_answer(
        cast(LLMPool, pool), system="Only cite supplied evidence.", prompt=prompt, max_tokens=500
    )

    assert response.text == answer
    assert len(pool.requests) == 2
    assert all(request["prompt"] == prompt for request in pool.requests)
    assert "FINAL ANSWER REPAIR" in pool.requests[1]["system"]
    assert leaked not in pool.requests[1]["prompt"]


def test_repeated_tool_output_fails_instead_of_claiming_answer_completion() -> None:
    pool = DraftPool(['<invoke name="web_search">'] * 2)
    with pytest.raises(PublicAnswerError):
        complete_public_answer(cast(LLMPool, pool), system="s", prompt="p", max_tokens=100)
    assert len(pool.requests) == 2


def test_cancellation_does_not_trigger_a_synthesis_retry() -> None:
    pool = DraftPool([LLMCancelledError("cancelled")])
    with pytest.raises(LLMCancelledError):
        complete_public_answer(cast(LLMPool, pool), system="s", prompt="p", max_tokens=100)
    assert len(pool.requests) == 1


def test_ordinary_prose_and_citations_are_unchanged() -> None:
    answer = "Use `terraform plan` to preview changes. [WEB-1]\nParameters are inputs."
    pool = DraftPool([answer])
    response = complete_public_answer(cast(LLMPool, pool), system="s", prompt="p", max_tokens=100)
    assert response.text == answer
    assert len(pool.requests) == 1


@pytest.mark.parametrize(
    "promise",
    [
        "I'll search for current Terraform documentation.",
        "Sure, I will look up Terraform online. Then I will compare the sources.",
        "Ich schaue jetzt im Internet nach aktuellen Terraform-Quellen.",
        "The user wants additional internet details about Terraform beyond the "
        "scholarly sources already covered.",
        "Der Nutzer möchte zusätzliche Informationen zu Terraform aus dem Internet.",
    ],
)
def test_completed_research_repairs_planning_only_without_new_tool_work(promise: str) -> None:
    answer = "Terraform plan previews the proposed changes. [web:abc]"
    pool = DraftPool([promise, answer])
    response = complete_public_answer(
        cast(LLMPool, pool),
        system="Use only the retrieved evidence.",
        prompt="Already retrieved official Terraform documentation.",
        max_tokens=200,
        require_completed_research_answer=True,
    )
    assert response.text == answer
    assert len(pool.requests) == 2
    assert pool.requests[0]["prompt"] == pool.requests[1]["prompt"]
    assert "FINAL ANSWER REPAIR" in pool.requests[1]["system"]


def test_repeated_search_promises_do_not_claim_completion() -> None:
    pool = DraftPool(["I'll search the web for Terraform."] * 2)
    with pytest.raises(PublicAnswerError):
        complete_public_answer(
            cast(LLMPool, pool),
            system="s",
            prompt="p",
            max_tokens=100,
            require_completed_research_answer=True,
        )
    assert len(pool.requests) == 2


@pytest.mark.parametrize(
    "answer",
    [
        "I found no reliable evidence for this claim in the retrieved sources.",
        "I'll review the evidence. Terraform plan previews changes. [web:abc]",
        "I can search the web when you approve the public search terms.",
        "The user wants additional Terraform details.\n\n"
        "Terraform plan previews changes before apply. [web:abc]",
    ],
)
def test_substantive_or_honest_limit_answers_do_not_trigger_repair(answer: str) -> None:
    pool = DraftPool([answer])
    result = complete_public_answer(
        cast(LLMPool, pool),
        system="s",
        prompt="p",
        max_tokens=100,
        require_completed_research_answer=True,
    )
    assert result.text == answer
    assert len(pool.requests) == 1


def test_ordinary_planning_requests_keep_existing_behavior() -> None:
    answer = "I will search the web first. Then I will compare the sources."
    pool = DraftPool([answer])
    assert (
        complete_public_answer(
            cast(LLMPool, pool), system="s", prompt="Suggest a research plan.", max_tokens=100
        ).text
        == answer
    )
