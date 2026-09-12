"""Portable corpus storage uses only synthetic records and local files."""

from pathlib import Path

import pytest

from sixsentences.core.models import WorkRecord
from sixsentences.corpus.local import CorpusError, LocalCorpus, build_local_corpus


def _records() -> list[WorkRecord]:
    return [
        WorkRecord(
            id="W2",
            title="Soil microbiology in dry regions",
            abstract="A field study.",
            cited_by_count=2,
        ),
        WorkRecord(
            id="W1",
            title="Active learning for systematic review screening",
            abstract="A recall-first screening method.",
            authors=["Ada Example"],
            referenced_works=["W2"],
            cited_by_count=12,
        ),
    ]


def test_build_verify_search_and_lookup(tmp_path: Path) -> None:
    manifest = build_local_corpus(_records(), tmp_path / "corpus")
    corpus = LocalCorpus(tmp_path / "corpus")

    assert manifest.works == 2
    assert corpus.verify().sha256 == manifest.sha256
    assert corpus.version().identifier.startswith("local-")
    assert [work.id for work in corpus.search('"active learning" AND screening')] == ["W1"]
    assert [work.id for work in corpus.by_ids(["W2", "W1", "W2"])] == ["W2", "W1"]


def test_build_rejects_duplicate_ids(tmp_path: Path) -> None:
    duplicate = WorkRecord(id="W1", title="Another record")
    with pytest.raises(ValueError, match="unique"):
        build_local_corpus([_records()[1], duplicate], tmp_path / "corpus")


def test_empty_corpus_is_valid_and_searchable(tmp_path: Path) -> None:
    build_local_corpus([], tmp_path / "corpus")
    corpus = LocalCorpus(tmp_path / "corpus")
    assert corpus.verify().works == 0
    assert corpus.search("evidence") == []


def test_verify_fails_closed_after_file_tampering(tmp_path: Path) -> None:
    build_local_corpus(_records(), tmp_path / "corpus")
    works_path = tmp_path / "corpus" / "works.parquet"
    works_path.write_bytes(works_path.read_bytes() + b"tamper")

    with pytest.raises(CorpusError, match="checksum"):
        LocalCorpus(tmp_path / "corpus").verify()
