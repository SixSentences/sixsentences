"""The Data Hub chat agent: grounded answers plus a strict action contract.

The model sees the exact dataset profile (column stats, missingness, preview
records, version history) and may answer from it or propose deterministic
operations. Every computed number comes from run_analysis / the exact chart
renderer on the server, never from the model's arithmetic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sixsentences_server.agent.actions import (
    normalize_workspace_actions_for_request,
    scope_workspace_actions_to_current_resource,
    specialist_resource_actions_system,
    workspace_action_types_requested,
)
from sixsentences_server.agent.events import emit_agent_event, emit_change_events
from sixsentences_server.agent.runtime import review_action_coverage
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

ANALYSIS_KINDS = {
    "descriptive",
    "missingness",
    "group_summary",
    "correlation",
    "meta_analysis",
}
CHART_KINDS = {"bar", "line", "scatter"}
PROFILE_FIELDS = {"name", "description", "provenance", "license"}

_READ_ONLY_REQUEST = re.compile(
    r"\b(?:read[ -]?only|nur\s+(?:prüf\w*|pruef\w*|lesen)|"
    r"(?:only|just)\s+(?:review|inspect|check)|"
    r"(?:nichts|nix)\s+(?:mehr\s+)?(?:änder\w*|aender\w*|bearbeit\w*)|"
    r"(?:do\s+not|don['’]?t|dont)\s+(?:change|edit|modify|update)\s+"
    r"anything(?!\s+(?:else|other)\b)|"
    r"(?:change|edit|modify)\s+nothing|keine\s+(?:änderungen|aenderungen))\b",
    re.IGNORECASE,
)


def _requests_missingness_analysis(request: str) -> bool:
    """Recognize novice wording for an exact server-owned missingness check."""

    normalized = " ".join(request.casefold().split())
    signals = (
        "missingness",
        "missing values",
        "missing value",
        "fehlende werte",
        "fehlenden werte",
        "werte fehlen",
        "wert fehlt",
        "lücken",
        "luecken",
        "null values",
        "nullwerte",
    )
    return any(signal in normalized for signal in signals)


def _requests_open_exploration(request: str) -> bool:
    """Recognize a novice request for a first evidence-based data overview."""

    normalized = " ".join(request.casefold().split())
    patterns = (
        r"\bwas\s+f[aä]llt\w*\s+(?:dir\s+)?(?:an\s+)?(?:den\s+)?daten\s+auf\b",
        r"\bwas\s+ist\s+(?:hier\s+)?auff[aä]llig\b",
        r"\b(?:gib|geb)\w*\s+(?:mir\s+)?(?:einen\s+)?(?:ersten\s+)?überblick\b",
        r"\b(?:what\s+stands\s+out|what\s+do\s+you\s+notice)\b",
        r"\b(?:explore|inspect)\s+(?:this|the)\s+(?:data|dataset)\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _requests_median_group_correction(request: str) -> bool:
    normalized = " ".join(request.casefold().split())
    return bool(
        re.search(r"\bmedian\w*\b", normalized)
        and re.search(
            r"\b(?:statt|instead|rather|doch|korrig|änder|aender|switch)\w*\b",
            normalized,
        )
    )


def _requests_no_chart(request: str) -> bool:
    normalized = " ".join(request.casefold().split())
    return bool(
        re.search(
            r"\b(?:kein\w*|ohne|no|without)\b.{0,40}"
            r"\b(?:grafik|diagramm|chart|plot|visual)\w*\b",
            normalized,
        )
    )


def _requests_group_comparison(request: str) -> bool:
    """Recognize novice requests to compare groups in the current dataset."""

    normalized = " ".join(request.casefold().split())
    group_signal = re.search(
        r"\b(?:gruppen?|groups?|kohorten?|cohorts?|bedingungen?|conditions?)\w*\b",
        normalized,
    )
    comparison_signal = re.search(
        r"\b(?:vergleich\w*|compare\w*|unterschied\w*|differ\w*|gegenüber\w*|"
        r"gegenueber\w*)\b",
        normalized,
    )
    return bool(group_signal and comparison_signal)


def _preferred_column(
    columns: list[dict[str, Any]],
    *,
    column_type: str,
    preferred_names: tuple[str, ...],
) -> str:
    candidates = [
        str(column.get("name") or "")
        for column in columns
        if column.get("type") == column_type and str(column.get("name") or "")
    ]
    for preferred in preferred_names:
        match = next(
            (candidate for candidate in candidates if candidate.casefold() == preferred),
            None,
        )
        if match is not None:
            return match
    return candidates[0] if len(candidates) == 1 else ""


def _requests_profile_change(request: str) -> bool:
    """Return whether an empty-profile request can still be fulfilled safely."""

    normalized = " ".join(request.casefold().split())
    operations = (
        "rename",
        "change the name",
        "change its name",
        "change the description",
        "update the description",
        "profile name",
        "set provenance",
        "set the license",
        "update provenance",
        "update the license",
        "benenn",
        "umbenenn",
        "namen ändern",
        "name ändern",
        "profilname",
        "titel ändern",
        "beschreibung",
        "beschreibung ändern",
        "provenienz",
        "herkunft",
        "lizenz",
    )
    return any(operation in normalized for operation in operations)


def _negated_profile_fields(request: str) -> set[str]:
    """Exclude explicitly rejected metadata changes without blocking calculations."""
    operations = {
        "name": r"rename|umbenenn\w*|benenn\w*|change\s+(?:its|the)\s+name",
        "description": r"(?:change|update|änder\w*|aender\w*|setz\w*)"
        r"[^.!?;]{0,35}(?:description|beschreibung)",
        "provenance": r"(?:change|update|änder\w*|aender\w*|setz\w*)"
        r"[^.!?;]{0,35}(?:provenance|origin|provenienz|herkunft)",
        "license": r"(?:change|update|änder\w*|aender\w*|setz\w*)"
        r"[^.!?;]{0,35}(?:license|lizenz)",
    }
    negator = r"(?:do\s+not|don['’]?t|dont|never|nicht|nichts|nix|keine\w*|ohne)"
    excluded = {
        field
        for field, operation in operations.items()
        if re.search(rf"\b{negator}\b[^.!?;]{{0,30}}\b(?:{operation})\b", request, re.I)
    }
    if re.search(
        r"\b(?:benenn\w*|umbenenn\w*)\b[^.!?;]{0,50}\b(?:nicht|keinesfalls)\s+um\b",
        request,
        re.IGNORECASE,
    ):
        excluded.add("name")
    return excluded


def _without_negated_profile_changes(
    actions: list[dict[str, Any]],
    excluded_fields: set[str],
) -> list[dict[str, Any]]:
    """Keep only permitted metadata fields, including after coverage repair."""
    allowed: list[dict[str, Any]] = []
    for action in actions:
        if action.get("operation") != "set_profile":
            allowed.append(action)
            continue
        changes = {
            field: value
            for field, value in dict(action.get("changes") or {}).items()
            if field not in excluded_fields
        }
        if changes:
            allowed.append({**action, "changes": changes})
    return allowed


def _deterministic_profile_changes(request: str) -> dict[str, str]:
    """Extract explicit profile metadata edits from common novice wording.

    This is intentionally limited to unambiguous imperative forms. It prevents
    a model from claiming that metadata changed while returning no executable
    action, without trying to infer values from a general discussion.
    """

    normalized = " ".join(request.strip().split())
    changes: dict[str, str] = {}
    excluded_fields = _negated_profile_fields(request)
    patterns = {
        "name": (
            r"(?:benenn(?:e)?|umbenenn(?:e)?|rename|change\s+(?:its|the)\s+name)"
            r".{0,80}?(?:\s(?:in|zu|to)\s+)[„\"']?(.+?)[“\"']?"
            r"(?=\s+um\b|\s+and\b|\s+und\b|[.;,]|$)",
        ),
        "description": (
            r"(?:setz(?:e)?|änder(?:e)?|aender(?:e)?|update|change)"
            r".{0,60}?(?:beschreibung|description)\s+(?:auf|zu|to)\s+"
            r"[„\"']?(.+?)[“\"']?"
            r"(?=\s+(?:und|and|ohne|without|keine|no)\b|[.;]|$)",
        ),
        "provenance": (
            r"(?:setz(?:e)?|änder(?:e)?|aender(?:e)?|update|change)"
            r".{0,60}?(?:provenienz|herkunft|provenance|origin)\s+(?:auf|zu|to)\s+"
            r"[„\"']?(.+?)[“\"']?(?=\s+(?:und|and|ohne|without)\b|[.;]|$)",
        ),
        "license": (
            r"(?:setz(?:e)?|änder(?:e)?|aender(?:e)?|update|change)"
            r".{0,60}?(?:lizenz|license)\s+(?:auf|zu|to)\s+"
            r"[„\"']?(.+?)[“\"']?(?=\s+(?:und|and|ohne|without)\b|[.;]|$)",
        ),
    }
    for profile_field, field_patterns in patterns.items():
        if profile_field in excluded_fields:
            continue
        for pattern in field_patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match is None:
                continue
            value = match.group(1).strip(" \t\n\r„“\"'.,")
            if value:
                changes[profile_field] = value[:2_000]
                break
    return changes


def _empty_dataset_answer(language: str) -> str:
    """Explain the missing prerequisite without inviting fabricated values."""

    if language.casefold().startswith("de"):
        return (
            "In diesem Datenprofil sind noch keine Zeilen oder Spalten vorhanden. "
            "Deshalb kann ich noch keine Ergebnisse berechnen oder eine belastbare "
            "Grafik erstellen. Laden Sie zuerst eine CSV-, TSV-, JSON- oder "
            "Excel-Datei über ‚Version hinzufügen‘ hoch. Danach kann ich die "
            "Datenqualität prüfen, Kennzahlen berechnen und eine passende Grafik "
            "aus den tatsächlichen Werten erzeugen. Beispielwerte würde ich nicht "
            "als Forschungsergebnis ausgeben."
        )
    return (
        "This dataset profile does not contain any rows or columns yet, so I cannot "
        "calculate results or create an evidence-based chart. Upload a CSV, TSV, "
        "JSON or Excel file through Add version first. I can then check data quality, "
        "calculate the requested statistics and chart the actual values. I would not "
        "present example values as research results."
    )


DATASET_AGENT_SYSTEM = (
    "You are the Data Hub agent for one research dataset. You answer questions "
    "about the data and perform operations through a strict action contract. "
    "Return STRICT JSON only with this shape: "
    '{"answer":"<concise user-facing response>","actions":[...],'
    '"workspace_actions":[]}. '
    "Valid action objects are: "
    '{"operation":"run_analysis","kind":"descriptive|missingness|group_summary|'
    'correlation|meta_analysis","definition":{...},"name":"<short label>"}; '
    '{"operation":"create_chart","x_column":"...","y_column":"...",'
    '"kind":"bar|line|scatter","title":"..."}; '
    '{"operation":"create_meta_chart","kind":"forest|funnel",'
    '"label_column":"...","effect_column":"...","se_column":"...","title":"..."}; '
    '{"operation":"render_visual","prompt":"<illustration brief>"}; '
    '{"operation":"set_profile","changes":{"name|description|provenance|license":"..."}}. '
    "Only use column names that exist in the supplied schema. descriptive needs "
    "definition.column; group_summary needs definition.group_by and "
    "definition.value_column (optional metric mean|median|sum|count); correlation "
    "needs definition.x_column and definition.y_column; meta_analysis needs "
    "definition.label_column, definition.effect_column and definition.se_column; "
    "missingness needs no definition. create_chart needs a numeric y_column and "
    "renders exact values, never estimates. For pooled effects across studies, "
    "run meta_analysis first and then draw the forest or funnel plot with "
    "create_meta_chart. Use render_visual only when the user "
    "asks for an illustrated, conceptual figure; it is confirmed by the user "
    "before anything renders. set_profile only when the user asks to rename or "
    "annotate the dataset. "
    "When the user asks you to check, calculate, analyze or compare missingness, "
    "descriptive statistics, groups, correlations or pooled effects, emit the "
    "matching run_analysis action. Do not mentally calculate a result from the "
    "preview and present it as if the full dataset was analyzed. Existing or "
    "imported rows are the active source material for this task. A cross-feature "
    "creation proposal is available only when the current request explicitly names "
    "the new artifact. "
    "Answer ONLY from the supplied profile, summary statistics, preview records "
    "and analysis results. Never invent a row, value, count, percentage or "
    "correlation; the profiled record window is a sample of the full dataset, "
    "say so when it matters. If the data cannot answer something, say so."
    + specialist_resource_actions_system("dataset")
)


@dataclass(frozen=True)
class DatasetAgentTurn:
    """One model turn: the user-facing answer plus proposed operations."""

    answer: str
    actions: list[dict[str, Any]]
    workspace_actions: list[dict[str, Any]] = field(default_factory=list)


def _valid_action(action: dict[str, Any], columns: set[str]) -> bool:
    """Keep only documented operations with plausible column references."""
    operation = str(action.get("operation") or "").strip()
    if operation == "run_analysis":
        if str(action.get("kind") or "") not in ANALYSIS_KINDS:
            return False
        definition = action.get("definition")
        if not isinstance(definition, dict):
            return False
        referenced = {
            str(definition.get(key) or "")
            for key in (
                "column",
                "group_by",
                "value_column",
                "x_column",
                "y_column",
                "label_column",
                "effect_column",
                "se_column",
            )
        } - {""}
        return referenced <= columns
    if operation == "create_chart":
        return (
            str(action.get("kind") or "") in CHART_KINDS
            and str(action.get("x_column") or "") in columns
            and str(action.get("y_column") or "") in columns
        )
    if operation == "create_meta_chart":
        return (
            str(action.get("kind") or "") in {"forest", "funnel"}
            and str(action.get("label_column") or "") in columns
            and str(action.get("effect_column") or "") in columns
            and str(action.get("se_column") or "") in columns
        )
    if operation == "render_visual":
        return bool(str(action.get("prompt") or "").strip())
    if operation == "set_profile":
        changes = action.get("changes")
        return isinstance(changes, dict) and bool(set(changes) & PROFILE_FIELDS)
    return False


def run_dataset_agent(
    pool: LLMPool,
    *,
    request: str,
    name: str,
    description: str,
    provenance: str,
    license: str,
    format: str,
    row_count: int,
    profile: dict[str, Any],
    versions: list[dict[str, Any]],
    history: list[dict[str, str]],
    language: str,
    assistant_preferences: dict[str, Any] | None = None,
) -> DatasetAgentTurn:
    """Plan one grounded analysis/design turn using the action contract."""
    columns = profile.get("columns") or []
    emit_agent_event(
        "context.loaded",
        tool="dataset.inspect",
        label="Inspect the current dataset",
        detail=(
            f"{row_count} rows, {len(columns)} profiled columns and {len(versions)} stored versions"
        ),
    )
    requested_workspace_actions = set(workspace_action_types_requested(request))
    can_leave_data_workspace = bool(requested_workspace_actions - {"create_visual"})
    if (
        not columns
        and row_count <= 0
        and not versions
        and not _requests_profile_change(request)
        and not can_leave_data_workspace
    ):
        return DatasetAgentTurn(
            answer=_empty_dataset_answer(language),
            actions=[],
            workspace_actions=[],
        )
    material = {
        "dataset": {
            "name": name,
            "description": description,
            "provenance": provenance,
            "license": license,
            "format": format or "empty",
            "rows": row_count,
        },
        "schema": columns[:40],
        "preview_records": (profile.get("preview") or [])[:12],
        "profiled_rows": profile.get("profiled_rows", 0),
        "versions": versions[:10],
    }
    prior = render_model_aware_context(history, pool=pool, current_request=request)
    preference_context = assistant_preference_context(assistant_preferences)
    prompt = (
        (f"Earlier dataset conversation:\n{prior}\n\n" if prior else "")
        + f"Dataset material:\n{json.dumps(material, ensure_ascii=False)[:60_000]}\n\n"
        + (f"{preference_context}\n\n" if preference_context else "")
        + f"User request: {request}"
    )
    system_prompt = (
        DATASET_AGENT_SYSTEM
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
    emit_agent_event(
        "plan.created",
        label="Plan the data turn",
        detail=(
            "Match the request to available fields, use deterministic calculations, "
            "then verify every result against the dataset profile."
        ),
        steps=[
            "Inspect schema and provenance",
            "Choose exact operations",
            "Check computed outputs",
        ],
    )
    emit_agent_event(
        "tool.started",
        tool="dataset.plan_operations",
        label="Plan grounded data operations",
        detail="Matching the requested analyses and charts to the stored dataset fields.",
    )
    payload: dict[str, Any] | None = None
    raw_response = ""
    try:
        response = request_structured_completion(
            pool,
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
        # provider outage must not surface as an HTTP 500 in the Data Hub chat.
        pass
    recovery_pool = structured_recovery_pool(pool)
    if payload is None:
        emit_agent_event(
            "tool.progress",
            tool="dataset.plan_operations",
            label="Check the data plan",
            detail="Checking that each requested operation has the required dataset fields.",
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
                tool="dataset.plan_operations",
                label="Prepare the data explanation",
                detail="Finalizing the explanation from the available dataset profile.",
            )
            payload = {
                "answer": partial_answer,
                "actions": [],
                "workspace_actions": [],
            }
    if payload is None:
        emit_agent_event(
            "tool.progress",
            tool="dataset.plan_operations",
            label="Complete the data answer",
            detail="Preparing a concise answer grounded in the stored dataset.",
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
            tool="dataset.plan_operations",
            label="Data plan needs another pass",
            detail="The requested analysis or chart is not ready yet.",
        )
        return DatasetAgentTurn(
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
                else "I left the dataset unchanged. I could not safely complete the "
                "read-only review; please try again."
            )
        return DatasetAgentTurn(answer=answer, actions=[], workspace_actions=[])
    known = {str(column.get("name") or "") for column in columns}
    actions = [
        action
        for action in (payload.get("actions") or [])[:12]
        if isinstance(action, dict) and _valid_action(action, known)
    ]
    # the model sometimes emits the same action two or three times in one
    # turn; running duplicates would stack identical tables in the output
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        key = json.dumps(action, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    if _requests_no_chart(request):
        deduped = [
            action
            for action in deduped
            if action.get("operation") not in {"create_chart", "create_meta_chart", "render_visual"}
        ]
    if _requests_median_group_correction(request):
        group_by = _preferred_column(
            columns,
            column_type="text",
            preferred_names=("group", "gruppe", "condition", "cohort"),
        )
        value_column = _preferred_column(
            columns,
            column_type="number",
            preferred_names=("score", "value", "outcome", "effect", "rating"),
        )
        if group_by and value_column:
            deduped = [
                action
                for action in deduped
                if not (
                    action.get("operation") == "run_analysis"
                    and action.get("kind") == "group_summary"
                )
            ]
            deduped.insert(
                0,
                {
                    "operation": "run_analysis",
                    "kind": "group_summary",
                    "definition": {
                        "group_by": group_by,
                        "value_column": value_column,
                        "metric": "median",
                    },
                    "name": f"Median {value_column} by {group_by}",
                },
            )
    # A novice should not have to phrase a deterministic data-quality request in
    # exactly the way the model expects. Missingness is parameter-free, so the
    # server can safely guarantee the exact analysis instead of accepting a
    # plausible answer calculated from the small preview window.
    if _requests_missingness_analysis(request) and not any(
        action.get("operation") == "run_analysis" and action.get("kind") == "missingness"
        for action in deduped
    ):
        deduped.insert(
            0,
            {
                "operation": "run_analysis",
                "kind": "missingness",
                "definition": {},
                "name": "Missingness",
            },
        )
    if _requests_open_exploration(request) and not any(
        action.get("operation") == "run_analysis" for action in deduped
    ):
        numeric_column = next(
            (
                str(column.get("name") or "")
                for column in columns
                if column.get("type") == "number" and str(column.get("name") or "")
            ),
            "",
        )
        if numeric_column:
            deduped.insert(
                0,
                {
                    "operation": "run_analysis",
                    "kind": "descriptive",
                    "definition": {"column": numeric_column},
                    "name": f"{numeric_column} overview",
                },
            )
    if _requests_group_comparison(request) and not any(
        action.get("operation") == "run_analysis" and action.get("kind") == "group_summary"
        for action in deduped
    ):
        group_by = _preferred_column(
            columns,
            column_type="text",
            preferred_names=("group", "gruppe", "condition", "cohort"),
        )
        value_column = _preferred_column(
            columns,
            column_type="number",
            preferred_names=("score", "value", "outcome", "effect", "rating"),
        )
        if group_by and value_column:
            deduped.insert(
                0,
                {
                    "operation": "run_analysis",
                    "kind": "group_summary",
                    "definition": {
                        "group_by": group_by,
                        "value_column": value_column,
                        "metric": "mean",
                    },
                    "name": f"Mean {value_column} by {group_by}",
                },
            )
        else:
            deduped.insert(
                0,
                {
                    "operation": "run_analysis",
                    "kind": "missingness",
                    "definition": {},
                    "name": "Data quality overview",
                },
            )
    explicit_profile_changes = _deterministic_profile_changes(request)
    if explicit_profile_changes and not any(
        action.get("operation") == "set_profile" for action in deduped
    ):
        deduped.append(
            {
                "operation": "set_profile",
                "changes": explicit_profile_changes,
            }
        )
    excluded_profile_fields = _negated_profile_fields(request)
    deduped = _without_negated_profile_changes(deduped, excluded_profile_fields)
    if deduped:
        review = review_action_coverage(
            pool,
            workspace="dataset",
            request=request,
            state_summary=json.dumps(material["dataset"], ensure_ascii=False),
            validated_actions=deduped,
            action_contract=(
                "Dataset actions from the documented contract: run_analysis, "
                "create_chart, create_meta_chart, render_visual or set_profile. "
                "Use only supplied schema columns and never invent values."
            ),
            language=language,
        )
        if not review.complete:
            existing = {
                json.dumps(action, sort_keys=True, ensure_ascii=False) for action in deduped
            }
            added = 0
            for candidate in review.missing_actions:
                if not _valid_action(candidate, known):
                    continue
                key = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
                if key in existing:
                    continue
                deduped.append(candidate)
                existing.add(key)
                added += 1
            emit_agent_event(
                "tool.completed",
                tool="dataset.complete_plan",
                label="Added the missing data steps",
                detail=review.summary,
                result_count=added,
            )
    deduped = _without_negated_profile_changes(deduped, excluded_profile_fields)
    workspace_actions = normalize_workspace_actions_for_request(
        payload.get("workspace_actions"),
        request,
    )
    if any(action.get("operation") in {"render_visual", "create_chart"} for action in deduped):
        workspace_actions = [
            action for action in workspace_actions if action.get("type") != "create_visual"
        ]
    workspace_actions = scope_workspace_actions_to_current_resource(
        workspace_actions,
        resource_type="dataset",
    )
    if "name" in excluded_profile_fields:
        workspace_actions = [
            action
            for action in workspace_actions
            if not (action.get("type") == "manage_resource" and action.get("operation") == "rename")
        ]
    emit_agent_event(
        "tool.completed",
        tool="dataset.plan_operations",
        label="Grounded data plan validated",
        detail=(
            f"{len(deduped)} server-computed operation"
            f"{'s' if len(deduped) != 1 else ''} ready to run."
        ),
        result_count=len(deduped),
    )
    emit_change_events(workspace="dataset", changes=deduped, applied=False)
    return DatasetAgentTurn(
        answer=answer or "I checked the dataset profile.",
        actions=deduped,
        workspace_actions=workspace_actions,
    )
