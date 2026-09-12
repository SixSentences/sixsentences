"""Exploratory capture-overlap assessment for reviewer INCLUDE votes.

Treat each reviewer's set of INCLUDE votes as one capture occasion: a work included by
many reviewers is "captured" often, a work included by only one is a singleton,
and works no reviewer included are unobserved. Chao2 over these frequencies
provides a capture-coverage proxy under its model assumptions.

These estimates depend on reviewer independence. Distinct model or provider
names, agreement, and sample size do not establish that assumption. An
assessment can report whether a proxy target is met under caller-documented
assumptions. It has no adjudicated relevance labels, cannot observe false
positive INCLUDE votes or records missed by every reviewer, and therefore only
describes vote-capture overlap rather than ground-truth retrieval performance.
Human seed calibration is separate.
"""

import math
from typing import Any

from pydantic import BaseModel

from sixsentences.coverage.estimator import estimate_completeness

DEFAULT_MIN_SCREENED = 100
DEFAULT_MIN_RECORDS_WITH_INCLUDE_VOTES = 5


class IncludeVoteOverlapReport(BaseModel):
    """Exploratory INCLUDE-vote capture proxy and its prerequisites."""

    method: str  # "chao2" | "undetermined"
    capture_coverage_proxy: float | None
    ci_low: float | None
    ci_high: float | None
    target_capture_coverage: float
    proxy_target_met: bool
    reviewers: int  # ensemble size = capture occasions
    records_with_include_votes: int
    estimated_capturable_records: float | None
    estimated_uncaptured_vote_positive_records: int | None
    screened: int
    min_sample: int
    min_records_with_include_votes: int
    note: str


def assess_include_vote_overlap(
    include_votes: dict[str, int],
    *,
    reviewers: int,
    screened: int,
    target_capture_coverage: float,
    min_sample: int = DEFAULT_MIN_SCREENED,
    min_records_with_include_votes: int = DEFAULT_MIN_RECORDS_WITH_INCLUDE_VOTES,
    reviewer_independence_documented: bool = False,
) -> IncludeVoteOverlapReport:
    """Assess an INCLUDE-vote overlap proxy without claiming ground truth.

    ``include_votes`` maps a work id to its count of distinct INCLUDE reviewers.
    The independence prerequisite defaults closed. ``True`` records a caller
    assertion; it is not proof and must not be inferred from reviewer names.
    """
    if isinstance(reviewers, bool) or not isinstance(reviewers, int) or reviewers < 1:
        raise ValueError("reviewers must be a positive integer")
    if isinstance(screened, bool) or not isinstance(screened, int) or screened < 0:
        raise ValueError("screened must be a non-negative integer")
    if isinstance(min_sample, bool) or not isinstance(min_sample, int) or min_sample < 1:
        raise ValueError("min_sample must be a positive integer")
    if (
        isinstance(min_records_with_include_votes, bool)
        or not isinstance(min_records_with_include_votes, int)
        or min_records_with_include_votes < 1
    ):
        raise ValueError("min_records_with_include_votes must be a positive integer")
    if (
        isinstance(target_capture_coverage, bool)
        or not isinstance(target_capture_coverage, int | float)
        or not math.isfinite(target_capture_coverage)
        or not 0.0 < target_capture_coverage <= 1.0
    ):
        raise ValueError(
            "target_capture_coverage must be finite, greater than zero, and at most one"
        )
    if not isinstance(reviewer_independence_documented, bool):
        raise ValueError("reviewer_independence_documented must be a boolean")
    if len(include_votes) > screened:
        raise ValueError("observed include votes contain more works than were screened")

    report = estimate_completeness(
        include_votes,
        reviewers,
        occasion_independence_verified=reviewer_independence_documented,
    )
    missed = (
        int(round(report.estimated_total - report.observed))
        if report.estimated_total is not None
        else None
    )

    base: dict[str, Any] = dict(
        capture_coverage_proxy=report.completeness,
        ci_low=report.ci_low,
        ci_high=report.ci_high,
        target_capture_coverage=target_capture_coverage,
        reviewers=reviewers,
        records_with_include_votes=report.observed,
        estimated_capturable_records=report.estimated_total,
        estimated_uncaptured_vote_positive_records=missed,
        screened=screened,
        min_sample=min_sample,
        min_records_with_include_votes=min_records_with_include_votes,
    )

    if report.method != "chao2":
        return IncludeVoteOverlapReport(
            method="undetermined",
            proxy_target_met=False,
            note=(
                "capture-overlap proxy is undetermined: reviewer-occasion independence "
                "and at least two singleton INCLUDE captures are required"
            ),
            **base,
        )

    assert report.ci_low is not None
    sample_met = screened >= min_sample
    observed_sample_met = report.observed >= min_records_with_include_votes
    meets_target = (
        reviewer_independence_documented
        and report.ci_low >= target_capture_coverage
        and sample_met
        and observed_sample_met
    )
    if meets_target:
        note = (
            f"target met under supplied assumptions: lower interval bound "
            f"{report.ci_low:.1%} >= {target_capture_coverage:.0%}; this is only "
            "a vote-overlap proxy"
        )
    elif not reviewer_independence_documented:
        note = (
            "target not assessed: reviewer independence has not been documented. "
            "Model agreement does not establish coverage of relevant records."
        )
        if not sample_met:
            note += f" Only {screened} screened (< {min_sample} minimum)."
    elif not sample_met:
        note = f"target not met: only {screened} screened (< {min_sample} minimum)"
    elif not observed_sample_met:
        note = (
            f"target not met: only {report.observed} observed INCLUDE works "
            f"(< {min_records_with_include_votes} minimum)"
        )
    else:
        note = (
            f"proxy target not met: lower interval bound {report.ci_low:.1%} < target "
            f"{target_capture_coverage:.0%} — add an independent capture occasion"
        )
    return IncludeVoteOverlapReport(
        method="chao2",
        proxy_target_met=meets_target,
        note=note,
        **base,
    )
