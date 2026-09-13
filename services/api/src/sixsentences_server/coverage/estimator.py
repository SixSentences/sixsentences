"""Honest completeness via capture-recapture (Chao2).

Product principle #3: report a coverage estimate, not a completeness claim.

Each executed query (base + every expansion variant) is a capture occasion.
A work found by many queries indicates the strategy is saturating that region;
a long tail of works found by only ONE query implies more uncaptured works
exist. The Chao2 richness estimator turns that overlap structure into an
estimate of the total number of topic-relevant works in the corpus — including
those NO query found — with a 95% confidence interval. Completeness is then
observed / estimated.

This is a statement about the SEARCH STRATEGY over the pinned corpus, not about
all of science. With fewer than two occasions there is no overlap to learn
from, so we say so ("undetermined") rather than inventing a number.

Estimator: bias-corrected Chao2 (Chao 1987; Colwell & Coddington 1994),
    S_est = S_obs + ((m-1)/m) * f1(f1-1) / (2(f2+1))
with f1/f2 the number of works seen in exactly one / exactly two occasions and
m the occasion count. CI: the standard log-normal interval on the number of
undetected works f0 (Chao 1987), which keeps the lower bound >= S_obs.
"""

import math

from pydantic import BaseModel


class CoverageReport(BaseModel):
    method: str  # "chao2" | "undetermined"
    observed: int
    estimated_total: float
    completeness: float  # observed / estimated_total, in (0, 1]
    ci_low: float  # completeness CI (from the estimated-total CI)
    ci_high: float
    occasions: int
    singletons: int  # f1: works found by exactly one query
    doubletons: int  # f2: works found by exactly two queries
    note: str


def estimate_completeness(capture_counts: dict[str, int], occasions: int) -> CoverageReport:
    """Estimate search-strategy completeness from per-work capture frequencies.

    `capture_counts` maps a work id to how many distinct queries returned it.
    `occasions` is the number of queries executed.
    """
    observed = len(capture_counts)
    if occasions < 2 or observed == 0:
        return CoverageReport(
            method="undetermined",
            observed=observed,
            estimated_total=float(observed),
            completeness=1.0 if observed else 0.0,
            ci_low=0.0,
            ci_high=1.0,
            occasions=occasions,
            singletons=sum(1 for c in capture_counts.values() if c == 1),
            doubletons=sum(1 for c in capture_counts.values() if c == 2),
            note="need >=2 searches with overlap to estimate completeness",
        )

    f1 = sum(1 for c in capture_counts.values() if c == 1)
    f2 = sum(1 for c in capture_counts.values() if c == 2)
    k = (occasions - 1) / occasions

    # bias-corrected Chao2: always defined, robust when f2 is small
    f0 = k * f1 * (f1 - 1) / (2 * (f2 + 1))  # estimated undetected works
    estimated_total = observed + f0
    completeness = observed / estimated_total if estimated_total else 1.0

    ci_low, ci_high = _completeness_ci(observed, f0, f1, f2, k)
    return CoverageReport(
        method="chao2",
        observed=observed,
        estimated_total=round(estimated_total, 2),
        completeness=round(completeness, 4),
        ci_low=round(ci_low, 4),
        ci_high=round(ci_high, 4),
        occasions=occasions,
        singletons=f1,
        doubletons=f2,
        note=f"estimated {int(round(f0))} topic-relevant works missed by the search",
    )


def _completeness_ci(observed: int, f0: float, f1: int, f2: int, k: float) -> tuple[float, float]:
    """Log-normal 95% CI on completeness via the CI on undetected works f0."""
    if f0 <= 0:
        return 1.0, 1.0
    # Variance of f0 (Chao 1987); the f2==0 branch uses the singleton-only form.
    if f2 > 0:
        r = f1 / f2
        var = k * f2 * (0.5 * r**2 + k * r**3 + 0.25 * k * r**4)
    else:
        var = (
            k * f1 * (f1 - 1) / 2
            + (k * f1 * (2 * f1 - 1)) ** 2 / 4
            - k * f1**4 / (4 * (observed + f0))
        )
    var = max(var, 1e-9)
    # log-normal factor keeps the estimated total >= observed
    c = math.exp(1.96 * math.sqrt(math.log(1 + var / f0**2)))
    total_low = observed + f0 / c
    total_high = observed + f0 * c
    # more undetected works -> lower completeness, so bounds invert
    return observed / total_high, observed / total_low
