"""Capture-recapture completeness estimation (Chao2)."""

import pytest

from sixsentences.coverage.estimator import estimate_completeness


def test_single_occasion_is_undetermined() -> None:
    # one query -> no overlap to learn from -> honest "undetermined"
    report = estimate_completeness({"W1": 1, "W2": 1, "W3": 1}, occasions=1)
    assert report.method == "undetermined"
    assert "need >=2" in report.note


def test_full_overlap_is_not_claimed_complete_without_singleton_evidence() -> None:
    # identical capture occasions do not establish that nothing was missed
    counts = {f"W{i}": 3 for i in range(20)}
    report = estimate_completeness(
        counts,
        occasions=3,
        occasion_independence_verified=True,
    )
    assert report.method == "undetermined"
    assert report.singletons == 0
    assert report.completeness is None


def test_many_singletons_imply_missed_works() -> None:
    # lots of works seen by exactly one query -> strategy is far from saturated
    counts = {f"S{i}": 1 for i in range(20)}
    counts |= {f"D{i}": 2 for i in range(4)}
    counts |= {f"C{i}": 3 for i in range(6)}
    report = estimate_completeness(
        counts,
        occasions=3,
        occasion_independence_verified=True,
    )
    assert report.method == "chao2"
    assert report.estimated_total is not None
    assert report.completeness is not None
    assert report.ci_low is not None and report.ci_high is not None
    assert report.estimated_total > report.observed  # some works estimated missing
    assert 0.0 < report.completeness < 1.0
    assert report.ci_low <= report.completeness <= report.ci_high


def test_bias_corrected_point_estimate_matches_formula() -> None:
    # f1=10, f2=5, observed=50, m=4 -> S_est = 50 + 0.75*10*9/(2*6) = 55.625
    counts = {f"S{i}": 1 for i in range(10)}
    counts |= {f"D{i}": 2 for i in range(5)}
    counts |= {f"C{i}": 4 for i in range(35)}  # rest seen in all occasions
    report = estimate_completeness(
        counts,
        occasions=4,
        occasion_independence_verified=True,
    )
    assert report.estimated_total is not None
    assert report.observed == 50
    assert report.singletons == 10 and report.doubletons == 5
    assert abs(report.estimated_total - 55.625) < 0.01


@pytest.mark.parametrize(
    ("counts", "occasions"),
    [
        ({}, -1),
        ({"W1": 0}, 2),
        ({"W1": 3}, 2),
        ({"W1": True}, 2),
        ({"": 1}, 2),
    ],
)
def test_invalid_capture_domains_fail_fast(counts: dict[str, int], occasions: int) -> None:
    with pytest.raises(ValueError):
        estimate_completeness(counts, occasions)


def test_zero_occasions_is_valid_only_for_an_empty_capture_map() -> None:
    report = estimate_completeness({}, occasions=0)
    assert report.method == "undetermined" and report.observed == 0


def test_independence_must_be_asserted_before_estimating() -> None:
    report = estimate_completeness({"W1": 1, "W2": 1}, occasions=2)

    assert report.method == "undetermined"
    assert report.completeness is None
    assert "independence" in report.note
