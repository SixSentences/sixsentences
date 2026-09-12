"""Bounded CSV, TSV, JSON and first-worksheet XLSX importers.

The importer intentionally supports a conservative tabular subset. It does not
evaluate spreadsheet formulae, infer dates from cell styles, follow external
relationships or coerce locale-specific numbers. XLSX formula cells expose only
their cached value, when one is present.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import posixpath
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from sixsentences.data.archives import (
    UnsafeArchiveError,
    ZipSafetyLimits,
    open_safe_zip,
    read_zip_member,
)
from sixsentences.data.errors import DatasetImportError
from sixsentences.data.profile import is_missing, normalize_rows, profile_rows
from sixsentences.data.types import (
    DEFAULT_LIMITS,
    CellValue,
    DatasetFormat,
    DatasetLimits,
    ParsedDataset,
    Row,
)

_SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DOCUMENT_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REFERENCE = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]*)$")


class _DuplicateJsonKey(ValueError):
    pass


def _unique_headers(values: Sequence[CellValue], *, limits: DatasetLimits) -> list[str]:
    if len(values) > limits.max_columns:
        raise DatasetImportError(f"dataset contains more than {limits.max_columns} columns")
    used: set[str] = set()
    headers: list[str] = []
    for index, value in enumerate(values):
        base = str(value).strip() if value is not None else ""
        base = base or f"column_{index + 1}"
        if len(base) > limits.max_cell_characters:
            raise DatasetImportError("a column name exceeds the configured cell limit")
        header = base
        suffix = 2
        while header in used:
            header = f"{base}_{suffix}"
            suffix += 1
        if len(header) > limits.max_cell_characters:
            raise DatasetImportError("a unique column name exceeds the configured cell limit")
        used.add(header)
        headers.append(header)
    if not headers:
        raise DatasetImportError("the dataset contains no columns")
    return headers


def _is_blank_row(values: Sequence[CellValue]) -> bool:
    return not values or all(is_missing(value) for value in values)


def _tabular(matrix: Iterable[Sequence[CellValue]], *, limits: DatasetLimits) -> tuple[Row, ...]:
    useful: list[Sequence[CellValue]] = []
    for values in matrix:
        if _is_blank_row(values):
            continue
        useful.append(values)
        if len(useful) > limits.max_rows + 1:
            raise DatasetImportError(f"dataset contains more than {limits.max_rows} data rows")
    if not useful:
        raise DatasetImportError("the dataset contains no rows")

    headers = _unique_headers(useful[0], limits=limits)
    output: list[Row] = []
    for row_number, values in enumerate(useful[1:], start=2):
        meaningful_width = len(values)
        while meaningful_width and is_missing(values[meaningful_width - 1]):
            meaningful_width -= 1
        if meaningful_width > len(headers):
            raise DatasetImportError(f"data row {row_number} is wider than the header")
        padded = list(values[: len(headers)]) + [None] * max(0, len(headers) - len(values))
        output.append(dict(zip(headers, padded, strict=True)))
    if not output:
        raise DatasetImportError("the dataset contains a header but no data rows")
    return normalize_rows(output, limits=limits)


def _parse_delimited(
    blob: bytes, *, limits: DatasetLimits, delimiter: str | None = None
) -> tuple[Row, ...]:
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("latin-1")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        matrix: list[Sequence[CellValue]] = []
        for physical_row, row in enumerate(reader, start=1):
            if physical_row > limits.max_rows + 10_001:
                raise DatasetImportError("the table contains too many physical rows")
            if len(row) > limits.max_columns:
                raise DatasetImportError(
                    f"row {physical_row} contains more than {limits.max_columns} columns"
                )
            if any(len(value) > limits.max_cell_characters for value in row):
                raise DatasetImportError(f"a cell in row {physical_row} exceeds the cell limit")
            matrix.append(row)
    except csv.Error as exc:
        raise DatasetImportError("not a readable delimited table") from exc
    return _tabular(matrix, limits=limits)


def _reject_json_constant(value: str) -> None:
    raise DatasetImportError(f"JSON numeric constant {value!r} is not finite")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _parse_json(blob: bytes, *, limits: DatasetLimits) -> tuple[Row, ...]:
    try:
        payload = json.loads(
            blob.decode("utf-8-sig"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except DatasetImportError:
        raise
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise DatasetImportError("not a readable, unambiguous JSON dataset") from exc

    if isinstance(payload, dict):
        wrappers = [
            payload[key]
            for key in ("data", "records", "results", "rows")
            if isinstance(payload.get(key), list)
        ]
        if len(wrappers) > 1:
            raise DatasetImportError("JSON contains multiple ambiguous record arrays")
        if wrappers:
            payload = wrappers[0]
        elif payload and all(isinstance(value, list) for value in payload.values()):
            columns = payload
            width = max((len(value) for value in columns.values()), default=0)
            if width > limits.max_rows:
                raise DatasetImportError(f"dataset contains more than {limits.max_rows} data rows")
            payload = [
                {
                    key: values[index] if index < len(values) else None
                    for key, values in columns.items()
                }
                for index in range(width)
            ]

    if not isinstance(payload, list) or not payload:
        raise DatasetImportError("JSON must contain a non-empty array of records")
    if len(payload) > limits.max_rows:
        raise DatasetImportError(f"dataset contains more than {limits.max_rows} data rows")
    if not all(isinstance(row, dict) for row in payload):
        raise DatasetImportError("every JSON record must be an object")
    rows = [dict(row) for row in payload]
    return normalize_rows(rows, limits=limits)


def _parse_xml(payload: bytes, *, part: str) -> ElementTree.Element:
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper or b"\x00" in payload:
        raise DatasetImportError(f"XLSX part {part!r} contains unsupported XML declarations")
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise DatasetImportError(f"XLSX part {part!r} is malformed XML") from exc


def _first_worksheet_name(
    names: set[str], workbook: ElementTree.Element, relationships: ElementTree.Element
) -> str:
    first_sheet = workbook.find(f".//{_SHEET_NS}sheets/{_SHEET_NS}sheet")
    if first_sheet is None:
        raise DatasetImportError("the workbook contains no worksheet")
    relationship_id = first_sheet.get(f"{_DOCUMENT_REL_NS}id", "")
    if not relationship_id:
        raise DatasetImportError("the first worksheet has no relationship")

    target = ""
    for relationship in relationships.findall(f"{_PACKAGE_REL_NS}Relationship"):
        if relationship.get("Id") != relationship_id:
            continue
        if relationship.get("TargetMode", "Internal") != "Internal":
            raise DatasetImportError("external worksheet relationships are not supported")
        target = relationship.get("Target", "")
        break
    if not target or "\\" in target or "\x00" in target:
        raise DatasetImportError("the first worksheet relationship is invalid")
    if any(part in {"", ".", ".."} for part in target.lstrip("/").split("/")):
        raise DatasetImportError("the first worksheet relationship is unsafe or missing")
    candidate = target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
    candidate = posixpath.normpath(candidate)
    if (
        candidate.startswith("../")
        or not candidate.startswith("xl/worksheets/")
        or not candidate.endswith(".xml")
        or candidate not in names
    ):
        raise DatasetImportError("the first worksheet relationship is unsafe or missing")
    return candidate


def _shared_strings(root: ElementTree.Element, *, limits: DatasetLimits) -> list[str]:
    items = root.findall(f"{_SHEET_NS}si")
    if len(items) > limits.max_xlsx_shared_strings:
        raise DatasetImportError("the workbook contains too many shared strings")
    shared: list[str] = []
    for item in items:
        value = "".join(node.text or "" for node in item.findall(f".//{_SHEET_NS}t"))
        if len(value) > limits.max_cell_characters:
            raise DatasetImportError("an XLSX shared string exceeds the cell limit")
        shared.append(value)
    return shared


def _xlsx_cell_value(
    cell: ElementTree.Element, shared: Sequence[str], *, limits: DatasetLimits
) -> CellValue:
    kind = cell.get("t", "")
    value = cell.find(f"{_SHEET_NS}v")
    if kind == "inlineStr":
        parsed: CellValue = "".join(node.text or "" for node in cell.findall(f".//{_SHEET_NS}t"))
    else:
        raw = (value.text or "") if value is not None else ""
        if kind == "s":
            try:
                shared_index = int(raw)
                if shared_index < 0:
                    raise ValueError
                parsed = shared[shared_index]
            except (ValueError, IndexError) as exc:
                raise DatasetImportError("an XLSX shared-string reference is invalid") from exc
        elif kind == "b":
            if raw not in {"0", "1"}:
                raise DatasetImportError("an XLSX boolean cell is invalid")
            parsed = raw == "1"
        elif raw == "":
            parsed = None
        elif kind in {"str", "e"}:
            parsed = raw
        else:
            try:
                number = float(raw)
                parsed = int(number) if number.is_integer() else number
            except ValueError:
                parsed = raw
    if isinstance(parsed, str) and len(parsed) > limits.max_cell_characters:
        raise DatasetImportError("an XLSX cell exceeds the cell limit")
    return parsed


def _column_index(reference: str) -> int:
    match = _CELL_REFERENCE.fullmatch(reference)
    if match is None:
        raise DatasetImportError("an XLSX cell reference is invalid")
    index = 0
    for character in match.group(1).upper():
        index = index * 26 + ord(character) - 64
    return index - 1


def _worksheet_matrix(
    root: ElementTree.Element, shared: Sequence[str], *, limits: DatasetLimits
) -> list[list[CellValue]]:
    row_elements = root.findall(f".//{_SHEET_NS}sheetData/{_SHEET_NS}row")
    if len(row_elements) > limits.max_rows + 1:
        raise DatasetImportError(f"dataset contains more than {limits.max_rows} data rows")
    matrix: list[list[CellValue]] = []
    for row_number, row in enumerate(row_elements, start=1):
        cells: dict[int, CellValue] = {}
        next_index = 0
        for cell in row.findall(f"{_SHEET_NS}c"):
            reference = cell.get("r")
            index = _column_index(reference) if reference else next_index
            if index >= limits.max_columns:
                raise DatasetImportError(
                    f"XLSX row {row_number} exceeds the {limits.max_columns}-column limit"
                )
            if index in cells:
                raise DatasetImportError(f"XLSX row {row_number} repeats a cell reference")
            cells[index] = _xlsx_cell_value(cell, shared, limits=limits)
            next_index = index + 1
        width = max(cells, default=-1) + 1
        matrix.append([cells.get(index) for index in range(width)])
    return matrix


def _parse_xlsx(blob: bytes, *, limits: DatasetLimits) -> tuple[Row, ...]:
    zip_limits = ZipSafetyLimits(
        max_archive_bytes=limits.max_source_bytes,
        max_entries=limits.max_xlsx_entries,
        max_uncompressed_bytes=limits.max_xlsx_uncompressed_bytes,
        max_member_bytes=limits.max_xlsx_member_bytes,
        max_compression_ratio=limits.max_zip_compression_ratio,
    )
    try:
        with open_safe_zip(blob, limits=zip_limits) as archive:
            names = set(archive.namelist())
            required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
            if not required.issubset(names):
                raise DatasetImportError("the XLSX workbook metadata is incomplete")
            workbook = _parse_xml(
                read_zip_member(archive, "xl/workbook.xml", max_bytes=limits.max_xlsx_member_bytes),
                part="xl/workbook.xml",
            )
            relationships = _parse_xml(
                read_zip_member(
                    archive,
                    "xl/_rels/workbook.xml.rels",
                    max_bytes=limits.max_xlsx_member_bytes,
                ),
                part="xl/_rels/workbook.xml.rels",
            )
            worksheet_name = _first_worksheet_name(names, workbook, relationships)
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                shared = _shared_strings(
                    _parse_xml(
                        read_zip_member(
                            archive,
                            "xl/sharedStrings.xml",
                            max_bytes=limits.max_xlsx_member_bytes,
                        ),
                        part="xl/sharedStrings.xml",
                    ),
                    limits=limits,
                )
            worksheet = _parse_xml(
                read_zip_member(archive, worksheet_name, max_bytes=limits.max_xlsx_member_bytes),
                part=worksheet_name,
            )
    except DatasetImportError:
        raise
    except UnsafeArchiveError as exc:
        raise DatasetImportError("not a safe, readable XLSX workbook") from exc
    return _tabular(_worksheet_matrix(worksheet, shared, limits=limits), limits=limits)


def parse_dataset(
    blob: bytes, filename: str, *, limits: DatasetLimits = DEFAULT_LIMITS
) -> ParsedDataset:
    """Parse a complete bounded dataset selected by its file extension."""

    if not isinstance(blob, bytes):
        raise TypeError("blob must be bytes")
    if len(blob) > limits.max_source_bytes:
        raise DatasetImportError(
            f"dataset exceeds the {limits.max_source_bytes}-byte encoded size limit"
        )
    suffix = Path(filename).suffix.lower()
    rows: tuple[Row, ...]
    format_name: DatasetFormat
    if suffix == ".csv":
        rows, format_name = _parse_delimited(blob, limits=limits), "csv"
    elif suffix in {".tsv", ".tab"}:
        rows, format_name = _parse_delimited(blob, limits=limits, delimiter="\t"), "tsv"
    elif suffix == ".json":
        rows, format_name = _parse_json(blob, limits=limits), "json"
    elif suffix == ".xlsx":
        rows, format_name = _parse_xlsx(blob, limits=limits), "xlsx"
    else:
        raise DatasetImportError("expected a .csv, .tsv, .tab, .json or .xlsx dataset")
    notes = [
        "The complete accepted row set is retained; inputs over a configured limit are rejected.",
        "No missing-value sentinels, units, measurement scales or locale-specific "
        "numbers are inferred.",
        "Blank headers receive deterministic names and duplicate headers receive numeric suffixes.",
    ]
    if format_name in {"csv", "tsv"}:
        notes.append("Delimited cells remain text; numeric conversion occurs only inside a recipe.")
    elif format_name == "json":
        notes.append("JSON table cells must be scalar; nested arrays and objects are rejected.")
    else:
        notes.extend(
            (
                "Only the first worksheet is imported; charts and other workbook "
                "parts are ignored.",
                "Formulae are not evaluated and only cached cell values are visible.",
                "Excel date and unit semantics are not inferred from cell styles.",
            )
        )
    return ParsedDataset(
        filename=Path(filename).name,
        format=format_name,
        byte_count=len(blob),
        sha256=hashlib.sha256(blob).hexdigest(),
        rows=rows,
        profile=profile_rows(rows, limits=limits),
        import_notes=tuple(notes),
    )


def parse_dataset_file(path: Path, *, limits: DatasetLimits = DEFAULT_LIMITS) -> ParsedDataset:
    """Read and parse a local file without allocating beyond its encoded bound."""

    with path.open("rb") as source:
        blob = source.read(limits.max_source_bytes + 1)
    return parse_dataset(blob, path.name, limits=limits)
