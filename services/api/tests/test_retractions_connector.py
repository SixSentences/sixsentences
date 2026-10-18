"""The Retraction Watch loader flags retractions, not every notice in the dataset."""

from pathlib import Path

from sixsentences_server.connectors.retractions import CACHE_FILE, load_retracted_dois


def test_only_retractions_are_flagged(tmp_path: Path) -> None:
    (tmp_path / CACHE_FILE).write_text(
        "Record ID,OriginalPaperDOI,RetractionNature\n"
        "1,10.1/retracted,Retraction\n"
        "2,10.1/corrected,Correction\n"
        "3,10.1/concern,Expression of concern\n"
        "4,10.1/reinstated,Reinstatement\n"
        "5,https://doi.org/10.1/SPACED, retraction \n"
        "6,unavailable,Retraction\n",
        encoding="utf-8",
    )

    assert load_retracted_dois(tmp_path) == {"10.1/retracted", "10.1/spaced"}


def test_a_byte_order_mark_does_not_hide_the_first_column(tmp_path: Path) -> None:
    (tmp_path / CACHE_FILE).write_bytes(
        b"\xef\xbb\xbfOriginalPaperDOI,RetractionNature\n10.1/BOM,Retraction\n"
    )

    assert load_retracted_dois(tmp_path) == {"10.1/bom"}


def test_missing_data_yields_no_dois(tmp_path: Path) -> None:
    assert load_retracted_dois(tmp_path) == set()
