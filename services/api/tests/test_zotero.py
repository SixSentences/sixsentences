"""Zotero sync: item mapping + Web API client (offline via MockTransport)."""

import json

import httpx

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.reporting.zotero import ZoteroClient, to_zotero_items


def test_to_zotero_items_maps_fields() -> None:
    work = WorkRecord(
        id="W1", doi="10.1/x", title="A study", authors=["A. One", "B. Two"], year=2020, venue="J"
    )
    item = to_zotero_items([work])[0]
    assert item["itemType"] == "journalArticle"
    assert item["title"] == "A study" and item["DOI"] == "10.1/x" and item["date"] == "2020"
    assert item["creators"][0] == {"creatorType": "author", "name": "A. One"}
    assert item["extra"] == "OpenAlex: W1"
    assert item["tags"] == [{"tag": "SixSentences"}]


def test_to_zotero_items_labels_pubmed_identity() -> None:
    work = WorkRecord(id="pubmed:123", pmid="123", title="A biomedical study")
    item = to_zotero_items([work])[0]
    assert item["extra"] == "PubMed PMID: 123"
    assert item["url"] == "https://pubmed.ncbi.nlm.nih.gov/123/"
    assert "OpenAlex" not in item["extra"]


def test_to_zotero_items_maps_book_chapter_as_book_section() -> None:
    work = WorkRecord(
        id="pubmed:987",
        pmid="987",
        title="A chapter about evidence synthesis",
        work_type="book-chapter",
        venue="Handbook of Research Methods",
        pages="101-118",
        publisher="Research Press",
    )
    item = to_zotero_items([work])[0]
    assert item["itemType"] == "bookSection"
    assert item["bookTitle"] == "Handbook of Research Methods"
    assert "publicationTitle" not in item
    assert item["pages"] == "101-118"
    assert item["publisher"] == "Research Press"


def test_client_sends_key_and_reports_counts() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"successful": {"0": {}, "1": {}}, "failed": {"2": {}}})

    client = ZoteroClient(
        "KEY", "user", "12345", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.create_items([{"itemType": "journalArticle"}] * 3)
    assert result.created == 2 and result.failed == 1
    assert seen[0].headers["Zotero-API-Key"] == "KEY"
    assert str(seen[0].url) == "https://api.zotero.org/users/12345/items"


def test_client_counts_an_error_status_as_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "invalid key"})

    client = ZoteroClient(
        "BAD", "user", "1", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.create_items([{"itemType": "journalArticle"}])
    assert result.created == 0 and result.failed == 1


def test_client_chunks_over_fifty_items() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        n = len(json.loads(request.content))
        ok = {str(i): {} for i in range(n)}
        return httpx.Response(200, json={"successful": ok, "failed": {}})

    client = ZoteroClient(
        "KEY", "group", "9", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.create_items([{"itemType": "journalArticle"}] * 120)
    assert calls == 3 and result.created == 120  # 50 + 50 + 20
