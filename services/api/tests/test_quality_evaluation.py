"""Release-quality evaluation covers every evidence-synthesis stage."""

from datetime import UTC, datetime
from typing import Literal

import pytest

from sixsentences_server.evals.quality import (
    CasePrediction,
    ClaimPrediction,
    GoldClaim,
    GoldenCase,
    GoldenSuite,
    GoldVerdict,
    GoldWork,
    PredictedVerdict,
    PredictionRun,
    QualityThresholds,
    SuiteSource,
    compare_quality_reports,
    evaluate_quality,
    inspect_suite,
    render_quality_report,
    render_suite_inventory,
    sha256_json,
)

HASH = "a" * 64


def _case(number: int, domain: str) -> GoldenCase:
    prefix = f"D{number}"
    return GoldenCase(
        case_id=f"case-{number}",
        source_name="published-benchmark",
        domain=domain,
        question=f"Question {number}",
        query=f'"topic {number}"',
        works=[
            GoldWork(
                work_id=f"{prefix}-I1",
                retrieval_relevant=True,
                screening=GoldVerdict.INCLUDE,
                full_text=GoldVerdict.INCLUDE,
            ),
            GoldWork(
                work_id=f"{prefix}-I2",
                retrieval_relevant=True,
                screening=GoldVerdict.INCLUDE,
                full_text=GoldVerdict.INCLUDE,
            ),
            GoldWork(
                work_id=f"{prefix}-E1",
                retrieval_relevant=False,
                screening=GoldVerdict.EXCLUDE,
                full_text=GoldVerdict.EXCLUDE,
            ),
            GoldWork(
                work_id=f"{prefix}-E2",
                retrieval_relevant=False,
                screening=GoldVerdict.EXCLUDE,
                full_text=GoldVerdict.EXCLUDE,
            ),
        ],
        final_relevant_ids=[f"{prefix}-I1", f"{prefix}-I2"],
        claims=[
            GoldClaim(
                claim_id=f"claim-{number}",
                text=f"The primary finding for review case {number} is supported.",
                supported_by=[f"{prefix}-I1"],
            )
        ],
    )


def _suite(
    kind: Literal["external", "internal_adjudicated", "test_fixture"] = "external",
) -> GoldenSuite:
    return GoldenSuite(
        suite_id="release-suite",
        version="2026.08.1",
        title="Cross-domain release suite",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        sources=[
            SuiteSource(
                kind=kind,
                name="published-benchmark",
                version="1",
                uri="https://example.invalid/immutable-evidence",
                license="CC0-1.0",
                license_verified=True,
                sha256=HASH,
            )
        ],
        cases=[_case(1, "computer science"), _case(2, "medicine"), _case(3, "social science")],
    )


def _prediction(case: GoldenCase) -> CasePrediction:
    include_ids = [work.work_id for work in case.works if work.retrieval_relevant]
    exclude_ids = [work.work_id for work in case.works if not work.retrieval_relevant]
    return CasePrediction(
        case_id=case.case_id,
        corpus_work_ids=[work.work_id for work in case.works],
        retrieved_ids=[*include_ids, *exclude_ids],
        screening={
            **{item: PredictedVerdict.INCLUDE for item in include_ids},
            **{item: PredictedVerdict.EXCLUDE for item in exclude_ids},
        },
        full_text={
            **{item: PredictedVerdict.INCLUDE for item in include_ids},
            **{item: PredictedVerdict.EXCLUDE for item in exclude_ids},
        },
        final_ranked_ids=[*include_ids, *exclude_ids],
        claims=[ClaimPrediction(claim_id=case.claims[0].claim_id, cited_work_ids=[include_ids[0]])],
    )


def _run(suite: GoldenSuite) -> PredictionRun:
    return PredictionRun(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_digest=suite.digest,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        generated_by="sixsentences_server.release-eval",
        git_revision="a" * 40,
        corpus_version="production-2026-08-01",
        corpus_sha256="b" * 64,
        corpus_build_digest="c" * 64,
        config_digest=sha256_json({"route": "release"}),
        predictions=[_prediction(case) for case in suite.cases],
    )


def _unit_thresholds(**overrides: float | int) -> QualityThresholds:
    values: dict[str, float | int] = {
        "min_release_cases": 3,
        "min_cases_per_domain": 1,
        "min_screening_gold": 12,
        "min_screening_includes": 6,
        "min_screening_excludes": 6,
        "min_full_text_gold": 12,
        "min_full_text_includes": 6,
        "min_full_text_excludes": 6,
        "min_citation_claims": 3,
        "min_final_relevance_judgements": 6,
    }
    values.update(overrides)
    return QualityThresholds(**values)  # type: ignore[arg-type]


def test_complete_external_suite_passes_all_release_gates() -> None:
    suite = _suite()
    report = evaluate_quality(suite, _run(suite), _unit_thresholds())

    assert report.passed is True
    assert report.release_eligible is True
    assert report.overall.domains == 3
    assert report.overall.retrieval.recall == 1.0
    assert report.overall.screening.sensitivity == 1.0
    assert report.overall.full_text.specificity == 1.0
    assert report.overall.citations.unsupported_claim_rate == 0.0
    assert report.overall.ranking.ndcg_at_50 == 1.0
    assert set(report.by_domain) == {"computer science", "medicine", "social science"}
    assert "quality gate: PASS" in render_quality_report(report)


def test_suite_preflight_reports_stage_balance_and_domain_coverage() -> None:
    inventory = inspect_suite(_suite(), _unit_thresholds())

    assert inventory.release_ready is True
    assert inventory.screening_includes == 6
    assert inventory.screening_excludes == 6
    assert inventory.full_text_domains == 3
    assert inventory.citation_domains == 3
    assert "suite preflight: PASS" in render_suite_inventory(inventory)


def test_false_exclusion_missing_source_and_bad_rank_fail_release() -> None:
    suite = _suite()
    run = _run(suite)
    first = run.predictions[0]
    first.corpus_work_ids = ["D1-I1", "D1-E1", "D1-E2"]
    first.retrieved_ids = ["D1-E1", "D1-E2", "D1-I1"]
    first.screening["D1-I1"] = PredictedVerdict.EXCLUDE
    first.screening["D1-I2"] = PredictedVerdict.UNSURE
    first.full_text["D1-I1"] = PredictedVerdict.EXCLUDE
    first.claims = [ClaimPrediction(claim_id="claim-1", cited_work_ids=["D1-E1"])]
    first.final_ranked_ids = ["D1-E1", "D1-E2"]

    report = evaluate_quality(suite, run, _unit_thresholds())

    assert report.passed is False
    failed = {gate.name for gate in report.gates if not gate.passed}
    assert "screening sensitivity" in failed
    assert "screening false exclusion" in failed
    assert "full-text false exclusion" in failed
    assert "citation precision" in failed
    assert "unsupported claims" in failed
    assert "final nDCG@50" in failed


def test_unresolved_predictions_preserve_the_gold_inventory() -> None:
    suite = _suite()
    run = _run(suite)
    for prediction in run.predictions:
        prediction.screening = {
            work_id: PredictedVerdict.UNSURE for work_id in prediction.screening
        }
        prediction.full_text = {
            work_id: PredictedVerdict.UNSURE for work_id in prediction.full_text
        }

    report = evaluate_quality(suite, run, _unit_thresholds())

    assert report.overall.screening.gold_includes == 6
    assert report.overall.screening.gold_excludes == 6
    assert report.overall.screening.unresolved == 12
    assert report.overall.full_text.gold_includes == 6
    assert report.overall.full_text.gold_excludes == 6
    assert report.overall.full_text.unresolved == 12


def test_missing_prediction_case_is_rejected_instead_of_scored_as_zero() -> None:
    suite = _suite()
    run = _run(suite)
    run.predictions.pop()

    with pytest.raises(ValueError, match="coverage mismatch"):
        evaluate_quality(suite, run)


def test_prediction_for_wrong_suite_version_is_rejected() -> None:
    suite = _suite()
    run = _run(suite)
    run.suite_version = "other"

    with pytest.raises(ValueError, match="different golden-suite version"):
        evaluate_quality(suite, run)


def test_duplicate_ranked_outputs_are_rejected_before_scoring() -> None:
    suite = _suite()
    payload = _run(suite).model_dump(mode="json")
    payload["predictions"][0]["retrieved_ids"].append("D1-I1")

    with pytest.raises(ValueError, match="duplicate work ids"):
        PredictionRun.model_validate(payload)


def test_test_fixture_can_never_approve_production() -> None:
    suite = _suite("test_fixture")
    report = evaluate_quality(suite, _run(suite), _unit_thresholds())

    assert all(gate.passed for gate in report.gates)
    assert report.release_eligible is False
    assert report.passed is False
    assert "cannot contain test fixtures" in render_quality_report(report)


def test_minimum_domain_gate_is_configurable_but_defaults_to_three() -> None:
    suite = _suite()
    suite.cases = suite.cases[:2]
    run = _run(suite)

    report = evaluate_quality(suite, run, _unit_thresholds())
    assert report.passed is False
    domain_gate = next(gate for gate in report.gates if gate.name == "minimum release domains")
    assert domain_gate.passed is False

    relaxed = evaluate_quality(
        suite,
        run,
        _unit_thresholds(
            min_release_cases=2,
            min_release_domains=2,
            min_screening_domains=2,
            min_full_text_domains=2,
            min_citation_domains=2,
            min_ranking_domains=2,
            min_screening_gold=8,
            min_screening_includes=4,
            min_screening_excludes=4,
            min_full_text_gold=8,
            min_full_text_includes=4,
            min_full_text_excludes=4,
            min_citation_claims=2,
            min_final_relevance_judgements=4,
        ),
    )
    assert relaxed.passed is True


def test_unexpected_claims_count_as_unsupported_instead_of_being_ignored() -> None:
    suite = _suite()
    run = _run(suite)
    run.predictions[0].claims.append(
        ClaimPrediction(claim_id="invented-claim", cited_work_ids=["D1-E1"])
    )

    report = evaluate_quality(suite, run, _unit_thresholds())

    assert report.passed is False
    assert report.overall.citations.unexpected_claims == 1
    assert report.overall.citations.unsupported_claims == 1


def test_suite_digest_does_not_change_with_build_timestamp() -> None:
    first = _suite()
    second = _suite()
    second.created_at = datetime(2026, 8, 2, tzinfo=UTC)

    assert first.digest == second.digest


def test_unverified_external_license_cannot_approve_production() -> None:
    suite = _suite()
    suite.sources[0].license_verified = False

    report = evaluate_quality(suite, _run(suite), _unit_thresholds())

    assert report.release_eligible is False
    assert report.passed is False


def test_regression_gate_detects_quality_loss_after_absolute_pass() -> None:
    suite = _suite()
    baseline = evaluate_quality(suite, _run(suite), _unit_thresholds())
    candidate = baseline.model_copy(deep=True)
    candidate.overall.screening.sensitivity = 0.98

    regression = compare_quality_reports(baseline, candidate)

    assert regression.passed is False
    failed = {gate.name for gate in regression.gates if not gate.passed}
    assert "screening sensitivity regression" in failed


def test_regression_comparison_requires_the_exact_same_suite() -> None:
    suite = _suite()
    baseline = evaluate_quality(suite, _run(suite), _unit_thresholds())
    candidate = baseline.model_copy(deep=True)
    candidate.suite_digest = "f" * 64

    with pytest.raises(ValueError, match="same golden suite"):
        compare_quality_reports(baseline, candidate)
