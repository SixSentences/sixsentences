"""The CLI exposes engine functions without starting a service."""

import json
from pathlib import Path

from sixsentences.cli import main


def test_query_command_compiles_openalex(capsys: object) -> None:
    assert main(["query", "terraform AND security", "--target", "openalex"]) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["query"] == "(terraform AND security)"


def test_query_command_emits_database_translation(capsys: object) -> None:
    assert main(["query", "title:terraform AND security", "--target", "pubmed"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert output.strip() == "(terraform[ti] AND security[tiab])"


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
