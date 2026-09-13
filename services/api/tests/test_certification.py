"""Screening-recall certification via capture-recapture across the ensemble."""

from sixsentences_server.screening.certification import certify_screening_recall


def test_undetermined_with_a_single_reviewer() -> None:
    cert = certify_screening_recall(
        {"W1": 1, "W2": 1}, reviewers=1, screened=200, target_recall=0.95
    )
    assert cert.method == "undetermined"
    assert cert.certified is False


def test_full_agreement_alone_cannot_certify_even_with_large_sample() -> None:
    # The model-only estimate may be perfect even when all reviewers share errors.
    votes = {f"W{i}": 3 for i in range(60)}
    cert = certify_screening_recall(votes, reviewers=3, screened=200, target_recall=0.95)
    assert cert.method == "chao2"
    assert cert.estimated_recall == 1.0
    assert cert.estimated_missed == 0
    assert cert.certified is False
    assert "exploratory" in cert.note
    assert "independence has not been verified" in cert.note


def test_verified_independence_still_requires_statistical_thresholds() -> None:
    # An explicitly supplied synthetic assumption tests the statistical gate;
    # it is not a provider attestation or a production quality approval.
    votes = {f"W{i}": 3 for i in range(60)}
    cert = certify_screening_recall(
        votes,
        reviewers=3,
        screened=200,
        target_recall=0.95,
        reviewer_independence_verified=True,
    )
    assert cert.certified is True


def test_many_singletons_imply_missed_includes_and_block_certification() -> None:
    votes = {f"S{i}": 1 for i in range(30)}  # 30 includes seen by only one reviewer
    votes |= {f"D{i}": 2 for i in range(5)}
    votes |= {f"C{i}": 3 for i in range(20)}
    cert = certify_screening_recall(
        votes,
        reviewers=3,
        screened=300,
        target_recall=0.95,
        reviewer_independence_verified=True,
    )
    assert cert.method == "chao2"
    assert cert.estimated_missed > 0
    assert cert.estimated_recall < 1.0
    assert cert.certified is False  # conservative recall below target


def test_small_sample_cannot_certify_even_with_perfect_agreement() -> None:
    votes = {f"W{i}": 3 for i in range(20)}
    cert = certify_screening_recall(
        votes,
        reviewers=3,
        screened=40,
        target_recall=0.95,
        reviewer_independence_verified=True,
    )
    assert cert.estimated_recall == 1.0
    assert cert.certified is False
    assert "minimum" in cert.note


def test_six_record_audit_payload_keeps_point_estimate_separate_from_certification() -> None:
    """The small UI-QA run can estimate full agreement without certifying recall."""
    cert = certify_screening_recall(
        {f"W{i}": 3 for i in range(6)}, reviewers=3, screened=6, target_recall=0.95
    )
    payload = cert.model_dump()
    assert payload["estimated_recall"] == 1.0
    assert payload["ci_low"] == 1.0
    assert payload["screened"] == 6
    assert payload["min_sample"] == 100
    assert payload["certified"] is False
    assert "6 screened" in payload["note"]
