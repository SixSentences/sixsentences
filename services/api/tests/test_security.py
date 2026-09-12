"""Security hardening: auth abuse, egress and hostile file bounds."""

import io
import logging
import struct
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import sixsentences_server.core.db as dbmod
from sixsentences_server.api.app import create_app
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import RateLimitBucket
from sixsentences_server.core.limits import MAX_REQUEST_BODY_BYTES
from sixsentences_server.core.ratelimit import (
    DatabaseRateLimiter,
    RateLimiter,
    RateLimiterUnavailable,
)
from sixsentences_server.core.security_logging import SensitiveDataFilter, redact_log_value
from sixsentences_server.core.uploads import (
    UnsafeArchiveError,
    UnsafeImageError,
    decode_image,
    open_safe_zip,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus


def test_rate_limiter_bounds_a_fixed_window() -> None:
    clock = {"t": 1000.0}
    limiter = RateLimiter(max_hits=3, window_seconds=60.0)
    limiter._clock = lambda: clock["t"]
    assert [limiter.allow("ip") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("other") is True
    clock["t"] += 61
    assert limiter.allow("ip") is True


def test_database_rate_limiter_is_atomic_and_pseudonymized(corpus: DuckDBCorpus) -> None:
    dbmod.init_db()
    clock = {"t": 1200.0}
    first = DatabaseRateLimiter(scope="shared-test", max_hits=25, window_seconds=60.0)
    second = DatabaseRateLimiter(scope="shared-test", max_hits=25, window_seconds=60.0)
    first._clock = lambda: clock["t"]
    second._clock = lambda: clock["t"]
    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = list(
            executor.map(
                lambda index: (first if index % 2 else second).allow("203.0.113.9"), range(80)
            )
        )
    assert sum(decisions) == 25
    with dbmod.db_session() as session:
        bucket = session.scalar(
            select(RateLimitBucket).where(RateLimitBucket.scope == "shared-test")
        )
        assert bucket is not None
        assert bucket.count == 80
        assert bucket.key_hash != "203.0.113.9"
        assert len(bucket.key_hash) == 64
    clock["t"] += 61
    assert first.allow("203.0.113.9") is True


def test_database_rate_limit_budget_is_shared_across_app_instances(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIX_RATE_LIMIT_BACKEND", "database")
    get_settings.cache_clear()
    first = TestClient(create_app())
    second = TestClient(create_app())
    statuses: list[int] = []
    for index in range(6):
        client = first if index % 2 == 0 else second
        statuses.append(
            client.post(
                "/auth/register",
                headers={"X-Forwarded-For": "198.51.100.27"},
                json={
                    "email": f"shared-limit-{index}@lab.org",
                    "password": "StrongPass123!",
                    "org_name": f"Shared limiter {index}",
                    "name": "Shared limiter",
                },
            ).status_code
        )
    assert statuses[:5] == [201] * 5
    assert statuses[5] == 429
    get_settings.cache_clear()


def test_database_rate_limit_failure_is_fail_closed(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIX_RATE_LIMIT_BACKEND", "database")
    get_settings.cache_clear()

    def unavailable(_limiter: DatabaseRateLimiter, _key: str) -> bool:
        raise RateLimiterUnavailable("test outage")

    monkeypatch.setattr(DatabaseRateLimiter, "allow", unavailable)
    response = TestClient(create_app()).post(
        "/auth/login",
        headers={"X-Forwarded-For": "192.0.2.88"},
        json={"email": "nobody@example.test", "password": "StrongPass123!"},
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["detail"]["code"] == "rate_limit_unavailable"
    get_settings.cache_clear()


def test_login_is_rate_limited_per_ip(corpus: DuckDBCorpus) -> None:
    client = TestClient(create_app())
    client.post(
        "/auth/register",
        json={
            "email": "victim@lab.org",
            "password": "StrongPass123!",
            "org_name": "V",
            "name": "V",
        },
    )
    statuses = [
        client.post(
            "/auth/login", json={"email": "victim@lab.org", "password": "wrong"}
        ).status_code
        for _ in range(12)
    ]
    assert 429 in statuses
    assert statuses.count(401) <= 10
    limited = client.post("/auth/login", json={"email": "victim@lab.org", "password": "wrong"})
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_rate_limit_uses_the_proxy_appended_client_hop(corpus: DuckDBCorpus) -> None:
    client = TestClient(create_app())
    client.post(
        "/auth/register",
        json={
            "email": "forwarded-victim@lab.org",
            "password": "StrongPass123!",
            "org_name": "Forwarded",
            "name": "Forwarded",
        },
    )
    statuses = [
        client.post(
            "/auth/login",
            headers={"X-Forwarded-For": f"198.51.100.{index}, 203.0.113.90"},
            json={"email": "forwarded-victim@lab.org", "password": "wrong"},
        ).status_code
        for index in range(11)
    ]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


def test_registration_spam_is_throttled(corpus: DuckDBCorpus) -> None:
    client = TestClient(create_app())
    statuses = [
        client.post(
            "/auth/register",
            json={
                "email": f"spam{i}@lab.org",
                "password": "StrongPass123!",
                "org_name": f"Spam {i}",
                "name": "S",
            },
        ).status_code
        for i in range(8)
    ]
    assert 429 in statuses


def test_public_read_endpoints_are_rate_limited(corpus: DuckDBCorpus) -> None:
    client = TestClient(create_app())
    statuses = [
        client.get(
            "/public/surveys/nonexistent", headers={"X-Forwarded-For": "203.0.113.44"}
        ).status_code
        for _ in range(121)
    ]
    assert statuses[:120] == [404] * 120
    assert statuses[120] == 429


def test_oversized_request_is_rejected_before_parsing(corpus: DuckDBCorpus) -> None:
    client = TestClient(create_app())
    response = client.post(
        "/auth/login", content=b"{}", headers={"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1)}
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "request body is too large"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_webhook_delivery_gates_each_target_through_the_guard() -> None:
    """Delivery consults the SSRF guard per target — the default is the
    resolve=True check, so a host that resolves inward is dropped at send
    time, not just literal-IP probes at registration."""
    from sixsentences_server.core import notifications

    calls: list[str] = []

    def guard(url: str) -> bool:
        calls.append(url)
        return "internal" not in url

    class _Hook:
        url = "https://internal.evil.test/collect"
        events: list[str] = []
        secret = "s"

    class _Session:
        def scalars(self, *_a, **_k):

            class _R:
                def all(self_inner):
                    return [_Hook()]

            return _R()

    delivered = notifications.fire_run_event(
        _Session(), org_id=1, event="run.completed", payload={}, guard=guard
    )
    assert delivered == 0
    assert calls == ["https://internal.evil.test/collect"]


def test_pdf_extraction_caps_pages_against_a_page_bomb() -> None:
    from sixsentences_server.acquisition.pdf import _MAX_PDF_PAGES, PdfTextExtractor

    seen = {"pages": 0}

    class _Page:
        def extract_text(self) -> str:
            seen["pages"] += 1
            return "text "

    class _HugeReader:
        pages = [_Page() for _ in range(_MAX_PDF_PAGES + 500)]

    import sixsentences_server.acquisition.pdf as pdf_mod

    original = pdf_mod.pypdf.PdfReader
    pdf_mod.pypdf.PdfReader = lambda *_a, **_k: _HugeReader()
    try:
        PdfTextExtractor().extract(b"%PDF-1.4 fake", "application/pdf")
    finally:
        pdf_mod.pypdf.PdfReader = original
    assert seen["pages"] == _MAX_PDF_PAGES


def test_zip_bombs_and_symbolic_links_are_rejected_before_reading() -> None:
    compressed = io.BytesIO()
    with ZipFile(compressed, "w", ZIP_DEFLATED) as archive:
        archive.writestr("huge.xml", b"0" * 1000000)
    with pytest.raises(UnsafeArchiveError, match="compression ratio"):
        open_safe_zip(
            compressed.getvalue(),
            max_files=10,
            max_uncompressed_bytes=2000000,
            max_compression_ratio=100,
        )
    linked = io.BytesIO()
    with ZipFile(linked, "w") as archive:
        member = ZipInfo("linked.tex")
        member.external_attr = 41471 << 16 | 40960
        archive.writestr(member, b"target")
    with pytest.raises(UnsafeArchiveError, match="symbolic link"):
        open_safe_zip(linked.getvalue(), max_files=10, max_uncompressed_bytes=10000)


def test_image_canvas_is_bounded_before_pixel_allocation() -> None:

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 4294967295)
        )

    header = struct.pack(">IIBBBBB", 7000, 7000, 8, 2, 0, 0, 0)
    oversized = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")
    with pytest.raises(UnsafeImageError, match="pixel safety limit"):
        decode_image(oversized)


def test_operational_log_filter_redacts_credentials_and_query_tickets() -> None:
    record = logging.LogRecord(
        "security-test",
        logging.ERROR,
        __file__,
        1,
        "failed %s at %s",
        (
            "Bearer six_ss_supersecretvalue",
            "https://api.example/events?ticket=six_st_verysecretvalue&run=1",
        ),
        None,
    )
    assert SensitiveDataFilter().filter(record)
    rendered = record.getMessage()
    assert "supersecretvalue" not in rendered
    assert "verysecretvalue" not in rendered
    assert rendered.count("REDACTED") == 2


@pytest.mark.parametrize(
    "token",
    [
        "github_pat_A1b2.C3d4-E5f6_7890token",
        "gho_A1b2.C3d4-E5f6_7890token",
        "ghu_A1b2.C3d4-E5f6_7890token",
    ],
)
def test_operational_log_filter_redacts_dotted_github_tokens(token: str) -> None:
    rendered = redact_log_value(f"GitHub request failed for {token}")
    assert token not in rendered
    assert "REDACTED" in rendered


def test_operational_log_filter_redacts_sensitive_form_query_values() -> None:
    record = logging.LogRecord(
        "security-test",
        logging.INFO,
        __file__,
        1,
        "request %s",
        (
            "https://app.example/survey?email=person%40example.org&answer=private-response&respondent_label=Participant+1",
        ),
        None,
    )
    assert SensitiveDataFilter().filter(record)
    rendered = record.getMessage()
    assert "person%40example.org" not in rendered
    assert "private-response" not in rendered
    assert "Participant+1" not in rendered
    assert rendered.count("REDACTED") == 3


def test_operational_log_filter_redacts_provider_secrets_content_and_pii() -> None:
    secret = "sk_live_abcdefghijklmnopqrstuv"
    private_key = "-----BEGIN PRIVATE KEY-----\nprivate-research-key\n-----END PRIVATE KEY-----"
    rendered = redact_log_value(
        f"contact researcher@example.org; Authorization: Bearer {secret}; password is thesis-secret; {private_key}"
    )
    for private_value in (
        "researcher@example.org",
        secret,
        "thesis-secret",
        "private-research-key",
    ):
        assert private_value not in rendered


def test_operational_log_filter_redacts_structured_extras_and_exception_details() -> None:
    try:
        raise RuntimeError(
            "provider response contained prompt=confidential-hypothesis and token=six_ss_runtimecredential"
        )
    except RuntimeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "security-test", logging.ERROR, __file__, 1, "provider request failed", (), exc_info
    )
    record.prompt = "private research question"
    record.metadata = {
        "email": "participant@example.org",
        "safe_status": "failed",
        "nested": ["https://example.org/?ticket=six_st_privatevalue"],
    }
    assert SensitiveDataFilter().filter(record)
    formatted = logging.Formatter("%(message)s").format(record)
    serialized = repr(record.__dict__)
    assert "confidential-hypothesis" not in formatted
    assert "runtimecredential" not in formatted
    assert "private research question" not in serialized
    assert "participant@example.org" not in serialized
    assert "privatevalue" not in serialized
    assert "RuntimeError: details redacted" in formatted
    assert record.metadata["safe_status"] == "failed"
