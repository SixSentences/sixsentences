"""Bibliography exports: BibTeX, RIS, CSL-JSON."""

import json

import pytest

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.reporting.exports import (
    render,
    source_record_url,
    to_bibtex,
    to_csl_json,
    to_ris,
)

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


def test_bibtex_labels_pubmed_identity_without_claiming_openalex() -> None:
    work = WorkRecord(id="pubmed:12345678", pmid="12345678", title="Biomedical study")
    out = to_bibtex([work])
    assert "note = {PubMed PMID: 12345678}" in out
    assert "OpenAlex" not in out


def test_source_record_url_resolves_supported_provider_identities() -> None:
    pubmed = WorkRecord(id="pubmed:12345678", pmid="12345678", title="Biomedical study")
    openalex = WorkRecord(id="W2741809807", title="OpenAlex study")
    upload = WorkRecord(id="upload:private", title="Private upload")
    assert source_record_url(pubmed) == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert source_record_url(openalex) == "https://openalex.org/W2741809807"
    assert source_record_url(upload) == ""


def test_ris_and_csl_keep_pubmed_identity_resolvable() -> None:
    work = WorkRecord(id="pubmed:12345678", pmid="12345678", title="Biomedical study")
    ris = to_ris([work])
    csl = json.loads(to_csl_json([work]))[0]
    assert "ID  - pubmed:12345678" in ris
    assert "UR  - https://pubmed.ncbi.nlm.nih.gov/12345678/" in ris
    assert csl["URL"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert csl["note"] == "PubMed PMID: 12345678"


def test_book_chapter_uses_chapter_types_and_container_fields() -> None:
    work = WorkRecord(
        id="pubmed:987",
        pmid="987",
        title="A chapter about evidence synthesis",
        authors=["Ada Researcher"],
        year=2025,
        venue="Handbook of Research Methods",
        work_type="book-chapter",
        volume="2",
        pages="101-118",
        publisher="Research Press",
    )
    bibtex = to_bibtex([work])
    ris = to_ris([work])
    csl = json.loads(to_csl_json([work]))[0]
    assert bibtex.startswith("@incollection{")
    assert "booktitle = {Handbook of Research Methods}" in bibtex
    assert "journal =" not in bibtex
    assert ris.startswith("TY  - CHAP")
    assert "T2  - Handbook of Research Methods" in ris
    assert "JO  -" not in ris
    assert csl["type"] == "chapter"
    assert csl["container-title"] == "Handbook of Research Methods"
    assert csl["page"] == "101-118"
    assert csl["publisher"] == "Research Press"


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
