"""Keep internal tool envelopes out of public answer streams and records."""

import re
from collections.abc import Callable
from dataclasses import replace
from html import unescape

from sixsentences_server.llm.base import LLMResponse, TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError

_TOOL_ENVELOPE = re.compile(
    r"<\s*/?\s*(?:tool_calls?|function_calls?|invoke)\b"
    r"|<\s*/?\s*parameter\b[^>]*\bname\s*="
    r"|<\|(?:tool_call|function_call)(?:_begin|_end)?\|>",
    re.IGNORECASE,
)
_ANSWER_REPAIR = (
    " FINAL ANSWER REPAIR: All permitted tool work has already finished. "
    "Write the actual answer using only the source material and tool results "
    "in the original prompt. Preserve its citation rules and evidence format. "
    "Do not emit tool calls, invocation XML, function envelopes or a plan to "
    "search again. Address the user directly in the required response language; "
    "do not merely restate what the user wants or narrate your progress. "
    "Do not claim any new action occurred. If the supplied "
    "evidence is insufficient, explain that limitation in ordinary prose."
)
_RESEARCH_PROMISE = re.compile(
    r"^(?:(?:okay|ok|sure|certainly|of course|gerne|klar|natürlich)[,:]?\s*)?"
    r"(?:(?:then|next|first|zuerst|danach|anschließend)\s+)?"
    r"(?:I(?:['’]ll|\s+will|\s+am\s+going\s+to|\s+need\s+to|\s+plan\s+to)\s+"
    r"(?:now\s+)?(?:search|look|browse|check|research|review|compare|find)\b"
    r"|I\s+am\s+(?:searching|looking|checking|browsing)\b"
    r"|Ich\s+(?:werde\s+|möchte\s+)?(?:jetzt\s+)?"
    r"(?:suche|suchen|recherchiere|recherchieren|schaue|schauen|prüfe|prüfen)\b)",
    re.IGNORECASE,
)
_REQUEST_RESTATEMENT = re.compile(
    r"^(?:the\s+user\s+(?:wants|asks|requested|requests|is\s+asking|would\s+like)\b"
    r"|(?:der\s+(?:nutzer|benutzer)|die\s+(?:nutzerin|benutzerin))\s+"
    r"(?:möchte|will|fragt|bittet|wünscht|hat\s+gefragt)\b)",
    re.IGNORECASE,
)


def contains_only_research_meta(text: str) -> bool:
    """Recognize all-meta research output without rejecting a substantive body."""

    sentences = [part.strip() for part in re.split(r"[.!?\n]+", text) if part.strip()]
    return bool(sentences) and all(
        _RESEARCH_PROMISE.match(part) or _REQUEST_RESTATEMENT.match(part) for part in sentences
    )


class PublicAnswerError(ProviderError):
    """A bounded synthesis retry still did not produce a public answer."""


def contains_internal_tool_syntax(text: str) -> bool:
    """Recognize complete, escaped and truncated model-only tool envelopes."""

    return bool(_TOOL_ENVELOPE.search(unescape(text)))


def complete_public_answer(
    pool: LLMPool,
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    on_reasoning: Callable[[str], None] | None = None,
    on_stream_reset: Callable[[], None] | None = None,
    require_completed_research_answer: bool = False,
) -> LLMResponse:
    """Validate a whole answer before publication, with one synthesis-only repair.

    Provider deltas stay private until the whole envelope has been checked.
    This also prevents a cancelled request from saving a partial tool call as
    an answer. The caller still owns citation/claim checks and final formatting.
    Every completion goes through the same metered, cancellable pool; no tool
    execution or fresh retrieval is available to the repair call.
    """

    for attempt in range(2):
        response = pool.complete(
            TaskType.CHAT,
            system=system if attempt == 0 else system + _ANSWER_REPAIR,
            prompt=prompt,
            max_tokens=max_tokens,
            on_delta=lambda _delta: None,
            on_stream_reset=on_stream_reset,
        )
        if (
            not response.text.strip()
            or contains_internal_tool_syntax(response.text)
            or (require_completed_research_answer and contains_only_research_meta(response.text))
        ):
            continue
        if response.reasoning and contains_internal_tool_syntax(response.reasoning):
            response = replace(response, reasoning=None)
        if response.reasoning and on_reasoning is not None:
            on_reasoning(response.reasoning)
        return response
    raise PublicAnswerError("The answer could not be completed. Please try again shortly.")
