"""Deterministic, read-only answers from the currently linked dataset profiles."""

from __future__ import annotations

import math
import re
from typing import Any

from sixsentences_server.core.locale import infer_response_language

_DATASET_REQUEST = re.compile(r"\b(?:datasets?|datens(?:atz|ätze|aetze)|daten)\b", re.IGNORECASE)
_ROW_REQUEST = re.compile(r"\b(?:rows?|zeilen|datensätze|datensaetze)\b", re.IGNORECASE)
_MISSING_REQUEST = re.compile(r"\b(?:missing\w*|fehlend\w*)\b", re.IGNORECASE)
_MEAN_REQUEST = re.compile(r"\b(?:mean|average|mittelwert\w*|durchschnitt\w*)\b", re.IGNORECASE)
_MEDIAN_REQUEST = re.compile(r"\bmedian\b", re.IGNORECASE)
_UNSUPPORTED_PROFILE_STATISTIC = re.compile(
    r"\b(?:min|max|minimum|maximum|sum|summe|variance|varianz|standardabweichung|"
    r"deviation|quartil\w*|quantil\w*|percentil\w*|perzentil\w*|mode|modus)\b",
    re.IGNORECASE,
)
_VALUE_ONLY = re.compile(
    r"\b(?:nur|only|just)\s+(?:(?:diesen|den|das|the|this|that|a|one|einen)\s+){0,2}"
    r"(?:wert|zahl|ergebnis|value|number|result)\b",
    re.IGNORECASE,
)
_ELLIPTICAL_STATISTIC = re.compile(
    r"^\s*(?:(?:und|and|auch|also|what\s+about)\s+)?"
    r"(?:(?:der|den|das|die|the)\s+)?"
    r"(?:median|mean|average|mittelwert|durchschnitt|minimum|maximum|variance|varianz)"
    r"\s*(?:\?|$)",
    re.IGNORECASE,
)
_OUTPUT_INSTRUCTION_WORD = re.compile(
    r"\b(?:bitte|please|nur|only|just|diesen|den|das|der|die|the|this|that|a|one|einen|"
    r"wert|zahl|ergebnis|value|number|result|auf|in|deutsch|english|nennen|geben|ausgeben|"
    r"antworte|answer|reply|return|nichts|nicht|do|not|don|t|no|edit|editing|bearbeiten|"
    r"ändern|aendern|manuscript|manuskript)\b",
    re.IGNORECASE,
)
_GLOBAL_PROFILE_SCOPE = re.compile(
    r"\b(?:unfiltered|ungefiltert\w*|entire\s+(?:table|dataset)|full\s+(?:table|dataset)|"
    r"gesamt\w*\s+(?:tabelle|datensatz|dataset)|all\s+(?:rows|values)|alle\s+(?:zeilen|werte))\b",
    re.IGNORECASE,
)
_STATISTIC_REQUEST = re.compile(
    r"\b(?:mean|average|mittelwert\w*|durchschnitt\w*|median|variance|varianz|"
    r"missing\w*|fehl\w*|minimum|maximum|sum\w*|total\w*|count\w*|anzahl|"
    r"value\w*|wert\w*|correlat\w*|korrelat\w*|verteil\w*|distribution\w*)\b",
    re.IGNORECASE,
)
_OTHER_ANALYSIS = re.compile(
    r"\b(?:group\w*|grupp\w*|filter\w*|median|variance|varianz|deviation|abweichung|"
    r"correlat\w*|korrelat\w*|regression|outlier\w*|ausreißer\w*|ausreisser\w*|"
    r"exclude\w*|excluding|except|without|ohne|ausschlie\w*|weighted|gewichtet\w*|"
    r"subset|teilmenge|where|wenn|vorher|nachher|compare|vergleich\w*|"
    r"positive\w*|negative\w*|positiv\w*|negativ\w*|chart\w*|plot\w*|diagram\w*|"
    r"visual\w*|survey\w*|interview\w*|umfrage\w*|create\w*|generat\w*|publish\w*|"
    r"delete\w*|erstell\w*|generier\w*|veröffentl\w*|lösch\w*|why|warum|wieso|"
    r"weshalb|interpret\w*|explain|erklär\w*|causal\w*|ursach\w*)\b|[<>]=?|[≤≥≠]|"
    r"\b(?:by|per|je|pro)\s+|\b(?:only|nur)\s+(?!(?:read|reading|lesen|prüfen)\b)",
    re.IGNORECASE,
)


def requests_dataset_evidence(
    message: str,
    datasets: list[dict[str, Any]] | None = None,
    history: list[dict[str, str]] | None = None,
) -> bool:
    """Include named-column and unambiguous conversational statistics follow-ups."""

    if _DATASET_REQUEST.search(message):
        return True
    if not _STATISTIC_REQUEST.search(message):
        return False
    return bool(
        _statistic_column_context(message, datasets, history)
        or (_ELLIPTICAL_STATISTIC.search(message) and _recent_dataset_context(datasets, history))
    )


def _named_columns(message: str, datasets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        column
        for dataset in datasets or []
        if isinstance(dataset.get("profile"), dict)
        for column in dataset["profile"].get("columns", [])
        if isinstance(column, dict)
        and isinstance(column.get("name"), str)
        and re.search(r"(?<!\w)" + re.escape(column["name"]) + r"(?!\w)", message, re.IGNORECASE)
    ]


def _statistic_column_context(
    message: str,
    datasets: list[dict[str, Any]] | None,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve only a column reference, never values from historical prose.

    A bare follow-up can cross other bare statistics follow-ups, but not a new
    user topic or a different explicitly named column. Assistant answers never
    bind the column; its values always come from the currently linked profile.
    """
    if _named_columns(message, datasets):
        return message
    if not _bare_statistic_followup(message) or _DATASET_REQUEST.search(message):
        return None
    return _recent_dataset_context(datasets, history)


def _recent_dataset_context(
    datasets: list[dict[str, Any]] | None,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Keep the latest user dataset topic, stopping at intervening subject changes."""
    for turn in reversed(history or []):
        if turn.get("role") != "user":
            continue
        previous = turn.get("content", "").strip()
        if not previous:
            continue
        if _DATASET_REQUEST.search(previous) or (
            _named_columns(previous, datasets) and _STATISTIC_REQUEST.search(previous)
        ):
            return previous
        if not _bare_statistic_followup(previous):
            break
    return None


def _bare_statistic_followup(message: str) -> bool:
    """Permit output controls after a bare metric, not hidden new data conditions."""
    match = _ELLIPTICAL_STATISTIC.search(message)
    if match is None:
        return False
    remainder = _OUTPUT_INSTRUCTION_WORD.sub("", message[match.end() :])
    return not remainder.strip(" \t\r\n.,;:!?\"'’-")


def _profile_response_language(
    message: str,
    language: str,
    history: list[dict[str, str]] | None,
) -> str:
    preferred = language.lower().replace("_", "-").split("-", 1)[0]
    for turn in history or []:
        if turn.get("role") == "user":
            preferred = infer_response_language(turn.get("content", ""), preferred)
    return infer_response_language(message, preferred)


def _exact_scalar_profile_answer(
    message: str,
    datasets: list[dict[str, Any]],
    *,
    language: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Read an unfiltered mean/median without asking an LLM to reuse chat numbers."""
    metrics = [
        key
        for key, pattern in (("mean", _MEAN_REQUEST), ("median", _MEDIAN_REQUEST))
        if pattern.search(message)
    ]
    requested = _GLOBAL_PROFILE_SCOPE.sub("", message)
    if (
        len(metrics) != 1
        or _ROW_REQUEST.search(requested)
        or _MISSING_REQUEST.search(requested)
        or _UNSUPPORTED_PROFILE_STATISTIC.search(requested)
    ):
        return None
    context = _statistic_column_context(message, datasets, history)
    if context is None:
        return None
    # Ignore only an explicit output-length request and the requested metric.
    # Filtering, grouping, further calculations and inherited constraints must
    # stay on the evidence-reader path, never receive a global table statistic.
    inherited = _recent_dataset_context(datasets, history)
    constraints = [message, context]
    if inherited and not _GLOBAL_PROFILE_SCOPE.search(message):
        constraints.append(inherited)
    for text in constraints:
        unfiltered = _MEDIAN_REQUEST.sub("", _VALUE_ONLY.sub("", text))
        if _OTHER_ANALYSIS.search(unfiltered):
            return None
    columns = _named_columns(context, datasets)
    if len(columns) != 1:
        return None
    metric = metrics[0]
    stats = columns[0].get("stats")
    value = stats.get(metric) if isinstance(stats, dict) else None
    german = _profile_response_language(message, language, history) == "de"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return (
            "Das aktuelle Datenprofil enthält dafür keinen verlässlichen Zahlenwert. "
            "Das Manuskript wurde nicht verändert."
            if german
            else "The current dataset profile does not contain a reliable numeric value for this. "
            "The manuscript was not changed."
        )
    number = format(value, ".15g")
    if german:
        number = number.replace(".", ",")
    if _VALUE_ONLY.search(message):
        return f"{number}."
    name = columns[0]["name"]
    if german:
        label = "Median" if metric == "median" else "Mittelwert"
        return (
            f"Der {label} der numerischen Werte in der Spalte „{name}“ beträgt {number}. "
            "Die Angabe stammt aus dem aktuellen vollständigen Datenprofil. "
            "Das Manuskript wurde nicht verändert."
        )
    return (
        f'The {metric} of the numeric values in the "{name}" column is {number}. '
        "This comes from the current full-table profile. The manuscript was not changed."
    )


def exact_dataset_profile_answer(
    message: str,
    datasets: list[dict[str, Any]] | None,
    *,
    language: str,
    history: list[dict[str, str]] | None = None,
) -> str | None:
    """Answer bounded profile statistics without model arithmetic.

    The caller owns read-only intent and tenant/link filtering. Unsupported
    calculations, multiple datasets or an ambiguous column stay on the normal
    evidence-reader path; they must never silently receive a global mean.
    """

    if not datasets or len(datasets) != 1:
        return None
    scalar = _exact_scalar_profile_answer(
        message,
        datasets,
        language=language,
        history=history,
    )
    if scalar is not None:
        return scalar
    if (
        not requests_dataset_evidence(message, datasets, history)
        or not all(
            pattern.search(message)
            for pattern in (
                _ROW_REQUEST,
                _MISSING_REQUEST,
                _MEAN_REQUEST,
            )
        )
        or _OTHER_ANALYSIS.search(message)
        or _UNSUPPORTED_PROFILE_STATISTIC.search(message)
    ):
        return None
    dataset = datasets[0]
    profile = dataset.get("profile")
    row_count = dataset.get("row_count")
    if not isinstance(profile, dict) or type(row_count) is not int or row_count < 0:
        return None
    columns = [
        column
        for column in profile.get("columns", [])
        if isinstance(column, dict)
        and isinstance(column.get("name"), str)
        and re.search(r"(?<!\w)" + re.escape(column["name"]) + r"(?!\w)", message, re.IGNORECASE)
    ]
    if len(columns) != 1:
        return None
    column = columns[0]
    missing = column.get("missing")
    stats = column.get("stats")
    if type(missing) is not int or not 0 <= missing <= row_count or not isinstance(stats, dict):
        return None
    mean = stats.get("mean")
    if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not math.isfinite(mean):
        return None
    # These are the stored full-table profile statistics, not calculations over
    # the bounded preview records. Do not infer individual row identities.
    mean_text = format(mean, ".15g")
    name = str(dataset.get("name") or "Linked dataset")
    column_name = column["name"]
    # The saved interface locale is only a fallback, not an override of the
    # user's output request. Preserve their preference on short follow-ups;
    # source text and prior assistant replies never choose the output language.
    response_language = _profile_response_language(message, language, history)
    if response_language == "de":
        return (
            f"Das aktuell verknüpfte Dataset „{name}“ enthält "
            f"{row_count} {'Zeile' if row_count == 1 else 'Zeilen'}. "
            f"In der Spalte „{column_name}“ "
            f"{'fehlt 1 Wert' if missing == 1 else f'fehlen {missing} Werte'}. "
            f"Der Mittelwert der numerischen Werte beträgt {mean_text.replace('.', ',')}. "
            "Die Angaben stammen direkt aus dem gespeicherten Datenprofil der vollständigen "
            "Tabelle, nicht aus früheren Chatantworten. Das Manuskript wurde nicht verändert."
        )
    return (
        f'The currently linked dataset "{name}" contains '
        f"{row_count} {'row' if row_count == 1 else 'rows'}. "
        f'The "{column_name}" column has '
        f"{missing} missing {'value' if missing == 1 else 'values'}. "
        f"The mean of its numeric values is {mean_text}. "
        "These values come directly from the saved full-table profile, not previous chat "
        "answers. The manuscript was not changed."
    )
