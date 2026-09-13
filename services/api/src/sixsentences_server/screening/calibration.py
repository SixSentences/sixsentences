"""Seed-label screening calibration (ASReview pattern).

Before trusting an automated screen, a reviewer hand-labels a small seed set;
this measures how the screener did on those seeds — above all its RECALL (did it
exclude any paper the human would include? that is the audit-critical error) plus
the model/human agreement (Cohen's kappa). The output is a calibration verdict:
only a screen that clears the target recall on the seeds should be trusted for
the rest — the same honest-operating-point stance as the coverage certification.
"""

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class SeedLabel:
    work_id: str
    included: bool  # the human's ground-truth label


class CalibrationReport(BaseModel):
    seed_size: int
    evaluated: int  # seeds the screener also ruled on
    human_includes: int
    recall: float  # of human-includes, the fraction the screener did NOT exclude
    agreement: float  # Cohen's kappa on include vs exclude (screener-unsure dropped)
    missed: list[str]  # human-include but screener EXCLUDED — the errors that matter
    verdict: str  # trusted | low_recall | insufficient_overlap
    note: str


def _kappa(pairs: list[tuple[bool, bool]]) -> float:
    n = len(pairs)
    if n == 0:
        return 0.0
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe >= 1.0 else round((po - pe) / (1 - pe), 4)


def calibrate_against_seeds(
    model_verdicts: dict[str, str],
    seeds: list[SeedLabel],
    *,
    target_recall: float = 0.95,
    min_seeds: int = 10,
) -> CalibrationReport:
    """Compare the screener's verdicts to human seed labels on the overlap."""
    evaluated = [s for s in seeds if s.work_id in model_verdicts]
    human_includes = [s for s in evaluated if s.included]
    # recall (safety): a human-include is caught unless the screener EXCLUDED it
    # (a screener "unsure" keeps the work, so it is not a miss — recall-first).
    missed = [s.work_id for s in human_includes if model_verdicts[s.work_id] == "exclude"]
    recall = (len(human_includes) - len(missed)) / len(human_includes) if human_includes else 1.0
    # kappa on the works the screener committed on (drop its unsure)
    pairs = [
        (model_verdicts[s.work_id] == "include", s.included)
        for s in evaluated
        if model_verdicts[s.work_id] in ("include", "exclude")
    ]
    agreement = _kappa(pairs)

    if len(evaluated) < min_seeds:
        verdict = "insufficient_overlap"
        note = (
            f"only {len(evaluated)} of {len(seeds)} seed labels were screened "
            f"(need >= {min_seeds}); label more works or widen the search"
        )
    elif recall < target_recall:
        verdict = "low_recall"
        note = (
            f"screener recall {recall:.0%} is below the {target_recall:.0%} target — it excluded "
            f"{len(missed)} paper(s) you would include; do not trust the screen as configured"
        )
    else:
        verdict = "trusted"
        note = (
            f"screener recall {recall:.0%} >= {target_recall:.0%} on {len(evaluated)} seeds "
            f"(kappa {agreement}); the operating point is calibrated"
        )
    return CalibrationReport(
        seed_size=len(seeds),
        evaluated=len(evaluated),
        human_includes=len(human_includes),
        recall=round(recall, 4),
        agreement=agreement,
        missed=missed,
        verdict=verdict,
        note=note,
    )
