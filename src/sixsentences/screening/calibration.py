"""Seed-label screening calibration (ASReview pattern).

A reviewer hand-labels a seed set, and this module measures how a screener did
on those cases: observed recall and model/human agreement (Cohen's kappa). The
result describes the seed evidence only. It never turns a small calibration set
into a general recall guarantee.
"""

import math
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class SeedLabel:
    """Human reference label for one unique work."""

    work_id: str
    included: bool  # the human's ground-truth label


class CalibrationReport(BaseModel):
    """Observed seed-set performance without a deployment guarantee."""

    seed_size: int
    evaluated: int  # seeds the screener also ruled on
    committed: int  # evaluated seeds with an include/exclude verdict
    human_includes: int
    recall: float | None  # None when the seed set has no positive reference case
    agreement: float | None  # Cohen's kappa; None for single-class/empty overlap
    committed_human_includes: int
    min_positive_seeds: int
    missed: list[str]  # human-include but screener EXCLUDED — the errors that matter
    verdict: str  # meets_target | below_target | insufficient_evidence
    note: str


def _kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe >= 1.0 else round((po - pe) / (1 - pe), 4)


def calibrate_against_seeds(
    model_verdicts: dict[str, str],
    seeds: list[SeedLabel],
    *,
    target_recall: float = 0.95,
    min_seeds: int = 10,
    min_positive_seeds: int = 5,
) -> CalibrationReport:
    """Compare the screener's verdicts to human seed labels on the overlap."""
    if (
        isinstance(target_recall, bool)
        or not isinstance(target_recall, int | float)
        or not math.isfinite(target_recall)
        or not 0.0 < target_recall <= 1.0
    ):
        raise ValueError("target_recall must be a finite number greater than zero and at most one")
    if isinstance(min_seeds, bool) or not isinstance(min_seeds, int) or min_seeds < 1:
        raise ValueError("min_seeds must be a positive integer")
    if (
        isinstance(min_positive_seeds, bool)
        or not isinstance(min_positive_seeds, int)
        or min_positive_seeds < 1
    ):
        raise ValueError("min_positive_seeds must be a positive integer")
    seed_ids = [seed.work_id for seed in seeds]
    if any(not isinstance(work_id, str) or not work_id.strip() for work_id in seed_ids):
        raise ValueError("seed work ids must be non-empty strings")
    if len(seed_ids) != len(set(seed_ids)):
        raise ValueError("seed work ids must be unique")
    if any(not isinstance(seed.included, bool) for seed in seeds):
        raise ValueError("seed included labels must be booleans")
    allowed_verdicts = {"include", "exclude", "unsure"}
    unknown = sorted(
        work_id for work_id, verdict in model_verdicts.items() if verdict not in allowed_verdicts
    )
    if unknown:
        raise ValueError(f"unknown screening verdict for work {unknown[0]!r}")

    evaluated = [s for s in seeds if s.work_id in model_verdicts]
    human_includes = [s for s in evaluated if s.included]
    # recall (safety): a human-include is caught unless the screener EXCLUDED it
    # (a screener "unsure" keeps the work, so it is not a miss — recall-first).
    missed = [s.work_id for s in human_includes if model_verdicts[s.work_id] == "exclude"]
    recall = (len(human_includes) - len(missed)) / len(human_includes) if human_includes else None
    # kappa on the works the screener committed on (drop its unsure)
    pairs = [
        (model_verdicts[s.work_id] == "include", s.included)
        for s in evaluated
        if model_verdicts[s.work_id] in ("include", "exclude")
    ]
    committed_human_includes = [
        seed for seed in human_includes if model_verdicts[seed.work_id] in ("include", "exclude")
    ]
    agreement = _kappa(pairs)

    if len(evaluated) < min_seeds:
        verdict = "insufficient_evidence"
        note = (
            f"only {len(evaluated)} of {len(seeds)} seed labels were screened "
            f"(need >= {min_seeds}); label more works or widen the search"
        )
    elif len(human_includes) < min_positive_seeds:
        verdict = "insufficient_evidence"
        note = (
            f"only {len(human_includes)} human-positive seed cases "
            f"(need >= {min_positive_seeds}); observed recall is too sparse to assess"
        )
    elif len(pairs) < min_seeds:
        verdict = "insufficient_evidence"
        note = (
            f"only {len(pairs)} committed include/exclude verdicts overlap the seeds "
            f"(need >= {min_seeds}); unsure verdicts do not establish calibration"
        )
    elif len(committed_human_includes) < min_positive_seeds:
        verdict = "insufficient_evidence"
        note = (
            f"only {len(committed_human_includes)} human-positive seeds have committed "
            f"include/exclude verdicts (need >= {min_positive_seeds}); abstention does "
            "not establish calibration"
        )
    elif recall is not None and recall < target_recall:
        verdict = "below_target"
        note = (
            f"observed seed recall {recall:.0%} is below the {target_recall:.0%} target; "
            f"{len(missed)} human-positive paper(s) were excluded"
        )
    else:
        assert recall is not None
        verdict = "meets_target"
        agreement_note = "undefined" if agreement is None else str(agreement)
        note = (
            f"observed seed recall {recall:.0%} meets the {target_recall:.0%} target on "
            f"{len(evaluated)} seeds ({len(pairs)} committed; kappa {agreement_note}); "
            "this is seed-set evidence, not a general recall guarantee"
        )
    return CalibrationReport(
        seed_size=len(seeds),
        evaluated=len(evaluated),
        committed=len(pairs),
        human_includes=len(human_includes),
        committed_human_includes=len(committed_human_includes),
        min_positive_seeds=min_positive_seeds,
        recall=round(recall, 4) if recall is not None else None,
        agreement=agreement,
        missed=missed,
        verdict=verdict,
        note=note,
    )
