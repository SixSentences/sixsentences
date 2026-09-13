"""Query calibration: hit-count sanity and canary recall."""

from sixsentences_server.pipeline.calibration import calibrate_hit_count, check_canaries


def test_hit_count_verdicts() -> None:
    assert calibrate_hit_count(0, 50_000).verdict == "empty"
    assert calibrate_hit_count(3, 50_000).verdict == "narrow"
    assert calibrate_hit_count(250, 50_000).verdict == "healthy"
    assert calibrate_hit_count(40_000, 50_000).verdict == "broad"


def test_canary_all_found() -> None:
    report = check_canaries({"W1", "W2", "W3"}, ["W1", "W2"])
    assert report.found == 2 and report.missed == [] and report.recall == 1.0


def test_canary_reports_missed_must_hits() -> None:
    report = check_canaries({"W1"}, ["W1", "W2", "W3"])
    assert report.found == 1
    assert report.missed == ["W2", "W3"]
    assert report.recall == round(1 / 3, 4)


def test_canary_deduplicates_input() -> None:
    report = check_canaries({"W1"}, ["W1", "W1", "W2"])
    assert report.total == 2  # duplicate canary counted once
