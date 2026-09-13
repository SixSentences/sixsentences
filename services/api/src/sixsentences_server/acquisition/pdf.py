"""PDF text extraction via pypdf, behind the TextExtractor protocol.

pypdf is pure-Python (no system deps) and yields clean running text — enough for
full-text screening and search. Structured extraction (sections, references) is
the GROBID/Docling upgrade, a drop-in behind this same protocol. A PDF with no
text layer (a scanned image) honestly stays ``stored_unparsed`` rather than
producing garbage; OCR is a later lever. Non-PDF content is delegated to the
stdlib extractor, so this class is a complete extractor on its own.
"""

import re
from io import BytesIO

import pypdf

from sixsentences_server.acquisition.extract import StdlibTextExtractor, TextExtractor
from sixsentences_server.acquisition.models import ExtractedText, TextStatus

_MIN_PDF_TEXT = 20  # below this pypdf found no real text layer (scanned/image PDF)
# a page bomb (thousands of pathological pages) would pin the ingest worker;
# a real paper is well under this, and 60 pages already saturate screening
_MAX_PDF_PAGES = 400


def extract_page_texts(content: bytes, *, max_pages: int = 60) -> list[str]:
    """Per-page text, for features that must anchor to a page (viewer highlights).

    Whitespace is collapsed per page so quotes can be matched against the
    viewer's text layer regardless of line breaks. Malformed PDFs yield []."""
    try:
        reader = pypdf.PdfReader(BytesIO(content))
        pages = reader.pages[:max_pages]
        return [" ".join((page.extract_text() or "").split()) for page in pages]
    except Exception:  # malformed / encrypted / truncated PDF
        return []


def extract_page_text(content: bytes, page_number: int) -> str | None:
    """Extract one viewer-selected page without walking every earlier page.

    Reader selections support long appendices, but a high page number must
    not turn one quote check into extraction of the entire document.
    """

    if not 1 <= page_number <= 2_000:
        return None
    try:
        reader = pypdf.PdfReader(BytesIO(content))
        if page_number > len(reader.pages):
            return None
        return " ".join((reader.pages[page_number - 1].extract_text() or "").split())
    except Exception:  # malformed / encrypted / truncated PDF
        return None


def extract_page_text_range(
    content: bytes,
    start_page: int,
    end_page: int,
    *,
    max_span: int = 32,
) -> list[str] | None:
    """Extract one bounded inclusive page range with a single PDF parse."""

    if (
        start_page < 1
        or end_page < start_page
        or end_page - start_page + 1 > max_span
        or end_page > 2_000
    ):
        return None
    try:
        reader = pypdf.PdfReader(BytesIO(content))
        if end_page > len(reader.pages):
            return None
        # Preserve layout line endings until Writer selection normalization;
        # collapsing here would turn ``unser-\niös`` into ``unser- iös`` and
        # destroy the only safe signal that the hyphen was visual wrapping.
        return [
            (reader.pages[index].extract_text() or "").replace("\x00", "")
            for index in range(start_page - 1, end_page)
        ]
    except Exception:  # malformed / encrypted / truncated PDF
        return None


def extract_page_texts_preserving_paragraphs(
    content: bytes, *, max_pages: int = _MAX_PDF_PAGES
) -> list[str]:
    """Extract complete page text while retaining paragraph boundaries.

    The highlight extractor above intentionally collapses whitespace for
    stable quote matching. A translated reading edition needs the opposite:
    headings and paragraph breaks should survive so the new PDF remains easy
    to read. Malformed and encrypted files retain the existing honest empty
    result contract.
    """

    try:
        reader = pypdf.PdfReader(BytesIO(content))
        extracted: list[str] = []
        for page in reader.pages[:max_pages]:
            raw = (page.extract_text() or "").replace("\x00", "")
            lines = [line.rstrip() for line in raw.splitlines()]
            normalized = "\n".join(lines)
            normalized = "\n\n".join(
                block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()
            )
            extracted.append(normalized)
        return extracted
    except Exception:  # malformed / encrypted / truncated PDF
        return []


class PdfTextExtractor:
    """Parse PDFs with pypdf; delegate every other content type to a fallback."""

    def __init__(self, fallback: TextExtractor | None = None) -> None:
        self.fallback = fallback or StdlibTextExtractor()

    def extract(self, content: bytes, content_type: str) -> ExtractedText:
        if "pdf" in content_type.lower() or content[:5] == b"%PDF-":
            return self._extract_pdf(content)
        return self.fallback.extract(content, content_type)

    @staticmethod
    def _extract_pdf(content: bytes) -> ExtractedText:
        try:
            reader = pypdf.PdfReader(BytesIO(content))
            text = " ".join(
                (page.extract_text() or "")
                for page in reader.pages[:_MAX_PDF_PAGES]  # bound a page bomb
            )
        except Exception:  # malformed / encrypted / truncated PDF
            return ExtractedText("", TextStatus.STORED_UNPARSED)
        text = " ".join(text.split())
        if len(text) < _MIN_PDF_TEXT:  # no extractable text layer (scanned image)
            return ExtractedText("", TextStatus.STORED_UNPARSED)
        return ExtractedText(text, TextStatus.PARSED)
