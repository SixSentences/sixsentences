"""Verbatim-quote verification: real spans pass, fabrications are dropped."""

from sixsentences_server.core.models import ReviewProtocol, Verdict, WorkRecord
from sixsentences_server.llm.mock import mock_pool
from sixsentences_server.screening.ensemble import screen_ensemble
from sixsentences_server.screening.quote import verify_quote

SRC = (
    "We introduce the Transformer, a model based on self-attention, "
    "achieving state-of-the-art results on machine translation."
)


def test_exact_span_verifies() -> None:
    quote, status = verify_quote("based on self-attention", SRC)
    assert status == "exact" and quote == "based on self-attention"


def test_case_quote_marks_and_punctuation_are_tolerated() -> None:
    quote, status = verify_quote('"Based On Self Attention."', SRC)
    assert status == "exact" and quote


def test_hyphenation_variant_verifies() -> None:
    # source has "state-of-the-art"; the model re-quotes it unhyphenated
    quote, status = verify_quote("state of the art results", SRC)
    assert quote is not None and status in ("exact", "fuzzy")


def test_fuzzy_mostly_verbatim_span_verifies() -> None:
    quote, status = verify_quote("a model based on self-attention achieving x", SRC)
    assert quote is not None  # long verbatim run + tiny trailing noise


def test_fabricated_quote_is_rejected() -> None:
    quote, status = verify_quote("we prove a quantum speedup for sorting", SRC)
    assert quote is None and status == "unverified"


def test_paraphrase_is_rejected() -> None:
    quote, status = verify_quote("the network attends over the input tokens", SRC)
    assert quote is None and status == "unverified"


def test_too_short_quote_is_empty() -> None:
    assert verify_quote("model", SRC) == (None, "empty")
    assert verify_quote("", SRC) == (None, "empty")


# --- integration through the ensemble --------------------------------------

_WORK = WorkRecord(id="W1", title="Attention", abstract=SRC)
_PROTOCOL = ReviewProtocol(question="attention models", query_string="attention")


def test_ensemble_keeps_a_verified_quote() -> None:
    pool = mock_pool(
        lambda m, p: '{"verdict":"include","reason":"on topic","quote":"based on self-attention"}',
        screening_models=1,
    )
    outcome = screen_ensemble(_WORK, _PROTOCOL, pool, pool.screening_refs())
    assert outcome.final.verdict is Verdict.INCLUDE
    assert outcome.final.quote == "based on self-attention"


def test_ensemble_drops_a_fabricated_quote_but_keeps_the_verdict() -> None:
    pool = mock_pool(
        lambda m, p: '{"verdict":"include","reason":"on topic","quote":"proves a quantum speedup"}',
        screening_models=1,
    )
    outcome = screen_ensemble(_WORK, _PROTOCOL, pool, pool.screening_refs())
    assert outcome.final.verdict is Verdict.INCLUDE  # decision stands
    assert outcome.final.quote is None  # hallucinated quote is not stored
