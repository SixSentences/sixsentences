"""Pinned benchmark adapters preserve provenance and stage semantics."""

import json
import zipfile
from pathlib import Path

import pytest

from sixsentences_server.evals.quality import GoldVerdict
from sixsentences_server.evals.sources import (
    ReviewCaseSpec,
    SciClaimCaseSpec,
    build_clef_tar_suite,
    build_csmed_ft_suite,
    build_sciclaimeval_suite,
    load_adjudicated_jsonl,
    load_webis_jsonl,
)


def test_webis_references_are_retrieval_gold_not_screening_gold(tmp_path: Path) -> None:
    source = tmp_path / "webis.jsonl"
    source.write_text(
        json.dumps(
            {
                "review_id": "review-1",
                "domain": "medicine",
                "question": "Which interventions work?",
                "query": '"intervention" AND outcome',
                "reference_ids": ["https://openalex.org/W1", "W2", "W1"],
            }
        ),
        encoding="utf-8",
    )

    suite = load_webis_jsonl(
        source,
        suite_id="webis",
        version="2026.1",
        source_uri="https://example.org/pinned-release",
        license_name="CC-BY-4.0",
        license_verified=True,
    )

    assert [work.work_id for work in suite.cases[0].works] == ["W1", "W2"]
    assert all(work.retrieval_relevant for work in suite.cases[0].works)
    assert all(work.screening is None for work in suite.cases[0].works)
    assert all(work.full_text is None for work in suite.cases[0].works)
    assert suite.sources[0].sha256
    assert suite.sources[0].license_verified is True


def test_adjudicated_adapter_loads_explicit_stage_and_claim_gold(tmp_path: Path) -> None:
    source = tmp_path / "adjudicated.jsonl"
    source.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "domain": "computer science",
                "question": "Does the method work?",
                "query": "method AND evaluation",
                "works": [
                    {
                        "work_id": "W1",
                        "retrieval_relevant": True,
                        "screening": "include",
                        "full_text": "include",
                    },
                    {
                        "work_id": "W2",
                        "retrieval_relevant": False,
                        "screening": "exclude",
                        "full_text": "exclude",
                    },
                ],
                "final_relevant_ids": ["W1"],
                "claims": [
                    {
                        "claim_id": "C1",
                        "text": "The intervention improved the primary outcome.",
                        "supported_by": ["W1"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_adjudicated_jsonl(
        source,
        suite_id="internal",
        version="2026.1",
        source_name="two-reviewer adjudication",
        source_uri="urn:sixsentences:evaluation:2026.1",
    )

    assert suite.cases[0].works[0].screening is GoldVerdict.INCLUDE
    assert suite.cases[0].works[1].full_text is GoldVerdict.EXCLUDE
    assert suite.cases[0].claims[0].supported_by == ["W1"]
    assert suite.sources[0].kind == "internal_adjudicated"


def test_adjudicated_claims_require_the_text_being_verified(tmp_path: Path) -> None:
    source = tmp_path / "adjudicated.jsonl"
    source.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "domain": "medicine",
                "question": "Does it work?",
                "query": "intervention",
                "works": [{"work_id": "W1", "retrieval_relevant": True}],
                "claims": [{"claim_id": "C1", "supported_by": ["W1"]}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="text"):
        load_adjudicated_jsonl(
            source,
            suite_id="invalid",
            version="2026.1",
            source_name="adjudication",
            source_uri="urn:test",
        )


def test_csmed_ft_aggregates_companion_reports_without_losing_inclusion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "csmed.zip"
    header = (
        "review_id,document_id,decision,reason_for_exclusion,publication_date,doi,"
        "journal,year,PubMed ID,PDF links,title,authors,abstract,main_text,citation,"
        "main_text_word_count,abstract_word_count,title_word_count\n"
    )
    rows = (
        "R1,D1,excluded,,2020,10.1/one,J,2020,1,,A,,,text,,1,0,1\n"
        "R1,D2,included,,2020,10.1/one,J,2020,1,,A,,,text,,1,0,1\n"
        "R1,D3,excluded,,2020,10.1/two,J,2020,2,,B,,,text,,1,0,1\n"
    )
    with zipfile.ZipFile(source, "w") as archive:
        for split in ("train", "dev", "test", "sample"):
            archive.writestr(
                f"CSMeD-FT/CSMeD-FT-{split}.csv",
                header + (rows if split == "train" else ""),
            )
            archive.writestr(
                f"CSMeD-FT/CSMeD-FT-{split}_reviews_metadata.json",
                json.dumps({"R1": {"title": "A review"}}) if split == "train" else "{}",
            )
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps(
            {
                "works": {
                    "10.1/one": {"id": "https://openalex.org/W1"},
                    "10.1/two": {"id": "https://openalex.org/W2"},
                }
            }
        ),
        encoding="utf-8",
    )

    suite = build_csmed_ft_suite(
        [ReviewCaseSpec(review_id="R1", domain="medicine", question="Q?", query="Q")],
        source_zip=source,
        openalex_map=mapping,
        suite_id="csmed",
        version="1",
    )

    case = suite.cases[0]
    assert len(case.works) == 2
    assert case.works[0].screening is GoldVerdict.INCLUDE
    assert case.works[0].full_text is GoldVerdict.INCLUDE
    assert case.works[1].full_text is GoldVerdict.EXCLUDE
    assert case.final_relevant_ids == ["W1"]


def test_clef_tar_keeps_all_positives_and_samples_negatives_deterministically(
    tmp_path: Path,
) -> None:
    qrels = tmp_path / "qrels.txt"
    qrels.write_text(
        "R1 0 1 1\nR1 0 2 0\nR1 0 3 0\nR1 0 4 0\n",
        encoding="utf-8",
    )
    topic = tmp_path / "R1.topic"
    topic.write_text("Topic: R1\nTitle: Test\n", encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps(
            {
                str(index): {
                    "id": f"https://openalex.org/W{index}",
                    "publication_year": 2020,
                }
                for index in range(1, 5)
            }
        ),
        encoding="utf-8",
    )
    kwargs = {
        "specs": [ReviewCaseSpec(review_id="R1", domain="oncology", question="Q?", query="Q")],
        "qrels": qrels,
        "topic_files": [topic],
        "openalex_map": mapping,
        "suite_id": "clef",
        "version": "1",
        "max_works_per_case": 3,
    }

    first = build_clef_tar_suite(**kwargs)
    second = build_clef_tar_suite(**kwargs)

    assert [work.work_id for work in first.cases[0].works] == [
        work.work_id for work in second.cases[0].works
    ]
    assert "W1" in [work.work_id for work in first.cases[0].works]
    assert first.cases[0].final_relevant_ids == ["W1"]
    assert sum(work.screening is GoldVerdict.EXCLUDE for work in first.cases[0].works) == 2


def test_sciclaimeval_accepts_only_explicit_cc_by_supported_claims(tmp_path: Path) -> None:
    data_root = tmp_path / "dataset"
    paper = data_root / "papers" / "paper.json"
    paper.parent.mkdir(parents=True)
    paper.write_text(json.dumps({"title": "A supported paper"}), encoding="utf-8")
    source = data_root / "task.json"
    source.write_text(
        json.dumps(
            [
                {
                    "paper_id": "P1",
                    "claim_id": "C1",
                    "claim": "The method improved accuracy.",
                    "label": "Supported",
                    "license_name": "CC BY 4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "paper_path": "papers/paper.json",
                },
                {
                    "paper_id": "P1",
                    "claim_id": "C2",
                    "claim": "This claim is refuted.",
                    "label": "Refuted",
                    "license_name": "CC BY 4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "paper_path": "papers/paper.json",
                },
            ]
        ),
        encoding="utf-8",
    )
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps({"P1": {"work": {"id": "https://openalex.org/W1"}}}),
        encoding="utf-8",
    )

    suite = build_sciclaimeval_suite(
        [SciClaimCaseSpec(paper_id="P1", domain="machine_learning")],
        source_json=source,
        dataset_root=data_root,
        openalex_map=mapping,
        suite_id="claims",
        version="1",
    )

    assert len(suite.cases[0].claims) == 1
    assert suite.cases[0].claims[0].text == "The method improved accuracy."
    assert suite.cases[0].claims[0].supported_by == ["W1"]
    assert suite.sources[0].license_verified is True
