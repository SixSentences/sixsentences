"""Deterministic research-data recipe tests over synthetic observations."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from sixsentences.data import (
    DEFAULT_LIMITS,
    DatasetAnalysisError,
    DescriptiveRecipe,
    GroupSummaryRecipe,
    MissingnessRecipe,
    PearsonCorrelationRecipe,
    RandomEffectsMetaAnalysisRecipe,
    analyze,
    analyze_rows,
    parse_dataset,
)


def test_missingness_uses_the_documented_rule_for_every_column() -> None:
    result = analyze_rows(
        [
            {"a": 1, "b": None, "sentinel": "NA"},
            {"a": " ", "b": 2, "sentinel": -99},
        ],
        MissingnessRecipe(),
    )
    assert result.rows == 2
    assert [(column.name, column.missing, column.percent) for column in result.columns] == [
        ("a", 1, 50.0),
        ("b", 1, 50.0),
        ("sentinel", 0, 0.0),
    ]
    assert any("Sentinel" in limitation for limitation in result.limitations)


def test_descriptive_uses_all_complete_values_and_sample_sd() -> None:
    dataset = parse_dataset(b"score\n1\n2\n3\n\n", "scores.csv")
    result = analyze(dataset, DescriptiveRecipe("score"))
    assert result.n == 3
    assert result.missing == 0
    assert result.mean == 2
    assert result.median == 2
    assert result.sample_sd == 1
    assert (result.minimum, result.maximum) == (1, 3)


def test_descriptive_reports_single_observation_sd_as_undefined() -> None:
    result = analyze_rows([{"x": 5}], DescriptiveRecipe("x"))
    assert result.sample_sd is None
    assert "undefined" in result.limitations[1]


@pytest.mark.parametrize(
    ("rows", "recipe", "message"),
    [
        ([{"x": "NA"}], DescriptiveRecipe("x"), "non-missing non-numeric"),
        ([{"x": None}], DescriptiveRecipe("x"), "no numeric values"),
        ([{"x": 1}], DescriptiveRecipe("unknown"), "unknown column"),
        ([], MissingnessRecipe(), "at least one"),
    ],
)
def test_analysis_rejects_ambiguous_or_undefined_inputs(
    rows: list[dict[str, object]], recipe: object, message: str
) -> None:
    with pytest.raises(DatasetAnalysisError, match=message):
        analyze_rows(rows, recipe)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("metric", "expected"),
    [("mean", 3.0), ("median", 3.0), ("sum", 6.0), ("count", 2)],
)
def test_group_summary_metrics_are_deterministic(metric: str, expected: float | int) -> None:
    rows = [
        {"group": "b", "score": 8},
        {"group": "a", "score": 2},
        {"group": "a", "score": 4},
        {"group": "", "score": 9},
        {"group": "b", "score": None},
    ]
    result = analyze_rows(
        rows,
        GroupSummaryRecipe("group", "score", metric),  # type: ignore[arg-type]
    )
    assert [(group.group, group.n, group.value) for group in result.groups] == [
        ("a", 2, expected),
        ("b", 1, 8 if metric != "count" else 1),
    ]
    assert result.missing_group == 1
    assert result.missing_value == 1


def test_group_summary_keeps_numeric_and_text_labels_distinct() -> None:
    result = analyze_rows(
        [{"group": 1, "score": 2}, {"group": "1", "score": 4}],
        GroupSummaryRecipe("group", "score"),
    )
    assert len(result.groups) == 2
    assert {type(group.group) for group in result.groups} == {int, str}


def test_group_summary_rejects_invalid_metric_and_mixed_values() -> None:
    with pytest.raises(DatasetAnalysisError, match="metric"):
        analyze_rows(
            [{"group": "a", "score": 1}],
            GroupSummaryRecipe("group", "score", "mode"),  # type: ignore[arg-type]
        )
    with pytest.raises(DatasetAnalysisError, match="non-missing non-numeric"):
        analyze_rows(
            [{"group": "a", "score": "unknown"}],
            GroupSummaryRecipe("group", "score"),
        )


def test_pearson_correlation_uses_complete_pairs() -> None:
    result = analyze_rows(
        [
            {"x": 1, "y": 2},
            {"x": 2, "y": 4},
            {"x": 3, "y": 6},
            {"x": 4, "y": None},
        ],
        PearsonCorrelationRecipe("x", "y"),
    )
    assert result.n == 3
    assert result.incomplete_pairs == 1
    assert result.pearson_r == pytest.approx(1.0)
    assert "does not establish causation" in result.limitations[0]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"x": 1, "y": 2}], "at least two"),
        ([{"x": 1, "y": 2}, {"x": 1, "y": 3}], "constant"),
        ([{"x": 1, "y": 2}, {"x": "bad", "y": 3}], "non-numeric"),
    ],
)
def test_pearson_rejects_undefined_inputs(rows: list[dict[str, object]], message: str) -> None:
    with pytest.raises(DatasetAnalysisError, match=message):
        analyze_rows(rows, PearsonCorrelationRecipe("x", "y"))


def test_der_simonian_laird_matches_independent_hand_calculation() -> None:
    rows = [
        {"study": "A", "effect": 0.2, "se": 0.1},
        {"study": "B", "effect": 0.5, "se": 0.2},
        {"study": "C", "effect": 0.1, "se": 0.15},
        {"study": "incomplete", "effect": None, "se": 0.3},
    ]
    result = analyze_rows(
        rows,
        RandomEffectsMetaAnalysisRecipe("effect", "se", "study"),
    )

    weights = [100.0, 25.0, 1 / 0.15**2]
    effects = [0.2, 0.5, 0.1]
    fixed = sum(w * e for w, e in zip(weights, effects, strict=True)) / sum(weights)
    q = sum(w * (e - fixed) ** 2 for w, e in zip(weights, effects, strict=True))
    c_value = sum(weights) - sum(w**2 for w in weights) / sum(weights)
    tau_squared = max(0.0, (q - 2) / c_value)
    random_weights = [1 / (se**2 + tau_squared) for se in (0.1, 0.2, 0.15)]
    pooled = sum(w * e for w, e in zip(random_weights, effects, strict=True)) / sum(random_weights)

    assert result.k == 3
    assert result.excluded_incomplete == 1
    assert result.fixed_effect == pytest.approx(fixed)
    assert result.q == pytest.approx(q)
    assert result.tau_squared == pytest.approx(tau_squared)
    assert result.pooled_effect == pytest.approx(pooled)
    assert result.ci_95[0] < result.pooled_effect < result.ci_95[1]
    assert 0 <= result.i_squared_percent <= 100
    assert any("Hartung-Knapp" in limitation for limitation in result.limitations)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [{"effect": 0.1, "se": 0.1}, {"effect": 0.2, "se": 0}],
            "strictly positive",
        ),
        (
            [{"effect": 0.1, "se": 0.1}, {"effect": "bad", "se": 0.2}],
            "non-numeric",
        ),
        ([{"effect": 0.1, "se": 0.1}], "at least two"),
        (
            [{"effect": 0.1, "se": 1e-300}, {"effect": 0.2, "se": 0.1}],
            "numerically safe range",
        ),
    ],
)
def test_meta_analysis_rejects_invalid_or_numerically_unsafe_inputs(
    rows: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(DatasetAnalysisError, match=message):
        analyze_rows(rows, RandomEffectsMetaAnalysisRecipe("effect", "se"))


def test_analysis_rejects_row_limit_instead_of_sampling() -> None:
    limits = replace(DEFAULT_LIMITS, max_rows=2, preview_rows=1)
    with pytest.raises(DatasetAnalysisError, match="more than 2"):
        analyze_rows(
            [{"x": 1}, {"x": 2}, {"x": 3}],
            DescriptiveRecipe("x"),
            limits=limits,
        )


def test_analysis_rejects_overflow_instead_of_emitting_nonfinite_json() -> None:
    with pytest.raises(DatasetAnalysisError, match="overflow"):
        analyze_rows(
            [{"x": 1e308}, {"x": 1e308}],
            DescriptiveRecipe("x"),
        )
    assert math.isfinite(analyze_rows([{"x": 1}, {"x": 2}], DescriptiveRecipe("x")).mean)
