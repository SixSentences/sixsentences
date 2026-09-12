"""Bounded, inspectable completion review for specialist agent turns.

This module implements an agentic checkpoint without exposing private model
reasoning. A specialist first proposes domain actions. The checkpoint then
asks the selected model whether those observable actions cover the current
request. It may return only missing actions, which still pass through the
domain's server-owned validators before use.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sixsentences_server.agent.events import (
    PUBLIC_PROGRESS_COPY_RULE,
    emit_agent_event,
    safe_agent_text,
    safe_event_value,
    safe_model_observation_value,
)
from sixsentences_server.core.structured_output import (
    extract_structured_object,
    request_structured_completion,
)
from sixsentences_server.llm.base import LLMCancelledError
from sixsentences_server.llm.providers import ProviderError


@dataclass(frozen=True)
class AgentCompletionReview:
    """Observable outcome of one bounded completion checkpoint."""

    complete: bool
    summary: str
    missing_actions: list[dict[str, Any]]


@dataclass(frozen=True)
class AgentExecutionReview:
    """User-facing conclusion after server-owned tools actually ran."""

    answer: str
    complete: bool
    summary: str


_RESULT_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?%?")
_TRANSCRIPT_SEGMENT_PROVENANCE = re.compile(
    r"(?ix)\b(?:seg(?:ment)?\.?|segment(?:e|en|s|nummern?)?)\s*"
    r"(?:nr\.?|number|nummer)?\s*"
    r"(?P<values>\d+(?:\s*(?:,|/|&|and|und)\s*\d+){0,9})"
)
_TRANSCRIPT_SEGMENT_RANGE = re.compile(
    r"(?ix)\b(?:seg(?:ment)?\.?|segment(?:e|en|s|nummern?)?)\s*"
    r"(?:nr\.?|number|nummer)?\s*\d+(?:\s*(?:,|/|&|and|und)\s*\d+)*"
    r"\s*(?:-|–|—|bis|to|through)\s*\d+"
)
_TRANSCRIPT_RECEIPT_COUNT = re.compile(
    r"(?ix)\b(?P<value>\d+)\s+"
    r"(?:verified|verifiziert\w*)\s+"
    r"(?:(?:transcript|transkript)[\s-]*)?"
    r"(?:passages?|quotes?|stellen|passagen|zitate)\b"
)
_FALSE_MISSING_RESULT = re.compile(
    r"(?ix)"
    r"(?:"
    r"(?:operation|tool|transcript(?:\s+operation)?)\s+(?:was\s+)?not\s+"
    r"(?:executed|performed|run)|"
    r"no\s+(?:verified|checked|validated)\s+(?:quotes?|passages?|outputs?|results?)|"
    r"not\s+(?:executed|performed|run)|"
    r"nicht\s+(?:ausgef(?:ü|ue)hrt|durchgef(?:ü|ue)hrt)|"
    r"keine\s+(?:verifizierten|gepr(?:ü|ue)ften|validierten)\s+"
    r"(?:zitate|passagen|ausgaben|ergebnisse)"
    r")"
)


def _transcript_execution_outcome(
    executed_results: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return the validated transcript outcome and its exact quote receipts."""

    transcript_results = [
        result
        for result in executed_results
        if str(result.get("operation") or "") == "answer_from_transcript"
        and result.get("read_only") is True
    ]
    if not transcript_results:
        return None, []
    statuses = {str(result.get("status") or "").strip().casefold() for result in transcript_results}
    quotes: list[dict[str, Any]] = []
    receipt_counts_are_valid = True
    for result in transcript_results:
        raw_quotes = result.get("verified_quotes")
        result_quotes = (
            [
                dict(quote)
                for quote in raw_quotes
                if isinstance(quote, dict)
                and quote.get("verified") is True
                and str(quote.get("text") or "").strip()
            ]
            if isinstance(raw_quotes, list)
            else []
        )
        raw_count = result.get("verified_quote_count")
        receipt_counts_are_valid = receipt_counts_are_valid and (
            isinstance(raw_count, int)
            and not isinstance(raw_count, bool)
            and raw_count == len(result_quotes)
        )
        quotes.extend(result_quotes)
    if receipt_counts_are_valid and statuses == {"completed"} and quotes:
        return "completed", quotes
    if receipt_counts_are_valid and statuses == {"empty"} and not quotes:
        return "empty", []
    # A completed receipt without evidence, an empty receipt with quotes, an
    # explicit failure, or mixed statuses is an invalid/failed contract.
    return "failed", []


def _extractive_transcript_answer(
    outcome: str,
    quotes: list[dict[str, Any]],
    *,
    language: str,
) -> tuple[str, bool, str]:
    """Build a fail-closed answer using only exact verified transcript text."""

    german = language.lower().startswith("de")
    if outcome == "completed" and quotes:
        lines = ["Verifizierte Transkriptstellen:" if german else "Verified transcript passages:"]
        for quote in quotes[:5]:
            provenance = [str(quote.get("speaker") or "").strip()]
            segment = quote.get("segment")
            if segment not in (None, ""):
                provenance.append(f"Seg. {segment}")
            timestamp = str(quote.get("timestamp") or "").strip()
            if timestamp:
                provenance.append(timestamp)
            suffix = " · ".join(item for item in provenance if item)
            line = f"- “{str(quote.get('text') or '').strip()}”"
            lines.append(f"{line} — {suffix}" if suffix else line)
        summary = (
            "Die abschließende Antwort wurde direkt aus den verifizierten "
            "Transkriptstellen erstellt."
            if german
            else "The final answer was rebuilt directly from the verified transcript passages."
        )
        return "\n".join(lines), True, summary
    if outcome == "empty":
        answer = (
            "Im Transkript gibt es dazu keine verifizierbare Stelle. Ich kann "
            "deshalb keine belegte Aussage dazu machen."
            if german
            else "The transcript contains no verifiable passage about that. I "
            "therefore cannot make a supported claim about it."
        )
        return answer, True, answer
    answer = (
        "Die Antwort aus dem Transkript konnte nicht sicher verifiziert werden. "
        "Es wurde keine unbelegte Aussage übernommen."
        if german
        else "The transcript answer could not be verified safely. No unsupported "
        "claim was retained."
    )
    return answer, False, answer


def transcript_execution_fallback(
    executed_results: list[dict[str, Any]],
    *,
    language: str,
) -> AgentExecutionReview:
    """Return the deterministic safe answer for a transcript execution receipt.

    This is also used when the optional narration-review pool cannot be
    created, so provider or entitlement failures cannot revive an unchecked
    initial draft.
    """

    outcome, quotes = _transcript_execution_outcome(executed_results)
    answer, complete, summary = _extractive_transcript_answer(
        outcome or "failed",
        quotes,
        language=language,
    )
    return AgentExecutionReview(answer=answer, complete=complete, summary=summary)


def _transcript_review_is_supported(
    answer: str,
    summary: str,
    *,
    verified_quotes: list[dict[str, Any]],
) -> bool:
    """Reject false missing-result claims and numbers absent from tool output."""

    if not answer.strip() or _FALSE_MISSING_RESULT.search(f"{summary}\n{answer}"):
        return False
    if _TRANSCRIPT_SEGMENT_RANGE.search(f"{summary}\n{answer}"):
        # Ranges imply every intermediate segment. The review must name the
        # exact verified IDs instead of compressing them into an ambiguous span.
        return False
    # Numeric metadata such as transcript segment counts and revision numbers is
    # not evidence for a substantive finding. Explicit provenance anchors are
    # accepted only when each value matches its concrete verified quote receipt;
    # they are then removed before the remaining substantive numbers are checked.
    segments = {
        int(segment)
        for quote in verified_quotes
        for segment in (quote.get("segment"),)
        if isinstance(segment, int) and not isinstance(segment, bool)
    }
    timestamps = {
        str(quote.get("timestamp") or "").strip()
        for quote in verified_quotes
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", str(quote.get("timestamp") or "").strip())
    }
    provenance_is_valid = True

    def remove_segment_numbers(match: re.Match[str]) -> str:
        nonlocal provenance_is_valid
        values = {int(value) for value in re.findall(r"\d+", match.group("values"))}
        if not values or not values <= segments:
            provenance_is_valid = False
            return match.group(0)
        return _RESULT_NUMBER.sub("", match.group(0))

    def remove_receipt_count(match: re.Match[str]) -> str:
        nonlocal provenance_is_valid
        if int(match.group("value")) != len(verified_quotes):
            provenance_is_valid = False
            return match.group(0)
        return match.group(0).replace(match.group("value"), "", 1)

    checked = _TRANSCRIPT_SEGMENT_PROVENANCE.sub(
        remove_segment_numbers,
        f"{summary}\n{answer}",
    )
    checked = _TRANSCRIPT_RECEIPT_COUNT.sub(remove_receipt_count, checked)
    for timestamp in sorted(timestamps, key=len, reverse=True):
        # A bare matching clock value could still be a substantive duration.
        # Remove it only in an explicit transcript-provenance shape.
        checked = re.sub(
            rf"(?ix)(?:\(\s*{re.escape(timestamp)}\s*\)|"
            rf"\b(?:timestamp|timecode|zeit(?:stempel|marke|angabe)?|um|at)"
            rf"\s*[:=]?\s*{re.escape(timestamp)})(?!\d)",
            lambda match: _RESULT_NUMBER.sub("", match.group(0)),
            checked,
        )
    if not provenance_is_valid:
        return False

    # Only remaining numbers spoken in exact verified passages may appear in
    # the public answer or checkpoint summary. Segment IDs are never added to
    # this set globally, so e.g. "3 participants" cannot pass via Segment 3.
    allowed_source = "\n".join(str(quote.get("text") or "") for quote in verified_quotes)
    allowed_numbers = {token.replace(",", ".") for token in _RESULT_NUMBER.findall(allowed_source)}
    claimed_numbers = {token.replace(",", ".") for token in _RESULT_NUMBER.findall(checked)}
    return claimed_numbers <= allowed_numbers


def merge_missing_actions(
    validated_actions: list[dict[str, Any]],
    missing_actions: list[dict[str, Any]],
    *,
    singleton_operations: set[str] | frozenset[str] = frozenset(),
    dominant_operations: dict[str, set[str] | frozenset[str]] | None = None,
    max_actions: int = 24,
) -> tuple[list[dict[str, Any]], int]:
    """Merge checkpoint actions without replaying aggregate workspace edits.

    Completion checkpoints are advisory.  A model may phrase a second complete
    replacement differently and therefore bypass an exact JSON de-duplication
    check.  Domain controllers use this helper to declare operations that may
    occur only once, and aggregate operations that already cover narrower
    mutations.  The returned plan preserves the original validated order.
    """

    merged = [dict(action) for action in validated_actions[:max_actions]]
    exact = {json.dumps(action, sort_keys=True, ensure_ascii=False) for action in merged}
    operations = {
        str(action.get("operation") or "").strip()
        for action in merged
        if str(action.get("operation") or "").strip()
    }
    dominance = dominant_operations or {}
    added = 0
    for candidate in missing_actions:
        if len(merged) >= max_actions or not isinstance(candidate, dict):
            break
        operation = str(candidate.get("operation") or "").strip()
        if not operation:
            continue
        if operation in singleton_operations and operation in operations:
            continue
        if any(
            dominant in operations and operation in covered
            for dominant, covered in dominance.items()
        ):
            continue
        key = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
        if key in exact:
            continue
        merged.append(dict(candidate))
        exact.add(key)
        operations.add(operation)
        added += 1
    return merged, added


def review_action_coverage(
    pool: Any,
    *,
    workspace: str,
    request: str,
    state_summary: str,
    validated_actions: list[dict[str, Any]],
    action_contract: str,
    language: str,
    max_missing_actions: int = 6,
) -> AgentCompletionReview:
    """Let the selected model close observable gaps in a specialist plan.

    This is deliberately one bounded checkpoint. It produces a concise work
    summary rather than hidden chain-of-thought. A malformed checkpoint never
    invalidates the already verified first round.
    """

    emit_agent_event(
        "checkpoint.started",
        tool=f"{workspace}.check_completion",
        label="Check the work against the request",
        detail=(
            "Comparing every validated action with the user's requested outcome "
            "before the turn is marked complete."
        ),
        input=safe_event_value(
            {
                "request": request,
                "workspace_state": state_summary,
                "validated_actions": validated_actions,
            }
        ),
    )
    system = (
        "You are the completion controller for one SixSentences specialist agent. "
        "Inspect only the observable user request, workspace summary and already "
        "validated actions. Do not reveal hidden reasoning or a private scratchpad. "
        "Return exactly one JSON object with this schema: "
        '{"status":"complete|continue","summary":"short user-facing work '
        'checkpoint","missing_actions":[]}. '
        "Use status complete when the validated actions cover the request. Use "
        "continue only when a concrete requested outcome is absent, and then return "
        f"at most {max_missing_actions} missing actions. Never repeat an existing "
        "action and never invent data, evidence, files, sources, answers or settings. "
        f"The summary follows this public copy rule: {PUBLIC_PROGRESS_COPY_RULE} "
        f"Every missing action must follow this domain contract: {action_contract} "
        f"Write the summary in {'German' if language.lower().startswith('de') else 'English'}."
    )
    prompt = (
        f"Workspace: {workspace}\n"
        f"Current workspace summary: {state_summary[:12_000]}\n"
        f"User request: {request[:8_000]}\n"
        "Already validated actions:\n"
        f"{json.dumps(safe_event_value(validated_actions), ensure_ascii=False)[:24_000]}"
    )
    payload: dict[str, Any] | None = None
    try:
        response = request_structured_completion(
            pool,
            system=system,
            prompt=prompt,
            max_tokens=1_600,
        )
        payload = extract_structured_object(
            str(getattr(response, "text", "")),
            required_keys={"status", "summary", "missing_actions"},
        )
    except LLMCancelledError:
        raise
    except ProviderError:
        payload = None
    except Exception:  # noqa: BLE001 - checkpoint is advisory and fail-safe
        payload = None

    if payload is None:
        summary = (
            "Die validierten Schritte bleiben als aktuelles Arbeitsergebnis erhalten."
            if language.lower().startswith("de")
            else "The validated steps remain available as the current work result."
        )
        emit_agent_event(
            "checkpoint.completed",
            tool=f"{workspace}.check_completion",
            label="Current work result ready",
            detail=summary,
            result_count=len(validated_actions),
        )
        return AgentCompletionReview(True, summary, [])

    status = str(payload.get("status") or "complete").strip().casefold()
    summary = str(payload.get("summary") or "").strip()[:2_000]
    raw_missing = payload.get("missing_actions")
    missing = (
        [dict(item) for item in raw_missing[:max_missing_actions] if isinstance(item, dict)]
        if isinstance(raw_missing, list)
        else []
    )
    complete = status != "continue" or not missing
    public_summary = safe_agent_text(
        summary,
        fallback=(
            "All requested outcomes are represented by validated actions."
            if complete
            else "The checkpoint found requested outcomes that still need an action."
        ),
    )
    emit_agent_event(
        "checkpoint.completed",
        tool=f"{workspace}.check_completion",
        label="Work plan complete" if complete else "Continue with missing steps",
        detail=public_summary,
        output=safe_event_value(
            {
                "status": "complete" if complete else "continue",
                "missing_actions": missing,
            }
        ),
        result_count=len(missing),
    )
    return AgentCompletionReview(complete, public_summary, missing)


def review_execution_results(
    pool: Any,
    *,
    workspace: str,
    request: str,
    initial_answer: str,
    executed_results: list[dict[str, Any]],
    language: str,
    max_review_rounds: int = 2,
) -> AgentExecutionReview:
    """Let the model inspect real tool outputs before it closes the turn.

    This is a bounded result-observation round, not a private reasoning trace.
    It receives only safe, server-validated outputs and may return a corrected
    user-facing answer.  A provider or schema failure preserves the first safe
    answer and never invalidates completed deterministic work.
    """

    safe_results = safe_event_value(executed_results)
    model_results = safe_model_observation_value(executed_results)
    public_fallback = (
        "Das angeforderte Ergebnis benötigt einen weiteren geprüften Durchlauf."
        if language.lower().startswith("de")
        else "The requested outcome needs another validated pass."
    )

    def is_staged_result(result: dict[str, Any]) -> bool:
        if bool(result.get("applied")):
            return False
        operation = str(result.get("operation") or "")
        if workspace != "manuscript":
            return result.get("staged") is True
        if operation == "edit_source":
            return result.get("staged") is True and result.get("applicable") is True
        if operation == "compile_candidate":
            return str(result.get("status") or "").strip().casefold() == "passed"
        if operation == "prepare_visual_request":
            return result.get("staged") is True
        return result.get("staged") is True

    staged_only = bool(executed_results) and all(
        is_staged_result(result) for result in executed_results
    )
    invalid_manuscript_results = workspace == "manuscript" and any(
        str(result.get("operation") or "")
        in {"edit_source", "compile_candidate", "prepare_visual_request"}
        and not is_staged_result(result)
        for result in executed_results
    )
    if invalid_manuscript_results:
        summary = (
            "Die vorgeschlagenen Manuskriptänderungen konnten nicht vollständig "
            "validiert werden und sind nicht zur Prüfung bereit. Das gespeicherte "
            "Manuskript wurde nicht verändert."
            if language.lower().startswith("de")
            else "The proposed manuscript changes could not be fully validated and "
            "are not ready for review. The saved manuscript was not changed."
        )
        emit_agent_event(
            "checkpoint.progress",
            tool=f"{workspace}.inspect_results",
            label="Changes need correction",
            detail=summary,
            output={"status": "needs_attention", "result_count": len(executed_results)},
            result_count=len(executed_results),
        )
        return AgentExecutionReview(summary, False, summary)
    if staged_only:
        summary = (
            "Die validierten Änderungen sind zur Prüfung vorgemerkt. Erst nach "
            "Ihrer Bestätigung werden sie in den geöffneten Arbeitsbereich übernommen."
            if language.lower().startswith("de")
            else "The validated changes are staged for review. They will update the "
            "open workspace only after you confirm them."
        )
        emit_agent_event(
            "checkpoint.completed",
            tool=f"{workspace}.inspect_results",
            label="Changes ready for review",
            detail=summary,
            output={"status": "staged", "result_count": len(executed_results)},
            result_count=len(executed_results),
        )
        return AgentExecutionReview(
            safe_agent_text(initial_answer, fallback=public_fallback),
            True,
            summary,
        )

    transcript_outcome, transcript_quotes = _transcript_execution_outcome(executed_results)

    emit_agent_event(
        "checkpoint.started",
        tool=f"{workspace}.inspect_results",
        label="Review the completed work",
        detail=("Checking how the completed results address the requested outcome."),
        input=safe_results,
    )
    system = (
        "You are the result controller for one SixSentences specialist agent. "
        "You receive a user request, an initial draft answer and outputs from "
        "server-owned tools that have already run. Do not reveal hidden reasoning, "
        "a private scratchpad or provider internals. Return exactly one JSON object "
        "with this schema: "
        '{"status":"complete|needs_attention","summary":"short operational '
        'checkpoint","answer":"final user-facing answer"}. '
        "Base every factual statement and number on the supplied tool outputs. "
        "A result with operation=answer_from_transcript, read_only=true and "
        "status=completed DID run successfully when it contains verified_quotes; "
        "applied=false only means that this read-only operation did not mutate the "
        "workspace. Never describe such a result as unexecuted or unavailable. "
        "Status=empty means the transcript was checked but yielded no verifiable "
        "passage. Status=failed means no transcript answer was validated. "
        "When citing transcript provenance, write each verified receipt anchor as "
        "Segment N or Seg. N (comma-separated IDs are allowed), never as a range or "
        "bare [N]. Include only receipt segment IDs. Write a receipt timestamp only "
        "when it is labelled as a timestamp/timecode or placed in parentheses, and "
        "include only the timestamp attached to that verified quote. "
        "State failed or unavailable results plainly. Never imply that a failed "
        "operation succeeded, never invent values, and do not ask the user to run "
        "an operation that already completed. The answer should say what was done, "
        "what the results mean, and any genuine limitation. "
        f"The summary follows this public copy rule: {PUBLIC_PROGRESS_COPY_RULE} "
        f"Write in {'German' if language.lower().startswith('de') else 'English'}."
    )
    prompt = (
        f"Workspace: {workspace}\n"
        f"User request: {request[:8_000]}\n"
        f"Initial answer draft: {initial_answer[:8_000]}\n"
        "Validated execution results:\n"
        f"{json.dumps(model_results, ensure_ascii=False)[:32_000]}"
    )
    payload: dict[str, Any] | None = None
    rounds = min(2, max(1, int(max_review_rounds)))
    current_answer = initial_answer
    for round_index in range(rounds):
        round_prompt = prompt
        if round_index:
            round_prompt += (
                "\n\nThe first result review reported that the user-facing outcome "
                "still needed attention. Re-check the same validated outputs, correct "
                "the answer without inventing or executing anything, and decide whether "
                "the bounded result review can now close.\n"
                f"Previous safe answer: {current_answer[:8_000]}"
            )
        try:
            response = request_structured_completion(
                pool,
                system=system,
                prompt=round_prompt,
                max_tokens=2_200,
            )
            candidate = extract_structured_object(
                str(getattr(response, "text", "")),
                required_keys={"status", "summary", "answer"},
            )
        except LLMCancelledError:
            raise
        except ProviderError:
            candidate = None
        except Exception:  # noqa: BLE001 - preserve deterministic tool results
            candidate = None
        if candidate is None:
            break
        payload = candidate
        candidate_answer = str(candidate.get("answer") or "").strip()[:12_000]
        if candidate_answer:
            current_answer = candidate_answer
        status = str(candidate.get("status") or "complete").strip().casefold()
        if status == "complete" or round_index + 1 >= rounds:
            break
        emit_agent_event(
            "checkpoint.progress",
            tool=f"{workspace}.inspect_results",
            label="Complete the result summary",
            detail=safe_agent_text(
                str(candidate.get("summary") or "").strip()[:2_000],
                fallback=("The result report is being completed from the validated outputs."),
            ),
            output={"status": "needs_attention"},
        )

    if payload is None:
        if transcript_outcome is not None:
            fallback = transcript_execution_fallback(
                executed_results,
                language=language,
            )
            answer, complete, summary = (
                fallback.answer,
                fallback.complete,
                fallback.summary,
            )
            emit_agent_event(
                "checkpoint.completed",
                tool=f"{workspace}.inspect_results",
                label=(
                    "Verified transcript result"
                    if complete
                    else "Transcript result needs attention"
                ),
                detail=summary,
                output={
                    "status": "complete" if complete else "needs_attention",
                    "result_count": len(executed_results),
                },
                result_count=len(executed_results),
            )
            return AgentExecutionReview(answer, complete, summary)
        summary = (
            "Die validierten Ergebnisse bleiben als abgeschlossenes Arbeitsergebnis erhalten."
            if language.lower().startswith("de")
            else "The validated results remain available as the completed work result."
        )
        emit_agent_event(
            "checkpoint.completed",
            tool=f"{workspace}.inspect_results",
            label="Validated results ready",
            detail=summary,
            output=safe_results,
            result_count=len(executed_results),
        )
        return AgentExecutionReview(
            safe_agent_text(initial_answer, fallback=public_fallback),
            True,
            summary,
        )

    answer = current_answer or initial_answer
    summary = str(payload.get("summary") or "").strip()[:2_000]
    complete = str(payload.get("status") or "complete").strip().casefold() == "complete"
    if transcript_outcome is not None:
        supported = (
            transcript_outcome == "completed"
            and complete
            and _transcript_review_is_supported(
                answer,
                summary,
                verified_quotes=transcript_quotes,
            )
        )
        if not supported:
            fallback = transcript_execution_fallback(
                executed_results,
                language=language,
            )
            answer, complete, summary = (
                fallback.answer,
                fallback.complete,
                fallback.summary,
            )
    answer = safe_agent_text(answer, fallback=public_fallback)
    summary = safe_agent_text(summary, fallback=public_fallback)
    emit_agent_event(
        "checkpoint.completed",
        tool=f"{workspace}.inspect_results",
        label="Result review complete" if complete else "Result needs attention",
        detail=summary,
        output=safe_event_value(
            {
                "status": "complete" if complete else "needs_attention",
                "result_count": len(executed_results),
            }
        ),
        result_count=len(executed_results),
    )
    return AgentExecutionReview(answer, complete, summary)
