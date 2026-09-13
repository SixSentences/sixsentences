"""Release contracts for novice-facing semantic chat evaluation."""

from pathlib import Path

import pytest

from sixsentences_server.evals.chat_semantic import (
    CHAT_SURFACES,
    NOVICE_TRAITS,
    ChatObservation,
    evaluate_chat_case,
    load_chat_benchmark,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chat_semantic_cases.json"


def test_manifest_covers_every_surface_and_novice_trait() -> None:
    cases = load_chat_benchmark(FIXTURE)

    assert len(cases) == len(CHAT_SURFACES) * len(NOVICE_TRAITS)
    assert {case.surface for case in cases} == CHAT_SURFACES
    assert {case.trait for case in cases} == NOVICE_TRAITS
    for surface in CHAT_SURFACES:
        assert {case.trait for case in cases if case.surface == surface} == NOVICE_TRAITS
    assert sum(len(case.turns) for case in cases) >= 60


def test_semantic_gate_rejects_correct_but_slow_and_misrouted_results() -> None:
    case = next(
        case for case in load_chat_benchmark(FIXTURE) if case.id == "writer-existing-evidence"
    )
    result = evaluate_chat_case(
        case,
        ChatObservation(
            answer="I drafted a section.",
            actions=("create_ai_interview",),
            artifacts=("writer_edit",),
            grounded_source_ids=(),
            confirmation_present=True,
            first_progress_ms=120,
            max_silent_gap_ms=7_500,
            total_ms=51_263,
        ),
    )

    assert result.passed is False
    assert "forbidden_action:create_ai_interview" in result.failures
    assert "missing_grounding" in result.failures
    assert any(failure.startswith("silent_gap_too_slow") for failure in result.failures)
    assert any(failure.startswith("total_too_slow") for failure in result.failures)


@pytest.mark.parametrize("missing", ["first_progress", "silent_gap", "total"])
def test_semantic_gate_requires_all_user_perceived_latency_measurements(missing: str) -> None:
    case = load_chat_benchmark(FIXTURE)[0]
    values = {
        "first_progress_ms": 100,
        "max_silent_gap_ms": 500,
        "total_ms": 1_000,
    }
    timing_key = {
        "first_progress": "first_progress_ms",
        "silent_gap": "max_silent_gap_ms",
        "total": "total_ms",
    }[missing]
    values[timing_key] = None
    result = evaluate_chat_case(
        case,
        ChatObservation(answer="Grounded answer", **values),  # type: ignore[arg-type]
    )
    assert f"missing_timing:{missing}" in result.failures
