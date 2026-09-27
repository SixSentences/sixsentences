"""Operator-only Jev evaluation artifacts and CLI safety gates."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from sixsentences_server.cli import app
from sixsentences_server.core.models import ReviewProtocol, WorkRecord
from sixsentences_server.evals.jev_shadow import (
    JevShadowDataset,
    JevShadowDatasetMetadata,
    JevShadowDatasetRecord,
    JevShadowGoldVerdict,
    JevShadowProtocol,
    conservative_jev_reservation_usd,
    load_jev_shadow_dataset,
    run_jev_shadow_evaluation,
    write_jev_shadow_report,
)
from sixsentences_server.screening.jev import (
    JEV_MODEL,
    JevAdvisorySignal,
    JevCriterionKind,
    JevCriterionResult,
    JevShadowResult,
    JevUsage,
)


def _dataset(records: tuple[JevShadowDatasetRecord, ...] | None = None) -> JevShadowDataset:
    return JevShadowDataset(
        metadata=JevShadowDatasetMetadata(
            dataset_id="public-screening-benchmark",
            version="2026.1",
            source_uri="urn:test:public-screening-benchmark:2026.1",
            license="CC0-1.0",
            license_verified=True,
        ),
        protocol=JevShadowProtocol(
            question="UNIQUE RAW LOCAL QUESTION",
            inclusion_criteria=("PRIVATE RAW INCLUSION CRITERION",),
            exclusion_criteria=("PRIVATE RAW EXCLUSION CRITERION",),
            query_string="PRIVATE RAW BOOLEAN QUERY",
        ),
        records=records
        or (
            JevShadowDatasetRecord(
                record_id="local-include-id",
                title="UNIQUE RAW INCLUDE TITLE",
                abstract="UNIQUE RAW INCLUDE ABSTRACT",
                domain="medicine",
                gold=JevShadowGoldVerdict.INCLUDE,
            ),
            JevShadowDatasetRecord(
                record_id="local-exclude-id",
                title="UNIQUE RAW EXCLUDE TITLE",
                abstract="UNIQUE RAW EXCLUDE ABSTRACT",
                domain="medicine",
                gold=JevShadowGoldVerdict.EXCLUDE,
            ),
        ),
    )


def _shadow_result(
    signal: JevAdvisorySignal,
    minimum_confidence: float,
    *,
    input_tokens: int = 100,
) -> JevShadowResult:
    choice = "met" if signal is JevAdvisorySignal.INCLUDE else "not_met"
    return JevShadowResult(
        signal=signal,
        model=JEV_MODEL,
        minimum_confidence=minimum_confidence,
        criteria=(
            JevCriterionResult(
                question_id="inclusion_0000",
                criterion_kind=JevCriterionKind.INCLUSION,
                criterion_index=0,
                criterion_sha256="c" * 64,
                choice=choice,
                probabilities={
                    "met": 0.99 if choice == "met" else 0.005,
                    "not_met": 0.99 if choice == "not_met" else 0.005,
                    "unclear": 0.005,
                },
                confidence=0.99,
            ),
        ),
        input_sha256="a" * 64,
        schema_sha256="b" * 64,
        usage=JevUsage(input_tokens=input_tokens, output_tokens=3),
        request_id="jev-test-request",
        latency_ms=4,
        retries=0,
    )


class _FakeJevClient:
    def __init__(self) -> None:
        self.works: list[WorkRecord] = []
        self.protocols: list[ReviewProtocol] = []
        self.confirmations: list[bool] = []

    def evaluate(
        self,
        work: WorkRecord,
        protocol: ReviewProtocol,
        *,
        public_bibliographic_data_confirmed: bool,
        minimum_confidence: float,
    ) -> JevShadowResult:
        self.works.append(work)
        self.protocols.append(protocol)
        self.confirmations.append(public_bibliographic_data_confirmed)
        signal = (
            JevAdvisorySignal.INCLUDE
            if "include" in work.title.casefold()
            else JevAdvisorySignal.EXCLUDE
        )
        return _shadow_result(signal, minimum_confidence)


def _write_dataset(path: Path, dataset: JevShadowDataset | None = None) -> None:
    path.write_text((dataset or _dataset()).model_dump_json(indent=2), encoding="utf-8")


def test_dataset_schema_rejects_identifying_or_unknown_provider_fields(tmp_path: Path) -> None:
    payload = json.loads(_dataset().model_dump_json())
    payload["records"][0]["authors"] = ["Must not enter the provider fixture"]
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="authors"):
        load_jev_shadow_dataset(source)


def test_runner_keeps_raw_content_out_of_report_and_never_passes_a_prefix() -> None:
    client = _FakeJevClient()
    dataset = _dataset()

    report = run_jev_shadow_evaluation(
        dataset,
        client=client,
        git_revision="d" * 40,
        budget_usd=Decimal("1"),
        limit=1,
        minimum_confidence=0.8,
    )

    assert len(client.works) == 1
    assert client.works[0].id == "jev-shadow-public-record"
    assert client.works[0].authors == []
    assert client.works[0].doi is None
    assert client.confirmations == [True]
    assert report.advisory_only is True
    assert report.authoritative is False
    assert report.passed is False
    assert report.gate_evaluation_complete is False
    assert any("bounded prefix" in blocker for blocker in report.gate_blockers)
    assert report.usage.requests == 1
    assert report.usage.input_tokens == 100
    assert report.usage.estimated_input_cost_usd == pytest.approx(0.0000042)
    assert report.conservative_reserved_usd == pytest.approx(0.002688)
    assert report.usage.provider_invoice is False

    serialized = report.model_dump_json()
    for raw_text in (
        "UNIQUE RAW INCLUDE TITLE",
        "UNIQUE RAW INCLUDE ABSTRACT",
        "UNIQUE RAW LOCAL QUESTION",
        "PRIVATE RAW INCLUSION CRITERION",
        "PRIVATE RAW EXCLUSION CRITERION",
        "PRIVATE RAW BOOLEAN QUERY",
    ):
        assert raw_text not in serialized


def test_budget_is_checked_before_any_evaluation_call() -> None:
    client = _FakeJevClient()

    with pytest.raises(ValueError, match="64k-context reservation"):
        run_jev_shadow_evaluation(
            _dataset(),
            client=client,
            git_revision="d" * 40,
            budget_usd=Decimal("0.001"),
            limit=1,
            minimum_confidence=0.8,
        )

    assert client.works == []


def test_complete_multidomain_gold_can_evaluate_existing_screening_gates() -> None:
    domains = ("health", "physical", "social")
    records = tuple(
        JevShadowDatasetRecord(
            record_id=f"record-{index}",
            title=("include" if index < 100 else "exclude") + f" title {index}",
            abstract=f"public abstract {index}",
            domain=domains[index % len(domains)],
            gold=(
                JevShadowGoldVerdict.INCLUDE
                if index < 100
                else JevShadowGoldVerdict.EXCLUDE
            ),
        )
        for index in range(500)
    )
    report = run_jev_shadow_evaluation(
        _dataset(records),
        client=_FakeJevClient(),
        git_revision="e" * 40,
        budget_usd=Decimal("2"),
        limit=500,
        minimum_confidence=0.8,
    )

    assert report.gate_evaluation_complete is True
    assert report.gate_blockers == ()
    assert report.passed is True
    assert report.metrics is not None
    assert report.metrics.sensitivity == 1.0
    assert report.metrics.specificity == 1.0
    assert len(report.by_domain) == 3
    assert all(gate.passed for gate in report.gates)


def test_report_writer_is_create_only(tmp_path: Path) -> None:
    report = run_jev_shadow_evaluation(
        _dataset(),
        client=_FakeJevClient(),
        git_revision="f" * 40,
        budget_usd=Decimal("1"),
        limit=2,
        minimum_confidence=0.8,
    )
    output = tmp_path / "report.json"

    write_jev_shadow_report(output, report)
    with pytest.raises(FileExistsError):
        write_jev_shadow_report(output, report)


def test_cli_requires_both_explicit_egress_confirmations_before_client(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset.json"
    _write_dataset(source)

    with patch("sixsentences_server.screening.jev.JevClient") as client_type:
        result = CliRunner().invoke(
            app,
            [
                "quality",
                "jev-shadow",
                "--dataset",
                str(source),
                "--out",
                str(tmp_path / "report.json"),
                "--budget-usd",
                "1",
                "--limit",
                "2",
                "--confirm-provider-spend",
            ],
        )

    assert result.exit_code == 2
    assert "--confirm-public-bibliographic-data" in result.output
    client_type.assert_not_called()


def test_cli_runs_with_fake_client_and_attested_revision(
    settings,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    settings.typesafe_api_key = "test-only-typesafe-key"
    settings.release_git_revision = "a" * 40
    source = tmp_path / "dataset.json"
    output = tmp_path / "report.json"
    _write_dataset(source)
    client = _FakeJevClient()

    with patch("sixsentences_server.screening.jev.JevClient", return_value=client) as client_type:
        result = CliRunner().invoke(
            app,
            [
                "quality",
                "jev-shadow",
                "--dataset",
                str(source),
                "--out",
                str(output),
                "--budget-usd",
                "1",
                "--limit",
                "2",
                "--minimum-confidence",
                "0.8",
                "--confirm-provider-spend",
                "--confirm-public-bibliographic-data",
                "--no-fail-on-gate",
            ],
        )

    assert result.exit_code == 0, result.output
    client_type.assert_called_once_with("test-only-typesafe-key", max_retries=0)
    assert len(client.works) == 2
    assert "No screening decisions changed" in result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["git_revision"] == "a" * 40
    assert payload["model"] == JEV_MODEL
    assert payload["advisory_only"] is True
    assert payload["authoritative"] is False
    assert payload["passed"] is False


def test_cli_rejects_budget_before_reading_key_or_constructing_client(
    settings,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    settings.typesafe_api_key = "test-only-typesafe-key"
    settings.release_git_revision = "a" * 40
    source = tmp_path / "dataset.json"
    _write_dataset(source)

    with patch("sixsentences_server.screening.jev.JevClient") as client_type:
        result = CliRunner().invoke(
            app,
            [
                "quality",
                "jev-shadow",
                "--dataset",
                str(source),
                "--out",
                str(tmp_path / "report.json"),
                "--budget-usd",
                "0.001",
                "--limit",
                "1",
                "--confirm-provider-spend",
                "--confirm-public-bibliographic-data",
            ],
        )

    assert result.exit_code == 2
    assert "64k-context reservation" in result.output
    client_type.assert_not_called()


def test_reservation_uses_full_published_context_price() -> None:
    assert conservative_jev_reservation_usd(1) == Decimal("0.002688")
    assert conservative_jev_reservation_usd(10) == Decimal("0.026880")
