"""Deterministic release contracts for user-facing chat benchmark results.

The benchmark runner may use real providers and browsers, but release scoring
must not depend on an LLM judging another LLM.  Each fixture declares observable
tool, action, artifact, grounding, latency and interaction requirements.  This
module validates those declarations and scores captured observations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CHAT_SURFACES = frozenset(
    {
        "quick_answer",
        "writer",
        "survey",
        "dataset",
        "interview",
        "interview_study",
    }
)

NOVICE_TRAITS = frozenset(
    {
        "typo",
        "ambiguous",
        "correction",
        "mixed_language",
        "contradictory",
        "missing_prerequisite",
        "destructive_safety",
        "existing_evidence",
    }
)

_STRUCTURED_FAILURE_MARKERS = (
    "could not verify the structured response",
    "strukturierte antwort nicht verifizieren",
    "could not safely complete this request",
    "anfrage gerade nicht sicher abschließen",
    "workspace was not changed",
    "workspace wurde nicht verändert",
)


@dataclass(frozen=True)
class ChatBenchmarkCase:
    """One declarative novice journey and its observable acceptance contract."""

    id: str
    surface: str
    trait: str
    locale: str
    turns: tuple[str, ...]
    must_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    must_artifacts: tuple[str, ...] = ()
    requires_grounding: bool = False
    requires_confirmation: bool = False
    max_first_progress_ms: int = 1_000
    max_silent_gap_ms: int = 5_000
    max_total_ms: int = 30_000


@dataclass(frozen=True)
class ChatObservation:
    """Provider and browser evidence captured for one benchmark journey."""

    answer: str
    actions: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    grounded_source_ids: tuple[str, ...] = ()
    confirmation_present: bool = False
    first_progress_ms: int | None = None
    max_silent_gap_ms: int | None = None
    total_ms: int | None = None
    terminal_state: str = "completed"
    duplicate_side_effects: int = 0
    latest_instruction_kept: bool = True
    provider_internals_visible: bool = False
    unsupported_claims: int = 0


@dataclass(frozen=True)
class ChatCaseResult:
    """Machine-checkable case outcome retained by the release evidence."""

    case_id: str
    passed: bool
    failures: tuple[str, ...] = field(default_factory=tuple)


def load_chat_benchmark(path: Path) -> tuple[ChatBenchmarkCase, ...]:
    """Load and validate the frozen JSON fixture without silent defaults."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("chat benchmark must contain a cases list")
    cases: list[ChatBenchmarkCase] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("every chat benchmark case must be an object")
        case_id = str(raw.get("id") or "").strip()
        surface = str(raw.get("surface") or "").strip()
        trait = str(raw.get("trait") or "").strip()
        locale = str(raw.get("locale") or "").strip()
        turns = tuple(str(item).strip() for item in raw.get("turns") or [] if str(item).strip())
        if not case_id or case_id in seen:
            raise ValueError(f"duplicate or empty chat benchmark id: {case_id!r}")
        if surface not in CHAT_SURFACES:
            raise ValueError(f"unknown chat surface for {case_id}: {surface!r}")
        if trait not in NOVICE_TRAITS:
            raise ValueError(f"unknown novice trait for {case_id}: {trait!r}")
        if locale not in {"de", "en", "mixed"}:
            raise ValueError(f"unknown locale for {case_id}: {locale!r}")
        if not turns:
            raise ValueError(f"chat benchmark case has no turns: {case_id}")
        seen.add(case_id)
        cases.append(
            ChatBenchmarkCase(
                id=case_id,
                surface=surface,
                trait=trait,
                locale=locale,
                turns=turns,
                must_actions=tuple(str(item) for item in raw.get("must_actions") or []),
                forbidden_actions=tuple(str(item) for item in raw.get("forbidden_actions") or []),
                must_artifacts=tuple(str(item) for item in raw.get("must_artifacts") or []),
                requires_grounding=bool(raw.get("requires_grounding")),
                requires_confirmation=bool(raw.get("requires_confirmation")),
                max_first_progress_ms=int(raw.get("max_first_progress_ms", 1_000)),
                max_silent_gap_ms=int(raw.get("max_silent_gap_ms", 5_000)),
                max_total_ms=int(raw.get("max_total_ms", 30_000)),
            )
        )
    return tuple(cases)


def evaluate_chat_case(
    case: ChatBenchmarkCase,
    observation: ChatObservation,
) -> ChatCaseResult:
    """Score one captured journey against deterministic release gates."""

    failures: list[str] = []
    folded_answer = observation.answer.casefold().strip()
    if not folded_answer:
        failures.append("empty_answer")
    if any(marker in folded_answer for marker in _STRUCTURED_FAILURE_MARKERS):
        failures.append("structured_response_failure_exposed")
    if observation.terminal_state != "completed":
        failures.append(f"terminal_state:{observation.terminal_state}")
    if observation.duplicate_side_effects:
        failures.append("duplicate_side_effect")
    if not observation.latest_instruction_kept:
        failures.append("latest_instruction_lost")
    if observation.provider_internals_visible:
        failures.append("provider_internals_visible")
    if observation.unsupported_claims:
        failures.append("unsupported_claim")

    action_set = set(observation.actions)
    artifact_set = set(observation.artifacts)
    for required in case.must_actions:
        if required not in action_set:
            failures.append(f"missing_action:{required}")
    for forbidden in case.forbidden_actions:
        if forbidden in action_set:
            failures.append(f"forbidden_action:{forbidden}")
    for required in case.must_artifacts:
        if required not in artifact_set:
            failures.append(f"missing_artifact:{required}")
    if case.requires_grounding and not observation.grounded_source_ids:
        failures.append("missing_grounding")
    if case.requires_confirmation and not observation.confirmation_present:
        failures.append("missing_confirmation")

    timing_checks = (
        ("first_progress", observation.first_progress_ms, case.max_first_progress_ms),
        ("silent_gap", observation.max_silent_gap_ms, case.max_silent_gap_ms),
        ("total", observation.total_ms, case.max_total_ms),
    )
    for label, actual, maximum in timing_checks:
        if actual is None:
            failures.append(f"missing_timing:{label}")
        elif actual > maximum:
            failures.append(f"{label}_too_slow:{actual}>{maximum}")
    return ChatCaseResult(case_id=case.id, passed=not failures, failures=tuple(failures))
