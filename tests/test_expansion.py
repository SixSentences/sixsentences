"""Query expansion remains provider-neutral and fail-closed."""

from sixsentences.pipeline.expansion import assess_saturation, validate_variants


def test_validation_normalizes_and_rejects_duplicates_and_invalid_syntax() -> None:
    report = validate_variants(
        ["terraform AND security"],
        [
            "terraform AND security",
            '"infrastructure as code" AND vulnerability',
            "(broken",
        ],
    )
    assert report.accepted == ['("infrastructure as code" AND vulnerability)']
    assert report.rejected["terraform AND security"] == "duplicate"
    assert report.rejected["(broken"] == "invalid_syntax"


def test_saturation_uses_new_unique_work_fraction() -> None:
    continuing = assess_saturation({"W1", "W2"}, {"W2", "W3"}, threshold=0.3)
    saturated = assess_saturation(set(range(100)), {99, 100}, threshold=0.05)  # type: ignore[arg-type]

    assert continuing.newly_seen == 1 and continuing.saturated is False
    assert saturated.newly_seen == 1 and saturated.saturated is True
