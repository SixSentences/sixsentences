"""Screening evaluation metrics: WSS@95 and recall@effort.

For the screening stage, plain accuracy is meaningless (includes are ~1-2% of
records). The field's metrics are recall-first:

- **recall@effort**: after screening the top `effort` fraction of a
  relevance-ranked list, what share of the true includes have been found. Drives
  the "screen 25% of records, catch 90% of includes" story.
- **WSS@r** (Work Saved over Sampling at recall r; Cohen 2006): the fraction of
  records you do NOT have to screen to reach recall r, beyond what random
  sampling would save. Screen the ranked list top-down to the position k where
  r of the includes are found; WSS = (N-k)/N - (1-r). ~0 means no better than
  random; toward 1 means a strong ranking saves most of the work. Can go
  slightly negative when the ranking is worse than random.

Both take the ground-truth include labels IN RANKED ORDER, so they evaluate the
ranker/screener a run would apply — the harness feeds them SYNERGY labels.
"""

import math

from pydantic import BaseModel


def recall_at_effort(labels_in_rank_order: list[bool], effort: float) -> float:
    total = sum(labels_in_rank_order)
    if total == 0:
        return 1.0
    cutoff = round(len(labels_in_rank_order) * effort)
    return sum(labels_in_rank_order[:cutoff]) / total


def wss_at_recall(labels_in_rank_order: list[bool], target_recall: float = 0.95) -> float:
    n = len(labels_in_rank_order)
    total = sum(labels_in_rank_order)
    if n == 0 or total == 0:
        return 0.0
    needed = math.ceil(target_recall * total)
    found = 0
    cutoff = n  # worst case: must screen everything
    for i, is_include in enumerate(labels_in_rank_order, start=1):
        if is_include:
            found += 1
        if found >= needed:
            cutoff = i
            break
    return round((n - cutoff) / n - (1 - target_recall), 4)


class ScreeningEvalReport(BaseModel):
    total: int
    includes: int
    wss_at_95: float
    recall_at_10pct: float
    recall_at_25pct: float
    recall_at_50pct: float


def evaluate_screening(labels_in_rank_order: list[bool]) -> ScreeningEvalReport:
    return ScreeningEvalReport(
        total=len(labels_in_rank_order),
        includes=sum(labels_in_rank_order),
        wss_at_95=wss_at_recall(labels_in_rank_order, 0.95),
        recall_at_10pct=round(recall_at_effort(labels_in_rank_order, 0.10), 4),
        recall_at_25pct=round(recall_at_effort(labels_in_rank_order, 0.25), 4),
        recall_at_50pct=round(recall_at_effort(labels_in_rank_order, 0.50), 4),
    )
