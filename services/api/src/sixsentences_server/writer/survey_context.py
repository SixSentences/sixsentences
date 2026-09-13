"""Bounded, de-identified survey context for the Writer agent."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

_WORD_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)
_STOPWORDS = {
    "about",
    "and",
    "aus",
    "bei",
    "das",
    "dem",
    "den",
    "der",
    "die",
    "eine",
    "für",
    "from",
    "ich",
    "ist",
    "mit",
    "oder",
    "survey",
    "the",
    "und",
    "von",
    "was",
    "wie",
    "with",
}


def _terms(value: str) -> set[str]:
    return {
        word.casefold() for word in _WORD_RE.findall(value) if word.casefold() not in _STOPWORDS
    }


def _answer_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, (dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _instrument_text(survey: dict[str, Any], *, max_chars: int = 12_000) -> str:
    lines = [
        f"Title: {survey.get('title') or 'Untitled survey'}",
        f"Status at retrieval: {survey.get('status') or 'unknown'}",
    ]
    description = str(survey.get("description") or "").strip()
    if description:
        lines.append(f"Study or participant description: {description}")
    settings = dict(survey.get("settings") or {})
    lines.append(
        "Identity collection: "
        + ("enabled in the form" if settings.get("collect_identity") else "disabled")
    )
    questions = [
        dict(question) for question in survey.get("questions") or [] if isinstance(question, dict)
    ]
    lines.append(f"Question count: {len(questions)}")
    lines.append("QUESTIONNAIRE IN PRESENTED ORDER:")
    for index, question in enumerate(questions, start=1):
        question_type = str(question.get("type") or "short_text")
        constraints: list[str] = [
            question_type.replace("_", " "),
            "required" if question.get("required") else "optional",
        ]
        options = [str(option) for option in question.get("options") or []]
        if options:
            constraints.append("options: " + " | ".join(options))
        if question_type == "rating":
            constraints.append("range: 1 to 5")
        elif question_type == "scale":
            constraints.append(f"range: {question.get('min', 1)} to {question.get('max', 10)}")
        lines.append(
            f"Q{index} [{question.get('id')} | {'; '.join(constraints)}]: {question.get('title')}"
        )
        detail = str(question.get("description") or "").strip()
        if detail:
            lines.append(f"  Prompt detail: {detail}")
    return "\n".join(lines)[:max_chars]


def _summary_text(summary: dict[str, Any], *, max_chars: int = 16_000) -> str:
    total = int(summary.get("responses") or 0)
    lines = [
        f"Submitted responses: {total}",
        f"Complete required-question records: {int(summary.get('complete') or 0)} "
        f"({float(summary.get('completion_percent') or 0):g}%)",
    ]
    for index, raw_item in enumerate(summary.get("questions") or [], start=1):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        answered = int(item.get("answered") or 0)
        missing = int(item.get("missing") or 0)
        kind = str(item.get("type") or "short_text")
        lines.append(
            f"Q{index} {item.get('title')} [{kind}]: answered={answered}, missing={missing}"
        )
        if kind in {"single_choice", "multiple_choice"}:
            counts = [
                f"{entry.get('option')}={int(entry.get('count') or 0)} "
                f"({float(entry.get('percent') or 0):g}%)"
                for entry in item.get("counts") or []
                if isinstance(entry, dict)
            ]
            if counts:
                lines.append("  Distribution: " + "; ".join(counts))
        elif kind in {"rating", "scale"}:
            lines.append(
                "  Descriptives: "
                f"mean={item.get('mean')}, min={item.get('min')}, max={item.get('max')}"
            )
            distribution = [
                f"{entry.get('value')}={int(entry.get('count') or 0)}"
                for entry in item.get("distribution") or []
                if isinstance(entry, dict)
            ]
            if distribution:
                lines.append("  Distribution: " + "; ".join(distribution))
        else:
            lines.append(
                "  Open-text answers are not summarized deterministically. "
                "Use retrieved response rows for exact wording."
            )
    return "\n".join(lines)[:max_chars]


def _response_candidates(
    survey: dict[str, Any],
    *,
    query_terms: set[str],
) -> list[tuple[int, int, dict[str, Any]]]:
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for position, raw_response in enumerate(survey.get("responses") or []):
        if not isinstance(raw_response, dict):
            continue
        response = dict(raw_response)
        answers = dict(response.get("answers") or {})
        searchable = " ".join(_answer_text(value) for value in answers.values())
        score = len(_terms(searchable) & query_terms) * 10
        ranked.append((score, position, response))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def prepare_survey_evidence(
    surveys: Iterable[dict[str, Any]],
    *,
    query: str,
    max_response_rows: int = 30,
    max_response_chars: int = 18_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prepare instruments, exact summaries and optional response rows."""

    prepared = [dict(item) for item in surveys]
    query_terms = _terms(query)
    candidates: list[tuple[int, str, int, dict[str, Any]]] = []
    per_survey: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for survey in prepared:
        if survey.get("mode") != "responses":
            continue
        public_id = str(survey.get("survey_id") or "")
        ranked = _response_candidates(survey, query_terms=query_terms)
        per_survey[public_id] = ranked
        for score, position, response in ranked:
            candidates.append((score, public_id, position, response))

    chosen: list[tuple[int, str, int, dict[str, Any]]] = []
    chosen_keys: set[tuple[str, int]] = set()
    for public_id, rows in per_survey.items():
        for score, position, response in rows[:1]:
            if len(chosen) >= max_response_rows:
                break
            chosen.append((score, public_id, position, response))
            chosen_keys.add((public_id, position))
    for candidate in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        key = (candidate[1], candidate[2])
        if key in chosen_keys or len(chosen) >= max_response_rows:
            continue
        chosen.append(candidate)
        chosen_keys.add(key)

    survey_by_id = {str(item.get("survey_id") or ""): item for item in prepared}
    response_chars = 0
    excerpts: dict[str, list[dict[str, Any]]] = {}
    for _, public_id, _, response in sorted(chosen, key=lambda item: (item[1], item[2])):
        survey = survey_by_id[public_id]
        questions = {
            str(question.get("id")): dict(question)
            for question in survey.get("questions") or []
            if isinstance(question, dict)
        }
        answers = []
        for question_id, value in dict(response.get("answers") or {}).items():
            question = questions.get(str(question_id))
            if question is None:
                continue
            answer = {
                "question_id": str(question_id),
                "question": str(question.get("title") or question_id),
                "value": _answer_text(value),
            }
            answers.append(answer)
        serialized_size = sum(len(item["question"]) + len(item["value"]) for item in answers)
        if response_chars + serialized_size > max_response_chars:
            continue
        response_chars += serialized_size
        excerpts.setdefault(public_id, []).append(
            {
                "response_id": str(response.get("response_id") or ""),
                "submitted_at": str(response.get("submitted_at") or ""),
                "answers": answers,
            }
        )

    contexts: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for survey in prepared:
        public_id = str(survey.get("survey_id") or "")
        responses = excerpts.get(public_id, [])
        summary = dict(survey.get("summary") or {})
        contexts.append(
            {
                "survey_id": public_id,
                "title": str(survey.get("title") or "Untitled survey"),
                "mode": str(survey.get("mode") or "summary"),
                "instrument_text": _instrument_text(survey),
                "summary_text": _summary_text(summary),
                "response_count": int(summary.get("responses") or 0),
                "response_rows": responses,
            }
        )
        provenance.append(
            {
                "survey_id": public_id,
                "title": str(survey.get("title") or "Untitled survey"),
                "mode": str(survey.get("mode") or "summary"),
                "question_count": len(survey.get("questions") or []),
                "response_count": int(summary.get("responses") or 0),
                "retrieved_response_ids": [response["response_id"] for response in responses],
            }
        )
    return contexts, provenance


def render_survey_evidence(
    contexts: list[dict[str, Any]],
    *,
    source_offset: int = 0,
) -> str:
    """Render survey evidence while preserving its linked-catalog ordinal."""

    sections: list[str] = []
    for index, context in enumerate(contexts, start=source_offset + 1):
        source_id = f"Q{index}"
        section = (
            f"SOURCE {source_id}: {context.get('title')} "
            f"(survey_id={context.get('survey_id')})\n"
            "INSTRUMENT AND ADMINISTRATION SETTINGS:\n"
            f"{context.get('instrument_text')}\n"
            "DETERMINISTIC DESCRIPTIVE RESULTS:\n"
            f"{context.get('summary_text')}"
        )
        rows = context.get("response_rows")
        if isinstance(rows, list) and rows:
            rendered_rows: list[str] = []
            for row_index, row in enumerate(rows, start=1):
                answers = "\n".join(
                    f"- {answer.get('question')}: {answer.get('value')}"
                    for answer in row.get("answers") or []
                )
                rendered_rows.append(
                    f"[{source_id}:R{row_index} | response_id={row.get('response_id')}]\n{answers}"
                )
            section += "\nRETRIEVED DE-IDENTIFIED RESPONSE ROWS:\n" + "\n\n".join(rendered_rows)
        sections.append(section)
    return "\n\n".join(sections)
