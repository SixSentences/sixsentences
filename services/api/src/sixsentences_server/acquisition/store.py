"""Content-addressed document store.

Blobs live at ``<root>/blobs/<sha[:2]>/<sha>``, extracted text at
``<root>/text/<sha>.txt``. Content-addressing gives free dedup (the same PDF
fetched by two runs is one blob) and a stable checksum for the audit ledger.
The local-filesystem backend is the H0-of-H1 stand-in for object storage; the DB
row references a blob by its sha, so swapping the backend later touches nothing
else.
"""

import hashlib
from pathlib import Path
from typing import Protocol


class DocumentStore(Protocol):
    def put(self, content: bytes) -> tuple[str, str]:
        """Store bytes; return (sha256_hex, storage_path)."""
        ...

    def put_text(self, checksum: str, text: str) -> None: ...

    def get(self, checksum: str) -> bytes | None: ...

    def get_text(self, checksum: str) -> str | None: ...


class LocalDocumentStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs = root / "blobs"
        self.texts = root / "text"

    def _blob_path(self, checksum: str) -> Path:
        return self.blobs / checksum[:2] / checksum

    def _text_path(self, checksum: str) -> Path:
        return self.texts / f"{checksum}.txt"

    def put(self, content: bytes) -> tuple[str, str]:
        checksum = hashlib.sha256(content).hexdigest()
        path = self._blob_path(checksum)
        if not path.exists():  # content-addressed: identical bytes are written once
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return checksum, str(path)

    def put_text(self, checksum: str, text: str) -> None:
        path = self._text_path(checksum)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def get(self, checksum: str) -> bytes | None:
        path = self._blob_path(checksum)
        return path.read_bytes() if path.exists() else None

    def get_text(self, checksum: str) -> str | None:
        path = self._text_path(checksum)
        return path.read_text(encoding="utf-8") if path.exists() else None
