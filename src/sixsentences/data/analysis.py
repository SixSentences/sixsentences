"""Small deterministic analysis recipes for bounded research tables.

These recipes are auditable calculations, not a replacement for a statistical
analysis plan. They do not infer causal effects, impute missing values, adjust
for multiplicity or validate study design assumptions.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sixsentences.data.errors import DatasetAnalysisError, DatasetImportError
from sixsentences.data.profile import as_number, is_missing, normalize_rows
from sixsentences.data.types import DEFAULT_LIMITS, CellValue, DatasetLimits, ParsedDataset, Row

type GroupMetric = Literal["mean", "median", "sum", "count"]


@dataclass(frozen=True, slots=True)
class MissingnessRecipe:
    """Count null and empty/whitespace-only cells in every column."""


@dataclass(frozen=True, slots=True)
class DescriptiveRecipe:
    """Summarize one numeric column with sample standard deviation."""

    column: str


@dataclass(frozen=True, slots=True)
class GroupSummaryRecipe:
    """Aggregate complete numeric values within observed groups."""

    group_by: str
    value_column: str
    metric: GroupMetric = "mean"


@dataclass(frozen=True, slots=True)
class PearsonCorrelationRecipe:
    """Compute complete-case Pearson product-moment correlation."""

    x_column: str
    y_column: str


@dataclass(frozen=True, slots=True)
class RandomEffectsMetaAnalysisRecipe:
    """Pool effects and positive standard errors with DerSimonian-Laird."""

    effect_column: str
    se_column: str
    label_column: str | None = None


type AnalysisRecipe = (
    MissingnessRecipe
    | DescriptiveRecipe
    | GroupSummaryRecipe
    | PearsonCorrelationRecipe
    | RandomEffectsMetaAnalysisRecipe
)


@dataclass(frozen=True, slots=True)
class MissingnessColumn:
    """Missingness count and percentage for one column."""

    name: str
    missing: int
    percent: float


@dataclass(frozen=True, slots=True)
class MissingnessResult:
    """Exact missingness result over all imported rows."""

    rows: int
    columns: tuple[MissingnessColumn, ...]
    kind: str = field(default="missingness", init=False)
    method: str = field(default="null/blank cell count", init=False)
    limitations: tuple[str, ...] = field(
        default=(
            "Only null and empty or whitespace-only strings are missing.",
            "Sentinel codes such as NA, -99 or unknown are not inferred.",
        ),
        init=False,
    )


@dataclass(frozen=True, slots=True)
class DescriptiveResult:
    """Descriptive statistics for one strictly numeric column."""

    column: str
    n: int
    missing: int
    mean: float
    median: float
    sample_sd: float | None
    minimum: float
    maximum: float
    kind: str = field(default="descriptive", init=False)
    method: str = field(default="complete numeric observations; sample SD uses n-1", init=False)
    limitations: tuple[str, ...] = field(
        default=(
            "No distributional assumptions, confidence intervals or outlier rules are applied.",
            "Sample SD is undefined and reported as null when n is one.",
        ),
        init=False,
    )


@dataclass(frozen=True, slots=True)
class GroupValue:
    """One deterministic group aggregate."""

    group: CellValue
    n: int
    value: float | int


@dataclass(frozen=True, slots=True)
class GroupSummaryResult:
    """Grouped aggregation over complete numeric observations."""

    group_by: str
    value_column: str
    metric: GroupMetric
    missing_group: int
    missing_value: int
    groups: tuple[GroupValue, ...]
    kind: str = field(default="group_summary", init=False)
    method: str = field(default="complete-case grouped numeric aggregation", init=False)
    limitations: tuple[str, ...] = field(
        default=(
            "Groups are observed labels; no weighting, uncertainty or "
            "confounding adjustment is applied.",
            "Rows with a missing group or value are reported and excluded.",
        ),
        init=False,
    )


@dataclass(frozen=True, slots=True)
class PearsonCorrelationResult:
    """Pearson correlation for complete numeric pairs."""

    x_column: str
    y_column: str
    n: int
    incomplete_pairs: int
    pearson_r: float
    kind: str = field(default="pearson_correlation", init=False)
    method: str = field(default="complete-case Pearson product-moment correlation", init=False)
    limitations: tuple[str, ...] = field(
        default=(
            "Correlation measures linear association and does not establish causation.",
            "No confidence interval, p-value, multiplicity correction or "
            "missing-data model is applied.",
        ),
        init=False,
    )


@dataclass(frozen=True, slots=True)
class MetaAnalysisStudy:
    """One effect and standard error included in a meta-analysis."""

    label: str
    effect: float
    se: float


@dataclass(frozen=True, slots=True)
class RandomEffectsMetaAnalysisResult:
    """Classic DerSimonian-Laird normal-effects pooling result."""

    k: int
    excluded_incomplete: int
    fixed_effect: float
    pooled_effect: float
    pooled_se: float
    ci_95: tuple[float, float]
    q: float
    df: int
    i_squared_percent: float
    tau_squared: float
    studies: tuple[MetaAnalysisStudy, ...]
    kind: str = field(default="random_effects_meta_analysis", init=False)
    method: str = field(default="DerSimonian-Laird random effects with normal 95% CI", init=False)
    limitations: tuple[str, ...] = field(
        default=(
            "Effects and standard errors must already be independent, comparable and on one scale.",
            "No effect transformation, continuity correction or back-transformation is performed.",
            "DerSimonian-Laird can underestimate uncertainty, especially with "
            "few or heterogeneous studies.",
            "The interval uses a normal critical value, not Hartung-Knapp or a "
            "prediction interval.",
            "I-squared and Q are descriptive here; publication bias and study "
            "quality are not assessed.",
        ),
        init=False,
    )


type AnalysisResult = (
    MissingnessResult
    | DescriptiveResult
    | GroupSummaryResult
    | PearsonCorrelationResult
    | RandomEffectsMetaAnalysisResult
)


def _require_rows(
    rows: Sequence[Mapping[str, object]], *, limits: DatasetLimits
) -> tuple[Row, ...]:
    try:
        normalized = normalize_rows(rows, limits=limits)
    except DatasetImportError as exc:
        raise DatasetAnalysisError(str(exc)) from exc
    if not normalized:
        raise DatasetAnalysisError("analysis needs at least one data row")
    return normalized


def _require_column(rows: Sequence[Row], column: str) -> None:
    if not column or column not in rows[0]:
        raise DatasetAnalysisError(f"unknown column {column!r}")


def _numeric_values(rows: Sequence[Row], column: str) -> tuple[list[float], int]:
    _require_column(rows, column)
    values: list[float] = []
    missing = 0
    invalid = 0
    for row in rows:
        value = row[column]
        if is_missing(value):
            missing += 1
            continue
        number = as_number(value)
        if number is None:
            invalid += 1
        else:
            values.append(number)
    if invalid:
        raise DatasetAnalysisError(
            f"column {column!r} contains {invalid} non-missing non-numeric value(s)"
        )
    if not values:
        raise DatasetAnalysisError(f"column {column!r} contains no numeric values")
    return values, missing


def _finite(value: float, *, calculation: str) -> float:
    if not math.isfinite(value):
        raise DatasetAnalysisError(f"{calculation} overflowed for the supplied values")
    return value


def _safe_fmean(values: Sequence[float], *, calculation: str) -> float:
    try:
        return _finite(statistics.fmean(values), calculation=calculation)
    except OverflowError as exc:
        raise DatasetAnalysisError(f"{calculation} overflowed for the supplied values") from exc


def _missingness(rows: Sequence[Row]) -> MissingnessResult:
    return MissingnessResult(
        rows=len(rows),
        columns=tuple(
            MissingnessColumn(
                name=column,
                missing=(missing := sum(is_missing(row[column]) for row in rows)),
                percent=missing / len(rows) * 100,
            )
            for column in rows[0]
        ),
    )


def _descriptive(rows: Sequence[Row], recipe: DescriptiveRecipe) -> DescriptiveResult:
    values, missing = _numeric_values(rows, recipe.column)
    mean = _safe_fmean(values, calculation="mean")
    try:
        median = _finite(float(statistics.median(values)), calculation="median")
        sample_sd = (
            _finite(statistics.stdev(values), calculation="sample standard deviation")
            if len(values) > 1
            else None
        )
    except (OverflowError, statistics.StatisticsError) as exc:
        raise DatasetAnalysisError("descriptive statistics are undefined for these values") from exc
    return DescriptiveResult(
        column=recipe.column,
        n=len(values),
        missing=missing,
        mean=mean,
        median=median,
        sample_sd=sample_sd,
        minimum=min(values),
        maximum=max(values),
    )


def _group_sort_key(value: CellValue) -> str:
    type_name = type(value).__name__
    return f"{type_name}:{json.dumps(value, ensure_ascii=False, allow_nan=False)}"


def _group_summary(rows: Sequence[Row], recipe: GroupSummaryRecipe) -> GroupSummaryResult:
    _require_column(rows, recipe.group_by)
    _require_column(rows, recipe.value_column)
    if recipe.metric not in {"mean", "median", "sum", "count"}:
        raise DatasetAnalysisError("group metric must be mean, median, sum or count")

    groups: dict[str, tuple[CellValue, list[float]]] = {}
    missing_group = 0
    missing_value = 0
    invalid = 0
    for row in rows:
        group = row[recipe.group_by]
        raw_value = row[recipe.value_column]
        group_is_missing = is_missing(group)
        value_is_missing = is_missing(raw_value)
        if group_is_missing:
            missing_group += 1
        if value_is_missing:
            missing_value += 1
        if group_is_missing or value_is_missing:
            continue
        value = as_number(raw_value)
        if value is None:
            invalid += 1
            continue
        key = _group_sort_key(group)
        if key not in groups:
            groups[key] = (group, [])
        groups[key][1].append(value)
    if invalid:
        raise DatasetAnalysisError(
            f"column {recipe.value_column!r} contains {invalid} non-missing non-numeric value(s)"
        )
    if not groups:
        raise DatasetAnalysisError("the selected grouping contains no complete numeric values")

    aggregated: list[GroupValue] = []
    for key in sorted(groups):
        group, values = groups[key]
        if recipe.metric == "mean":
            result: float | int = _safe_fmean(values, calculation="group mean")
        elif recipe.metric == "median":
            result = _finite(float(statistics.median(values)), calculation="group median")
        elif recipe.metric == "sum":
            try:
                result = _finite(math.fsum(values), calculation="group sum")
            except (OverflowError, ValueError) as exc:
                raise DatasetAnalysisError("group sum overflowed for the supplied values") from exc
        else:
            result = len(values)
        aggregated.append(GroupValue(group=group, n=len(values), value=result))
    return GroupSummaryResult(
        group_by=recipe.group_by,
        value_column=recipe.value_column,
        metric=recipe.metric,
        missing_group=missing_group,
        missing_value=missing_value,
        groups=tuple(aggregated),
    )


def _pearson(rows: Sequence[Row], recipe: PearsonCorrelationRecipe) -> PearsonCorrelationResult:
    _require_column(rows, recipe.x_column)
    _require_column(rows, recipe.y_column)
    pairs: list[tuple[float, float]] = []
    incomplete = 0
    invalid = 0
    for row in rows:
        raw_x, raw_y = row[recipe.x_column], row[recipe.y_column]
        if is_missing(raw_x) or is_missing(raw_y):
            incomplete += 1
            continue
        x, y = as_number(raw_x), as_number(raw_y)
        if x is None or y is None:
            invalid += 1
            continue
        pairs.append((x, y))
    if invalid:
        raise DatasetAnalysisError(
            f"selected correlation columns contain {invalid} non-numeric complete pair(s)"
        )
    if len(pairs) < 2:
        raise DatasetAnalysisError("Pearson correlation needs at least two complete numeric pairs")
    xs, ys = zip(*pairs, strict=True)
    try:
        correlation = _finite(statistics.correlation(xs, ys), calculation="Pearson correlation")
    except statistics.StatisticsError as exc:
        raise DatasetAnalysisError(
            "Pearson correlation is undefined when either selected column is constant"
        ) from exc
    return PearsonCorrelationResult(
        x_column=recipe.x_column,
        y_column=recipe.y_column,
        n=len(pairs),
        incomplete_pairs=incomplete,
        pearson_r=correlation,
    )


def _inverse_variance_weights(standard_errors: Sequence[float]) -> list[float]:
    weights: list[float] = []
    for standard_error in standard_errors:
        variance = standard_error * standard_error
        if variance <= 0 or not math.isfinite(variance):
            raise DatasetAnalysisError("a standard error is outside the numerically safe range")
        weight = 1.0 / variance
        if not math.isfinite(weight):
            raise DatasetAnalysisError("an inverse-variance weight is outside the safe range")
        weights.append(weight)
    return weights


def _weighted_mean(effects: Sequence[float], weights: Sequence[float], *, name: str) -> float:
    try:
        total = math.fsum(weights)
        if total <= 0 or not math.isfinite(total):
            raise DatasetAnalysisError(f"{name} has an invalid total weight")
        result = math.fsum(
            effect * (weight / total) for effect, weight in zip(effects, weights, strict=True)
        )
    except (OverflowError, ValueError) as exc:
        raise DatasetAnalysisError(f"{name} overflowed for the supplied values") from exc
    return _finite(result, calculation=name)


def _meta_analysis(
    rows: Sequence[Row], recipe: RandomEffectsMetaAnalysisRecipe
) -> RandomEffectsMetaAnalysisResult:
    _require_column(rows, recipe.effect_column)
    _require_column(rows, recipe.se_column)
    if recipe.label_column is not None:
        _require_column(rows, recipe.label_column)

    studies: list[MetaAnalysisStudy] = []
    incomplete = 0
    invalid = 0
    for index, row in enumerate(rows, start=1):
        raw_effect, raw_se = row[recipe.effect_column], row[recipe.se_column]
        if is_missing(raw_effect) or is_missing(raw_se):
            incomplete += 1
            continue
        effect, standard_error = as_number(raw_effect), as_number(raw_se)
        if effect is None or standard_error is None:
            invalid += 1
            continue
        if standard_error <= 0:
            raise DatasetAnalysisError("meta-analysis standard errors must be strictly positive")
        raw_label = row[recipe.label_column] if recipe.label_column is not None else None
        label = str(raw_label) if not is_missing(raw_label) else str(index)
        studies.append(MetaAnalysisStudy(label=label, effect=effect, se=standard_error))
    if invalid:
        raise DatasetAnalysisError(
            f"selected meta-analysis columns contain {invalid} non-numeric complete row(s)"
        )
    if len(studies) < 2:
        raise DatasetAnalysisError(
            "random-effects meta-analysis needs at least two complete effects with positive SE"
        )

    effects = [study.effect for study in studies]
    standard_errors = [study.se for study in studies]
    fixed_weights = _inverse_variance_weights(standard_errors)
    fixed = _weighted_mean(effects, fixed_weights, name="fixed-effect estimate")
    try:
        q = _finite(
            math.fsum(
                weight * (effect - fixed) ** 2
                for effect, weight in zip(effects, fixed_weights, strict=True)
            ),
            calculation="Cochran Q",
        )
        weight_total = math.fsum(fixed_weights)
        proportions = [weight / weight_total for weight in fixed_weights]
        c_value = weight_total * (1.0 - math.fsum(value * value for value in proportions))
    except (OverflowError, ValueError) as exc:
        raise DatasetAnalysisError("heterogeneity calculation overflowed") from exc
    if c_value <= 0 or not math.isfinite(c_value):
        raise DatasetAnalysisError("heterogeneity is undefined for the supplied standard errors")

    degrees_of_freedom = len(studies) - 1
    tau_squared = _finite(max(0.0, (q - degrees_of_freedom) / c_value), calculation="tau-squared")
    random_standard_errors = [math.sqrt(se * se + tau_squared) for se in standard_errors]
    random_weights = _inverse_variance_weights(random_standard_errors)
    pooled = _weighted_mean(effects, random_weights, name="random-effects estimate")
    random_weight_total = math.fsum(random_weights)
    pooled_se = _finite(math.sqrt(1.0 / random_weight_total), calculation="pooled SE")
    z_975 = 1.959963984540054
    confidence_interval = (
        _finite(pooled - z_975 * pooled_se, calculation="lower confidence limit"),
        _finite(pooled + z_975 * pooled_se, calculation="upper confidence limit"),
    )
    i_squared = max(0.0, (q - degrees_of_freedom) / q * 100.0) if q > 0 else 0.0
    return RandomEffectsMetaAnalysisResult(
        k=len(studies),
        excluded_incomplete=incomplete,
        fixed_effect=fixed,
        pooled_effect=pooled,
        pooled_se=pooled_se,
        ci_95=confidence_interval,
        q=q,
        df=degrees_of_freedom,
        i_squared_percent=i_squared,
        tau_squared=tau_squared,
        studies=tuple(studies),
    )


def analyze_rows(
    rows: Sequence[Mapping[str, object]],
    recipe: AnalysisRecipe,
    *,
    limits: DatasetLimits = DEFAULT_LIMITS,
) -> AnalysisResult:
    """Execute one typed recipe over every supplied bounded row."""

    normalized = _require_rows(rows, limits=limits)
    if isinstance(recipe, MissingnessRecipe):
        return _missingness(normalized)
    if isinstance(recipe, DescriptiveRecipe):
        return _descriptive(normalized, recipe)
    if isinstance(recipe, GroupSummaryRecipe):
        return _group_summary(normalized, recipe)
    if isinstance(recipe, PearsonCorrelationRecipe):
        return _pearson(normalized, recipe)
    if isinstance(recipe, RandomEffectsMetaAnalysisRecipe):
        return _meta_analysis(normalized, recipe)
    raise TypeError(f"unsupported analysis recipe: {type(recipe).__name__}")


def analyze(dataset: ParsedDataset, recipe: AnalysisRecipe) -> AnalysisResult:
    """Execute a typed recipe over a parsed dataset's complete row set."""

    return analyze_rows(dataset.rows, recipe)
