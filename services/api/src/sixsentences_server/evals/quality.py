"""Versioned, release-gated evaluation for the complete review pipeline.

The evaluator deliberately keeps immutable external judgements separate from
system predictions.  A release report therefore cannot silently turn model
output into ground truth, and every score can be reproduced against the exact
suite, corpus, configuration, seed, and Git revision that produced it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sixsentences_server.evals.metrics import evaluate_screening

QUALITY_SUITE_SCHEMA: Final = 3
QUALITY_PREDICTION_SCHEMA: Final = 2
QUALITY_REPORT_SCHEMA: Final = 3
QUALITY_REGRESSION_SCHEMA: Final = 2
DEFAULT_EVAL_SEED = 42


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    """Return a stable digest for JSON-compatible evidence."""

    return hashlib.sha256(_canonical_json(value)).hexdigest()


class GoldVerdict(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class PredictedVerdict(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNSURE = "unsure"


class SuiteSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["external", "internal_adjudicated", "test_fixture"]
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    license: str = Field(min_length=1)
    license_verified: bool = False
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoldWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: str = Field(min_length=1)
    retrieval_relevant: bool = False
    screening: GoldVerdict | None = None
    full_text: GoldVerdict | None = None


class GoldClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    supported_by: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_support(self) -> GoldClaim:
        if len(self.supported_by) != len(set(self.supported_by)):
            raise ValueError("supported_by contains duplicate work ids")
        return self


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    question: str = Field(min_length=1)
    query: str = Field(min_length=1)
    works: list[GoldWork] = Field(min_length=1)
    final_relevant_ids: list[str] = Field(default_factory=list)
    claims: list[GoldClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identifiers(self) -> GoldenCase:
        ids = [work.work_id for work in self.works]
        if len(ids) != len(set(ids)):
            raise ValueError(f"case {self.case_id!r} contains duplicate work ids")
        known = set(ids)
        unknown_final = set(self.final_relevant_ids) - known
        if unknown_final:
            raise ValueError(f"final relevance references unknown ids: {sorted(unknown_final)}")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError(f"case {self.case_id!r} contains duplicate claim ids")
        unknown_support = {
            work_id
            for claim in self.claims
            for work_id in claim.supported_by
            if work_id not in known
        }
        if unknown_support:
            raise ValueError(f"claim support references unknown ids: {sorted(unknown_support)}")
        return self


class GoldenSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = QUALITY_SUITE_SCHEMA
    suite_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    created_at: datetime
    sources: list[SuiteSource] = Field(min_length=1)
    cases: list[GoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> GoldenSuite:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("suite contains duplicate case ids")
        source_names = [source.name for source in self.sources]
        if len(source_names) != len(set(source_names)):
            raise ValueError("suite contains duplicate source names")
        unknown_sources = {case.source_name for case in self.cases} - set(source_names)
        if unknown_sources:
            raise ValueError(f"cases reference unknown sources: {sorted(unknown_sources)}")
        return self

    @property
    def digest(self) -> str:
        # Build time is provenance, not benchmark content. Rebuilding the same
        # pinned bytes and normalized cases must yield the same suite digest.
        return sha256_json(self.model_dump(mode="json", exclude={"created_at"}))


class ClaimPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    cited_work_ids: list[str] = Field(default_factory=list)


class CasePrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    corpus_work_ids: list[str] | None = None
    retrieved_ids: list[str] = Field(default_factory=list)
    screening: dict[str, PredictedVerdict] = Field(default_factory=dict)
    full_text: dict[str, PredictedVerdict] = Field(default_factory=dict)
    final_ranked_ids: list[str] = Field(default_factory=list)
    claims: list[ClaimPrediction] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_output_identifiers(self) -> CasePrediction:
        for label, values in (
            ("corpus_work_ids", self.corpus_work_ids or []),
            ("retrieved_ids", self.retrieved_ids),
            ("final_ranked_ids", self.final_ranked_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} contains duplicate work ids")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claims contain duplicate claim ids")
        return self


class PredictionRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = QUALITY_PREDICTION_SCHEMA
    suite_id: str = Field(min_length=1)
    suite_version: str = Field(min_length=1)
    suite_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    generated_by: str = Field(min_length=3)
    git_revision: str = Field(min_length=7)
    corpus_version: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_build_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = DEFAULT_EVAL_SEED
    predictions: list[CasePrediction] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_predictions(self) -> PredictionRun:
        case_ids = [prediction.case_id for prediction in self.predictions]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("prediction run contains duplicate case ids")
        return self

    @property
    def digest(self) -> str:
        """Stable prediction digest independent of the export timestamp."""

        return sha256_json(self.model_dump(mode="json", exclude={"created_at"}))


class QualityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_release_cases: int = 10
    min_release_domains: int = 3
    min_cases_per_domain: int = 2
    min_screening_gold: int = 500
    min_screening_includes: int = 50
    min_screening_excludes: int = 200
    min_screening_domains: int = 3
    min_full_text_gold: int = 100
    min_full_text_includes: int = 30
    min_full_text_excludes: int = 50
    min_full_text_domains: int = 3
    min_citation_claims: int = 50
    min_citation_domains: int = 3
    min_final_relevance_judgements: int = 100
    min_ranking_domains: int = 3
    min_corpus_coverage: float = 0.80
    min_retrieval_recall: float = 0.85
    min_retrieval_recall_at_50: float = 0.70
    min_screening_sensitivity: float = 0.95
    max_screening_false_exclusion_rate: float = 0.05
    max_screening_unresolved_rate: float = 0.45
    min_full_text_sensitivity: float = 0.90
    max_full_text_false_exclusion_rate: float = 0.10
    max_full_text_unresolved_rate: float = 0.35
    min_citation_precision: float = 0.95
    min_citation_recall: float = 0.85
    max_unsupported_claim_rate: float = 0.02
    min_final_ndcg_at_50: float = 0.75
    min_domain_retrieval_recall: float = 0.65
    min_domain_screening_sensitivity: float = 0.85
    min_domain_screening_specificity: float = 0.70
    min_domain_full_text_sensitivity: float = 0.80
    min_domain_full_text_specificity: float = 0.70
    min_domain_citation_precision: float = 0.80
    min_domain_citation_recall: float = 0.70
    min_domain_final_ndcg_at_50: float = 0.60


class StageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold: int = 0
    gold_includes: int = 0
    gold_excludes: int = 0
    evaluated: int = 0
    true_positive: int = 0
    true_negative: int = 0
    false_positive: int = 0
    false_negative: int = 0
    unresolved: int = 0
    sensitivity: float = 1.0
    specificity: float = 1.0
    false_exclusion_rate: float = 0.0
    unresolved_rate: float = 0.0


class RetrievalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_relevant: int = 0
    present_in_corpus: int = 0
    retrieved: int = 0
    corpus_coverage: float = 0.0
    recall: float = 0.0
    precision: float = 0.0
    recall_at_50: float = 0.0
    reciprocal_rank: float = 0.0


class CitationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: int = 0
    expected_links: int = 0
    predicted_links: int = 0
    correct_links: int = 0
    precision: float = 1.0
    recall: float = 1.0
    unsupported_claims: int = 0
    unsupported_claim_rate: float = 0.0
    unexpected_claims: int = 0


class RankingMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_relevant: int = 0
    recall_at_50: float = 0.0
    ndcg_at_50: float = 0.0


class CaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    domain: str
    retrieval: RetrievalMetrics
    screening: StageMetrics
    screening_wss_at_95: float
    full_text: StageMetrics
    citations: CitationMetrics
    ranking: RankingMetrics


class AggregateMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: int
    domains: int
    retrieval: RetrievalMetrics
    screening: StageMetrics
    screening_wss_at_95: float
    full_text: StageMetrics
    citations: CitationMetrics
    ranking: RankingMetrics


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float
    threshold: float
    operator: Literal[">=", "<="]
    passed: bool


class SuiteInventory(BaseModel):
    """Label and provenance inventory checked before an expensive evaluation."""

    model_config = ConfigDict(extra="forbid")

    suite_id: str
    suite_version: str
    suite_digest: str
    sources: int
    source_kinds: list[str]
    external_licenses_verified: bool
    cases: int
    domains: dict[str, int]
    works: int
    retrieval_relevant: int
    screening_labels: int
    screening_includes: int
    screening_excludes: int
    screening_domains: int
    full_text_labels: int
    full_text_includes: int
    full_text_excludes: int
    full_text_domains: int
    citation_claims: int
    citation_links: int
    citation_domains: int
    final_relevance_judgements: int
    ranking_domains: int
    gates: list[GateResult]
    release_ready: bool


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = QUALITY_REPORT_SCHEMA
    generated_at: datetime
    suite_id: str
    suite_version: str
    suite_digest: str
    prediction_digest: str
    source_kinds: list[str]
    corpus_version: str
    corpus_sha256: str
    corpus_build_digest: str
    config_digest: str
    seed: int
    git_revision: str
    prediction_git_revision: str
    prediction_runner: str
    thresholds: QualityThresholds
    cases: list[CaseMetrics]
    by_domain: dict[str, AggregateMetrics]
    overall: AggregateMetrics
    gates: list[GateResult]
    release_eligible: bool
    passed: bool

    @property
    def digest(self) -> str:
        """Stable report identity independent of when it was evaluated."""

        return sha256_json(self.model_dump(mode="json", exclude={"generated_at"}))


class QualityRegressionThresholds(BaseModel):
    """Maximum allowed degradation from the last approved release."""

    model_config = ConfigDict(extra="forbid")

    max_corpus_coverage_drop: float = 0.02
    max_retrieval_recall_drop: float = 0.02
    max_screening_sensitivity_drop: float = 0.01
    max_screening_specificity_drop: float = 0.03
    max_full_text_sensitivity_drop: float = 0.02
    max_full_text_specificity_drop: float = 0.05
    max_citation_precision_drop: float = 0.02
    max_citation_recall_drop: float = 0.02
    max_final_ndcg_at_50_drop: float = 0.03
    max_domain_retrieval_recall_drop: float = 0.05
    max_domain_screening_sensitivity_drop: float = 0.05
    max_domain_full_text_sensitivity_drop: float = 0.08
    max_domain_citation_precision_drop: float = 0.05
    max_domain_citation_recall_drop: float = 0.05
    max_domain_final_ndcg_at_50_drop: float = 0.05


class QualityRegressionReport(BaseModel):
    """Reproducible comparison with the last approved quality baseline."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = QUALITY_REGRESSION_SCHEMA
    generated_at: datetime
    suite_id: str
    suite_version: str
    suite_digest: str
    baseline_report_digest: str
    candidate_report_digest: str
    thresholds: QualityRegressionThresholds
    gates: list[GateResult]
    passed: bool

    @property
    def digest(self) -> str:
        """Stable comparison identity independent of the evaluation time."""

        return sha256_json(self.model_dump(mode="json", exclude={"generated_at"}))


def load_suite(path: Path) -> GoldenSuite:
    return GoldenSuite.model_validate_json(path.read_text(encoding="utf-8"))


def load_predictions(path: Path) -> PredictionRun:
    return PredictionRun.model_validate_json(path.read_text(encoding="utf-8"))


def write_report(path: Path, report: QualityReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def _ratio(numerator: float, denominator: float, *, empty: float = 1.0) -> float:
    return numerator / denominator if denominator else empty


def _unique_ordered(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _retrieval_metrics(case: GoldenCase, prediction: CasePrediction) -> RetrievalMetrics:
    gold = {work.work_id for work in case.works if work.retrieval_relevant}
    corpus = set(prediction.corpus_work_ids) if prediction.corpus_work_ids is not None else None
    available = gold if corpus is None else gold & corpus
    ranked = _unique_ordered(prediction.retrieved_ids)
    retrieved = available & set(ranked)
    first_rank = next((index for index, item in enumerate(ranked, 1) if item in available), None)
    return RetrievalMetrics(
        gold_relevant=len(gold),
        present_in_corpus=len(available),
        retrieved=len(retrieved),
        corpus_coverage=_ratio(len(available), len(gold), empty=0.0),
        recall=_ratio(len(retrieved), len(available), empty=0.0),
        precision=_ratio(len(retrieved), len(ranked), empty=0.0),
        recall_at_50=_ratio(len(set(ranked[:50]) & available), len(available), empty=0.0),
        reciprocal_rank=(1.0 / first_rank) if first_rank is not None else 0.0,
    )


def _stage_metrics(
    works: list[GoldWork],
    predictions: dict[str, PredictedVerdict],
    *,
    field: Literal["screening", "full_text"],
) -> StageMetrics:
    gold = {
        work.work_id: getattr(work, field) for work in works if getattr(work, field) is not None
    }
    tp = tn = fp = fn = unresolved = 0
    for work_id, verdict in gold.items():
        predicted = predictions.get(work_id, PredictedVerdict.UNSURE)
        if predicted is PredictedVerdict.UNSURE:
            unresolved += 1
        elif verdict is GoldVerdict.INCLUDE and predicted is PredictedVerdict.INCLUDE:
            tp += 1
        elif verdict is GoldVerdict.INCLUDE:
            fn += 1
        elif predicted is PredictedVerdict.EXCLUDE:
            tn += 1
        else:
            fp += 1
    positives = sum(verdict is GoldVerdict.INCLUDE for verdict in gold.values())
    negatives = len(gold) - positives
    return StageMetrics(
        gold=len(gold),
        gold_includes=positives,
        gold_excludes=negatives,
        evaluated=len(gold) - unresolved,
        true_positive=tp,
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        unresolved=unresolved,
        sensitivity=_ratio(tp, positives, empty=1.0),
        specificity=_ratio(tn, negatives, empty=1.0),
        false_exclusion_rate=_ratio(fn, positives, empty=0.0),
        unresolved_rate=_ratio(unresolved, len(gold), empty=0.0),
    )


def _screening_wss(case: GoldenCase, prediction: CasePrediction) -> float:
    labels = {
        work.work_id: work.screening is GoldVerdict.INCLUDE
        for work in case.works
        if work.screening is not None
    }
    ordered = [labels[item] for item in _unique_ordered(prediction.retrieved_ids) if item in labels]
    missing = [label for item, label in labels.items() if item not in prediction.retrieved_ids]
    report = evaluate_screening(ordered + missing)
    return report.wss_at_95


def _citation_metrics(case: GoldenCase, prediction: CasePrediction) -> CitationMetrics:
    expected = {claim.claim_id: set(claim.supported_by) for claim in case.claims}
    actual = {claim.claim_id: set(claim.cited_work_ids) for claim in prediction.claims}
    expected_links = sum(len(items) for items in expected.values())
    predicted_links = sum(len(items) for items in actual.values())
    correct = sum(
        len(support & actual.get(claim_id, set())) for claim_id, support in expected.items()
    )
    unsupported_expected = sum(
        1
        for claim_id, support in expected.items()
        if not actual.get(claim_id, set()) or not (support & actual[claim_id])
    )
    unexpected = len(set(actual) - set(expected))
    unsupported = unsupported_expected + unexpected
    claim_denominator = max(len(expected), len(actual))
    return CitationMetrics(
        claims=len(expected),
        expected_links=expected_links,
        predicted_links=predicted_links,
        correct_links=correct,
        precision=_ratio(correct, predicted_links, empty=1.0 if not expected else 0.0),
        recall=_ratio(correct, expected_links, empty=1.0),
        unsupported_claims=unsupported,
        unsupported_claim_rate=_ratio(unsupported, claim_denominator, empty=0.0),
        unexpected_claims=unexpected,
    )


def _ranking_metrics(case: GoldenCase, prediction: CasePrediction) -> RankingMetrics:
    gold = set(case.final_relevant_ids)
    ranked = _unique_ordered(prediction.final_ranked_ids)[:50]
    gains = [1.0 if item in gold else 0.0 for item in ranked]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(50, len(gold))))
    return RankingMetrics(
        gold_relevant=len(gold),
        recall_at_50=_ratio(len(set(ranked) & gold), len(gold), empty=1.0),
        ndcg_at_50=_ratio(dcg, ideal, empty=1.0),
    )


def _case_metrics(case: GoldenCase, prediction: CasePrediction) -> CaseMetrics:
    return CaseMetrics(
        case_id=case.case_id,
        domain=case.domain,
        retrieval=_retrieval_metrics(case, prediction),
        screening=_stage_metrics(case.works, prediction.screening, field="screening"),
        screening_wss_at_95=_screening_wss(case, prediction),
        full_text=_stage_metrics(case.works, prediction.full_text, field="full_text"),
        citations=_citation_metrics(case, prediction),
        ranking=_ranking_metrics(case, prediction),
    )


def _aggregate(cases: list[CaseMetrics]) -> AggregateMetrics:
    retrieval_gold = sum(item.retrieval.gold_relevant for item in cases)
    retrieval_present = sum(item.retrieval.present_in_corpus for item in cases)
    retrieval_found = sum(item.retrieval.retrieved for item in cases)
    retrieval_at_50_found = sum(
        round(item.retrieval.recall_at_50 * item.retrieval.present_in_corpus) for item in cases
    )
    screening = _aggregate_stage([item.screening for item in cases])
    full_text = _aggregate_stage([item.full_text for item in cases])
    claims = sum(item.citations.claims for item in cases)
    expected_links = sum(item.citations.expected_links for item in cases)
    predicted_links = sum(item.citations.predicted_links for item in cases)
    correct_links = sum(item.citations.correct_links for item in cases)
    unsupported = sum(item.citations.unsupported_claims for item in cases)
    unexpected = sum(item.citations.unexpected_claims for item in cases)
    ranking_gold = sum(item.ranking.gold_relevant for item in cases)
    return AggregateMetrics(
        cases=len(cases),
        domains=len({item.domain for item in cases}),
        retrieval=RetrievalMetrics(
            gold_relevant=retrieval_gold,
            present_in_corpus=retrieval_present,
            retrieved=retrieval_found,
            corpus_coverage=_ratio(retrieval_present, retrieval_gold, empty=0.0),
            recall=_ratio(retrieval_found, retrieval_present, empty=0.0),
            precision=(
                sum(item.retrieval.precision for item in cases) / len(cases) if cases else 0.0
            ),
            recall_at_50=_ratio(retrieval_at_50_found, retrieval_present, empty=0.0),
            reciprocal_rank=(
                sum(item.retrieval.reciprocal_rank for item in cases) / len(cases) if cases else 0.0
            ),
        ),
        screening=screening,
        screening_wss_at_95=(
            sum(item.screening_wss_at_95 for item in cases) / len(cases) if cases else 0.0
        ),
        full_text=full_text,
        citations=CitationMetrics(
            claims=claims,
            expected_links=expected_links,
            predicted_links=predicted_links,
            correct_links=correct_links,
            precision=_ratio(correct_links, predicted_links, empty=1.0 if not claims else 0.0),
            recall=_ratio(correct_links, expected_links, empty=1.0),
            unsupported_claims=unsupported,
            unsupported_claim_rate=_ratio(
                unsupported,
                max(claims, claims + unexpected),
                empty=0.0,
            ),
            unexpected_claims=unexpected,
        ),
        ranking=RankingMetrics(
            gold_relevant=ranking_gold,
            recall_at_50=(
                sum(item.ranking.recall_at_50 for item in cases) / len(cases) if cases else 0.0
            ),
            ndcg_at_50=(
                sum(item.ranking.ndcg_at_50 for item in cases) / len(cases) if cases else 0.0
            ),
        ),
    )


def _aggregate_stage(items: list[StageMetrics]) -> StageMetrics:
    gold = sum(item.gold for item in items)
    gold_includes = sum(item.gold_includes for item in items)
    gold_excludes = sum(item.gold_excludes for item in items)
    evaluated = sum(item.evaluated for item in items)
    tp = sum(item.true_positive for item in items)
    tn = sum(item.true_negative for item in items)
    fp = sum(item.false_positive for item in items)
    fn = sum(item.false_negative for item in items)
    unresolved = sum(item.unresolved for item in items)
    return StageMetrics(
        gold=gold,
        # Unresolved predictions remain part of the immutable gold inventory.
        # Reconstructing these counts from only the confusion matrix silently
        # discarded every missing or explicitly unsure label.
        gold_includes=gold_includes,
        gold_excludes=gold_excludes,
        evaluated=evaluated,
        true_positive=tp,
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        unresolved=unresolved,
        sensitivity=_ratio(tp, gold_includes, empty=1.0),
        specificity=_ratio(tn, gold_excludes, empty=1.0),
        false_exclusion_rate=_ratio(fn, gold_includes, empty=0.0),
        unresolved_rate=_ratio(unresolved, gold, empty=0.0),
    )


def _gate(name: str, value: float, threshold: float, operator: Literal[">=", "<="]) -> GateResult:
    passed = value >= threshold if operator == ">=" else value <= threshold
    return GateResult(
        name=name,
        value=round(value, 6),
        threshold=threshold,
        operator=operator,
        passed=passed,
    )


def inspect_suite(
    suite: GoldenSuite,
    thresholds: QualityThresholds | None = None,
) -> SuiteInventory:
    """Inspect whether a suite has enough independent evidence for release use."""

    limits = thresholds or QualityThresholds()
    domains: defaultdict[str, int] = defaultdict(int)
    screening_domains: set[str] = set()
    full_text_domains: set[str] = set()
    citation_domains: set[str] = set()
    ranking_domains: set[str] = set()
    works = retrieval_relevant = 0
    screening_includes = screening_excludes = 0
    full_text_includes = full_text_excludes = 0
    citation_claims = citation_links = final_judgements = 0
    for case in suite.cases:
        domains[case.domain] += 1
        works += len(case.works)
        retrieval_relevant += sum(work.retrieval_relevant for work in case.works)
        case_screening = [work.screening for work in case.works if work.screening is not None]
        if case_screening:
            screening_domains.add(case.domain)
        screening_includes += sum(item is GoldVerdict.INCLUDE for item in case_screening)
        screening_excludes += sum(item is GoldVerdict.EXCLUDE for item in case_screening)
        case_full_text = [work.full_text for work in case.works if work.full_text is not None]
        if case_full_text:
            full_text_domains.add(case.domain)
        full_text_includes += sum(item is GoldVerdict.INCLUDE for item in case_full_text)
        full_text_excludes += sum(item is GoldVerdict.EXCLUDE for item in case_full_text)
        if case.claims:
            citation_domains.add(case.domain)
        citation_claims += len(case.claims)
        citation_links += sum(len(claim.supported_by) for claim in case.claims)
        if case.final_relevant_ids:
            ranking_domains.add(case.domain)
        final_judgements += len(case.final_relevant_ids)
    source_kinds: list[str] = sorted({source.kind for source in suite.sources})
    external_sources = [source for source in suite.sources if source.kind == "external"]
    external_verified = bool(external_sources) and all(
        source.license_verified for source in external_sources
    )
    checks = [
        _gate(
            "minimum release cases",
            float(len(suite.cases)),
            limits.min_release_cases,
            ">=",
        ),
        _gate(
            "minimum release domains",
            float(len(domains)),
            limits.min_release_domains,
            ">=",
        ),
        _gate(
            "minimum cases per domain",
            float(min(domains.values(), default=0)),
            limits.min_cases_per_domain,
            ">=",
        ),
        _gate(
            "minimum screening labels",
            float(screening_includes + screening_excludes),
            limits.min_screening_gold,
            ">=",
        ),
        _gate(
            "minimum screening includes",
            float(screening_includes),
            limits.min_screening_includes,
            ">=",
        ),
        _gate(
            "minimum screening excludes",
            float(screening_excludes),
            limits.min_screening_excludes,
            ">=",
        ),
        _gate(
            "minimum screening domains",
            float(len(screening_domains)),
            limits.min_screening_domains,
            ">=",
        ),
        _gate(
            "minimum full-text labels",
            float(full_text_includes + full_text_excludes),
            limits.min_full_text_gold,
            ">=",
        ),
        _gate(
            "minimum full-text includes",
            float(full_text_includes),
            limits.min_full_text_includes,
            ">=",
        ),
        _gate(
            "minimum full-text excludes",
            float(full_text_excludes),
            limits.min_full_text_excludes,
            ">=",
        ),
        _gate(
            "minimum full-text domains",
            float(len(full_text_domains)),
            limits.min_full_text_domains,
            ">=",
        ),
        _gate(
            "minimum citation claims",
            float(citation_claims),
            limits.min_citation_claims,
            ">=",
        ),
        _gate(
            "minimum citation domains",
            float(len(citation_domains)),
            limits.min_citation_domains,
            ">=",
        ),
        _gate(
            "minimum final relevance judgements",
            float(final_judgements),
            limits.min_final_relevance_judgements,
            ">=",
        ),
        _gate(
            "minimum ranking domains",
            float(len(ranking_domains)),
            limits.min_ranking_domains,
            ">=",
        ),
    ]
    release_ready = (
        external_verified
        and "test_fixture" not in source_kinds
        and all(gate.passed for gate in checks)
    )
    return SuiteInventory(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_digest=suite.digest,
        sources=len(suite.sources),
        source_kinds=source_kinds,
        external_licenses_verified=external_verified,
        cases=len(suite.cases),
        domains=dict(sorted(domains.items())),
        works=works,
        retrieval_relevant=retrieval_relevant,
        screening_labels=screening_includes + screening_excludes,
        screening_includes=screening_includes,
        screening_excludes=screening_excludes,
        screening_domains=len(screening_domains),
        full_text_labels=full_text_includes + full_text_excludes,
        full_text_includes=full_text_includes,
        full_text_excludes=full_text_excludes,
        full_text_domains=len(full_text_domains),
        citation_claims=citation_claims,
        citation_links=citation_links,
        citation_domains=len(citation_domains),
        final_relevance_judgements=final_judgements,
        ranking_domains=len(ranking_domains),
        gates=checks,
        release_ready=release_ready,
    )


def render_suite_inventory(inventory: SuiteInventory) -> str:
    """Render a compact preflight summary for benchmark operators."""

    lines = [
        f"suite preflight: {'PASS' if inventory.release_ready else 'FAIL'}",
        f"suite: {inventory.suite_id}@{inventory.suite_version}",
        f"digest: {inventory.suite_digest}",
        f"sources: {inventory.sources} ({', '.join(inventory.source_kinds)})",
        f"cases/domains: {inventory.cases}/{len(inventory.domains)}",
        "domain cases: "
        + ", ".join(f"{name}={count}" for name, count in inventory.domains.items()),
        (
            "labels: "
            f"screening={inventory.screening_labels} "
            f"({inventory.screening_includes} include/{inventory.screening_excludes} exclude), "
            f"full-text={inventory.full_text_labels} "
            f"({inventory.full_text_includes} include/{inventory.full_text_excludes} exclude), "
            f"claims={inventory.citation_claims}, final={inventory.final_relevance_judgements}"
        ),
        "",
    ]
    lines.extend(
        f"{'PASS' if gate.passed else 'FAIL'}  {gate.name}: "
        f"{gate.value:.0f} {gate.operator} {gate.threshold:.0f}"
        for gate in inventory.gates
    )
    if not inventory.external_licenses_verified:
        lines.append("FAIL  external benchmark provenance or license verification")
    if "test_fixture" in inventory.source_kinds:
        lines.append("FAIL  test fixtures are not production evidence")
    return "\n".join(lines)


def current_git_revision() -> str:
    """Return the checked-out revision recorded in release evidence."""

    configured = os.environ.get("SIX_RELEASE_GIT_REVISION", "").strip().lower()
    if len(configured) == 40 and all(character in "0123456789abcdef" for character in configured):
        return configured
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def evaluate_quality(
    suite: GoldenSuite,
    run: PredictionRun,
    thresholds: QualityThresholds | None = None,
) -> QualityReport:
    """Evaluate one immutable prediction run and apply production gates."""

    if run.suite_id != suite.suite_id or run.suite_version != suite.version:
        raise ValueError("prediction run targets a different golden-suite version")
    if run.suite_digest != suite.digest:
        raise ValueError("prediction run targets different golden-suite content")
    predictions = {item.case_id: item for item in run.predictions}
    missing = [case.case_id for case in suite.cases if case.case_id not in predictions]
    extra = sorted(set(predictions) - {case.case_id for case in suite.cases})
    if missing or extra:
        raise ValueError(f"prediction coverage mismatch: missing={missing}, extra={extra}")
    cases = [_case_metrics(case, predictions[case.case_id]) for case in suite.cases]
    grouped: defaultdict[str, list[CaseMetrics]] = defaultdict(list)
    for case in cases:
        grouped[case.domain].append(case)
    by_domain = {domain: _aggregate(items) for domain, items in sorted(grouped.items())}
    overall = _aggregate(cases)
    limits = thresholds or QualityThresholds()
    gates = [
        _gate(
            "minimum release cases",
            float(overall.cases),
            float(limits.min_release_cases),
            ">=",
        ),
        _gate(
            "minimum release domains",
            float(overall.domains),
            float(limits.min_release_domains),
            ">=",
        ),
        _gate(
            "minimum cases per domain",
            float(min((item.cases for item in by_domain.values()), default=0)),
            float(limits.min_cases_per_domain),
            ">=",
        ),
        _gate(
            "minimum screening labels",
            float(overall.screening.gold),
            float(limits.min_screening_gold),
            ">=",
        ),
        _gate(
            "minimum screening includes",
            float(overall.screening.gold_includes),
            float(limits.min_screening_includes),
            ">=",
        ),
        _gate(
            "minimum screening excludes",
            float(overall.screening.gold_excludes),
            float(limits.min_screening_excludes),
            ">=",
        ),
        _gate(
            "minimum screening domains",
            float(sum(item.screening.gold > 0 for item in by_domain.values())),
            float(limits.min_screening_domains),
            ">=",
        ),
        _gate(
            "minimum full-text labels",
            float(overall.full_text.gold),
            float(limits.min_full_text_gold),
            ">=",
        ),
        _gate(
            "minimum full-text includes",
            float(overall.full_text.gold_includes),
            float(limits.min_full_text_includes),
            ">=",
        ),
        _gate(
            "minimum full-text excludes",
            float(overall.full_text.gold_excludes),
            float(limits.min_full_text_excludes),
            ">=",
        ),
        _gate(
            "minimum full-text domains",
            float(sum(item.full_text.gold > 0 for item in by_domain.values())),
            float(limits.min_full_text_domains),
            ">=",
        ),
        _gate(
            "minimum citation claims",
            float(overall.citations.claims),
            float(limits.min_citation_claims),
            ">=",
        ),
        _gate(
            "minimum citation domains",
            float(sum(item.citations.claims > 0 for item in by_domain.values())),
            float(limits.min_citation_domains),
            ">=",
        ),
        _gate(
            "minimum final relevance judgements",
            float(overall.ranking.gold_relevant),
            float(limits.min_final_relevance_judgements),
            ">=",
        ),
        _gate(
            "minimum ranking domains",
            float(sum(item.ranking.gold_relevant > 0 for item in by_domain.values())),
            float(limits.min_ranking_domains),
            ">=",
        ),
        _gate(
            "corpus coverage",
            overall.retrieval.corpus_coverage,
            limits.min_corpus_coverage,
            ">=",
        ),
        _gate(
            "retrieval recall",
            overall.retrieval.recall,
            limits.min_retrieval_recall,
            ">=",
        ),
        _gate(
            "retrieval recall@50",
            overall.retrieval.recall_at_50,
            limits.min_retrieval_recall_at_50,
            ">=",
        ),
        _gate(
            "screening sensitivity",
            overall.screening.sensitivity,
            limits.min_screening_sensitivity,
            ">=",
        ),
        _gate(
            "screening false exclusion",
            overall.screening.false_exclusion_rate,
            limits.max_screening_false_exclusion_rate,
            "<=",
        ),
        _gate(
            "screening unresolved",
            overall.screening.unresolved_rate,
            limits.max_screening_unresolved_rate,
            "<=",
        ),
        _gate(
            "full-text sensitivity",
            overall.full_text.sensitivity,
            limits.min_full_text_sensitivity,
            ">=",
        ),
        _gate(
            "full-text false exclusion",
            overall.full_text.false_exclusion_rate,
            limits.max_full_text_false_exclusion_rate,
            "<=",
        ),
        _gate(
            "full-text unresolved",
            overall.full_text.unresolved_rate,
            limits.max_full_text_unresolved_rate,
            "<=",
        ),
        _gate(
            "citation precision",
            overall.citations.precision,
            limits.min_citation_precision,
            ">=",
        ),
        _gate(
            "citation recall",
            overall.citations.recall,
            limits.min_citation_recall,
            ">=",
        ),
        _gate(
            "unsupported claims",
            overall.citations.unsupported_claim_rate,
            limits.max_unsupported_claim_rate,
            "<=",
        ),
        _gate(
            "final nDCG@50",
            overall.ranking.ndcg_at_50,
            limits.min_final_ndcg_at_50,
            ">=",
        ),
    ]
    retrieval_domains = [
        metrics.retrieval.recall
        for metrics in by_domain.values()
        if metrics.retrieval.gold_relevant
    ]
    ranking_domains = [
        metrics.ranking.ndcg_at_50
        for metrics in by_domain.values()
        if metrics.ranking.gold_relevant
    ]
    screening_sensitivity_domains = [
        metrics.screening.sensitivity
        for metrics in by_domain.values()
        if metrics.screening.gold_includes
    ]
    screening_specificity_domains = [
        metrics.screening.specificity
        for metrics in by_domain.values()
        if metrics.screening.gold_excludes
    ]
    full_text_sensitivity_domains = [
        metrics.full_text.sensitivity
        for metrics in by_domain.values()
        if metrics.full_text.gold_includes
    ]
    full_text_specificity_domains = [
        metrics.full_text.specificity
        for metrics in by_domain.values()
        if metrics.full_text.gold_excludes
    ]
    citation_precision_domains = [
        metrics.citations.precision for metrics in by_domain.values() if metrics.citations.claims
    ]
    citation_recall_domains = [
        metrics.citations.recall for metrics in by_domain.values() if metrics.citations.claims
    ]
    gates.extend(
        [
            _gate(
                "worst-domain retrieval recall",
                min(retrieval_domains, default=0.0),
                limits.min_domain_retrieval_recall,
                ">=",
            ),
            _gate(
                "worst-domain screening sensitivity",
                min(screening_sensitivity_domains, default=0.0),
                limits.min_domain_screening_sensitivity,
                ">=",
            ),
            _gate(
                "worst-domain screening specificity",
                min(screening_specificity_domains, default=0.0),
                limits.min_domain_screening_specificity,
                ">=",
            ),
            _gate(
                "worst-domain full-text sensitivity",
                min(full_text_sensitivity_domains, default=0.0),
                limits.min_domain_full_text_sensitivity,
                ">=",
            ),
            _gate(
                "worst-domain full-text specificity",
                min(full_text_specificity_domains, default=0.0),
                limits.min_domain_full_text_specificity,
                ">=",
            ),
            _gate(
                "worst-domain citation precision",
                min(citation_precision_domains, default=0.0),
                limits.min_domain_citation_precision,
                ">=",
            ),
            _gate(
                "worst-domain citation recall",
                min(citation_recall_domains, default=0.0),
                limits.min_domain_citation_recall,
                ">=",
            ),
            _gate(
                "worst-domain final nDCG@50",
                min(ranking_domains, default=0.0),
                limits.min_domain_final_ndcg_at_50,
                ">=",
            ),
        ]
    )
    source_kinds: list[str] = sorted({source.kind for source in suite.sources})
    external_sources = [source for source in suite.sources if source.kind == "external"]
    release_eligible = (
        bool(external_sources)
        and all(source.license_verified for source in external_sources)
        and "test_fixture" not in source_kinds
    )
    return QualityReport(
        generated_at=datetime.now(UTC),
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_digest=suite.digest,
        prediction_digest=run.digest,
        source_kinds=source_kinds,
        corpus_version=run.corpus_version,
        corpus_sha256=run.corpus_sha256,
        corpus_build_digest=run.corpus_build_digest,
        config_digest=run.config_digest,
        seed=run.seed,
        git_revision=current_git_revision(),
        prediction_git_revision=run.git_revision,
        prediction_runner=run.generated_by,
        thresholds=limits,
        cases=cases,
        by_domain=by_domain,
        overall=overall,
        gates=gates,
        release_eligible=release_eligible,
        passed=release_eligible and all(gate.passed for gate in gates),
    )


def render_quality_report(report: QualityReport) -> str:
    status = "PASS" if report.passed else "FAIL"
    lines = [
        f"quality gate: {status}",
        f"suite: {report.suite_id}@{report.suite_version} ({', '.join(report.source_kinds)})",
        f"corpus: {report.corpus_version}",
        f"cases/domains: {report.overall.cases}/{report.overall.domains}",
        "",
    ]
    lines.extend(
        f"{'PASS' if gate.passed else 'FAIL'}  {gate.name}: "
        f"{gate.value:.3f} {gate.operator} {gate.threshold:.3f}"
        for gate in report.gates
    )
    if not report.release_eligible:
        lines.append(
            "FAIL  release evidence: production needs a license-verified external source "
            "and cannot contain test fixtures"
        )
    return "\n".join(lines)


def compare_quality_reports(
    baseline: QualityReport,
    candidate: QualityReport,
    thresholds: QualityRegressionThresholds | None = None,
) -> QualityRegressionReport:
    """Fail a release when it regresses beyond bounded quality tolerances.

    Comparisons are only meaningful on exactly the same immutable golden
    suite. A benchmark change therefore starts a separately reviewed baseline
    instead of making an apparent model improvement from different examples.
    """

    if (
        baseline.suite_id != candidate.suite_id
        or baseline.suite_version != candidate.suite_version
        or baseline.suite_digest != candidate.suite_digest
    ):
        raise ValueError("quality regression reports must use the same golden suite")
    limits = thresholds or QualityRegressionThresholds()

    def drop_gate(name: str, old: float, new: float, maximum: float) -> GateResult:
        return _gate(name, max(0.0, old - new), maximum, "<=")

    gates = [
        drop_gate(
            "corpus coverage regression",
            baseline.overall.retrieval.corpus_coverage,
            candidate.overall.retrieval.corpus_coverage,
            limits.max_corpus_coverage_drop,
        ),
        drop_gate(
            "retrieval recall regression",
            baseline.overall.retrieval.recall,
            candidate.overall.retrieval.recall,
            limits.max_retrieval_recall_drop,
        ),
        drop_gate(
            "screening sensitivity regression",
            baseline.overall.screening.sensitivity,
            candidate.overall.screening.sensitivity,
            limits.max_screening_sensitivity_drop,
        ),
        drop_gate(
            "screening specificity regression",
            baseline.overall.screening.specificity,
            candidate.overall.screening.specificity,
            limits.max_screening_specificity_drop,
        ),
        drop_gate(
            "full-text sensitivity regression",
            baseline.overall.full_text.sensitivity,
            candidate.overall.full_text.sensitivity,
            limits.max_full_text_sensitivity_drop,
        ),
        drop_gate(
            "full-text specificity regression",
            baseline.overall.full_text.specificity,
            candidate.overall.full_text.specificity,
            limits.max_full_text_specificity_drop,
        ),
        drop_gate(
            "citation precision regression",
            baseline.overall.citations.precision,
            candidate.overall.citations.precision,
            limits.max_citation_precision_drop,
        ),
        drop_gate(
            "citation recall regression",
            baseline.overall.citations.recall,
            candidate.overall.citations.recall,
            limits.max_citation_recall_drop,
        ),
        drop_gate(
            "final nDCG@50 regression",
            baseline.overall.ranking.ndcg_at_50,
            candidate.overall.ranking.ndcg_at_50,
            limits.max_final_ndcg_at_50_drop,
        ),
    ]
    for domain, old in baseline.by_domain.items():
        new = candidate.by_domain.get(domain)
        if new is None:
            raise ValueError(f"candidate report is missing baseline domain {domain!r}")
        gates.extend(
            [
                drop_gate(
                    f"{domain} retrieval recall regression",
                    old.retrieval.recall,
                    new.retrieval.recall,
                    limits.max_domain_retrieval_recall_drop,
                ),
                drop_gate(
                    f"{domain} screening sensitivity regression",
                    old.screening.sensitivity,
                    new.screening.sensitivity,
                    limits.max_domain_screening_sensitivity_drop,
                ),
                drop_gate(
                    f"{domain} full-text sensitivity regression",
                    old.full_text.sensitivity,
                    new.full_text.sensitivity,
                    limits.max_domain_full_text_sensitivity_drop,
                ),
                drop_gate(
                    f"{domain} citation precision regression",
                    old.citations.precision,
                    new.citations.precision,
                    limits.max_domain_citation_precision_drop,
                ),
                drop_gate(
                    f"{domain} citation recall regression",
                    old.citations.recall,
                    new.citations.recall,
                    limits.max_domain_citation_recall_drop,
                ),
                drop_gate(
                    f"{domain} final nDCG@50 regression",
                    old.ranking.ndcg_at_50,
                    new.ranking.ndcg_at_50,
                    limits.max_domain_final_ndcg_at_50_drop,
                ),
            ]
        )
    return QualityRegressionReport(
        generated_at=datetime.now(UTC),
        suite_id=candidate.suite_id,
        suite_version=candidate.suite_version,
        suite_digest=candidate.suite_digest,
        baseline_report_digest=baseline.digest,
        candidate_report_digest=candidate.digest,
        thresholds=limits,
        gates=gates,
        passed=all(gate.passed for gate in gates),
    )


def write_regression_report(path: Path, report: QualityRegressionReport) -> None:
    """Write one immutable quality-regression evidence file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def render_regression_report(report: QualityRegressionReport) -> str:
    """Render a compact operator-facing regression summary."""

    lines = [
        f"quality regression gate: {'PASS' if report.passed else 'FAIL'}",
        f"suite: {report.suite_id}@{report.suite_version}",
        "",
    ]
    lines.extend(
        f"{'PASS' if gate.passed else 'FAIL'}  {gate.name}: "
        f"{gate.value:.3f} {gate.operator} {gate.threshold:.3f}"
        for gate in report.gates
    )
    return "\n".join(lines)
