"""Import, profile and visualize user-owned research data.

No dataframe dependency is required: CSV/TSV/JSON and the first worksheet of
ordinary XLSX workbooks are parsed with the standard library. Profiles are
bounded and deterministic so AI features receive exact values, missingness and
provenance instead of an opaque file or invented statistics.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont

from sixsentences_server.core.uploads import (
    UnsafeArchiveError,
    open_safe_zip,
    read_zip_member,
)

_MAX_ROWS = 10_000
_PROFILE_ROWS = 2_000
_MAX_XLSX_FILES = 1_000
_MAX_XLSX_UNPACKED = 100_000_000
_MAX_XLSX_MEMBER = 50_000_000


class DatasetImportError(ValueError):
    pass


# Every sentence a failed data operation is allowed to show a caller. The API
# answers with this catalogue's own copy of the text and never with the
# exception's: a message that was not written for disclosure — a future
# ``raise`` that quotes a path, a third-party library's wording — then cannot
# reach a response merely by travelling inside a ``DatasetImportError``.
NO_NUMERIC_COLUMN = "column {column} contains no numeric values"
_GENERIC_FAILURE = "this data operation could not run on the stored dataset"
_DISCLOSABLE_FAILURES: frozenset[str] = frozenset(
    {
        "analysis kind must be descriptive, missingness, group_summary, "
        "correlation or meta_analysis",
        "correlation is undefined for a constant column",
        "correlation needs at least two complete numeric pairs",
        "every JSON record must be an object",
        "group metric must be mean, median, sum or count",
        "JSON must contain a non-empty array of records",
        "meta-analysis needs at least two effects with positive SE",
        "not a readable JSON dataset",
        "not a readable XLSX workbook",
        "the dataset contains no rows",
        "the dataset has no profiled records to analyse",
        "the meta-analysis contains no studies to plot",
        "the selected columns contain no plottable values",
        "the selected grouping contains no numeric values",
        "the table must be UTF-8 or Latin-1 text",
        "the workbook contains no worksheet",
        "upload CSV, TSV, JSON or XLSX data",
    }
)


def dataset_failure_detail(error: DatasetImportError, *, column: str = "") -> str:
    """Return the curated, user-facing text for a failed data operation.

    ``column`` is the column the *caller* asked for, not one read back out of
    the exception, so the one message that names a column keeps naming it.
    """
    reported = str(error)
    for disclosable in _DISCLOSABLE_FAILURES:
        if disclosable == reported:
            return disclosable
    if column and reported == NO_NUMERIC_COLUMN.format(column=repr(column)):
        return NO_NUMERIC_COLUMN.format(column=repr(column))
    return _GENERIC_FAILURE


@dataclass
class ParsedDataset:
    format: str
    rows: list[dict[str, Any]]
    profile: dict[str, Any]


class _MetaStudy(TypedDict):
    label: str
    effect: float
    se: float


def _unique_headers(values: list[Any]) -> list[str]:
    seen: Counter[str] = Counter()
    headers: list[str] = []
    for index, value in enumerate(values):
        base = str(value or "").strip() or f"column_{index + 1}"
        seen[base] += 1
        headers.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return headers


def _tabular(rows: list[list[Any]]) -> list[dict[str, Any]]:
    useful = [row for row in rows if any(value not in (None, "") for value in row)]
    if not useful:
        raise DatasetImportError("the dataset contains no rows")
    headers = _unique_headers(useful[0])
    output: list[dict[str, Any]] = []
    for values in useful[1 : _MAX_ROWS + 1]:
        padded = (values + [None] * len(headers))[: len(headers)]
        output.append(dict(zip(headers, padded, strict=True)))
    return output


def _parse_delimited(blob: bytes, delimiter: str | None = None) -> list[dict[str, Any]]:
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = blob.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise DatasetImportError("the table must be UTF-8 or Latin-1 text") from exc
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return _tabular([list(row) for row in reader])


def _parse_json(blob: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetImportError("not a readable JSON dataset") from exc
    if isinstance(payload, dict):
        for key in ("data", "records", "results", "rows"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            if payload and all(isinstance(value, list) for value in payload.values()):
                width = max(len(value) for value in payload.values())
                payload = [
                    {
                        key: value[index] if index < len(value) else None
                        for key, value in payload.items()
                    }
                    for index in range(width)
                ]
    if not isinstance(payload, list) or not payload:
        raise DatasetImportError("JSON must contain a non-empty array of records")
    if not all(isinstance(row, dict) for row in payload[:_MAX_ROWS]):
        raise DatasetImportError("every JSON record must be an object")
    keys: list[str] = []
    for row in payload[:_MAX_ROWS]:
        for key in row:
            if str(key) not in keys:
                keys.append(str(key))
    return [
        {key: row.get(key) for key in keys} for row in payload[:_MAX_ROWS] if isinstance(row, dict)
    ]


def _xlsx_cell_value(cell: ElementTree.Element, shared: list[str], ns: str) -> Any:
    kind = cell.get("t", "")
    value = cell.find(f"{ns}v")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{ns}t"))
    raw = (value.text or "") if value is not None else ""
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return raw == "1"
    if raw == "":
        return None
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    index = 0
    for char in letters.group(0):
        index = index * 26 + ord(char) - 64
    return index - 1


def _parse_xlsx(blob: bytes) -> list[dict[str, Any]]:
    try:
        archive = open_safe_zip(
            blob,
            max_files=_MAX_XLSX_FILES,
            max_uncompressed_bytes=_MAX_XLSX_UNPACKED,
            max_member_bytes=_MAX_XLSX_MEMBER,
        )
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(
                read_zip_member(
                    archive,
                    "xl/sharedStrings.xml",
                    max_bytes=_MAX_XLSX_MEMBER,
                )
            )
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            shared = [
                "".join(node.text or "" for node in item.findall(f".//{ns}t")) for item in root
            ]
        sheets = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if not sheets:
            raise DatasetImportError("the workbook contains no worksheet")
        root = ElementTree.fromstring(
            read_zip_member(archive, sheets[0], max_bytes=_MAX_XLSX_MEMBER)
        )
        archive.close()
    except (UnsafeArchiveError, ElementTree.ParseError, KeyError) as exc:
        raise DatasetImportError("not a readable XLSX workbook") from exc
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    matrix: list[list[Any]] = []
    for row in root.findall(f".//{ns}sheetData/{ns}row")[: _MAX_ROWS + 1]:
        cells: dict[int, Any] = {}
        for cell in row.findall(f"{ns}c"):
            cells[_column_index(cell.get("r", "A1"))] = _xlsx_cell_value(cell, shared, ns)
        width = max(cells, default=-1) + 1
        matrix.append([cells.get(index) for index in range(width)])
    return _tabular(matrix)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def profile_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = list(rows[0]) if rows else []
    columns: list[dict[str, Any]] = []
    for key in keys:
        values = [row.get(key) for row in rows]
        present = [value for value in values if value not in (None, "")]
        numbers = [number for value in present if (number := _as_number(value)) is not None]
        numeric = bool(present) and len(numbers) / len(present) >= 0.9
        column: dict[str, Any] = {
            "name": key,
            "type": "number" if numeric else "text",
            "missing": len(values) - len(present),
            "unique": len({str(value) for value in present}),
        }
        if numeric and numbers:
            column["stats"] = {
                "min": min(numbers),
                "max": max(numbers),
                "mean": statistics.fmean(numbers),
                "median": statistics.median(numbers),
            }
        else:
            column["top"] = [
                {"value": value, "count": count}
                for value, count in Counter(str(value) for value in present).most_common(5)
            ]
        columns.append(column)
    return {
        "columns": columns,
        "preview": rows[:20],
        "records": rows[:_PROFILE_ROWS],
        "profiled_rows": min(len(rows), _PROFILE_ROWS),
    }


def parse_dataset(blob: bytes, filename: str) -> ParsedDataset:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        rows, format_name = _parse_delimited(blob), "csv"
    elif suffix in {".tsv", ".tab"}:
        rows, format_name = _parse_delimited(blob, "\t"), "tsv"
    elif suffix == ".json":
        rows, format_name = _parse_json(blob), "json"
    elif suffix == ".xlsx":
        rows, format_name = _parse_xlsx(blob), "xlsx"
    else:
        raise DatasetImportError("upload CSV, TSV, JSON or XLSX data")
    return ParsedDataset(format=format_name, rows=rows, profile=profile_rows(rows))


def dataset_context(name: str, description: str, provenance: str, profile: dict[str, Any]) -> str:
    columns = profile.get("columns") or []
    lines = [f"Dataset: {name}"]
    if description:
        lines.append(f"Description: {description}")
    if provenance:
        lines.append(f"Provenance: {provenance}")
    for column in columns[:30]:
        detail = f"{column.get('name')} ({column.get('type')}, missing {column.get('missing', 0)})"
        if column.get("stats"):
            stats = column["stats"]
            detail += (
                f": min {stats['min']:.4g}, max {stats['max']:.4g}, "
                f"mean {stats['mean']:.4g}, median {stats['median']:.4g}"
            )
        lines.append("- " + detail)
    preview = profile.get("preview") or []
    if preview:
        lines.append("Exact preview records:\n" + json.dumps(preview[:12], ensure_ascii=False))
    return "\n".join(lines)


def run_analysis(
    profile: dict[str, Any], *, kind: str, definition: dict[str, Any]
) -> dict[str, Any]:
    """Execute a small, deterministic analysis recipe over profiled records.

    The result carries every input choice and exact derived number. This is
    deliberately narrower than arbitrary generated code: publication values
    must be reproducible and reject malformed columns instead of guessing.
    """

    records = list(profile.get("records") or [])
    if not records:
        raise DatasetImportError("the dataset has no profiled records to analyse")
    if kind == "missingness":
        columns = list(profile.get("columns") or [])
        return {
            "kind": kind,
            "rows": len(records),
            "columns": [
                {
                    "name": column.get("name"),
                    "missing": int(column.get("missing") or 0),
                    "percent": round(
                        int(column.get("missing") or 0) / max(len(records), 1) * 100, 4
                    ),
                }
                for column in columns
            ],
        }
    if kind == "descriptive":
        column = str(definition.get("column") or "")
        values = [
            number for record in records if (number := _as_number(record.get(column))) is not None
        ]
        if not values:
            raise DatasetImportError(NO_NUMERIC_COLUMN.format(column=repr(column)))
        deviation = statistics.stdev(values) if len(values) > 1 else 0.0
        return {
            "kind": kind,
            "column": column,
            "n": len(values),
            "missing": len(records) - len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "sd": deviation,
            "min": min(values),
            "max": max(values),
        }
    if kind == "group_summary":
        group_by = str(definition.get("group_by") or "")
        value_column = str(definition.get("value_column") or "")
        metric = str(definition.get("metric") or "mean")
        if metric not in {"mean", "median", "sum", "count"}:
            raise DatasetImportError("group metric must be mean, median, sum or count")
        groups: dict[str, list[float]] = {}
        for record in records:
            group = record.get(group_by)
            value = _as_number(record.get(value_column))
            if group not in (None, "") and value is not None:
                groups.setdefault(str(group), []).append(value)
        if not groups:
            raise DatasetImportError("the selected grouping contains no numeric values")
        aggregators: dict[str, Callable[[list[float]], float | int]] = {
            "mean": lambda values: statistics.fmean(values),
            "median": lambda values: statistics.median(values),
            "sum": lambda values: sum(values),
            "count": lambda values: len(values),
        }
        aggregate = aggregators[metric]
        return {
            "kind": kind,
            "group_by": group_by,
            "value_column": value_column,
            "metric": metric,
            "groups": [
                {"group": group, "n": len(values), "value": aggregate(values)}
                for group, values in sorted(groups.items())
            ],
        }
    if kind == "correlation":
        x_column = str(definition.get("x_column") or "")
        y_column = str(definition.get("y_column") or "")
        pairs = [
            (x, y)
            for record in records
            if (x := _as_number(record.get(x_column))) is not None
            and (y := _as_number(record.get(y_column))) is not None
        ]
        if len(pairs) < 2:
            raise DatasetImportError("correlation needs at least two complete numeric pairs")
        xs, ys = zip(*pairs, strict=True)
        x_mean, y_mean = statistics.fmean(xs), statistics.fmean(ys)
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
        denominator = math.sqrt(
            sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys)
        )
        if denominator == 0:
            raise DatasetImportError("correlation is undefined for a constant column")
        return {
            "kind": kind,
            "x_column": x_column,
            "y_column": y_column,
            "n": len(pairs),
            "pearson_r": numerator / denominator,
        }
    if kind == "meta_analysis":
        effect_column = str(definition.get("effect_column") or "")
        se_column = str(definition.get("se_column") or "")
        studies: list[_MetaStudy] = [
            {
                "label": str(record.get(definition.get("label_column")) or index + 1),
                "effect": effect,
                "se": se,
            }
            for index, record in enumerate(records)
            if (effect := _as_number(record.get(effect_column))) is not None
            and (se := _as_number(record.get(se_column))) is not None
            and se > 0
        ]
        if len(studies) < 2:
            raise DatasetImportError("meta-analysis needs at least two effects with positive SE")
        fixed_weights = [1 / float(study["se"]) ** 2 for study in studies]
        fixed = sum(
            weight * float(study["effect"])
            for weight, study in zip(fixed_weights, studies, strict=True)
        ) / sum(fixed_weights)
        q = sum(
            weight * (float(study["effect"]) - fixed) ** 2
            for weight, study in zip(fixed_weights, studies, strict=True)
        )
        df = len(studies) - 1
        c = sum(fixed_weights) - sum(weight**2 for weight in fixed_weights) / sum(fixed_weights)
        tau_squared = max(0.0, (q - df) / c) if c > 0 else 0.0
        random_weights = [1 / (float(study["se"]) ** 2 + tau_squared) for study in studies]
        pooled = sum(
            weight * float(study["effect"])
            for weight, study in zip(random_weights, studies, strict=True)
        ) / sum(random_weights)
        pooled_se = math.sqrt(1 / sum(random_weights))
        return {
            "kind": kind,
            "model": "DerSimonian-Laird random effects",
            "k": len(studies),
            "pooled_effect": pooled,
            "se": pooled_se,
            "ci_95": [pooled - 1.96 * pooled_se, pooled + 1.96 * pooled_se],
            "q": q,
            "df": df,
            "i_squared": max(0.0, (q - df) / q * 100) if q > 0 else 0.0,
            "tau_squared": tau_squared,
            "studies": studies,
        }
    raise DatasetImportError(
        "analysis kind must be descriptive, missingness, group_summary, "
        "correlation or meta_analysis"
    )


def render_dataset_chart(
    profile: dict[str, Any], *, x_column: str, y_column: str, kind: str, title: str
) -> bytes:
    records = profile.get("records") or []
    raw_points = [(row.get(x_column), _as_number(row.get(y_column))) for row in records]
    points: list[tuple[Any, float]] = [
        (x, y) for x, y in raw_points if x not in (None, "") and y is not None
    ]
    if not points:
        raise DatasetImportError("the selected columns contain no plottable values")
    if kind == "bar":
        grouped: dict[str, list[float]] = {}
        for label, value in points:
            grouped.setdefault(str(label), []).append(float(value))
        points = [(label, statistics.fmean(values)) for label, values in list(grouped.items())[:40]]
    else:
        points = points[:200]
    width, height = 1600, 1000
    left, right, top, bottom = 170, 80, 120, 170
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    font, small, heading = _chart_fonts()
    draw.text((left, 42), title[:90], fill="#102d26", font=heading)
    draw.line((left, top, left, height - bottom), fill="#173f35", width=3)
    draw.line((left, height - bottom, width - right, height - bottom), fill="#173f35", width=3)
    values = [float(y) for _, y in points]
    low, high = min(values), max(values)
    if math.isclose(low, high):
        low, high = low - 1, high + 1
    padding = (high - low) * 0.08
    low, high = min(0, low - padding), high + padding

    def py(value: float) -> float:
        return top + (high - value) / (high - low) * (height - top - bottom)

    for tick in range(6):
        value = low + (high - low) * tick / 5
        y = py(value)
        draw.line((left, y, width - right, y), fill="#dce6e1", width=2)
        draw.text((30, y - 12), f"{value:.4g}", fill="#63736e", font=small)
    plot_width = width - left - right
    if kind == "bar":
        slot = plot_width / len(points)
        for index, (label, value) in enumerate(points):
            x0 = left + index * slot + slot * 0.16
            x1 = left + (index + 1) * slot - slot * 0.16
            y0, y1 = sorted((py(float(value)), py(0)))
            draw.rounded_rectangle((x0, y0, x1, y1), radius=5, fill="#527d6d")
            if len(points) <= 16:
                draw.text(
                    (x0, height - bottom + 18),
                    str(label)[:12],
                    fill="#63736e",
                    font=small,
                )
    else:
        numeric_x = [_as_number(x) for x, _ in points]
        use_numeric = all(value is not None for value in numeric_x)
        x_values = (
            [float(value) for value in numeric_x if value is not None]
            if use_numeric
            else list(range(len(points)))
        )
        x_low, x_high = min(x_values), max(x_values)
        if math.isclose(x_low, x_high):
            x_low, x_high = x_low - 1, x_high + 1
        coords = [
            (left + (x - x_low) / (x_high - x_low) * plot_width, py(float(point[1])))
            for x, point in zip(x_values, points, strict=True)
        ]
        if kind == "line" and len(coords) > 1:
            draw.line(coords, fill="#315f52", width=5)
        for x, y in coords:
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#527d6d", outline="white", width=2)
    draw.text((width // 2 - 100, height - 72), x_column[:40], fill="#173f35", font=font)
    draw.text((18, 82), y_column[:40], fill="#173f35", font=font)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _chart_fonts() -> tuple[Any, Any, Any]:
    try:
        return (
            ImageFont.truetype("DejaVuSans.ttf", 25),
            ImageFont.truetype("DejaVuSans.ttf", 20),
            ImageFont.truetype("DejaVuSans.ttf", 38),
        )
    except OSError:
        default = ImageFont.load_default()
        return default, default, default


def render_forest_plot(analysis: dict[str, Any], title: str) -> bytes:
    """Exact forest plot for a run_analysis meta_analysis result.

    Study squares scale with random-effects weight; the pooled effect is the
    diamond below. Every number comes from the deterministic recipe."""
    studies = list(analysis.get("studies") or [])
    if not studies:
        raise DatasetImportError("the meta-analysis contains no studies to plot")
    effects = [float(study["effect"]) for study in studies]
    ses = [float(study["se"]) for study in studies]
    tau_squared = float(analysis.get("tau_squared") or 0.0)
    weights = [1 / (se**2 + tau_squared) for se in ses]
    ci_95 = analysis.get("ci_95") or [analysis.get("pooled_effect", 0.0)] * 2
    low = min(min(e - 1.96 * se for e, se in zip(effects, ses, strict=True)), float(ci_95[0]))
    high = max(max(e + 1.96 * se for e, se in zip(effects, ses, strict=True)), float(ci_95[1]))
    if math.isclose(low, high):
        low, high = low - 1, high + 1
    span = high - low
    low, high = low - span * 0.06, high + span * 0.06

    font, small, heading = _chart_fonts()
    width = 1600
    left, right, top, row_h = 420, 560, 150, 74
    height = top + row_h * (len(studies) + 1) + 150
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((80, 52), title[:80], fill="#102d26", font=heading)
    draw.text((80, 108), "Study", fill="#63736e", font=small)
    draw.text((width - right + 60, 108), "Effect [95% CI]", fill="#63736e", font=small)

    def px(value: float) -> float:
        return left + (value - low) / (high - low) * (width - left - right)

    pooled = float(analysis.get("pooled_effect") or 0.0)
    axis_y = top + row_h * (len(studies) + 1) + 40
    draw.line((left, axis_y, width - right, axis_y), fill="#173f35", width=3)
    for tick in range(7):
        value = low + (high - low) * tick / 6
        x = px(value)
        draw.line((x, axis_y, x, axis_y + 10), fill="#173f35", width=2)
        draw.text((x - 30, axis_y + 18), f"{value:.3g}", fill="#63736e", font=small)
    zero = 0.0 if low < 0 < high else None
    if zero is not None:
        draw.line((px(0.0), top - 20, px(0.0), axis_y), fill="#c2d2ca", width=2)

    max_weight = max(weights)
    for index, study in enumerate(studies):
        y = top + index * row_h + row_h // 2
        effect, se = effects[index], ses[index]
        lo, hi = effect - 1.96 * se, effect + 1.96 * se
        draw.line((px(lo), y, px(hi), y), fill="#315f52", width=3)
        size = 8 + 20 * math.sqrt(weights[index] / max_weight)
        draw.rectangle(
            (px(effect) - size / 2, y - size / 2, px(effect) + size / 2, y + size / 2),
            fill="#527d6d",
        )
        label = str(study.get("label") or index + 1)[:38]
        draw.text((80, y - 14), label, fill="#102d26", font=font)
        draw.text(
            (width - right + 60, y - 14),
            f"{effect:.3g} [{lo:.3g}, {hi:.3g}]",
            fill="#63736e",
            font=small,
        )

    diamond_y = top + len(studies) * row_h + row_h // 2
    cx, half, rise = px(pooled), (px(float(ci_95[1])) - px(float(ci_95[0]))) / 2, 20
    draw.polygon(
        [
            (cx - half, diamond_y),
            (cx, diamond_y - rise),
            (cx + half, diamond_y),
            (cx, diamond_y + rise),
        ],
        fill="#173f35",
    )
    draw.text((80, diamond_y - 14), "Pooled (random effects)", fill="#102d26", font=font)
    draw.text(
        (width - right + 60, diamond_y - 14),
        f"{pooled:.3g} [{float(ci_95[0]):.3g}, {float(ci_95[1]):.3g}]",
        fill="#63736e",
        font=small,
    )
    i_squared = float(analysis.get("i_squared") or 0.0)
    draw.text(
        (80, height - 56),
        f"k={len(studies)} · I² {i_squared:.1f}% · τ² {tau_squared:.3g} · DerSimonian-Laird",
        fill="#63736e",
        font=small,
    )
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_funnel_plot(analysis: dict[str, Any], title: str) -> bytes:
    """Exact funnel plot (effect vs standard error, inverted axis) with the
    pseudo 95% confidence funnel around the pooled effect."""
    studies = list(analysis.get("studies") or [])
    if not studies:
        raise DatasetImportError("the meta-analysis contains no studies to plot")
    effects = [float(study["effect"]) for study in studies]
    ses = [float(study["se"]) for study in studies]
    pooled = float(analysis.get("pooled_effect") or 0.0)
    font, small, heading = _chart_fonts()
    width, height = 1600, 1000
    left, right, top, bottom = 170, 80, 140, 150
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((left, 52), title[:80], fill="#102d26", font=heading)

    x_low, x_high = min(effects + [pooled]), max(effects + [pooled])
    x_pad = max((x_high - x_low) * 0.35, 0.05)
    x_low, x_high = x_low - x_pad, x_high + x_pad
    se_max = max(ses) * 1.15

    def px(value: float) -> float:
        return left + (value - x_low) / (x_high - x_low) * (width - left - right)

    def py(se: float) -> float:
        return top + se / se_max * (height - top - bottom)

    draw.line((left, top, left, height - bottom), fill="#173f35", width=3)
    draw.line((left, height - bottom, width - right, height - bottom), fill="#173f35", width=3)
    for tick in range(6):
        value = x_low + (x_high - x_low) * tick / 5
        x = px(value)
        draw.line((x, height - bottom, x, height - bottom + 10), fill="#173f35", width=2)
        draw.text((x - 30, height - bottom + 18), f"{value:.3g}", fill="#63736e", font=small)
        se = se_max * tick / 5
        y = py(se)
        draw.line((left - 10, y, left, y), fill="#173f35", width=2)
        draw.text((40, y - 12), f"{se:.3g}", fill="#63736e", font=small)

    draw.line((px(pooled), top, px(pooled), height - bottom), fill="#315f52", width=3)
    for sign in (-1, 1):
        draw.line(
            (px(pooled), top, px(pooled + sign * 1.96 * se_max), height - bottom),
            fill="#c2d2ca",
            width=3,
        )
    for effect, se in zip(effects, ses, strict=True):
        x, y = px(effect), py(se)
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill="#527d6d", outline="white", width=2)
    draw.text((width // 2 - 60, height - 62), "Effect", fill="#173f35", font=font)
    draw.text((30, top - 40), "SE", fill="#173f35", font=font)
    draw.text(
        (width - right - 420, height - 62),
        f"pooled {pooled:.3g} · k={len(studies)}",
        fill="#63736e",
        font=small,
    )
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()
