"""Shared fail-closed parsing and recovery for structured agent responses."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any, Protocol, cast

from sixsentences_server.llm.base import (
    LLMCancelledError,
    LLMConfigError,
    ModelRef,
    TaskType,
)
from sixsentences_server.llm.privacy import PRIVATE_DEFAULT_MODEL

_STRUCTURED_RECOVERY_MODEL = ModelRef(
    "gemini",
    PRIVATE_DEFAULT_MODEL,
)


class StructuredCompletionPool(Protocol):
    """Minimal completion interface used by structured response recovery."""

    def complete(
        self,
        task: TaskType,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
    ) -> Any:
        """Return one model completion."""


def request_structured_completion(
    pool: StructuredCompletionPool,
    *,
    system: str,
    prompt: str,
    max_tokens: int,
) -> Any:
    """Prefer provider-enforced JSON while retaining deterministic test pools."""

    complete_json = getattr(pool, "complete_json", None)
    if callable(complete_json):
        return complete_json(
            TaskType.CHAT,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
        )
    return pool.complete(
        TaskType.CHAT,
        system=system,
        prompt=prompt,
        max_tokens=max_tokens,
    )


def structured_recovery_pool(
    pool: StructuredCompletionPool,
) -> StructuredCompletionPool:
    """Use the product's reliable JSON formatter for invisible recovery calls.

    The user's selected model remains responsible for the substantive answer.
    Only a malformed response is retried through this bounded formatter. Fixed
    adapters and deployments without model pinning keep their existing pool.
    """

    pinned = getattr(pool, "pinned", None)
    if not callable(pinned):
        return pool
    try:
        candidate = pinned(_STRUCTURED_RECOVERY_MODEL)
    except LLMConfigError:
        return pool
    return cast(StructuredCompletionPool, candidate)


def extract_structured_object(
    value: str,
    *,
    required_keys: Collection[str] = (),
) -> dict[str, Any] | None:
    """Extract one complete schema-shaped JSON object from provider output.

    Reasoning models sometimes put a short explanation before a valid object
    or wrap it in a Markdown fence. Scanning with ``raw_decode`` accepts that
    harmless wrapping while still rejecting truncated JSON and unrelated
    objects. At least one required schema key must be present when supplied.
    """

    raw = str(value or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw[3:-3].strip()
        if raw.casefold().startswith("json"):
            raw = raw[4:].lstrip()
    required = set(required_keys)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            candidate, _ = decoder.raw_decode(raw[match.start() :])
        except ValueError:
            continue
        if not isinstance(candidate, dict):
            continue
        if required and required.isdisjoint(candidate):
            continue
        return candidate
    return None


def extract_complete_string_field(
    value: str,
    *,
    field_names: Collection[str],
    max_length: int = 10_000,
) -> str | None:
    """Recover one fully encoded string field from a partial JSON object.

    Long agent responses can be truncated after a complete user-facing answer
    but before their action array closes. The incomplete object must never be
    trusted as an action proposal. This helper therefore decodes only an exact
    JSON string value belonging to an allow-listed field and ignores every
    other byte. Plain prose, incomplete strings and non-string values remain
    invalid.
    """

    raw = str(value or "").strip()
    if "{" not in raw:
        return None
    decoder = json.JSONDecoder()
    for field_name in field_names:
        key_pattern = re.compile(rf'(?<!\\)"{re.escape(str(field_name))}"\s*:\s*')
        for match in key_pattern.finditer(raw):
            try:
                candidate, _ = decoder.raw_decode(raw[match.end() :])
            except ValueError:
                continue
            if not isinstance(candidate, str):
                continue
            answer = candidate.strip()
            if answer:
                return answer[:max_length]
    return None


def recover_structured_object(
    pool: StructuredCompletionPool,
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    required_keys: Collection[str],
) -> dict[str, Any] | None:
    """Retry the original grounded request once with a strict format reminder.

    The unverified first response is deliberately not replayed. This prevents
    invented claims or mutations from being laundered through a JSON repair
    prompt. A second malformed response remains a safe, action-free failure.
    """

    recovery_system = (
        system + "\n\nFORMAT RECOVERY: Your previous response could not be parsed. "
        "Answer the original grounded request again. Return exactly one JSON "
        "object matching the schema above. Do not add Markdown fences, reasoning "
        "text or commentary outside the JSON object. Preserve the user's exact "
        "requested controls, order, constraints and evidence scope."
    )
    # A complex Writer proposal may have been truncated at the normal answer
    # budget. The recovery is still a single request, but receives enough room
    # to close every requested field and edit without silently dropping one.
    recovery_tokens = max(max_tokens, min(6_000, max_tokens + max_tokens // 2))
    try:
        recovered = request_structured_completion(
            pool,
            system=recovery_system,
            prompt=prompt,
            max_tokens=recovery_tokens,
        )
    except LLMCancelledError:
        raise
    except Exception:  # noqa: BLE001 - preserve the safe failure contract
        return None
    return extract_structured_object(
        str(getattr(recovered, "text", "")),
        required_keys=required_keys,
    )


def recover_action_free_answer(
    pool: StructuredCompletionPool,
    *,
    system: str,
    prompt: str,
    max_tokens: int,
    answer_key: str,
    empty_fields: dict[str, Any],
) -> dict[str, Any] | None:
    """Make one final grounded, mutation-free recovery attempt.

    A model can fail a large structured contract even when it can still answer
    the research question. This last pass deliberately narrows the contract to
    one user-facing answer field. Server-owned empty fields are then added back
    so no malformed edit, quote or workspace action can survive the recovery.
    The original grounded prompt is reused and the unverified provider output
    is never shown to the recovery model.
    """

    safe_system = (
        system + "\n\nFINAL SAFE RECOVERY: The earlier structured responses were invalid. "
        "Answer the original grounded request without proposing or claiming any "
        "edit, action, mutation, quote, visual, workspace creation or side effect. "
        f'Return exactly one JSON object with one key: {{"{answer_key}":'
        '"<useful grounded answer>"}}. Do not add Markdown fences, reasoning or '
        "any other key. If the supplied evidence cannot answer the request, say "
        "what is missing plainly instead of guessing."
    )
    try:
        recovered = request_structured_completion(
            pool,
            system=safe_system,
            prompt=prompt,
            max_tokens=max(900, min(max_tokens, 2_400)),
        )
    except LLMCancelledError:
        raise
    except Exception:  # noqa: BLE001 - preserve the safe failure contract
        return None
    payload = extract_structured_object(
        str(getattr(recovered, "text", "")),
        required_keys={answer_key},
    )
    if payload is None:
        return None
    answer = str(payload.get(answer_key) or "").strip()
    if not answer:
        return None
    return {answer_key: answer, **empty_fields}
