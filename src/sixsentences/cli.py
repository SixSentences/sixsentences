"""Minimal command-line interface for the portable engine."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from sixsentences.connectors.openalex import OpenAlexClient, OpenAlexError
from sixsentences.core.models import PrismaCounts, ReviewProtocol, WorkRecord
from sixsentences.corpus.local import CorpusError, LocalCorpus, build_local_corpus
from sixsentences.coverage.estimator import estimate_completeness
from sixsentences.pipeline.expansion import validate_variants
from sixsentences.querylang.ast import to_display
from sixsentences.querylang.compile_duckdb import compile_duckdb
from sixsentences.querylang.compile_openalex import compile_openalex
from sixsentences.querylang.parser import QueryParseError, parse_query
from sixsentences.querylang.translate import translations
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


def _print_json(value: object) -> None:
    if hasattr(value, "model_dump_json"):
        print(value.model_dump_json(indent=2))
        return
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _emit_records(records: Sequence[WorkRecord]) -> None:
    for record in records:
        print(record.model_dump_json())


def _command_query(args: argparse.Namespace) -> None:
    node = parse_query(args.query)
    if args.target == "display":
        print(to_display(node))
    elif args.target == "openalex":
        query, notes = compile_openalex(node)
        _print_json(
            {
                "query": query,
                "dropped_fields": notes.dropped_fields,
                "dropped_wildcards": notes.dropped_wildcards,
            }
        )
    elif args.target == "duckdb":
        sql, parameters = compile_duckdb(node)
        _print_json({"sql": sql, "parameters": parameters})
    else:
        print(translations(node)[args.target])


def _command_corpus_build(args: argparse.Namespace) -> None:
    manifest = build_local_corpus(
        _load_jsonl(args.input),
        args.corpus,
        source=args.source,
    )
    _print_json(manifest)


def _command_corpus_search(args: argparse.Namespace) -> None:
    corpus = LocalCorpus(args.corpus)
    corpus.verify()
    _emit_records(corpus.search(args.query, limit=args.limit))


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
    _emit_records(records)


def _command_rank(args: argparse.Namespace) -> None:
    protocol = ReviewProtocol.model_validate(_load_json(args.protocol))
    ranked = rank_works(
        _load_jsonl(args.input),
        protocol,
        now_year=args.now_year,
    )
    for item in ranked:
        print(item.model_dump_json())


def _command_coverage(args: argparse.Namespace) -> None:
    payload = _load_json(args.captures)
    if not isinstance(payload, dict):
        raise ValueError("captures must be a JSON object mapping work ids to counts")
    counts = cast(dict[str, int], payload)
    _print_json(
        estimate_completeness(
            counts,
            args.occasions,
            occasion_independence_verified=args.occasion_independence_verified,
        )
    )


def _command_prisma(args: argparse.Namespace) -> None:
    counts = PrismaCounts.model_validate(_load_json(args.counts))
    output = render_flow_svg(counts) if args.format == "svg" else render_flow_text(counts)
    if args.output is None:
        print(output)
    else:
        args.output.write_text(output + ("\n" if args.format == "text" else ""), encoding="utf-8")


def _command_expansion_validate(args: argparse.Namespace) -> None:
    payload = _load_json(args.candidates)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("candidates must be a JSON array of query strings")
    _print_json(validate_variants(args.existing, payload, limit=args.limit))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sixsentences",
        description="Portable building blocks for systematic literature search.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    query = subcommands.add_parser("query", help="parse and compile a boolean query")
    query.add_argument("query")
    query.add_argument(
        "--target",
        choices=("display", "openalex", "duckdb", "pubmed", "scopus", "wos", "ieee"),
        default="display",
    )
    query.set_defaults(handler=_command_query)

    corpus_build = subcommands.add_parser("corpus-build", help="build a local corpus from JSONL")
    corpus_build.add_argument("input", type=Path)
    corpus_build.add_argument("corpus", type=Path)
    corpus_build.add_argument("--source", default="local-jsonl")
    corpus_build.set_defaults(handler=_command_corpus_build)

    corpus_search = subcommands.add_parser("corpus-search", help="search a local corpus")
    corpus_search.add_argument("corpus", type=Path)
    corpus_search.add_argument("query")
    corpus_search.add_argument("--limit", type=int, default=100)
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
    openalex.set_defaults(handler=_command_openalex_search)

    rank = subcommands.add_parser("rank", help="rank JSONL works against a protocol")
    rank.add_argument("input", type=Path)
    rank.add_argument("protocol", type=Path)
    rank.add_argument("--now-year", type=int)
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
    coverage.set_defaults(handler=_command_coverage)

    prisma = subcommands.add_parser("prisma", help="render explicit PRISMA counters")
    prisma.add_argument("counts", type=Path)
    prisma.add_argument("--format", choices=("text", "svg"), default="text")
    prisma.add_argument("--output", type=Path)
    prisma.set_defaults(handler=_command_prisma)

    expansion = subcommands.add_parser(
        "expansion-validate", help="validate provider-generated query candidates"
    )
    expansion.add_argument("candidates", type=Path)
    expansion.add_argument("--existing", action="append", default=[])
    expansion.add_argument("--limit", type=int, default=5)
    expansion.set_defaults(handler=_command_expansion_validate)
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
