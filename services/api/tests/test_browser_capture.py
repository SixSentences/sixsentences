"""Least-privilege Browser Capture pairing, provenance and idempotency."""

from __future__ import annotations

import base64
import hashlib
import time as stdlib_time
import zlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event, Lock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
    NumberObject,
)
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session as OrmSession

import sixsentences_server.api.app as api_app
from sixsentences_server.acquisition.models import ExtractedText, TextStatus
from sixsentences_server.acquisition.upload import _pdf_metadata_text, extract_pdf_capture_metadata
from sixsentences_server.api.app import (
    _LIBRARY_PAPER_METADATA_LOCK,
    _LIBRARY_WEB_METADATA_LOCK,
    BrowserCapturePaperCreate,
    _browser_capture_web_url,
    _capture_doi_identity,
    _capture_transaction_lock,
    _enforce_browser_capture_pdf_size,
    _merge_capture_authors,
    _privacy_export_tables,
    create_app,
)
from sixsentences_server.config import Settings
from sixsentences_server.core.auth import create_companion_code, exchange_companion_code
from sixsentences_server.core.db import (
    BrowserCapturedPaperMetadataRow,
    BrowserCaptureReceiptRow,
    DocumentRow,
    LibraryWebSourceRow,
    Run,
    User,
    WorkRow,
    db_session,
)
from sixsentences_server.core.limits import (
    MAX_BROWSER_CAPTURE_PDF_BASE64_CHARS,
    MAX_BROWSER_CAPTURE_PDF_BYTES,
    MAX_REQUEST_BODY_BYTES,
)
from sixsentences_server.core.models import WorkRecord

PASSWORD = "StrongPass123!"
VERIFIER = "v" * 43
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
)
STATE = "browser-state-00000001"
REDIRECT = "https://abcdefghijklmnop.chromiumapp.org/"
ARXIV_2601_23265_BYTES = 42568953
GPT1_PDF_URL = (
    "https://cdn.openai.com/research-covers/language-unsupervised/language_understanding_paper.pdf"
)
GPT1_SOURCE_SHA256 = "eb81a65e0856bd38e855e1f1de6ee12e7c3eb92ae05c8270d345a475beb631d0"
STORE_EXTENSION_ID = "nniehinpekncehhibpmpfmkhfoednhia"
STORE_REDIRECT = f"https://{STORE_EXTENSION_ID}.chromiumapp.org/"


def test_browser_capture_pdf_limits_cover_exact_arxiv_and_fail_closed_above_50_mib() -> None:

    class SizedPdf:
        def __init__(self, size: int) -> None:
            self.size = size

        def __len__(self) -> int:
            return self.size

    assert MAX_BROWSER_CAPTURE_PDF_BYTES == 50 * 1024**2
    assert ARXIV_2601_23265_BYTES < MAX_BROWSER_CAPTURE_PDF_BYTES
    _enforce_browser_capture_pdf_size(SizedPdf(ARXIV_2601_23265_BYTES))
    _enforce_browser_capture_pdf_size(SizedPdf(MAX_BROWSER_CAPTURE_PDF_BYTES))
    with pytest.raises(HTTPException) as rejected:
        _enforce_browser_capture_pdf_size(SizedPdf(MAX_BROWSER_CAPTURE_PDF_BYTES + 1))
    assert rejected.value.status_code == 413
    assert rejected.value.detail == "Browser Capture PDFs are limited to 50 MiB"
    assert MAX_BROWSER_CAPTURE_PDF_BASE64_CHARS == (MAX_BROWSER_CAPTURE_PDF_BYTES + 2) // 3 * 4
    assert (ARXIV_2601_23265_BYTES + 2) // 3 * 4 < MAX_BROWSER_CAPTURE_PDF_BASE64_CHARS
    max_length = next(
        metadata.max_length
        for metadata in BrowserCapturePaperCreate.model_fields["content_base64"].metadata
        if getattr(metadata, "max_length", None) is not None
    )
    assert max_length == MAX_BROWSER_CAPTURE_PDF_BASE64_CHARS
    assert MAX_BROWSER_CAPTURE_PDF_BASE64_CHARS + 64 * 1024 < MAX_REQUEST_BODY_BYTES


def test_paper_identity_normalizers_are_stable() -> None:
    assert _capture_doi_identity(" DOI:10.1234/Example. ") == "10.1234/example"
    assert (
        _capture_doi_identity("https://doi.org/10.48550/arXiv.1706.03762v7")
        == "10.48550/arxiv.1706.03762"
    )
    assert _capture_doi_identity("not a doi") == ""
    assert _merge_capture_authors(
        ["Ada Lovelace", "Grace Hopper"], ["ada lovelace", "Barbara Liskov"]
    ) == (["Ada Lovelace", "Grace Hopper", "Barbara Liskov"], True)
    bounded, changed = _merge_capture_authors(
        [f"Existing Author {index}" for index in range(75)],
        [f"Incoming Author {index}" for index in range(75)],
        max_authors=100,
    )
    assert changed is True
    assert len(bounded) == 100
    assert bounded[-1] == "Incoming Author 24"
    reviewed = [f"Reviewed Author {index}" for index in range(101)]
    preserved, changed = _merge_capture_authors(reviewed, ["New Scraped Author"], max_authors=100)
    assert preserved == reviewed
    assert changed is False


def test_web_url_sanitizer_removes_exact_secret_keys_and_keeps_functional_query() -> None:
    sanitized = _browser_capture_web_url(
        "https://example.org/guide?chapter=2&client_secret=a&id_token=b&auth_token=c&samlresponse=d&ticket=e&assertion=f&credential=g&oauth_token=h&form_password=i&signed_signature=j"
    )
    assert sanitized == "https://example.org/guide?chapter=2"


def _client(settings: Settings) -> TestClient:
    settings.browser_capture_redirect_uris = REDIRECT
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "email": "capture@example.org",
            "password": PASSWORD,
            "org_name": "Capture workspace",
            "name": "Capture Owner",
        },
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _capture_key(client: TestClient) -> str:
    pair = client.post(
        "/browser-capture/pair",
        json={
            "code_challenge": CHALLENGE,
            "state": STATE,
            "device_name": "Chrome on Mac",
            "redirect_uri": REDIRECT,
        },
    )
    assert pair.status_code == 200, pair.text
    callback = pair.json()["callback_url"]
    code = callback.split("code=", 1)[1].split("&", 1)[0]
    anonymous = TestClient(client.app)
    wrong = anonymous.post(
        "/browser-capture/pair/exchange",
        json={"code": code, "code_verifier": "w" * 43, "state": STATE},
    )
    assert wrong.status_code == 401
    wrong_state = anonymous.post(
        "/browser-capture/pair/exchange",
        json={"code": code, "code_verifier": VERIFIER, "state": "wrong-state-value"},
    )
    assert wrong_state.status_code == 401
    exchange = anonymous.post(
        "/browser-capture/pair/exchange",
        json={"code": code, "code_verifier": VERIFIER, "state": STATE},
    )
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["scopes"] == ["capture:write"]
    assert (
        anonymous.post(
            "/browser-capture/pair/exchange",
            json={"code": code, "code_verifier": VERIFIER, "state": STATE},
        ).status_code
        == 401
    )
    return str(exchange.json()["api_key"])


def _hold_first_metadata_lock(
    monkeypatch: pytest.MonkeyPatch, lock_key: str
) -> tuple[Event, Event, Event]:
    """Force a PATCH and capture to overlap at their shared transaction lock."""
    first_acquired = Event()
    second_attempted = Event()
    release_first = Event()
    counter_lock = Lock()
    matching_calls = 0

    def observed_lock(session: OrmSession, org_id: int, *keys: str) -> None:
        nonlocal matching_calls
        if lock_key not in keys:
            _capture_transaction_lock(session, org_id, *keys)
            return
        with counter_lock:
            matching_calls += 1
            call_number = matching_calls
        if call_number == 1:
            _capture_transaction_lock(session, org_id, *keys)
            first_acquired.set()
            if not release_first.wait(timeout=10):
                raise RuntimeError("metadata concurrency test did not release the first lock")
            return
        second_attempted.set()
        _capture_transaction_lock(session, org_id, *keys)

    monkeypatch.setattr("sixsentences_server.api.app._capture_transaction_lock", observed_lock)
    return (first_acquired, second_attempted, release_first)


def _payload(capture_id: str = "capture-web-00000001") -> dict[str, object]:
    return {
        "capture_id": capture_id,
        "url": "https://Example.org/article?utm_source=test&b=2&a=1#results",
        "canonical_url": "https://example.org/article?a=1&b=2",
        "title": "A useful source",
        "site_name": "Example",
        "authors": ["Ada Author"],
        "published_at": "2026-08-13",
        "description": "Allowlisted metadata only.",
        "selected_excerpt": "A passage the user explicitly selected.",
        "source_kind": "web",
        "doi": "",
        "project_id": None,
        "captured_at": "2026-08-13T16:00:00Z",
        "extension_version": "0.1.0",
        "metadata_fields": ["citation_title", "og:site_name"],
    }


def _extension_web_payload(capture_id: str = "capture-extension-web-0001") -> dict[str, object]:
    return {
        "capture_id": capture_id,
        "url": "https://example.org/guide?utm_source=newsletter&chapter=2&token=private-value&client_secret=hidden&samlresponse=assertion#introduction",
        "canonical_url": "https://example.org/guide?chapter=2&utm_medium=email",
        "title": "A practical guide",
        "site_name": "Example Guides",
        "description": "Public metadata exposed by the page.",
        "selected_excerpt": "A passage the user explicitly selected.",
        "source_kind": "web",
        "captured_at": "2026-08-14T20:00:00Z",
        "extension_version": "0.1.5",
    }


def test_pairing_is_exact_one_use_and_capture_key_is_narrow(settings: Settings) -> None:
    client = _client(settings)
    rejected = client.post(
        "/browser-capture/pair",
        json={
            "code_challenge": CHALLENGE,
            "state": STATE,
            "device_name": "Chrome",
            "redirect_uri": "https://attacker.chromiumapp.org/",
        },
    )
    assert rejected.status_code == 422
    key = _capture_key(client)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
    assert extension.get("/library/web-sources").status_code == 403
    assert extension.get("/documents").status_code == 403
    assert extension.patch("/documents/1/metadata", json={}).status_code == 403
    assert extension.patch("/library/web-sources/source", json={}).status_code == 403
    assert extension.get("/projects").status_code == 403
    scopes = client.get("/auth/api-key-scopes")
    assert all(not row["id"].startswith(("capture:", "companion:")) for row in scopes.json())
    manual = client.post(
        "/auth/api-keys",
        json={"name": "Not a device", "scopes": ["capture:write"], "expires_in_days": 90},
    )
    assert manual.status_code == 422
    assert "pairing" in manual.json()["detail"]


def test_narrow_web_capture_sanitizes_and_replays_without_page_content(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    payload = _extension_web_payload()
    created = extension.post("/browser-capture/web", json=payload)
    assert created.status_code == 201, created.text
    item = created.json()["item"]
    assert item["url"] == "https://example.org/guide?chapter=2"
    assert item["canonical_url"] == "https://example.org/guide?chapter=2"
    assert item["title"] == "A practical guide"
    assert item["source_kind"] == "web"
    assert item["authors"] == []
    assert item["doi"] == ""
    assert item["provenance"]["metadata_fields"] == [
        "description",
        "selected_excerpt",
        "site_name",
        "title",
    ]
    serialized = created.text
    assert "private-value" not in serialized
    assert "hidden" not in serialized
    assert "assertion" not in serialized
    assert "utm_" not in serialized
    assert "introduction" not in item["url"]
    replay = extension.post("/browser-capture/web", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "duplicate"
    assert replay.json()["item"]["id"] == item["id"]
    changed = dict(payload)
    changed["title"] = "A different page"
    conflict = extension.post("/browser-capture/web", json=changed)
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authors", ["Unrequested author"]),
        ("metadata", {"extra": "Unrequested structured data"}),
        ("source_kind", "paper"),
    ],
)
def test_narrow_web_capture_rejects_non_web_or_unallowlisted_fields(
    settings: Settings, field: str, value: object
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    payload = _extension_web_payload(f"capture-narrow-{field}-0001")
    payload[field] = value
    assert extension.post("/browser-capture/web", json=payload).status_code == 422


@pytest.mark.parametrize("field", ["title", "extension_version"])
def test_narrow_web_capture_rejects_control_only_required_text(
    settings: Settings, field: str
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    payload = _extension_web_payload(f"capture-control-{field}-0001")
    payload[field] = " \x00\u202e\t "
    assert extension.post("/browser-capture/web", json=payload).status_code == 422


def test_narrow_web_capture_ignores_cross_host_canonical_without_leaking_receipt(
    settings: Settings,
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    payload = _extension_web_payload("capture-cross-host-web-0001")
    payload["canonical_url"] = "https://attacker.example/poison?token=private"
    response = extension.post("/browser-capture/web", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["item"]["canonical_url"] == "https://example.org/guide?chapter=2"
    assert result["warnings"] == ["Cross-host canonical URL was ignored."]
    assert "capture-cross-host-web-0001" not in response.text
    assert "private" not in response.text
    assert "source_url" not in result["item"]["provenance"]
    assert "capture_id" not in result["item"]["provenance"]


def test_web_capture_canonicalizes_dedupes_and_rejects_id_reuse(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    created = extension.post("/library/web-sources", json=_payload())
    assert created.status_code == 201, created.text
    assert created.json()["item"]["canonical_url"] == "https://example.org/article?a=1&b=2"
    assert (
        created.json()["item"]["url"]
        == "https://example.org/article?utm_source=test&b=2&a=1#results"
    )
    replay = extension.post("/library/web-sources", json=_payload())
    assert replay.status_code == 200
    changed = _payload()
    changed["title"] = "Different content"
    assert extension.post("/library/web-sources", json=changed).status_code == 409
    duplicate_url = _payload("capture-web-00000002")
    duplicate = extension.post("/library/web-sources", json=duplicate_url)
    assert duplicate.json()["status"] == "duplicate"


def test_library_entry_filters_resolve_one_org_scoped_record(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    web = extension.post("/library/web-sources", json=_payload())
    assert web.status_code == 201, web.text
    web_id = web.json()["item"]["id"]
    exact_web = client.get(
        "/library/web-sources", params={"entry_id": web_id, "offset": 0, "limit": 1}
    )
    assert exact_web.status_code == 200, exact_web.text
    assert [row["id"] for row in exact_web.json()] == [web_id]
    assert (
        client.get(
            "/library/web-sources", params={"entry_id": "missing-source", "offset": 0, "limit": 1}
        ).json()
        == []
    )
    citation = extension.post("/browser-capture/citations", json=_citation_payload())
    assert citation.status_code == 201, citation.text
    document_id = citation.json()["item"]["id"]
    exact_document = client.get(
        "/documents", params={"entry_id": document_id, "offset": 0, "limit": 1}
    )
    assert exact_document.status_code == 200, exact_document.text
    assert [row["id"] for row in exact_document.json()] == [document_id]
    assert (
        client.get(
            "/documents", params={"entry_id": document_id + 999999, "offset": 0, "limit": 1}
        ).json()
        == []
    )


def test_capture_urls_fail_closed_for_local_and_ambiguous_hosts(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    unsafe_urls = [
        "http://127.1/research",
        "http://2130706433/research",
        "http://0x7f000001/research",
        "http://0x7f.0.0.1/research",
        "http://0177.0.0.1/research",
        "http://[::ffff:127.0.0.1]/research",
        "http://foo.localhost/research",
        "http://[fe80::1%25en0]/research",
        "https://exa mple.org/research",
        "https://-bad.example/research",
        "https://example.org/%00research",
        "https://example.org/%5cresearch",
    ]
    for index, url in enumerate(unsafe_urls):
        payload = _payload(f"capture-url-{index:08d}")
        payload["url"] = url
        payload["canonical_url"] = url
        rejected = extension.post("/library/web-sources", json=payload)
        assert rejected.status_code == 422, (url, rejected.text)


def test_cross_host_canonical_is_ignored_and_exported(settings: Settings) -> None:
    client = _client(settings)
    payload = _payload()
    payload["canonical_url"] = "https://attacker.example/poison"
    created = client.post("/library/web-sources", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["item"]["canonical_url"].startswith("https://example.org/article")
    assert created.json()["warnings"]
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "capture@example.org"))
        assert user is not None
        tables, _inventory = _privacy_export_tables(session, user=user)
        assert len(tables[LibraryWebSourceRow.__tablename__]) == 1


def _pdf_payload(capture_id: str = "capture-paper-0000001") -> dict[str, object]:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    content = buffer.getvalue()
    return {
        "capture_id": capture_id,
        "filename": "Captured paper.pdf",
        "content_base64": base64.b64encode(content).decode(),
        "source_url": "https://example.org/paper.pdf",
        "sha256": hashlib.sha256(content).hexdigest(),
        "project_id": None,
        "title": "Captured research paper",
        "authors": ["Ada Author"],
        "published_at": "2026-08-13",
        "doi": "10.1234/captured",
    }


def _gpt1_cover_fixture() -> bytes:
    """Deterministic reduced cover fixture for the exact reported public PDF.

    The upstream 541,036-byte paper is identified by ``GPT1_SOURCE_SHA256``;
    this generated fixture retains only the visible cover structure and PDF
    CreationDate needed for the regression without vendoring the paper.
    """
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    operations: list[str] = []

    def line(x: int, y: int, size: int, value: str) -> None:
        escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operations.append(f"BT /F1 {size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET")

    line(171, 679, 17, "Improving Language Understanding")
    line(204, 659, 17, "by Generative Pre-Training")
    for x, author, email in (
        (80, "Alec Radford", "alec@openai.com"),
        (210, "Karthik Narasimhan", "karthikn@openai.com"),
        (350, "Tim Salimans", "tim@openai.com"),
        (455, "Ilya Sutskever", "ilyasu@openai.com"),
    ):
        line(x, 603, 10, author)
        line(x, 592, 10, "OpenAI")
        line(x, 581, 10, email)
    line(280, 540, 12, "Abstract")
    line(
        110,
        515,
        10,
        "Natural language understanding comprises diverse tasks and this fixture preserves only metadata.",
    )
    line(108, 317, 12, "1 Introduction")
    line(108, 52, 9, "Preprint. Work in progress.")
    content = DecodedStreamObject()
    content.set_data("\n".join(operations).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_metadata(
        {"/Creator": "LaTeX with hyperref package", "/CreationDate": "D:20180608211434+02'00'"}
    )
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _cover_stream_fixture(data: bytes, *, flate: bool) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if flate:
        stream = EncodedStreamObject()
        stream._data = data
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    else:
        stream = DecodedStreamObject()
        stream.set_data(data)
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _cover_fonts_fixture(
    fonts: list[DictionaryObject],
    *,
    content_data: bytes = b"BT /F1 18 Tf 1 0 0 1 100 700 Tm (Bounded title) Tj ET",
) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    content = DecodedStreamObject()
    content.set_data(content_data)
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject(f"/F{index}"): writer._add_object(font)
                    for index, font in enumerate(fonts, start=1)
                }
            )
        }
    )
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _cover_font_fixture(font: DictionaryObject) -> bytes:
    return _cover_fonts_fixture([font])


def test_pdf_cover_metadata_fails_closed_on_bombs_oversize_and_malformed_streams() -> None:
    hostile_pdfs = {
        "compressed expansion": _cover_stream_fixture(zlib.compress(b"q\n" * 300000), flate=True),
        "oversize decoded stream": _cover_stream_fixture(b"q\n" * 300000, flate=False),
        "operation flood": _cover_stream_fixture(b"q\n" * 25000, flate=False),
        "malformed Flate stream": _cover_stream_fixture(b"not-a-valid-zlib-stream", flate=True),
    }
    for label, content in hostile_pdfs.items():
        extracted = extract_pdf_capture_metadata(content)
        assert extracted.title is None, label
        assert extracted.authors == (), label
        assert extracted.fields == (), label


def test_pdf_cover_metadata_bounds_font_cmaps_and_cid_width_ranges() -> None:
    cmap = EncodedStreamObject()
    cmap._data = zlib.compress(b"% oversized ToUnicode map\n" * 20000)
    cmap[NameObject("/Filter")] = NameObject("/FlateDecode")
    cmap_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): cmap,
        }
    )
    descendant = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType2"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/W"): ArrayObject(
                [NumberObject(0), NumberObject(1000000), NumberObject(500)]
            ),
        }
    )
    cid_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject([descendant]),
        }
    )
    explicit_cid_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject(
                [
                    DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/CIDFontType2"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                            NameObject("/W"): ArrayObject(
                                [
                                    NumberObject(0),
                                    ArrayObject([NumberObject(500) for _ in range(5000)]),
                                ]
                            ),
                        }
                    )
                ]
            ),
        }
    )
    compact_range = DecodedStreamObject()
    compact_range.set_data(b"1 beginbfrange <0000> <FFFF> <0000> endbfrange")
    compact_range_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): compact_range,
        }
    )
    oversized_destination = DecodedStreamObject()
    oversized_destination.set_data(b"1 beginbfchar <42> <" + b"0041" * 5000 + b"> endbfchar")
    oversized_destination_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): oversized_destination,
        }
    )
    aggregate_fonts = []
    aggregate_entries = b" ".join(f"<{index:04X}> <0041>".encode() for index in range(3000))
    for name in ("Helvetica", "Times-Roman"):
        aggregate_cmap = DecodedStreamObject()
        aggregate_cmap.set_data(b"3000 beginbfchar " + aggregate_entries + b" endbfchar")
        aggregate_fonts.append(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject(f"/{name}"),
                    NameObject("/ToUnicode"): aggregate_cmap,
                }
            )
        )
    oversized_glyph_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): DictionaryObject(
                {
                    NameObject("/BaseEncoding"): NameObject("/WinAnsiEncoding"),
                    NameObject("/Differences"): ArrayObject(
                        [NumberObject(0), NameObject("/" + "A" * 4000)]
                    ),
                }
            ),
        }
    )
    oversized_glyph_content = _cover_fonts_fixture(
        [oversized_glyph_font], content_data=b"BT /F1 18 Tf <" + b"00" * 2000 + b"> Tj ET"
    )
    for label, content in {
        "ToUnicode expansion": _cover_font_fixture(cmap_font),
        "CID width expansion": _cover_font_fixture(cid_font),
        "explicit CID width expansion": _cover_font_fixture(explicit_cid_font),
        "compact ToUnicode range": _cover_font_fixture(compact_range_font),
        "oversized ToUnicode destination": _cover_font_fixture(oversized_destination_font),
        "aggregate ToUnicode mappings": _cover_fonts_fixture(aggregate_fonts),
        "oversized Differences glyph": oversized_glyph_content,
    }.items():
        extracted = extract_pdf_capture_metadata(content)
        assert extracted.title is None, label
        assert extracted.authors == (), label
        assert extracted.fields == (), label


def test_pdf_metadata_bounds_input_before_unicode_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_input_lengths: list[int] = []
    original_normalize = __import__("unicodedata").normalize

    def observed_normalize(form: str, value: str) -> str:
        normalized_input_lengths.append(len(value))
        return original_normalize(form, value)

    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload.unicodedata.normalize", observed_normalize
    )
    assert _pdf_metadata_text("A" * 100000, 10) == "A" * 10
    assert normalized_input_lengths == [256]


def _citation_payload(capture_id: str = "capture-citation-000001") -> dict[str, object]:
    return {
        "capture_id": capture_id,
        "source_url": "https://journals.example.org/article/42?ref=browser#abstract",
        "canonical_url": "https://journals.example.org/article/42",
        "title": "A captured citation",
        "authors": ["Ada Author", "Grace Researcher"],
        "published_at": "2025-04-03",
        "description": "A reviewed abstract.",
        "doi": "10.1234/citation",
        "project_id": None,
        "captured_at": "2026-08-13T16:00:00Z",
        "extension_version": "0.1.0",
        "metadata_fields": ["citation_title", "citation_doi"],
    }


def test_exact_gpt1_direct_pdf_recovers_bounded_document_metadata(settings: Settings) -> None:
    content = _gpt1_cover_fixture()
    extracted = extract_pdf_capture_metadata(content)
    assert extracted.title == "Improving Language Understanding by Generative Pre-Training"
    assert extracted.authors == (
        "Alec Radford",
        "Karthik Narasimhan",
        "Tim Salimans",
        "Ilya Sutskever",
    )
    assert extracted.item_type == "preprint"
    assert extracted.document_date == "2018-06-08"
    assert "pdf.info.creation_date" in extracted.fields
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    payload = {
        "capture_id": "capture-gpt1-direct-pdf-0001",
        "filename": "language_understanding_paper.pdf",
        "content_base64": base64.b64encode(content).decode(),
        "source_url": GPT1_PDF_URL,
        "pdf_url": GPT1_PDF_URL,
        "canonical_url": GPT1_PDF_URL,
        "sha256": hashlib.sha256(content).hexdigest(),
        "project_id": None,
        "title": "language_understanding_paper",
        "authors": [],
        "published_at": None,
        "description": "",
        "doi": "",
        "captured_at": "2026-08-22T12:00:00Z",
        "extension_version": "0.1.9",
        "metadata_fields": ["pdf_url"],
        "metadata": {"item_type": "document", "format": "PDF", "accessed_at": "2026-08-22"},
    }
    saved = extension.post("/browser-capture/papers", json=payload)
    assert saved.status_code == 201, saved.text
    assert saved.json()["item"]["title"] == extracted.title
    paper = client.get("/documents").json()[0]
    assert paper["metadata"]["title"] == extracted.title
    assert paper["metadata"]["authors"] == list(extracted.authors)
    assert paper["metadata"]["abstract"].startswith(
        "Natural language understanding comprises diverse tasks"
    )
    assert paper["metadata"]["item_type"] == "preprint"
    assert paper["metadata"]["format"] == "PDF"
    assert paper["metadata"]["published_at"] is None
    assert paper["metadata"]["year"] is None
    assert paper["metadata_provenance"]["title"]["source"] == "document"
    assert paper["metadata_provenance"]["authors"]["source"] == "document"
    with db_session() as session:
        captured = session.scalar(select(BrowserCapturedPaperMetadataRow))
        assert captured is not None
        provenance = dict(captured.provenance or {})
        assert provenance["pdf_document"] == {
            "metadata_fields": list(extracted.fields),
            "creation_date": "2018-06-08",
        }
        assert provenance["metadata_fields"] == ["pdf_url", *extracted.fields]
    manual = client.patch(
        f"/documents/{paper['id']}/metadata",
        json={
            "expected_revision": paper["metadata_revision"],
            "mode": "edit",
            "metadata": {
                "title": "language_understanding_paper",
                "authors": [],
                "item_type": "document",
            },
        },
    )
    assert manual.status_code == 200, manual.text
    replay_payload = {**payload, "capture_id": "capture-gpt1-direct-pdf-0002"}
    replay = extension.post("/browser-capture/papers", json=replay_payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["item"]["title"] == "language_understanding_paper"
    refreshed = client.get("/documents").json()[0]
    assert refreshed["metadata"]["title"] == "language_understanding_paper"
    assert refreshed["metadata"]["authors"] == []
    assert refreshed["metadata"]["item_type"] == "document"
    assert refreshed["metadata_provenance"]["title"]["source"] == "user"


def test_gpt1_regression_tracks_the_reported_upstream_identity_without_vendoring() -> None:
    assert GPT1_PDF_URL.endswith("/language_understanding_paper.pdf")
    assert GPT1_SOURCE_SHA256 == "eb81a65e0856bd38e855e1f1de6ee12e7c3eb92ae05c8270d345a475beb631d0"


def test_direct_pdf_upgrade_preserves_existing_landing_metadata_and_provenance(
    settings: Settings,
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation_payload = _citation_payload("capture-gpt1-landing-0001")
    citation_payload.update(
        {
            "title": "Reviewed landing-page title",
            "authors": ["Landing Page Author"],
            "description": "Reviewed landing-page abstract.",
            "metadata": {"item_type": "journalArticle", "publisher": "Landing Page Publisher"},
        }
    )
    citation = extension.post("/browser-capture/citations", json=citation_payload)
    assert citation.status_code == 201, citation.text
    content = _gpt1_cover_fixture()
    pdf_payload = {
        "capture_id": "capture-gpt1-landing-0002",
        "filename": "language_understanding_paper.pdf",
        "content_base64": base64.b64encode(content).decode(),
        "source_url": GPT1_PDF_URL,
        "pdf_url": GPT1_PDF_URL,
        "canonical_url": GPT1_PDF_URL,
        "sha256": hashlib.sha256(content).hexdigest(),
        "project_id": None,
        "title": "language_understanding_paper",
        "authors": [],
        "published_at": None,
        "description": "",
        "doi": citation_payload["doi"],
        "captured_at": "2026-08-22T12:00:00Z",
        "extension_version": "0.1.9",
        "metadata_fields": ["pdf_url"],
        "metadata": {"item_type": "document"},
    }
    upgraded = extension.post("/browser-capture/papers", json=pdf_payload)
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["has_file"] is True
    assert upgraded.json()["item"]["title"] == "Reviewed landing-page title"
    papers = client.get("/documents").json()
    assert len(papers) == 1
    paper = papers[0]
    assert paper["metadata"]["title"] == "Reviewed landing-page title"
    assert paper["metadata"]["authors"] == ["Landing Page Author"]
    assert paper["metadata"]["abstract"] == "Reviewed landing-page abstract."
    assert paper["metadata"]["item_type"] == "journalArticle"
    assert paper["metadata"]["publisher"] == "Landing Page Publisher"
    assert paper["metadata"]["format"] == "PDF"
    for field in ("title", "authors", "abstract", "item_type", "publisher"):
        assert paper["metadata_provenance"][field]["source"] == "browser_capture"
    assert paper["metadata_provenance"]["format"]["source"] == "document"


def test_paper_landing_page_is_saved_as_citation_and_pdf_upgrades_it(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation = extension.post("/browser-capture/citations", json=_citation_payload())
    assert citation.status_code == 201, citation.text
    assert citation.json()["has_file"] is False
    listed = client.get("/documents", params={"q": "Ada Author"})
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["title"] == "A captured citation"
    assert listed.json()[0]["has_file"] is False
    assert listed.json()[0]["url"].endswith("?ref=browser#abstract")
    pdf = _pdf_payload("capture-citation-upgrade")
    pdf["source_url"] = "https://journals.example.org/article/42.pdf"
    pdf["doi"] = "10.1234/citation"
    encoded_pdf = str(pdf["content_base64"])
    original_b64decode = base64.b64decode
    decoded_pdf_calls = 0

    def decode_once(value: object, *args: object, **kwargs: object) -> bytes:
        nonlocal decoded_pdf_calls
        if value == encoded_pdf:
            decoded_pdf_calls += 1
        return original_b64decode(value, *args, **kwargs)

    monkeypatch.setattr("sixsentences_server.api.app.base64.b64decode", decode_once)
    upgraded = extension.post("/browser-capture/papers", json=pdf)
    assert upgraded.status_code == 200, upgraded.text
    assert decoded_pdf_calls == 1
    assert upgraded.json()["status"] == "pdf_attached"
    assert upgraded.json()["item"]["id"] == citation.json()["item"]["id"]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        metadata = session.scalar(select(BrowserCapturedPaperMetadataRow))
        assert metadata is not None and metadata.title == "A captured citation"
        assert (metadata.provenance or {})["attachment"] == "pdf"


def test_researchgate_style_metadata_survives_failed_pdf_attachment(settings: Settings) -> None:
    """A best-effort attachment must never become the citation transaction."""
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    landing_url = "https://www.researchgate.net/publication/405480961_Hallucinated_Resources_Brittle_Oracles_Decoupled_Security"
    citation = _citation_payload("researchgate-metadata-0001")
    citation.update(
        {
            "source_url": f"{landing_url}?trackingId=browser#overview",
            "canonical_url": landing_url,
            "title": "Hallucinated Resources, Brittle Oracles, Decoupled Security: An Empirical Study of LLM-Generated Terraform",
            "authors": ["Alex Researcher"],
            "published_at": "2026-06",
            "description": "An empirical study of LLM-generated Terraform.",
            "doi": "",
            "metadata_fields": [
                "citation_title",
                "citation_author",
                "citation_publication_date",
                "citation_abstract",
            ],
            "metadata": {"item_type": "preprint", "license": "CC BY 4.0", "language": "en"},
        }
    )
    saved = extension.post("/browser-capture/citations", json=citation)
    assert saved.status_code == 201, saved.text
    assert saved.json()["has_file"] is False
    invalid_pdf = _pdf_payload("researchgate-attachment-0001")
    invalid_pdf.update(
        {
            "content_base64": base64.b64encode(b"ResearchGate HTML error page").decode(),
            "sha256": hashlib.sha256(b"ResearchGate HTML error page").hexdigest(),
            "source_url": landing_url,
            "canonical_url": landing_url,
            "title": citation["title"],
            "authors": citation["authors"],
            "doi": "",
        }
    )
    failed_attachment = extension.post("/browser-capture/papers", json=invalid_pdf)
    assert failed_attachment.status_code == 422, failed_attachment.text
    replay = extension.post("/browser-capture/citations", json=citation)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "already_saved"
    assert replay.json()["item"]["id"] == saved.json()["item"]["id"]
    listed = client.get("/documents").json()
    assert len(listed) == 1
    assert listed[0]["id"] == saved.json()["item"]["id"]
    assert listed[0]["has_file"] is False
    assert listed[0]["title"] == citation["title"]
    assert listed[0]["metadata"]["item_type"] == "preprint"
    with db_session() as session:
        metadata = session.scalar(select(BrowserCapturedPaperMetadataRow))
        assert metadata is not None
        assert metadata.canonical_url == landing_url
        assert metadata.pdf_checksum is None
        assert (metadata.provenance or {})["attachment"] == "metadata_only"
        assert session.scalar(select(func.count(BrowserCaptureReceiptRow.id))) == 1


def test_doi_less_landing_page_recapture_deduplicates_and_enriches(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    canonical_url = "https://publisher.example.org/publication/405480961"
    first = _citation_payload("doi-less-citation-first-0001")
    first.update(
        {
            "source_url": f"{canonical_url}?tracking=first#overview",
            "canonical_url": canonical_url,
            "title": "Reviewed landing-page title",
            "authors": ["Ada Author"],
            "published_at": None,
            "description": "",
            "doi": "",
        }
    )
    created = extension.post("/browser-capture/citations", json=first)
    assert created.status_code == 201, created.text
    richer = _citation_payload("doi-less-citation-richer-0001")
    richer.update(
        {
            "source_url": f"{canonical_url}?tracking=second#details",
            "canonical_url": canonical_url,
            "title": "Conflicting scraper title",
            "authors": ["ada author", "Grace Researcher"],
            "published_at": "2026-06",
            "description": "Metadata recovered during a later capture.",
            "doi": "",
            "metadata": {"item_type": "preprint", "license": "CC BY 4.0"},
        }
    )
    enriched = extension.post("/browser-capture/citations", json=richer)
    assert enriched.status_code == 200, enriched.text
    assert enriched.json()["item"]["id"] == created.json()["item"]["id"]
    assert enriched.json()["status"] == "metadata_enriched"
    assert {
        "authors",
        "published_at",
        "description",
        "bibliographic.item_type",
        "bibliographic.license",
    } <= set(enriched.json()["metadata_fields_added"])
    listed = client.get("/documents").json()
    assert len(listed) == 1
    assert listed[0]["title"] == first["title"]
    assert listed[0]["authors"] == ["Ada Author", "Grace Researcher"]
    assert listed[0]["metadata"]["abstract"] == richer["description"]
    assert listed[0]["metadata"]["license"] == "CC BY 4.0"


def test_pdf_capture_retains_selected_excerpt_and_rich_metadata(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    paper = _pdf_payload("capture-paper-rich-metadata")
    paper.update(
        {
            "selected_excerpt": "The passage selected before saving the direct PDF.",
            "metadata": {
                "container_title": "Proceedings of Safe Capture",
                "publisher": "SixSentences Press",
                "keywords": ["capture", "metadata"],
                "item_type": "conferencePaper",
            },
        }
    )
    saved = extension.post("/browser-capture/papers", json=paper)
    assert saved.status_code == 201, saved.text
    listed = client.get("/documents").json()
    assert (
        listed[0]["metadata"]["selected_excerpt"]
        == "The passage selected before saving the direct PDF."
    )
    assert listed[0]["metadata"]["container_title"] == "Proceedings of Safe Capture"
    assert listed[0]["metadata"]["publisher"] == "SixSentences Press"


def test_same_doi_with_different_pdf_keeps_existing_attachment(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    first = _pdf_payload("capture-doi-first-pdf-0001")
    created = extension.post("/browser-capture/papers", json=first)
    assert created.status_code == 201, created.text
    second = _pdf_payload("capture-doi-second-pdf-0001")
    writer = PdfWriter()
    writer.add_blank_page(width=500, height=700)
    writer.add_blank_page(width=500, height=700)
    buffer = BytesIO()
    writer.write(buffer)
    content = buffer.getvalue()
    second.update(
        {
            "content_base64": base64.b64encode(content).decode(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "source_url": "https://another.example.org/different.pdf",
            "canonical_url": "https://another.example.org/different",
            "doi": "doi:10.1234/captured",
        }
    )
    duplicate = extension.post("/browser-capture/papers", json=second)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["status"] in {"already_saved", "metadata_enriched"}
    assert "different_pdf_same_identity" in duplicate.json()["warnings"]
    assert duplicate.json()["item"]["id"] == created.json()["item"]["id"]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        document = session.scalar(select(DocumentRow))
        assert document is not None and document.checksum == first["sha256"]


def test_identical_pdf_with_conflicting_doi_never_bridges_another_citation(
    settings: Settings,
) -> None:
    client = _client(settings)
    paper = _pdf_payload("capture-three-identity-conflict")
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    ).json()
    with db_session() as session:
        document = session.get(DocumentRow, uploaded["id"])
        assert document is not None
        work = session.get(WorkRow, document.work_id)
        assert work is not None
        work.doi = "10.5555/existing-paper"
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation_payload = _citation_payload("capture-conflicting-citation")
    citation_payload["doi"] = "10.5555/different-citation"
    citation = extension.post("/browser-capture/citations", json=citation_payload)
    assert citation.status_code == 201, citation.text
    citation_id = citation.json()["item"]["id"]
    paper["capture_id"] = "capture-conflicting-pdf"
    paper["doi"] = "10.5555/different-citation"
    captured = extension.post("/browser-capture/papers", json=paper)
    assert captured.status_code == 200, captured.text
    assert captured.json()["item"]["id"] == uploaded["id"]
    assert captured.json()["warnings"] == ["conflicting_doi_for_identical_pdf"]
    with db_session() as session:
        assert session.get(DocumentRow, citation_id) is not None
        citation_receipt = session.scalar(
            select(BrowserCaptureReceiptRow).where(
                BrowserCaptureReceiptRow.capture_id == citation_payload["capture_id"]
            )
        )
        assert citation_receipt is not None
        assert citation_receipt.document_id == citation_id


def test_citation_and_independent_upload_converge_when_pdf_bridges_both(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation_payload = _citation_payload("capture-bridge-citation-0001")
    citation = extension.post("/browser-capture/citations", json=citation_payload)
    assert citation.status_code == 201, citation.text
    paper = _pdf_payload("capture-bridge-paper-0001")
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    )
    assert uploaded.status_code == 201, uploaded.text
    paper.update(
        {
            "source_url": citation_payload["source_url"],
            "canonical_url": citation_payload["canonical_url"],
            "doi": citation_payload["doi"],
        }
    )
    converged = extension.post("/browser-capture/papers", json=paper)
    assert converged.status_code == 200, converged.text
    assert converged.json()["item"]["id"] == uploaded.json()["id"]
    listed = client.get("/documents").json()
    assert len(listed) == 1 and listed[0]["has_file"] is True
    assert listed[0]["title"] == citation_payload["title"]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        assert session.scalar(select(func.count(BrowserCapturedPaperMetadataRow.id))) == 1
        assert session.scalar(select(func.count(BrowserCaptureReceiptRow.id))) == 2


def test_library_listing_prefers_full_pdf_over_newer_legacy_citation(settings: Settings) -> None:
    client = _client(settings)
    paper = _pdf_payload("capture-list-winner-pdf-0001")
    upload = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    ).json()
    with db_session() as session:
        full = session.get(DocumentRow, upload["id"])
        assert full is not None
        full_work = session.get(WorkRow, full.work_id)
        assert full_work is not None
        full_work.doi = "10.1234/list-winner"
        legacy_work = WorkRow(id="W-legacy-list", title="Legacy duplicate", payload={})
        session.add(legacy_work)
        session.flush()
        legacy = DocumentRow(
            org_id=full.org_id,
            work_id=legacy_work.id,
            status="not_retrieved",
            source="browser_capture",
            url="https://example.org/legacy",
            byte_size=0,
            text_status="not_retrieved",
        )
        session.add(legacy)
        session.flush()
        session.add(
            BrowserCapturedPaperMetadataRow(
                org_id=full.org_id,
                document_id=legacy.id,
                title="Reviewed legacy title",
                authors=["Ada Author", "Grace Researcher"],
                doi="10.1234/list-winner",
                published_at="2025-08-13",
                description="Reviewed legacy abstract.",
                source_url="https://example.org/legacy",
                canonical_url="https://example.org/legacy",
                canonical_url_hash="f" * 64,
            )
        )
    listed = client.get("/documents").json()
    assert len(listed) == 1
    assert listed[0]["id"] == upload["id"]
    assert listed[0]["has_file"] is True
    assert listed[0]["title"] == "Reviewed legacy title"
    assert listed[0]["authors"] == ["Ada Author", "Grace Researcher"]
    assert listed[0]["year"] == 2025
    searched = client.get("/documents", params={"q": "Reviewed legacy abstract"}).json()
    assert [row["id"] for row in searched] == [upload["id"]]
    another = _pdf_payload("capture-list-newest-pdf-0001")
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=600)
    writer.add_blank_page(width=400, height=600)
    buffer = BytesIO()
    writer.write(buffer)
    content = buffer.getvalue()
    another.update(
        {
            "content_base64": base64.b64encode(content).decode(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "doi": "10.1234/newest-distinct",
            "source_url": "https://example.org/newest.pdf",
        }
    )
    newest = client.post(
        "/orgs/current/documents",
        json={"filename": another["filename"], "content_base64": another["content_base64"]},
    ).json()
    listed = client.get("/documents").json()
    assert [row["id"] for row in listed] == [newest["id"], upload["id"]]


def test_library_listing_prefers_run_pdf_over_unbound_citation_with_same_doi(
    settings: Settings,
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation = extension.post(
        "/browser-capture/citations", json=_citation_payload("capture-list-run-pdf-0001")
    )
    assert citation.status_code == 201, citation.text
    citation_id = citation.json()["item"]["id"]
    paper = _pdf_payload("capture-list-run-full-pdf")
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    )
    assert uploaded.status_code == 201, uploaded.text
    with db_session() as session:
        citation_document = session.get(DocumentRow, citation_id)
        assert citation_document is not None and citation_document.run_id is None
        run_pdf = session.get(DocumentRow, uploaded.json()["id"])
        assert run_pdf is not None and run_pdf.run_id is None
        work = session.get(WorkRow, run_pdf.work_id)
        assert work is not None
        work.doi = "https://doi.org/10.1234/citation"
        run = Run(
            org_id=citation_document.org_id,
            question="Active paper chat",
            status="running",
            config={},
        )
        session.add(run)
        session.flush()
        run_pdf.run_id = run.id
        run_pdf_id = run_pdf.id
        run_id = run.id
    listed = client.get("/documents").json()
    assert len(listed) == 1
    assert listed[0]["id"] == run_pdf_id
    assert listed[0]["has_file"] is True
    assert client.get(f"/documents/{run_pdf_id}/file").status_code == 200
    assert client.delete(f"/documents/{run_pdf_id}").status_code == 204
    assert client.get(f"/documents/{run_pdf_id}/file").status_code == 200
    with db_session() as session:
        assert session.get(DocumentRow, citation_id) is None
        assert session.get(DocumentRow, run_pdf_id) is not None
        assert session.get(Run, run_id) is not None


def test_library_listing_uses_hidden_component_work_metadata_and_searches_it(
    settings: Settings,
) -> None:
    client = _client(settings)
    paper = _pdf_payload("capture-list-hidden-work")
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    ).json()
    with db_session() as session:
        full = session.get(DocumentRow, uploaded["id"])
        assert full is not None
        full_work = session.get(WorkRow, full.work_id)
        assert full_work is not None
        full_work.title = "Uploaded document"
        full_work.doi = "10.4321/component-rich"
        hidden_work = WorkRow(
            id="W-hidden-rich-component",
            doi="https://doi.org/10.4321/component-rich",
            title="Deterministic hidden metadata title",
            year=2022,
            payload={"abstract": "Rare component-search phrase", "authors": ["Hidden Researcher"]},
        )
        session.add(hidden_work)
        session.flush()
        session.add(
            DocumentRow(
                org_id=full.org_id,
                work_id=hidden_work.id,
                status="not_retrieved",
                source="browser_capture",
                url="https://example.org/hidden-rich",
                byte_size=0,
                text_status="not_retrieved",
            )
        )
    listed = client.get("/documents").json()
    assert len(listed) == 1
    assert listed[0]["id"] == uploaded["id"]
    assert listed[0]["title"] == "Deterministic hidden metadata title"
    assert listed[0]["doi"] == "10.4321/component-rich"
    assert listed[0]["year"] == 2022
    assert listed[0]["authors"] == ["Hidden Researcher"]
    searched = client.get("/documents", params={"q": "Rare component-search phrase"}).json()
    assert [row["id"] for row in searched] == [uploaded["id"]]


def test_library_delete_preserves_active_run_clone_and_shared_blob(settings: Settings) -> None:
    client = _client(settings)
    paper = _pdf_payload("capture-delete-library-original")
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    )
    assert uploaded.status_code == 201, uploaded.text
    original_id = uploaded.json()["id"]
    with db_session() as session:
        original = session.get(DocumentRow, original_id)
        assert original is not None and original.run_id is None
        run = Run(
            org_id=original.org_id,
            question="Active chat keeps its PDF",
            status="running",
            config={},
        )
        session.add(run)
        session.flush()
        clone = DocumentRow(
            org_id=original.org_id,
            run_id=run.id,
            project_id=original.project_id,
            folder=original.folder,
            work_id=original.work_id,
            status=original.status,
            source=original.source,
            legal_basis=original.legal_basis,
            license=original.license,
            version=original.version,
            url=original.url,
            content_type=original.content_type,
            checksum=original.checksum,
            byte_size=original.byte_size,
            storage_path=original.storage_path,
            text_status=original.text_status,
            reason=original.reason,
            retrieved_at=original.retrieved_at,
        )
        session.add(clone)
        session.flush()
        clone_id = clone.id
        run_id = run.id
        run_public_id = run.public_id
    assert client.get(f"/documents/{clone_id}/file").status_code == 200
    deleted = client.delete(f"/documents/{original_id}")
    assert deleted.status_code == 204, deleted.text
    assert client.get("/documents").json() == []
    assert client.get(f"/documents/{original_id}/file").status_code == 404
    assert client.get(f"/documents/{clone_id}/file").status_code == 200
    run_documents = client.get(f"/runs/{run_public_id}/documents")
    assert run_documents.status_code == 200, run_documents.text
    assert [row["id"] for row in run_documents.json()] == [clone_id]
    with db_session() as session:
        assert session.get(DocumentRow, original_id) is None
        preserved_clone = session.get(DocumentRow, clone_id)
        assert preserved_clone is not None
        assert preserved_clone.library_suppressed is True
        assert session.get(Run, run_id) is not None
    reuploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    )
    assert reuploaded.status_code == 201, reuploaded.text
    recreated_id = reuploaded.json()["id"]
    assert recreated_id not in {original_id, clone_id}
    assert [row["id"] for row in client.get("/documents").json()] == [recreated_id]
    with db_session() as session:
        recreated = session.get(DocumentRow, recreated_id)
        assert recreated is not None and recreated.run_id is None
        assert recreated.library_suppressed is False
        assert session.get(DocumentRow, clone_id) is not None


def test_citation_dedupes_by_doi_and_only_enriches_missing_metadata(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    partial = _citation_payload("capture-citation-partial-0001")
    partial.update(
        {
            "source_url": "https://first.example.org/paper",
            "canonical_url": "https://first.example.org/paper",
            "title": "Reviewed title",
            "authors": ["Ada Author"],
            "published_at": None,
            "description": "",
            "doi": "https://doi.org/10.1234/Shared.",
        }
    )
    created = extension.post("/browser-capture/citations", json=partial)
    assert created.status_code == 201, created.text
    richer = _citation_payload("capture-citation-richer-0001")
    richer.update(
        {
            "source_url": "https://second.example.org/unrelated-path",
            "canonical_url": "https://second.example.org/unrelated-path",
            "title": "Conflicting title",
            "authors": ["ada author", "Grace Researcher"],
            "published_at": "2024-06-01",
            "description": "Missing abstract now supplied.",
            "doi": "DOI:10.1234/shared",
        }
    )
    enriched = extension.post("/browser-capture/citations", json=richer)
    assert enriched.status_code == 200, enriched.text
    assert enriched.json()["status"] == "metadata_enriched"
    assert {"authors", "published_at", "description"} <= set(
        enriched.json()["metadata_fields_added"]
    )
    assert enriched.json()["item"]["id"] == created.json()["item"]["id"]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        metadata = session.scalar(select(BrowserCapturedPaperMetadataRow))
        assert metadata is not None
        assert metadata.title == "Reviewed title"
        assert metadata.source_url == partial["source_url"]
        assert metadata.authors == ["Ada Author", "Grace Researcher"]
        assert metadata.published_at == "2024-06-01"
        assert metadata.description == "Missing abstract now supplied."


def test_citation_flushes_synthetic_work_before_document(settings: Settings) -> None:
    """Regression for PostgreSQL's documents_work_id_fkey insert ordering."""
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    flush_snapshots: list[tuple[bool, bool]] = []

    def observe_flush(session: OrmSession, *_args: object) -> None:
        new_rows = list(session.new)
        has_synthetic_work = any(
            isinstance(row, WorkRow) and row.title == "Captured paper citation" for row in new_rows
        )
        if has_synthetic_work:
            flush_snapshots.append((True, any(isinstance(row, DocumentRow) for row in new_rows)))

    event.listen(OrmSession, "before_flush", observe_flush)
    try:
        response = extension.post(
            "/browser-capture/citations", json=_citation_payload("capture-citation-flush-order")
        )
    finally:
        event.remove(OrmSession, "before_flush", observe_flush)
    assert response.status_code == 201, response.text
    assert flush_snapshots == [(True, False)]


def test_library_paper_metadata_fill_edit_revision_and_capture_tombstone(
    settings: Settings,
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    payload = _citation_payload("capture-library-metadata-0001")
    payload.update(
        {
            "description": "",
            "selected_excerpt": "A manually selected supporting passage.",
            "metadata": {
                "container_title": "Journal of Reviewed Sources",
                "volume": "12",
                "issue": "3",
                "pages": "40-52",
                "publisher": "Evidence Press",
                "language": "en",
                "license": "CC BY 4.0",
                "issn": "1234-5678",
                "arxiv_id": "1706.03762v7",
                "keywords": ["Transformers", "transformers", "Attention"],
                "item_type": "journalArticle",
                "subtitle": "A practical metadata test",
                "short_title": "Metadata test",
                "series_title": "Research Systems",
                "series_number": "7",
                "edition": "2",
                "publisher_place": "Example City",
                "accessed_at": "2026-08-14",
                "archive": "Institutional Repository",
                "archive_location": "Collection A",
                "citation_key": "author2024metadata",
                "format": "PDF",
                "call_number": "QA76.9.M48",
                "pmid": "12345678",
                "pmcid": "PMC1234567",
                "extra": "Reviewed in the SixSentences Library.",
            },
        }
    )
    created = extension.post("/browser-capture/citations", json=payload)
    assert created.status_code == 201, created.text
    document_id = created.json()["item"]["id"]
    listed = client.get("/documents").json()[0]
    assert listed["metadata"]["container_title"] == "Journal of Reviewed Sources"
    assert listed["metadata"]["selected_excerpt"] == "A manually selected supporting passage."
    assert listed["metadata"]["arxiv_id"] == "1706.03762"
    assert listed["metadata"]["keywords"] == ["Transformers", "Attention"]
    assert listed["metadata"]["subtitle"] == "A practical metadata test"
    assert listed["metadata"]["citation_key"] == "author2024metadata"
    assert listed["metadata"]["pmcid"] == "PMC1234567"
    assert listed["metadata"]["extra"] == "Reviewed in the SixSentences Library."
    initial_revision = listed["metadata_revision"]
    filled = client.patch(
        f"/documents/{document_id}/metadata",
        json={
            "expected_revision": initial_revision,
            "mode": "fill_missing",
            "metadata": {
                "title": "Must not replace the reviewed title",
                "authors": ["Ada Author", "New Collaborator"],
                "abstract": "The previously missing abstract.",
            },
        },
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["metadata"]["title"] == payload["title"]
    assert filled.json()["metadata"]["authors"] == [
        "Ada Author",
        "Grace Researcher",
        "New Collaborator",
    ]
    assert filled.json()["metadata"]["abstract"] == "The previously missing abstract."
    filled_revision = filled.json()["metadata_revision"]
    assert filled_revision != initial_revision
    edited = client.patch(
        f"/documents/{document_id}/metadata",
        json={
            "expected_revision": filled_revision,
            "mode": "edit",
            "metadata": {
                "title": "Corrected by the researcher",
                "abstract": None,
                "published_at": "2024-06-03",
                "doi": "https://doi.org/10.1234/Citation",
                "canonical_url": None,
                "pdf_url": "https://journals.example.org/article/42.pdf",
                "container_title": "Corrected Journal",
                "publisher": "Corrected Publisher",
                "selected_excerpt": None,
                "publisher_place": "Tübingen",
                "citation_key": "researcher2024corrected",
                "pmid": "87654321",
                "extra": "A manual correction.",
            },
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["metadata"]["title"] == "Corrected by the researcher"
    assert edited.json()["metadata"]["abstract"] is None
    assert edited.json()["metadata"]["year"] == 2024
    assert edited.json()["metadata"]["canonical_url"] is None
    assert edited.json()["metadata"]["selected_excerpt"] is None
    assert edited.json()["metadata"]["publisher_place"] == "Tübingen"
    assert edited.json()["metadata"]["citation_key"] == "researcher2024corrected"
    assert edited.json()["metadata_provenance"]["title"]["source"] == "user"
    assert edited.json()["metadata_provenance"]["selected_excerpt"]["source"] == "user"
    stale = client.patch(
        f"/documents/{document_id}/metadata",
        json={
            "expected_revision": filled_revision,
            "mode": "edit",
            "metadata": {"title": "A stale overwrite"},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "library_metadata_revision_conflict"
    richer = _citation_payload("capture-library-metadata-0002")
    richer.update(
        {
            "description": "Automatic enrichment must respect the manual clear.",
            "doi": "doi:10.1234/citation",
            "selected_excerpt": "Automatic capture must not restore this passage.",
        }
    )
    enriched = extension.post("/browser-capture/citations", json=richer)
    assert enriched.status_code == 200, enriched.text
    refreshed = client.get("/documents").json()[0]
    assert refreshed["metadata"]["title"] == "Corrected by the researcher"
    assert refreshed["metadata"]["abstract"] is None
    assert refreshed["metadata"]["selected_excerpt"] is None


def test_library_metadata_is_org_scoped_and_rejects_identity_collision(settings: Settings) -> None:
    client = _client(settings)
    first = client.post(
        "/orgs/current/documents",
        json={
            "filename": "first.pdf",
            "content_base64": _pdf_payload("metadata-first")["content_base64"],
        },
    )
    second_payload = _pdf_payload("metadata-second")
    second_content = base64.b64decode(str(second_payload["content_base64"])) + b"\n"
    second = client.post(
        "/orgs/current/documents",
        json={
            "filename": "second.pdf",
            "content_base64": base64.b64encode(second_content).decode(),
        },
    )
    assert first.status_code == 201 and second.status_code == 201
    listed = {row["id"]: row for row in client.get("/documents").json()}
    first_id = first.json()["id"]
    second_id = second.json()["id"]
    first_edit = client.patch(
        f"/documents/{first_id}/metadata",
        json={
            "expected_revision": listed[first_id]["metadata_revision"],
            "mode": "edit",
            "metadata": {"doi": "10.5555/unique-paper"},
        },
    )
    assert first_edit.status_code == 200, first_edit.text
    conflict = client.patch(
        f"/documents/{second_id}/metadata",
        json={
            "expected_revision": listed[second_id]["metadata_revision"],
            "mode": "edit",
            "metadata": {"doi": "10.5555/unique-paper"},
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "library_metadata_identity_conflict"
    outsider = TestClient(client.app)
    registered = outsider.post(
        "/auth/register",
        json={
            "email": "metadata-outsider@example.org",
            "password": PASSWORD,
            "org_name": "Other metadata workspace",
            "name": "Outsider",
        },
    )
    assert registered.status_code == 201
    outsider.headers["Authorization"] = f"Bearer {registered.json()['token']}"
    hidden = outsider.patch(
        f"/documents/{first_id}/metadata",
        json={
            "expected_revision": first_edit.json()["metadata_revision"],
            "mode": "edit",
            "metadata": {"title": "Cross-org overwrite"},
        },
    )
    assert hidden.status_code == 404


def test_web_source_metadata_enriches_edits_and_detects_stale_revision(settings: Settings) -> None:
    client = _client(settings)
    initial = _payload("capture-web-metadata-0001")
    initial.update(
        {
            "description": "",
            "metadata": {
                "container_title": "Wikipedia",
                "language": "en",
                "keywords": ["Infrastructure as code"],
                "item_type": "webpage",
                "short_title": "IaC",
                "accessed_at": "2026-08-14",
                "archive": "Wikipedia",
                "citation_key": "wikipedia2026iac",
                "extra": "Captured from the reviewed article page.",
            },
        }
    )
    created = client.post("/library/web-sources", json=initial)
    assert created.status_code == 201, created.text
    source = created.json()["item"]
    assert source["metadata"]["container_title"] == "Wikipedia"
    assert source["metadata"]["short_title"] == "IaC"
    assert source["metadata"]["citation_key"] == "wikipedia2026iac"
    revision = source["metadata_revision"]
    duplicate = _payload("capture-web-metadata-0002")
    duplicate.update(
        {
            "description": "A missing description supplied by a later capture.",
            "metadata": {"publisher": "Wikimedia Foundation"},
        }
    )
    enriched = client.post("/library/web-sources", json=duplicate)
    assert enriched.status_code == 200, enriched.text
    assert enriched.json()["status"] == "metadata_enriched"
    assert enriched.json()["item"]["metadata"]["publisher"] == "Wikimedia Foundation"
    current = enriched.json()["item"]
    edited = client.patch(
        f"/library/web-sources/{source['id']}",
        json={
            "expected_revision": current["metadata_revision"],
            "mode": "edit",
            "metadata": {
                "title": "Infrastructure as code — Wikipedia",
                "authors": ["Wikipedia contributors"],
                "published_at": "2026-08-13",
                "abstract": "A reviewed description.",
                "selected_excerpt": "A reviewed excerpt.",
                "site_name": "Wikipedia",
                "source_url": "https://en.wikipedia.org/wiki/Infrastructure_as_code",
                "canonical_url": "https://en.wikipedia.org/wiki/Infrastructure_as_code",
            },
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Infrastructure as code — Wikipedia"
    assert edited.json()["metadata_provenance"]["title"]["source"] == "user"
    cleared = client.patch(
        f"/library/web-sources/{source['id']}",
        json={
            "expected_revision": edited.json()["metadata_revision"],
            "mode": "edit",
            "metadata": {"abstract": None, "selected_excerpt": None},
        },
    )
    assert cleared.status_code == 200, cleared.text
    recaptured = _payload("capture-web-metadata-0003")
    recaptured.update(
        {
            "url": "https://en.wikipedia.org/wiki/Infrastructure_as_code",
            "canonical_url": "https://en.wikipedia.org/wiki/Infrastructure_as_code",
            "description": "Automatic text must not restore a manual clear.",
            "selected_excerpt": "Automatic passage must stay cleared.",
        }
    )
    recapture_response = client.post("/library/web-sources", json=recaptured)
    assert recapture_response.status_code == 200, recapture_response.text
    assert recapture_response.json()["item"]["metadata"]["abstract"] is None
    assert recapture_response.json()["item"]["metadata"]["selected_excerpt"] is None
    canonical_cleared = client.patch(
        f"/library/web-sources/{source['id']}",
        json={
            "expected_revision": recapture_response.json()["item"]["metadata_revision"],
            "mode": "edit",
            "metadata": {"canonical_url": None},
        },
    )
    assert canonical_cleared.status_code == 200, canonical_cleared.text
    assert canonical_cleared.json()["canonical_url"] == ""
    assert canonical_cleared.json()["metadata"]["canonical_url"] is None
    stale = client.patch(
        f"/library/web-sources/{source['id']}",
        json={"expected_revision": revision, "mode": "edit", "metadata": {"title": "Stale title"}},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "library_metadata_revision_conflict"


def test_paper_capture_and_manual_patch_share_one_serial_lock(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    key = _capture_key(client)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
    initial = _citation_payload("capture-paper-lock-initial")
    created = extension.post("/browser-capture/citations", json=initial)
    assert created.status_code == 201, created.text
    paper = client.get("/documents").json()[0]
    first_acquired, second_attempted, release_first = _hold_first_metadata_lock(
        monkeypatch, _LIBRARY_PAPER_METADATA_LOCK
    )
    user = TestClient(client.app, headers={"Authorization": str(client.headers["Authorization"])})
    recapture = _citation_payload("capture-paper-lock-recapture")
    recapture.update(
        {
            "description": "Automatic text must not overwrite a concurrent clear.",
            "metadata": {"publisher": "Concurrent Evidence Press"},
        }
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        patch_future = executor.submit(
            user.patch,
            f"/documents/{paper['id']}/metadata",
            json={
                "expected_revision": paper["metadata_revision"],
                "mode": "edit",
                "metadata": {"title": "Manually reviewed during capture", "abstract": None},
            },
        )
        assert first_acquired.wait(timeout=5)
        capture_future = executor.submit(
            extension.post, "/browser-capture/citations", json=recapture
        )
        assert second_attempted.wait(timeout=5)
        release_first.set()
        patched = patch_future.result(timeout=15)
        captured = capture_future.result(timeout=15)
    assert patched.status_code == 200, patched.text
    assert captured.status_code == 200, captured.text
    final = client.get("/documents").json()[0]
    assert final["metadata"]["title"] == "Manually reviewed during capture"
    assert final["metadata"]["abstract"] is None
    assert final["metadata"]["publisher"] == "Concurrent Evidence Press"
    assert final["metadata_provenance"]["abstract"]["source"] == "user"


def test_web_capture_and_manual_patch_share_one_serial_lock(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    initial = client.post("/library/web-sources", json=_payload("capture-web-lock-initial"))
    assert initial.status_code == 201, initial.text
    source = initial.json()["item"]
    first_acquired, second_attempted, release_first = _hold_first_metadata_lock(
        monkeypatch, _LIBRARY_WEB_METADATA_LOCK
    )
    user = TestClient(client.app, headers={"Authorization": str(client.headers["Authorization"])})
    recapture = _payload("capture-web-lock-recapture")
    recapture.update(
        {
            "description": "Automatic text must not overwrite a concurrent clear.",
            "metadata": {"publisher": "Concurrent Web Press"},
        }
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        patch_future = executor.submit(
            user.patch,
            f"/library/web-sources/{source['id']}",
            json={
                "expected_revision": source["metadata_revision"],
                "mode": "edit",
                "metadata": {"title": "Manually reviewed web source", "abstract": None},
            },
        )
        assert first_acquired.wait(timeout=5)
        capture_future = executor.submit(user.post, "/library/web-sources", json=recapture)
        assert second_attempted.wait(timeout=5)
        release_first.set()
        patched = patch_future.result(timeout=15)
        captured = capture_future.result(timeout=15)
    assert patched.status_code == 200, patched.text
    assert captured.status_code == 200, captured.text
    final = client.get("/library/web-sources").json()[0]
    assert final["metadata"]["title"] == "Manually reviewed web source"
    assert final["metadata"]["abstract"] is None
    assert final["metadata"]["publisher"] == "Concurrent Web Press"
    assert final["metadata_provenance"]["abstract"]["source"] == "user"


def test_linked_arxiv_pdf_keeps_landing_provenance_and_upgrades_without_doi(
    settings: Settings,
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation = _citation_payload("capture-arxiv-citation-0001")
    citation.update(
        {
            "source_url": "https://arxiv.org/abs/1706.03762#abstract",
            "canonical_url": "https://arxiv.org/abs/1706.03762",
            "title": "Attention Is All You Need",
            "description": "The reviewed arXiv abstract.",
            "doi": "",
            "extension_version": "0.1.1",
        }
    )
    assert extension.post("/browser-capture/citations", json=citation).status_code == 201
    paper = _pdf_payload("capture-arxiv-pdf-000001")
    paper.update(
        {
            "source_url": "https://arxiv.org/abs/1706.03762#abstract",
            "canonical_url": "https://arxiv.org/abs/1706.03762",
            "pdf_url": "https://arxiv.org/pdf/1706.03762v7",
            "title": "Attention Is All You Need",
            "description": "The reviewed arXiv abstract.",
            "doi": "",
            "captured_at": "2026-08-13T18:00:00Z",
            "extension_version": "0.1.1",
            "metadata_fields": ["citation_title", "citation_author", "pdf_url"],
        }
    )
    upgraded = extension.post("/browser-capture/papers", json=paper)
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["status"] == "pdf_attached"
    assert upgraded.json()["source_url"].endswith("#abstract")
    assert upgraded.json()["pdf_url"] == "https://arxiv.org/pdf/1706.03762v7"
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        document = session.scalar(select(DocumentRow))
        metadata = session.scalar(select(BrowserCapturedPaperMetadataRow))
        assert document is not None and document.url == paper["pdf_url"]
        assert metadata is not None
        assert metadata.source_url == paper["source_url"]
        assert metadata.canonical_url == paper["canonical_url"]
        assert metadata.description == paper["description"]
        assert metadata.provenance["extension_version"] == "0.1.1"
        assert metadata.provenance["attachment"] == "pdf"
    changed_pdf = dict(paper)
    changed_pdf["pdf_url"] = "https://arxiv.org/pdf/1706.03762v2"
    assert extension.post("/browser-capture/papers", json=changed_pdf).status_code == 409


def test_browser_capture_keeps_reviewed_metadata_out_of_global_work(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    secret = _pdf_payload("capture-private-metadata")
    secret["filename"] = "tenant-private-filename.pdf"
    secret["title"] = "Tenant private reviewed title"
    secret["authors"] = ["Private Person"]
    secret["doi"] = "10.9999/private"
    result = extension.post("/browser-capture/papers", json=secret)
    assert result.status_code == 201, result.text
    with db_session() as session:
        document = session.get(DocumentRow, result.json()["item"]["id"])
        assert document is not None
        work = session.get(WorkRow, document.work_id)
        assert work is not None
        serialized = repr((work.title, work.doi, work.year, work.payload))
        assert "Tenant private" not in serialized
        assert "Private Person" not in serialized
        assert "10.9999/private" not in serialized
        assert "tenant-private-filename" not in serialized


def test_confirmed_pdf_is_bounded_idempotent_and_keeps_provenance(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    payload = _pdf_payload()
    created = extension.post("/browser-capture/papers", json=payload)
    assert created.status_code == 201, created.text

    def reject_reparse(_content: bytes) -> None:
        pytest.fail("a finalized receipt must not reparse the same PDF")

    monkeypatch.setattr("sixsentences_server.api.app.extract_pdf_capture_metadata", reject_reparse)
    replay = extension.post("/browser-capture/papers", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "already_saved"
    assert replay.json()["item"]["title"] == payload["title"]
    changed = dict(payload)
    changed["title"] = "Different paper"
    assert extension.post("/browser-capture/papers", json=changed).status_code == 409
    with db_session() as session:
        doc = session.get(DocumentRow, created.json()["item"]["id"])
        assert doc is not None and doc.url == "https://example.org/paper.pdf"
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        metadata = session.scalar(select(BrowserCapturedPaperMetadataRow))
        assert metadata is not None
        assert "pdf_document" not in (metadata.provenance or {})


def test_waiting_pdf_capture_rejects_a_replaced_receipt(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    key = _capture_key(client)
    payload = _pdf_payload("capture-replaced-receipt")
    waiter_entered_poll = Event()
    release_waiter = Event()
    original_sleep = stdlib_time.sleep
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
    created = extension.post("/browser-capture/papers", json=payload)
    assert created.status_code == 201, created.text
    with db_session() as session:
        receipt = session.scalar(
            select(BrowserCaptureReceiptRow).where(
                BrowserCaptureReceiptRow.capture_id == payload["capture_id"]
            )
        )
        assert receipt is not None and receipt.document_id is not None
        document = session.get(DocumentRow, receipt.document_id)
        metadata = session.scalar(
            select(BrowserCapturedPaperMetadataRow).where(
                BrowserCapturedPaperMetadataRow.document_id == receipt.document_id
            )
        )
        assert document is not None and metadata is not None
        receipt.document_id = None
        session.delete(metadata)
        session.delete(document)

    def controlled_sleep(seconds: float) -> None:
        if seconds == 0.05:
            waiter_entered_poll.set()
            assert release_waiter.wait(timeout=5)
            return
        original_sleep(seconds)

    monkeypatch.setattr(api_app.time, "sleep", controlled_sleep)

    def save() -> object:
        waiting_extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
        return waiting_extension.post("/browser-capture/papers", json=payload)

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        waiter_future = executor.submit(save)
        assert waiter_entered_poll.wait(timeout=5)
        with db_session() as session:
            receipt = session.scalar(
                select(BrowserCaptureReceiptRow).where(
                    BrowserCaptureReceiptRow.capture_id == payload["capture_id"]
                )
            )
            assert receipt is not None and receipt.document_id is None
            receipt.payload_sha256 = "f" * 64
        release_waiter.set()
        waiter = waiter_future.result(timeout=10)
        assert waiter.status_code == 409, waiter.text
        assert waiter.json()["detail"] == "paper capture reservation was replaced; retry"
    finally:
        release_waiter.set()
        executor.shutdown(wait=True)


def test_normal_uploads_and_browser_capture_share_checksum_identity(settings: Settings) -> None:
    client = _client(settings)
    paper = _pdf_payload("capture-after-normal-upload-0001")
    upload_body = {"filename": paper["filename"], "content_base64": paper["content_base64"]}
    first = client.post("/orgs/current/documents", json=upload_body)
    second = client.post("/orgs/current/documents", json=upload_body)
    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    captured = extension.post("/browser-capture/papers", json=paper)
    assert captured.status_code == 200, captured.text
    assert captured.json()["item"]["id"] == first.json()["id"]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1


def test_capture_clones_run_bound_upload_into_durable_library_original(settings: Settings) -> None:
    client = _client(settings)
    paper = _pdf_payload("capture-run-bound-upload-0001")
    pending = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    ).json()
    project = client.post("/projects", json={"name": "Run-bound source"}).json()
    run = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "Keep this paper", "query": "keep", "document_ids": [pending["id"]]},
    ).json()
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    captured = extension.post("/browser-capture/papers", json=paper)
    assert captured.status_code == 200, captured.text
    captured_id = captured.json()["item"]["id"]
    assert captured_id == pending["id"]
    with db_session() as session:
        durable = session.get(DocumentRow, captured_id)
        assert durable is not None and durable.run_id is None
    assert client.delete(f"/runs/{run['id']}").status_code == 200
    listed = client.get("/documents").json()
    assert [row["id"] for row in listed] == [captured_id]
    assert client.get(f"/documents/{captured_id}/file").status_code == 200
    replay = extension.post("/browser-capture/papers", json=paper)
    assert replay.status_code == 200
    assert replay.json()["item"]["id"] == captured_id


def test_legacy_run_bound_receipt_replay_retargets_to_durable_winner(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    paper = _pdf_payload("capture-legacy-run-replay-0001")
    paper["doi"] = "10.5555/legacy-replay"
    captured = extension.post("/browser-capture/papers", json=paper)
    assert captured.status_code == 201, captured.text
    durable_id = captured.json()["item"]["id"]
    project = client.post("/projects", json={"name": "Legacy receipt"}).json()
    run = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "Keep the replay durable",
            "query": "replay",
            "document_ids": [durable_id],
        },
    ).json()
    with db_session() as session:
        run_document = session.scalar(
            select(DocumentRow).where(
                DocumentRow.run_id == run["id"], DocumentRow.checksum == paper["sha256"]
            )
        )
        assert run_document is not None
        receipt = session.scalar(
            select(BrowserCaptureReceiptRow).where(
                BrowserCaptureReceiptRow.capture_id == paper["capture_id"]
            )
        )
        assert receipt is not None
        receipt.document_id = run_document.id
    replay = extension.post("/browser-capture/papers", json=paper)
    assert replay.status_code == 200, replay.text
    assert replay.json()["item"]["id"] == durable_id
    with db_session() as session:
        receipt = session.scalar(
            select(BrowserCaptureReceiptRow).where(
                BrowserCaptureReceiptRow.capture_id == paper["capture_id"]
            )
        )
        assert receipt is not None and receipt.document_id == durable_id


def test_normal_upload_reuses_browser_captured_pdf(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    paper = _pdf_payload("capture-before-normal-upload-0001")
    captured = extension.post("/browser-capture/papers", json=paper)
    assert captured.status_code == 201, captured.text
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["id"] == captured.json()["item"]["id"]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1


def test_exact_normal_upload_enriches_browser_metadata_and_promotes_work(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    paper = _pdf_payload("capture-enrich-exact-0001")
    paper["title"] = "Reviewed browser title"
    paper["authors"] = ["Ada Author"]
    paper["doi"] = ""
    captured = extension.post("/browser-capture/papers", json=paper)
    assert captured.status_code == 201, captured.text
    document_id = captured.json()["item"]["id"]
    verified = WorkRecord(
        id="W123456789",
        doi="10.5555/enriched",
        title="Canonical provider title",
        year=2026,
        authors=["Ada Author", "Grace Researcher"],
        abstract="A verified abstract.",
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload._resolve_metadata",
        lambda *args, **kwargs: (verified, "doi"),
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload.PdfTextExtractor.extract",
        lambda *args, **kwargs: ExtractedText("Verified PDF text layer.", TextStatus.PARSED),
    )
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": paper["filename"], "content_base64": paper["content_base64"]},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["id"] == document_id
    assert uploaded.json()["verified"] is True, uploaded.json()
    listed = client.get("/documents").json()
    assert len(listed) == 1
    assert listed[0]["title"] == "Reviewed browser title"
    assert listed[0]["authors"] == ["Ada Author", "Grace Researcher"]
    assert listed[0]["year"] == 2026


def test_citation_normal_upload_attaches_pdf_in_place_and_becomes_verified(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation_payload = _citation_payload("capture-normal-upgrade-0001")
    citation_payload["doi"] = "10.5555/normal-upgrade"
    citation = extension.post("/browser-capture/citations", json=citation_payload)
    assert citation.status_code == 201, citation.text
    document_id = citation.json()["item"]["id"]
    verified = WorkRecord(
        id="W987654321",
        doi="10.5555/normal-upgrade",
        title="Verified full paper",
        year=2024,
        authors=["Ada Author", "Grace Researcher"],
        abstract="Verified provider abstract.",
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload._resolve_metadata",
        lambda *args, **kwargs: (verified, "doi"),
    )
    monkeypatch.setattr(
        "sixsentences_server.acquisition.upload.PdfTextExtractor.extract",
        lambda *args, **kwargs: ExtractedText("Verified PDF text layer.", TextStatus.PARSED),
    )
    pdf = _pdf_payload("capture-normal-upgrade-pdf")
    uploaded = client.post(
        "/orgs/current/documents",
        json={"filename": pdf["filename"], "content_base64": pdf["content_base64"]},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["id"] == document_id, uploaded.json()
    assert uploaded.json()["work_id"] == verified.id
    assert uploaded.json()["verified"] is True
    listed = client.get("/documents").json()
    assert len(listed) == 1 and listed[0]["has_file"] is True
    assert listed[0]["authors"] == ["Ada Author", "Grace Researcher"]


def test_concurrent_pdf_retry_creates_one_document(settings: Settings) -> None:
    client = _client(settings)
    key = _capture_key(client)
    payload = _pdf_payload("capture-paper-concurrent")

    def save() -> int:
        extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
        return extension.post("/browser-capture/papers", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _index: save(), range(2)))
    assert statuses == [200, 201]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1


def test_concurrent_distinct_pdf_receipts_bind_one_document(settings: Settings) -> None:
    client = _client(settings)
    key = _capture_key(client)

    def save(index: int) -> int:
        extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
        return extension.post(
            "/browser-capture/papers", json=_pdf_payload(f"capture-paper-race-{index:04d}")
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(save, range(2)))
    assert statuses == [200, 201]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        assert session.scalar(select(func.count(BrowserCaptureReceiptRow.id))) == 2
        assert all(
            receipt.document_id is not None
            for receipt in session.scalars(select(BrowserCaptureReceiptRow)).all()
        )


def test_concurrent_different_pdfs_with_same_doi_keep_one_attachment(settings: Settings) -> None:
    client = _client(settings)
    key = _capture_key(client)
    payloads = [
        _pdf_payload("capture-different-pdf-race-0001"),
        _pdf_payload("capture-different-pdf-race-0002"),
    ]
    writer = PdfWriter()
    writer.add_blank_page(width=500, height=700)
    writer.add_blank_page(width=500, height=700)
    buffer = BytesIO()
    writer.write(buffer)
    second_content = buffer.getvalue()
    payloads[1].update(
        {
            "content_base64": base64.b64encode(second_content).decode(),
            "sha256": hashlib.sha256(second_content).hexdigest(),
        }
    )
    for payload in payloads:
        payload["doi"] = "10.5555/concurrent-identity"
        payload["canonical_url"] = "https://example.org/concurrent-identity"

    def save(payload: dict[str, object]) -> int:
        extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
        return extension.post("/browser-capture/papers", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(save, payloads))
    assert statuses == [200, 201]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        receipts = session.scalars(select(BrowserCaptureReceiptRow)).all()
        assert len(receipts) == 2
        assert len({receipt.document_id for receipt in receipts}) == 1


def test_concurrent_citations_bind_both_receipts(settings: Settings) -> None:
    client = _client(settings)
    key = _capture_key(client)

    def save(index: int) -> int:
        extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
        return extension.post(
            "/browser-capture/citations",
            json=_citation_payload(f"capture-citation-race-{index:04d}"),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(save, range(2)))
    assert statuses == [200, 201]
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        assert session.scalar(select(func.count(BrowserCaptureReceiptRow.id))) == 2


def test_concurrent_arxiv_citation_and_pdf_share_one_paper(settings: Settings) -> None:
    client = _client(settings)
    key = _capture_key(client)
    citation = _citation_payload("capture-arxiv-race-citation")
    citation.update(
        {
            "source_url": "https://arxiv.org/abs/1706.03762",
            "canonical_url": "https://arxiv.org/abs/1706.03762",
            "doi": "",
        }
    )
    paper = _pdf_payload("capture-arxiv-race-paper")
    paper.update(
        {
            "source_url": "https://arxiv.org/abs/1706.03762v7",
            "canonical_url": "https://arxiv.org/abs/1706.03762",
            "pdf_url": "https://arxiv.org/pdf/1706.03762v7",
            "doi": "",
            "captured_at": "2026-08-13T18:00:00Z",
            "extension_version": "0.1.1",
        }
    )

    def save(kind: str) -> int:
        extension = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
        path, payload = (
            ("/browser-capture/citations", citation)
            if kind == "citation"
            else ("/browser-capture/papers", paper)
        )
        return extension.post(path, json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(save, ["citation", "paper"]))
    assert all(status in {200, 201} for status in statuses)
    with db_session() as session:
        assert session.scalar(select(func.count(DocumentRow.id))) == 1
        assert session.scalar(select(func.count(BrowserCapturedPaperMetadataRow.id))) == 1
        assert session.scalar(select(func.count(BrowserCaptureReceiptRow.id))) == 2


def test_canonical_duplicate_capture_id_stays_bound(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    created = extension.post("/library/web-sources", json=_payload("capture-alias-00000001"))
    assert created.status_code == 201
    duplicate = extension.post("/library/web-sources", json=_payload("capture-alias-00000002"))
    assert duplicate.status_code == 200
    changed = _payload("capture-alias-00000002")
    changed["url"] = "https://example.org/different"
    changed["canonical_url"] = "https://example.org/different"
    assert extension.post("/library/web-sources", json=changed).status_code == 409


def test_library_deletes_capture_receipts_without_sqlite_fk_cascades(settings: Settings) -> None:
    client = _client(settings)
    extension = TestClient(client.app, headers={"Authorization": f"Bearer {_capture_key(client)}"})
    citation_payload = _citation_payload("capture-delete-citation")
    citation = extension.post("/browser-capture/citations", json=citation_payload)
    assert citation.status_code == 201, citation.text
    document_id = citation.json()["item"]["id"]
    assert client.delete(f"/documents/{document_id}").status_code == 204
    with db_session() as session:
        assert session.scalar(select(func.count(BrowserCaptureReceiptRow.id))) == 0
        assert session.scalar(select(func.count(BrowserCapturedPaperMetadataRow.id))) == 0
    recreated = extension.post("/browser-capture/citations", json=citation_payload)
    assert recreated.status_code == 201, recreated.text
    web_payload = _payload("capture-delete-web-source")
    web = extension.post("/library/web-sources", json=web_payload)
    assert web.status_code == 201, web.text
    assert client.delete(f"/library/web-sources/{web.json()['item']['id']}").status_code == 204
    with db_session() as session:
        web_receipts = session.scalar(
            select(func.count(BrowserCaptureReceiptRow.id)).where(
                BrowserCaptureReceiptRow.web_source_id.is_not(None)
            )
        )
        assert web_receipts == 0
    assert extension.post("/library/web-sources", json=web_payload).status_code == 201


def test_capture_device_cap_revoke_and_companion_key_do_not_share_slots(settings: Settings) -> None:
    client = _client(settings)
    keys = [_capture_key(client) for _index in range(5)]
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "capture@example.org"))
        assert user is not None
        companion_code = create_companion_code(
            session, user.id, CHALLENGE, "Companion on the same Mac"
        )
    with db_session() as session:
        companion_key = exchange_companion_code(session, companion_code, VERIFIER)
        assert companion_key is not None
    pair = client.post(
        "/browser-capture/pair",
        json={
            "code_challenge": CHALLENGE,
            "state": STATE,
            "device_name": "Sixth browser",
            "redirect_uri": REDIRECT,
        },
    )
    code = pair.json()["callback_url"].split("code=", 1)[1].split("&", 1)[0]
    blocked = TestClient(client.app).post(
        "/browser-capture/pair/exchange",
        json={"code": code, "code_verifier": VERIFIER, "state": STATE},
    )
    assert blocked.status_code == 409
    devices = client.get("/browser-capture/devices").json()["devices"]
    assert len(devices) == 5
    assert client.delete(f"/browser-capture/devices/{devices[0]['id']}").status_code == 200
    replacement = _capture_key(client)
    assert replacement and replacement not in keys
