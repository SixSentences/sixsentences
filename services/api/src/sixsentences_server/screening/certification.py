"""Exploratory screening-recall estimation (QUALITY.md layers 3 and 5).

Treat each reviewer's set of INCLUDE votes as one capture occasion: a work included by
many reviewers is "captured" often, a work included by only one is a singleton,
and works NO reviewer included are the "unseen" includes the estimator infers.
Chao2 over these capture frequencies estimates the TOTAL number of true
includes — including the ones the ensemble missed — which yields an estimated
screening recall with a confidence interval.

These estimates depend on reviewer independence. Distinct model or provider
names, agreement, and sample size do not establish that assumption. Certification
therefore additionally requires an independently verified basis supplied by a
trusted caller; the hosted model-only pipeline has no such basis. Neither its
point estimate nor its interval is an independently measured recall guarantee.
The statistical lower-bound and minimum-sample checks remain necessary even
when that separate prerequisite is met. Human seed calibration is separate.
"""

from typing import Any

from pydantic import BaseModel

from sixsentences_server.coverage.estimator import estimate_completeness

MIN_SCREENED_TO_CERTIFY = 100  # small samples cannot certify (published SR practice)


class RecallCertification(BaseModel):
    method: str  # "chao2" | "undetermined"
    estimated_recall: float
    ci_low: float
    ci_high: float
    target_recall: float
    certified: bool
    reviewers: int  # ensemble size = capture occasions
    included_observed: int
    estimated_true_includes: float
    estimated_missed: int  # includes no reviewer caught
    screened: int
    min_sample: int
    note: str


def certify_screening_recall(
    include_votes: dict[str, int],
    *,
    reviewers: int,
    screened: int,
    target_recall: float,
    min_sample: int = MIN_SCREENED_TO_CERTIFY,
    reviewer_independence_verified: bool = False,
) -> RecallCertification:
    """Estimate recall without inferring independence from model agreement.

    ``include_votes`` maps a work id to its count of distinct INCLUDE reviewers.
    The independence prerequisite is server-owned, defaults closed, and must
    never be inferred from the number or names of models or providers.
    """
    report = estimate_completeness(include_votes, reviewers)  # completeness == recall here
    missed = int(round(report.estimated_total - report.observed))

    base: dict[str, Any] = dict(
        estimated_recall=report.completeness,
        ci_low=report.ci_low,
        ci_high=report.ci_high,
        target_recall=target_recall,
        reviewers=reviewers,
        included_observed=report.observed,
        estimated_true_includes=report.estimated_total,
        estimated_missed=missed,
        screened=screened,
        min_sample=min_sample,
    )

    if report.method != "chao2":
        return RecallCertification(
            method="undetermined",
            certified=False,
            note="need >=2 independent reviewers with disagreement to certify recall",
            **base,
        )

    sample_met = screened >= min_sample
    certified = reviewer_independence_verified and report.ci_low >= target_recall and sample_met
    if certified:
        note = f"certified: conservative recall {report.ci_low:.1%} >= target {target_recall:.0%}"
    elif not reviewer_independence_verified:
        note = (
            "not certified: exploratory model-overlap estimate only; reviewer independence "
            "has not been verified. Model agreement does not establish recall."
        )
        if not sample_met:
            note += f" Only {screened} screened (< {min_sample} minimum)."
    elif not sample_met:
        note = f"not certified: only {screened} screened (< {min_sample} minimum)"
    else:
        note = (
            f"not certified: conservative recall {report.ci_low:.1%} < target "
            f"{target_recall:.0%} — screen more or add an independent reviewer"
        )
    return RecallCertification(method="chao2", certified=certified, note=note, **base)
