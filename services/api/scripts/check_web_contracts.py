"""Compare the open web client's API calls with implemented FastAPI routes."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

CALL_NAMES = {
    "fetchAuthenticatedDocumentBytes": "GET",
    "followChatTurnRequest": "GET",
    "request": "GET",
    "streamChatRequest": "POST",
    "streamSpecialistRequest": "POST",
}
PATH_LITERAL = re.compile(r"([`\"'])(/.*?)(?<!\\)\1", re.DOTALL)
METHOD = re.compile(r"\bmethod\s*:\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']")
DYNAMIC = re.compile(r"\$\{(?:[^{}]|\{[^{}]*\})+\}")
PARAMETER = re.compile(r"\{[^{}]+\}")


@dataclass(frozen=True, order=True, slots=True)
class Contract:
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
    """Find the closing generic bracket while tolerating nested TS structures."""

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


def _canonical_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = DYNAMIC.sub("{}", path)
    if "${" in path:
        path = path.split("${", 1)[0]
    path = PARAMETER.sub("{}", path)
    return path.rstrip("/") or "/"


def extract_client_contracts(source: str) -> set[Contract]:
    """Extract method/path pairs from the small request wrappers used by api.ts."""

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
        method_match = METHOD.search(body)
        method = method_match.group(1) if method_match else CALL_NAMES[call_name]
        for literal in PATH_LITERAL.finditer(body):
            raw_path = literal.group(2)
            if "\n" in raw_path or raw_path.startswith("//"):
                continue
            contracts.add(Contract(method, _canonical_path(raw_path)))
    return contracts


def application_contracts() -> set[Contract]:
    """Load method/path pairs directly from FastAPI's route registry."""

    from sixsentences_server.app import create_app

    contracts: set[Contract] = set()
    for path, operations in create_app().openapi()["paths"].items():
        for method in operations:
            if method.casefold() not in {"get", "post", "put", "patch", "delete"}:
                continue
            contracts.add(Contract(method.upper(), _canonical_path(path)))
    return contracts


def compare(client_source: str) -> tuple[set[Contract], set[Contract], set[Contract]]:
    """Return required, implemented and missing contract sets."""

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
    report = {
        "client_contracts": len(required),
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
