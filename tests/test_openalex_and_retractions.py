"""Public metadata connectors are exercised with recorded synthetic responses."""

import traceback
from pathlib import Path

import httpx
import pytest

from sixsentences.connectors.openalex import OpenAlexClient, OpenAlexError
from sixsentences.connectors.retractions import (
    RetractionDownloadError,
    download_retractions,
    load_retracted_dois,
)


def test_openalex_search_parses_metadata_without_network() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "doi": "https://doi.org/10.5555/example",
                        "title": "Synthetic review",
                        "abstract_inverted_index": {"Synthetic": [0], "evidence": [1]},
                        "publication_year": 2025,
                        "primary_location": {"source": {"display_name": "Example Journal"}},
                        "authorships": [{"author": {"display_name": "Ada Example"}}],
                        "cited_by_count": 7,
                        "is_retracted": False,
                    }
                ],
                "meta": {"next_cursor": None, "count": 1},
            },
        )

    http = httpx.Client(
        base_url="https://api.openalex.org",
        transport=httpx.MockTransport(handler),
    )
    with OpenAlexClient(mailto="test@example.invalid", api_key="synthetic", http=http) as client:
        records = client.search('"systematic review"', limit=1)

    assert records[0].id == "W123"
    assert records[0].abstract == "Synthetic evidence"
    assert calls[0].url.params["mailto"] == "test@example.invalid"
    assert calls[0].url.params["api_key"] == "synthetic"


def test_openalex_public_parameters_fail_fast() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"results": [], "meta": {"next_cursor": None}})

    client = OpenAlexClient(
        api_key="synthetic",
        http=httpx.Client(
            base_url="https://api.openalex.org",
            transport=httpx.MockTransport(handler),
        ),
    )
    try:
        with pytest.raises(ValueError, match="direction"):
            client.related("W1", direction="sideways")
        with pytest.raises(ValueError, match="year_from"):
            client.search("evidence", year_from=2025, year_to=2024)
        with pytest.raises(ValueError, match="per_page"):
            list(client.iter_search_pages("evidence", per_page=0))
        with pytest.raises(ValueError, match="limit"):
            list(client.iter_works("has_abstract:true", limit=-1))
        assert client.related("W1", direction="cites", limit=0) == []
    finally:
        client.close()
    assert calls == []


def test_openalex_errors_do_not_expose_the_api_key() -> None:
    sentinel = "sentinel-api-key-must-not-leak"
    client = OpenAlexClient(
        api_key=sentinel,
        http=httpx.Client(
            base_url="https://api.openalex.org",
            transport=httpx.MockTransport(lambda _request: httpx.Response(400)),
        ),
    )
    try:
        with pytest.raises(OpenAlexError) as caught:
            client.search("synthetic query", limit=1)
    finally:
        client.close()

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert sentinel not in str(caught.value)
    assert sentinel not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_retraction_loader_normalizes_dois(tmp_path: Path) -> None:
    (tmp_path / "retraction_watch.csv").write_text(
        "OriginalPaperDOI,Title,RetractionNature\n"
        "https://doi.org/10.1/ABC,Synthetic,Retraction\n"
        "10.1/corrected,Corrected,Correction\n"
        "unavailable,None,Retraction\n",
        encoding="utf-8",
    )
    assert load_retracted_dois(tmp_path) == {"10.1/abc"}


def test_retraction_loader_accepts_a_utf8_bom(tmp_path: Path) -> None:
    (tmp_path / "retraction_watch.csv").write_bytes(
        b"\xef\xbb\xbfOriginalPaperDOI,Title,RetractionNature\n10.1/BOM,Synthetic,Retraction\n"
    )

    assert load_retracted_dois(tmp_path) == {"10.1/bom"}


def test_retraction_download_is_atomic_and_schema_checked(tmp_path: Path) -> None:
    payload = b"OriginalPaperDOI,Title,RetractionNature\n10.1/example,Synthetic,Retraction\n"
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=payload))
    )
    try:
        target = download_retractions(tmp_path, max_bytes=1_000, http=client)
    finally:
        client.close()
    assert target.read_bytes() == payload
    assert not list(tmp_path.glob(".*.tmp"))


def test_oversized_retraction_update_preserves_previous_cache(tmp_path: Path) -> None:
    target = tmp_path / "retraction_watch.csv"
    target.write_text("OriginalPaperDOI,Title\n10.1/old,Previous\n", encoding="utf-8")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"x" * 50))
    )
    try:
        with pytest.raises(RetractionDownloadError, match="size cap"):
            download_retractions(tmp_path, max_bytes=10, http=client)
    finally:
        client.close()
    assert "10.1/old" in target.read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".*.tmp"))


def test_retraction_download_rejects_redirects(tmp_path: Path) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "http://127.0.0.1/"})
        )
    )
    try:
        with pytest.raises(RetractionDownloadError, match="redirect"):
            download_retractions(tmp_path, http=client)
    finally:
        client.close()
    assert not (tmp_path / "retraction_watch.csv").exists()


def test_retraction_download_rejects_unexpected_csv_schema(tmp_path: Path) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"wrong,header\nvalue,value\n")
        )
    )
    try:
        with pytest.raises(RetractionDownloadError, match="schema"):
            download_retractions(tmp_path, http=client)
    finally:
        client.close()
    assert not (tmp_path / "retraction_watch.csv").exists()
