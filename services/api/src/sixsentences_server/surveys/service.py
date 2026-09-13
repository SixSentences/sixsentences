"""Deterministic survey summaries plus a grounded AI analysis layer."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sixsentences_server.agent.actions import normalize_workspace_actions_for_request
from sixsentences_server.agent.events import emit_agent_event, emit_change_events
from sixsentences_server.agent.runtime import (
    merge_missing_actions,
    review_action_coverage,
)
from sixsentences_server.core.assistant_preferences import (
    assistant_preference_context,
    assistant_system_instruction,
)
from sixsentences_server.core.conversation import render_model_aware_context
from sixsentences_server.core.grounding import structured_response_failure
from sixsentences_server.core.locale import response_language_instruction
from sixsentences_server.core.structured_output import (
    extract_complete_string_field,
    extract_structured_object,
    recover_action_free_answer,
    recover_structured_object,
    request_structured_completion,
    structured_recovery_pool,
)
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError

QUESTION_TYPES = {
    "short_text",
    "long_text",
    "single_choice",
    "multiple_choice",
    "rating",
    "scale",
}

_FULL_REPLACEMENT_PATTERNS = (
    r"\b(?:alle|sämtliche|saemtliche|die alten?|bestehenden?)\s+fragen?\b.{0,40}"
    r"\b(?:ersetzen|austauschen|neu machen)\b",
    r"\b(?:alte|bestehende)\s+frage\b.{0,40}"
    r"\b(?:komplett|vollständig|vollstaendig)?\s*(?:ersetzen|austauschen)\b",
    r"\b(?:replace|remove)\s+(?:all\s+)?(?:old|existing|current)?\s*questions?\b",
    r"\b(?:auf|to)\s+(?:genau|exakt|exactly)\s+\d{1,2}\s+(?:fragen?|questions?)\b",
    r"\b(?:genau|exakt|exactly)\s+\d{1,2}\s+(?:fragen?|questions?)\b",
)
_ANONYMOUS_SURVEY_REQUEST = re.compile(
    r"\b(?:anonymous|anonym\w*)\b",
    re.IGNORECASE,
)
_IDENTITY_COLLECTION_REQUEST = re.compile(
    r"\b(?:name|names|namen?|e-?mail(?:s|adressen?)?|email(?:s|addresses?)?|"
    r"identity|identit[aä]t)\w*\b",
    re.IGNORECASE,
)
_ADD_RATING_REQUEST = re.compile(
    r"\b(?:add|append|insert|füg\w*|fueg\w*|ergänz\w*|ergaenz\w*)\b"
    r".{0,120}\b(?:rating|bewertung)\w*\b|"
    r"\b(?:rating|bewertung)\w*\b.{0,120}"
    r"\b(?:add|append|insert|hinzu|ergänz\w*|ergaenz\w*)\b",
    re.IGNORECASE,
)
_REPLACE_RATING_WITH_SCALE_REQUEST = re.compile(
    r"\b(?:scale|skala)\b.{0,100}\b(?:0\s*(?:bis|to|[-–])\s*10)\b"
    r"|\b(?:0\s*(?:bis|to|[-–])\s*10)\b.{0,100}\b(?:scale|skala)\b",
    re.IGNORECASE,
)
_DELETE_ALL_QUESTIONS_REQUEST = re.compile(
    r"\b(?:delete|remove|lösch\w*|loesch\w*|entfern\w*)\b.{0,80}"
    r"\b(?:all|alle|sämtliche|saemtliche|bisherigen|bestehenden)\b.{0,30}"
    r"\b(?:questions?|fragen?)\b",
    re.IGNORECASE,
)
_QUESTION_WORDING_IMPROVEMENT_REQUEST = re.compile(
    r"\b(?:questions?|fragen?)\b.{0,100}\b(?:wording|formulier\w*|"
    r"verständlicher|verstaendlicher|klarer|weniger\s+komisch|improv\w*|"
    r"rewrite|überarbeit\w*|ueberarbeit\w*)\b|"
    r"\b(?:wording|formulier\w*|verständlicher|verstaendlicher|klarer|"
    r"weniger\s+komisch|umgangssprach\w*|lockerer|freundlicher|natürlicher|"
    r"natuerlicher|einfacher|improv\w*|rewrite|überarbeit\w*|ueberarbeit\w*)\b"
    r".{0,100}\b(?:questions?|fragen?)\b",
    re.IGNORECASE,
)
_SURVEY_LANGUAGE_EDIT_REQUEST = re.compile(
    r"\b(?:sprache|ton|stil|formulier\w*|wording|language|tone|style)\b"
    r".{0,120}\b(?:anpass\w*|änder\w*|aender\w*|mach\w*|rewrite|change|"
    r"umgangssprach\w*|lockerer|freundlicher|verständlicher|verstaendlicher|"
    r"einfacher)\b|"
    r"\b(?:umgangssprach\w*|lockerer|freundlicher|verständlicher|"
    r"verstaendlicher|einfacher)\b.{0,120}\b(?:sprache|ton|stil|formulier\w*|"
    r"questions?|fragen?|survey|umfrage)\b",
    re.IGNORECASE,
)
_SURVEY_EDIT_REQUEST = re.compile(
    r"\b(?:create|build|add|append|insert|update|edit|change|rewrite|rename|"
    r"remove|delete|reorder|improve|erstell\w*|bau\w*|füg\w*|fueg\w*|"
    r"ergänz\w*|ergaenz\w*|änder\w*|aender\w*|anpass\w*|überarbeit\w*|"
    r"ueberarbeit\w*|formulier\w*|mach\w*|umbenenn\w*|lösch\w*|loesch\w*|"
    r"entfern\w*|sortier\w*)\b",
    re.IGNORECASE,
)
_READ_ONLY_REQUEST = re.compile(
    r"\b(?:read[ -]?only|nur\s+(?:prüf\w*|pruef\w*|lesen)|"
    r"(?:only|just)\s+(?:review|inspect|check)|"
    r"(?:nichts|nix)\s+(?:mehr\s+)?(?:änder\w*|aender\w*|bearbeit\w*)|"
    r"(?:do\s+not|don['’]?t|dont)\s+(?:change|edit|modify|update)\s+"
    r"anything(?!\s+(?:else|other)\b)|"
    r"(?:change|edit|modify)\s+nothing|keine\s+(?:änderungen|aenderungen))\b",
    re.IGNORECASE,
)

_NO_SURVEY_EDIT = re.compile(
    r"\b(?:nix|nichts|nicht|keine\w*|without|do\s+not|don't|dont)\b"
    r".{0,40}\b(?:änder\w*|aender\w*|edit\w*|change\w*|rewrite\w*|"
    r"anpass\w*|überarbeit\w*|ueberarbeit\w*)\b",
    re.IGNORECASE,
)
_EXPLICIT_SEPARATE_SURVEY_REQUEST = re.compile(
    r"\b(?:new|fresh|another|additional|separate|follow[ -]?up|"
    r"neu(?:e|en|er|es)?|weitere\w*|zusätzliche\w*|zusaetzliche\w*|"
    r"separate\w*)\b.{0,70}\b(?:survey|questionnaire|umfrage|fragebogen)\w*\b|"
    r"\b(?:survey|questionnaire|umfrage|fragebogen)\w*\b.{0,70}"
    r"\b(?:new|fresh|another|additional|separate|follow[ -]?up|"
    r"neu(?:e|en|er|es)?|weitere\w*|zusätzliche\w*|zusaetzliche\w*)\b",
    re.IGNORECASE,
)


def _consume_current_survey_creation(
    request: str,
    actions: list[dict[str, Any]],
    workspace_actions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn a misplaced create proposal into edits of the open survey.

    Inside the Survey Workspace, an unqualified "create a survey" request
    refers to the survey already on screen.  Models sometimes emit the global
    ``create_survey`` handoff anyway.  Reusing its validated payload keeps the
    exact generated questions while preventing a surprising second survey.
    """

    if _EXPLICIT_SEPARATE_SURVEY_REQUEST.search(request):
        return actions, workspace_actions
    current_actions = list(actions)
    remaining: list[dict[str, Any]] = []
    for proposal in workspace_actions:
        if proposal.get("type") != "create_survey":
            remaining.append(proposal)
            continue
        if not any(action.get("operation") == "set_title" for action in current_actions):
            title = str(proposal.get("title") or "").strip()
            if title:
                current_actions.append({"operation": "set_title", "value": title})
        if not any(action.get("operation") == "set_description" for action in current_actions):
            description = str(proposal.get("description") or "").strip()
            if description:
                current_actions.append({"operation": "set_description", "value": description})
        if not any(
            action.get("operation") in {"replace_questions", "add_question"}
            for action in current_actions
        ):
            questions = proposal.get("questions")
            if isinstance(questions, list) and questions:
                current_actions.append({"operation": "replace_questions", "questions": questions})
    return current_actions[:24], remaining


def _explicit_rating_actions(
    request: str,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Compile explicit novice rating/scale controls without model drift."""

    if _REPLACE_RATING_WITH_SCALE_REQUEST.search(request) and re.search(
        r"\b(?:replace|ersetzen|austausch\w*)\b",
        request,
        re.IGNORECASE,
    ):
        existing_rating = next(
            (question for question in questions if question.get("type") == "rating"),
            None,
        )
        if existing_rating is None:
            return None
        replacement = [dict(question) for question in questions if question is not existing_rating]
        title = str(existing_rating.get("title") or "Wie zufrieden sind Sie?")
        scale = {
            "id": "q_satisfaction_scale",
            "title": title,
            "description": str(existing_rating.get("description") or ""),
            "type": "scale",
            "required": bool(existing_rating.get("required")),
            "options": [],
            "min": 0,
            "max": 10,
        }
        position = (
            1
            if re.search(
                r"\b(?:after|nach)\b.{0,50}\b(?:consent|einwilligung)\w*\b",
                request,
                re.IGNORECASE,
            )
            else len(replacement)
        )
        replacement.insert(min(position, len(replacement)), scale)
        return [{"operation": "replace_questions", "questions": replacement}]

    if not _ADD_RATING_REQUEST.search(request):
        return None
    german = bool(re.search(r"\b(?:frage|zufriedenheit|füge|fuege|hinzu)\w*\b", request, re.I))
    return [
        {
            "operation": "add_question",
            "position": len(questions),
            "question": {
                "title": "Wie zufrieden sind Sie?" if german else "How satisfied are you?",
                "description": "",
                "type": "rating",
                "required": False,
                "options": [],
                "min": None,
                "max": None,
            },
        }
    ]


def _explicit_choice_edit(
    request: str,
    questions: list[dict[str, Any]],
    model_actions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Keep explicitly named choice options exact, including novice typos."""

    target_match = re.search(
        r"\b(?:frage|question)\s*(\d{1,2})\b",
        request,
        re.IGNORECASE,
    )
    if target_match is None:
        return None
    target_index = int(target_match.group(1)) - 1
    if not 0 <= target_index < len(questions):
        return None
    requested_type = None
    if re.search(
        r"\b(?:multiple[ -]?choice|mehr(?:fach|aus)wahl|mehrauswhal)\w*\b",
        request,
        re.IGNORECASE,
    ):
        requested_type = "multiple_choice"
    elif re.search(
        r"\b(?:single[ -]?choice|einfachauswahl|einzelauswahl)\w*\b",
        request,
        re.IGNORECASE,
    ):
        requested_type = "single_choice"
    if requested_type is None:
        return None

    proposed_options: list[str] = []
    target_id = str(questions[target_index].get("id") or "")
    for action in model_actions:
        if action.get("operation") != "update_question":
            continue
        if str(action.get("question_id") or "") not in {"", target_id}:
            continue
        changes = action.get("changes")
        if isinstance(changes, dict):
            proposed_options = _normalize_option_values(changes.get("options"))
        if proposed_options:
            break

    request_text = request.casefold()
    exact_options = [
        option
        for option in proposed_options
        if option.casefold() in request_text
        or (
            option.casefold() in {"other", "sonstiges"}
            and any(alias in request_text for alias in ("other", "sonstiges"))
        )
    ]
    if len(exact_options) < 2:
        tail_match = re.search(r"\b(?:mit|with)\b(.+)$", request, re.IGNORECASE)
        tail = tail_match.group(1) if tail_match else ""
        exact_options = list(
            dict.fromkeys(
                re.findall(
                    r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9+.#/-]*\b",
                    tail,
                )
            )
        )
    if len(exact_options) < 2:
        return None
    return [
        {
            "operation": "update_question",
            "question_id": target_id,
            "changes": {"type": requested_type, "options": exact_options},
        }
    ]


def _requests_full_question_replacement(request: str) -> bool:
    normalized = " ".join(request.casefold().split())
    return any(re.search(pattern, normalized) for pattern in _FULL_REPLACEMENT_PATTERNS)


def _requested_question_total(request: str) -> int | None:
    """Return an explicit requested final question count, if present."""
    normalized = " ".join(request.casefold().split())
    patterns = (
        r"\b(?:auf|to)\s+(?:genau|exakt|exactly)\s+(\d{1,2})\s+(?:fragen?|questions?)\b",
        r"\b(?:genau|exakt|exactly)\s+(\d{1,2})\s+(?:fragen?|questions?)\b",
        r"\b(?:make|mach\w*)\b.{0,40}\b(\d{1,2})\s+(?:fragen?|questions?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            requested = int(match.group(1))
            return requested if 1 <= requested <= 80 else None
    return None


def _planned_question_total(
    current_questions: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> int:
    """Estimate the final question count represented by validated actions."""
    total = len(current_questions)
    for action in actions:
        operation = str(action.get("operation") or "")
        if operation == "replace_questions":
            replacement = action.get("questions")
            return len(replacement) if isinstance(replacement, list) else total
        if operation == "add_question":
            total += 1
        elif operation == "delete_question":
            total = max(0, total - 1)
    return total


def _coerce_full_question_replacement(
    request: str,
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn explicit full-redesign add actions into one atomic replacement."""
    if not _requests_full_question_replacement(request):
        return actions
    if any(action.get("operation") == "replace_questions" for action in actions):
        return actions
    additions = [
        action
        for action in actions
        if action.get("operation") == "add_question" and isinstance(action.get("question"), dict)
    ]
    if not additions:
        return actions

    def _addition_order(item: tuple[int, dict[str, Any]]) -> int:
        raw_position = item[1].get("position")
        return raw_position if isinstance(raw_position, int) else len(additions) + item[0]

    ordered = sorted(
        enumerate(additions),
        key=_addition_order,
    )
    replacement = {
        "operation": "replace_questions",
        "questions": [dict(action["question"]) for _, action in ordered],
    }
    unrelated = [
        action
        for action in actions
        if action.get("operation") not in {"add_question", "update_question", "delete_question"}
    ]
    return [replacement, *unrelated]


def _targeted_survey_correction(
    request: str,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Resolve a narrowly phrased rename against one exact survey value."""
    normalized = " ".join(request.strip().split())
    patterns = (
        r"(?i)\bnenn(?:e)?\s+(.+?)\s+bitte\s+(.+?)(?:\s*,?\s*sonst\s+(?:nichts|nix).*)?$",
        r"(?i)\b(?:rename|change)\s+(.+?)\s+(?:to|into)\s+(.+?)"
        r"(?:\s*,?\s*(?:and\s+)?change\s+nothing\s+else.*)?$",
    )
    match = next(
        (candidate for pattern in patterns if (candidate := re.search(pattern, normalized))),
        None,
    )
    if match is None:
        return None
    old_value = match.group(1).strip(" \t\n\r.,:;\"'")
    new_value = match.group(2).strip(" \t\n\r.,:;\"'")
    if not old_value or not new_value or old_value.casefold() == new_value.casefold():
        return None

    matches: list[tuple[dict[str, Any], str, int | None]] = []
    for question in questions:
        for field_name in ("title", "description"):
            if str(question.get(field_name) or "").casefold() == old_value.casefold():
                matches.append((question, field_name, None))
        for option_position, option in enumerate(question.get("options") or []):
            if str(option).casefold() == old_value.casefold():
                matches.append((question, "options", option_position))
    if len(matches) != 1:
        return None
    question, field_name, matching_position = matches[0]
    if field_name == "options" and matching_position is not None:
        options = list(question.get("options") or [])
        options[matching_position] = new_value
        changes: dict[str, Any] = {"options": options}
    else:
        changes = {field_name: new_value}
    return [
        {
            "operation": "update_question",
            "question_id": str(question.get("id") or ""),
            "changes": changes,
        }
    ]


def _question_wording_updates(
    request: str,
    questions: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Keep wording-only edits useful without allowing schema drift."""

    if (
        _QUESTION_WORDING_IMPROVEMENT_REQUEST.search(request) is None
        and _SURVEY_LANGUAGE_EDIT_REQUEST.search(request) is None
    ):
        return None
    if re.search(
        r"\b(?:add|append|insert|füg\w*|fueg\w*|ergänz\w*|ergaenz\w*)\b"
        r".{0,100}\b(?:questions?|fragen?)\b",
        request,
        re.IGNORECASE,
    ):
        return None
    by_id = {str(question.get("id") or ""): question for question in questions}
    updates: list[dict[str, Any]] = []
    for action in actions:
        if action.get("operation") != "update_question":
            continue
        question_id = str(action.get("question_id") or "")
        current = by_id.get(question_id)
        changes = action.get("changes")
        if current is None or not isinstance(changes, dict):
            continue
        safe_changes: dict[str, str] = {}
        for field_name in ("title", "description"):
            value = changes.get(field_name)
            if not isinstance(value, str):
                continue
            normalized = " ".join(value.split()).strip()
            if normalized and normalized != str(current.get(field_name) or ""):
                safe_changes[field_name] = normalized
        if safe_changes:
            updates.append(
                {
                    "operation": "update_question",
                    "question_id": question_id,
                    "changes": safe_changes,
                }
            )
    return updates


def _explicit_choice_addition(
    request: str,
    questions: list[dict[str, Any]],
    model_actions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Preserve explicit choice controls and options in novice add requests."""

    if re.search(r"\b(?:frage|question)\s*\d{1,2}\b", request, re.I):
        return None
    if (
        re.search(
            r"\b(?:add|append|insert|füg\w*|fueg\w*|ergänz\w*|ergaenz\w*)\b",
            request,
            re.IGNORECASE,
        )
        is None
    ):
        return None
    if re.search(
        r"\b(?:multiple[ -]?choice|mehr(?:fach|aus)wahl|mehrauswhal)\w*\b",
        request,
        re.IGNORECASE,
    ):
        requested_type = "multiple_choice"
    elif re.search(
        r"\b(?:single[ -]?choice|einfachauswahl|einzelauswahl)\w*\b",
        request,
        re.IGNORECASE,
    ):
        requested_type = "single_choice"
    else:
        return None

    proposed: dict[str, Any] = {}
    position = len(questions)
    for action in model_actions:
        if action.get("operation") != "add_question":
            continue
        candidate = action.get("question")
        if not isinstance(candidate, dict):
            continue
        proposed = dict(candidate)
        raw_position = action.get("position")
        if isinstance(raw_position, int):
            position = raw_position
        break

    request_text = request.casefold()
    proposed_options = [
        option
        for option in _normalize_option_values(proposed.get("options"))
        if option.casefold() in request_text
    ]
    tail_match = re.search(r"\b(?:mit|with)\b(.+)$", request, re.IGNORECASE)
    tail = tail_match.group(1) if tail_match else ""
    tail = re.split(
        r",?\s{0,40}\b(?:auf\s+deutsch|in\s+german|auf\s+englisch|in\s+english)\b",
        tail,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    explicit_options = list(
        dict.fromkeys(
            re.findall(
                r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9+.#/-]*\b",
                tail,
            )
        )
    )
    # The model may omit the last explicitly named option even while returning
    # an otherwise valid control. The user's list is authoritative whenever it
    # contains at least two choices; do not silently accept a truncated model
    # proposal merely because it already passes the minimum-option validator.
    options = explicit_options if len(explicit_options) >= 2 else proposed_options
    if len(options) < 2:
        return None

    german = bool(re.search(r"\b(?:frage|studienphase|auf\s+deutsch|sonstiges)\b", request, re.I))
    title = " ".join(str(proposed.get("title") or "").split()).strip()
    if re.search(r"\bstudienphase\b", request, re.IGNORECASE):
        title = (
            "In welcher Studienphase befindest du dich?"
            if german
            else "What is your current study stage?"
        )
    elif not title:
        title = "Welche Option trifft zu?" if german else "Which option applies?"
    return [
        {
            "operation": "add_question",
            "position": position,
            "question": {
                "title": title,
                "description": str(proposed.get("description") or ""),
                "type": requested_type,
                "required": bool(proposed.get("required")),
                "options": options,
                "min": None,
                "max": None,
            },
        }
    ]


SURVEY_AGENT_SYSTEM = (
    "You are the Survey Workspace agent. You can design and edit the survey as well "
    "as rigorously analyze its responses. Return STRICT JSON only with this shape: "
    '{"answer":"<concise user-facing response>","actions":[...],'
    '"workspace_actions":[]}. '
    "Valid action objects are: "
    '{"operation":"set_title","value":"..."}; '
    '{"operation":"set_description","value":"..."}; '
    '{"operation":"add_question","position":1,"question":{"title":"...",'
    '"description":"","type":"short_text|long_text|single_choice|multiple_choice|'
    'rating|scale","required":false,"options":[],"min":null,"max":null}}; '
    '{"operation":"update_question","question_id":"q1","changes":{...}}; '
    '{"operation":"delete_question","question_id":"q1"}; '
    '{"operation":"replace_questions","questions":[...]}; '
    '{"operation":"reorder_questions","question_ids":["q2","q1"]}; '
    '{"operation":"set_confirmation","value":"..."}; '
    '{"operation":"set_collect_identity","value":true}. '
    "This agent is embedded inside one already open survey. Unqualified requests such "
    "as 'create a survey', 'make six questions' or 'build this questionnaire' target "
    "that open survey through actions. create_survey is available only when the current "
    "request explicitly asks for another, additional or separate survey. "
    "Only emit edit actions when the user explicitly asks to create, change, remove, "
    "reorder or improve the survey. An analysis request never changes the form. "
    "A request to make existing questions clearer, less awkward or better worded is an "
    "explicit edit request. Return focused update_question actions with the supplied "
    "question IDs. Change only title or description, preserve meaning, type, options, "
    "required state, range and order, and leave already clear questions unchanged. "
    "Never publish, close or delete the survey. Reuse the exact supplied question_id when "
    "updating or deleting. A position is zero-based. For a full redesign, use "
    "replace_questions; otherwise prefer focused actions. If responses already exist, "
    "do not delete/replace questions or change their type/options/range unless the user "
    "explicitly requested that disruptive change; in that case add "
    '"confirm_existing_responses":true to that action. '
    "For analysis, answer ONLY from the questionnaire, deterministic summary and "
    "submitted response rows. Never invent a respondent, count, percentage, quote, "
    "subgroup or causal interpretation. Distinguish observations from interpretations; "
    "small samples are exploratory. If the data cannot answer something, say so."
    " Existing survey questions and submitted responses are the active source material. "
    "Use them directly for requests to summarize, compare, analyze or write. This "
    "embedded agent works in the open survey and always returns workspace_actions as "
    "an empty list."
)


@dataclass(frozen=True)
class SurveyAgentTurn:
    """One model turn before its validated actions are applied."""

    answer: str
    actions: list[dict[str, Any]]
    workspace_actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SurveyMutation:
    """Validated survey state and the audit trail shown in the chat."""

    title: str
    description: str
    questions: list[dict[str, Any]]
    settings: dict[str, Any]
    results: list[dict[str, Any]]
    changed: bool


def normalize_questions(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalize the editable form contract."""
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw[:80]):
        title = str(item.get("title") or "").strip()[:500]
        if not title:
            continue
        question_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(item.get("id") or ""))[:40]
        if not question_id or question_id in seen:
            question_id = f"q{index + 1}"
            while question_id in seen:
                question_id += "x"
        seen.add(question_id)
        kind = str(item.get("type") or "short_text")
        if kind not in QUESTION_TYPES:
            kind = "short_text"
        options = _normalize_option_values(item.get("options"))
        if kind in {"single_choice", "multiple_choice"} and len(options) < 2:
            options = ["Option 1", "Option 2"]
        questions.append(
            {
                "id": question_id,
                "title": title,
                "description": str(item.get("description") or "").strip()[:800],
                "type": kind,
                "required": bool(item.get("required")),
                "options": options,
                "min": int(item.get("min", 1)) if kind == "scale" else None,
                "max": int(item.get("max", 10)) if kind == "scale" else None,
            }
        )
    return questions


def _normalize_option_values(raw: Any) -> list[str]:
    """Normalize choice options without ever treating a string as characters."""
    if isinstance(raw, str):
        values: list[Any] = re.split(r"\s{0,40}(?:\r?\n|\||;|,)\s{0,40}", raw.strip())
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
        # Older agent output could accidentally persist a string as a list of
        # characters. Repair only unmistakable delimiter-bearing sequences so
        # legitimate compact choices such as A/B/C remain separate options.
        if values and all(isinstance(value, str) and len(value) <= 1 for value in values):
            joined = "".join(str(value) for value in values).strip()
            if re.search(r"[\r\n|;,]", joined):
                values = re.split(r"\s{0,40}(?:\r?\n|\||;|,)\s{0,40}", joined)
    else:
        values = []

    normalized: list[str] = []
    for value in values[:30]:
        if isinstance(value, dict):
            value = (
                value.get("label") or value.get("title") or value.get("text") or value.get("value")
            )
        text = str(value or "").strip()[:180]
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def validate_answers(questions: list[dict[str, Any]], raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only known fields and reject missing required answers."""
    answers: dict[str, Any] = {}
    for question in questions:
        question_id = str(question["id"])
        value = raw.get(question_id)
        empty = value is None or value == "" or value == []
        if question.get("required") and empty:
            raise ValueError(f"Answer required: {question['title']}")
        if empty:
            continue
        kind = question.get("type")
        if kind == "multiple_choice":
            allowed = set(question.get("options") or [])
            picked = [str(item)[:180] for item in value] if isinstance(value, list) else []
            answers[question_id] = [item for item in picked if item in allowed]
        elif kind == "single_choice":
            text = str(value)[:180]
            if text not in set(question.get("options") or []):
                raise ValueError(f"Invalid option for: {question['title']}")
            answers[question_id] = text
        elif kind in {"rating", "scale"}:
            number = float(str(value))
            lower = 1 if kind == "rating" else int(question.get("min") or 1)
            upper = 5 if kind == "rating" else int(question.get("max") or 10)
            if not lower <= number <= upper:
                raise ValueError(f"Value out of range for: {question['title']}")
            answers[question_id] = number
        else:
            answers[question_id] = str(value).strip()[:10_000]
    return answers


def survey_summary(
    questions: list[dict[str, Any]], responses: list[dict[str, Any]]
) -> dict[str, Any]:
    """Exact descriptive profile used by both UI and analysis prompt."""
    total = len(responses)
    items: list[dict[str, Any]] = []
    for question in questions:
        question_id = str(question["id"])
        values = [row.get("answers", {}).get(question_id) for row in responses]
        values = [value for value in values if value not in (None, "", [])]
        kind = str(question.get("type") or "short_text")
        item: dict[str, Any] = {
            "id": question_id,
            "title": question.get("title", ""),
            "type": kind,
            "answered": len(values),
            "missing": total - len(values),
        }
        if kind in {"single_choice", "multiple_choice"}:
            flat = [
                entry
                for value in values
                for entry in (value if isinstance(value, list) else [value])
            ]
            counts = Counter(str(value) for value in flat)
            item["counts"] = [
                {
                    "option": option,
                    "count": counts.get(option, 0),
                    "percent": round(counts.get(option, 0) / len(values) * 100, 1)
                    if values
                    else 0.0,
                }
                for option in question.get("options", [])
            ]
        elif kind in {"rating", "scale"}:
            numeric = [float(value) for value in values]
            item.update(
                {
                    "mean": round(sum(numeric) / len(numeric), 2) if numeric else None,
                    "min": min(numeric) if numeric else None,
                    "max": max(numeric) if numeric else None,
                    "distribution": [
                        {"value": value, "count": count}
                        for value, count in sorted(Counter(numeric).items())
                    ],
                }
            )
        else:
            item["responses"] = [str(value)[:600] for value in values[:40]]
        items.append(item)
    complete = sum(
        all(
            row.get("answers", {}).get(str(question["id"])) not in (None, "", [])
            for question in questions
            if question.get("required")
        )
        for row in responses
    )
    return {
        "responses": total,
        "complete": complete,
        "completion_percent": round(complete / total * 100, 1) if total else 0.0,
        "questions": items,
    }


def run_survey_agent(
    pool: LLMPool,
    *,
    request: str,
    title: str,
    description: str,
    status: str,
    questions: list[dict[str, Any]],
    settings: dict[str, Any],
    responses: list[dict[str, Any]],
    history: list[dict[str, str]],
    language: str,
    assistant_preferences: dict[str, Any] | None = None,
) -> SurveyAgentTurn:
    """Plan one grounded analysis/design turn using a strict action contract."""
    emit_agent_event(
        "context.loaded",
        tool="survey.inspect",
        label="Read the current survey",
        detail=f"{len(questions)} questions with answer types and order",
    )
    material = {
        "survey": {
            "title": title,
            "description": description,
            "status": status,
            "settings": settings,
            "questions": questions,
            "response_count": len(responses),
        },
        "summary": survey_summary(questions, responses),
        "response_rows": [row.get("answers", {}) for row in responses[:300]],
    }
    prior = render_model_aware_context(history, pool=pool, current_request=request)
    preference_context = assistant_preference_context(assistant_preferences)
    prompt = (
        (f"Earlier survey conversation:\n{prior}\n\n" if prior else "")
        + f"Survey material:\n{json.dumps(material, ensure_ascii=False)[:80_000]}\n\n"
        + (f"{preference_context}\n\n" if preference_context else "")
        + f"User request: {request}"
    )
    system_prompt = (
        SURVEY_AGENT_SYSTEM
        + response_language_instruction(language)
        + assistant_system_instruction(assistant_preferences)
    )
    targeted_correction = _targeted_survey_correction(request, questions)
    # Exact value matching is not affirmative authorization. A negation in a
    # narrowly parsed rename remains read-only, including ambiguous label text.
    negated_correction = targeted_correction is not None and bool(
        re.search(
            r"\b(?:not|never|don['’]?t|nicht|niemals|keinesfalls)\b",
            request,
            re.IGNORECASE,
        )
    )
    read_only_text = request
    if targeted_correction is not None and not negated_correction:
        # A resolved, exact rename followed by "change nothing else" limits
        # that edit; it does not forbid it. An independent read-only instruction
        # anywhere else in the request must still veto every mutation.
        read_only_text = re.sub(
            r"\s{0,40},?\s{0,40}\b(?:sonst\s+(?:nichts|nix)\s+(?:mehr\s+)?"
            r"(?:ändern|aendern|bearbeiten)|(?:and\s+)?change\s+nothing\s+else)"
            r"\s{0,40}[.!?]{0,20}\s{0,40}$",
            "",
            request,
            flags=re.IGNORECASE,
        )
    read_only_request = negated_correction or bool(_READ_ONLY_REQUEST.search(read_only_text))
    if read_only_request:
        system_prompt += (
            " The current request is a read-only review. Answer the question from the "
            "supplied current state; return actions=[] and workspace_actions=[]. "
            "Do not continue an earlier edit, draft new changes, or claim anything was changed."
        )
    payload: dict[str, Any] | None = None
    recovery_pool = structured_recovery_pool(pool)
    # Mutations use the reliable structured-action route immediately. This avoids
    # waiting through a long prose response and a second formatter pass before an
    # obvious edit can reach the survey on screen.
    is_edit_request = bool(_SURVEY_EDIT_REQUEST.search(request)) and not bool(
        _NO_SURVEY_EDIT.search(request)
    )
    action_pool = recovery_pool if is_edit_request else pool
    emit_agent_event(
        "plan.created",
        label="Plan the survey turn",
        detail=(
            "Understand the requested outcome, preserve the current survey, then "
            "validate each proposed change."
        ),
        steps=[
            "Inspect current structure",
            "Draft requested changes",
            "Validate question types and order",
        ],
    )
    emit_agent_event(
        "tool.started",
        tool="survey.propose_changes",
        label="Draft the requested survey changes",
        detail="Drafting the requested questions, wording and settings for the open survey.",
    )
    raw_response = ""
    try:
        response = request_structured_completion(
            action_pool,
            system=system_prompt,
            prompt=prompt,
            max_tokens=3_200,
        )
        raw_response = response.text
        payload = extract_structured_object(
            raw_response,
            required_keys={"answer", "actions"},
        )
    except ProviderError:
        # Continue through the independent bounded recovery route. A transient
        # provider outage must not surface as an HTTP 500 in the survey chat.
        pass
    if payload is None:
        emit_agent_event(
            "tool.progress",
            tool="survey.propose_changes",
            label="Check the survey proposal",
            detail="Checking each proposed change against the survey structure and question types.",
        )
        payload = recover_structured_object(
            recovery_pool,
            system=system_prompt,
            prompt=prompt,
            max_tokens=3_200,
            required_keys={"answer", "actions"},
        )
    if payload is None:
        partial_answer = extract_complete_string_field(
            raw_response,
            field_names=("answer", "reply"),
        )
        if partial_answer:
            emit_agent_event(
                "tool.progress",
                tool="survey.propose_changes",
                label="Prepare the survey explanation",
                detail="Finalizing the explanation from the current survey and response set.",
            )
            payload = {
                "answer": partial_answer,
                "actions": [],
                "workspace_actions": [],
            }
    if payload is None:
        emit_agent_event(
            "tool.progress",
            tool="survey.propose_changes",
            label="Complete the survey answer",
            detail="Preparing a concise answer grounded in the open survey.",
        )
        payload = recover_action_free_answer(
            recovery_pool,
            system=system_prompt,
            prompt=prompt,
            max_tokens=3_200,
            answer_key="answer",
            empty_fields={"actions": [], "workspace_actions": []},
        )
    if payload is None:
        emit_agent_event(
            "tool.failed",
            tool="survey.propose_changes",
            label="Survey proposal needs another pass",
            detail="The requested survey outcome is not ready yet.",
        )
        return SurveyAgentTurn(
            answer=structured_response_failure(language),
            actions=[],
            workspace_actions=[],
        )
    answer = str(payload.get("answer") or payload.get("reply") or "").strip()[:10_000]
    if read_only_request:
        # This boundary precedes deterministic edits and completion repair. A
        # previous edit request or an unsolicited model action cannot authorize
        # a mutation after the user explicitly switched to inspection.
        if payload.get("actions") or payload.get("workspace_actions"):
            answer = (
                "Ich habe nichts geändert. Die reine Prüfung konnte ich nicht sicher "
                "abschließen; bitte versuche es erneut."
                if language.casefold().startswith("de")
                else "I left the survey unchanged. I could not safely complete the "
                "read-only review; please try again."
            )
        return SurveyAgentTurn(answer=answer, actions=[], workspace_actions=[])
    actions = [
        action
        for action in (payload.get("actions") or [])[:24]
        if isinstance(action, dict) and str(action.get("operation") or "").strip()
    ]
    wording_updates = _question_wording_updates(request, questions, actions)
    if wording_updates == []:
        wording_prompt = (
            "Rewrite only the awkward wording in the existing survey questions. "
            "Preserve meaning, question IDs, control types, options, required flags, "
            "ranges and order. Leave already clear questions unchanged. Return strict "
            'JSON as {"answer":"...","actions":[{"operation":"update_question",'
            '"question_id":"<existing id>","changes":{"title":"..."}}],'
            '"workspace_actions":[]} with at least one concrete update when wording can '
            "be improved.\n\n"
            f"Questions:\n{json.dumps(questions, ensure_ascii=False)}\n\n"
            f"User request: {request}"
        )
        try:
            wording_response = request_structured_completion(
                recovery_pool,
                system=system_prompt,
                prompt=wording_prompt,
                max_tokens=1_600,
            )
            wording_payload = extract_structured_object(
                wording_response.text,
                required_keys={"answer", "actions"},
            )
        except Exception:  # noqa: BLE001 - keep the original safe response
            wording_payload = None
        if wording_payload is not None:
            repaired_actions = [
                action
                for action in (wording_payload.get("actions") or [])[:24]
                if isinstance(action, dict)
            ]
            wording_updates = _question_wording_updates(
                request,
                questions,
                repaired_actions,
            )
    if wording_updates is not None:
        actions = wording_updates
        if wording_updates:
            answer = (
                "Ich habe die Formulierungen verständlicher gemacht. Fragetypen, "
                "Optionen, Pflichtfelder und Reihenfolge bleiben unverändert."
                if language == "de"
                else "I made the wording clearer. Question types, options, required "
                "fields and order remain unchanged."
            )
    choice_control = _explicit_choice_edit(request, questions, actions)
    choice_addition = _explicit_choice_addition(request, questions, actions)
    deterministic_control = _explicit_rating_actions(request, questions)
    if _DELETE_ALL_QUESTIONS_REQUEST.search(request):
        # A survey cannot be left without a question. Represent the refused
        # operation explicitly so the UI shows a concrete safety result even
        # when the model chooses to omit the destructive action altogether.
        actions = [{"operation": "replace_questions", "questions": []}]
    elif choice_control is not None:
        actions = choice_control
    elif choice_addition is not None:
        actions = choice_addition
    elif deterministic_control is not None:
        actions = deterministic_control
    elif targeted_correction is not None:
        actions = targeted_correction
    else:
        actions = _coerce_full_question_replacement(request, actions)
    if _ANONYMOUS_SURVEY_REQUEST.search(request) and _IDENTITY_COLLECTION_REQUEST.search(request):
        # An anonymous response set and collecting direct identifiers cannot
        # both be true. A novice may ask for both in the same sentence. Keep
        # the privacy-preserving interpretation deterministic instead of
        # letting a model silently enable identity collection.
        actions = [
            action for action in actions if action.get("operation") != "set_collect_identity"
        ]
        actions.append({"operation": "set_collect_identity", "value": False})
        warning = (
            "Eine anonyme Umfrage kann keine Namen oder E-Mail-Adressen erfassen. "
            "Die Identitätserfassung bleibt deshalb ausgeschaltet."
            if language == "de"
            else "An anonymous survey cannot collect names or email addresses. "
            "Identity collection therefore remains disabled."
        )
        answer = f"{warning} {answer}".strip()
    if is_edit_request and actions:
        review = review_action_coverage(
            pool,
            workspace="survey",
            request=request,
            state_summary=json.dumps(material["survey"], ensure_ascii=False),
            validated_actions=actions,
            action_contract=(
                "Survey actions from the documented contract: set_title, "
                "set_description, add_question, update_question, delete_question, "
                "replace_questions, reorder_questions, set_confirmation or "
                "set_collect_identity. Reuse existing question IDs."
            ),
            language=language,
        )
        if not review.complete:
            actions, added = merge_missing_actions(
                actions,
                review.missing_actions,
                singleton_operations={
                    "set_title",
                    "set_description",
                    "replace_questions",
                    "reorder_questions",
                    "set_confirmation",
                    "set_collect_identity",
                },
                dominant_operations={
                    "replace_questions": {
                        "add_question",
                        "update_question",
                        "delete_question",
                        "replace_questions",
                        "reorder_questions",
                    }
                },
                max_actions=24,
            )
            emit_agent_event(
                "tool.completed",
                tool="survey.complete_plan",
                label="Added the missing survey steps",
                detail=review.summary,
                result_count=added,
            )
    requested_total = _requested_question_total(request)
    if (
        requested_total is not None
        and _planned_question_total(questions, actions) != requested_total
    ):
        emit_agent_event(
            "tool.progress",
            tool="survey.complete_plan",
            label=f"Complete the requested {requested_total}-question survey",
            detail=(
                f"Preparing a complete {requested_total}-question draft with the "
                "requested controls and order."
            ),
        )
        exact_count_prompt = (
            f"Prepare the requested final survey with exactly {requested_total} questions. "
            "Return strict "
            'JSON as {"answer":"...","actions":[{"operation":"replace_questions",'
            '"questions":[...]}],"workspace_actions":[]}. The replacement must contain '
            f"exactly {requested_total} useful questions. Honor every explicitly numbered "
            "question type, option list, scale range and ordering instruction in the latest "
            "request. Target the open survey represented below.\n\n"
            f"Current survey:\n{json.dumps(material['survey'], ensure_ascii=False)}\n\n"
            f"Latest user request:\n{request}"
        )
        exact_payload: dict[str, Any] | None = None
        try:
            exact_response = request_structured_completion(
                recovery_pool,
                system=system_prompt,
                prompt=exact_count_prompt,
                max_tokens=4_000,
            )
            exact_payload = extract_structured_object(
                exact_response.text,
                required_keys={"answer", "actions"},
            )
        except Exception:  # noqa: BLE001 - preserve the already validated first plan
            exact_payload = None
        replacement = next(
            (
                dict(candidate)
                for candidate in (exact_payload or {}).get("actions", [])
                if isinstance(candidate, dict)
                and candidate.get("operation") == "replace_questions"
                and isinstance(candidate.get("questions"), list)
                and len(candidate["questions"]) == requested_total
            ),
            None,
        )
        if replacement is not None:
            actions = [
                replacement,
                *[
                    action
                    for action in actions
                    if action.get("operation")
                    not in {
                        "add_question",
                        "update_question",
                        "delete_question",
                        "replace_questions",
                        "reorder_questions",
                    }
                ],
            ][:24]
            answer = str((exact_payload or {}).get("answer") or answer).strip()[:10_000]
            emit_agent_event(
                "tool.completed",
                tool="survey.complete_plan",
                label=f"Prepared exactly {requested_total} questions",
                detail="The replacement now matches the requested final count and controls.",
                result_count=requested_total,
            )
        else:
            emit_agent_event(
                "tool.failed",
                tool="survey.complete_plan",
                label="Requested question count needs attention",
                detail=f"A complete {requested_total}-question proposal is not ready yet.",
            )
    workspace_actions = normalize_workspace_actions_for_request(
        payload.get("workspace_actions"),
        request,
    )
    if not actions:
        actions, workspace_actions = _consume_current_survey_creation(
            request,
            actions,
            workspace_actions,
        )
    # The embedded Survey agent is intentionally narrow. Even a model-generated
    # cross-workspace proposal is discarded here; creation and navigation belong
    # to Quick Answer, not to an editor that already has a survey open.
    workspace_actions = []
    emit_agent_event(
        "tool.completed",
        tool="survey.propose_changes",
        label="Survey proposal validated",
        detail=(
            f"{len(actions)} validated change"
            f"{'s' if len(actions) != 1 else ''} ready for the survey editor."
        ),
        result_count=len(actions),
    )
    emit_change_events(workspace="survey", changes=actions, applied=False)
    return SurveyAgentTurn(
        answer=answer or "I checked the survey and its current response set.",
        actions=actions,
        workspace_actions=workspace_actions,
    )


def _question_index(questions: list[dict[str, Any]], action: dict[str, Any]) -> int | None:
    question_id = str(action.get("question_id") or "").strip()
    if question_id:
        return next(
            (index for index, item in enumerate(questions) if item.get("id") == question_id),
            None,
        )
    raw_index = action.get("index")
    if isinstance(raw_index, int) and 0 <= raw_index < len(questions):
        return raw_index
    return None


def _action_result(
    operation: str,
    *,
    applied: bool,
    label: str,
    question_id: str = "",
    detail: str = "",
    before: Any = None,
    after: Any = None,
) -> dict[str, Any]:
    result = {
        "operation": operation,
        "applied": applied,
        "label": label[:240],
        "question_id": question_id,
        "detail": detail[:500],
    }
    if before is not None:
        result["before"] = before
    if after is not None:
        result["after"] = after
    return result


def apply_survey_actions(
    *,
    title: str,
    description: str,
    questions: list[dict[str, Any]],
    settings: dict[str, Any],
    actions: list[dict[str, Any]],
    response_count: int,
) -> SurveyMutation:
    """Apply only the documented survey tools and return their visible audit trail."""
    next_title = title
    next_description = description
    next_questions = [dict(question) for question in questions]
    next_settings = {
        "collect_identity": bool(settings.get("collect_identity")),
        "confirmation": str(settings.get("confirmation") or "Thank you for taking part.")[:500],
        **({"password_hash": settings["password_hash"]} if settings.get("password_hash") else {}),
        **(
            {"result_dataset_id": settings["result_dataset_id"]}
            if settings.get("result_dataset_id")
            else {}
        ),
    }
    results: list[dict[str, Any]] = []
    changed = False

    for action in actions[:24]:
        operation = str(action.get("operation") or "").strip()
        if operation == "set_title":
            value = str(action.get("value") or "").strip()[:240]
            if len(value) < 2:
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Update survey title",
                        detail="The title was empty.",
                    )
                )
                continue
            before = next_title
            changed = changed or value != before
            next_title = value
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label="Updated survey title",
                    before=before,
                    after=value,
                )
            )
            continue

        if operation == "set_description":
            value = str(action.get("value") or "").strip()[:4000]
            before = next_description
            changed = changed or value != before
            next_description = value
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label="Updated survey introduction",
                    before=before,
                    after=value,
                )
            )
            continue

        if operation == "set_confirmation":
            value = str(action.get("value") or "").strip()[:500]
            if not value:
                value = "Thank you for taking part."
            before = next_settings["confirmation"]
            changed = changed or value != before
            next_settings["confirmation"] = value
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label="Updated confirmation message",
                    before=before,
                    after=value,
                )
            )
            continue

        if operation == "set_collect_identity":
            identity_enabled = bool(action.get("value"))
            before = next_settings["collect_identity"]
            changed = changed or identity_enabled != before
            next_settings["collect_identity"] = identity_enabled
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label=(
                        "Enabled respondent names"
                        if identity_enabled
                        else "Disabled respondent names"
                    ),
                    before=before,
                    after=identity_enabled,
                )
            )
            continue

        if operation == "add_question":
            candidate = action.get("question")
            if not isinstance(candidate, dict):
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Add question",
                        detail="No valid question was supplied.",
                    )
                )
                continue
            raw = [*next_questions, {**candidate, "id": ""}]
            normalized = normalize_questions(raw)
            if len(normalized) != len(next_questions) + 1:
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Add question",
                        detail="The question needs a title.",
                    )
                )
                continue
            added = normalized.pop()
            position_raw = action.get("position")
            position = position_raw if isinstance(position_raw, int) else len(next_questions)
            position = max(0, min(position, len(next_questions)))
            normalized.insert(position, added)
            next_questions = normalized
            changed = True
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label=f"Added: {added['title']}",
                    question_id=str(added["id"]),
                    after=added,
                )
            )
            continue

        if operation == "update_question":
            index = _question_index(next_questions, action)
            changes = action.get("changes")
            if index is None or not isinstance(changes, dict):
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Update question",
                        detail="The target question was not found.",
                    )
                )
                continue
            disruptive = {"type", "options", "min", "max"} & set(changes)
            if response_count and disruptive and not action.get("confirm_existing_responses"):
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label=f"Protected: {next_questions[index]['title']}",
                        question_id=str(next_questions[index]["id"]),
                        detail=(
                            "Changing answer structure after responses exist needs "
                            "an explicit request."
                        ),
                    )
                )
                continue
            current_id = str(next_questions[index]["id"])
            before_question = dict(next_questions[index])
            allowed = {
                key: value
                for key, value in changes.items()
                if key in {"title", "description", "type", "required", "options", "min", "max"}
            }
            normalized = normalize_questions(
                [{**next_questions[index], **allowed, "id": current_id}]
            )
            if not normalized:
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Update question",
                        question_id=current_id,
                        detail="The updated question needs a title.",
                    )
                )
                continue
            updated = normalized[0]
            changed = changed or updated != next_questions[index]
            next_questions[index] = updated
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label=f"Updated: {updated['title']}",
                    question_id=current_id,
                    before=before_question,
                    after=updated,
                )
            )
            continue

        if operation == "delete_question":
            index = _question_index(next_questions, action)
            if index is None:
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Remove question",
                        detail="The target question was not found.",
                    )
                )
                continue
            target = next_questions[index]
            if len(next_questions) == 1:
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label=f"Kept: {target['title']}",
                        question_id=str(target["id"]),
                        detail="A survey needs at least one question.",
                    )
                )
                continue
            if response_count and not action.get("confirm_existing_responses"):
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label=f"Protected: {target['title']}",
                        question_id=str(target["id"]),
                        detail=(
                            "Removing a question after responses exist needs an explicit request."
                        ),
                    )
                )
                continue
            next_questions.pop(index)
            changed = True
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label=f"Removed: {target['title']}",
                    question_id=str(target["id"]),
                    before=target,
                )
            )
            continue

        if operation == "replace_questions":
            replacement = action.get("questions")
            normalized = normalize_questions(replacement if isinstance(replacement, list) else [])
            if not normalized:
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Replace questionnaire",
                        detail="At least one valid question is required.",
                    )
                )
                continue
            if response_count and not action.get("confirm_existing_responses"):
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Protected existing questionnaire",
                        detail=(
                            "Replacing questions after responses exist needs an explicit request."
                        ),
                    )
                )
                continue
            before_questions = list(next_questions)
            next_questions = normalized
            changed = True
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label=f"Created {len(normalized)} survey questions",
                    before=before_questions,
                    after=normalized,
                )
            )
            continue

        if operation == "reorder_questions":
            order = action.get("question_ids")
            requested = [str(item) for item in order] if isinstance(order, list) else []
            current = [str(item["id"]) for item in next_questions]
            if len(requested) != len(current) or set(requested) != set(current):
                results.append(
                    _action_result(
                        operation,
                        applied=False,
                        label="Reorder questions",
                        detail="The order must contain every current question exactly once.",
                    )
                )
                continue
            by_id = {str(item["id"]): item for item in next_questions}
            next_questions = [by_id[question_id] for question_id in requested]
            changed = changed or requested != current
            results.append(
                _action_result(
                    operation,
                    applied=True,
                    label="Reordered survey questions",
                    before=current,
                    after=requested,
                )
            )
            continue

        results.append(
            _action_result(
                operation or "unknown",
                applied=False,
                label="Survey action needs review",
                detail="This change is not supported by the survey editor.",
            )
        )

    return SurveyMutation(
        title=next_title,
        description=next_description,
        questions=next_questions,
        settings=next_settings,
        results=results,
        changed=changed,
    )
