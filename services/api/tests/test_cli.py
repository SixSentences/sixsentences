"""The operator CLI answers what a bug report asks for first."""

from __future__ import annotations

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from sixsentences_server import __version__
from sixsentences_server.cli import app


def test_version_option_prints_the_installed_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"six-community {__version__}"


def test_version_is_read_from_the_installed_distribution() -> None:
    """A literal in the source is a second place for the version to drift."""

    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]

    assert __version__ == declared


def test_the_root_callback_does_not_swallow_a_subcommand() -> None:
    """Adding an eager option must leave every command reachable."""

    result = CliRunner().invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "doctor" in result.output
