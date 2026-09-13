"""Full-text extraction.

The stdlib extractor handles text, HTML, and JATS/XML (PMC OA) — the XML path
goes through ``defusedxml`` because the bytes are untrusted. PDFs need a real
parser farm (GROBID/Docling) — a deliberate seam:
the bytes are stored and the document is marked ``stored_unparsed`` rather than
pretending we have text. Retrieval still succeeded (the PRISMA "report
retrieved" box is ticked and the legal basis recorded); parsing is a separate,
backend-gated step. Wiring a PDF backend is a one-class addition behind the
``TextExtractor`` protocol.
"""

from html.parser import HTMLParser
from typing import Protocol
from xml.etree import ElementTree

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as parse_untrusted_xml

from sixsentences_server.acquisition.models import ExtractedText, TextStatus

_MIN_TEXT = 40  # shorter than this is treated as "no real full text found"


class TextExtractor(Protocol):
    def extract(self, content: bytes, content_type: str) -> ExtractedText: ...


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _strip_html(raw: str) -> str:
    parser = _TextCollector()
    parser.feed(raw)
    return " ".join(parser.parts)


def _strip_xml(raw: str) -> str:
    # The XML is an upload or a fetched open-access location, so it is attacker
    # material: a DTD whose entities reference each other ("billion laughs")
    # expands to gigabytes inside the parser and takes the worker with it.
    # defusedxml refuses the entity and external-reference declarations that
    # make that possible; a rejected document extracts as empty text, exactly
    # like a malformed one.
    try:
        root = parse_untrusted_xml(raw)
    except (ElementTree.ParseError, DefusedXmlException):
        return ""
    return " ".join(t.strip() for t in root.itertext() if t.strip())


class StdlibTextExtractor:
    def extract(self, content: bytes, content_type: str) -> ExtractedText:
        ctype = content_type.lower()
        if "pdf" in ctype or content[:5] == b"%PDF-":
            return ExtractedText("", TextStatus.STORED_UNPARSED)  # honest deferral
        raw = content.decode("utf-8", errors="ignore")
        head = raw[:200].lower()
        if "xml" in ctype or raw.lstrip().startswith("<?xml") or "<article" in head:
            text = _strip_xml(raw)
        elif "html" in ctype or "<html" in head:
            text = _strip_html(raw)
        elif ctype == "" or "text" in ctype:
            text = raw
        else:
            return ExtractedText("", TextStatus.UNSUPPORTED)
        text = " ".join(text.split())  # normalize whitespace
        if len(text) < _MIN_TEXT:
            return ExtractedText(text, TextStatus.EMPTY)
        return ExtractedText(text, TextStatus.PARSED)
