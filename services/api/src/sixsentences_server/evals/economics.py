"""Reproducible cost scenarios for systematic-review releases.

This is a planning model, not an accounting ledger. Every assumption is emitted
with the report, while model prices and plan guardrails are imported from the
same runtime modules that enforce real usage.  Operators can therefore compare
low/base/high reviews before releasing a routing or workflow change.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sixsentences_server.core.plans import COMMUNITY_PLAN
from sixsentences_server.evals.quality import current_git_revision, sha256_json
from sixsentences_server.llm.base import PRICES_PER_MTOK, TaskType, cost_usd, price_of
from sixsentences_server.llm.pool import RoutingConfig
from sixsentences_server.llm.privacy import (
    PRIVATE_DEFAULT_MODEL,
    PRIVATE_PREMIUM_MODEL,
    PRIVATE_SCREENING_MODELS,
    require_route_for_scope,
)

ECONOMICS_SCHEMA: Final = 3
COST_CALIBRATION_SCHEMA: Final = 2


class TokenCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    model: str
    calls: int = Field(ge=0)
    input_tokens_per_call: int = Field(ge=0)
    output_tokens_per_call: int = Field(ge=0)

    @property
    def total_input_tokens(self) -> int:
        return self.calls * self.input_tokens_per_call

    @property
    def total_output_tokens(self) -> int:
        return self.calls * self.output_tokens_per_call

    @property
    def total_cost_usd(self) -> float:
        # Long-context tiers apply to one request, not the summed tokens of
        # thousands of independent abstract-screening calls.
        return self.calls * cost_usd(
            self.model, self.input_tokens_per_call, self.output_tokens_per_call
        )


class ReviewCostScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["low", "base", "high", "custom"]
    candidates: int = Field(ge=1)
    screening_reviewers: int = Field(default=2, ge=1)
    adjudication_fraction: float = Field(default=0.12, ge=0.0, le=1.0)
    full_text_fraction: float = Field(default=0.15, ge=0.0, le=1.0)
    full_text_retrieval_rate: float = Field(default=0.70, ge=0.0, le=1.0)
    full_text_reviewers: int = Field(default=2, ge=1)
    final_studies_fraction: float = Field(default=0.03, ge=0.0, le=1.0)
    protocol_calls: int = Field(default=1, ge=1)
    query_expansion_calls: int = Field(default=3, ge=0)
    synthesis_calls: int = Field(default=2, ge=0)
    claims_per_final_study: float = Field(default=2.0, ge=0.0)
    infrastructure_base_usd: float = Field(default=0.04, ge=0.0)
    infrastructure_per_candidate_usd: float = Field(default=0.00002, ge=0.0)
    full_text_transfer_usd: float = Field(default=0.001, ge=0.0)
    contingency_fraction: float = Field(default=0.20, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def final_fraction_not_above_full_text(self) -> ReviewCostScenario:
        if self.final_studies_fraction > self.full_text_fraction:
            raise ValueError("final studies cannot exceed the full-text candidate fraction")
        return self


class CostRouting(BaseModel):
    """Recorded model routes for planning, separate from provider credentials."""

    model_config = ConfigDict(extra="forbid")

    synthesis_model: str = PRIVATE_PREMIUM_MODEL
    screening_models: list[str] = Field(
        default_factory=lambda: list(PRIVATE_SCREENING_MODELS),
        min_length=1,
    )
    precision_models: list[str] = Field(
        default_factory=lambda: list(reversed(PRIVATE_SCREENING_MODELS)),
        min_length=1,
    )

    @model_validator(mode="after")
    def distinct_review_models(self) -> CostRouting:
        """Do not count repeated IDs as multiple review model configurations."""
        for models in (self.screening_models, self.precision_models):
            if len(set(models)) != len(models):
                raise ValueError("review model routes must contain distinct model IDs")
        return self

    @property
    def claim_verification_model(self) -> str:
        """Mirror the runtime claim route without rewriting legacy report fields."""
        if any(model in PRIVATE_SCREENING_MODELS for model in self.screening_models):
            return PRIVATE_DEFAULT_MODEL
        return self.precision_models[0]

    @classmethod
    def from_runtime(cls, routing: RoutingConfig) -> CostRouting:
        """Read effective private routes without building clients or sending data."""
        if routing.synthesis is None or not routing.screening:
            raise ValueError("cost evidence requires configured synthesis and screening routes")
        refs = [routing.synthesis, *routing.screening]
        if routing.adjudication is not None:
            refs.append(routing.adjudication)
        for ref in refs:
            require_route_for_scope(ref, "private")
        precision = list(
            dict.fromkeys(
                [
                    *([routing.adjudication.model] if routing.adjudication is not None else []),
                    *(ref.model for ref in routing.screening),
                ]
            )
        )
        return cls(
            synthesis_model=routing.synthesis.model,
            screening_models=[ref.model for ref in routing.screening],
            precision_models=precision,
        )


def validate_observed_cost_routes(
    routing: CostRouting, calls: Iterable[tuple[str, str, str]]
) -> None:
    """Reject a cost calibration whose actual metered models contradict its report.

    Inputs contain task, provider and model IDs only, never prompts or keys.
    Native Gemini model IDs cannot be substituted with OpenRouter aliases.
    Unknown tasks fail closed because this planning model cannot price their
    relationship to the review. The immutable usage ledger is not changed.
    """
    allowed = {
        TaskType.PROTOCOL_SYNTHESIS.value: {routing.synthesis_model},
        TaskType.QUERY_EXPANSION.value: {routing.synthesis_model},
        TaskType.CHAT.value: {routing.synthesis_model},
        TaskType.SCREENING.value: set(routing.screening_models),
        TaskType.ADJUDICATION.value: set(routing.precision_models),
        TaskType.FULL_TEXT_SCREENING.value: set(routing.precision_models),
        TaskType.CLAIM_VERIFICATION.value: {routing.claim_verification_model},
    }
    for task, provider, model in calls:
        if model not in allowed.get(task, set()):
            raise ValueError(f"observed {task} model does not match the cost report")
        if model.startswith("gemini-") and provider != "gemini":
            raise ValueError("observed Gemini cost route was not executed by the native provider")


class StageCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class PlanEconomics(BaseModel):
    """Provider-budget safety for the community resource profile."""

    model_config = ConfigDict(extra="forbid")

    plan: str
    provider_budget_usd: float
    action_cost_limit_usd: float
    estimated_reviews_per_budget: float
    review_fits_action_limit: bool
    review_fits_provider_budget: bool


class ReviewCostReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = ECONOMICS_SCHEMA
    generated_at: datetime
    git_revision: str
    scenario: ReviewCostScenario
    routing: CostRouting
    assumptions_digest: str
    model_prices_per_million_tokens: dict[str, tuple[float, float]]
    stages: list[StageCost]
    llm_cost_usd: float
    risk_adjusted_llm_cost_usd: float
    infrastructure_cost_usd: float
    subtotal_variable_cost_usd: float
    contingency_usd: float
    total_variable_cost_usd: float
    plans: list[PlanEconomics]

    @property
    def digest(self) -> str:
        """Stable assumptions and result digest independent of generation time."""

        return sha256_json(self.model_dump(mode="json", exclude={"generated_at"}))


class ObservedStageUsage(BaseModel):
    """Persisted cost telemetry for one task in a completed review."""

    model_config = ConfigDict(extra="forbid")

    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class ObservedReviewUsage(BaseModel):
    """Immutable runtime facts used to calibrate release cost assumptions."""

    model_config = ConfigDict(extra="forbid")

    run_id: int = Field(ge=1)
    run_public_id: str = Field(min_length=1, max_length=32)
    corpus_version: str = Field(min_length=1)
    git_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    run_config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: int = Field(ge=1)
    completed: bool
    total_calls: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0.0)
    provider_priced_calls: int = Field(ge=0)
    catalog_priced_calls: int = Field(ge=0)
    by_task: dict[str, ObservedStageUsage]

    @model_validator(mode="after")
    def totals_match_stage_ledger(self) -> ObservedReviewUsage:
        if self.provider_priced_calls + self.catalog_priced_calls != self.total_calls:
            raise ValueError("cost-source call counts do not match total calls")
        if sum(item.calls for item in self.by_task.values()) != self.total_calls:
            raise ValueError("task call counts do not match total calls")
        if sum(item.input_tokens for item in self.by_task.values()) != self.total_input_tokens:
            raise ValueError("task input tokens do not match total input tokens")
        if sum(item.output_tokens for item in self.by_task.values()) != self.total_output_tokens:
            raise ValueError("task output tokens do not match total output tokens")
        task_cost = round(sum(item.cost_usd for item in self.by_task.values()), 6)
        # Each stage and the total are persisted at micro-dollar precision. Their
        # independent rounding may differ by at most half a unit per value.
        rounding_tolerance = (len(self.by_task) + 1) * 0.0000005
        if abs(task_cost - self.total_cost_usd) > rounding_tolerance:
            raise ValueError("task costs do not match total cost")
        return self

    @property
    def digest(self) -> str:
        """Stable checksum for the observed telemetry payload."""

        return sha256_json(self.model_dump(mode="json"))


class CostCalibrationThresholds(BaseModel):
    """Fail-closed limits for one representative end-to-end review."""

    model_config = ConfigDict(extra="forbid")

    min_candidates: int = 200
    min_actual_to_estimated_ratio: float = 0.20
    max_actual_to_estimated_ratio: float = 1.25
    min_provider_priced_call_fraction: float = 0.80
    required_tasks: tuple[str, ...] = (
        TaskType.PROTOCOL_SYNTHESIS.value,
        TaskType.SCREENING.value,
        TaskType.FULL_TEXT_SCREENING.value,
    )


class CostCalibrationGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    detail: str


class CostCalibrationReport(BaseModel):
    """Comparison of a real run with the risk-adjusted planning model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = COST_CALIBRATION_SCHEMA
    generated_at: datetime
    git_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    baseline_cost_report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_usage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed: ObservedReviewUsage
    normalized_estimate: ReviewCostReport
    actual_to_estimated_ratio: float = Field(ge=0.0)
    provider_priced_call_fraction: float = Field(ge=0.0, le=1.0)
    thresholds: CostCalibrationThresholds
    gates: list[CostCalibrationGate]
    passed: bool

    @property
    def digest(self) -> str:
        """Stable calibration digest independent of its generation time."""

        payload = self.model_dump(mode="json")
        payload.pop("generated_at", None)
        normalized = payload.get("normalized_estimate")
        if isinstance(normalized, dict):
            normalized.pop("generated_at", None)
        return sha256_json(payload)


def standard_scenarios() -> dict[str, ReviewCostScenario]:
    """Return bounded planning cases; none are hidden constants in a spreadsheet."""

    return {
        "low": ReviewCostScenario(
            name="low",
            candidates=250,
            adjudication_fraction=0.06,
            full_text_fraction=0.10,
            full_text_retrieval_rate=0.65,
            final_studies_fraction=0.02,
            query_expansion_calls=1,
        ),
        "base": ReviewCostScenario(name="base", candidates=1_000),
        "high": ReviewCostScenario(
            name="high",
            candidates=6_000,
            adjudication_fraction=0.20,
            full_text_fraction=0.25,
            full_text_retrieval_rate=0.80,
            final_studies_fraction=0.06,
            query_expansion_calls=5,
            synthesis_calls=3,
        ),
    }


def _token_calls(scenario: ReviewCostScenario, routing: CostRouting) -> list[TokenCall]:
    if scenario.screening_reviewers > len(set(routing.screening_models)):
        raise ValueError("scenario requires more distinct screening models than configured")
    if scenario.full_text_reviewers > len(set(routing.precision_models)):
        raise ValueError("scenario requires more distinct full-text models than configured")
    full_text_sought = round(scenario.candidates * scenario.full_text_fraction)
    full_text_retrieved = round(full_text_sought * scenario.full_text_retrieval_rate)
    final_studies = round(scenario.candidates * scenario.final_studies_fraction)
    calls = [
        TokenCall(
            stage="protocol",
            model=routing.synthesis_model,
            calls=scenario.protocol_calls,
            input_tokens_per_call=450,
            output_tokens_per_call=900,
        ),
        TokenCall(
            stage="query expansion",
            model=routing.synthesis_model,
            calls=scenario.query_expansion_calls,
            input_tokens_per_call=1_200,
            output_tokens_per_call=600,
        ),
    ]
    for model in routing.screening_models[: scenario.screening_reviewers]:
        calls.append(
            TokenCall(
                stage="title and abstract screening",
                model=model,
                calls=scenario.candidates,
                input_tokens_per_call=850,
                output_tokens_per_call=140,
            )
        )
    calls.extend(
        [
            TokenCall(
                stage="screening adjudication",
                model=routing.precision_models[0],
                calls=round(scenario.candidates * scenario.adjudication_fraction),
                input_tokens_per_call=1_100,
                output_tokens_per_call=160,
            ),
            TokenCall(
                stage="claim verification",
                model=routing.claim_verification_model,
                calls=round(final_studies * scenario.claims_per_final_study),
                input_tokens_per_call=1_800,
                output_tokens_per_call=220,
            ),
            TokenCall(
                stage="final synthesis",
                model=routing.synthesis_model,
                calls=scenario.synthesis_calls,
                input_tokens_per_call=12_000,
                output_tokens_per_call=2_000,
            ),
        ]
    )
    for model in routing.precision_models[: scenario.full_text_reviewers]:
        calls.append(
            TokenCall(
                stage="full-text screening",
                model=model,
                calls=full_text_retrieved,
                input_tokens_per_call=7_500,
                output_tokens_per_call=260,
            )
        )
    return calls


def _stage_costs(calls: list[TokenCall]) -> list[StageCost]:
    grouped: dict[str, StageCost] = {}
    for call in calls:
        stage = grouped.setdefault(
            call.stage,
            StageCost(stage=call.stage, calls=0, input_tokens=0, output_tokens=0, cost_usd=0),
        )
        stage.calls += call.calls
        stage.input_tokens += call.total_input_tokens
        stage.output_tokens += call.total_output_tokens
        stage.cost_usd += call.total_cost_usd
    for stage in grouped.values():
        stage.cost_usd = round(stage.cost_usd, 6)
    return list(grouped.values())


def estimate_review_cost(
    scenario: ReviewCostScenario,
    *,
    routing: CostRouting | None = None,
) -> ReviewCostReport:
    """Estimate one review and compare it with community provider-safety limits."""

    resolved_routing = routing or CostRouting()
    calls = _token_calls(scenario, resolved_routing)
    if any(call.model not in PRICES_PER_MTOK for call in calls):
        raise ValueError("cost evidence requires a reviewed price for every model")
    stages = _stage_costs(calls)
    llm_cost = sum(stage.cost_usd for stage in stages)
    full_texts = round(
        scenario.candidates * scenario.full_text_fraction * scenario.full_text_retrieval_rate
    )
    infrastructure = (
        scenario.infrastructure_base_usd
        + scenario.candidates * scenario.infrastructure_per_candidate_usd
        + full_texts * scenario.full_text_transfer_usd
    )
    subtotal = llm_cost + infrastructure
    contingency = subtotal * scenario.contingency_fraction
    risk_adjusted_llm_cost = llm_cost * (1 + scenario.contingency_fraction)
    total = subtotal + contingency
    plan = COMMUNITY_PLAN
    provider_budget = plan.monthly_ai_cost_usd
    plans = [
        PlanEconomics(
            plan=plan.tier.value,
            provider_budget_usd=round(provider_budget, 4),
            action_cost_limit_usd=plan.max_ai_cost_per_action_usd,
            estimated_reviews_per_budget=round(provider_budget / total, 2),
            review_fits_action_limit=(risk_adjusted_llm_cost <= plan.max_ai_cost_per_action_usd),
            review_fits_provider_budget=(total <= provider_budget),
        )
    ]
    price_models = {
        model
        for model in [
            resolved_routing.synthesis_model,
            resolved_routing.claim_verification_model,
            *resolved_routing.precision_models,
            *resolved_routing.screening_models,
        ]
    }
    assumptions = {
        "scenario": scenario.model_dump(mode="json"),
        "routing": resolved_routing.model_dump(mode="json"),
        "claim_verification_model": resolved_routing.claim_verification_model,
    }
    return ReviewCostReport(
        generated_at=datetime.now(UTC),
        git_revision=current_git_revision(),
        scenario=scenario,
        routing=resolved_routing,
        assumptions_digest=sha256_json(assumptions),
        model_prices_per_million_tokens={model: price_of(model) for model in sorted(price_models)},
        stages=stages,
        llm_cost_usd=round(llm_cost, 6),
        risk_adjusted_llm_cost_usd=round(risk_adjusted_llm_cost, 6),
        infrastructure_cost_usd=round(infrastructure, 6),
        subtotal_variable_cost_usd=round(subtotal, 6),
        contingency_usd=round(contingency, 6),
        total_variable_cost_usd=round(total, 6),
        plans=plans,
    )


def calibrate_review_cost(
    baseline: ReviewCostReport,
    observed: ObservedReviewUsage,
    *,
    thresholds: CostCalibrationThresholds | None = None,
) -> CostCalibrationReport:
    """Calibrate the planning model against one completed, metered review.

    The baseline scenario is normalized to the observed number of screened
    candidates before comparing costs. This prevents a small smoke run from
    looking cheap merely because it processed fewer records.
    """

    limits = thresholds or CostCalibrationThresholds()
    if baseline.scenario.name != "base":
        raise ValueError("cost calibration requires the standard base scenario")
    if baseline.git_revision != observed.git_revision:
        raise ValueError("observed run and cost model came from different Git revisions")
    normalized_scenario = baseline.scenario.model_copy(
        update={"name": "custom", "candidates": observed.candidates}
    )
    normalized = estimate_review_cost(
        normalized_scenario,
        routing=baseline.routing,
    )
    if normalized.git_revision != observed.git_revision:
        raise ValueError("current evaluator revision does not match observed run")
    estimated = normalized.risk_adjusted_llm_cost_usd
    ratio = observed.total_cost_usd / estimated if estimated > 0 else float("inf")
    priced_fraction = (
        observed.provider_priced_calls / observed.total_calls if observed.total_calls else 0.0
    )
    missing_tasks = sorted(
        task
        for task in limits.required_tasks
        if task not in observed.by_task or observed.by_task[task].calls < 1
    )
    gates = [
        CostCalibrationGate(
            name="completed representative review",
            passed=observed.completed and observed.candidates >= limits.min_candidates,
            detail=(
                f"completed={observed.completed}; candidates={observed.candidates}; "
                f"minimum={limits.min_candidates}"
            ),
        ),
        CostCalibrationGate(
            name="required pipeline tasks metered",
            passed=not missing_tasks,
            detail="all required tasks present"
            if not missing_tasks
            else f"missing: {', '.join(missing_tasks)}",
        ),
        CostCalibrationGate(
            name="provider-priced call coverage",
            passed=priced_fraction >= limits.min_provider_priced_call_fraction,
            detail=(
                f"{priced_fraction:.1%} provider-priced; "
                f"minimum={limits.min_provider_priced_call_fraction:.1%}"
            ),
        ),
        CostCalibrationGate(
            name="cost-model lower bound",
            passed=ratio >= limits.min_actual_to_estimated_ratio,
            detail=(
                f"actual/estimate={ratio:.3f}; minimum={limits.min_actual_to_estimated_ratio:.3f}"
            ),
        ),
        CostCalibrationGate(
            name="cost-model upper bound",
            passed=ratio <= limits.max_actual_to_estimated_ratio,
            detail=(
                f"actual/estimate={ratio:.3f}; maximum={limits.max_actual_to_estimated_ratio:.3f}"
            ),
        ),
    ]
    return CostCalibrationReport(
        generated_at=datetime.now(UTC),
        git_revision=observed.git_revision,
        baseline_cost_report_digest=baseline.digest,
        observed_usage_digest=observed.digest,
        observed=observed,
        normalized_estimate=normalized,
        actual_to_estimated_ratio=round(ratio, 6),
        provider_priced_call_fraction=round(priced_fraction, 6),
        thresholds=limits,
        gates=gates,
        passed=all(gate.passed for gate in gates),
    )


def write_cost_calibration(path: Path, report: CostCalibrationReport) -> None:
    """Write immutable cost-calibration evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def render_cost_calibration(report: CostCalibrationReport) -> str:
    """Render a concise operator-facing calibration summary."""

    lines = [
        f"cost calibration: {'PASS' if report.passed else 'FAIL'}",
        f"run: {report.observed.run_public_id} ({report.observed.candidates:,} candidates)",
        f"actual LLM: ${report.observed.total_cost_usd:.4f}",
        f"risk-adjusted estimate: ${report.normalized_estimate.risk_adjusted_llm_cost_usd:.4f}",
        f"actual / estimate: {report.actual_to_estimated_ratio:.3f}",
        "",
    ]
    for gate in report.gates:
        lines.append(f"{'PASS' if gate.passed else 'FAIL'}  {gate.name}: {gate.detail}")
    return "\n".join(lines)


def write_cost_report(path: Path, report: ReviewCostReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def render_cost_report(report: ReviewCostReport) -> str:
    lines = [
        f"review cost scenario: {report.scenario.name}",
        f"candidates: {report.scenario.candidates:,}",
        f"synthesis model: {report.routing.synthesis_model}",
        f"screening models: {', '.join(report.routing.screening_models)}",
        f"precision models: {', '.join(report.routing.precision_models)}",
        f"claim verification model: {report.routing.claim_verification_model}",
        f"LLM: ${report.llm_cost_usd:.4f}",
        f"LLM (risk-adjusted): ${report.risk_adjusted_llm_cost_usd:.4f}",
        f"infrastructure: ${report.infrastructure_cost_usd:.4f}",
        f"contingency: ${report.contingency_usd:.4f}",
        f"total variable: ${report.total_variable_cost_usd:.4f}",
        "",
    ]
    for stage in report.stages:
        lines.append(f"{stage.stage}: {stage.calls:,} calls, ${stage.cost_usd:.4f}")
    lines.append("")
    for plan in report.plans:
        action = "PASS" if plan.review_fits_action_limit else "FAIL"
        budget = "PASS" if plan.review_fits_provider_budget else "FAIL"
        lines.append(
            f"{plan.plan}: {plan.estimated_reviews_per_budget:.2f} reviews per "
            f"provider budget; action={action}; budget={budget}"
        )
    return "\n".join(lines)
