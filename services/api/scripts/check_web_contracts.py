#!/usr/bin/env python3
"""Compare the open web client's request contracts with the Community API."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

PATH_LITERAL = re.compile(r"([`\"'])(/.*?)(?<!\\)\1", re.DOTALL)
API_PATH_LITERAL = re.compile(r"`\$\{API_URL\}(/.*?)(?<!\\)`", re.DOTALL)
METHOD = re.compile(r"\bmethod\s*:\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']")
DYNAMIC = re.compile(r"\$\{(?:[^{}]|\{[^{}]*\})+\}")
PARAMETER = re.compile(r"\{[^{}]+\}")
CALL_NAMES = frozenset(
    {
        "fetchAuthenticatedDocumentBytes",
        "followChatTurnRequest",
        "request",
        "streamChatRequest",
        "streamSpecialistRequest",
    }
)


@dataclass(frozen=True, order=True, slots=True)
class Contract:
    """One canonical HTTP method/path pair."""

    method: str
    path: str


def _skip_string(source: str, index: int) -> int:
    quote = source[index]
    index += 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == quote:
            return index + 1
        else:
            index += 1
    return len(source)


def _generic_end(source: str, index: int) -> int | None:
    """Find a call's closing generic bracket, including nested TS types."""

    angle = 0
    round_depth = square = curly = 0
    while index < len(source):
        character = source[index]
        if character in "'\"`":
            index = _skip_string(source, index)
            continue
        if character == "<" and round_depth == square == curly == 0:
            angle += 1
        elif character == ">" and round_depth == square == curly == 0:
            angle -= 1
            if angle == 0:
                return index + 1
        elif character == "(":
            round_depth += 1
        elif character == ")":
            round_depth = max(0, round_depth - 1)
        elif character == "[":
            square += 1
        elif character == "]":
            square = max(0, square - 1)
        elif character == "{":
            curly += 1
        elif character == "}":
            curly = max(0, curly - 1)
        index += 1
    return None


def _call_end(source: str, opening: int) -> int | None:
    depth = 0
    index = opening
    while index < len(source):
        character = source[index]
        if character in "'\"`":
            index = _skip_string(source, index)
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _arguments(body: str) -> list[str]:
    """Split top-level call arguments without interpreting TypeScript."""

    arguments: list[str] = []
    start = 0
    round_depth = square = curly = 0
    index = 0
    while index < len(body):
        character = body[index]
        if character in "'\"`":
            index = _skip_string(body, index)
            continue
        if character == "(":
            round_depth += 1
        elif character == ")":
            round_depth -= 1
        elif character == "[":
            square += 1
        elif character == "]":
            square -= 1
        elif character == "{":
            curly += 1
        elif character == "}":
            curly -= 1
        elif character == "," and round_depth == square == curly == 0:
            arguments.append(body[start:index].strip())
            start = index + 1
        index += 1
    arguments.append(body[start:].strip())
    return arguments


def _canonical_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = DYNAMIC.sub("{}", path)
    if "${" in path:
        path = path.split("${", 1)[0]
    path = PARAMETER.sub("{}", path)
    return path.rstrip("/") or "/"


def _literal_paths(argument: str) -> set[str]:
    paths: set[str] = set()
    for match in PATH_LITERAL.finditer(argument):
        raw_path = match.group(2)
        if "\n" not in raw_path and not raw_path.startswith("//"):
            paths.add(_canonical_path(raw_path))
    return paths


def extract_central_client_contracts(source: str) -> set[Contract]:
    """Extract the central typed API surface, including agent event replay."""

    contracts: set[Contract] = set()
    names = "|".join(sorted(CALL_NAMES, key=len, reverse=True))
    for match in re.finditer(rf"\b({names})\b", source):
        call_name = match.group(1)
        index = match.end()
        while index < len(source) and source[index].isspace():
            index += 1
        if index < len(source) and source[index] == "<":
            generic_end = _generic_end(source, index)
            if generic_end is None:
                continue
            index = generic_end
            while index < len(source) and source[index].isspace():
                index += 1
        if index >= len(source) or source[index] != "(":
            continue
        end = _call_end(source, index)
        if end is None:
            continue
        body = source[index + 1 : end - 1]
        arguments = _arguments(body)
        paths = _literal_paths(arguments[0]) if arguments else set()
        if not paths:
            continue
        method_match = METHOD.search(body)
        default_method = "POST" if call_name.startswith("stream") else "GET"
        method = method_match.group(1) if method_match else default_method
        contracts.update(Contract(method, path) for path in paths)
        if call_name == "streamChatRequest" and len(arguments) > 1:
            contracts.update(Contract("GET", path) for path in _literal_paths(arguments[1]))

    agent_stream = re.search(r"`\$\{API_URL\}(/agent/turns/\$\{[^`]+?/events/stream)`", source)
    if agent_stream is not None:
        contracts.add(Contract("GET", _canonical_path(agent_stream.group(1))))
    return contracts


def extract_client_contracts(source: str) -> set[Contract]:
    """Extract every central and direct transport contract in ``api.ts``."""

    contracts = extract_central_client_contracts(source)
    # Several exported download/stream helpers call the transport directly.
    # These are part of the browser contract even though they do not pass
    # through the generic request wrappers above.
    for match in re.finditer(r"\b(?:fetch|fetchApiResponse)\b", source):
        index = match.end()
        while index < len(source) and source[index].isspace():
            index += 1
        if index >= len(source) or source[index] != "(":
            continue
        end = _call_end(source, index)
        if end is None:
            continue
        body = source[index + 1 : end - 1]
        method_match = METHOD.search(body)
        method = method_match.group(1) if method_match else "GET"
        for literal in API_PATH_LITERAL.finditer(body):
            raw_path = literal.group(1)
            if "${path}" not in raw_path:
                contracts.add(Contract(method, _canonical_path(raw_path)))

    # EventSource consumes this ticketed URL outside the module.
    stream_url = re.search(
        r"return\s+`\$\{API_URL\}(/runs/\$\{runId\}/events/stream[^`]*)`", source
    )
    if stream_url is not None:
        contracts.add(Contract("GET", _canonical_path(stream_url.group(1))))
    return contracts


def application_contracts() -> set[Contract]:
    """Load canonical method/path pairs from FastAPI's route registry."""

    from sixsentences_server.api.app import create_app

    contracts: set[Contract] = set()
    for path, operations in create_app().openapi()["paths"].items():
        for method in operations:
            if method.casefold() in {"get", "post", "put", "patch", "delete"}:
                contracts.add(Contract(method.upper(), _canonical_path(path)))
    return contracts


def contract_sha256(contracts: set[Contract]) -> str:
    """Return the documented stable digest for one contract set."""

    serialized = "".join(f"{item.method} {item.path}\n" for item in sorted(contracts))
    return hashlib.sha256(serialized.encode()).hexdigest()


def compare(client_source: str) -> tuple[set[Contract], set[Contract], set[Contract]]:
    """Return required, implemented, and missing contract sets."""

    required = extract_client_contracts(client_source)
    implemented = application_contracts()
    return required, implemented, required - implemented


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_client = Path(__file__).resolve().parents[3] / "apps/web/src/lib/api.ts"
    parser.add_argument("--client", type=Path, default=default_client)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()

    source = args.client.read_text(encoding="utf-8")
    required, implemented, missing = compare(source)
    central = extract_central_client_contracts(source)
    report = {
        "client_contracts": len(required),
        "client_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "contract_sha256": contract_sha256(required),
        "central_contracts": len(central),
        "central_contract_sha256": contract_sha256(central),
        "implemented_contracts": len(required & implemented),
        "missing_contracts": len(missing),
        "missing": [f"{item.method} {item.path}" for item in sorted(missing)],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Web contracts: {report['implemented_contracts']}/{report['client_contracts']} "
            f"implemented; {report['missing_contracts']} missing."
        )
        for item in report["missing"]:
            print(f"MISSING {item}")
    if args.require_complete and missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
