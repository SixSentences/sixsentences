"""The release runner executes real pipeline stages without leaking gold labels."""

from datetime import UTC, datetime

import pytest

from sixsentences_server.core.db import Run, db_session, init_db
from sixsentences_server.evals.predict import (
    PREDICTION_SHARD_RUNNER,
    PredictionRunError,
    QualityPredictionConfig,
    _claim_excerpt,
    generate_predictions,
    merge_prediction_shards,
    partition_suite_cases,
    write_predictions,
)
from sixsentences_server.evals.quality import (
    CasePrediction,
    GoldClaim,
    GoldenCase,
    GoldenSuite,
    GoldWork,
    PredictionRun,
    SuiteSource,
)
from sixsentences_server.llm.mock import mock_pool


def _suite() -> GoldenSuite:
    return GoldenSuite(
        suite_id="runner-test",
        version="1",
        title="Prediction runner isolation",
        created_at=datetime(2026, 8, 6, tzinfo=UTC),
        sources=[
            SuiteSource(
                kind="test_fixture",
                name="fixture",
                version="1",
                uri="urn:test:prediction-runner",
                license="test-only",
                license_verified=True,
                sha256="a" * 64,
            )
        ],
        cases=[
            GoldenCase(
                case_id="case-1",
                source_name="fixture",
                domain="computer science",
                question="How do transformer architectures work?",
                query="transformer",
                works=[GoldWork(work_id="W-GOLD-SECRET", retrieval_relevant=True)],
                final_relevant_ids=["W-GOLD-SECRET"],
                claims=[
                    GoldClaim(
                        claim_id="claim-1",
                        text="Transformers are based on self-attention.",
                        supported_by=["W-GOLD-SECRET"],
                    )
                ],
            )
        ],
    )


def test_prediction_runner_executes_pipeline_without_gold_label_leakage(
    corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_db()

    def handler(_model: str, prompt: str) -> str:
        if "Existing queries:" in prompt:
            return '{"queries":[]}'
        if "Claim ID:" in prompt:
            return '{"supported_by":["W1"],"reason":"The abstract directly supports it."}'
        return (
            '{"verdict":"include","reason":"Topically eligible.","quote":"based on self-attention"}'
        )

    pool = mock_pool(handler, screening_models=1)
    original_info = corpus.info

    def immutable_info():
        return {
            **original_info(),
            "sha256": "b" * 64,
            "build_digest": "c" * 64,
        }

    monkeypatch.setattr(corpus, "info", immutable_info)
    with db_session() as session:
        prediction_run = generate_predictions(
            session,
            _suite(),
            corpus=corpus,
            pool=pool,
            config=QualityPredictionConfig(
                exhaustive=True,
                acquire=False,
                full_text_screen=False,
                retrieval_limit=100,
                screen_limit=25,
            ),
        )
        persisted = session.query(Run).one()

    prediction = prediction_run.predictions[0]
    assert persisted.status == "completed"
    assert persisted.config["release_evaluation"] is True
    assert persisted.config["evaluation_controls"]["screen_limit"] == 25
    assert prediction.retrieved_ids == ["W1"]
    assert prediction.screening["W1"] == "include"
    assert prediction.corpus_work_ids == []
    assert prediction.claims[0].cited_work_ids == ["W1"]
    assert prediction_run.generated_by == "sixsentences_server.release-eval"
    assert prediction_run.seed == 42
    assert all("W-GOLD-SECRET" not in prompt for _, prompt in pool.clients["mock"].calls)


def test_full_text_prediction_requires_acquisition() -> None:
    with pytest.raises(ValueError, match="requires acquisition"):
        QualityPredictionConfig(acquire=False, full_text_screen=True)


def test_prediction_screen_limit_is_positive_and_attested() -> None:
    controls = QualityPredictionConfig(
        acquire=False,
        full_text_screen=False,
        screen_limit=750,
    )

    assert controls.screen_limit == 750
    assert controls.model_dump(mode="json")["screen_limit"] == 750
    with pytest.raises(ValueError):
        QualityPredictionConfig(
            acquire=False,
            full_text_screen=False,
            screen_limit=0,
        )


def test_claim_excerpt_prefers_relevant_later_passage() -> None:
    text = (
        "This opening sentence is generic background with no useful mechanism. "
        "Another unrelated sentence discusses deployment logistics. "
        "Transformers use self-attention to connect tokens across a sequence."
    )

    excerpt = _claim_excerpt(
        text,
        "Transformers are based on self-attention.",
        limit=75,
    )

    assert "self-attention" in excerpt
    assert len(excerpt) <= 75


def test_prediction_artifact_is_never_overwritten(tmp_path) -> None:
    target = tmp_path / "predictions.json"
    target.write_text("original", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_predictions(target, object())  # type: ignore[arg-type]

    assert target.read_text(encoding="utf-8") == "original"


def _sharded_suite() -> GoldenSuite:
    base = _suite()
    cases = []
    for index, work_count in enumerate((8, 4, 2, 1), start=1):
        cases.append(
            base.cases[0].model_copy(
                update={
                    "case_id": f"case-{index}",
                    "works": [
                        GoldWork(work_id=f"W-{index}-{work}", retrieval_relevant=True)
                        for work in range(work_count)
                    ],
                    "final_relevant_ids": [f"W-{index}-0"],
                    "claims": [],
                }
            )
        )
    return base.model_copy(update={"cases": cases})


def _prediction_shard(suite: GoldenSuite, case_ids: list[str]) -> PredictionRun:
    return PredictionRun(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_digest=suite.digest,
        created_at=datetime(2026, 8, 9, tzinfo=UTC),
        generated_by=PREDICTION_SHARD_RUNNER,
        git_revision="1234567",
        corpus_version="corpus-v1",
        corpus_sha256="b" * 64,
        corpus_build_digest="c" * 64,
        config_digest="d" * 64,
        predictions=[CasePrediction(case_id=case_id) for case_id in case_ids],
    )


def test_suite_partition_is_balanced_complete_and_deterministic() -> None:
    suite = _sharded_suite()

    first = partition_suite_cases(suite, 2)
    second = partition_suite_cases(suite, 2)

    assert first == second
    assert sorted(case_id for shard in first for case_id in shard) == [
        "case-1",
        "case-2",
        "case-3",
        "case-4",
    ]
    assert all(shard for shard in first)


def test_prediction_shards_merge_only_when_complete_and_matching() -> None:
    suite = _sharded_suite()
    partitions = partition_suite_cases(suite, 2)

    merged = merge_prediction_shards(
        suite,
        [_prediction_shard(suite, case_ids) for case_ids in partitions],
    )

    assert merged.generated_by == "sixsentences_server.release-eval"
    assert [prediction.case_id for prediction in merged.predictions] == [
        "case-1",
        "case-2",
        "case-3",
        "case-4",
    ]

    with pytest.raises(PredictionRunError, match="missing"):
        merge_prediction_shards(suite, [_prediction_shard(suite, ["case-1"])])

    duplicate = _prediction_shard(suite, ["case-1"])
    with pytest.raises(PredictionRunError, match="duplicate"):
        merge_prediction_shards(
            suite,
            [
                _prediction_shard(suite, ["case-1", "case-2", "case-3", "case-4"]),
                duplicate,
            ],
        )

    mismatched = _prediction_shard(suite, partitions[1]).model_copy(
        update={"corpus_version": "other-corpus"}
    )
    with pytest.raises(PredictionRunError, match="metadata"):
        merge_prediction_shards(
            suite,
            [_prediction_shard(suite, partitions[0]), mismatched],
        )
