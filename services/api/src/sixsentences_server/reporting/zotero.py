"""Zotero Web API v3 client and loss-minimising item mapping."""

import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx

from sixsentences_server.core.models import WorkRecord

ZOTERO_API = "https://api.zotero.org"
_MAX_ITEMS = 50  # Zotero accepts up to 50 items per write
# The library coordinates come from a request body and are pasted straight into
# the request path. Anything outside this alphabet ("..", "?", "#", "//", "@")
# could move the call off the ``/users/<id>/items`` path it must stay on, so the
# client refuses it instead of asking the caller to have validated it.
_LIBRARY_TYPES = ("user", "group")
_LIBRARY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_COLLECTION_KEY = re.compile(r"[A-Za-z0-9]{1,32}")


def to_zotero_items(works: list[WorkRecord]) -> list[dict[str, Any]]:
    return [
        {
            "itemType": "journalArticle",
            "title": w.title,
            "creators": [{"creatorType": "author", "name": a} for a in w.authors[:50]],
            "date": str(w.year) if w.year else "",
            "publicationTitle": w.venue or "",
            "DOI": w.doi or "",
            "url": w.oa_url or w.pdf_url or "",
            "extra": f"OpenAlex: {w.id}",
            "tags": [{"tag": "SixSentences"}],
        }
        for w in works
    ]


@dataclass
class ZoteroResult:
    created: int
    failed: int
    keys: dict[int, str] | None = None


@dataclass
class ZoteroPage:
    items: list[dict[str, Any]]
    library_version: int


class ZoteroClient:
    def __init__(
        self,
        api_key: str,
        library_type: str,
        library_id: str,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        if library_type not in _LIBRARY_TYPES:
            raise ValueError("Zotero library type must be 'user' or 'group'")
        if not _LIBRARY_ID.fullmatch(library_id):
            raise ValueError("Zotero library id must be letters, digits, '-' or '_'")
        self.api_key = api_key
        self.root = f"{ZOTERO_API}/{library_type}s/{library_id}"
        self.url = f"{self.root}/items"
        self.http = http or httpx.Client(timeout=60)

    @property
    def headers(self) -> dict[str, str]:
        return {"Zotero-API-Key": self.api_key, "Zotero-API-Version": "3"}

    def key_info(self) -> dict[str, Any] | None:
        """Validate a key and return the scopes Zotero assigned to it."""
        try:
            response = self.http.get(f"{ZOTERO_API}/keys/current", headers=self.headers)
        except httpx.HTTPError:
            return None
        return response.json() if response.status_code == 200 else None

    def list_collections(self) -> list[dict[str, Any]]:
        try:
            response = self.http.get(
                f"{self.root}/collections",
                params={"format": "json", "limit": 100},
                headers=self.headers,
            )
        except httpx.HTTPError:
            return []
        if response.status_code != 200:
            return []
        return [
            {
                "key": str(row.get("key", "")),
                "name": str((row.get("data") or {}).get("name", "")),
                "parent": str((row.get("data") or {}).get("parentCollection", "")),
                "version": int(row.get("version") or 0),
            }
            for row in response.json()
            if row.get("key")
        ]

    def read_items(
        self,
        *,
        since: int = 0,
        collection_key: str = "",
        limit: int = 10_000,
    ) -> ZoteroPage:
        """Read an incremental library snapshot, preserving notes and attachments."""
        if collection_key and not _COLLECTION_KEY.fullmatch(collection_key):
            raise ValueError("Zotero collection key must be letters or digits")
        items: list[dict[str, Any]] = []
        start = 0
        library_version = since
        url = f"{self.root}/collections/{collection_key}/items" if collection_key else self.url
        while len(items) < limit:
            params: dict[str, Any] = {
                "format": "json",
                "limit": min(100, limit - len(items)),
                "start": start,
                "include": "data",
            }
            if since > 0:
                params["since"] = since
            response = self.http.get(url, params=params, headers=self.headers)
            if response.status_code != 200:
                response.raise_for_status()
            with suppress(ValueError):
                library_version = max(
                    library_version,
                    int(response.headers.get("Last-Modified-Version", library_version)),
                )
            page = response.json()
            if not page:
                break
            items.extend(page)
            if len(page) < 100:
                break
            start += len(page)
        return ZoteroPage(items=items[:limit], library_version=library_version)

    def deleted_since(self, version: int) -> tuple[list[str], int]:
        if version <= 0:
            return [], 0
        response = self.http.get(
            f"{self.root}/deleted", params={"since": version}, headers=self.headers
        )
        if response.status_code != 200:
            response.raise_for_status()
        data = response.json()
        try:
            latest = int(response.headers.get("Last-Modified-Version", version))
        except ValueError:
            latest = version
        return [str(key) for key in data.get("items", [])], latest

    def list_items(self, *, limit: int = 300) -> list[dict[str, Any]]:
        """Read the library's items (title + DOI is all canary resolution
        needs). Attachments and notes are skipped; the key is used for this
        request only and never stored."""
        items: list[dict[str, Any]] = []
        start = 0
        while len(items) < limit:
            try:
                response = self.http.get(
                    self.url,
                    params={
                        "format": "json",
                        "limit": min(100, limit - len(items)),
                        "start": start,
                    },
                    headers={
                        **self.headers,
                    },
                )
            except httpx.HTTPError:
                break
            if response.status_code != 200:
                break
            page = response.json()
            if not page:
                break
            for item in page:
                data = item.get("data") or {}
                if data.get("itemType") in ("attachment", "note"):
                    continue
                title = (data.get("title") or "").strip()
                doi = (data.get("DOI") or "").strip().lower()
                if title or doi:
                    items.append({"title": title, "doi": doi})
            if len(page) < 100:
                break
            start += len(page)
        return items[:limit]

    def create_items(self, items: list[dict[str, Any]]) -> ZoteroResult:
        created = failed = 0
        keys: dict[int, str] = {}
        for start in range(0, len(items), _MAX_ITEMS):
            chunk = items[start : start + _MAX_ITEMS]
            try:
                response = self.http.post(
                    self.url,
                    json=chunk,
                    headers={**self.headers, "Content-Type": "application/json"},
                )
            except httpx.HTTPError:
                failed += len(chunk)
                continue
            if response.status_code not in (200, 201):
                failed += len(chunk)
                continue
            data = response.json()
            successful = data.get("successful", {})
            created += len(successful)
            failed += len(data.get("failed", {}))
            for local_index, item in successful.items():
                key = str(item.get("key", "")) if isinstance(item, dict) else ""
                if key:
                    keys[start + int(local_index)] = key
        return ZoteroResult(created=created, failed=failed, keys=keys)

    def update_item(self, key: str, data: dict[str, Any], version: int) -> bool:
        response = self.http.put(
            f"{self.url}/{key}",
            json=data,
            headers={
                **self.headers,
                "Content-Type": "application/json",
                "If-Unmodified-Since-Version": str(version),
            },
        )
        return response.status_code in (200, 204)
