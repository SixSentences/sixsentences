"""Capture-recapture completeness estimation (Chao2)."""

from sixsentences_server.coverage.estimator import estimate_completeness


def test_single_occasion_is_undetermined() -> None:
    # one query -> no overlap to learn from -> honest "undetermined"
    report = estimate_completeness({"W1": 1, "W2": 1, "W3": 1}, occasions=1)
    assert report.method == "undetermined"
    assert "need >=2" in report.note


def test_full_overlap_reads_as_near_complete() -> None:
    # every work found by every one of 3 queries -> no singletons -> complete
    counts = {f"W{i}": 3 for i in range(20)}
    report = estimate_completeness(counts, occasions=3)
    assert report.method == "chao2"
    assert report.singletons == 0
    assert report.completeness == 1.0  # f1==0 -> f0==0 -> nothing missed


def test_many_singletons_imply_missed_works() -> None:
    # lots of works seen by exactly one query -> strategy is far from saturated
    counts = {f"S{i}": 1 for i in range(20)}
    counts |= {f"D{i}": 2 for i in range(4)}
    counts |= {f"C{i}": 3 for i in range(6)}
    report = estimate_completeness(counts, occasions=3)
    assert report.method == "chao2"
    assert report.estimated_total > report.observed  # some works estimated missing
    assert 0.0 < report.completeness < 1.0
    assert report.ci_low <= report.completeness <= report.ci_high


def test_bias_corrected_point_estimate_matches_formula() -> None:
    # f1=10, f2=5, observed=50, m=4 -> S_est = 50 + 0.75*10*9/(2*6) = 55.625
    counts = {f"S{i}": 1 for i in range(10)}
    counts |= {f"D{i}": 2 for i in range(5)}
    counts |= {f"C{i}": 4 for i in range(35)}  # rest seen in all occasions
    report = estimate_completeness(counts, occasions=4)
    assert report.observed == 50
    assert report.singletons == 10 and report.doubletons == 5
    assert abs(report.estimated_total - 55.625) < 0.01
