"""GROBID full-text extraction: structured TEI-XML instead of raw PDF text.

GROBID parses a PDF into structured TEI (title, abstract, labelled body
sections, references) — much cleaner than pypdf's running text (no headers/
footers/page numbers, correct reading order, references separated). It is the
"parser farm" from the concept. GROBID is an external service (a Docker/Java
process), reached over HTTP with the existing httpx dependency — no new Python
package. It is entirely optional: with no SIX_GROBID_URL, or if the service is
unreachable, extraction falls back to pypdf, so acquisition never breaks. This
sits behind the same TextExtractor protocol as the stdlib and pypdf extractors.
"""

from xml.etree import ElementTree

import httpx

from sixsentences_server.acquisition.extract import TextExtractor
from sixsentences_server.acquisition.models import ExtractedText, TextStatus

_TEI = "{http://www.tei-c.org/ns/1.0}"
_MIN_TEXT = 200  # below this the TEI carried no real body -> fall back


def tei_to_text(tei: bytes) -> str:
    """Flatten GROBID TEI into title + abstract + body text."""
    try:
        root = ElementTree.fromstring(tei)
    except ElementTree.ParseError:
        return ""
    parts: list[str] = []
    header = root.find(f"{_TEI}teiHeader")
    if header is not None:
        title = header.find(f".//{_TEI}titleStmt/{_TEI}title")
        if title is not None and title.text:
            parts.append(title.text.strip())
        abstract = header.find(f".//{_TEI}abstract")
        if abstract is not None:
            parts.append(" ".join(t.strip() for t in abstract.itertext() if t.strip()))
    body = root.find(f".//{_TEI}text/{_TEI}body")
    if body is not None:
        parts.append(" ".join(t.strip() for t in body.itertext() if t.strip()))
    return " ".join(p for p in parts if p)


class GrobidTextExtractor:
    """Extract PDFs via a GROBID service; delegate everything else to a fallback."""

    def __init__(
        self,
        *,
        grobid_url: str,
        fallback: TextExtractor,
        http: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.grobid_url = grobid_url.rstrip("/")
        self.fallback = fallback
        self.http = http or httpx.Client(timeout=timeout)

    def extract(self, content: bytes, content_type: str) -> ExtractedText:
        is_pdf = "pdf" in content_type.lower() or content[:5] == b"%PDF-"
        if not is_pdf or not self.grobid_url:
            return self.fallback.extract(content, content_type)
        tei = self._call_grobid(content)
        if tei is None:  # service unreachable / error -> pypdf, never break
            return self.fallback.extract(content, content_type)
        text = " ".join(tei_to_text(tei).split())
        if len(text) < _MIN_TEXT:
            return self.fallback.extract(content, content_type)
        return ExtractedText(text, TextStatus.PARSED)

    def _call_grobid(self, content: bytes) -> bytes | None:
        try:
            response = self.http.post(
                f"{self.grobid_url}/api/processFulltextDocument",
                files={"input": ("document.pdf", content, "application/pdf")},
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return response.content
