"""Retraction data (Retraction Watch via Crossref, daily public CSV).

Integrity-layer input: a set of retracted DOIs that callers can check during
retrieval or for living reviews. The CSV is cached under ``data_dir``; loading
degrades gracefully when absent.
"""

import csv
import os
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx

RETRACTION_WATCH_CSV_URL = (
    "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"
)
CACHE_FILE = "retraction_watch.csv"
MAX_DOWNLOAD_BYTES: Final = 256 * 1024 * 1024


class RetractionDownloadError(RuntimeError):
    """Raised when the fixed Retraction Watch download cannot be trusted."""


def download_retractions(
    data_dir: Path,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    http: httpx.Client | None = None,
) -> Path:
    """Atomically cache the fixed Retraction Watch CSV with a hard size cap.

    Redirects are rejected so a compromised response cannot turn this helper
    into a fetcher for an arbitrary host. A failed update preserves any
    previously verified cache file.
    """

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    target = data_dir / CACHE_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    temporary = data_dir / f".{CACHE_FILE}.{uuid4().hex}.tmp"
    client = http or httpx.Client()
    owns_client = http is None
    try:
        try:
            with client.stream(
                "GET",
                RETRACTION_WATCH_CSV_URL,
                timeout=300,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    raise RetractionDownloadError(
                        "Retraction Watch returned an unexpected redirect"
                    )
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_bytes = int(content_length)
                    except ValueError as exc:
                        raise RetractionDownloadError("invalid download content length") from exc
                    if declared_bytes < 0 or declared_bytes > max_bytes:
                        raise RetractionDownloadError(
                            "Retraction Watch download exceeds the size cap"
                        )

                downloaded = 0
                with temporary.open("xb") as handle:
                    for chunk in response.iter_bytes():
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise RetractionDownloadError(
                                "Retraction Watch download exceeds the size cap"
                            )
                        handle.write(chunk)
                    if downloaded == 0:
                        raise RetractionDownloadError("Retraction Watch download was empty")
                    handle.flush()
                    os.fsync(handle.fileno())
                with temporary.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
                    header = next(csv.reader(handle), [])
                required_columns = {"OriginalPaperDOI", "RetractionNature"}
                if not required_columns.issubset(header):
                    raise RetractionDownloadError(
                        "Retraction Watch download does not contain the expected CSV schema"
                    )
        except httpx.HTTPError as exc:
            raise RetractionDownloadError("Retraction Watch download failed") from exc
        temporary.replace(target)
        return target
    finally:
        if owns_client:
            client.close()
        temporary.unlink(missing_ok=True)


def load_retracted_dois(data_dir: Path) -> set[str]:
    """Return lowercased retracted DOIs; empty set if data not downloaded."""
    path = data_dir / CACHE_FILE
    if not path.exists():
        return set()
    dois: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (row.get("RetractionNature") or "").strip().casefold() != "retraction":
                continue
            doi = (row.get("OriginalPaperDOI") or "").strip().lower()
            if doi and doi != "unavailable":
                dois.add(doi.removeprefix("https://doi.org/"))
    return dois
