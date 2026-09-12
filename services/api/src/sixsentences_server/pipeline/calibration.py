"""Query calibration: hit-count sanity + canary recall (plan stage 1).

Two cheap, auditable checks that catch a mis-specified query before the
expensive stages:

- **Hit-count calibration**: a query that returns nothing is over-constrained
  (or out of the corpus's scope); one that matches a large fraction of the
  corpus is too broad to be a search. The verdict is surfaced so the caller
  (or a future auto-refinement loop) can react.
- **Canary set**: known must-hit works supplied by the user (seed papers). If a
  canary is not retrieved, the query provably missed a relevant work — a direct
  recall signal that a broadening step should act on. This is the run-time
  companion to the eval harness's relative-recall measurement.
"""

from pydantic import BaseModel

NARROW_BELOW = 5
BROAD_RATIO = 0.5


class HitCountCalibration(BaseModel):
    unique: int
    corpus_size: int
    ratio: float
    verdict: str  # empty | narrow | healthy | broad
    note: str


class CanaryReport(BaseModel):
    total: int
    found: int
    missed: list[str]
    recall: float


def calibrate_hit_count(unique: int, corpus_size: int) -> HitCountCalibration:
    ratio = unique / corpus_size if corpus_size else 0.0
    if unique == 0:
        verdict = "empty"
        note = "query returned nothing — too narrow or out of corpus scope; broaden or use --live"
    elif unique < NARROW_BELOW:
        verdict = "narrow"
        note = f"only {unique} hits — the query may be over-constrained"
    elif ratio > BROAD_RATIO:
        verdict = "broad"
        note = f"query matches {ratio:.0%} of the corpus — likely too broad to be a search"
    else:
        verdict = "healthy"
        note = f"{unique} hits ({ratio:.1%} of the corpus)"
    return HitCountCalibration(
        unique=unique, corpus_size=corpus_size, ratio=round(ratio, 4), verdict=verdict, note=note
    )


def check_canaries(retrieved_ids: set[str], canary_ids: list[str]) -> CanaryReport:
    canaries = list(dict.fromkeys(canary_ids))  # dedup, keep order
    found = [c for c in canaries if c in retrieved_ids]
    missed = [c for c in canaries if c not in retrieved_ids]
    recall = len(found) / len(canaries) if canaries else 1.0
    return CanaryReport(
        total=len(canaries), found=len(found), missed=missed, recall=round(recall, 4)
    )
