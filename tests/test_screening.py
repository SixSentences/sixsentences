"""Screening utilities expose uncertainty and require external independence evidence."""

import pytest

from sixsentences.screening.calibration import SeedLabel, calibrate_against_seeds
from sixsentences.screening.overlap import assess_include_vote_overlap
from sixsentences.screening.quote import verify_quote


def test_quote_verification_accepts_verbatim_but_not_paraphrase() -> None:
    source = "The intervention improved recall by twelve percentage points."
    assert verify_quote("intervention improved recall", source)[1] == "exact"
    assert verify_quote("the method was substantially better", source) == (None, "unverified")


def test_quote_verification_rejects_invented_or_omitted_negation() -> None:
    positive = "The intervention improved recall across all evaluated cohorts."
    negative = "The intervention did not improve recall across evaluated cohorts."

    assert verify_quote("not The intervention improved recall", positive) == (
        None,
        "unverified",
    )
    assert verify_quote("improve recall across evaluated cohorts", negative) == (
        None,
        "unverified",
    )
    adverbial_negative = (
        "The intervention did not significantly improve survival among adults "
        "during the controlled evaluation."
    )
    assert verify_quote(
        "improve survival among adults during the controlled evaluation",
        adverbial_negative,
    ) == (None, "unverified")


@pytest.mark.parametrize(
    "negation",
    [
        "didn't",
        "didn’t",
        "didn‘t",
        "didnʼt",
        "didn＇t",
        "didn′t",
        "didn‛t",
        "didn‚t",
        "didn`t",
        "didn´t",
        "cannot",
        "None",
        "Nothing",
    ],
)
def test_quote_verification_rejects_omitted_contracted_negation(negation: str) -> None:
    source = f"The treatment {negation} improve survival in adults."

    assert verify_quote("improve survival in adults.", source) == (None, "unverified")


def test_quote_fuzzy_matching_does_not_accept_changed_numbers() -> None:
    source = "The intervention improved recall by twelve points in 2025."
    assert verify_quote("improved recall by twelve points in 2024", source) == (
        None,
        "unverified",
    )


@pytest.mark.parametrize(
    ("source", "quote"),
    [
        ("The observed effect was +5 percent.", "The observed effect was -5 percent"),
        ("The estimate was <5 units.", "The estimate was >5 units"),
        ("The estimate was ≤5 units.", "The estimate was ≥5 units"),
        ("The computation was 5 - 3 units.", "The computation was 5 + 3 units"),
        ("The implementation used C++ in production.", "The implementation used C-- in production"),
        ("-5 percent was observed in the trial.", "+5 percent was observed in the trial"),
        (
            "The response reached 50% in the treated group.",
            "The response reached 50 in the treated group",
        ),
        (
            "The intervention cost $500 per participant.",
            "The intervention cost 500 per participant",
        ),
        (
            "The observed event ratio was 5/10 in treatment.",
            "The observed event ratio was 5*10 in treatment",
        ),
        ("The dose was 5 mg/kg in treatment.", "The dose was 5 mg*kg in treatment"),
        (
            "The formula was 2×(3+4) in the analysis.",
            "The formula was 2×3+4 in the analysis",
        ),
        (
            "The normalized value was √4 in the analysis.",
            "The normalized value was 4 in the analysis",
        ),
        (
            "The interval was 5–10 units in the analysis.",
            "The interval was 5 10 units in the analysis",
        ),
        (
            "The measured height was 5′10″ in the cohort.",
            "The measured height was 5 10 in the cohort",
        ),
        ("The transformed value was |x| in the model.", "The transformed value was x in the model"),
        (
            "The encoded operator was '+' in the record.",
            "The encoded operator was semantic_plus in the record",
        ),
        ("The response reached 50%.", "The response reached 50"),
        ("The response reached 50 %.", "The response reached 50"),
        ("The intervention cost $ 500 per participant.", "500 per participant"),
        ("The normalized value was √4 in the analysis.", "4 in the analysis"),
        ("The estimate was 5.0 units in the analysis.", "The estimate was 5"),
    ],
)
def test_quote_matching_preserves_all_internal_symbols(source: str, quote: str) -> None:
    assert verify_quote(quote, source) == (None, "unverified")


def test_quote_matching_accepts_the_same_semantic_units_and_operators() -> None:
    source = "The response reached 50% at a cost of $500 and a ratio of 5/10."
    quote = "response reached 50% at a cost of $500 and a ratio of 5/10."

    assert verify_quote(quote, source) == (quote, "exact")


def test_seed_calibration_counts_unsure_as_recall_safe() -> None:
    seeds = [SeedLabel(f"W{index}", included=index < 6) for index in range(15)]
    verdicts = {seed.work_id: "exclude" for seed in seeds}
    for index in range(5):
        verdicts[f"W{index}"] = "include"
    verdicts["W5"] = "unsure"

    report = calibrate_against_seeds(verdicts, seeds, min_seeds=10)
    assert report.recall == 1.0
    assert report.committed == 14
    assert report.verdict == "meets_target"


def test_seed_calibration_without_positive_cases_is_inconclusive() -> None:
    seeds = [SeedLabel(f"W{index}", included=False) for index in range(10)]
    report = calibrate_against_seeds(
        {seed.work_id: "exclude" for seed in seeds},
        seeds,
    )
    assert report.recall is None
    assert report.agreement is None
    assert report.verdict == "insufficient_evidence"


def test_seed_calibration_requires_valid_unique_inputs_and_committed_overlap() -> None:
    seeds = [SeedLabel(f"W{index}", included=index == 0) for index in range(10)]
    mostly_unsure = {seed.work_id: "unsure" for seed in seeds}
    mostly_unsure["W0"] = "include"
    assert calibrate_against_seeds(mostly_unsure, seeds).verdict == "insufficient_evidence"

    with pytest.raises(ValueError, match="unknown"):
        calibrate_against_seeds({"W0": "maybe"}, seeds)
    with pytest.raises(ValueError, match="unique"):
        calibrate_against_seeds({}, [SeedLabel("W1", True), SeedLabel("W1", False)])
    with pytest.raises(ValueError, match="target_recall"):
        calibrate_against_seeds({}, seeds, target_recall=0.0)
    with pytest.raises(ValueError, match="min_seeds"):
        calibrate_against_seeds({}, seeds, min_seeds=0)
    with pytest.raises(ValueError, match="min_positive_seeds"):
        calibrate_against_seeds({}, seeds, min_positive_seeds=0)


def test_seed_calibration_requires_a_committed_positive_reference_case() -> None:
    seeds = [SeedLabel(f"W{index}", included=index < 2) for index in range(20)]
    verdicts = {seed.work_id: "exclude" for seed in seeds}
    verdicts["W0"] = "unsure"
    verdicts["W1"] = "unsure"
    report = calibrate_against_seeds(verdicts, seeds)
    assert report.verdict == "insufficient_evidence"
    assert "human-positive" in report.note


def test_one_positive_seed_cannot_meet_the_target() -> None:
    seeds = [SeedLabel(f"W{index}", included=index == 0) for index in range(10)]
    verdicts = {seed.work_id: "exclude" for seed in seeds}
    verdicts["W0"] = "include"

    report = calibrate_against_seeds(verdicts, seeds)

    assert report.recall == 1.0
    assert report.verdict == "insufficient_evidence"
    assert report.agreement is not None


def test_model_overlap_cannot_self_establish_independence() -> None:
    votes = {f"W{index}": 2 for index in range(20)}
    report = assess_include_vote_overlap(
        votes,
        reviewers=2,
        screened=200,
        target_capture_coverage=0.95,
    )
    assert report.proxy_target_met is False
    assert "independence" in report.note


def test_one_observed_include_cannot_establish_capture_coverage() -> None:
    report = assess_include_vote_overlap(
        {"only": 2},
        reviewers=2,
        screened=100,
        target_capture_coverage=0.95,
        reviewer_independence_documented=True,
    )

    assert report.proxy_target_met is False
    assert report.method == "undetermined"
    assert report.capture_coverage_proxy is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reviewers": 0, "screened": 0, "target_capture_coverage": 0.95},
        {"reviewers": 2, "screened": -1, "target_capture_coverage": 0.95},
        {"reviewers": 2, "screened": 10, "target_capture_coverage": 0.0},
        {
            "reviewers": 2,
            "screened": 10,
            "target_capture_coverage": 0.95,
            "min_sample": 0,
        },
    ],
)
def test_overlap_assessment_rejects_implausible_parameters(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        assess_include_vote_overlap({}, **kwargs)  # type: ignore[arg-type]


def test_overlap_assessment_rejects_more_observed_includes_than_screened() -> None:
    with pytest.raises(ValueError, match="more works"):
        assess_include_vote_overlap(
            {"W1": 1, "W2": 1},
            reviewers=2,
            screened=1,
            target_capture_coverage=0.95,
        )
