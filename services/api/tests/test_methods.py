"""Citable methods paragraph — real numbers, PRISMA-conformant, reproducible."""

from sixsentences_server.reporting.methods import MethodsFacts, render_methods_paragraph


def _rich_facts() -> MethodsFacts:
    return MethodsFacts(
        date="2026-07-14",
        corpus_version="micro-2026-07-14-openalex-50000",
        synthesized_by="deepseek:deepseek-v4-pro",
        query_string="(llm OR agent) AND (reasoning OR planning)",
        queries_executed=16,
        stopped_because="saturated",
        identified=599,
        duplicates=317,
        screened=15,
        included=9,
        excluded=0,
        unsure=6,
        ensemble_size=2,
        adjudicated=1,
        quotes_verified=9,
        coverage_method="chao2",
        coverage_completeness=0.807,
        coverage_ci_low=0.723,
        coverage_ci_high=0.87,
        recall_method="chao2",
        recall_value=1.0,
        recall_ci_low=1.0,
        recall_ci_high=1.0,
        recall_certified=False,
        retracted_flagged=0,
        cites_retracted=0,
        models=["deepseek:deepseek-v4-flash", "deepseek:deepseek-v4-pro"],
    )


def test_paragraph_carries_the_real_numbers() -> None:
    text = render_methods_paragraph(_rich_facts())
    assert "micro-2026-07-14-openalex-50000" in text
    assert "599 records" in text and "317 duplicates" in text
    assert "282 unique" in text  # 599 - 317, computed not stored
    assert "16 queries executed" in text
    assert "2 independent model reviewers" in text
    assert "9 were included" in text
    assert "80.7%" in text  # coverage completeness
    assert "not certified" in text  # recall not certified
    assert "PRISMA 2020" in text
    assert "deepseek" not in text.lower()
    assert "openrouter" not in text.lower()


def test_paragraph_degrades_gracefully_without_screening_or_stats() -> None:
    text = render_methods_paragraph(
        MethodsFacts(identified=42, duplicates=2, corpus_version="micro-x")
    )
    assert "40 unique" in text
    assert "were included" not in text  # no screening sentence
    assert "capture-recapture" not in text  # no coverage/recall sentence
    assert "PRISMA 2020" in text
    assert "Automation tool SixSentences_" in text
    assert "model" not in text.lower()
