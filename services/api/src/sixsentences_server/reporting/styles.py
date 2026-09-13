"""Styled bibliographies and minimal DOCX packaging — the Word bridge.

Most of academia writes in Word with a citation style, not in LaTeX with
BibTeX. This renders the run's works as APA 7 / IEEE / Harvard reference
list entries (rule-based, dependency-free; journal-article shaped, which is
what the corpus holds) and packs text or tables into a minimal but valid
.docx (a zip of three XML parts — no python-docx dependency).
"""

import io
import re
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from sixsentences_server.core.models import WorkRecord

STYLES = ("apa", "ieee", "harvard")


def _split_author(name: str) -> tuple[str, str]:
    """Best-effort (family, initials) from free-form author strings like
    'A. Vaswani', 'Alice Smith' or 'Smith, Alice'."""
    name = name.strip()
    if "," in name:
        family, _, rest = name.partition(",")
        given = rest.strip()
        family = family.strip()
    else:
        parts = name.split()
        if not parts:
            return "", ""
        family = parts[-1]
        given = " ".join(parts[:-1])
    initials = " ".join(f"{token[0].upper()}." for token in re.findall(r"[A-Za-zÀ-ž]+", given))
    return family, initials


def _doi_url(work: WorkRecord) -> str:
    return f"https://doi.org/{work.doi}" if work.doi else ""


def _apa_authors(authors: list[str]) -> str:
    formatted = []
    for author in authors[:20]:
        family, initials = _split_author(author)
        formatted.append(f"{family}, {initials}".rstrip(", ").strip())
    if not formatted:
        return ""
    if len(formatted) == 1:
        return formatted[0]
    return ", ".join(formatted[:-1]) + ", & " + formatted[-1]


def _ieee_authors(authors: list[str]) -> str:
    formatted = []
    for author in authors[:6]:
        family, initials = _split_author(author)
        formatted.append(f"{initials} {family}".strip())
    if len(authors) > 6:
        formatted.append("et al.")
    return ", ".join(formatted)


def format_entry(work: WorkRecord, style: str, index: int) -> str:
    year = str(work.year) if work.year else "n.d."
    venue = work.venue or ""
    doi = _doi_url(work)
    if style == "apa":
        parts = [
            f"{_apa_authors(work.authors)} ({year}).".strip(),
            f"{work.title}.",
        ]
        if venue:
            parts.append(f"{venue}.")
        if doi:
            parts.append(doi)
        return " ".join(p for p in parts if p and p != "().")
    if style == "ieee":
        segments = [f"[{index}]", f"{_ieee_authors(work.authors)},".strip(",") + ","]
        segments.append(f'"{work.title},"')
        if venue:
            segments.append(f"{venue},")
        segments.append(f"{year}.")
        if work.doi:
            segments.append(f"doi: {work.doi}.")
        return " ".join(s for s in segments if s.strip(",").strip())
    # harvard
    parts = [f"{_apa_authors(work.authors)} ({year})".strip(), f"'{work.title}',"]
    if venue:
        parts.append(f"{venue}.")
    if doi:
        parts.append(f"Available at: {doi}.")
    return " ".join(p for p in parts if p)


def format_bibliography(works: list[WorkRecord], style: str) -> list[str]:
    """Reference-list entries, alphabetical for APA/Harvard, cited-order
    (here: given order) for IEEE."""
    if style not in STYLES:
        raise ValueError(f"unknown style: {style!r}")

    def _sort_key(work: WorkRecord) -> str:
        head = _split_author(work.authors[0])[0] if work.authors else work.title
        return head.lower()

    ordered = list(works) if style == "ieee" else sorted(works, key=_sort_key)
    return [format_entry(work, style, i) for i, work in enumerate(ordered, start=1)]


# -- minimal DOCX packaging ---------------------------------------------------

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
    'package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    "openxmlformats-officedocument.wordprocessingml.document.main+xml"
    '"/></Types>'
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _para(text: str, *, bold: bool = False, hanging: bool = False) -> str:
    props = []
    if hanging:
        props.append('<w:ind w:left="720" w:hanging="720"/>')
    run_props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f'<w:p>{ppr}<w:r>{run_props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    def cell(text: str, *, bold: bool = False) -> str:
        properties = "<w:tcPr><w:tcW w:w='2400' w:type='dxa'/></w:tcPr>"
        return f"<w:tc>{properties}{_para(text, bold=bold)}</w:tc>"

    borders = (
        "<w:tblBorders>"
        + "".join(
            f'<w:{edge} w:val="single" w:sz="4" w:color="999999"/>'
            for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        + "</w:tblBorders>"
    )
    header_row = "<w:tr>" + "".join(cell(h, bold=True) for h in headers) + "</w:tr>"
    body_rows = "".join(
        "<w:tr>" + "".join(cell(value) for value in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl><w:tblPr>{borders}</w:tblPr>{header_row}{body_rows}</w:tbl>"


def build_docx(blocks: list[dict[str, Any]]) -> bytes:
    """Pack blocks into a valid .docx. Block kinds:
    {"kind": "heading"|"paragraph", "text": str} or
    {"kind": "table", "headers": [...], "rows": [[...], ...]} or
    {"kind": "references", "entries": [...]} (hanging indent)."""
    body_parts: list[str] = []
    for block in blocks:
        kind = block.get("kind")
        if kind == "heading":
            body_parts.append(_para(str(block["text"]), bold=True))
        elif kind == "paragraph":
            body_parts.append(_para(str(block["text"])))
        elif kind == "references":
            body_parts.extend(_para(entry, hanging=True) for entry in block["entries"])
        elif kind == "table":
            body_parts.append(_table(block["headers"], block["rows"]))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>'
        + "".join(body_parts)
        + "<w:sectPr/></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _CONTENT_TYPES)
        package.writestr("_rels/.rels", _RELS)
        package.writestr("word/document.xml", document)
    return buffer.getvalue()
