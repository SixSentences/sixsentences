"""Deterministic contracts for bounded multi-angle research plans."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sixsentences_server.agent.events import agent_event_sink, safe_event_value
from sixsentences_server.agent.loop import AgentRunner
from sixsentences_server.agent.research_plan import (
    MAX_RESEARCH_ANGLES,
    MIN_RESEARCH_ANGLES,
    ResearchAngleDraft,
    ResearchPlan,
    build_research_plan,
)


class _FinishPool:
    def __init__(self) -> None:
        self.prompts: list[dict[str, object]] = []

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args
        self.prompts.append(kwargs)
        return SimpleNamespace(
            text=json.dumps(
                {
                    "action": "finish",
                    "update": "The evidence plan is ready.",
                    "final": {"ready": True},
                }
            )
        )


def test_default_research_plan_is_stable_bounded_and_explicit() -> None:
    first = build_research_plan("Effects of sleep on code review quality")
    repeated = build_research_plan("  effects   of sleep on code review quality  ")

    assert MIN_RESEARCH_ANGLES <= len(first.angles) <= MAX_RESEARCH_ANGLES
    assert len(first.angles) == 4
    assert first.id == repeated.id
    assert first.request_fingerprint == repeated.request_fingerprint
    assert [angle.id for angle in first.angles] == [angle.id for angle in repeated.angles]
    assert len({angle.id for angle in first.angles}) == len(first.angles)
    assert all(angle.coverage_criterion for angle in first.angles)
    assert first.coverage.minimum_covered_angles == MIN_RESEARCH_ANGLES
    assert first.coverage.minimum_sources_per_covered_angle == 1
    assert first.coverage.require_independent_source is True
    assert first.coverage.require_limit_or_conflict_check is True
    assert first.coverage.require_citation_traceability is True


def test_candidate_angles_are_deduplicated_and_capped() -> None:
    duplicate = ResearchAngleDraft(
        label="Population",
        subquestion="Which populations are represented in the evidence?",
        coverage_criterion="Record the population for one relevant source.",
    )
    candidates = [
        duplicate,
        ResearchAngleDraft(
            label="Duplicate wording",
            subquestion="  which populations are represented in the evidence?! ",
            coverage_criterion="This duplicate must not consume another slot.",
        ),
        *[
            ResearchAngleDraft(
                label=f"Angle {index}",
                subquestion=f"What does distinct angle {index} establish?",
                coverage_criterion=f"Record evidence for distinct angle {index}.",
            )
            for index in range(1, 7)
        ],
    ]

    plan = build_research_plan(
        "Bounded candidate test",
        candidate_angles=candidates,
        max_angles=MAX_RESEARCH_ANGLES,
    )

    assert len(plan.angles) == MAX_RESEARCH_ANGLES
    assert sum("populations are represented" in angle.subquestion for angle in plan.angles) == 1
    assert [angle.label for angle in plan.angles[:2]] == ["Population", "Angle 1"]


def test_plan_metadata_round_trips_through_json_and_public_event_bounds() -> None:
    plan = build_research_plan("Compare retrieval quality across evidence sources")
    metadata = json.loads(json.dumps(plan.to_metadata()))

    restored = ResearchPlan.from_metadata(metadata)
    public = safe_event_value({"research_plan": metadata})

    assert restored == plan
    assert public["research_plan"]["plan_id"] == plan.id
    assert public["research_plan"]["angle_ids"] == [angle.id for angle in plan.angles]
    assert public["research_plan"]["subquestions"] == [angle.subquestion for angle in plan.angles]
    assert "…" not in json.dumps(public, ensure_ascii=False)


def test_plan_metadata_rejects_tampering_and_misaligned_angle_arrays() -> None:
    metadata = build_research_plan("Tamper-evident plan").to_metadata()
    tampered = {**metadata, "plan_id": "rp_0000000000000000"}
    with pytest.raises(ValueError, match="not stable"):
        ResearchPlan.from_metadata(tampered)

    misaligned = {**metadata, "angle_labels": metadata["angle_labels"][:-1]}
    with pytest.raises(ValueError, match="lengths do not match"):
        ResearchPlan.from_metadata(misaligned)


def test_agent_runner_emits_and_uses_server_owned_research_plan() -> None:
    plan = build_research_plan("Evaluate infrastructure-as-code security evidence")
    pool = _FinishPool()
    events: list[dict[str, object]] = []
    runner = AgentRunner(
        pool,
        workspace="research",
        instructions="Use observed evidence only.",
        plan_steps=["Search complementary evidence angles", "Verify coverage"],
        research_plan=plan,
    )

    with agent_event_sink(events.append):
        result = runner.run(request="Evaluate infrastructure-as-code security evidence")

    assert result.completed
    created = events[0]
    assert created["event"] == "plan.created"
    assert created["research_plan"] == plan.to_metadata()
    prompt = str(pool.prompts[0]["prompt"])
    assert "SERVER-OWNED RESEARCH PLAN" in prompt
    assert plan.id in prompt
    assert all(angle.id in prompt for angle in plan.angles)


def test_quick_answer_search_floor_uses_stable_plan_angles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sixsentences_server.chat.service import (
        ToolStep,
        _QuickAnswerResearchDecisionPool,
    )

    plan = build_research_plan("Assess reproducibility of infrastructure-as-code studies")
    contexts: list[str] = []

    def formulate(
        request: str,
        pool: object,
        *,
        surface: str,
        context: str,
    ) -> str:
        del request, pool, surface
        contexts.append(context)
        return f"planned query {len(contexts)}"

    monkeypatch.setattr("sixsentences_server.chat.service.formulate_search_query", formulate)
    steps: list[ToolStep] = []
    adapter = _QuickAnswerResearchDecisionPool(
        object(),  # type: ignore[arg-type]
        request="Assess reproducibility of infrastructure-as-code studies",
        history="",
        works=lambda: [],
        steps=steps,
        tools={"find_papers": "Find scholarly evidence"},
        runner_tool_names={"find_papers"},
        base_tool_calls=3,
        minimum_searches=0,
        expand_search_floor_after_first=False,
        research_plan=plan,
    )

    first = adapter._ensure_search_decision(
        {"action": "answer"},
        {"find_papers": "Find scholarly evidence"},
    )
    steps.append(
        ToolStep(
            tool="find_papers",
            query=str(first["query"]),
            results=[{"id": "W1"}],
        )
    )
    adapter._ensure_search_decision(
        {"action": "answer"},
        {"find_papers": "Find scholarly evidence"},
    )

    assert adapter.required_searches() == plan.coverage.minimum_covered_angles
    assert plan.angles[0].id in contexts[0]
    assert plan.angles[0].coverage_criterion in contexts[0]
    assert plan.angles[1].id in contexts[1]
    assert plan.angles[1].coverage_criterion in contexts[1]


def test_research_coverage_ignores_failed_and_empty_search_receipts() -> None:
    from sixsentences_server.chat.service import (
        ToolStep,
        _QuickAnswerResearchDecisionPool,
    )

    steps = [
        ToolStep(tool="find_papers", query="one", status="failed"),
        ToolStep(tool="find_papers", query="two", results=[]),
        ToolStep(
            tool="web_search",
            query="three",
            results=[{"error": "provider unavailable"}],
        ),
    ]
    adapter = _QuickAnswerResearchDecisionPool(
        object(),  # type: ignore[arg-type]
        request="Assess the evidence",
        history="",
        works=lambda: [],
        steps=steps,
        tools={"find_papers": "Find scholarly evidence"},
        runner_tool_names={"find_papers"},
        base_tool_calls=3,
        minimum_searches=3,
        expand_search_floor_after_first=False,
    )

    assert adapter.coverage_search_count() == 0
    steps.append(
        ToolStep(
            tool="find_papers",
            query="four",
            results=[{"id": "pubmed:12345678", "title": "Traceable evidence"}],
        )
    )
    assert adapter.coverage_search_count() == 1
