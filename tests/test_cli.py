"""The CLI exposes engine functions without starting a service."""

import json
import tomllib
from pathlib import Path

import pytest

from sixsentences import __version__
from sixsentences.cli import main
from sixsentences.core.models import WorkRecord


def test_query_command_compiles_openalex(capsys: object) -> None:
    assert main(["query", "terraform AND security", "--target", "openalex"]) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["query"] == "(terraform AND security)"


def test_query_command_emits_database_translation(capsys: object) -> None:
    assert main(["query", "title:terraform AND security", "--target", "pubmed"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert output.strip() == "(terraform[ti] AND security[tiab])"


def test_query_command_emits_central_translation(capsys: object) -> None:
    assert main(["query", "title:evidence AND screening NOT animal*", "--target", "central"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert output.strip() == "((evidence:ti AND screening:ti,ab,kw) NOT animal*:ti,ab,kw)"


def test_a_query_one_database_cannot_express_still_translates_for_another(
    capsys: object,
) -> None:
    assert main(["query", "Crohn's", "--target", "pubmed"]) == 0
    assert capsys.readouterr().out.strip() == '"Crohn\'s"[tiab]'  # type: ignore[attr-defined]
    assert main(["query", "Crohn's", "--target", "central"]) == 2
    assert "Cochrane CENTRAL" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_corpus_commands_round_trip(tmp_path: Path, capsys: object) -> None:
    input_path = tmp_path / "works.jsonl"
    input_path.write_text(
        json.dumps({"id": "W1", "title": "Systematic evidence synthesis"}) + "\n",
        encoding="utf-8",
    )
    corpus_path = tmp_path / "corpus"

    assert main(["corpus-build", str(input_path), str(corpus_path)]) == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    assert main(["corpus-search", str(corpus_path), "evidence"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert json.loads(output)["id"] == "W1"


def test_coverage_command_does_not_coerce_invalid_counts(tmp_path: Path, capsys: object) -> None:
    for index, invalid in enumerate((True, 1.9, "2")):
        captures = tmp_path / f"captures-{index}.json"
        captures.write_text(json.dumps({"W1": invalid}), encoding="utf-8")
        assert main(["coverage", str(captures), "--occasions", "2"]) == 2
        assert "capture count" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_data_profile_command_emits_reproducible_metadata(tmp_path: Path, capsys: object) -> None:
    dataset = tmp_path / "observations.csv"
    dataset.write_text("group,score\nA,2\nB,4\n", encoding="utf-8")

    assert main(["data-profile", str(dataset)]) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["filename"] == "observations.csv"
    assert output["format"] == "csv"
    assert len(output["sha256"]) == 64
    assert output["profile"]["row_count"] == 2
    assert output["profile"]["columns"][1]["statistics"]["mean"] == 3


def test_data_analyze_command_runs_typed_recipe(tmp_path: Path, capsys: object) -> None:
    dataset = tmp_path / "observations.json"
    dataset.write_text(
        json.dumps([{"x": 1, "y": 2}, {"x": 2, "y": 4}, {"x": 3, "y": None}]),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "data-analyze",
                str(dataset),
                "correlation",
                "--x-column",
                "x",
                "--y-column",
                "y",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["kind"] == "pearson_correlation"
    assert output["n"] == 2
    assert output["incomplete_pairs"] == 1
    assert output["pearson_r"] == 1


def test_data_analyze_command_reports_invalid_numeric_cells(tmp_path: Path, capsys: object) -> None:
    dataset = tmp_path / "observations.csv"
    dataset.write_text("score\nunknown\n", encoding="utf-8")

    assert main(["data-analyze", str(dataset), "descriptive", "--column", "score"]) == 2
    assert "non-missing non-numeric" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_version_flag_answers_before_a_subcommand_is_demanded(capsys: object) -> None:
    """The first thing a bug report asks for has to be reachable without one."""

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"sixsentences {__version__}"  # type: ignore[attr-defined]


def test_version_is_read_from_the_installed_distribution() -> None:
    """A literal in the source is a second place for the version to drift."""

    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]

    assert __version__ == declared


def _fixture_tree(root: Path) -> None:
    """Write one synthetic input of every kind the CLI reads."""

    root.mkdir(parents=True, exist_ok=True)
    (root / "works.jsonl").write_text(
        json.dumps({"id": "W1", "title": "Systematic evidence synthesis", "year": 2020})
        + "\n"
        + json.dumps({"id": "W2", "title": "Evidence screening at scale", "year": 2021})
        + "\n",
        encoding="utf-8",
    )
    (root / "protocol.json").write_text(
        json.dumps({"question": "Does screening scale?", "query_string": "evidence AND screening"}),
        encoding="utf-8",
    )
    (root / "captures.json").write_text(json.dumps({"W1": 2, "W2": 1}), encoding="utf-8")
    (root / "counts.json").write_text(
        json.dumps(
            {
                "records_identified": 10,
                "duplicates_removed": 2,
                "records_screened": 8,
                "records_excluded": 5,
                "records_unsure": 1,
                "included": 2,
                "reports_sought_for_retrieval": 3,
                "reports_not_retrieved": 1,
                "reports_assessed_for_eligibility": 2,
                "reports_excluded_fulltext": 1,
                "reports_included": 1,
                "studies_included": 1,
            }
        ),
        encoding="utf-8",
    )
    (root / "candidates.json").write_text(
        json.dumps(["evidence OR screening", "evidence AND appraisal"]), encoding="utf-8"
    )
    (root / "observations.csv").write_text(
        "group,score,other,effect,se\nA,2,4,0.3,0.1\nA,4,8,0.5,0.2\nB,8,16,0.4,0.15\n",
        encoding="utf-8",
    )


def _deterministic_invocations(root: Path) -> dict[str, list[str]]:
    """Every command whose result is identical on two consecutive runs."""

    _fixture_tree(root)
    corpus = root / "corpus"
    assert main(["corpus-build", str(root / "works.jsonl"), str(corpus)]) == 0
    dataset = str(root / "observations.csv")
    return {
        "query-display": ["query", "evidence AND screening"],
        "query-openalex": ["query", "evidence AND screening", "--target", "openalex"],
        "query-duckdb": ["query", "evidence AND screening", "--target", "duckdb"],
        "query-pubmed": ["query", "title:evidence AND screening", "--target", "pubmed"],
        "corpus-search": ["corpus-search", str(corpus), "evidence"],
        "rank": [
            "rank",
            str(root / "works.jsonl"),
            str(root / "protocol.json"),
            "--now-year",
            "2024",
        ],
        "coverage": ["coverage", str(root / "captures.json"), "--occasions", "2"],
        "prisma-text": ["prisma", str(root / "counts.json")],
        "prisma-svg": ["prisma", str(root / "counts.json"), "--format", "svg"],
        "expansion-validate": ["expansion-validate", str(root / "candidates.json")],
        "data-profile": ["data-profile", dataset],
        "analyze-missingness": ["data-analyze", dataset, "missingness"],
        "analyze-descriptive": ["data-analyze", dataset, "descriptive", "--column", "score"],
        "analyze-group-summary": [
            "data-analyze",
            dataset,
            "group-summary",
            "--group-by",
            "group",
            "--value-column",
            "score",
        ],
        "analyze-correlation": [
            "data-analyze",
            dataset,
            "correlation",
            "--x-column",
            "score",
            "--y-column",
            "other",
        ],
        "analyze-meta-analysis": [
            "data-analyze",
            dataset,
            "meta-analysis",
            "--effect-column",
            "effect",
            "--se-column",
            "se",
        ],
    }


# Named here rather than derived, so parametrization needs no filesystem at
# collection time. `test_every_deterministic_command_is_parametrized` keeps the
# two lists in step.
_DETERMINISTIC = [
    "query-display",
    "query-openalex",
    "query-duckdb",
    "query-pubmed",
    "corpus-search",
    "rank",
    "coverage",
    "prisma-text",
    "prisma-svg",
    "expansion-validate",
    "data-profile",
    "analyze-missingness",
    "analyze-descriptive",
    "analyze-group-summary",
    "analyze-correlation",
    "analyze-meta-analysis",
]


def test_every_deterministic_command_is_parametrized(tmp_path: Path) -> None:
    """A command added to the fixture must not quietly go untested."""

    assert sorted(_deterministic_invocations(tmp_path)) == sorted(_DETERMINISTIC)


@pytest.mark.parametrize("command", _DETERMINISTIC)
def test_output_writes_exactly_what_stdout_would_have_received(
    command: str, tmp_path: Path, capsys: object
) -> None:
    """Redirection and --output must never disagree about the result."""

    argv = _deterministic_invocations(tmp_path)[command]
    capsys.readouterr()  # type: ignore[attr-defined]

    assert main(argv) == 0
    printed = capsys.readouterr().out  # type: ignore[attr-defined]

    destination = tmp_path / "result.out"
    assert main([*argv, "--output", str(destination)]) == 0
    written = capsys.readouterr()  # type: ignore[attr-defined]

    assert destination.read_text(encoding="utf-8") == printed
    assert written.out == ""
    assert written.err == ""


def test_openalex_search_writes_its_records(
    tmp_path: Path, capsys: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one networked command uses the same writer, against a stub client."""

    class StubClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def search(self, _query: str, **_kwargs: object) -> list[WorkRecord]:
            return [WorkRecord(id="W1", title="Evidence synthesis")]

        def close(self) -> None:
            pass

    monkeypatch.setattr("sixsentences.cli.OpenAlexClient", StubClient)
    destination = tmp_path / "records.jsonl"

    assert main(["openalex-search", "evidence", "--output", str(destination)]) == 0

    assert capsys.readouterr().out == ""  # type: ignore[attr-defined]
    assert json.loads(destination.read_text(encoding="utf-8"))["id"] == "W1"


def test_corpus_build_writes_its_manifest(tmp_path: Path, capsys: object) -> None:
    """Byte parity cannot be asserted here: the manifest carries a build time."""

    _fixture_tree(tmp_path)
    destination = tmp_path / "manifest.json"

    assert (
        main(
            [
                "corpus-build",
                str(tmp_path / "works.jsonl"),
                str(tmp_path / "corpus"),
                "--output",
                str(destination),
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == ""  # type: ignore[attr-defined]
    manifest = json.loads(destination.read_text(encoding="utf-8"))
    assert manifest["works"] == 2
    assert manifest["source"] == "local-jsonl"


def test_output_is_accepted_before_and_after_an_analysis_recipe(
    tmp_path: Path, capsys: object
) -> None:
    """A nested parser must not overwrite a value the parent already read."""

    _fixture_tree(tmp_path)
    dataset = str(tmp_path / "observations.csv")
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"

    assert main(["data-analyze", dataset, "--output", str(before), "missingness"]) == 0
    assert main(["data-analyze", dataset, "missingness", "--output", str(after)]) == 0

    assert capsys.readouterr().out == ""  # type: ignore[attr-defined]
    assert before.read_text(encoding="utf-8") == after.read_text(encoding="utf-8")


def test_a_failing_command_writes_no_file(tmp_path: Path, capsys: object) -> None:
    """A partial result is worse than none; the path is touched only on success."""

    _fixture_tree(tmp_path)
    destination = tmp_path / "never-written.json"
    (tmp_path / "observations.csv").write_text("score\nunknown\n", encoding="utf-8")

    assert (
        main(
            [
                "data-analyze",
                str(tmp_path / "observations.csv"),
                "descriptive",
                "--column",
                "score",
                "--output",
                str(destination),
            ]
        )
        == 2
    )

    assert not destination.exists()
    assert "non-missing non-numeric" in capsys.readouterr().err  # type: ignore[attr-defined]
