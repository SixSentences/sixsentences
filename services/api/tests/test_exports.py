"""Bibliography exports: BibTeX, RIS, CSL-JSON."""

import json

import pytest

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.reporting.exports import render, to_bibtex, to_csl_json, to_ris

W1 = WorkRecord(
    id="W1",
    doi="10.1/abc",
    title="Attention & Transformers",
    year=2017,
    venue="NeurIPS",
    authors=["Ashish Vaswani", "Noam Shazeer"],
    cited_by_count=100000,
)
W2 = WorkRecord(id="W2", title="Untitled Preprint", authors=[], is_retracted=True)


def test_bibtex_entry_structure_and_escaping() -> None:
    out = to_bibtex([W1])
    assert out.startswith("@article{vaswani2017attention,")
    assert "title = {Attention \\& Transformers}" in out  # LaTeX-escaped ampersand
    assert "author = {Ashish Vaswani and Noam Shazeer}" in out
    assert "year = {2017}" in out
    assert "journal = {NeurIPS}" in out
    assert "doi = {10.1/abc}" in out
    assert "note = {OpenAlex:W1}" in out  # traceable back to the corpus id


def test_bibtex_keys_are_unique_on_collision() -> None:
    a = WorkRecord(id="Wa", title="Attention Is All", year=2017, authors=["Ashish Vaswani"])
    b = WorkRecord(id="Wb", title="Attention Applied", year=2017, authors=["Ashish Vaswani"])
    out = to_bibtex([a, b])
    assert "@article{vaswani2017attention," in out
    assert "@article{vaswani2017attentiona," in out  # deduped suffix


def test_bibtex_key_uses_title_when_author_missing() -> None:
    assert "@article{untitled," in to_bibtex([W2])  # no author/year -> first title word


def test_bibtex_key_falls_back_to_id_when_no_usable_words() -> None:
    bare = WorkRecord(id="W9", title="Of A")  # only stop/short words, no author, no year
    assert "@article{w9," in to_bibtex([bare])


def test_ris_fields_and_author_inversion() -> None:
    out = to_ris([W1])
    assert out.startswith("TY  - JOUR")
    assert "AU  - Vaswani, Ashish" in out  # 'Given Family' -> 'Family, Given'
    assert "TI  - Attention & Transformers" in out
    assert "PY  - 2017" in out
    assert "DO  - 10.1/abc" in out
    assert "ID  - W1" in out
    assert out.rstrip().endswith("ER  -")


def test_csl_json_is_valid_and_structured() -> None:
    items = json.loads(to_csl_json([W1, W2]))
    assert items[0]["type"] == "article-journal"
    assert items[0]["author"][0] == {"family": "Vaswani", "given": "Ashish"}
    assert items[0]["issued"] == {"date-parts": [[2017]]}
    assert items[0]["container-title"] == "NeurIPS"
    assert items[0]["DOI"] == "10.1/abc"
    assert items[1]["note"] == "RETRACTED"  # retraction surfaced honestly


def test_render_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unknown export format"):
        render([W1], "endnote")
