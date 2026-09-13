"""Writer source/PDF selection bounds, normalization and provenance."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import sixsentences_server.acquisition.pdf as pdf_module
import sixsentences_server.api.app as app_module
from sixsentences_server.acquisition.pdf import extract_page_text_range
from sixsentences_server.api.app import (
    WriterChatSelection,
    _verified_writer_chat_selection,
)
from sixsentences_server.writer.selection import (
    WRITER_SELECTION_MAX_CHARACTERS,
    normalize_pdf_selection_text,
    pdf_selection_matches_text,
    source_region_matches_pdf_quote,
)


def _mini_pdf(text: str) -> bytes:
    """Return a minimal one-page PDF with extractable Helvetica text."""

    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(result))
        result += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(result)
    result += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        result += f"{offset:010d} 00000 n \n".encode()
    result += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF"
    ).encode()
    return bytes(result)


def test_pdf_matching_repairs_watk_layout_artifacts_only() -> None:
    copied = "unser- \n iös und Reini- \r\n gungstipps, aber evidence-based"

    assert normalize_pdf_selection_text(copied) == (
        "unseriös und Reinigungstipps, aber evidence-based"
    )
    assert pdf_selection_matches_text(
        copied,
        "Die Hinweise sind unser- \n iös und Reini- \n gungstipps, aber evidence-based.",
    )
    assert pdf_selection_matches_text(
        "The findings use “careful” checks—without invention.",
        'The \ufb01ndings use "careful" checks-without invention.',
    )
    assert not pdf_selection_matches_text(
        "fabricated unrelated wording",
        "The findings use careful checks without invention.",
    )


def test_writer_page_range_preserves_line_end_hyphen_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def extract_text(self) -> str:
            return "unser- \n iös und Reini- \n gungstipps"

    class FakeReader:
        pages = [FakePage()]

    monkeypatch.setattr(pdf_module.pypdf, "PdfReader", lambda _stream: FakeReader())
    extracted = extract_page_text_range(b"pdf", 1, 1)

    assert extracted == ["unser- \n iös und Reini- \n gungstipps"]
    assert pdf_selection_matches_text(
        "unser- \n iös und Reini- \n gungstipps",
        extracted[0],
    )
    pdf_path = tmp_path / "writer.pdf"
    pdf_path.write_bytes(b"pdf")
    payload = _verified_writer_chat_selection(
        WriterChatSelection(
            kind="pdf",
            quote="unser- \n iös und Reini- \n gungstipps",
            page=1,
        ),
        project_files={"main.tex": "unseriös und Reinigungstipps"},
        pdf_path=pdf_path,
        synctex_path=tmp_path / "missing.synctex.gz",
        compiled_pdf_current=True,
    )
    assert payload is not None
    assert payload["quote"] == "unser- \n iös und Reini- \n gungstipps"


def test_writer_selection_schema_rejects_over_bound_and_inconsistent_ranges() -> None:
    with pytest.raises(ValidationError):
        WriterChatSelection(
            kind="pdf",
            quote="x" * (WRITER_SELECTION_MAX_CHARACTERS + 1),
            page=1,
        )
    with pytest.raises(ValidationError):
        WriterChatSelection(kind="pdf", quote="bounded passage", page=1, page_end=40)
    with pytest.raises(ValidationError):
        WriterChatSelection(
            kind="pdf",
            quote="first\n\nsecond",
            page=1,
            page_end=2,
            segments=[{"quote": "different", "page": 1}],
        )
    with pytest.raises(ValidationError):
        WriterChatSelection(
            kind="source",
            quote="selected source",
            path="../outside.tex",
            line=1,
        )


def test_synctex_source_binding_rejects_reordered_bag_of_words() -> None:
    quote = (
        "This study examines the model performance under realistic deployment "
        "conditions using robust evaluation methods."
    )
    reordered = (
        "This model study uses robust methods. The evaluation examines deployment "
        "performance under different conditions. "
    ) * 80

    assert not source_region_matches_pdf_quote(reordered, 1, quote)
    assert source_region_matches_pdf_quote(
        "The preface.\n" + quote + "\nThe appendix.",
        2,
        quote,
    )
    assert not source_region_matches_pdf_quote(
        ("alpha bravo charlie delta echo foxtrot golf hotel india totally unrelated ending words"),
        1,
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima",
    )


def test_source_selection_is_bound_to_the_reported_current_line(tmp_path: Path) -> None:
    selection = WriterChatSelection(
        kind="source",
        quote="Complete selected paragraph.",
        path="main.tex",
        line=2,
    )
    payload = _verified_writer_chat_selection(
        selection,
        project_files={"main.tex": "Before.\nComplete selected paragraph.\nAfter."},
        pdf_path=tmp_path / "missing.pdf",
        synctex_path=tmp_path / "missing.synctex.gz",
        compiled_pdf_current=False,
    )
    assert payload is not None
    assert payload["quote"] == "Complete selected paragraph."
    assert payload["line_end"] == 2

    with pytest.raises(HTTPException, match="no longer matches"):
        _verified_writer_chat_selection(
            selection.model_copy(update={"line": 1}),
            project_files={"main.tex": "Before.\nComplete selected paragraph.\nAfter."},
            pdf_path=tmp_path / "missing.pdf",
            synctex_path=tmp_path / "missing.synctex.gz",
            compiled_pdf_current=False,
        )


def test_source_selection_verification_is_linear_for_repeated_source(
    tmp_path: Path,
) -> None:
    source = "abcdefgh" * 250_000
    selection = WriterChatSelection(
        kind="source",
        quote="abcdefgh",
        path="main.tex",
        line=1,
    )

    started = time.perf_counter()
    payload = _verified_writer_chat_selection(
        selection,
        project_files={"main.tex": source},
        pdf_path=tmp_path / "missing.pdf",
        synctex_path=tmp_path / "missing.synctex.gz",
        compiled_pdf_current=False,
    )
    elapsed = time.perf_counter() - started

    assert payload is not None
    assert payload["quote"] == selection.quote
    assert elapsed < 2.0


def test_pdf_selection_requires_current_compilation_and_verified_page_text(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "writer.pdf"
    pdf_path.write_bytes(_mini_pdf("Complete rendered selection on this page."))
    selection = WriterChatSelection(
        kind="pdf",
        quote="Complete rendered selection",
        page=1,
    )

    with pytest.raises(HTTPException, match="compile the current manuscript"):
        _verified_writer_chat_selection(
            selection,
            project_files={"main.tex": "Complete rendered selection"},
            pdf_path=pdf_path,
            synctex_path=tmp_path / "missing.synctex.gz",
            compiled_pdf_current=False,
        )

    payload = _verified_writer_chat_selection(
        selection,
        project_files={"main.tex": "Complete rendered selection"},
        pdf_path=pdf_path,
        synctex_path=tmp_path / "missing.synctex.gz",
        compiled_pdf_current=True,
    )
    assert payload is not None and payload["quote"] == selection.quote

    with pytest.raises(HTTPException, match="could not be verified"):
        _verified_writer_chat_selection(
            selection.model_copy(update={"quote": "Wrong fabricated selection"}),
            project_files={"main.tex": "Complete rendered selection"},
            pdf_path=pdf_path,
            synctex_path=tmp_path / "missing.synctex.gz",
            compiled_pdf_current=True,
        )
    with pytest.raises(HTTPException, match="outside the compiled manuscript"):
        _verified_writer_chat_selection(
            selection.model_copy(update={"page": 2}),
            project_files={"main.tex": "Complete rendered selection"},
            pdf_path=pdf_path,
            synctex_path=tmp_path / "missing.synctex.gz",
            compiled_pdf_current=True,
        )


def test_unrelated_synctex_coordinate_is_not_promoted_to_source_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote = "Complete rendered selection on this page."
    pdf_path = tmp_path / "writer.pdf"
    pdf_path.write_bytes(_mini_pdf(quote))
    synctex_path = tmp_path / "writer.synctex.gz"
    synctex_path.write_bytes(b"placeholder")
    source = (
        "An unrelated opening paragraph with completely different terms.\n"
        + ("filler context without matching vocabulary\n" * 300)
        + quote
    )
    monkeypatch.setattr(
        app_module,
        "synctex_inverse",
        lambda **_kwargs: {"path": "main.tex", "line": 1, "column": 1},
    )

    payload = _verified_writer_chat_selection(
        WriterChatSelection(
            kind="pdf",
            quote=quote,
            page=1,
            pdf_x=72,
            pdf_y=720,
        ),
        project_files={"main.tex": source},
        pdf_path=pdf_path,
        synctex_path=synctex_path,
        compiled_pdf_current=True,
    )

    assert payload is not None
    assert "source_path" not in payload
    assert "source_line" not in payload
    assert not source_region_matches_pdf_quote(source, 1, quote)
