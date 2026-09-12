"""Resolve which linked primary-research sources belong in a Writer turn."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

_SURVEY = re.compile(r"\b(?:survey|questionnaire|umfrage|fragebogen)\w*\b", re.I)
_DATASET = re.compile(r"\b(?:dataset\w*|datensatz\w*|datensätz\w*|datensaetz\w*)\b", re.I)
_GENERIC_DATA = re.compile(r"\b(?:data|daten)\b", re.I)
_PROJECT_SOURCE_ONLY = re.compile(
    r"\b(?:only|solely|just|nur|ausschlie(?:ß|ss)lich)\s+"
    r"(?:(?:the|this|current|saved|selected|marked|from|in|use|read|"
    r"den|das|die|dem|aus|im|lies|verwende|nutze|dies\w*|aktuell\w*|"
    r"gespeichert\w*|markiert\w*)\s+){0,6}"
    r"(?:manuscript\w*|manuskript\w*|source\s+text|quelltext|passage|abschnitt)\b",
    re.I,
)
_INTERVIEW = re.compile(
    r"\b(?:interview|transcript|transkript|verbatim|quote|quotation|zitat|"
    r"direktzitat|speaker|sprecher|timestamp|zeitangabe)\w*\b",
    re.I,
)
_NEGATOR = re.compile(
    r"\b(?:no|not|without|don't|do\s+not|instead\s+of|"
    r"kein\w*|nicht|ohne|statt)\b",
    re.I,
)
_SOURCE_BINDING = re.compile(
    r"\b(?:based\s+on|using|use|from|linked|attached|"
    r"auf\s+basis(?:\s+von)?|nutz\w*|nimm\w*|nehm\w*|verwend\w*|aus|verknüpf\w*|"
    r"verknuepf\w*)\b",
    re.I,
)
_CREATION_VERB = re.compile(
    r"\b(?:create|start|set\s+up|make|build|generate|"
    r"erstell\w*|anleg\w*|start\w*|mach\w*|bau\w*|generier\w*)\b",
    re.I,
)
_NEW_ARTIFACT = re.compile(r"\b(?:new|another|neu\w*|weiter\w*)\b", re.I)


@dataclass(frozen=True)
class WriterEvidenceScope:
    include_interviews: bool = True
    include_surveys: bool = True
    requested_interviews: bool = False
    requested_surveys: bool = False
    include_datasets: bool = True
    requested_datasets: bool = False
    retrieval_query: str = ""

    def prompt_note(self, *, interviews: int, surveys: int, datasets: int = 0) -> str:
        requested: list[str] = []
        if self.requested_interviews:
            requested.append("interview evidence")
        if self.requested_surveys:
            requested.append("survey evidence")
        if self.requested_datasets:
            requested.append("dataset evidence")
        lines = [
            "EVIDENCE SCOPE FOR THIS TURN: "
            + (
                ", ".join(requested)
                if requested
                else "all linked evidence is available"
                if self.include_interviews and self.include_surveys and self.include_datasets
                else "the source restrictions below apply"
            )
            + "."
        ]
        if not self.include_interviews:
            lines.append("Do not use interview evidence, including excerpts from older turns.")
        if not self.include_surveys:
            lines.append("Do not use survey evidence, including excerpts from older turns.")
        if not self.include_datasets:
            lines.append("Do not use dataset evidence, including values from older turns.")
        if self.requested_interviews and interviews == 0:
            lines.append(
                "The user requested interview evidence, but no interview is linked. "
                "Do not substitute survey data or invent interview findings. State "
                "that an interview must be linked before grounded drafting."
            )
        if self.requested_surveys and surveys == 0:
            lines.append(
                "The user requested survey evidence, but no survey is linked. Do not "
                "substitute interview data or invent survey findings. State that a "
                "survey must be linked before grounded drafting."
            )
        if self.requested_datasets and datasets == 0:
            lines.append(
                "The user requested dataset evidence, but no dataset is linked. "
                "Do not invent dataset values or substitute another evidence family."
            )
        return "\n".join(lines)


def _mentions(text: str, pattern: re.Pattern[str]) -> tuple[bool, bool]:
    """Return positive and negated mentions of one evidence family."""
    positive = False
    negative = False
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 42) : match.start()]
        after = text[match.end() : match.end() + 32]
        local_negative = bool(
            re.search(
                rf"(?:{_NEGATOR.pattern})[^.!?]{{0,28}}$",
                before,
                re.I,
            )
            or re.match(rf"\s+(?:bitte\s+)?(?:{_NEGATOR.pattern})", after, re.I)
        )
        if local_negative:
            # "Use the linked interviews, do not create a new interview" is
            # a creation boundary, not a request to exclude the linked
            # evidence. Treat that artifact mention as neutral so the earlier
            # positive source mention remains authoritative. This distinction
            # matters especially for novice prompts that repeat the noun while
            # explaining what the assistant must not create.
            local = text[max(0, match.start() - 56) : match.end() + 56]
            if _CREATION_VERB.search(local) and _NEW_ARTIFACT.search(local):
                continue
            negative = True
        else:
            positive = True
    return positive, negative


def _source_bound_types(text: str) -> set[str]:
    """Return evidence families used as inputs, not as requested outputs."""
    bound: set[str] = set()
    for binding in _SOURCE_BINDING.finditer(text):
        suffix = text[binding.end() : binding.end() + 100]
        matches = sorted(
            [
                *((match.start(), match.end(), "survey") for match in _SURVEY.finditer(suffix)),
                *((match.start(), match.end(), "dataset") for match in _DATASET.finditer(suffix)),
                *(
                    (match.start(), match.end(), "interview")
                    for match in _INTERVIEW.finditer(suffix)
                ),
            ]
        )
        if not matches:
            continue
        first = matches[0]
        bound.add(first[2])
        previous_end = first[1]
        for start, end, kind in matches[1:]:
            connector = suffix[previous_end:start]
            if not re.fullmatch(r"\s*(?:,|&|and|und|sowie|plus)\s*", connector, re.I):
                break
            bound.add(kind)
            previous_end = end
    return bound


def _user_messages(history: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        str(item.get("content") or "").strip()
        for item in history
        if str(item.get("role") or "").lower() == "user" and str(item.get("content") or "").strip()
    ]


def _explicit_scope(message: str) -> WriterEvidenceScope | None:
    """Apply the same source exclusions to current and inherited instructions."""
    current_survey, current_survey_negative = _mentions(message, _SURVEY)
    current_interview, current_interview_negative = _mentions(message, _INTERVIEW)
    current_dataset, current_dataset_negative = _mentions(message, _DATASET)
    current_dataset_negative = current_dataset_negative or _mentions(message, _GENERIC_DATA)[1]
    if _PROJECT_SOURCE_ONLY.search(message):
        return WriterEvidenceScope(
            include_interviews=False,
            include_surveys=False,
            include_datasets=False,
            retrieval_query=message,
        )
    bound_types = _source_bound_types(message)
    bound_survey = current_survey and "survey" in bound_types
    bound_interview = current_interview and "interview" in bound_types
    bound_dataset = current_dataset and "dataset" in bound_types
    if bound_survey or bound_interview or bound_dataset:
        return WriterEvidenceScope(
            include_interviews=bound_interview and not current_interview_negative,
            include_surveys=bound_survey and not current_survey_negative,
            requested_interviews=bound_interview and not current_interview_negative,
            requested_surveys=bound_survey and not current_survey_negative,
            include_datasets=bound_dataset and not current_dataset_negative,
            requested_datasets=bound_dataset and not current_dataset_negative,
            retrieval_query=message,
        )
    if current_survey or current_interview or current_dataset:
        return WriterEvidenceScope(
            include_interviews=current_interview and not current_interview_negative,
            include_surveys=current_survey and not current_survey_negative,
            requested_interviews=current_interview and not current_interview_negative,
            requested_surveys=current_survey and not current_survey_negative,
            include_datasets=current_dataset and not current_dataset_negative,
            requested_datasets=current_dataset and not current_dataset_negative,
            retrieval_query=message,
        )

    # Pure negative statements narrow the current turn without creating a new
    # positive binding.  "Do not use the interview" therefore still works.
    if current_survey_negative or current_interview_negative or current_dataset_negative:
        return WriterEvidenceScope(
            include_interviews=not current_interview_negative,
            include_surveys=not current_survey_negative,
            include_datasets=not current_dataset_negative,
            retrieval_query=message,
        )

    return None


def resolve_writer_evidence_scope(
    message: str,
    history: Sequence[Mapping[str, Any]],
) -> WriterEvidenceScope:
    """Select linked evidence using the latest explicit user instruction.

    An explicit positive source choice replaces the previous choice. Separate
    exclusions accumulate until a positive choice overrides them. A terse
    follow-up inherits these rules from the caller's bounded user history;
    assistant claims never bind sources.
    """
    current = _explicit_scope(message)
    if current is not None and (
        current.requested_interviews or current.requested_surveys or current.requested_datasets
    ):
        return current

    def apply_scope(
        previous: WriterEvidenceScope,
        instruction: WriterEvidenceScope,
    ) -> WriterEvidenceScope:
        if (
            instruction.requested_interviews
            or instruction.requested_surveys
            or instruction.requested_datasets
        ):
            return instruction
        # "No surveys" must not reopen interviews excluded in an earlier turn,
        # nor silently substitute them for a now-excluded requested source.
        return replace(
            instruction,
            include_interviews=previous.include_interviews and instruction.include_interviews,
            include_surveys=previous.include_surveys and instruction.include_surveys,
            requested_interviews=previous.requested_interviews and instruction.include_interviews,
            requested_surveys=previous.requested_surveys and instruction.include_surveys,
            include_datasets=previous.include_datasets and instruction.include_datasets,
            requested_datasets=previous.requested_datasets and instruction.include_datasets,
        )

    inherited = WriterEvidenceScope()
    for previous in _user_messages(history):
        _, survey_negative = _mentions(previous, _SURVEY)
        _, interview_negative = _mentions(previous, _INTERVIEW)
        _, dataset_negative = _mentions(previous, _DATASET)
        dataset_negative = dataset_negative or _mentions(previous, _GENERIC_DATA)[1]
        if not (
            _source_bound_types(previous)
            or survey_negative
            or interview_negative
            or dataset_negative
            or _PROJECT_SOURCE_ONLY.search(previous)
        ):
            continue
        instruction = _explicit_scope(previous)
        if instruction is not None:
            inherited = apply_scope(inherited, instruction)
    if current is not None:
        return apply_scope(inherited, current)
    if inherited.retrieval_query:
        return replace(
            inherited,
            retrieval_query=f"{inherited.retrieval_query}\nFollow-up: {message}",
        )
    return WriterEvidenceScope(retrieval_query=message)
