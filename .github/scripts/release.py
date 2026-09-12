#!/usr/bin/env python3
"""Validate and finalize immutable SixSentences release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Final

_PEP440_RE: Final = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:(?P<kind>a|b|rc)(?P<number>0|[1-9][0-9]*))?$"
)
_TAG_RE: Final = re.compile(
    r"^v(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-(?:alpha|beta|rc)\.(?:0|[1-9][0-9]*))?$"
)
_CHECKSUM_RE: Final = re.compile(r"^(?P<digest>[0-9a-f]{64}) [ *](?P<name>[^/\r\n]+)$")
_DISTRIBUTION_NAME: Final = "sixsentences-engine"


class ReleaseValidationError(ValueError):
    """Raised when release metadata or artifacts fail closed validation."""


@dataclass(frozen=True)
class ReleaseVersion:
    """Equivalent Python, public, and tag forms of one release version."""

    package: str
    public: str
    tag: str


def release_version(package_version: str) -> ReleaseVersion:
    """Convert the project's supported PEP 440 subset to public tag syntax."""

    match = _PEP440_RE.fullmatch(package_version)
    if match is None:
        raise ReleaseValidationError("package version must be X.Y.Z, X.Y.ZaN, X.Y.ZbN, or X.Y.ZrcN")
    base = ".".join(match.group(name) for name in ("major", "minor", "patch"))
    kind = match.group("kind")
    number = match.group("number")
    suffix = ""
    if kind is not None and number is not None:
        public_kind = {"a": "alpha", "b": "beta", "rc": "rc"}[kind]
        suffix = f"-{public_kind}.{number}"
    public = base + suffix
    return ReleaseVersion(package_version, public, f"v{public}")


def _read_project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        document = tomllib.load(handle)
    try:
        value = document["project"]["version"]
    except (KeyError, TypeError) as exc:
        raise ReleaseValidationError("pyproject.toml has no project.version") from exc
    if not isinstance(value, str):
        raise ReleaseValidationError("pyproject.toml project.version must be a string")
    return value


def _read_cff_scalar(root: Path, key: str) -> str:
    matches: list[str] = []
    for line in (root / "CITATION.cff").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}:"):
            matches.append(line.partition(":")[2].strip().strip("'\""))
    if len(matches) != 1 or not matches[0]:
        raise ReleaseValidationError(f"CITATION.cff must contain one top-level {key}")
    return matches[0]


def validate_files(root: Path, *, tag: str, commit_date: date) -> ReleaseVersion:
    """Validate canonical version, citation, changelog, and release-note files."""

    if _TAG_RE.fullmatch(tag) is None:
        raise ReleaseValidationError("release tag has unsupported syntax")
    version = release_version(_read_project_version(root))
    if tag != version.tag:
        raise ReleaseValidationError(f"tag {tag!r} does not match expected {version.tag!r}")
    cff_version = _read_cff_scalar(root, "version")
    if cff_version != version.public:
        raise ReleaseValidationError(
            f"CITATION.cff version {cff_version!r} does not match {version.public!r}"
        )
    try:
        cff_date = date.fromisoformat(_read_cff_scalar(root, "date-released"))
    except ValueError as exc:
        raise ReleaseValidationError("CITATION.cff date-released is not an ISO date") from exc
    if cff_date != commit_date:
        raise ReleaseValidationError(
            f"CITATION.cff date {cff_date} does not match tagged commit UTC date {commit_date}"
        )

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    expected_changelog = f"## [{version.public}] - {commit_date.isoformat()}"
    if expected_changelog not in changelog:
        raise ReleaseValidationError(f"CHANGELOG.md is missing {expected_changelog!r}")
    notes = root / "docs" / "releases" / f"{tag}.md"
    if not notes.is_file() or not notes.read_text(encoding="utf-8").strip():
        raise ReleaseValidationError(f"release notes are missing at {notes.relative_to(root)}")
    return version


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def validate_repository(root: Path, *, tag: str, main_ref: str) -> tuple[ReleaseVersion, int]:
    """Validate that an immutable tag is clean, checked out, and contained in main."""

    status = _git(root, "status", "--porcelain", "--untracked-files=all").stdout
    if status:
        raise ReleaseValidationError("release checkout is not clean")
    tag_ref = f"refs/tags/{tag}^{{commit}}"
    tag_commit = _git(root, "rev-parse", "--verify", tag_ref).stdout.strip()
    head_commit = _git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    if tag_commit != head_commit:
        raise ReleaseValidationError("release tag does not resolve to the checked-out commit")
    _git(root, "rev-parse", "--verify", f"{main_ref}^{{commit}}")
    ancestor = _git(root, "merge-base", "--is-ancestor", tag_commit, main_ref, check=False)
    if ancestor.returncode != 0:
        raise ReleaseValidationError(f"tagged commit is not contained in {main_ref}")

    committed_at = _git(root, "show", "-s", "--format=%cI", tag_commit).stdout.strip()
    try:
        commit_date = datetime.fromisoformat(committed_at).astimezone(UTC).date()
    except ValueError as exc:
        raise ReleaseValidationError("tagged commit has an invalid committer date") from exc
    epoch_text = _git(root, "show", "-s", "--format=%ct", tag_commit).stdout.strip()
    if not epoch_text.isdigit():
        raise ReleaseValidationError("tagged commit has an invalid source epoch")
    return validate_files(root, tag=tag, commit_date=commit_date), int(epoch_text)


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "" not in path.parts


def _metadata_values(payload: bytes, *, source: str) -> tuple[str, str]:
    metadata = BytesParser().parsebytes(payload)
    name = metadata.get("Name")
    version = metadata.get("Version")
    if name is None or version is None:
        raise ReleaseValidationError(f"{source} has incomplete package metadata")
    return name, version


def _verify_wheel(path: Path, *, package_version: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if any(not _safe_archive_name(name) for name in names):
                raise ReleaseValidationError(f"{path.name} contains an unsafe archive path")
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                raise ReleaseValidationError(f"{path.name} must contain one METADATA file")
            metadata_payload = archive.read(metadata_names[0])
            if "sixsentences/py.typed" not in names:
                raise ReleaseValidationError(f"{path.name} does not contain py.typed")
    except zipfile.BadZipFile as exc:
        raise ReleaseValidationError(f"{path.name} is not a valid wheel archive") from exc
    name, version = _metadata_values(metadata_payload, source=path.name)
    if name != _DISTRIBUTION_NAME or version != package_version:
        raise ReleaseValidationError(f"{path.name} package metadata does not match the release")


def _verify_sdist(path: Path, *, package_version: str) -> None:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            if any(
                not _safe_archive_name(member.name)
                or member.issym()
                or member.islnk()
                or member.isdev()
                for member in members
            ):
                raise ReleaseValidationError(f"{path.name} contains an unsafe archive member")
            metadata_members = [member for member in members if member.name.endswith("/PKG-INFO")]
            if len(metadata_members) != 1:
                raise ReleaseValidationError(f"{path.name} must contain one PKG-INFO file")
            lockfile_members = [member for member in members if member.name.endswith("/uv.lock")]
            if len(lockfile_members) != 1 or not lockfile_members[0].isfile():
                raise ReleaseValidationError(f"{path.name} must contain one regular uv.lock file")
            extracted = archive.extractfile(metadata_members[0])
            if extracted is None:
                raise ReleaseValidationError(f"{path.name} PKG-INFO is not a regular file")
            metadata_payload = extracted.read()
    except tarfile.TarError as exc:
        raise ReleaseValidationError(f"{path.name} is not a valid source archive") from exc
    name, version = _metadata_values(metadata_payload, source=path.name)
    if name != _DISTRIBUTION_NAME or version != package_version:
        raise ReleaseValidationError(f"{path.name} package metadata does not match the release")


def _verify_sbom(path: Path, *, package_version: str) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"{path.name} is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ReleaseValidationError(f"{path.name} must contain a JSON object")
    if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.5":
        raise ReleaseValidationError(f"{path.name} is not a CycloneDX 1.5 SBOM")
    metadata = document.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(component, dict):
        raise ReleaseValidationError(f"{path.name} has no root component")
    if component.get("name") != _DISTRIBUTION_NAME or component.get("version") != package_version:
        raise ReleaseValidationError(f"{path.name} root component does not match the release")
    components = document.get("components")
    if not isinstance(components, list):
        raise ReleaseValidationError(f"{path.name} has no component list")
    names = {item.get("name") for item in components if isinstance(item, dict)}
    missing = {"duckdb", "httpx", "pydantic"} - names
    if missing:
        raise ReleaseValidationError(
            f"{path.name} is missing runtime components: {', '.join(sorted(missing))}"
        )


def artifact_paths(
    directory: Path,
    *,
    package_version: str,
    public_version: str,
) -> tuple[Path, Path, Path]:
    """Return and validate the one wheel, sdist, and SBOM expected for a release."""

    wheel = directory / f"sixsentences_engine-{package_version}-py3-none-any.whl"
    sdist = directory / f"sixsentences_engine-{package_version}.tar.gz"
    sbom = directory / f"sixsentences-engine-{public_version}.cdx.json"
    expected = {wheel.name, sdist.name, sbom.name, "RELEASE_NOTES.md"}
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    unexpected = actual - expected
    missing = expected - actual
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if unexpected:
            details.append(f"unexpected {', '.join(sorted(unexpected))}")
        raise ReleaseValidationError("release directory mismatch: " + "; ".join(details))
    _verify_wheel(wheel, package_version=package_version)
    _verify_sdist(sdist, package_version=package_version)
    _verify_sbom(sbom, package_version=package_version)
    return wheel, sdist, sbom


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finalize_artifacts(
    directory: Path,
    *,
    package_version: str,
    public_version: str,
) -> Path:
    """Validate artifacts and write their deterministic SHA-256 manifest."""

    artifacts = artifact_paths(
        directory,
        package_version=package_version,
        public_version=public_version,
    )
    checksum_path = directory / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in sorted(artifacts)),
        encoding="utf-8",
    )
    return checksum_path


def verify_checksums(directory: Path) -> None:
    """Fail closed unless SHA256SUMS exactly covers every distributable artifact."""

    checksum_path = directory / "SHA256SUMS"
    entries: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        match = _CHECKSUM_RE.fullmatch(line)
        if match is None or match.group("name") in entries:
            raise ReleaseValidationError("SHA256SUMS contains an invalid or duplicate entry")
        entries[match.group("name")] = match.group("digest")
    expected_names = {
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name not in {"RELEASE_NOTES.md", "SHA256SUMS"}
    }
    if set(entries) != expected_names:
        raise ReleaseValidationError("SHA256SUMS does not exactly cover the release artifacts")
    for name, expected in entries.items():
        if _sha256(directory / name) != expected:
            raise ReleaseValidationError(f"checksum mismatch for {name}")


def _write_github_output(path: Path, version: ReleaseVersion, source_epoch: int) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"package-version={version.package}\n")
        handle.write(f"public-version={version.public}\n")
        handle.write(f"source-date-epoch={source_epoch}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="validate tag and release metadata")
    validate.add_argument("--root", type=Path, default=Path.cwd())
    validate.add_argument("--tag", required=True)
    validate.add_argument("--main-ref", default="refs/remotes/origin/main")
    validate.add_argument("--github-output", type=Path)

    finalize = subcommands.add_parser("finalize", help="validate artifacts and write checksums")
    finalize.add_argument("--directory", required=True, type=Path)
    finalize.add_argument("--package-version", required=True)
    finalize.add_argument("--public-version", required=True)

    verify = subcommands.add_parser("verify-checksums", help="verify the release checksum manifest")
    verify.add_argument("--directory", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run release validation tooling and return a conventional exit code."""

    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            version, source_epoch = validate_repository(
                args.root.resolve(),
                tag=args.tag,
                main_ref=args.main_ref,
            )
            if args.github_output is not None:
                _write_github_output(args.github_output, version, source_epoch)
            print(f"Release metadata validated for {version.tag}.")
        elif args.command == "finalize":
            finalize_artifacts(
                args.directory.resolve(),
                package_version=args.package_version,
                public_version=args.public_version,
            )
            print("Release artifacts validated and SHA256SUMS written.")
        else:
            verify_checksums(args.directory.resolve())
            print("Release checksums verified.")
    except (OSError, ReleaseValidationError, subprocess.CalledProcessError) as exc:
        print(f"Release validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
