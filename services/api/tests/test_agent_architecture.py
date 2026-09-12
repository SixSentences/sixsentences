"""Architecture-drift checks for direct hosted-agent dependencies."""

from __future__ import annotations

import ast
from pathlib import Path

_AGENT_PACKAGE = Path(__file__).parents[1] / "src" / "sixsentences" / "agent"
_FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "asyncio",
    "builtins",
    "concurrent",
    "ctypes",
    "ftplib",
    "http",
    "httpx",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "requests",
    "runpy",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "telnetlib",
    "threading",
    "urllib",
    "urllib3",
    "websockets",
}
_FORBIDDEN_BUILTINS = {"__import__", "compile", "eval", "exec", "open"}


def _import_roots(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.ImportFrom):
        return (str(node.module or "").partition(".")[0],)
    return tuple(str(name.name).partition(".")[0] for name in node.names)


def test_agent_controller_has_no_direct_generic_execution_or_network_imports() -> None:
    """Flag direct authority drift in the generic orchestration package.

    Domain handlers may call bounded, server-owned connectors elsewhere in the
    modular monolith. The generic controller itself must not gain a shell,
    dynamic loader, subprocess, raw socket or unrestricted HTTP dependency.
    This enumerated static check is not a sandbox or a transitive import-graph
    proof; code review and runtime authorization remain the security boundary.
    """

    violations: list[str] = []
    for path in sorted(_AGENT_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for root in _import_roots(node):
                    if root in _FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{path.name}:{node.lineno}: import {root}")
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_BUILTINS:
                violations.append(f"{path.name}:{node.lineno}: call {node.func.id}")
    assert violations == [], "Direct agent dependency boundary changed:\n" + "\n".join(violations)
