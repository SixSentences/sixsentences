"""Freeze the deny-by-default public source boundary."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "src" / "sixsentences"
ALLOWED_PACKAGES = {
    "connectors",
    "core",
    "corpus",
    "coverage",
    "pipeline",
    "querylang",
    "ranking",
    "reporting",
    "screening",
}
FORBIDDEN_IMPORTS = (
    "sixsentences.api",
    "sixsentences.chat",
    "sixsentences.llm",
    "sixsentences.mail",
    "sixsentences.ops",
    "sqlalchemy",
    "stripe",
)


def test_only_allowlisted_package_boundaries_are_exported() -> None:
    actual = {
        path.name for path in PACKAGE.iterdir() if path.is_dir() and path.name != "__pycache__"
    }
    assert actual == ALLOWED_PACKAGES


def test_export_has_no_forbidden_import_edges() -> None:
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_IMPORTS):
                    violations.append(f"{path.relative_to(PACKAGE)}: {name}")
    assert violations == []
