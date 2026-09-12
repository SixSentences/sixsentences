"""Retraction data (Retraction Watch via Crossref, daily public CSV).

Integrity layer input: a set of retracted DOIs checked at retrieval time and
(later) continuously for living reviews. The CSV is ~tens of MB, cached under
data_dir; loading degrades gracefully when absent.
"""

import csv
from pathlib import Path

import httpx

RETRACTION_WATCH_CSV_URL = (
    "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"
)
CACHE_FILE = "retraction_watch.csv"


def download_retractions(data_dir: Path, *, url: str = RETRACTION_WATCH_CSV_URL) -> Path:
    target = data_dir / CACHE_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as response:
        response.raise_for_status()
        with target.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)
    return target


def load_retracted_dois(data_dir: Path) -> set[str]:
    """Return lowercased retracted DOIs; empty set if data not downloaded."""
    path = data_dir / CACHE_FILE
    if not path.exists():
        return set()
    dois: set[str] = set()
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            doi = (row.get("OriginalPaperDOI") or "").strip().lower()
            if doi and doi != "unavailable":
                dois.add(doi.removeprefix("https://doi.org/"))
    return dois
