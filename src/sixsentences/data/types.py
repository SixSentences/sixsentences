"""Public value and profile types for bounded research datasets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type CellValue = str | int | float | bool | None
type Row = dict[str, CellValue]
type DatasetFormat = Literal["csv", "tsv", "json", "xlsx"]
type ColumnKind = Literal["number", "text", "mixed", "empty"]


@dataclass(frozen=True, slots=True)
class DatasetLimits:
    """Resource limits applied before and during dataset parsing.

    Inputs over a limit are rejected rather than silently truncated. This is
    important for both resource safety and reproducible scientific results.
    """

    max_source_bytes: int = 16 * 1024 * 1024
    max_rows: int = 10_000
    max_columns: int = 256
    max_cell_characters: int = 100_000
    preview_rows: int = 20
    max_xlsx_entries: int = 1_000
    max_xlsx_uncompressed_bytes: int = 100 * 1024 * 1024
    max_xlsx_member_bytes: int = 32 * 1024 * 1024
    max_xlsx_shared_strings: int = 100_000
    max_zip_compression_ratio: int = 200

    def __post_init__(self) -> None:
        """Reject nonsensical limits at the public API boundary."""

        for name in (
            "max_source_bytes",
            "max_rows",
            "max_columns",
            "max_cell_characters",
            "preview_rows",
            "max_xlsx_entries",
            "max_xlsx_uncompressed_bytes",
            "max_xlsx_member_bytes",
            "max_xlsx_shared_strings",
            "max_zip_compression_ratio",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.preview_rows > self.max_rows:
            raise ValueError("preview_rows cannot exceed max_rows")
        if self.max_xlsx_member_bytes > self.max_xlsx_uncompressed_bytes:
            raise ValueError("max_xlsx_member_bytes cannot exceed max_xlsx_uncompressed_bytes")


DEFAULT_LIMITS = DatasetLimits()


@dataclass(frozen=True, slots=True)
class NumericStatistics:
    """Exact descriptive statistics over the reported numeric values."""

    minimum: float
    maximum: float
    mean: float
    median: float


@dataclass(frozen=True, slots=True)
class ValueCount:
    """A typed scalar value and its observed frequency."""

    value: CellValue
    count: int


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """Deterministic structural and descriptive information for one column."""

    name: str
    kind: ColumnKind
    missing: int
    present: int
    unique: int
    numeric: int
    statistics: NumericStatistics | None
    top_values: tuple[ValueCount, ...]


@dataclass(frozen=True, slots=True)
class DatasetProfile:
    """A deterministic profile computed over every imported row."""

    row_count: int
    column_count: int
    columns: tuple[ColumnProfile, ...]
    preview: tuple[Row, ...]
    missing_definition: str = "null or an empty/whitespace-only string"
    numeric_definition: str = "finite JSON number or dot-decimal text; booleans excluded"
    method: str = "all accepted rows; no sampling, inference or imputation"
    limitations: tuple[str, ...] = (
        "Text categories are compared exactly after import; no semantic normalization is applied.",
        "Date, unit, sentinel-code and measurement-scale semantics are not inferred.",
    )


@dataclass(frozen=True, slots=True)
class ParsedDataset:
    """A bounded in-memory dataset plus reproducibility metadata."""

    filename: str
    format: DatasetFormat
    byte_count: int
    sha256: str
    rows: tuple[Row, ...]
    profile: DatasetProfile
    import_notes: tuple[str, ...]
    limits: DatasetLimits = DEFAULT_LIMITS
