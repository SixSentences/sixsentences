"""Portable, provider-free building blocks for user-owned research data.

The package imports bounded CSV, TSV, JSON and ordinary XLSX tables, computes
complete deterministic profiles and runs a deliberately small set of auditable
analysis recipes. It has no database, network, hosted-service or plotting
dependency.
"""

from sixsentences.data.analysis import (
    AnalysisRecipe,
    AnalysisResult,
    DescriptiveRecipe,
    DescriptiveResult,
    GroupMetric,
    GroupSummaryRecipe,
    GroupSummaryResult,
    GroupValue,
    MetaAnalysisStudy,
    MissingnessColumn,
    MissingnessRecipe,
    MissingnessResult,
    PearsonCorrelationRecipe,
    PearsonCorrelationResult,
    RandomEffectsMetaAnalysisRecipe,
    RandomEffectsMetaAnalysisResult,
    analyze,
    analyze_rows,
)
from sixsentences.data.archives import (
    UnsafeArchiveError,
    ZipMember,
    ZipSafetyLimits,
    open_safe_zip,
    preflight_zip,
    read_zip_member,
)
from sixsentences.data.errors import DatasetAnalysisError, DatasetError, DatasetImportError
from sixsentences.data.importers import parse_dataset, parse_dataset_file
from sixsentences.data.profile import as_number, dataset_context, is_missing, profile_rows
from sixsentences.data.types import (
    DEFAULT_LIMITS,
    CellValue,
    ColumnKind,
    ColumnProfile,
    DatasetFormat,
    DatasetLimits,
    DatasetProfile,
    NumericStatistics,
    ParsedDataset,
    Row,
    ValueCount,
)

__all__ = [
    "DEFAULT_LIMITS",
    "AnalysisRecipe",
    "AnalysisResult",
    "CellValue",
    "ColumnKind",
    "ColumnProfile",
    "DatasetAnalysisError",
    "DatasetError",
    "DatasetFormat",
    "DatasetImportError",
    "DatasetLimits",
    "DatasetProfile",
    "DescriptiveRecipe",
    "DescriptiveResult",
    "GroupSummaryRecipe",
    "GroupSummaryResult",
    "GroupMetric",
    "GroupValue",
    "MetaAnalysisStudy",
    "MissingnessColumn",
    "MissingnessRecipe",
    "MissingnessResult",
    "NumericStatistics",
    "ParsedDataset",
    "PearsonCorrelationRecipe",
    "PearsonCorrelationResult",
    "RandomEffectsMetaAnalysisRecipe",
    "RandomEffectsMetaAnalysisResult",
    "Row",
    "UnsafeArchiveError",
    "ValueCount",
    "ZipMember",
    "ZipSafetyLimits",
    "analyze",
    "analyze_rows",
    "as_number",
    "dataset_context",
    "is_missing",
    "open_safe_zip",
    "parse_dataset",
    "parse_dataset_file",
    "preflight_zip",
    "profile_rows",
    "read_zip_member",
]
