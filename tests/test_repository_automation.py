"""Synthetic tests for repository DCO and release automation."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"repository_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DCO = _load_script("check_dco")
RELEASE = _load_script("release")


def _identity(value: str) -> object:
    return DCO.Identity.parse(value)


def test_dco_requires_author_and_every_coauthor_to_sign_off() -> None:
    trailers = DCO.interpret_identity_trailers(
        """feat: synthetic contribution

Co-authored-by: Bob Example <bob@example.com>
Signed-off-by: Alice Example <alice@example.com>
Signed-off-by: Bob Example <bob@example.com>
"""
    )

    errors = DCO.validate_identities(_identity("Alice Example <alice@example.com>"), trailers)

    assert errors == []


def test_dco_rejects_missing_or_mismatched_personal_signoffs() -> None:
    trailers = DCO.interpret_identity_trailers(
        """fix: synthetic contribution

Co-authored-by: Bob Example <bob@example.com>
Signed-off-by: Someone Else <else@example.com>
"""
    )

    errors = DCO.validate_identities(_identity("Alice Example <alice@example.com>"), trailers)

    assert errors == [
        "commit author is not signed off: alice example <alice@example.com>",
        "co-author is not signed off: bob example <bob@example.com>",
    ]


def test_dco_does_not_accept_a_trailer_like_line_from_the_message_body() -> None:
    trailers = DCO.interpret_identity_trailers(
        """docs: synthetic contribution

The following line is an example, not a footer:
Signed-off-by: Alice Example <alice@example.com>

This final paragraph makes the example part of the body.
"""
    )

    assert trailers.signoffs == ()


def test_dependabot_alias_is_accepted_only_for_a_dependabot_owned_pr() -> None:
    author = _identity("dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>")
    trailers = DCO.interpret_identity_trailers(
        """chore(deps): synthetic update

Signed-off-by: dependabot[bot] <support@github.com>
"""
    )

    assert DCO.validate_identities(author, trailers) != []
    assert (
        DCO.validate_identities(
            author,
            trailers,
            pull_request_author="dependabot[bot]",
        )
        == []
    )


@pytest.mark.parametrize(
    ("package", "public", "tag"),
    [
        ("1.2.3", "1.2.3", "v1.2.3"),
        ("1.2.3a4", "1.2.3-alpha.4", "v1.2.3-alpha.4"),
        ("1.2.3b4", "1.2.3-beta.4", "v1.2.3-beta.4"),
        ("1.2.3rc4", "1.2.3-rc.4", "v1.2.3-rc.4"),
    ],
)
def test_release_version_mapping(package: str, public: str, tag: str) -> None:
    assert RELEASE.release_version(package) == RELEASE.ReleaseVersion(package, public, tag)


@pytest.mark.parametrize("value", ["1.2", "01.2.3", "1.2.3.dev1", "1.2.3+local"])
def test_release_version_mapping_rejects_unsupported_versions(value: str) -> None:
    with pytest.raises(RELEASE.ReleaseValidationError):
        RELEASE.release_version(value)


def test_release_file_gate_matches_pyproject_cff_changelog_and_notes(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sixsentences-engine"\nversion = "0.1.0a1"\n',
        encoding="utf-8",
    )
    (tmp_path / "CITATION.cff").write_text(
        "version: 0.1.0-alpha.1\ndate-released: 2026-09-12\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "## [0.1.0-alpha.1] - 2026-09-12\n",
        encoding="utf-8",
    )
    notes = tmp_path / "docs" / "releases"
    notes.mkdir(parents=True)
    (notes / "v0.1.0-alpha.1.md").write_text("# Release\n", encoding="utf-8")

    result = RELEASE.validate_files(
        tmp_path,
        tag="v0.1.0-alpha.1",
        commit_date=date(2026, 9, 12),
    )

    assert result.package == "0.1.0a1"


def test_checksum_verification_fails_on_changed_or_unlisted_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"original")
    digest = hashlib.sha256(b"original").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  artifact.whl\n", encoding="utf-8")
    RELEASE.verify_checksums(tmp_path)

    artifact.write_bytes(b"changed")
    with pytest.raises(RELEASE.ReleaseValidationError, match="checksum mismatch"):
        RELEASE.verify_checksums(tmp_path)

    artifact.write_bytes(b"original")
    (tmp_path / "unlisted.tar.gz").write_bytes(b"extra")
    with pytest.raises(RELEASE.ReleaseValidationError, match="exactly cover"):
        RELEASE.verify_checksums(tmp_path)
