"""RIS / BibTeX reference imports.

Brings exports from Scopus, Web of Science, PubMed, or any reference manager
into a run's identification stage as an additional database arm — the piece
that makes a formally multi-database review possible. Parsing is deliberately
dependency-free and lenient: a record needs a title to count; everything else
is best effort.
"""

import re
from typing import Any

_RIS_TAG = re.compile(r"^([A-Z][A-Z0-9])\s{0,2}-\s?(.*)$")
_BIB_ENTRY = re.compile(r"@(\w+)\s*\{", re.IGNORECASE)
_BIB_FIELD = re.compile(
    r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)", re.IGNORECASE
)
_YEAR = re.compile(r"(19|20)\d{2}")


def _blank() -> dict[str, Any]:
    """The loss-minimising reference shape shared by file connectors.

    Runs only need the core bibliographic fields, but reference managers carry
    valuable research context (keywords, categories, notes and attachment
    links). Keeping it here means a Citavi round trip does not silently flatten
    the user's project into title/author/year records.
    """
    return {
        "title": "",
        "abstract": "",
        "year": None,
        "doi": "",
        "authors": [],
        "venue": "",
        "reference_type": "article",
        "citation_key": "",
        "keywords": [],
        "categories": [],
        "notes": [],
        "url": "",
        "isbn": "",
        "issn": "",
        "attachments": [],
    }


def _clean_doi(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    return value.lower()


def parse_ris(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_tag = ""

    def flush() -> None:
        nonlocal current
        if current and current["title"].strip():
            current["title"] = current["title"].strip()
            records.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.strip("﻿\r\n")
        match = _RIS_TAG.match(line.strip())
        if not match:
            # continuation line of the previous long field
            if current is not None and last_tag in ("TI", "T1", "AB", "N2") and line.strip():
                key = "title" if last_tag in ("TI", "T1") else "abstract"
                current[key] = (current[key] + " " + line.strip()).strip()
            continue
        tag, value = match.group(1), match.group(2).strip()
        last_tag = tag
        if tag == "TY":
            flush()
            current = _blank()
            continue
        if current is None:
            current = _blank()
        if tag == "ER":
            flush()
        elif tag in ("TI", "T1") and value:
            current["title"] = (current["title"] + " " + value).strip()
        elif tag in ("AB", "N2") and value:
            current["abstract"] = (current["abstract"] + " " + value).strip()
        elif tag in ("PY", "Y1") and (year := _YEAR.search(value)):
            current["year"] = int(year.group(0))
        elif tag == "DO" and value:
            current["doi"] = _clean_doi(value)
        elif tag in ("AU", "A1") and value:
            current["authors"].append(value)
        elif tag in ("JO", "JF", "T2") and value and not current["venue"]:
            current["venue"] = value
        elif tag == "KW" and value:
            current["keywords"].append(value)
        elif tag in ("N1", "RN") and value:
            current["notes"].append(value)
        elif tag == "UR" and value:
            current["url"] = value
        elif tag == "SN" and value:
            if value.lower().startswith("isbn") or len(re.sub(r"\D", "", value)) >= 10:
                current["isbn"] = value
            else:
                current["issn"] = value
        elif tag in ("L1", "L2", "L4") and value:
            current["attachments"].append(value)
        elif tag == "ID" and value:
            current["citation_key"] = value
    flush()
    return records


def _unbrace(value: str) -> str:
    value = value.strip().strip(",").strip()
    braced = value.startswith("{") and value.endswith("}")
    quoted = value.startswith('"') and value.endswith('"')
    if braced or quoted:
        value = value[1:-1]
    return re.sub(r"[{}]", "", value).strip()


def parse_bibtex(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for match in _BIB_ENTRY.finditer(text):
        if match.group(1).lower() in ("comment", "preamble", "string"):
            continue
        # walk to the matching closing brace of this entry
        depth = 1
        start = match.end()
        pos = start
        while pos < len(text) and depth:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        body = text[start : pos - 1]
        record = _blank()
        record["reference_type"] = match.group(1).lower()
        cite_key = body.split(",", 1)[0].strip()
        if cite_key:
            record["citation_key"] = cite_key
        for field_match in _BIB_FIELD.finditer(body):
            name = field_match.group(1).lower()
            value = _unbrace(field_match.group(2))
            if name == "title":
                record["title"] = value
            elif name == "abstract":
                record["abstract"] = value
            elif name == "year" and (year := _YEAR.search(value)):
                record["year"] = int(year.group(0))
            elif name == "doi":
                record["doi"] = _clean_doi(value)
            elif name == "author":
                record["authors"] = [a.strip() for a in re.split(r"\s+and\s+", value) if a.strip()]
            elif name in ("journal", "booktitle") and not record["venue"]:
                record["venue"] = value
            elif name in ("keywords", "keyword"):
                record["keywords"] = [
                    item.strip() for item in re.split(r"[;,]", value) if item.strip()
                ]
            elif name in ("note", "annotation") and value:
                record["notes"].append(value)
            elif name == "url":
                record["url"] = value
            elif name == "isbn":
                record["isbn"] = value
            elif name == "issn":
                record["issn"] = value
            elif name in ("file", "pdf") and value:
                record["attachments"].extend(
                    item.strip() for item in value.split(";") if item.strip()
                )
        if record["title"]:
            records.append(record)
    return records


def parse_endnote_tagged(text: str) -> list[dict[str, Any]]:
    """Parse EndNote Tagged/ENW exports, Citavi's third standard exchange path."""
    records: list[dict[str, Any]] = []
    current = _blank()

    def flush() -> None:
        nonlocal current
        if current["title"].strip():
            current["title"] = current["title"].strip()
            records.append(current)
        current = _blank()

    for raw_line in text.splitlines():
        line = raw_line.strip("\ufeff\r\n")
        if not line.strip():
            flush()
            continue
        match = re.match(r"^%([A-Z0-9])\s+(.*)$", line)
        if not match:
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "0":
            if current["title"]:
                flush()
            current["reference_type"] = value.lower().replace(" ", "_")
        elif tag == "T":
            current["title"] = (current["title"] + " " + value).strip()
        elif tag == "A":
            current["authors"].append(value)
        elif tag == "D" and (year := _YEAR.search(value)):
            current["year"] = int(year.group(0))
        elif tag in ("J", "B") and not current["venue"]:
            current["venue"] = value
        elif tag == "X":
            current["abstract"] = (current["abstract"] + " " + value).strip()
        elif tag == "R" and value.lower().startswith("10."):
            current["doi"] = _clean_doi(value)
        elif tag == "K":
            current["keywords"].extend(
                item.strip() for item in re.split(r"[;,]", value) if item.strip()
            )
        elif tag == "Z":
            current["notes"].append(value)
        elif tag == "U":
            current["url"] = value
        elif tag == "@":
            current["isbn"] = value
        elif tag == "L":
            current["attachments"].append(value)
    flush()
    return records


def parse_references(filename: str, text: str) -> list[dict[str, Any]]:
    """Best-effort dispatch: extension first, then content sniffing."""
    lowered = filename.lower()
    if lowered.endswith(".ris"):
        return parse_ris(text)
    if lowered.endswith((".bib", ".bibtex")):
        return parse_bibtex(text)
    if lowered.endswith((".enw", ".enl")):
        return parse_endnote_tagged(text)
    if re.search(r"^TY\s{0,2}-", text, re.MULTILINE):
        return parse_ris(text)
    if _BIB_ENTRY.search(text):
        return parse_bibtex(text)
    if re.search(r"^%0\s+", text, re.MULTILINE):
        return parse_endnote_tagged(text)
    return parse_ris(text)
