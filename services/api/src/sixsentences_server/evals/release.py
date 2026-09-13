"""Fail-closed promotion gate for production corpus generations.

A structurally valid snapshot is only a candidate. Promotion additionally
requires quality evidence from a pinned multi-domain suite and low/base/high
cost reports generated from the same committed code revision.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from sixsentences_server.corpus.duckdb_store import RELEASE_FILE, DuckDBCorpus
from sixsentences_server.evals.economics import (
    CostCalibrationReport,
    CostCalibrationThresholds,
    ReviewCostReport,
    calibrate_review_cost,
    estimate_review_cost,
    standard_scenarios,
)
from sixsentences_server.evals.quality import (
    GoldenSuite,
    PredictionRun,
    QualityRegressionReport,
    QualityRegressionThresholds,
    QualityReport,
    QualityThresholds,
    compare_quality_reports,
    current_git_revision,
    evaluate_quality,
    sha256_json,
)

RELEASE_EVIDENCE_FILE = RELEASE_FILE
RELEASE_EVIDENCE_SCHEMA: Final = 5
QUALITY_EVIDENCE_FILE = "quality-report.json"
QUALITY_SUITE_EVIDENCE_FILE = "suite.json"
QUALITY_PREDICTIONS_EVIDENCE_FILE = "predictions.json"
REGRESSION_EVIDENCE_FILE = "quality-regression.json"
COST_CALIBRATION_EVIDENCE_FILE = "cost-calibration.json"
APPROVED_PREDICTION_RUNNERS: Final = frozenset({"sixsentences_server.release-eval"})
_ARTIFACT_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,80}$"


class ReleaseGateError(RuntimeError):
    """Raised when candidate evidence is incomplete, stale, or failing."""


class CostEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal["low", "base", "high"]
    artifact_file: str = Field(pattern=_ARTIFACT_PATTERN)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assumptions_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    llm_cost_usd: float
    infrastructure_cost_usd: float
    total_variable_cost_usd: float


class CorpusReleaseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[5] = RELEASE_EVIDENCE_SCHEMA
    promoted_at: datetime
    generation: str
    corpus_version: str
    corpus_sha256: str
    corpus_build_digest: str
    corpus_manifest_digest: str
    quality_artifact_file: str = Field(pattern=_ARTIFACT_PATTERN)
    quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_suite_artifact_file: str = Field(pattern=_ARTIFACT_PATTERN)
    quality_suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_predictions_artifact_file: str = Field(pattern=_ARTIFACT_PATTERN)
    quality_predictions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_suite_id: str
    quality_suite_version: str
    quality_suite_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_prediction_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_prediction_git_revision: str
    quality_prediction_runner: str
    quality_config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_generation: str | None = None
    baseline_quality_report_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    regression_artifact_file: str | None = Field(default=None, pattern=_ARTIFACT_PATTERN)
    regression_report_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    regression_report_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    suite_rebaseline_reason: str | None = Field(default=None, min_length=20, max_length=500)
    git_revision: str
    cost_evidence: list[CostEvidence]
    cost_calibration_artifact_file: str = Field(pattern=_ARTIFACT_PATTERN)
    cost_calibration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_calibration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_calibration_run_public_id: str
    gates: dict[str, bool]


def _load_quality_report(path: Path) -> tuple[bytes, QualityReport]:
    try:
        payload = path.read_bytes()
        return payload, QualityReport.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise ReleaseGateError(f"quality report is unreadable or invalid: {path}") from exc


def _load_quality_suite(path: Path) -> tuple[bytes, GoldenSuite]:
    try:
        payload = path.read_bytes()
        return payload, GoldenSuite.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise ReleaseGateError(f"quality suite is unreadable or invalid: {path}") from exc


def _load_quality_predictions(path: Path) -> tuple[bytes, PredictionRun]:
    try:
        payload = path.read_bytes()
        return payload, PredictionRun.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise ReleaseGateError(f"quality predictions are unreadable or invalid: {path}") from exc


def _validate_quality_artifacts(
    suite: GoldenSuite,
    predictions: PredictionRun,
    quality: QualityReport,
) -> None:
    if (
        suite.suite_id != quality.suite_id
        or suite.version != quality.suite_version
        or suite.digest != quality.suite_digest
    ):
        raise ReleaseGateError("quality suite does not match the quality report")
    if (
        predictions.suite_id != quality.suite_id
        or predictions.suite_version != quality.suite_version
        or predictions.suite_digest != quality.suite_digest
        or predictions.digest != quality.prediction_digest
        or predictions.git_revision != quality.prediction_git_revision
        or predictions.generated_by != quality.prediction_runner
        or predictions.corpus_version != quality.corpus_version
        or predictions.corpus_sha256 != quality.corpus_sha256
        or predictions.corpus_build_digest != quality.corpus_build_digest
        or predictions.config_digest != quality.config_digest
        or predictions.seed != quality.seed
    ):
        raise ReleaseGateError("quality predictions do not match the quality report")


def _validate_quality_reproduction(
    suite: GoldenSuite,
    predictions: PredictionRun,
    quality: QualityReport,
) -> None:
    try:
        recomputed = evaluate_quality(suite, predictions)
    except ValueError as exc:
        raise ReleaseGateError("quality artifacts cannot reproduce the quality report") from exc
    # The evaluator revision is verified independently. Normalize the fresh
    # report to the submitted evaluator revision before comparing metrics.
    recomputed.git_revision = quality.git_revision
    if recomputed.digest != quality.digest:
        raise ReleaseGateError("quality artifacts do not reproduce the quality report")


def _load_cost_reports(paths: list[Path]) -> dict[str, tuple[bytes, ReviewCostReport]]:
    reports: dict[str, tuple[bytes, ReviewCostReport]] = {}
    for path in paths:
        try:
            payload = path.read_bytes()
            report = ReviewCostReport.model_validate_json(payload)
        except (OSError, ValueError) as exc:
            raise ReleaseGateError(f"cost report is unreadable or invalid: {path}") from exc
        name = report.scenario.name
        if name not in {"low", "base", "high"}:
            raise ReleaseGateError(f"cost report {path} is not a standard release scenario")
        if name in reports:
            raise ReleaseGateError(f"duplicate cost report for scenario {name}")
        reports[name] = (payload, report)
    missing = {"low", "base", "high"} - set(reports)
    if missing:
        raise ReleaseGateError(f"missing cost scenarios: {', '.join(sorted(missing))}")
    return reports


def _load_regression_report(path: Path) -> tuple[bytes, QualityRegressionReport]:
    try:
        payload = path.read_bytes()
        return payload, QualityRegressionReport.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise ReleaseGateError(f"quality regression report is unreadable: {path}") from exc


def _load_cost_calibration(path: Path) -> tuple[bytes, CostCalibrationReport]:
    try:
        payload = path.read_bytes()
        return payload, CostCalibrationReport.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise ReleaseGateError(f"cost calibration report is unreadable: {path}") from exc


def _artifact_path(directory: Path, filename: str) -> Path:
    path = directory / filename
    if path.parent != directory or path.is_symlink() or not path.is_file():
        raise ReleaseGateError(f"release artifact is missing or unsafe: {filename}")
    return path


def _validate_economics(
    reports: dict[str, tuple[bytes, ReviewCostReport]],
    *,
    require_current_configuration: bool = False,
) -> None:
    for scenario, (_, report) in reports.items():
        if not report.model_prices_per_million_tokens:
            raise ReleaseGateError(f"{scenario} cost report has no model-price snapshot")
        if not all(plan.review_fits_provider_budget for plan in report.plans):
            raise ReleaseGateError(f"{scenario} cost report exceeds the configured provider budget")
    if not all(plan.review_fits_action_limit for plan in reports["base"][1].plans):
        raise ReleaseGateError("base review exceeds the per-action provider limit")
    if not all(plan.review_fits_action_limit for plan in reports["high"][1].plans):
        raise ReleaseGateError("high review exceeds the per-action provider limit")
    if not require_current_configuration:
        return
    expected_scenarios = standard_scenarios()
    for name, (_, report) in reports.items():
        if report.scenario != expected_scenarios[name]:
            raise ReleaseGateError(f"{name} cost report changed the canonical scenario")
        expected = estimate_review_cost(
            expected_scenarios[name],
            routing=report.routing,
        )
        if report.digest != expected.digest:
            raise ReleaseGateError(f"{name} cost report was not produced by the current cost model")


def _validate_cost_calibration(
    calibration: CostCalibrationReport,
    baseline: ReviewCostReport,
    *,
    require_current_configuration: bool = False,
) -> None:
    expected_scenario = baseline.scenario.model_copy(
        update={"name": "custom", "candidates": calibration.observed.candidates}
    )
    if calibration.baseline_cost_report_digest != baseline.digest:
        raise ReleaseGateError("cost calibration belongs to another base cost report")
    if calibration.observed_usage_digest != calibration.observed.digest:
        raise ReleaseGateError("cost calibration observed-usage digest changed")
    if (
        calibration.normalized_estimate.scenario != expected_scenario
        or calibration.normalized_estimate.routing != baseline.routing
    ):
        raise ReleaseGateError("cost calibration normalized an incompatible scenario")
    estimated_cost = calibration.normalized_estimate.risk_adjusted_llm_cost_usd
    expected_ratio = (
        calibration.observed.total_cost_usd / estimated_cost if estimated_cost > 0 else float("inf")
    )
    expected_priced_fraction = (
        calibration.observed.provider_priced_calls / calibration.observed.total_calls
        if calibration.observed.total_calls
        else 0.0
    )
    if calibration.actual_to_estimated_ratio != round(
        expected_ratio, 6
    ) or calibration.provider_priced_call_fraction != round(expected_priced_fraction, 6):
        raise ReleaseGateError("cost calibration ratios do not match observed usage")
    if not calibration.passed or not all(gate.passed for gate in calibration.gates):
        raise ReleaseGateError("cost calibration contains a failing gate")
    if require_current_configuration:
        expected = calibrate_review_cost(
            baseline,
            calibration.observed,
            thresholds=CostCalibrationThresholds(),
        )
        if calibration.digest != expected.digest:
            raise ReleaseGateError(
                "cost calibration was not produced by the current calibration model"
            )


def verify_release_evidence(
    corpus: DuckDBCorpus,
    generation: str,
) -> CorpusReleaseEvidence:
    """Verify every byte and semantic link in a stored release bundle."""

    manifest = corpus.generation_manifest(generation)
    directory = corpus.snapshots_dir / generation
    evidence_path = _artifact_path(directory, RELEASE_EVIDENCE_FILE)
    try:
        evidence = CorpusReleaseEvidence.model_validate_json(evidence_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ReleaseGateError("production corpus release evidence is invalid") from exc
    expected_manifest_digest = sha256_json(manifest.model_dump(mode="json"))
    if (
        evidence.generation != generation
        or evidence.corpus_version != manifest.version
        or evidence.corpus_sha256 != manifest.sha256
        or evidence.corpus_build_digest != manifest.build_digest
        or evidence.corpus_manifest_digest != expected_manifest_digest
    ):
        raise ReleaseGateError("release receipt does not match the corpus manifest")
    required_gates = {
        "corpus_structural",
        "quality",
        "quality_external",
        "economics",
        "cost_calibration",
        "revision_match",
        "prediction_runner",
        "regression",
        "suite_continuity",
    }
    if set(evidence.gates) != required_gates or not all(evidence.gates.values()):
        raise ReleaseGateError("release receipt does not contain passing canonical gates")
    if evidence.git_revision == "unknown":
        raise ReleaseGateError("release receipt has no immutable Git revision")
    if evidence.quality_artifact_file != QUALITY_EVIDENCE_FILE:
        raise ReleaseGateError("release receipt uses a non-canonical quality artifact")
    if evidence.quality_suite_artifact_file != QUALITY_SUITE_EVIDENCE_FILE:
        raise ReleaseGateError("release receipt uses a non-canonical quality suite artifact")
    if evidence.quality_predictions_artifact_file != QUALITY_PREDICTIONS_EVIDENCE_FILE:
        raise ReleaseGateError("release receipt uses a non-canonical quality predictions artifact")
    quality_path = _artifact_path(directory, evidence.quality_artifact_file)
    quality_payload, quality = _load_quality_report(quality_path)
    suite_path = _artifact_path(directory, evidence.quality_suite_artifact_file)
    suite_payload, suite = _load_quality_suite(suite_path)
    predictions_path = _artifact_path(directory, evidence.quality_predictions_artifact_file)
    predictions_payload, predictions = _load_quality_predictions(predictions_path)
    if hashlib.sha256(quality_payload).hexdigest() != evidence.quality_report_sha256:
        raise ReleaseGateError("quality evidence checksum changed")
    if quality.digest != evidence.quality_report_digest:
        raise ReleaseGateError("quality evidence digest changed")
    if hashlib.sha256(suite_payload).hexdigest() != evidence.quality_suite_sha256:
        raise ReleaseGateError("quality suite checksum changed")
    if hashlib.sha256(predictions_payload).hexdigest() != evidence.quality_predictions_sha256:
        raise ReleaseGateError("quality predictions checksum changed")
    if (
        quality.corpus_version != manifest.version
        or quality.corpus_sha256 != manifest.sha256
        or quality.corpus_build_digest != manifest.build_digest
        or quality.suite_id != evidence.quality_suite_id
        or quality.suite_version != evidence.quality_suite_version
        or quality.suite_digest != evidence.quality_suite_digest
        or quality.prediction_digest != evidence.quality_prediction_digest
        or quality.prediction_git_revision != evidence.quality_prediction_git_revision
        or quality.prediction_runner != evidence.quality_prediction_runner
        or quality.config_digest != evidence.quality_config_digest
    ):
        raise ReleaseGateError("quality evidence does not match its release receipt")
    if not quality.passed or not quality.release_eligible:
        raise ReleaseGateError("stored quality evidence no longer passes")
    if quality.thresholds != QualityThresholds():
        raise ReleaseGateError("stored quality evidence used non-production thresholds")
    if quality.git_revision != evidence.git_revision:
        raise ReleaseGateError("quality evaluator revision does not match the release")
    if quality.prediction_git_revision != evidence.git_revision:
        raise ReleaseGateError("quality predictions were produced by another revision")
    if quality.prediction_runner not in APPROVED_PREDICTION_RUNNERS:
        raise ReleaseGateError("quality predictions came from an unapproved runner")
    _validate_quality_artifacts(suite, predictions, quality)

    if len(evidence.cost_evidence) != 3:
        raise ReleaseGateError("release receipt needs exactly three cost scenarios")
    stored_costs: dict[str, tuple[bytes, ReviewCostReport]] = {}
    for item in evidence.cost_evidence:
        expected_name = f"cost-report-{item.scenario}.json"
        if item.artifact_file != expected_name:
            raise ReleaseGateError(f"non-canonical cost artifact for {item.scenario}")
        path = _artifact_path(directory, item.artifact_file)
        payload = path.read_bytes()
        try:
            report = ReviewCostReport.model_validate_json(payload)
        except ValueError as exc:
            raise ReleaseGateError(f"stored {item.scenario} cost report is invalid") from exc
        if hashlib.sha256(payload).hexdigest() != item.report_sha256:
            raise ReleaseGateError(f"stored {item.scenario} cost report checksum changed")
        if (
            report.digest != item.report_digest
            or report.assumptions_digest != item.assumptions_digest
            or report.scenario.name != item.scenario
            or report.git_revision != evidence.git_revision
            or report.llm_cost_usd != item.llm_cost_usd
            or report.infrastructure_cost_usd != item.infrastructure_cost_usd
            or report.total_variable_cost_usd != item.total_variable_cost_usd
        ):
            raise ReleaseGateError(f"stored {item.scenario} cost evidence changed")
        if item.scenario in stored_costs:
            raise ReleaseGateError(f"duplicate stored cost scenario {item.scenario}")
        stored_costs[item.scenario] = (payload, report)
    _validate_economics(stored_costs)
    if evidence.cost_calibration_artifact_file != COST_CALIBRATION_EVIDENCE_FILE:
        raise ReleaseGateError("release receipt uses a non-canonical cost calibration artifact")
    calibration_path = _artifact_path(directory, evidence.cost_calibration_artifact_file)
    calibration_payload, calibration = _load_cost_calibration(calibration_path)
    base_report = stored_costs["base"][1]
    if hashlib.sha256(calibration_payload).hexdigest() != evidence.cost_calibration_sha256:
        raise ReleaseGateError("cost calibration checksum changed")
    if (
        calibration.digest != evidence.cost_calibration_digest
        or calibration.observed.run_public_id != evidence.cost_calibration_run_public_id
        or calibration.git_revision != evidence.git_revision
        or calibration.observed.git_revision != evidence.git_revision
        or calibration.normalized_estimate.git_revision != evidence.git_revision
        or calibration.thresholds != CostCalibrationThresholds()
    ):
        raise ReleaseGateError("stored cost calibration does not match the release")
    _validate_cost_calibration(calibration, base_report)

    has_baseline = evidence.baseline_generation is not None
    if has_baseline != (evidence.baseline_quality_report_digest is not None):
        raise ReleaseGateError("release baseline fields are incomplete")
    if not has_baseline:
        if any(
            value is not None
            for value in (
                evidence.regression_artifact_file,
                evidence.regression_report_sha256,
                evidence.regression_report_digest,
                evidence.suite_rebaseline_reason,
            )
        ):
            raise ReleaseGateError("first release must not contain regression evidence")
        return evidence
    if evidence.suite_rebaseline_reason is not None:
        if any(
            value is not None
            for value in (
                evidence.regression_artifact_file,
                evidence.regression_report_sha256,
                evidence.regression_report_digest,
            )
        ):
            raise ReleaseGateError("suite rebaseline must not masquerade as a regression pass")
        return evidence
    if (
        evidence.regression_artifact_file != REGRESSION_EVIDENCE_FILE
        or evidence.regression_report_sha256 is None
        or evidence.regression_report_digest is None
    ):
        raise ReleaseGateError("release regression evidence is incomplete")
    regression_path = _artifact_path(directory, evidence.regression_artifact_file)
    regression_payload, regression = _load_regression_report(regression_path)
    if hashlib.sha256(regression_payload).hexdigest() != evidence.regression_report_sha256:
        raise ReleaseGateError("quality regression checksum changed")
    if regression.digest != evidence.regression_report_digest:
        raise ReleaseGateError("quality regression digest changed")
    if (
        not regression.passed
        or regression.thresholds != QualityRegressionThresholds()
        or regression.baseline_report_digest != evidence.baseline_quality_report_digest
        or regression.candidate_report_digest != quality.digest
        or regression.suite_digest != quality.suite_digest
    ):
        raise ReleaseGateError("quality regression evidence does not match the release")
    return evidence


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_bytes(payload)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)


def _active_production_baseline(
    corpus: DuckDBCorpus,
    *,
    candidate_generation: str,
    approve_active_unreleased_beta: bool,
) -> tuple[str, QualityReport] | None:
    """Load the immutable quality report of the current production release."""

    if not corpus.exists():
        if approve_active_unreleased_beta:
            raise ReleaseGateError("there is no active production beta to approve")
        return None
    info = corpus.info()
    build = info.get("build")
    profile = str(build.get("profile") or "") if isinstance(build, dict) else ""
    if not profile.startswith("production-"):
        if approve_active_unreleased_beta:
            raise ReleaseGateError("the active corpus is not an unreleased production beta")
        return None
    generation = corpus.works_path.parent.name
    release_path = corpus.works_path.parent / RELEASE_EVIDENCE_FILE
    release_artifact_present = release_path.exists() or release_path.is_symlink()
    if not release_artifact_present:
        if not approve_active_unreleased_beta:
            verify_release_evidence(corpus, generation)
        if generation != candidate_generation:
            raise ReleaseGateError(
                "the explicit beta approval must target the exact active generation"
            )
        return None
    evidence = verify_release_evidence(corpus, generation)
    if approve_active_unreleased_beta:
        raise ReleaseGateError("the active production generation already has release evidence")
    quality_path = corpus.works_path.parent / evidence.quality_artifact_file
    _, report = _load_quality_report(quality_path)
    return generation, report


def promote_corpus_release(
    corpus: DuckDBCorpus,
    *,
    generation: str,
    quality_report_path: Path,
    quality_suite_path: Path,
    quality_predictions_path: Path,
    cost_report_paths: list[Path],
    cost_calibration_path: Path,
    snapshots_to_keep: int = 2,
    suite_rebaseline_reason: str | None = None,
    approve_active_unreleased_beta: bool = False,
) -> CorpusReleaseEvidence:
    """Validate all evidence, persist its receipt, then atomically promote."""

    manifest = corpus.generation_manifest(generation)
    if not manifest.build.profile.startswith("production-"):
        raise ReleaseGateError("only a production corpus profile can be promoted")
    quality_payload, quality = _load_quality_report(quality_report_path)
    suite_payload, suite = _load_quality_suite(quality_suite_path)
    predictions_payload, predictions = _load_quality_predictions(quality_predictions_path)
    costs = _load_cost_reports(cost_report_paths)
    calibration_payload, calibration = _load_cost_calibration(cost_calibration_path)
    if not quality.passed or not quality.release_eligible:
        raise ReleaseGateError("quality report did not pass production release gates")
    if quality.thresholds != QualityThresholds():
        raise ReleaseGateError("quality report did not use the production release thresholds")
    _validate_quality_artifacts(suite, predictions, quality)
    _validate_quality_reproduction(suite, predictions, quality)
    corpus_matches = (
        quality.corpus_version == manifest.version
        and quality.corpus_sha256 == manifest.sha256
        and quality.corpus_build_digest == manifest.build_digest
    )
    if not corpus_matches:
        raise ReleaseGateError("quality report belongs to a different corpus generation")
    revision = current_git_revision()
    if revision == "unknown":
        raise ReleaseGateError("release Git revision is unavailable; set SIX_RELEASE_GIT_REVISION")
    if quality.prediction_runner not in APPROVED_PREDICTION_RUNNERS:
        raise ReleaseGateError("quality predictions came from an unapproved runner")
    report_revisions = {quality.git_revision, quality.prediction_git_revision}
    report_revisions.update(report.git_revision for _, report in costs.values())
    report_revisions.update(
        {
            calibration.git_revision,
            calibration.observed.git_revision,
            calibration.normalized_estimate.git_revision,
        }
    )
    if revision != "unknown" and report_revisions != {revision}:
        raise ReleaseGateError("quality or cost evidence was generated from another Git revision")
    _validate_economics(costs, require_current_configuration=True)
    if not calibration.passed or calibration.thresholds != CostCalibrationThresholds():
        raise ReleaseGateError("cost calibration did not pass production thresholds")
    _validate_cost_calibration(
        calibration,
        costs["base"][1],
        require_current_configuration=True,
    )
    baseline = _active_production_baseline(
        corpus,
        candidate_generation=generation,
        approve_active_unreleased_beta=approve_active_unreleased_beta,
    )
    regression_payload: bytes | None = None
    regression_passed = True
    baseline_generation: str | None = None
    baseline_digest: str | None = None
    rebaseline_reason = suite_rebaseline_reason.strip() if suite_rebaseline_reason else None
    regression_digest: str | None = None
    if baseline is not None:
        baseline_generation, baseline_quality = baseline
        baseline_digest = baseline_quality.digest
        same_suite = (
            baseline_quality.suite_id == quality.suite_id
            and baseline_quality.suite_version == quality.suite_version
            and baseline_quality.suite_digest == quality.suite_digest
        )
        if not same_suite:
            if rebaseline_reason is None or len(rebaseline_reason) < 20:
                raise ReleaseGateError(
                    "quality suite changed; provide an explicit suite rebaseline reason"
                )
        else:
            if rebaseline_reason is not None:
                raise ReleaseGateError(
                    "suite rebaseline reason is only allowed when the suite changed"
                )
            regression = compare_quality_reports(baseline_quality, quality)
            if regression.thresholds != QualityRegressionThresholds():
                raise ReleaseGateError("quality regression did not use release thresholds")
            if not regression.passed:
                failures = ", ".join(gate.name for gate in regression.gates if not gate.passed)
                raise ReleaseGateError(f"quality regression gates failed: {failures}")
            regression_passed = regression.passed
            regression_payload = regression.model_dump_json(indent=2).encode("utf-8")
            regression_digest = regression.digest
    elif rebaseline_reason is not None:
        raise ReleaseGateError("the first production release has no suite to rebaseline")
    evidence = CorpusReleaseEvidence(
        promoted_at=datetime.now(UTC),
        generation=generation,
        corpus_version=manifest.version,
        corpus_sha256=manifest.sha256,
        corpus_build_digest=manifest.build_digest,
        corpus_manifest_digest=sha256_json(manifest.model_dump(mode="json")),
        quality_artifact_file=QUALITY_EVIDENCE_FILE,
        quality_report_sha256=hashlib.sha256(quality_payload).hexdigest(),
        quality_report_digest=quality.digest,
        quality_suite_artifact_file=QUALITY_SUITE_EVIDENCE_FILE,
        quality_suite_sha256=hashlib.sha256(suite_payload).hexdigest(),
        quality_predictions_artifact_file=QUALITY_PREDICTIONS_EVIDENCE_FILE,
        quality_predictions_sha256=hashlib.sha256(predictions_payload).hexdigest(),
        quality_suite_id=quality.suite_id,
        quality_suite_version=quality.suite_version,
        quality_suite_digest=quality.suite_digest,
        quality_prediction_digest=quality.prediction_digest,
        quality_prediction_git_revision=quality.prediction_git_revision,
        quality_prediction_runner=quality.prediction_runner,
        quality_config_digest=quality.config_digest,
        baseline_generation=baseline_generation,
        baseline_quality_report_digest=baseline_digest,
        regression_artifact_file=(
            REGRESSION_EVIDENCE_FILE if regression_payload is not None else None
        ),
        regression_report_sha256=(
            hashlib.sha256(regression_payload).hexdigest()
            if regression_payload is not None
            else None
        ),
        regression_report_digest=regression_digest,
        suite_rebaseline_reason=rebaseline_reason,
        git_revision=revision,
        cost_evidence=[
            CostEvidence(
                scenario=cast(Literal["low", "base", "high"], scenario),
                artifact_file=f"cost-report-{scenario}.json",
                report_sha256=hashlib.sha256(payload).hexdigest(),
                report_digest=report.digest,
                assumptions_digest=report.assumptions_digest,
                llm_cost_usd=report.llm_cost_usd,
                infrastructure_cost_usd=report.infrastructure_cost_usd,
                total_variable_cost_usd=report.total_variable_cost_usd,
            )
            for scenario, (payload, report) in sorted(costs.items())
        ],
        cost_calibration_artifact_file=COST_CALIBRATION_EVIDENCE_FILE,
        cost_calibration_sha256=hashlib.sha256(calibration_payload).hexdigest(),
        cost_calibration_digest=calibration.digest,
        cost_calibration_run_public_id=calibration.observed.run_public_id,
        gates={
            "corpus_structural": manifest.gates.passed,
            "quality": quality.passed,
            "quality_external": quality.release_eligible,
            "economics": True,
            "cost_calibration": calibration.passed,
            "revision_match": revision == "unknown" or report_revisions == {revision},
            "prediction_runner": quality.prediction_runner in APPROVED_PREDICTION_RUNNERS,
            "regression": regression_passed,
            "suite_continuity": baseline is None
            or regression_payload is not None
            or bool(rebaseline_reason),
        },
    )
    evidence_dir = corpus.snapshots_dir / generation
    _atomic_write(evidence_dir / QUALITY_EVIDENCE_FILE, quality_payload)
    _atomic_write(evidence_dir / QUALITY_SUITE_EVIDENCE_FILE, suite_payload)
    _atomic_write(evidence_dir / QUALITY_PREDICTIONS_EVIDENCE_FILE, predictions_payload)
    for scenario, (payload, _) in costs.items():
        _atomic_write(evidence_dir / f"cost-report-{scenario}.json", payload)
    _atomic_write(evidence_dir / COST_CALIBRATION_EVIDENCE_FILE, calibration_payload)
    if regression_payload is not None:
        _atomic_write(evidence_dir / REGRESSION_EVIDENCE_FILE, regression_payload)
    evidence_path = evidence_dir / RELEASE_EVIDENCE_FILE
    _atomic_write(evidence_path, evidence.model_dump_json(indent=2).encode("utf-8"))
    directory_fd = os.open(evidence_dir, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    verify_release_evidence(corpus, generation)
    corpus.activate_generation(
        generation,
        snapshots_to_keep=snapshots_to_keep,
        require_release_evidence=True,
    )
    return evidence


def load_release_evidence(path: Path) -> CorpusReleaseEvidence:
    """Load a stored promotion receipt for audits and incident response."""

    return CorpusReleaseEvidence.model_validate_json(path.read_text(encoding="utf-8"))


def render_release_evidence(evidence: CorpusReleaseEvidence) -> str:
    costs = ", ".join(
        f"{item.scenario}=${item.total_variable_cost_usd:.4f}" for item in evidence.cost_evidence
    )
    return (
        f"promoted {evidence.corpus_version} ({evidence.generation})\n"
        f"quality: {evidence.quality_suite_id}@{evidence.quality_suite_version}\n"
        f"costs: {costs}\n"
        f"git: {evidence.git_revision}"
    )
