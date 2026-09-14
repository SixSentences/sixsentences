#!/usr/bin/env python3
"""Fail-closed audit of the final community service tree and runtime surface."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

EXPECTED_SOURCE_COMMIT = "19762eff1b1f7074e7429b4562b1c8991d5951fa"
EXPECTED_HTTP_OPERATIONS = 400
EXPECTED_HTTP_CONTRACT_SHA256 = "bf4ec20090d1c9eda237a382b7c74272ef2dcf8f900f0c573a10589a8f05f095"
EXPECTED_WEBSOCKETS = 1
EXPECTED_TABLES = 89
EXPECTED_ENGINE_VERSION = "0.2.0a1"

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".example",
    ".ini",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".pyi",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_ARTIFACT_SUFFIXES = {
    ".bak",
    ".db",
    ".dump",
    ".pem",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".zip",
}
SECRET_PATTERNS = {
    "aws-access-key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "github-token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    "google-api-key": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "openai-key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "slack-token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
}
SAFE_TEST_SECRET_CANARIES = {
    "tests/test_repository_graphics.py": {b"sk-" + b"ABCDEFGHIJKLMNOPQRSTUVWX"},
    "tests/test_security.py": {b"-----BEGIN " + b"PRIVATE KEY-----"},
}
PRIVATE_MARKERS = {
    "absolute-home-path": re.compile(
        rb"(?:/Users|/home)/[A-Za-z0-9._-]+(?:/|$)"
    ),
}
FORBIDDEN_SOURCE_MARKERS = (
    "stripe",
    "checkout",
    "billing",
    "waitlist",
    "dsa_",
    "switching",
    "platform_admin",
    "subscription",
    "is_admin",
    "chatreport",
    "chat_report",
    "credit_topup",
    "topup_",
    "upgrade_to",
)
FORBIDDEN_ROUTE_PREFIXES = (
    "/admin",
    "/billing",
    "/contracts",
    "/dsa",
    "/features",
    "/waitlist",
)
REQUIRED_ROUTE_FRAGMENTS = (
    "/auth/",
    "/projects",
    "/runs/",
    "/library/",
    "/writer",
    "/surveys",
    "/interviews",
    "/figures",
    "/datasets",
    "/repository-",
)
MANIFEST_NAME = "COMMUNITY_EXPORT_MANIFEST.json"
IGNORED_DIRS = {".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "data"}


def sha256(path: Path) -> str:
    """Hash one regular file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def included_files(root: Path) -> list[Path]:
    """Return all auditable files, rejecting symlinks and generated state."""

    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink:{relative.as_posix()}")
        if path.is_file() and relative.as_posix() != MANIFEST_NAME:
            files.append(path)
    return files


def verify_manifest(root: Path, files: list[Path]) -> None:
    """Verify exact file-set and digest binding for the sanitized tree."""

    manifest_path = root / MANIFEST_NAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("format") != 1 or payload.get("source_commit") != EXPECTED_SOURCE_COMMIT:
        raise RuntimeError("manifest:identity")
    expected = {str(item["path"]): str(item["sha256"]) for item in payload.get("files", [])}
    actual = {path.relative_to(root).as_posix(): sha256(path) for path in files}
    if expected != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            path for path in set(expected) & set(actual) if expected[path] != actual[path]
        )
        detail = (missing or extra or changed or ["unknown"])[0]
        raise RuntimeError(
            f"manifest:file-set-or-hash:{detail} — a change under services/api/ has to"
            " rewrite the manifest: python scripts/audit_community_export.py"
            " --refresh-manifest"
        )


def refresh_manifest(root: Path, files: list[Path]) -> tuple[str, ...]:
    """Rewrite the manifest for the current tree and report what it changed.

    Only ever reached after every other gate passed. The manifest records a tree
    that has already been shown to hold the source boundary, so refreshing it can
    record new hashes but never grant approval to something the audit rejects.
    """

    manifest_path = root / MANIFEST_NAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    before = {str(item["path"]): str(item["sha256"]) for item in payload.get("files", [])}
    entries = [
        {
            "bytes": path.stat().st_size,
            "mode": f"{path.stat().st_mode & 0o777:04o}",
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256(path),
        }
        for path in files
    ]
    after = {str(entry["path"]): str(entry["sha256"]) for entry in entries}
    payload["files"] = sorted(entries, key=lambda entry: str(entry["path"]))
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return tuple(
        sorted(
            [f"changed:{name}" for name in before.keys() & after.keys() if before[name] != after[name]]
            + [f"added:{name}" for name in after.keys() - before.keys()]
            + [f"removed:{name}" for name in before.keys() - after.keys()]
        )
    )


def scan_tree(root: Path, files: list[Path]) -> None:
    """Scan every distributable text file without echoing matched content."""

    for path in files:
        relative = path.relative_to(root)
        name = relative.as_posix()
        if name == ".env" or path.suffix.casefold() in FORBIDDEN_ARTIFACT_SUFFIXES:
            raise RuntimeError(f"forbidden-artifact:{name}")
        if path.stat().st_size > 8 * 1024 * 1024 or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        data = path.read_bytes()
        for rule, pattern in {**SECRET_PATTERNS, **PRIVATE_MARKERS}.items():
            matches = {match.group(0) for match in pattern.finditer(data)}
            if matches and not matches.issubset(SAFE_TEST_SECRET_CANARIES.get(name, set())):
                raise RuntimeError(f"{rule}:{name}")

    source = root / "src/sixsentences_server"
    for path in sorted(source.rglob("*.py")):
        lowered = path.read_text(encoding="utf-8").casefold().replace(" ", "")
        for marker in FORBIDDEN_SOURCE_MARKERS:
            if marker in lowered:
                if (
                    marker == "subscription"
                    and path.relative_to(source).as_posix() == "acquisition/models.py"
                ):
                    continue
                raise RuntimeError(f"hosted-boundary:{path.relative_to(root).as_posix()}")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                normalized = node.name.casefold().replace("_", "")
                if any(
                    marker.replace("_", "") in normalized for marker in FORBIDDEN_SOURCE_MARKERS
                ):
                    raise RuntimeError(f"hosted-symbol:{path.relative_to(root).as_posix()}")


def verify_codeql_regressions(root: Path) -> None:
    """Check the three previously reported high-severity source patterns."""

    if list(root.rglob("audit_boundary.py")):
        raise RuntimeError("codeql:legacy-audit-boundary")
    query_parser = (root / "src/sixsentences_server/querylang/parser.py").read_text(
        encoding="utf-8"
    )
    participation = (root / "src/sixsentences_server/core/study_participation.py").read_text(
        encoding="utf-8"
    )
    mail = (root / "src/sixsentences_server/mail/service.py").read_text(encoding="utf-8")
    app = (root / "src/sixsentences_server/api/app.py").read_text(encoding="utf-8")
    if "_TOKEN_RE" in query_parser or "re.compile" in query_parser:
        raise RuntimeError("codeql:query-parser-regex")
    if "EMAIL_RE" in participation or "re.compile" in participation:
        raise RuntimeError("codeql:email-regex")
    if "MESSAGE_ID_RE" in mail or "re.compile" in mail:
        raise RuntimeError("codeql:message-id-regex")
    if ".exception(failure_message)" in app:
        raise RuntimeError("codeql:tainted-cleartext-logging")


def verify_runtime(root: Path) -> None:
    """Import the packaged app and verify the exact community contract."""

    source = str(root / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    with tempfile.TemporaryDirectory(prefix="six-community-audit-") as temporary:
        os.environ["SIX_DATA_DIR"] = temporary
        os.environ["SIX_DATABASE_URL"] = "sqlite:///:memory:"
        from starlette.routing import WebSocketRoute

        from sixsentences_server.api.app import create_app
        from sixsentences_server.config import Settings
        from sixsentences_server.core.db import Base
        from sixsentences_server.core.plans import all_plans

        settings = Settings()
        if settings.model_config.get("env_prefix") != "SIX_" or settings.self_signup:
            raise RuntimeError("config:environment-or-signup-default")
        app = create_app()
        operation_set = {
            f"{method} {route.path}"
            for route in app.routes
            for method in (getattr(route, "methods", ()) or ())
        }
        operations = len(operation_set)
        websockets = sum(isinstance(route, WebSocketRoute) for route in app.routes)
        if (operations, websockets) != (EXPECTED_HTTP_OPERATIONS, EXPECTED_WEBSOCKETS):
            raise RuntimeError(f"contract:route-count:{operations}:{websockets}")
        serialized = "".join(f"{operation}\n" for operation in sorted(operation_set))
        if hashlib.sha256(serialized.encode()).hexdigest() != EXPECTED_HTTP_CONTRACT_SHA256:
            raise RuntimeError("contract:route-digest")
        if "POST /runs/{run_id}/chat-report" not in operation_set:
            raise RuntimeError("contract:conversation-review")
        paths = {str(getattr(route, "path", "")) for route in app.routes}
        if any(path.startswith(FORBIDDEN_ROUTE_PREFIXES) for path in paths):
            raise RuntimeError("contract:hosted-route")
        if not all(
            any(fragment in path for path in paths) for fragment in REQUIRED_ROUTE_FRAGMENTS
        ):
            raise RuntimeError("contract:missing-research-family")
        if len(Base.metadata.tables) != EXPECTED_TABLES:
            raise RuntimeError(f"contract:table-count:{len(Base.metadata.tables)}")
        plans = all_plans()
        if len(plans) != 1 or plans[0].tier.value != "community":
            raise RuntimeError("contract:commercial-plan")


def verify_packaging(root: Path) -> None:
    """Bind service and root-engine versions and executable operational files."""

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    expected_dependency = f"sixsentences-engine=={EXPECTED_ENGINE_VERSION}"
    if (
        project["version"] != EXPECTED_ENGINE_VERSION
        or expected_dependency not in project["dependencies"]
    ):
        raise RuntimeError("package:engine-version")
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    if (
        'name = "sixsentences-engine"' not in lock
        or f'version = "{EXPECTED_ENGINE_VERSION}"' not in lock
    ):
        raise RuntimeError("package:engine-lock")
    for relative in ("deploy/community/backup.sh", "deploy/community/restore.sh"):
        script = root / relative
        if not os.access(script, os.X_OK):
            raise RuntimeError(f"package:not-executable:{relative}")
        subprocess.run(["bash", "-n", str(script)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "service", type=Path, nargs="?", default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--refresh-manifest",
        action="store_true",
        help="rewrite the manifest for this tree once every other gate has passed",
    )
    args = parser.parse_args()
    root = args.service.resolve()
    refreshed: tuple[str, ...] = ()
    try:
        files = included_files(root)
        if not args.refresh_manifest:
            verify_manifest(root, files)
        scan_tree(root, files)
        verify_codeql_regressions(root)
        verify_packaging(root)
        verify_runtime(root)
        if args.refresh_manifest:
            refreshed = refresh_manifest(root, files)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"community export audit failed: {exc}", file=sys.stderr)
        return 1
    if args.refresh_manifest:
        print(
            "community export manifest refreshed: "
            + (", ".join(refreshed) if refreshed else "no change")
        )
        return 0
    print(
        "community export audit passed: "
        f"{len(files)} files, {EXPECTED_HTTP_OPERATIONS} HTTP operations, "
        f"{EXPECTED_WEBSOCKETS} WebSocket, {EXPECTED_TABLES} tables"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
