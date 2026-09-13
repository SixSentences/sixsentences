"""Seed-label screening calibration (ASReview pattern)."""

from sixsentences_server.screening.calibration import SeedLabel, calibrate_against_seeds

_INCLUDES = [SeedLabel(f"W{i}", True) for i in range(10)]
_EXCLUDES = [SeedLabel(f"X{i}", False) for i in range(5)]
_SEEDS = _INCLUDES + _EXCLUDES


def test_trusted_when_recall_clears_the_target() -> None:
    verdicts = {f"W{i}": "include" for i in range(10)} | {f"X{i}": "exclude" for i in range(5)}
    report = calibrate_against_seeds(verdicts, _SEEDS)
    assert report.verdict == "trusted"
    assert report.recall == 1.0 and not report.missed
    assert report.agreement == 1.0  # perfect agreement -> kappa 1


def test_low_recall_when_a_human_include_is_excluded() -> None:
    verdicts = (
        {f"W{i}": "include" for i in range(9)}
        | {"W9": "exclude"}  # the screener wrongly excludes one relevant paper
        | {f"X{i}": "exclude" for i in range(5)}
    )
    report = calibrate_against_seeds(verdicts, _SEEDS)
    assert report.verdict == "low_recall"
    assert report.recall == 0.9 and report.missed == ["W9"]


def test_screener_unsure_keeps_a_work_so_it_is_not_a_miss() -> None:
    verdicts = (
        {f"W{i}": "include" for i in range(9)}
        | {"W9": "unsure"}  # unsure keeps the work -> caught, recall-first
        | {f"X{i}": "exclude" for i in range(5)}
    )
    report = calibrate_against_seeds(verdicts, _SEEDS)
    assert report.recall == 1.0 and report.verdict == "trusted"


def test_insufficient_overlap_is_honest() -> None:
    seeds = [SeedLabel("W1", True), SeedLabel("W2", True)]
    report = calibrate_against_seeds({"W1": "include"}, seeds)
    assert report.verdict == "insufficient_overlap" and report.evaluated == 1
