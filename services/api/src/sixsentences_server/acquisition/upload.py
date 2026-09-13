"""User-supplied documents: an uploaded PDF or a public PDF link, ingested with
verified metadata.

The pipeline mirrors the acquisition service (fetch -> store -> extract ->
ledger) but the provenance differs: the user supplied the file, so the ledger
row carries ``LegalBasis.USER_UPLOAD``. Metadata is never trusted from the PDF
alone (principle: real, resolvable sources): a DOI or arXiv id found in the
text is resolved against OpenAlex, and a title guess is verified by a title
search with an overlap check. Only a resolved work carries verified metadata;
without a match the document gets an honest synthetic work (id ``W0…``,
source "upload") so chat and citations can still reference it — clearly
marked unverified. The synthetic id space (``W0`` + digits) cannot collide
with OpenAlex ids, which never start with a zero.
"""

import hashlib
import math
import re
import statistics
import unicodedata
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

import pypdf
from PIL import Image
from pypdf._codecs import adobe_glyphs
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NullObject,
    StreamObject,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.acquisition.fetch import DocumentFetcher, HttpxFetcher
from sixsentences_server.acquisition.landing import pdf_url_from_html
from sixsentences_server.acquisition.models import (
    DocumentSource,
    LegalBasis,
    TextStatus,
)
from sixsentences_server.acquisition.pdf import PdfTextExtractor
from sixsentences_server.acquisition.store import DocumentStore, LocalDocumentStore
from sixsentences_server.acquisition.vision import ImageReadError, transcribe_image
from sixsentences_server.config import get_settings
from sixsentences_server.connectors.openalex import OpenAlexClient, sanitize_search_text
from sixsentences_server.core.db import DocumentRow, Run, SourceRecordRow, WorkRow
from sixsentences_server.core.limits import MAX_DOCUMENT_UPLOAD_BYTES
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.core.uploads import UnsafeImageError, decode_image

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>()\[\]{},;]+", re.IGNORECASE)
_ARXIV_RE = re.compile(r"arxiv[:\s/]+(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
# a bare arXiv id (YYMM.NNNNN) as it appears in filenames like 2307.03172v3.pdf
_ARXIV_BARE_RE = re.compile(r"\b(\d{2}(0[1-9]|1[0-2])\.\d{4,5})(v\d+)?\b")
_METADATA_CHARS = 12_000  # DOIs/ids live on the first pages
_TITLE_HEAD_CHARS = 240  # scholarly PDFs open with the title
_PDF_COVER_FRAGMENT_LIMIT = 800
_PDF_COVER_STREAM_LIMIT = 64
_PDF_COVER_ENCODED_BYTES = 1024 * 1024
_PDF_COVER_DECODED_BYTES = 512 * 1024
_PDF_COVER_OPERATION_LIMIT = 20_000
_PDF_COVER_FONT_LIMIT = 64
_PDF_COVER_CMAP_ENCODED_BYTES = 256 * 1024
_PDF_COVER_CMAP_DECODED_BYTES = 256 * 1024
_PDF_COVER_FONT_WIDTH_LIMIT = 4096
_PDF_COVER_CMAP_MAPPING_LIMIT = 4096
_PDF_COVER_CMAP_SOURCE_BYTES_LIMIT = 4
_PDF_COVER_CMAP_DESTINATION_BYTES_LIMIT = 8
_PDF_COVER_ENCODING_OUTPUT_CHARS_LIMIT = 4
_MAX_PDF_PAGES = 2_000


class _PdfCaptureBoundError(ValueError):
    """The untrusted cover exceeded the local metadata extraction budget."""


def validate_pdf_structure(content: bytes) -> None:
    """Reject data that only impersonates a PDF or cannot expose a page tree.

    This is deliberately separate from text extraction: a structurally valid
    scanned PDF may have no text layer and remains a supported document.
    """

    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4_096:]:
        raise UploadError("the uploaded file is not a structurally valid PDF")
    try:
        reader = pypdf.PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise UploadError("encrypted PDFs are not supported")
        page_count = len(reader.pages)
        if page_count < 1:
            raise UploadError("the PDF does not contain any pages")
        if page_count > _MAX_PDF_PAGES:
            raise UploadError(f"the PDF exceeds the {_MAX_PDF_PAGES:,}-page safety limit")
        # Resolve each bounded page dictionary so truncated object references
        # fail before bytes are persisted or exposed to another parser.
        for page in reader.pages:
            if str(page.get("/Type", "/Page")) != "/Page":
                raise UploadError("the PDF contains a malformed page tree")
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError("the uploaded file is not a structurally valid PDF") from exc


@dataclass
class _PdfCoverFontBudget:
    cmap_encoded_bytes: int = 0
    cmap_decoded_bytes: int = 0
    cmap_mappings: int = 0
    width_entries: int = 0


@dataclass(frozen=True)
class PdfCaptureMetadata:
    """Bounded metadata evidenced by the PDF itself.

    This is intentionally not a bibliographic resolver. Values come only from
    the embedded PDF Info dictionary or visible first-page structure. In
    particular, ``document_date`` is provenance about the file and must never
    be promoted to a publication date without an independent source.
    """

    title: str | None = None
    authors: tuple[str, ...] = ()
    abstract: str | None = None
    item_type: str | None = None
    publisher: str | None = None
    document_date: str | None = None
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PdfCoverFragment:
    text: str
    x: float
    y: float
    font_size: float


def _pdf_metadata_text(value: Any, limit: int) -> str:
    raw = value if isinstance(value, str) else str(value or "")
    normalized = unicodedata.normalize("NFKC", raw[: max(limit * 4, 256)])
    visible = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    return " ".join(visible.split()).strip()[:limit]


def _bounded_flate_decode(data: bytes, limit: int) -> bytes:
    """Decode one Flate stream without ever materializing more than ``limit``."""

    if limit <= 0:
        raise _PdfCaptureBoundError("PDF cover decoded-byte limit reached")
    last_error: zlib.error | None = None
    for window_bits in (zlib.MAX_WBITS, zlib.MAX_WBITS | 32, -zlib.MAX_WBITS):
        try:
            decompressor = zlib.decompressobj(window_bits)
            decoded = decompressor.decompress(data, limit + 1)
            if len(decoded) > limit or decompressor.unconsumed_tail:
                raise _PdfCaptureBoundError("PDF cover decoded-byte limit reached")
            remaining = limit + 1 - len(decoded)
            if remaining:
                decoded += decompressor.flush(remaining)
            if len(decoded) > limit:
                raise _PdfCaptureBoundError("PDF cover decoded-byte limit reached")
            if not decompressor.eof:
                raise zlib.error("incomplete Flate stream")
            return decoded
        except zlib.error as exc:
            last_error = exc
    raise _PdfCaptureBoundError("PDF cover Flate stream is malformed") from last_error


def _pdf_cover_streams(value: Any) -> list[StreamObject]:
    resolved = value.get_object() if hasattr(value, "get_object") else value
    if resolved is None or isinstance(resolved, NullObject):
        return []
    values = list(resolved) if isinstance(resolved, ArrayObject) else [resolved]
    if len(values) > _PDF_COVER_STREAM_LIMIT:
        raise _PdfCaptureBoundError("PDF cover has too many content streams")
    streams: list[StreamObject] = []
    for candidate in values:
        stream = candidate.get_object() if hasattr(candidate, "get_object") else candidate
        if not isinstance(stream, StreamObject):
            raise _PdfCaptureBoundError("PDF cover content is not a stream")
        streams.append(stream)
    return streams


def _pdf_cover_stream_data(
    stream: StreamObject,
    decoded_limit: int,
    encoded_limit: int,
) -> tuple[bytes, int]:
    raw = stream._data  # noqa: SLF001 - avoid an unbounded pypdf decode
    if not isinstance(raw, bytes):
        raise _PdfCaptureBoundError("PDF cover stream bytes are unavailable")
    if len(raw) > encoded_limit:
        raise _PdfCaptureBoundError("PDF cover encoded-byte limit reached")
    filter_value = stream.get("/Filter", stream.get("/F"))
    if filter_value is not None and hasattr(filter_value, "get_object"):
        filter_value = filter_value.get_object()
    filters = list(filter_value) if isinstance(filter_value, ArrayObject) else [filter_value]
    filters = [value for value in filters if value is not None]
    if not filters:
        if len(raw) > decoded_limit:
            raise _PdfCaptureBoundError("PDF cover decoded-byte limit reached")
        return raw, len(raw)
    if len(filters) != 1 or str(filters[0]) not in {"/FlateDecode", "/Fl"}:
        raise _PdfCaptureBoundError("PDF cover uses an unsupported content filter")
    decode_parms = stream.get("/DecodeParms", stream.get("/DP"))
    if decode_parms is not None and hasattr(decode_parms, "get_object"):
        decode_parms = decode_parms.get_object()
    if isinstance(decode_parms, ArrayObject):
        if not decode_parms:
            decode_parms = None
        elif len(decode_parms) == 1:
            decode_parms = decode_parms[0].get_object()
        else:
            raise _PdfCaptureBoundError("PDF cover decode parameters are ambiguous")
    if decode_parms is None:
        predictor = 1
    elif isinstance(decode_parms, DictionaryObject):
        try:
            predictor = int(decode_parms.get("/Predictor", 1))
        except (TypeError, ValueError) as exc:
            raise _PdfCaptureBoundError("PDF cover predictor is malformed") from exc
    else:
        raise _PdfCaptureBoundError("PDF cover decode parameters are malformed")
    if predictor != 1:
        raise _PdfCaptureBoundError("PDF cover predictors are not parsed for metadata")
    return _bounded_flate_decode(raw, decoded_limit), len(raw)


def _bounded_pdf_cover_content(page: Any, reader: pypdf.PdfReader) -> DecodedStreamObject:
    """Return operation-checked cover content without decoding XObjects."""

    streams = _pdf_cover_streams(page.get("/Contents"))
    combined = bytearray()
    encoded_total = 0
    for stream in streams:
        separator_size = 1 if combined else 0
        remaining = _PDF_COVER_DECODED_BYTES - len(combined) - separator_size
        decoded, encoded_size = _pdf_cover_stream_data(
            stream,
            remaining,
            _PDF_COVER_ENCODED_BYTES - encoded_total,
        )
        encoded_total += encoded_size
        if separator_size:
            combined.extend(b"\n")
        combined.extend(decoded)
    safe_stream = DecodedStreamObject()
    safe_stream.set_data(bytes(combined))
    parsed = ContentStream(safe_stream, reader)
    if len(parsed.operations) > _PDF_COVER_OPERATION_LIMIT:
        raise _PdfCaptureBoundError("PDF cover operation limit reached")
    return safe_stream


def _pdf_number(value: Any, *, label: str) -> int | float:
    resolved = value.get_object() if hasattr(value, "get_object") else value
    if not isinstance(resolved, (int, float)) or not math.isfinite(float(resolved)):
        raise _PdfCaptureBoundError(f"PDF cover {label} is malformed")
    return resolved


def _bounded_pdf_encoding(value: Any) -> Any:
    """Clone only the bounded encoding data pypdf needs for text decoding."""

    resolved = value.get_object() if hasattr(value, "get_object") else value
    if isinstance(resolved, NameObject):
        if len(str(resolved)) > 64:
            raise _PdfCaptureBoundError("PDF cover font encoding is oversized")
        return resolved
    if not isinstance(resolved, DictionaryObject):
        raise _PdfCaptureBoundError("PDF cover font encoding is malformed")
    safe = DictionaryObject()
    base_encoding = resolved.get("/BaseEncoding")
    if base_encoding is not None:
        base_encoding = (
            base_encoding.get_object() if hasattr(base_encoding, "get_object") else base_encoding
        )
        if not isinstance(base_encoding, NameObject):
            raise _PdfCaptureBoundError("PDF cover base encoding is malformed")
        if len(str(base_encoding)) > 64:
            raise _PdfCaptureBoundError("PDF cover base encoding is oversized")
        safe[NameObject("/BaseEncoding")] = base_encoding
    differences = resolved.get("/Differences")
    if differences is not None:
        differences = (
            differences.get_object() if hasattr(differences, "get_object") else differences
        )
        if not isinstance(differences, ArrayObject) or len(differences) > 512:
            raise _PdfCaptureBoundError("PDF cover encoding differences are oversized")
        safe_differences = ArrayObject()
        for entry in differences:
            entry = entry.get_object() if hasattr(entry, "get_object") else entry
            if isinstance(entry, int):
                if not 0 <= entry <= 255:
                    raise _PdfCaptureBoundError(
                        "PDF cover encoding difference index is out of range"
                    )
            elif isinstance(entry, NameObject):
                decoded_glyph = adobe_glyphs.get(str(entry))
                if (
                    decoded_glyph is None
                    or len(decoded_glyph) > _PDF_COVER_ENCODING_OUTPUT_CHARS_LIMIT
                ):
                    raise _PdfCaptureBoundError("PDF cover encoding difference is unsupported")
            else:
                raise _PdfCaptureBoundError("PDF cover encoding difference is malformed")
            safe_differences.append(entry)
        safe[NameObject("/Differences")] = safe_differences
    return safe


def _pdf_cmap_mapping_count(data: bytes) -> int:
    """Count and bound mappings before pypdf expands compact CMap syntax."""

    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    uncommented = b"\n".join(line.split(b"%", 1)[0] for line in normalized.split(b"\n"))

    def blocks(begin: bytes, end: bytes) -> list[bytes]:
        begin_pattern = rb"\b" + re.escape(begin) + rb"\b"
        end_pattern = rb"\b" + re.escape(end) + rb"\b"
        block_pattern = re.compile(
            begin_pattern + rb"(.*?)" + end_pattern,
            re.DOTALL,
        )
        matches = list(block_pattern.finditer(uncommented))
        if len(matches) != len(re.findall(begin_pattern, uncommented)) or len(matches) != len(
            re.findall(end_pattern, uncommented)
        ):
            raise _PdfCaptureBoundError("PDF cover ToUnicode map is malformed")
        return [match.group(1) for match in matches]

    def checked_hex(token: bytes, *, source: bool) -> int:
        if not token or len(token) % 2:
            raise _PdfCaptureBoundError("PDF cover ToUnicode code is malformed")
        byte_limit = (
            _PDF_COVER_CMAP_SOURCE_BYTES_LIMIT
            if source
            else _PDF_COVER_CMAP_DESTINATION_BYTES_LIMIT
        )
        if len(token) // 2 > byte_limit:
            raise _PdfCaptureBoundError("PDF cover ToUnicode code is oversized")
        return int(token, 16)

    def skip_space(value: bytes, position: int) -> int:
        while position < len(value) and value[position] in b" \t\n\f\0":
            position += 1
        return position

    def hex_token(value: bytes, position: int) -> tuple[bytes, int]:
        position = skip_space(value, position)
        if position >= len(value) or value[position] != ord("<"):
            raise _PdfCaptureBoundError("PDF cover ToUnicode code is malformed")
        end = value.find(b">", position + 1)
        if end < 0:
            raise _PdfCaptureBoundError("PDF cover ToUnicode code is malformed")
        token = value[position + 1 : end]
        if re.fullmatch(rb"[0-9A-Fa-f]+", token) is None:
            raise _PdfCaptureBoundError("PDF cover ToUnicode code is malformed")
        return token, end + 1

    count = 0
    for block in blocks(b"beginbfchar", b"endbfchar"):
        position = 0
        while (position := skip_space(block, position)) < len(block):
            source, position = hex_token(block, position)
            destination, position = hex_token(block, position)
            checked_hex(source, source=True)
            checked_hex(destination, source=False)
            count += 1
            if count > _PDF_COVER_CMAP_MAPPING_LIMIT:
                raise _PdfCaptureBoundError("PDF cover ToUnicode map is oversized")

    for block in blocks(b"beginbfrange", b"endbfrange"):
        position = 0
        while (position := skip_space(block, position)) < len(block):
            start_token, position = hex_token(block, position)
            stop_token, position = hex_token(block, position)
            start = checked_hex(start_token, source=True)
            stop = checked_hex(stop_token, source=True)
            if stop < start:
                raise _PdfCaptureBoundError("PDF cover ToUnicode range is reversed")
            range_size = stop - start + 1
            position = skip_space(block, position)
            if position < len(block) and block[position] == ord("<"):
                destination, position = hex_token(block, position)
                checked_hex(destination, source=False)
                mapped_entries = range_size
            elif position < len(block) and block[position] == ord("["):
                position += 1
                destination_count = 0
                while True:
                    position = skip_space(block, position)
                    if position >= len(block):
                        raise _PdfCaptureBoundError("PDF cover ToUnicode range is malformed")
                    if block[position] == ord("]"):
                        position += 1
                        break
                    destination, position = hex_token(block, position)
                    checked_hex(destination, source=False)
                    destination_count += 1
                    if destination_count > _PDF_COVER_CMAP_MAPPING_LIMIT:
                        raise _PdfCaptureBoundError("PDF cover ToUnicode map is oversized")
                if not destination_count:
                    raise _PdfCaptureBoundError("PDF cover ToUnicode range is malformed")
                # pypdf consumes every array entry, even if it exceeds the
                # declared source range. Charge whichever side is larger.
                mapped_entries = max(range_size, destination_count)
            else:
                raise _PdfCaptureBoundError("PDF cover ToUnicode range is malformed")
            count += mapped_entries
            if count > _PDF_COVER_CMAP_MAPPING_LIMIT:
                raise _PdfCaptureBoundError("PDF cover ToUnicode map is oversized")
    return count


def _bounded_pdf_to_unicode(
    value: Any,
    budget: _PdfCoverFontBudget,
) -> Any:
    resolved = value.get_object() if hasattr(value, "get_object") else value
    if isinstance(resolved, NameObject):
        if not str(resolved).startswith("/Identity"):
            raise _PdfCaptureBoundError("PDF cover ToUnicode name is unsupported")
        return resolved
    if not isinstance(resolved, StreamObject):
        raise _PdfCaptureBoundError("PDF cover ToUnicode map is malformed")
    decoded, encoded_size = _pdf_cover_stream_data(
        resolved,
        _PDF_COVER_CMAP_DECODED_BYTES - budget.cmap_decoded_bytes,
        _PDF_COVER_CMAP_ENCODED_BYTES - budget.cmap_encoded_bytes,
    )
    mapping_count = _pdf_cmap_mapping_count(decoded)
    if budget.cmap_mappings + mapping_count > _PDF_COVER_CMAP_MAPPING_LIMIT:
        raise _PdfCaptureBoundError("PDF cover ToUnicode map is oversized")
    budget.cmap_encoded_bytes += encoded_size
    budget.cmap_decoded_bytes += len(decoded)
    budget.cmap_mappings += mapping_count
    safe = DecodedStreamObject()
    safe.set_data(decoded)
    return safe


def _bounded_pdf_widths(value: Any, budget: _PdfCoverFontBudget) -> ArrayObject:
    resolved = value.get_object() if hasattr(value, "get_object") else value
    if not isinstance(resolved, ArrayObject):
        raise _PdfCaptureBoundError("PDF cover font widths are malformed")
    if budget.width_entries + len(resolved) > _PDF_COVER_FONT_WIDTH_LIMIT:
        raise _PdfCaptureBoundError("PDF cover font widths are oversized")
    safe = ArrayObject()
    for width in resolved:
        _pdf_number(width, label="font width")
        safe.append(width.get_object() if hasattr(width, "get_object") else width)
    budget.width_entries += len(safe)
    return safe


def _bounded_pdf_cid_widths(value: Any, budget: _PdfCoverFontBudget) -> ArrayObject:
    resolved = value.get_object() if hasattr(value, "get_object") else value
    if not isinstance(resolved, ArrayObject):
        raise _PdfCaptureBoundError("PDF cover CID widths are malformed")
    safe = ArrayObject()
    index = 0
    expanded = 0
    while index < len(resolved):
        start = int(_pdf_number(resolved[index], label="CID width start"))
        if not 0 <= start <= 0x10FFFF or index + 1 >= len(resolved):
            raise _PdfCaptureBoundError("PDF cover CID width range is malformed")
        second = (
            resolved[index + 1].get_object()
            if hasattr(resolved[index + 1], "get_object")
            else resolved[index + 1]
        )
        safe.append(resolved[index].get_object())
        if isinstance(second, ArrayObject):
            if start + len(second) > 0x110000:
                raise _PdfCaptureBoundError("PDF cover CID width range is out of bounds")
            if budget.width_entries + expanded + len(second) > _PDF_COVER_FONT_WIDTH_LIMIT:
                raise _PdfCaptureBoundError("PDF cover CID widths are oversized")
            safe_widths = ArrayObject()
            for width in second:
                _pdf_number(width, label="CID width")
                safe_widths.append(width.get_object() if hasattr(width, "get_object") else width)
            expanded += len(safe_widths)
            safe.append(safe_widths)
            index += 2
        else:
            if index + 2 >= len(resolved):
                raise _PdfCaptureBoundError("PDF cover CID width range is incomplete")
            stop = int(_pdf_number(second, label="CID width stop"))
            width = _pdf_number(resolved[index + 2], label="CID width")
            if stop < start or stop > 0x10FFFF:
                raise _PdfCaptureBoundError("PDF cover CID width range is invalid")
            expanded += stop - start + 1
            safe.extend([second, width])
            index += 3
        if budget.width_entries + expanded > _PDF_COVER_FONT_WIDTH_LIMIT:
            raise _PdfCaptureBoundError("PDF cover CID widths are oversized")
    budget.width_entries += expanded
    return safe


def _bounded_pdf_font(
    value: Any,
    budget: _PdfCoverFontBudget,
) -> DictionaryObject:
    font = value.get_object() if hasattr(value, "get_object") else value
    if not isinstance(font, DictionaryObject):
        raise _PdfCaptureBoundError("PDF cover font is malformed")
    subtype = font.get("/Subtype")
    subtype = (
        subtype.get_object() if subtype is not None and hasattr(subtype, "get_object") else subtype
    )
    if str(subtype) not in {"/Type1", "/MMType1", "/TrueType", "/Type0"}:
        raise _PdfCaptureBoundError("PDF cover font subtype is unsupported")
    safe = DictionaryObject()
    for key in ("/Type", "/Subtype", "/BaseFont"):
        value = font.get(key)
        if value is not None:
            value = value.get_object() if hasattr(value, "get_object") else value
            if not isinstance(value, NameObject):
                raise _PdfCaptureBoundError("PDF cover font name is malformed")
            safe[NameObject(key)] = value
    if "/Encoding" in font:
        safe[NameObject("/Encoding")] = _bounded_pdf_encoding(font["/Encoding"])
    if "/ToUnicode" in font:
        safe[NameObject("/ToUnicode")] = _bounded_pdf_to_unicode(font["/ToUnicode"], budget)
    if str(subtype) != "/Type0":
        if "/Widths" in font:
            safe[NameObject("/Widths")] = _bounded_pdf_widths(font["/Widths"], budget)
        first_char = font.get("/FirstChar")
        if first_char is not None:
            first = int(_pdf_number(first_char, label="first character"))
            if not 0 <= first <= 0x10FFFF:
                raise _PdfCaptureBoundError("PDF cover first character is out of range")
            safe[NameObject("/FirstChar")] = first_char.get_object()
        last_char = font.get("/LastChar")
        if last_char is not None:
            last = int(_pdf_number(last_char, label="last character"))
            if not 0 <= last <= 0x10FFFF:
                raise _PdfCaptureBoundError("PDF cover last character is out of range")
            safe[NameObject("/LastChar")] = last_char.get_object()
        return safe

    descendants = font.get("/DescendantFonts")
    descendants = (
        descendants.get_object()
        if descendants is not None and hasattr(descendants, "get_object")
        else descendants
    )
    if not isinstance(descendants, ArrayObject) or len(descendants) != 1:
        raise _PdfCaptureBoundError("PDF cover descendant fonts are malformed")
    descendant = descendants[0].get_object()
    if not isinstance(descendant, DictionaryObject):
        raise _PdfCaptureBoundError("PDF cover descendant font is malformed")
    descendant_subtype = descendant.get("/Subtype")
    descendant_subtype = (
        descendant_subtype.get_object()
        if descendant_subtype is not None and hasattr(descendant_subtype, "get_object")
        else descendant_subtype
    )
    if str(descendant_subtype) not in {"/CIDFontType0", "/CIDFontType2"}:
        raise _PdfCaptureBoundError("PDF cover descendant font subtype is unsupported")
    safe_descendant = DictionaryObject()
    for key in ("/Type", "/Subtype", "/BaseFont"):
        value = descendant.get(key)
        if value is not None:
            value = value.get_object() if hasattr(value, "get_object") else value
            if not isinstance(value, NameObject):
                raise _PdfCaptureBoundError("PDF cover descendant font name is malformed")
            safe_descendant[NameObject(key)] = value
    if "/DW" in descendant:
        _pdf_number(descendant["/DW"], label="CID default width")
        safe_descendant[NameObject("/DW")] = descendant["/DW"].get_object()
    if "/W" in descendant:
        safe_descendant[NameObject("/W")] = _bounded_pdf_cid_widths(descendant["/W"], budget)
    safe[NameObject("/DescendantFonts")] = ArrayObject([safe_descendant])
    return safe


def _bounded_pdf_cover_resources(page: Any) -> DictionaryObject:
    resources = page.get("/Resources")
    resources = (
        resources.get_object()
        if resources is not None and hasattr(resources, "get_object")
        else resources
    )
    safe_resources = DictionaryObject()
    if not isinstance(resources, DictionaryObject) or "/Font" not in resources:
        return safe_resources
    font_resources: Any = resources["/Font"]
    fonts = font_resources.get_object() if hasattr(font_resources, "get_object") else font_resources
    if not isinstance(fonts, DictionaryObject) or len(fonts) > _PDF_COVER_FONT_LIMIT:
        raise _PdfCaptureBoundError("PDF cover font resources are oversized")
    budget = _PdfCoverFontBudget()
    safe_fonts = DictionaryObject()
    for name, font in fonts.items():
        if not isinstance(name, NameObject):
            raise _PdfCaptureBoundError("PDF cover font resource name is malformed")
        safe_fonts[name] = _bounded_pdf_font(font, budget)
    if safe_fonts:
        safe_resources[NameObject("/Font")] = safe_fonts
    return safe_resources


def _pdf_info_value(info: Any, key: str) -> Any:
    try:
        return info.get(key, "") if info else ""
    except Exception:  # noqa: BLE001 - malformed Info dictionaries are untrusted
        return ""


def _credible_pdf_title(value: str | None) -> str | None:
    title = _pdf_metadata_text(value, 500)
    if not 10 <= len(title) <= 500:
        return None
    if title.casefold().endswith((".pdf", ".dvi", ".tex", ".doc")):
        return None
    if sum(character.isalpha() for character in title) < len(title) * 0.45:
        return None
    return title


_PDF_AUTHOR_ORG_WORDS = re.compile(
    r"\b(?:academy|association|center|centre|college|company|corporation|department|"
    r"division|faculty|foundation|group|hospital|inc|institute|laboratory|lab|"
    r"latex|microsoft|openai|research|school|society|university|writer)\b",
    re.IGNORECASE,
)


def _credible_pdf_author(value: str) -> str | None:
    author = _pdf_metadata_text(value, 200)
    tokens = author.split()
    if (
        not 2 <= len(tokens) <= 8
        or "@" in author
        or re.search(r"https?://|\d", author, re.IGNORECASE)
        or _PDF_AUTHOR_ORG_WORDS.search(author)
        or sum(character.isalpha() for character in author) < len(author) * 0.65
    ):
        return None
    word_tokens = [re.sub(r"[^\w'-]", "", token, flags=re.UNICODE) for token in tokens]
    if sum(bool(token) and token.upper() == token for token in word_tokens) >= len(tokens):
        return None
    return author


def _embedded_pdf_authors(value: Any) -> tuple[str, ...]:
    raw = _pdf_metadata_text(value, 2_000)
    if not raw:
        return ()
    candidates = re.split(r"\s*(?:;|\n|\band\b)\s*", raw, flags=re.IGNORECASE)
    authors: list[str] = []
    seen: set[str] = set()
    for candidate in candidates[:50]:
        author = _credible_pdf_author(candidate)
        identity = author.casefold() if author else ""
        if author and identity not in seen:
            authors.append(author)
            seen.add(identity)
    return tuple(authors)


def _pdf_document_date(info: Any) -> str | None:
    """Return an embedded file date for provenance, never publication dating."""

    try:
        created = info.creation_date if info is not None else None
    except Exception:  # noqa: BLE001 - malformed Info values are untrusted input
        created = None
    if isinstance(created, datetime):
        return created.date().isoformat()
    raw = _pdf_metadata_text(_pdf_info_value(info, "/CreationDate"), 80)
    match = re.match(r"^D:(\d{4})(\d{2})?(\d{2})?", raw)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2) or "1")
    day = int(match.group(3) or "1")
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _pdf_cover_title(
    fragments: list[_PdfCoverFragment], page_height: float
) -> tuple[str | None, set[int]]:
    abstract = next(
        (fragment for fragment in fragments if fragment.text.casefold() == "abstract"),
        None,
    )
    floor = abstract.y if abstract is not None else page_height * 0.52
    candidates = [
        (index, fragment)
        for index, fragment in enumerate(fragments)
        if floor < fragment.y < page_height * 0.97
        and "@" not in fragment.text
        and len(fragment.text) <= 500
    ]
    if not candidates:
        return None, set()
    sizes = [fragment.font_size for _, fragment in candidates if fragment.font_size > 0]
    if not sizes:
        return None, set()
    maximum = max(sizes)
    median = statistics.median(sizes)
    if maximum < max(11.0, median * 1.15):
        return None, set()
    selected = [
        (index, fragment) for index, fragment in candidates if fragment.font_size >= maximum - 0.6
    ]
    selected.sort(key=lambda item: (-item[1].y, item[1].x))
    title = _credible_pdf_title(" ".join(fragment.text for _, fragment in selected))
    return title, {index for index, _ in selected} if title else set()


def _pdf_cover_authors(
    fragments: list[_PdfCoverFragment], title_indexes: set[int]
) -> tuple[str, ...]:
    if not title_indexes:
        return ()
    title_floor = min(fragments[index].y for index in title_indexes)
    abstract = next(
        (fragment for fragment in fragments if fragment.text.casefold() == "abstract"),
        None,
    )
    abstract_ceiling = abstract.y if abstract is not None else title_floor - 180
    band = [
        fragment
        for index, fragment in enumerate(fragments)
        if index not in title_indexes and abstract_ceiling < fragment.y < title_floor
    ]
    if not band:
        return ()
    repeated = {
        identity
        for identity in {_pdf_metadata_text(fragment.text, 200).casefold() for fragment in band}
        if sum(_pdf_metadata_text(candidate.text, 200).casefold() == identity for candidate in band)
        >= 2
    }
    authors: list[str] = []
    email_fragments = [fragment for fragment in band if "@" in fragment.text]
    for email in email_fragments[:50]:
        candidates = sorted(
            (
                fragment
                for fragment in band
                if fragment.y > email.y
                and fragment.y - email.y <= 100
                and abs(fragment.x - email.x) <= 90
                and fragment.text.casefold() not in repeated
            ),
            key=lambda fragment: (
                fragment.y - email.y,
                abs(fragment.x - email.x),
            ),
        )
        author = next(
            (
                candidate
                for fragment in candidates
                if (candidate := _credible_pdf_author(fragment.text)) is not None
            ),
            None,
        )
        if author and author.casefold() not in {item.casefold() for item in authors}:
            authors.append(author)
    if authors:
        return tuple(authors[:50])

    # Papers without printed email addresses commonly place all authors on the
    # first text row below the title. Accept only a row made entirely of
    # person-like fragments; affiliations and prose therefore fail closed.
    rows: dict[int, list[_PdfCoverFragment]] = {}
    for fragment in band:
        rows.setdefault(round(fragment.y), []).append(fragment)
    for _row_y, row in sorted(rows.items(), reverse=True):
        row.sort(key=lambda fragment: fragment.x)
        row_authors = [
            author
            for fragment in row
            if (author := _credible_pdf_author(fragment.text)) is not None
        ]
        if len(row) >= 2 and len(row_authors) == len(row):
            return tuple(dict.fromkeys(row_authors))[:50]
    return ()


def _pdf_cover_abstract(raw_text: str) -> str | None:
    match = re.search(
        r"(?ims)^\s*abstract\s*$\s*(.+?)(?=^\s*(?:1|I)[.)]?\s+introduction\b)",
        raw_text,
    )
    if not match:
        return None
    abstract = _pdf_metadata_text(match.group(1), 2_000)
    return abstract if len(abstract) >= 40 else None


def extract_pdf_capture_metadata(content: bytes) -> PdfCaptureMetadata:
    """Extract bounded, fill-only capture metadata from one verified PDF.

    Only the first page and PDF Info dictionary are inspected. The function
    performs no network request, persists no page text and returns empty values
    for malformed, encrypted or structurally ambiguous files.
    """

    try:
        reader = pypdf.PdfReader(BytesIO(content))
        if not reader.pages:
            return PdfCaptureMetadata()
        info = reader.metadata
        page = reader.pages[0]
        page[NameObject("/Contents")] = _bounded_pdf_cover_content(page, reader)
        page[NameObject("/Resources")] = _bounded_pdf_cover_resources(page)
        fragments: list[_PdfCoverFragment] = []

        def visit_text(
            text: str,
            _cm: list[float],
            tm: list[float],
            _font: dict[str, Any] | None,
            font_size: float,
        ) -> None:
            if len(fragments) >= _PDF_COVER_FRAGMENT_LIMIT:
                return
            cleaned = _pdf_metadata_text(text, 500)
            if not cleaned:
                return
            try:
                x, y, size = float(tm[4]), float(tm[5]), float(font_size)
            except (IndexError, TypeError, ValueError):
                return
            if not all(math.isfinite(value) for value in (x, y, size)):
                return
            fragments.append(_PdfCoverFragment(cleaned, x, y, size))

        raw_text = page.extract_text(visitor_text=visit_text) or ""
        page_height = float(page.mediabox.height)
    except Exception:  # noqa: BLE001 - hostile/malformed PDF metadata fails closed
        return PdfCaptureMetadata()

    embedded_title = _credible_pdf_title(_pdf_info_value(info, "/Title"))
    cover_title, title_indexes = _pdf_cover_title(fragments, page_height)
    title = embedded_title or cover_title
    embedded_authors = _embedded_pdf_authors(_pdf_info_value(info, "/Author"))
    cover_authors = _pdf_cover_authors(fragments, title_indexes)
    authors = embedded_authors or cover_authors
    abstract = _pdf_cover_abstract(raw_text)
    normalized_first_page = _pdf_metadata_text(raw_text, 20_000)
    item_type = (
        "preprint"
        if re.search(r"\bpreprint\b", normalized_first_page, re.IGNORECASE)
        else "report"
        if re.search(
            r"\b(?:technical|working)\s+(?:paper|report)\b",
            normalized_first_page,
            re.IGNORECASE,
        )
        else None
    )
    publisher = _pdf_metadata_text(_pdf_info_value(info, "/Publisher"), 300) or None
    document_date = _pdf_document_date(info)
    fields = tuple(
        field
        for field, present in (
            ("pdf.info.title", bool(embedded_title)),
            ("pdf.page_1.title", bool(cover_title and not embedded_title)),
            ("pdf.info.author", bool(embedded_authors)),
            ("pdf.page_1.authors", bool(cover_authors and not embedded_authors)),
            ("pdf.page_1.abstract", bool(abstract)),
            ("pdf.page_1.item_type", bool(item_type)),
            ("pdf.info.publisher", bool(publisher)),
            ("pdf.info.creation_date", bool(document_date)),
        )
        if present
    )
    return PdfCaptureMetadata(
        title=title,
        authors=authors,
        abstract=abstract,
        item_type=item_type,
        publisher=publisher,
        document_date=document_date,
        fields=fields,
    )


class UploadError(ValueError):
    """User-facing ingest failure (bad file, dead link, not a PDF)."""


def is_verified_work_id(work_id: str) -> bool:
    """Synthetic upload works start with W0; OpenAlex ids never do."""
    return not work_id.startswith("W0")


def _synthetic_work_id(checksum: str) -> str:
    # deterministic per file: the same PDF uploaded twice is the same work
    return f"W0{int(checksum[:12], 16) % 10**12:012d}"


def _find_doi(text: str) -> str | None:
    match = _DOI_RE.search(text[:_METADATA_CHARS])
    return match.group(0).rstrip(".,;:") if match else None


def _find_arxiv_id(text: str) -> str | None:
    match = _ARXIV_RE.search(text[:_METADATA_CHARS])
    if match:
        return match.group(1)
    # the id often hides in a filename or link, e.g. 2307.03172v3.pdf —
    # the bare pattern is month-validated so page numbers cannot match
    bare = _ARXIV_BARE_RE.search(text[:_METADATA_CHARS])
    return bare.group(1) if bare else None


def _pdf_info_title(content: bytes) -> str | None:
    """The PDF's own /Title metadata, when it looks like a real title."""
    try:
        info = pypdf.PdfReader(BytesIO(content)).metadata
        title = (info.title or "").strip() if info else ""
    except Exception:  # noqa: BLE001 - malformed metadata never blocks ingest
        return None
    if not 10 <= len(title) <= 250:
        return None
    if title.lower().endswith((".pdf", ".dvi", ".tex", ".doc")):
        return None
    if sum(ch.isalpha() for ch in title) < len(title) * 0.5:
        return None
    return title


# arXiv stamps a margin note ("arXiv:2307.03172v3 [cs.CL] 20 Nov 2023") that
# pypdf often extracts ahead of the title — strip it from title candidates
_ARXIV_STAMP = re.compile(
    r"arxiv:\s*\S+|\[(cs|stat|math|econ|eess|physics|q-bio|q-fin|astro-ph|cond-mat)"
    r"[^\]]*\]|\b\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}\b",
    re.IGNORECASE,
)


def _clean_head(text: str) -> str:
    """The head of page one with arXiv margin stamps removed."""
    head = _ARXIV_STAMP.sub(" ", text[:400])
    return " ".join(head.split())[:_TITLE_HEAD_CHARS].strip()


def _transcript_title(text: str) -> str | None:
    """Vision transcripts keep real lines; the first substantial one (often
    the page's heading) names a photographed source best."""
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if len(cleaned) >= 4 and sum(ch.isalpha() for ch in cleaned) >= 3:
            return cleaned[:120]
    return None


def _guess_title(text: str, fallback: str) -> str:
    """Display title when nothing resolves: scholarly PDFs open with the
    title, so take the head of page one (the extractor collapses newlines,
    so there are no lines to split on)."""
    head = _clean_head(text)
    if len(head) >= 20 and sum(ch.isalpha() for ch in head) >= len(head) * 0.6:
        cut = head[:120]
        if " " in cut[60:]:  # end on a word boundary past a sane minimum
            cut = cut[: cut.rindex(" ")]
        return cut
    return fallback


def _title_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2}


def _title_matches(candidate: str, hit_title: str) -> bool:
    """A search hit only counts when its title is contained in the page head —
    containment, not symmetric overlap: the head also carries authors and
    affiliations that must not dilute the match."""
    hit, head = _title_tokens(hit_title), _title_tokens(candidate)
    if len(hit) < 3 or not head:
        return False
    return len(hit & head) / len(hit) >= 0.7


def _resolve_metadata(
    text: str,
    filename: str,
    client: OpenAlexClient,
    *,
    url: str | None = None,
    content: bytes | None = None,
) -> tuple[WorkRecord | None, str | None]:
    """Resolve a PDF and report which explicit identity established the match."""
    doi = _find_doi(text)
    if doi:
        work = client.get_work(f"doi:{doi}")
        if work is not None:
            return work, "doi"
    # arXiv id: page text first, then the filename / source link. OpenAlex
    # has no arxiv: namespace — every arXiv id resolves via its DataCite DOI.
    arxiv_id = _find_arxiv_id(text) or _find_arxiv_id(f"{filename} {url or ''}")
    if arxiv_id:
        work = client.get_work(f"doi:10.48550/arXiv.{arxiv_id}")
        if work is not None:
            return work, "arxiv"
    # title search: the PDF's own metadata title, else the head of page one
    candidates = [
        title
        for title in (
            _pdf_info_title(content) if content else None,
            _clean_head(text),
        )
        if title and len(title) >= 20
    ]
    for candidate in candidates:
        try:
            hits = client.search(sanitize_search_text(candidate), limit=1)
        except Exception:  # noqa: BLE001 - resolution is best-effort
            hits = []
        if hits and _title_matches(candidate, hits[0].title):
            return hits[0], "title"
    return None, None


def _fetch_pdf(url: str, fetcher: DocumentFetcher) -> tuple[bytes, str]:
    """Download a public PDF link; follow one landing page to its PDF."""
    blob = fetcher.fetch(url)
    if blob is None:
        raise UploadError("the link could not be fetched (private, dead, or too large)")
    content, final_url = blob.content, blob.final_url or url
    if content[:5] != b"%PDF-" and "pdf" not in blob.content_type.lower():
        pdf_url = pdf_url_from_html(content, final_url)
        pdf_blob = fetcher.fetch(pdf_url) if pdf_url else None
        if pdf_blob is None or pdf_blob.content[:5] != b"%PDF-":
            raise UploadError("the link did not resolve to a PDF")
        content, final_url = (
            pdf_blob.content,
            pdf_blob.final_url or pdf_url or final_url,
        )
    return content, final_url


def attach_document_to_run(session: Session, doc: DocumentRow, run_id: int) -> None:
    """Bind a (possibly pending) document to a run and register its work as a
    source record so it flows into the run's works, context and citations."""
    doc.run_id = run_id
    run = session.get(Run, run_id)
    if run is not None:
        doc.project_id = run.project_id
    run_org = doc.org_id
    exists = session.scalar(
        select(SourceRecordRow.id).where(
            SourceRecordRow.run_id == run_id,
            SourceRecordRow.work_id == doc.work_id,
        )
    )
    if exists is None:
        session.add(
            SourceRecordRow(
                org_id=run_org,
                run_id=run_id,
                work_id=doc.work_id,
                source="upload",
                corpus_version=None,
            )
        )
    session.flush()


def _pdf_from_image(content: bytes) -> bytes | None:
    """A photo or screenshot becomes a one-page PDF, flattened onto white."""
    try:
        image, _ = decode_image(content)
        if image.mode in ("RGBA", "LA", "P", "PA"):
            rgba = image.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            image = flat
        elif image.mode != "RGB":
            image = image.convert("RGB")
        out = BytesIO()
        image.save(out, format="PDF", resolution=150.0)
        return out.getvalue()
    except UnsafeImageError:
        return None


def ingest_document(
    session: Session,
    *,
    org_id: int,
    run_id: int | None,
    content: bytes | None = None,
    url: str | None = None,
    filename: str = "",
    store: DocumentStore | None = None,
    fetcher: DocumentFetcher | None = None,
    oa_client: OpenAlexClient | None = None,
    storage_check: Callable[[int, str], None] | None = None,
    provider_cost_guard: Callable[[float], None] | None = None,
    provider_cost_sink: Callable[[str, str, float, str], None] | None = None,
    private_metadata: bool = False,
    existing_document: Callable[[WorkRecord, str, str | None], DocumentRow | None] | None = None,
) -> DocumentRow:
    """Ingest one user-supplied PDF (bytes or public link) into the ledger."""
    settings = get_settings()
    store = store or LocalDocumentStore(settings.documents_dir)
    source_url = None
    if content is None:
        if not url:
            raise UploadError("provide a PDF file or a link to one")
        fetcher = fetcher or HttpxFetcher(mailto=settings.openalex_mailto)
        content, source_url = _fetch_pdf(url, fetcher)
    if len(content) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise UploadError("the PDF is larger than 100 MB")
    original_image: bytes | None = None
    if content[:5] != b"%PDF-":
        # a photographed or scanned source (a phone shot of a lecturer's
        # PDF): the image becomes a one-page PDF so the reader, quotes and
        # the library work, and its text comes from a faithful transcription
        pdf = _pdf_from_image(content)
        if pdf is None:
            raise UploadError("that file is not a PDF or a readable image")
        original_image = content
        content = pdf
    validate_pdf_structure(content)

    preview_checksum = hashlib.sha256(content).hexdigest()
    if original_image is not None:
        try:
            text = transcribe_image(
                original_image,
                before_request=provider_cost_guard,
                on_cost=provider_cost_sink,
            )
        except ImageReadError as exc:
            raise UploadError(str(exc)) from exc
        text_status = TextStatus.PARSED
    else:
        extracted = PdfTextExtractor().extract(content, "application/pdf")
        text = extracted.text
        text_status = extracted.status
    oa_client = oa_client or OpenAlexClient(
        mailto=settings.openalex_mailto, api_key=settings.openalex_api_key
    )
    resolution = (
        (None, None)
        if private_metadata
        else (
            _resolve_metadata(text, filename, oa_client, url=source_url or url, content=content)
            if text
            else (None, None)
        )
    )
    record, identity_kind = resolution
    if record is None:  # honest synthetic work: referencable but unverified
        title = (
            "Captured paper"
            if private_metadata
            else (
                _pdf_info_title(content)
                or (_transcript_title(text) if original_image is not None and text else None)
                or _guess_title(text, filename or "Uploaded document")
            )
        )
        synthetic_checksum = (
            hashlib.sha256(f"{org_id}:{preview_checksum}".encode()).hexdigest()
            if private_metadata
            else preview_checksum
        )
        record = WorkRecord(
            id=_synthetic_work_id(synthetic_checksum),
            title=title,
            source="browser_capture" if private_metadata else "upload",
        )
    if existing_document is not None:
        existing = existing_document(record, preview_checksum, identity_kind)
        if existing is not None:
            return existing
    if storage_check is not None:
        storage_check(len(content), preview_checksum)
    checksum, path = store.put(content)
    if text_status is TextStatus.PARSED and text:
        store.put_text(checksum, text)
    if session.get(WorkRow, record.id) is None:
        session.add(
            WorkRow(
                id=record.id,
                doi=record.doi,
                title=record.title,
                year=record.year,
                payload=(
                    {
                        "id": record.id,
                        "title": "Captured paper",
                        "source": "browser_capture",
                    }
                    if private_metadata
                    else record.model_dump(mode="json")
                ),
            )
        )
        # the work must land before the ledger row references it: works and
        # documents share no relationship(), so a single flush would order
        # them by mapper name (DocumentRow < WorkRow) and trip the foreign key
        session.flush()

    doc = DocumentRow(
        org_id=org_id,
        run_id=None,
        work_id=record.id,
        status="retrieved",
        source=DocumentSource.UPLOAD.value,
        legal_basis=LegalBasis.USER_UPLOAD.value,
        license=None,
        version=None,
        url=source_url or url,
        content_type="application/pdf",
        checksum=checksum,
        byte_size=len(content),
        storage_path=path,
        text_status=text_status.value,
    )
    session.add(doc)
    session.flush()
    if run_id is not None:
        attach_document_to_run(session, doc, run_id)
    return doc
