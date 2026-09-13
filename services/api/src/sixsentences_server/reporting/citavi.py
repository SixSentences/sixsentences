"""Citavi-compatible round-trip exports with research context preserved."""

import re
from typing import Any


def _safe(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def records_to_ris(records: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for record in records:
        lines = ["TY  - JOUR"]
        for author in record.get("authors") or []:
            lines.append(f"AU  - {_safe(author)}")
        lines.append(f"TI  - {_safe(record.get('title'))}")
        if record.get("year"):
            lines.append(f"PY  - {record['year']}")
        if record.get("venue"):
            lines.append(f"JO  - {_safe(record['venue'])}")
        if record.get("abstract"):
            lines.append(f"AB  - {_safe(record['abstract'])}")
        if record.get("doi"):
            lines.append(f"DO  - {_safe(record['doi'])}")
        if record.get("url"):
            lines.append(f"UR  - {_safe(record['url'])}")
        if record.get("citation_key"):
            lines.append(f"ID  - {_safe(record['citation_key'])}")
        for keyword in record.get("keywords") or []:
            lines.append(f"KW  - {_safe(keyword)}")
        for category in record.get("categories") or []:
            lines.append(f"KW  - Citavi category: {_safe(category)}")
        for note in record.get("notes") or []:
            lines.append(f"N1  - {_safe(note)}")
        for attachment in record.get("attachments") or []:
            lines.append(f"L1  - {_safe(attachment)}")
        lines.append("ER  - ")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + ("\n" if blocks else "")


def records_to_endnote(records: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for record in records:
        lines = ["%0 Journal Article", f"%T {_safe(record.get('title'))}"]
        lines.extend(f"%A {_safe(author)}" for author in record.get("authors") or [])
        if record.get("year"):
            lines.append(f"%D {record['year']}")
        if record.get("venue"):
            lines.append(f"%J {_safe(record['venue'])}")
        if record.get("abstract"):
            lines.append(f"%X {_safe(record['abstract'])}")
        if record.get("doi"):
            lines.append(f"%R {_safe(record['doi'])}")
        if record.get("url"):
            lines.append(f"%U {_safe(record['url'])}")
        lines.extend(f"%K {_safe(keyword)}" for keyword in record.get("keywords") or [])
        lines.extend(f"%Z {_safe(note)}" for note in record.get("notes") or [])
        lines.extend(f"%L {_safe(item)}" for item in record.get("attachments") or [])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def records_to_bibtex(records: list[dict[str, Any]]) -> str:
    entries: list[str] = []
    used: set[str] = set()
    for index, record in enumerate(records, start=1):
        raw_key = _safe(record.get("citation_key")) or f"sixsentences{index}"
        stem = re.sub(r"[^A-Za-z0-9:_-]", "", raw_key) or f"sixsentences{index}"
        key = stem
        suffix = 2
        while key in used:
            key = f"{stem}{suffix}"
            suffix += 1
        used.add(key)
        fields: list[tuple[str, str]] = [("title", _safe(record.get("title")))]
        if record.get("authors"):
            fields.append(("author", " and ".join(_safe(a) for a in record["authors"])))
        for source, target in (
            ("year", "year"),
            ("venue", "journal"),
            ("doi", "doi"),
            ("url", "url"),
            ("abstract", "abstract"),
            ("isbn", "isbn"),
            ("issn", "issn"),
        ):
            if record.get(source):
                fields.append((target, _safe(record[source])))
        if record.get("keywords"):
            fields.append(("keywords", "; ".join(_safe(k) for k in record["keywords"])))
        if record.get("notes"):
            fields.append(("note", " | ".join(_safe(n) for n in record["notes"])))
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        entries.append(f"@article{{{key},\n{body}\n}}")
    return "\n\n".join(entries) + ("\n" if entries else "")
