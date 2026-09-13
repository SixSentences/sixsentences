"""Security contracts for hostile PDF font data handled by pypdf."""

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.errors import LimitReachedError
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from sixsentences_server.acquisition.models import TextStatus
from sixsentences_server.acquisition.pdf import PdfTextExtractor


def _pdf_with_font(font: DictionaryObject, content_data: bytes) -> bytes:
    """Build a small, deterministic PDF that exercises one hostile font."""

    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    font_reference = writer._add_object(font)  # noqa: SLF001 - deterministic fixture
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    content = DecodedStreamObject()
    content.set_data(content_data)
    page[NameObject("/Contents")] = writer._add_object(  # noqa: SLF001
        content
    )
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _assert_dependency_limit_fails_closed(content: bytes) -> None:
    """Require pypdf's resource limit and the application's honest fallback."""

    with pytest.raises(LimitReachedError):
        PdfReader(BytesIO(content)).pages[0].extract_text()

    extracted = PdfTextExtractor().extract(content, "application/pdf")
    assert extracted.status is TextStatus.STORED_UNPARSED
    assert extracted.text == ""


def test_pdf_extraction_keeps_ordinary_font_content_readable() -> None:
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )

    extracted = PdfTextExtractor().extract(
        _pdf_with_font(
            font,
            b"BT /F1 12 Tf 5 60 Td (Research paper text remains readable.) Tj ET",
        ),
        "application/pdf",
    )

    assert extracted.status is TextStatus.PARSED
    assert extracted.text == "Research paper text remains readable."


def test_pdf_extraction_rejects_oversized_to_unicode_destination() -> None:
    # GHSA-fp3f-mc75-235c: 514 decoded bytes are just above pypdf's 512-byte
    # destination limit, without putting a genuinely memory-exhausting fixture in CI.
    to_unicode = DecodedStreamObject()
    to_unicode.set_data(
        b"begincmap\n1 beginbfrange\n<0041> <0041> <" + (b"0041" * 257) + b">\nendbfrange\nendcmap"
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): to_unicode,
        }
    )

    _assert_dependency_limit_fails_closed(_pdf_with_font(font, b"BT /F1 12 Tf <0041> Tj ET"))


def test_pdf_extraction_rejects_oversized_cid_width_range() -> None:
    # GHSA-fwg2-594c-jp42: the inclusive /W range contains 65,537 widths,
    # one more than pypdf's patched limit, so vulnerable expansion stays out of CI.
    descendant = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType2"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/W"): ArrayObject(
                [NumberObject(0), NumberObject(65_536), NumberObject(500)]
            ),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject([descendant]),
        }
    )

    _assert_dependency_limit_fails_closed(
        _pdf_with_font(font, b"BT /F1 12 Tf <" + (b"0001" * 32) + b"> Tj ET")
    )
