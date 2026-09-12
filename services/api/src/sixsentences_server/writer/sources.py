"""Turn user-owned PDFs and reference exports into citable Writer sources."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sixsentences_server.connectors.refimport import parse_references


class SourceImportError(ValueError):
    pass


@dataclass
class ImportedSource:
    title: str
    authors: list[str]
    year: int | None
    doi: str
    cite_key: str
    bibtex: str
    text_content: str


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def citation_stem(title: str, authors: list[str], year: int | None) -> str:
    surname = re.split(r"[,\s]+", authors[0].strip())[0] if authors else "source"
    word = next((word for word in re.split(r"\W+", title) if len(word) > 2), "work")
    stem = re.sub(r"[^a-z0-9]", "", _ascii(f"{surname}{year or ''}{word}").lower())
    return stem or "source"


def _escape(value: str) -> str:
    return value.replace("\\", "").replace("{", "").replace("}", "").strip()


def make_bibtex(
    *,
    cite_key: str,
    title: str,
    authors: list[str],
    year: int | None,
    doi: str,
) -> str:
    fields = [("title", title)]
    if authors:
        fields.append(("author", " and ".join(authors)))
    if year:
        fields.append(("year", str(year)))
    if doi:
        fields.append(("doi", doi))
    body = ",\n".join(f"  {name} = {{{_escape(value)}}}" for name, value in fields)
    return f"@article{{{cite_key},\n{body}\n}}"


def import_reference_file(filename: str, blob: bytes) -> list[ImportedSource]:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".bib", ".bibtex", ".ris"}:
        raise SourceImportError("upload a PDF, BibTeX or RIS file")
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("latin-1")
    records = parse_references(filename, text)
    if not records:
        raise SourceImportError("no references with a title were found")
    output: list[ImportedSource] = []
    for record in records[:500]:
        title = str(record.get("title") or "Untitled source").strip()
        authors = [str(author).strip() for author in record.get("authors") or []]
        year = record.get("year") if isinstance(record.get("year"), int) else None
        doi = str(record.get("doi") or "").strip()
        key = citation_stem(title, authors, year)
        output.append(
            ImportedSource(
                title=title,
                authors=authors,
                year=year,
                doi=doi,
                cite_key=key,
                bibtex=make_bibtex(
                    cite_key=key,
                    title=title,
                    authors=authors,
                    year=year,
                    doi=doi,
                ),
                text_content=str(record.get("abstract") or ""),
            )
        )
    return output


def pdf_source(
    filename: str,
    text: str,
    metadata: dict[str, Any],
) -> ImportedSource:
    title = str(metadata.get("title") or Path(filename).stem.replace("-", " ")).strip()
    authors = [str(value).strip() for value in metadata.get("authors") or [] if value]
    year = metadata.get("year") if isinstance(metadata.get("year"), int) else None
    doi = str(metadata.get("doi") or "").strip()
    key = citation_stem(title, authors, year)
    return ImportedSource(
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        cite_key=key,
        bibtex=make_bibtex(
            cite_key=key,
            title=title,
            authors=authors,
            year=year,
            doi=doi,
        ),
        text_content=text[:60_000],
    )
