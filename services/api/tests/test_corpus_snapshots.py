"""Corpus refreshes publish only complete, queryable generations."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from sixsentences_server.connectors.openalex import OpenAlexClient
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import CorpusManifestError, DuckDBCorpus
from sixsentences_server.corpus.ingest import (
    MIN_ESTIMATED_BYTES_PER_WORK,
    SNAPSHOT_BUILD_OVERHEAD,
    CorpusSyncError,
    estimate_snapshot_bytes,
    sync_corpus,
    sync_micro_corpus,
)
from sixsentences_server.corpus.manifest import (
    CorpusBuildPlan,
    CorpusQualityGates,
    CorpusSlice,
    production_corpus_plan,
)


def _work(number: int) -> WorkRecord:
    return WorkRecord(
        id=f"W{number}",
        title=f"Snapshot work {number}",
        abstract="A stable corpus record.",
        year=2026,
        authors=["Researcher"],
        cited_by_count=number,
        referenced_works=[],
        work_type="article",
    )


class StubOpenAlex(OpenAlexClient):
    def __init__(
        self,
        works: list[WorkRecord],
        *,
        fail_after: int | None = None,
    ) -> None:
        self.works = works
        self.fail_after = fail_after

    def iter_works(
        self,
        oa_filter: str,
        *,
        limit: int,
        sort: str = "cited_by_count:desc",
    ) -> Iterator[WorkRecord]:
        del oa_filter, sort
        for index, work in enumerate(self.works[:limit], start=1):
            yield work
            if self.fail_after == index:
                raise RuntimeError("simulated interrupted OpenAlex stream")


def test_sync_streams_and_atomically_rotates_snapshots(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")

    assert sync_micro_corpus(corpus, StubOpenAlex([_work(1)])).works == 1
    first_path = corpus.works_path
    assert corpus.count() == 1

    assert sync_micro_corpus(corpus, StubOpenAlex([_work(1), _work(2)])).works == 2
    assert corpus.count() == 2
    assert corpus.works_path != first_path

    assert (
        sync_micro_corpus(
            corpus,
            StubOpenAlex([_work(1), _work(2), _work(3)]),
            snapshots_to_keep=2,
        ).works
        == 3
    )
    assert corpus.count() == 3
    assert corpus.info()["snapshots"] == 2
    assert corpus.info()["bytes"] > 0
    assert len(corpus.info()["sha256"]) == 64
    snapshots = [path for path in corpus.snapshots_dir.iterdir() if not path.name.startswith(".")]
    assert len(snapshots) == 2
    assert not first_path.exists()


def test_pinned_generation_and_exact_coverage_ignore_active_pointer(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    first = sync_micro_corpus(corpus, StubOpenAlex([_work(1), _work(2)]))
    second = sync_micro_corpus(corpus, StubOpenAlex([_work(2), _work(3)]))

    assert corpus.works_path.parent.name == second.generation
    pinned = DuckDBCorpus(corpus.corpus_dir, generation=first.generation)

    assert pinned.version().version == first.version
    assert pinned.present_work_ids(["W3", "W1", "W2", "W1"]) == ["W1", "W2"]
    assert corpus.present_work_ids(["W1", "W2", "W3"]) == ["W2", "W3"]


def test_interrupted_sync_keeps_previous_generation(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    sync_micro_corpus(corpus, StubOpenAlex([_work(1), _work(2)]))
    active_path = corpus.works_path
    active_version = corpus.version()

    with pytest.raises(RuntimeError, match="interrupted"):
        sync_micro_corpus(
            corpus,
            StubOpenAlex([_work(3)], fail_after=1),
        )

    assert corpus.works_path == active_path
    assert corpus.version() == active_version
    assert corpus.count() == 2
    assert not any(path.name.endswith(".partial") for path in corpus.snapshots_dir.iterdir())


def test_release_gates_reject_incomplete_generation_before_activation(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    sync_micro_corpus(corpus, StubOpenAlex([_work(1), _work(2)]))
    active_path = corpus.works_path
    plan = CorpusBuildPlan(
        profile="release-test",
        from_year=2015,
        slices=(
            CorpusSlice(
                id="domain-one",
                label="Domain one",
                oa_filter="has_abstract:true",
                target_works=10,
            ),
        ),
        quality_gates=CorpusQualityGates(minimum_target_ratio=0.9),
    )

    with pytest.raises(CorpusSyncError, match="target_works"):
        sync_corpus(corpus, StubOpenAlex([_work(3), _work(4)]), plan=plan)

    assert corpus.works_path == active_path
    assert corpus.count() == 2


def test_release_gates_reject_records_outside_the_slice_years(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    plan = CorpusBuildPlan(
        profile="production-test",
        from_year=2020,
        to_year=2026,
        slices=(
            CorpusSlice(
                id="recent",
                label="Recent",
                oa_filter="has_abstract:true",
                target_works=1,
                from_year=2020,
                to_year=2026,
            ),
        ),
    )

    with pytest.raises(CorpusSyncError, match="publication_years"):
        sync_corpus(corpus, StubOpenAlex([_work(1).model_copy(update={"year": 2019})]), plan=plan)


def test_verify_and_rollback_use_hash_checked_immutable_manifests(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    sync_micro_corpus(corpus, StubOpenAlex([_work(1)]))
    first_generation = corpus.works_path.parent.name
    sync_micro_corpus(corpus, StubOpenAlex([_work(1), _work(2)]))

    assert corpus.verify()["works"] == 2
    corpus.activate_generation(first_generation)
    assert corpus.verify()["works"] == 1

    with corpus.works_path.open("ab") as handle:
        handle.write(b"corrupt")
    with pytest.raises(CorpusManifestError, match="byte size"):
        corpus.verify()


def test_cross_slice_duplicates_are_rejected_by_uniqueness_gate(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    plan = CorpusBuildPlan(
        profile="duplicate-test",
        from_year=2015,
        slices=(
            CorpusSlice(id="one", label="One", oa_filter="slice:one", target_works=1),
            CorpusSlice(id="two", label="Two", oa_filter="slice:two", target_works=1),
        ),
    )

    with pytest.raises(CorpusSyncError, match="unique_ids"):
        sync_corpus(corpus, StubOpenAlex([_work(1)]), plan=plan)


def test_staged_generation_does_not_change_the_active_pointer(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    first = sync_micro_corpus(corpus, StubOpenAlex([_work(1)]))
    active_path = corpus.works_path

    staged = sync_micro_corpus(
        corpus,
        StubOpenAlex([_work(1), _work(2)]),
        activate=False,
    )

    assert first.activated is True
    assert staged.activated is False
    assert corpus.works_path == active_path
    assert corpus.count() == 1
    snapshots = {item["generation"]: item for item in corpus.list_snapshots()}
    assert snapshots[staged.generation]["active"] is False
    assert snapshots[staged.generation]["works"] == 2


def test_production_plan_is_balanced_across_domains_and_time_bands() -> None:
    plan = production_corpus_plan(target_works=1_000_003, from_year=2015, to_year=2026)

    assert plan.profile == "production-multidomain-v2"
    assert plan.target_works == 1_000_003
    assert len(plan.slices) == 12
    assert {item.from_year for item in plan.slices} == {2015, 2019, 2023}
    assert {item.to_year for item in plan.slices} == {2018, 2022, 2026}
    assert (
        max(item.target_works for item in plan.slices)
        - min(item.target_works for item in plan.slices)
        <= 1
    )
    assert len(plan.digest) == 64


def test_snapshot_storage_forecast_is_conservative_without_a_baseline(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")

    assert estimate_snapshot_bytes(corpus, 1_000) == int(
        1_000 * MIN_ESTIMATED_BYTES_PER_WORK * SNAPSHOT_BUILD_OVERHEAD
    )


def test_build_plan_rejects_overlapping_or_out_of_range_slices() -> None:
    with pytest.raises(ValueError, match="overlapping year bands"):
        CorpusBuildPlan(
            profile="production-test",
            from_year=2020,
            to_year=2026,
            slices=(
                CorpusSlice(
                    id="first-band",
                    label="First band",
                    oa_filter="has_abstract:true",
                    target_works=10,
                    from_year=2020,
                    to_year=2023,
                ),
                CorpusSlice(
                    id="second-band",
                    label="Second band",
                    oa_filter="has_abstract:true",
                    target_works=10,
                    from_year=2023,
                    to_year=2026,
                ),
            ),
        )

    with pytest.raises(ValueError, match="outside the plan years"):
        CorpusBuildPlan(
            profile="production-test",
            from_year=2020,
            to_year=2026,
            slices=(
                CorpusSlice(
                    id="old-band",
                    label="Old band",
                    oa_filter="has_abstract:true",
                    target_works=10,
                    from_year=2019,
                    to_year=2021,
                ),
            ),
        )


def test_slice_rejects_hidden_publication_date_filters() -> None:
    with pytest.raises(ValueError, match="year fields"):
        CorpusSlice(
            id="hidden-date",
            label="Hidden date",
            oa_filter="has_abstract:true,from_publication_date:2020-01-01",
            target_works=10,
        )


def test_unapproved_production_snapshot_cannot_be_rollback_target(tmp_path: Path) -> None:
    corpus = DuckDBCorpus(tmp_path / "corpus")
    staged = sync_corpus(
        corpus,
        StubOpenAlex([_work(1)]),
        plan=CorpusBuildPlan(
            profile="production-test",
            from_year=2026,
            to_year=2026,
            slices=(
                CorpusSlice(
                    id="test-slice",
                    label="Test slice",
                    oa_filter="has_abstract:true",
                    target_works=1,
                ),
            ),
        ),
        activate=False,
    )

    with pytest.raises(CorpusManifestError, match="release evidence is invalid"):
        corpus.activate_generation(
            staged.generation,
            require_release_evidence=True,
        )

    assert not corpus.current_path.exists()
