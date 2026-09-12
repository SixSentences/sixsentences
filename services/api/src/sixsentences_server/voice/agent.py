"""The study designer agent: conversation plus a strict action contract.

Same pattern as the Data Hub and Survey agents: the model sees the exact
study state and may answer from it or propose validated operations; the
endpoint applies them through the same rules as the manual editor, so the
agent can never do anything the researcher could not. The research
hypothesis has no representation here, by design.
"""

from __future__ import annotations

import re
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
from sixsentences_server.voice.service import (
    MAX_SECTIONS,
    TONES,
    VOICES,
    normalize_guide,
)

PERSONA_FIELDS = {
    "tone",
    "voice",
    "language",
    "mode",
    "patience_ms",
    "max_session_minutes",
    "retention",
    "budget_minutes",
}
CONSENT_FIELDS = {"consent_text", "contact_line"}

_COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "eine": 1,
    "einen": 1,
    "einer": 1,
    "eins": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "fuenf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "elf": 11,
    "zwölf": 12,
    "zwoelf": 12,
}
_EXACT_GUIDE_COUNT = re.compile(
    r"\b(?:exactly|genau|nur)\s+(\d+|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve|eine[rsn]?|eins|zwei|drei|vier|fünf|fuenf|"
    r"sechs|sieben|acht|neun|zehn|elf|zwölf|zwoelf)\s+"
    r"(?:open\s+|offen\w*\s+)?(?:core\s+)?(?:questions?|kernfragen?|fragen?|opening\s+question|"
    r"eröffnungsfrage|eroeffnungsfrage)",
    re.IGNORECASE,
)
_PLAIN_GUIDE_COUNT = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"eine[rsn]?|eins|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|"
    r"zehn|elf|zwölf|zwoelf)\s+"
    r"(?:open\s+|offen\w*\s+)?(?:topics?|themen|sections?|abschnitte?|"
    r"questions?|fragen?)\b",
    re.IGNORECASE,
)
_STUDY_EDIT_REQUEST = re.compile(
    r"\b(?:create|build|add|append|insert|update|edit|change|rewrite|rename|"
    r"remove|delete|extend|expand|erstell\w*|bau\w*|füg\w*|fueg\w*|"
    r"ergänz\w*|ergaenz\w*|änder\w*|aender\w*|anpass\w*|überarbeit\w*|"
    r"ueberarbeit\w*|umbenenn\w*|lösch\w*|loesch\w*|entfern\w*|"
    r"erweiter\w*)\b",
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

_NO_STUDY_EDIT = re.compile(
    r"\b(?:nix|nichts|nicht|keine\w*|without|do\s+not|don't|dont)\b"
    r".{0,40}\b(?:änder\w*|aender\w*|edit\w*|change\w*|rewrite\w*|"
    r"anpass\w*|überarbeit\w*|ueberarbeit\w*)\b",
    re.IGNORECASE,
)
_ONE_PROBE_EACH = re.compile(
    r"\b(?:je|jeweils|each|every)\s+(?:genau\s+|exactly\s+)?(?:eine[rsn]?|one|1)\s+"
    r"(?:nachfrage|follow[ -]?up|probe)\b|"
    r"\b(?:eine[rsn]?|one|1)\s+(?:nachfrage|follow[ -]?up|probe)\s+"
    r"(?:je|jeweils|each|per)\b",
    re.IGNORECASE,
)
_GENERIC_STUDY_RESOURCE_ACTION = re.compile(
    r"\b(?:delete|remove|rename|move|open|show|lösch\w*|loesch\w*|"
    r"umbenenn\w*|verschieb\w*|öffne?\w*|oeffne?\w*|zeig\w*)\b"
    r".{0,100}\b(?:study|studie)\b",
    re.IGNORECASE,
)
_EXPLICIT_SEPARATE_STUDY_REQUEST = re.compile(
    r"\b(?:new|fresh|another|additional|separate|follow[ -]?up|"
    r"neu(?:e|en|er|es)?|weitere\w*|zusätzliche\w*|zusaetzliche\w*|"
    r"separate\w*)\b.{0,80}\b(?:ai[ -]?interview|ki[ -]?interview|"
    r"interview[ -]?study|interviewstud\w*)\b|"
    r"\b(?:ai[ -]?interview|ki[ -]?interview|interview[ -]?study|"
    r"interviewstud\w*)\b.{0,80}\b(?:new|fresh|another|additional|"
    r"separate|follow[ -]?up|neu(?:e|en|er|es)?|weitere\w*)\b",
    re.IGNORECASE,
)


def _consume_current_study_creation(
    request: str,
    actions: list[dict[str, Any]],
    workspace_actions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map a global creation proposal onto the interview study on screen."""

    if _EXPLICIT_SEPARATE_STUDY_REQUEST.search(request):
        return actions, workspace_actions
    current_actions = list(actions)
    remaining: list[dict[str, Any]] = []
    for proposal in workspace_actions:
        if proposal.get("type") != "create_ai_interview":
            remaining.append(proposal)
            continue
        if not any(action.get("operation") == "rename" for action in current_actions):
            title = str(proposal.get("title") or "").strip()
            if title:
                current_actions.append({"operation": "rename", "title": title})
        if not any(action.get("operation") == "set_guide" for action in current_actions):
            sections = proposal.get("sections")
            if isinstance(sections, list) and sections:
                current_actions.append({"operation": "set_guide", "sections": sections})
        if not any(action.get("operation") == "set_persona" for action in current_actions):
            language = str(proposal.get("language") or "").strip()
            if language in {"de", "en"}:
                current_actions.append(
                    {"operation": "set_persona", "changes": {"language": language}}
                )
    return current_actions[:6], remaining


def _requested_guide_shape(request: str) -> tuple[int | None, int | None]:
    """Extract explicit novice constraints for guide size and probes."""

    match = _EXACT_GUIDE_COUNT.search(request) or _PLAIN_GUIDE_COUNT.search(request)
    raw_count = match.group(1).casefold() if match else ""
    section_count = int(raw_count) if raw_count.isdigit() else _COUNT_WORDS.get(raw_count)
    if section_count is not None and not 1 <= section_count <= MAX_SECTIONS:
        section_count = None
    probes_per_section = 1 if _ONE_PROBE_EACH.search(request) else None
    return section_count, probes_per_section


def _enforce_requested_guide_shape(
    request: str,
    actions: list[dict[str, Any]],
    *,
    language: str,
) -> list[dict[str, Any]]:
    """Honor explicit guide counts without fabricating study evidence.

    The model still writes the substantive questions. The server only clamps
    an overlong guide and supplies a neutral, non-leading probe when the user
    explicitly requested one for every already-generated section.
    """

    section_count, probes_per_section = _requested_guide_shape(request)
    if section_count is None and probes_per_section is None:
        return actions
    german_request = bool(
        re.search(r"\b(?:und|bitte|genau|frage|fragen|nachfrage|leitfaden)\b", request, re.I)
    )
    fallback_probe = (
        "Können Sie dafür ein konkretes Beispiel beschreiben?"
        if language == "de" or german_request
        else "Could you describe a concrete example of that?"
    )
    normalized_actions: list[dict[str, Any]] = []
    for action in actions:
        if action.get("operation") != "set_guide":
            normalized_actions.append(action)
            continue
        sections = [dict(section) for section in action.get("sections") or []]
        if section_count is not None and len(sections) >= section_count:
            sections = sections[:section_count]
        if probes_per_section == 1:
            for section in sections:
                probes = [
                    str(probe).strip()
                    for probe in section.get("probes") or []
                    if str(probe).strip()
                ]
                section["probes"] = probes[:1] or [fallback_probe]
        normalized_actions.append({**action, "sections": sections})
    return normalized_actions


STUDY_AGENT_SYSTEM = (
    "You are the study designer agent for one live AI-led interview study. "
    "You help the researcher shape the interview guide and configure the "
    "interviewer through conversation plus a strict action contract. "
    'Return STRICT JSON only: {"answer":"<concise user-facing response>",'
    '"actions":[...],"workspace_actions":[]}. Valid action objects are: '
    '{"operation":"set_guide","sections":[{"title":"<2-5 words>",'
    '"question":"<one open core question>","probes":["<concrete follow-up>"],'
    '"must_cover":true|false}]} which replaces the WHOLE guide (1 to 24 '
    "sections, up to 5 probes each; write all participant-facing wording in "
    "the study language); "
    '{"operation":"set_persona","changes":{"tone":"warm|neutral|formal",'
    '"voice":"' + "|".join(VOICES) + '","language":"de|en",'
    '"mode":"guided|iterative",'
    '"patience_ms":<600-3000>,"max_session_minutes":<30-60>,'
    '"retention":"keep|transcript_only","budget_minutes":<30-6000>}}; '
    "Fieldwork budget_minutes must be at least max_session_minutes; when changing "
    "both, always return a combination that can fund one full session. "
    "Mode guided works through the guide's topics; mode iterative asks ONLY "
    "the first section's question as an opener and derives every follow-up "
    "from the participant's answers (for iterative studies, design exactly "
    "one strong opening question). "
    '{"operation":"set_consent","changes":{"consent_text":"<participant-facing '
    'study description>","contact_line":"<contact for questions>"}}; '
    '{"operation":"rename","title":"<new study title>"}. '
    "This agent is embedded inside one already open interview study. An unqualified "
    "request to create or build an interview targets that open study with set_guide and "
    "related actions. create_ai_interview is available only when the current request "
    "explicitly asks for another, additional or separate study. Interviewing craft: questions are "
    "open and non-leading, one thought per "
    "question, ordered from easy to sensitive; probes ask for concrete "
    "episodes, never yes/no; never bake an expected answer or hypothesis "
    "into a question. When replacing the guide, keep what the researcher "
    "already refined unless they ask otherwise; when they name a NEW topic, "
    "redesign for that topic and ignore the old one. The researcher's "
    "explicit request always outranks the current study state. Only emit "
    "actions when the "
    "researcher asks for a change; plain questions get plain answers. Never "
    "invent sessions, participants or results. The current study, its guide and "
    "completed sessions are the active source material for analysis and design. A "
    "request that explicitly names a new study is handled outside this embedded "
    "editor. This agent works in the open interview study and always returns "
    "workspace_actions as an empty list."
)


@dataclass(frozen=True)
class StudyAgentTurn:
    """One model turn: the user-facing answer plus validated operations."""

    answer: str
    actions: list[dict[str, Any]]
    workspace_actions: list[dict[str, Any]] = field(default_factory=list)


def _valid_action(action: dict[str, Any]) -> dict[str, Any] | None:
    operation = str(action.get("operation") or "").strip()
    if operation == "set_guide":
        normalized = normalize_guide({"sections": action.get("sections") or []})
        if not normalized["sections"]:
            return None
        return {"operation": "set_guide", "sections": normalized["sections"]}
    if operation == "set_persona":
        changes = action.get("changes")
        if not isinstance(changes, dict):
            return None
        cleaned: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in PERSONA_FIELDS:
                continue
            allowed_enums = {
                "tone": TONES,
                "voice": VOICES,
                "language": ("de", "en"),
                "mode": ("guided", "iterative"),
                "retention": ("keep", "transcript_only"),
            }
            if key in allowed_enums and value in allowed_enums[key]:
                cleaned[key] = value
            elif key in ("patience_ms", "max_session_minutes", "budget_minutes"):
                try:
                    cleaned[key] = int(value)
                except (TypeError, ValueError):
                    continue
        return {"operation": "set_persona", "changes": cleaned} if cleaned else None
    if operation == "set_consent":
        changes = action.get("changes")
        if not isinstance(changes, dict):
            return None
        cleaned = {
            key: str(value).strip() for key, value in changes.items() if key in CONSENT_FIELDS
        }
        return {"operation": "set_consent", "changes": cleaned} if cleaned else None
    if operation == "rename":
        title = str(action.get("title") or "").strip()[:240]
        return {"operation": "rename", "title": title} if title else None
    return None


def run_study_agent(
    pool: LLMPool,
    *,
    request: str,
    study: dict[str, Any],
    history: list[dict[str, str]],
    language: str,
    assistant_preferences: dict[str, Any] | None = None,
) -> StudyAgentTurn:
    """Plan one design turn over the exact study state."""
    import json

    guide = dict(study.get("guide") or {})
    emit_agent_event(
        "context.loaded",
        tool="interview_study.inspect",
        label="Read the current interview study",
        detail=(
            f"Guide version {study.get('guide_version') or 1} with "
            f"{len(guide.get('topics') or [])} topics"
        ),
    )
    prior = render_model_aware_context(history, pool=pool, current_request=request)
    preference_context = assistant_preference_context(assistant_preferences)
    prompt = (
        (f"Earlier design conversation:\n{prior}\n\n" if prior else "")
        # The API builds this state from bounded fields and the normalized
        # guide. Cutting serialized JSON hid later guide sections entirely.
        + f"Current study state:\n{json.dumps(study, ensure_ascii=False)}\n\n"
        + (f"{preference_context}\n\n" if preference_context else "")
        + f"Researcher request: {request}"
    )
    system_prompt = (
        STUDY_AGENT_SYSTEM
        + response_language_instruction(language)
        + assistant_system_instruction(assistant_preferences)
    )
    read_only_request = bool(_READ_ONLY_REQUEST.search(request))
    if read_only_request:
        system_prompt += (
            " The current request is a read-only review. Answer the question from the "
            "supplied current state; return actions=[] and workspace_actions=[]. "
            "Do not continue an earlier edit, draft new changes, or claim anything was changed."
        )
    payload: dict[str, Any] | None = None
    recovery_pool = structured_recovery_pool(pool)
    is_edit_request = bool(_STUDY_EDIT_REQUEST.search(request)) and not bool(
        _NO_STUDY_EDIT.search(request)
    )
    action_pool = recovery_pool if is_edit_request else pool
    emit_agent_event(
        "plan.created",
        label="Plan the interview-study turn",
        detail="Shape the requested topics, questions and settings in the open interview guide.",
        steps=[
            "Inspect the current guide",
            "Draft scoped edits",
            "Validate participant flow",
        ],
    )
    emit_agent_event(
        "tool.started",
        tool="interview_study.propose_changes",
        label="Draft the requested guide changes",
        detail="Updating the open study's topics, questions and method settings.",
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
        # provider outage must not surface as an HTTP 500 in the study chat.
        pass
    if payload is None:
        emit_agent_event(
            "tool.progress",
            tool="interview_study.propose_changes",
            label="Check the guide proposal",
            detail="Checking the proposed topics, questions and settings against the study design.",
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
                tool="interview_study.propose_changes",
                label="Prepare the study explanation",
                detail="Finalizing the explanation from the current guide and study settings.",
            )
            payload = {
                "answer": partial_answer,
                "actions": [],
                "workspace_actions": [],
            }
    if payload is None:
        emit_agent_event(
            "tool.progress",
            tool="interview_study.propose_changes",
            label="Complete the study answer",
            detail="Preparing a concise answer grounded in the open interview study.",
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
            tool="interview_study.propose_changes",
            label="Guide proposal needs another pass",
            detail="The requested interview-study outcome is not ready yet.",
        )
        return StudyAgentTurn(
            answer=structured_response_failure(language),
            actions=[],
            workspace_actions=[],
        )
    answer = str(payload.get("answer") or "").strip()[:10_000]
    if read_only_request:
        # This boundary precedes deterministic edits and completion repair. A
        # previous edit request or an unsolicited model action cannot authorize
        # a mutation after the user explicitly switched to inspection.
        if payload.get("actions") or payload.get("workspace_actions"):
            answer = (
                "Ich habe nichts geändert. Die reine Prüfung konnte ich nicht sicher "
                "abschließen; bitte versuche es erneut."
                if language.casefold().startswith("de")
                else "I left the study unchanged. I could not safely complete the "
                "read-only review; please try again."
            )
        return StudyAgentTurn(answer=answer, actions=[], workspace_actions=[])
    actions = []
    for action in (payload.get("actions") or [])[:6]:
        if isinstance(action, dict):
            valid = _valid_action(action)
            if valid is not None:
                actions.append(valid)
    requested_sections, _ = _requested_guide_shape(request)
    proposed_guide = next(
        (action for action in actions if action.get("operation") == "set_guide"),
        None,
    )
    if (
        requested_sections is not None
        and proposed_guide is not None
        and len(proposed_guide.get("sections") or []) != requested_sections
    ):
        emit_agent_event(
            "tool.progress",
            tool="interview_study.complete_plan",
            label=f"Complete the requested {requested_sections}-topic guide",
            detail=(
                f"Preparing a complete {requested_sections}-topic guide with the "
                "requested questions and probes."
            ),
        )
        exact_prompt = (
            f"Prepare the requested guide with exactly {requested_sections} interview topics. "
            "Return "
            'strict JSON as {"answer":"...","actions":[{"operation":"set_guide",'
            '"sections":[...]}],"workspace_actions":[]}. The set_guide action must contain '
            f"exactly {requested_sections} substantive, distinct topics. Each topic needs a "
            "short title, one open non-leading core question and useful probes. Preserve all "
            "other explicit requirements from the latest request. Target the open study "
            "represented below.\n\n"
            f"Current study:\n{json.dumps(study, ensure_ascii=False)}\n\n"
            f"Latest researcher request:\n{request}"
        )
        exact_payload: dict[str, Any] | None = None
        try:
            exact_response = request_structured_completion(
                recovery_pool,
                system=system_prompt,
                prompt=exact_prompt,
                max_tokens=6_000,
            )
            exact_payload = extract_structured_object(
                exact_response.text,
                required_keys={"answer", "actions"},
            )
        except ProviderError:
            exact_payload = None
        exact_guide = next(
            (
                _valid_action(candidate)
                for candidate in (exact_payload or {}).get("actions", [])
                if isinstance(candidate, dict) and candidate.get("operation") == "set_guide"
            ),
            None,
        )
        if exact_guide is not None and len(exact_guide.get("sections") or []) == requested_sections:
            actions = [
                exact_guide,
                *[action for action in actions if action.get("operation") != "set_guide"],
            ][:6]
            answer = str((exact_payload or {}).get("answer") or answer).strip()[:10_000]
            emit_agent_event(
                "tool.completed",
                tool="interview_study.complete_plan",
                label=f"Prepared exactly {requested_sections} interview topics",
                detail="The open guide now matches the requested scope.",
                result_count=requested_sections,
            )
        else:
            emit_agent_event(
                "tool.failed",
                tool="interview_study.complete_plan",
                label="Requested topic count needs attention",
                detail=f"A complete {requested_sections}-topic guide is not ready yet.",
            )
    actions = _enforce_requested_guide_shape(request, actions, language=language)
    if is_edit_request and actions:
        review = review_action_coverage(
            pool,
            workspace="interview_study",
            request=request,
            state_summary=json.dumps(study, ensure_ascii=False),
            validated_actions=actions,
            action_contract=(
                "Interview-study actions from the documented contract: set_guide, "
                "set_persona, set_consent or rename. Keep participant wording open, "
                "non-leading and in the study language."
            ),
            language=language,
        )
        if not review.complete:
            validated_missing: list[dict[str, Any]] = []
            for candidate in review.missing_actions:
                valid = _valid_action(candidate)
                if valid is not None:
                    validated_missing.append(valid)
            actions, added = merge_missing_actions(
                actions,
                validated_missing,
                singleton_operations={
                    "set_guide",
                    "set_persona",
                    "set_consent",
                    "rename",
                },
                max_actions=6,
            )
            emit_agent_event(
                "tool.completed",
                tool="interview_study.complete_plan",
                label="Added the missing guide steps",
                detail=review.summary,
                result_count=added,
            )
    workspace_request = request
    if _GENERIC_STUDY_RESOURCE_ACTION.search(request) and not re.search(
        r"\b(?:interview[ -]?study|interviewstud\w*|ai[ -]?interview|ki[ -]?interview)\b",
        request,
        re.IGNORECASE,
    ):
        # Inside the study designer, novice phrases such as "delete this
        # study" unambiguously refer to the open interview study. Supplying
        # that scoped noun only to the action normalizer keeps the global
        # router conservative while still producing the protected,
        # confirmation-gated resource proposal the user expects here.
        workspace_request = f"{request} interview study"
    workspace_actions = normalize_workspace_actions_for_request(
        payload.get("workspace_actions"),
        workspace_request,
    )
    if not actions:
        actions, workspace_actions = _consume_current_study_creation(
            workspace_request,
            actions,
            workspace_actions,
        )
    # The embedded study agent is intentionally narrow. It may manage only the
    # interview study that is already open. Cross-feature creation and global
    # controls belong to Quick Answer, where the destination is explicit.
    workspace_actions = [
        action
        for action in workspace_actions
        if action.get("type") == "manage_resource"
        and action.get("resource_type") == "interview_study"
    ]
    emit_agent_event(
        "tool.completed",
        tool="interview_study.propose_changes",
        label="Interview guide proposal validated",
        detail=(
            f"{len(actions)} validated change"
            f"{'s' if len(actions) != 1 else ''} ready for the open study."
        ),
        result_count=len(actions),
    )
    emit_change_events(workspace="interview_study", changes=actions, applied=False)
    return StudyAgentTurn(
        answer=answer or "I looked at the study.",
        actions=actions,
        workspace_actions=workspace_actions,
    )
