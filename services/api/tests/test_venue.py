"""Venue integrity: list-driven, honest grey zone, no built-in accusations."""

from pathlib import Path

from sixsentences_server.integrity.venue import (
    assess_venue,
    is_non_peer_reviewed,
    is_preprint,
    load_venue_lists,
    normalize_venue,
)


def test_unknown_without_a_venue() -> None:
    assert assess_venue(None).status == "unknown"
    assert assess_venue("").status == "unknown"


def test_preprint_is_detected() -> None:
    assert assess_venue("arXiv (Cornell University)").status == "preprint"
    assert is_preprint("bioRxiv") and is_preprint("SSRN Electronic Journal")


def test_predatory_only_when_a_list_matches() -> None:
    predatory = frozenset({normalize_venue("Journal of Dubious Studies")})
    assert assess_venue("Journal of Dubious Studies", predatory=predatory).status == "predatory"
    # no built-in list -> the same venue is merely "unlisted", never accused
    assert assess_venue("Journal of Dubious Studies").status == "unlisted"


def test_indexed_from_the_whitelist() -> None:
    indexed = frozenset({normalize_venue("Nature Communications")})
    assert assess_venue("Nature Communications", indexed=indexed).status == "indexed"


def test_unlisted_is_the_honest_grey_zone() -> None:
    assert assess_venue("Some Regular Conference").status == "unlisted"


def test_lists_load_from_data_dir(tmp_path: Path) -> None:
    (tmp_path / "venues_indexed.txt").write_text("Nature\nScience\n", encoding="utf-8")
    (tmp_path / "venues_predatory.txt").write_text("Bad Journal\n", encoding="utf-8")
    indexed, predatory = load_venue_lists(tmp_path)
    assert normalize_venue("Nature") in indexed
    assert normalize_venue("Bad Journal") in predatory


def test_missing_files_yield_empty_lists(tmp_path: Path) -> None:
    indexed, predatory = load_venue_lists(tmp_path)
    assert indexed == frozenset() and predatory == frozenset()


def test_is_non_peer_reviewed() -> None:
    assert is_non_peer_reviewed("preprint", "article")  # a preprint venue
    assert is_non_peer_reviewed("indexed", "preprint")  # a preprint type
    assert is_non_peer_reviewed("unlisted", "dataset")  # a non-article type
    assert not is_non_peer_reviewed("indexed", "article")  # a real article stays
    assert not is_non_peer_reviewed("unlisted", None)  # unknown venue/type -> kept
