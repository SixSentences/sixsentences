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
from typing import Literal, Protocol
from xml.etree import ElementTree

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as parse_untrusted_xml

from sixsentences_server.acquisition.models import ExtractedText, TextStatus

_MIN_TEXT = 40  # shorter than this is treated as "no real full text found"
_MARKUP_HEAD_CHARS = 200


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
    try:
        parser.feed(raw)
    except (AssertionError, ValueError):
        # Malformed marked sections / numeric references must not expose partial text.
        return ""
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


def _markup_signal(raw: str) -> Literal["html", "xml", "unknown"] | None:
    """Sniff a bounded prolog/root, without validating or rewriting parser input.

    Incomplete prologs retain the old sniff priority. The initial whitespace/BOM
    scan preserves declaration detection beyond the root-sniff window.
    """
    index = 0
    while index < len(raw) and (raw[index].isspace() or raw[index] == "\ufeff"):
        index += 1
    if raw.startswith("<?xml", index):
        return "xml"
    head = raw[:_MARKUP_HEAD_CHARS]
    while index < len(head):
        if head[index].isspace() or head[index] == "\ufeff":
            index += 1
            continue
        if head.startswith("<?xml", index):
            return "xml"
        if head.startswith("<!--", index):
            end = head.find("-->", index + 4)
            if end == -1:
                return "unknown"
            index = end + 3
            continue
        if head[index : index + 9].lower() == "<!doctype":
            index += 9
            if index == len(head):
                return "unknown"
            if not head[index].isspace():
                return "unknown"
            while index < len(head) and head[index].isspace():
                index += 1
            start = index
            while index < len(head) and not head[index].isspace() and head[index] not in "[>":
                index += 1
            if index == len(head):
                return "unknown"
            if head[start:index].lower() != "html":
                return "xml"
            quote = ""
            while index < len(head):
                char = head[index]
                index += 1
                if quote:
                    if char == quote:
                        quote = ""
                elif char in "\"'":
                    quote = char
                elif char == "[":
                    return "xml"  # even a DOCTYPE named html can declare XML entities
                elif char == ">":
                    break
            else:
                return "unknown"
            continue
        if head.startswith(("<?", "<!"), index):
            return "unknown"
        if head[index : index + 5].lower() == "<html":
            end = index + 5
            if end == len(head):
                return "unknown"
            if head[end].isspace() or head[end] in "/>":
                return "html"
        return None
    return "unknown"


class StdlibTextExtractor:
    def extract(self, content: bytes, content_type: str) -> ExtractedText:
        ctype = content_type.lower()
        if "pdf" in ctype or content[:5] == b"%PDF-":
            return ExtractedText("", TextStatus.STORED_UNPARSED)  # honest deferral
        raw = content.decode("utf-8", errors="ignore")
        head = raw[:_MARKUP_HEAD_CHARS].lower()
        signal = _markup_signal(raw)
        if signal == "unknown":
            # Do not create a new XML-to-HTML route when the bounded sniff is ambiguous.
            signal = "xml" if "<article" in head else "html" if "<html" in head else None
        if "xml" in ctype or signal == "xml":
            text = _strip_xml(raw)
        elif "html" in ctype or signal == "html":
            text = _strip_html(raw)
        elif "<article" in head:
            text = _strip_xml(raw)
        elif ctype == "" or "text" in ctype:
            text = raw
        else:
            return ExtractedText("", TextStatus.UNSUPPORTED)
        text = " ".join(text.split())  # normalize whitespace
        if len(text) < _MIN_TEXT:
            return ExtractedText(text, TextStatus.EMPTY)
        return ExtractedText(text, TextStatus.PARSED)
