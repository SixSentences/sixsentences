"""Self-hosted SixSentences research workspace server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sixsentences-community-api")
except PackageNotFoundError:  # A source tree imported without installation.
    __version__ = "0+unknown"

__all__ = ["__version__"]
