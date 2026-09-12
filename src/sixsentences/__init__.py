"""Open building blocks for auditable research workflows."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sixsentences-engine")
except PackageNotFoundError:  # A source tree imported without installation.
    __version__ = "0+unknown"

__all__ = ["__version__"]
