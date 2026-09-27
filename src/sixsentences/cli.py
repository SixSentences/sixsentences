"""Command-line interface for the portable SixSentences toolkit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from sixsentences import __version__
from sixsentences.connectors.openalex import OpenAlexClient, OpenAlexError
from sixsentences.core.models import PrismaCounts, ReviewProtocol, WorkRecord
from sixsentences.corpus.local import CorpusError, LocalCorpus, build_local_corpus
from sixsentences.coverage.estimator import estimate_completeness
from sixsentences.data import (
    AnalysisRecipe,
    DescriptiveRecipe,
    GroupSummaryRecipe,
    MissingnessRecipe,
    PearsonCorrelationRecipe,
    RandomEffectsMetaAnalysisRecipe,
    analyze,
    parse_dataset_file,
)
from sixsentences.pipeline.expansion import validate_variants
from sixsentences.querylang.ast import to_display
from sixsentences.querylang.compile_duckdb import compile_duckdb
from sixsentences.querylang.compile_openalex import compile_openalex
from sixsentences.querylang.parser import QueryParseError, parse_query
from sixsentences.querylang.translate import translate
from sixsentences.ranking.scorer import rank_works
from sixsentences.reporting.prisma import render_flow_svg, render_flow_text


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[WorkRecord]:
    records: list[WorkRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(WorkRecord.model_validate_json(line))
            except ValidationError as exc:
                raise ValueError(f"invalid work on line {line_number} of {path}") from exc
    return records


def _json_text(value: object) -> str:
    if hasattr(value, "model_dump_json"):
        return cast(str, value.model_dump_json(indent=2)) + "\n"
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _jsonl_text(records: Iterable[BaseModel]) -> str:
    return "".join(record.model_dump_json() + "\n" for record in records)


def _emit(args: argparse.Namespace, text: str) -> None:
    """Write one complete result to `--output`, or to stdout.

    A file receives exactly the bytes stdout would have received, so redirection
    and `--output` cannot disagree about a trailing newline. The payload is
    built in full before the path is touched, so a command that fails partway
    leaves no half-written artifact behind.
    """

    destination: Path | None = getattr(args, "output", None)
    if destination is None:
        sys.stdout.write(text)
        return
    destination.write_text(text, encoding="utf-8")


def _command_query(args: argparse.Namespace) -> None:
    node = parse_query(args.query)
    if args.target == "display":
        _emit(args, to_display(node) + "\n")
    elif args.target == "openalex":
        query, notes = compile_openalex(node)
        _emit(
            args,
            _json_text(
                {
                    "query": query,
                    "dropped_fields": notes.dropped_fields,
                    "dropped_wildcards": notes.dropped_wildcards,
                }
            ),
        )
    elif args.target == "duckdb":
        sql, parameters = compile_duckdb(node)
        _emit(args, _json_text({"sql": sql, "parameters": parameters}))
    else:
        _emit(args, translate(node, args.target) + "\n")


def _command_corpus_build(args: argparse.Namespace) -> None:
    manifest = build_local_corpus(
        _load_jsonl(args.input),
        args.corpus,
        source=args.source,
    )
    _emit(args, _json_text(manifest))


def _command_corpus_search(args: argparse.Namespace) -> None:
    corpus = LocalCorpus(args.corpus)
    corpus.verify()
    _emit(args, _jsonl_text(corpus.search(args.query, limit=args.limit)))


def _command_openalex_search(args: argparse.Namespace) -> None:
    client = OpenAlexClient(
        mailto=args.mailto,
        api_key=os.environ.get("OPENALEX_API_KEY", ""),
    )
    try:
        records = client.search(
            args.query,
            limit=args.limit,
            year_from=args.year_from,
            year_to=args.year_to,
        )
    finally:
        client.close()
    _emit(args, _jsonl_text(records))


def _command_rank(args: argparse.Namespace) -> None:
    protocol = ReviewProtocol.model_validate(_load_json(args.protocol))
    ranked = rank_works(
        _load_jsonl(args.input),
        protocol,
        now_year=args.now_year,
    )
    _emit(args, _jsonl_text(ranked))


def _command_coverage(args: argparse.Namespace) -> None:
    payload = _load_json(args.captures)
    if not isinstance(payload, dict):
        raise ValueError("captures must be a JSON object mapping work ids to counts")
    counts = cast(dict[str, int], payload)
    _emit(
        args,
        _json_text(
            estimate_completeness(
                counts,
                args.occasions,
                occasion_independence_verified=args.occasion_independence_verified,
            )
        ),
    )


def _command_prisma(args: argparse.Namespace) -> None:
    counts = PrismaCounts.model_validate(_load_json(args.counts))
    rendered = render_flow_svg(counts) if args.format == "svg" else render_flow_text(counts)
    _emit(args, rendered + "\n")


def _command_expansion_validate(args: argparse.Namespace) -> None:
    payload = _load_json(args.candidates)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("candidates must be a JSON array of query strings")
    _emit(args, _json_text(validate_variants(args.existing, payload, limit=args.limit)))


def _command_data_profile(args: argparse.Namespace) -> None:
    dataset = parse_dataset_file(args.input)
    _emit(
        args,
        _json_text(
            {
                "filename": dataset.filename,
                "format": dataset.format,
                "byte_count": dataset.byte_count,
                "sha256": dataset.sha256,
                "profile": asdict(dataset.profile),
                "import_notes": dataset.import_notes,
            }
        ),
    )


def _command_data_analyze(args: argparse.Namespace) -> None:
    dataset = parse_dataset_file(args.input)
    recipe: AnalysisRecipe
    if args.analysis_kind == "missingness":
        recipe = MissingnessRecipe()
    elif args.analysis_kind == "descriptive":
        recipe = DescriptiveRecipe(column=args.column)
    elif args.analysis_kind == "group-summary":
        recipe = GroupSummaryRecipe(
            group_by=args.group_by,
            value_column=args.value_column,
            metric=args.metric,
        )
    elif args.analysis_kind == "correlation":
        recipe = PearsonCorrelationRecipe(x_column=args.x_column, y_column=args.y_column)
    elif args.analysis_kind == "meta-analysis":
        recipe = RandomEffectsMetaAnalysisRecipe(
            effect_column=args.effect_column,
            se_column=args.se_column,
            label_column=args.label_column,
        )
    else:
        raise ValueError(f"unknown analysis recipe {args.analysis_kind!r}")
    _emit(args, _json_text(asdict(analyze(dataset, recipe))))


def _add_output(parser: argparse.ArgumentParser, result: str, *, nested: bool = False) -> None:
    """Give one command the shared `--output` option.

    `nested` suppresses the default on a recipe parser, so that accepting the
    option in both positions does not let the recipe overwrite a value the
    parent already read.
    """

    parser.add_argument(
        "--output",
        type=Path,
        default=argparse.SUPPRESS if nested else None,
        metavar="PATH",
        help=f"write the {result} to PATH instead of stdout",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sixsentences",
        description="Open building blocks for auditable research workflows.",
    )
    # argparse resolves an eager action while it consumes the option, so this
    # answers before the required subcommand below is enforced.
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print the installed engine version and exit",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    query = subcommands.add_parser("query", help="parse and compile a boolean query")
    query.add_argument("query")
    query.add_argument(
        "--target",
        choices=("display", "openalex", "duckdb", "pubmed", "scopus", "wos", "ieee", "central"),
        default="display",
    )
    _add_output(query, "compiled query")
    query.set_defaults(handler=_command_query)

    corpus_build = subcommands.add_parser("corpus-build", help="build a local corpus from JSONL")
    corpus_build.add_argument("input", type=Path)
    corpus_build.add_argument("corpus", type=Path)
    corpus_build.add_argument("--source", default="local-jsonl")
    _add_output(corpus_build, "corpus manifest")
    corpus_build.set_defaults(handler=_command_corpus_build)

    corpus_search = subcommands.add_parser("corpus-search", help="search a local corpus")
    corpus_search.add_argument("corpus", type=Path)
    corpus_search.add_argument("query")
    corpus_search.add_argument("--limit", type=int, default=100)
    _add_output(corpus_search, "matching records")
    corpus_search.set_defaults(handler=_command_corpus_search)

    openalex = subcommands.add_parser(
        "openalex-search",
        help="search OpenAlex metadata (OPENALEX_API_KEY recommended)",
    )
    openalex.add_argument("query")
    openalex.add_argument("--mailto", default="")
    openalex.add_argument("--limit", type=int, default=200)
    openalex.add_argument("--year-from", type=int)
    openalex.add_argument("--year-to", type=int)
    _add_output(openalex, "fetched records")
    openalex.set_defaults(handler=_command_openalex_search)

    rank = subcommands.add_parser("rank", help="rank JSONL works against a protocol")
    rank.add_argument("input", type=Path)
    rank.add_argument("protocol", type=Path)
    rank.add_argument("--now-year", type=int)
    _add_output(rank, "ranked records")
    rank.set_defaults(handler=_command_rank)

    coverage = subcommands.add_parser("coverage", help="estimate search coverage with Chao2")
    coverage.add_argument("captures", type=Path)
    coverage.add_argument("--occasions", required=True, type=int)
    coverage.add_argument(
        "--occasion-independence-verified",
        action="store_true",
        help=(
            "assert an external basis for treating capture occasions as independent; "
            "without it the estimate is undetermined"
        ),
    )
    _add_output(coverage, "coverage estimate")
    coverage.set_defaults(handler=_command_coverage)

    prisma = subcommands.add_parser("prisma", help="render explicit PRISMA counters")
    prisma.add_argument("counts", type=Path)
    prisma.add_argument("--format", choices=("text", "svg"), default="text")
    _add_output(prisma, "rendered flow")
    prisma.set_defaults(handler=_command_prisma)

    expansion = subcommands.add_parser(
        "expansion-validate", help="validate provider-generated query candidates"
    )
    expansion.add_argument("candidates", type=Path)
    expansion.add_argument("--existing", action="append", default=[])
    expansion.add_argument("--limit", type=int, default=5)
    _add_output(expansion, "validation result")
    expansion.set_defaults(handler=_command_expansion_validate)

    data_profile = subcommands.add_parser(
        "data-profile", help="profile a bounded CSV, TSV, JSON or XLSX dataset"
    )
    data_profile.add_argument("input", type=Path)
    _add_output(data_profile, "dataset profile")
    data_profile.set_defaults(handler=_command_data_profile)

    data_analyze = subcommands.add_parser(
        "data-analyze", help="run a deterministic analysis recipe over a bounded dataset"
    )
    data_analyze.add_argument("input", type=Path)
    # Accepted before the recipe and after it, because both read naturally.
    _add_output(data_analyze, "analysis result")
    analysis_recipes = data_analyze.add_subparsers(dest="analysis_kind", required=True)

    missingness = analysis_recipes.add_parser("missingness", help="count null and blank cells")
    _add_output(missingness, "analysis result", nested=True)

    descriptive = analysis_recipes.add_parser(
        "descriptive", help="describe one complete numeric column"
    )
    descriptive.add_argument("--column", required=True)
    _add_output(descriptive, "analysis result", nested=True)

    group_summary = analysis_recipes.add_parser(
        "group-summary", help="aggregate complete numeric values by group"
    )
    group_summary.add_argument("--group-by", required=True)
    group_summary.add_argument("--value-column", required=True)
    group_summary.add_argument(
        "--metric", choices=("mean", "median", "sum", "count"), default="mean"
    )
    _add_output(group_summary, "analysis result", nested=True)

    correlation = analysis_recipes.add_parser(
        "correlation", help="compute complete-case Pearson correlation"
    )
    correlation.add_argument("--x-column", required=True)
    correlation.add_argument("--y-column", required=True)
    _add_output(correlation, "analysis result", nested=True)

    meta_analysis = analysis_recipes.add_parser(
        "meta-analysis", help="run DerSimonian-Laird random-effects pooling"
    )
    meta_analysis.add_argument("--effect-column", required=True)
    meta_analysis.add_argument("--se-column", required=True)
    meta_analysis.add_argument("--label-column")
    _add_output(meta_analysis, "analysis result", nested=True)
    data_analyze.set_defaults(handler=_command_data_analyze)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a conventional process exit code."""

    parser = _parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (
        CorpusError,
        OpenAlexError,
        OSError,
        QueryParseError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
