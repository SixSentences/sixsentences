"""Screening metrics: WSS@95 and recall@effort."""

from sixsentences_server.evals.metrics import evaluate_screening, recall_at_effort, wss_at_recall


def test_perfect_ranking_saves_almost_all_work() -> None:
    labels = [True] * 5 + [False] * 95  # all 5 includes ranked first
    assert wss_at_recall(labels, 0.95) == 0.90  # (100-5)/100 - 0.05
    assert recall_at_effort(labels, 0.10) == 1.0


def test_worst_ranking_is_below_random() -> None:
    labels = [False] * 95 + [True] * 5  # includes ranked last
    assert wss_at_recall(labels, 0.95) == -0.05  # must screen everything
    assert recall_at_effort(labels, 0.10) == 0.0


def test_recall_at_effort_is_monotonic() -> None:
    labels = [True, False, True, False, True, False, True, False, True, False]
    r10 = recall_at_effort(labels, 0.10)
    r50 = recall_at_effort(labels, 0.50)
    r100 = recall_at_effort(labels, 1.0)
    assert r10 <= r50 <= r100 == 1.0


def test_no_includes_is_degenerate_but_safe() -> None:
    assert wss_at_recall([False] * 10) == 0.0
    assert recall_at_effort([False] * 10, 0.5) == 1.0  # vacuously complete


def test_report_bundles_the_metrics() -> None:
    report = evaluate_screening([True] * 10 + [False] * 90)
    assert report.total == 100 and report.includes == 10
    assert report.wss_at_95 > 0.8  # strong ranking
    assert report.recall_at_10pct == 1.0
