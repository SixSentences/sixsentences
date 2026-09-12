"""Domain errors raised by portable research-data operations."""


class DatasetError(ValueError):
    """Base class for invalid or unsupported dataset operations."""


class DatasetImportError(DatasetError):
    """A dataset cannot be decoded faithfully within the configured limits."""


class DatasetAnalysisError(DatasetError):
    """An analysis recipe is invalid or undefined for the selected values."""
