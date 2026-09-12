#!/usr/bin/env python3
"""Validate and finalize immutable SixSentences release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
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
_API_DISTRIBUTION_NAME: Final = "sixsentences-community-api"
_WEB_PACKAGE_NAME: Final = "@sixsentences/web"
_EXTENSION_PACKAGE_NAME: Final = "@sixsentences/browser-extension"
_ENGINE_DEPENDENCY_RE: Final = re.compile(
    r"^sixsentences[-_.]engine(?=$|[\s<>=!~;\[])",
    re.IGNORECASE,
)


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


def _read_toml_document(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseValidationError(f"cannot read valid TOML from {path}") from exc
    return document


def _read_json_document(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"cannot read valid JSON from {path}") from exc
    if not isinstance(document, dict):
        raise ReleaseValidationError(f"{path} must contain a JSON object")
    return document


def _read_project_version(path: Path) -> str:
    document = _read_toml_document(path)
    project = document.get("project")
    value = project.get("version") if isinstance(project, dict) else None
    if not isinstance(value, str):
        raise ReleaseValidationError(f"{path} project.version must be a string")
    return value


def _locked_package_version(path: Path, *, package_name: str) -> str:
    document = _read_toml_document(path)
    packages = document.get("package")
    if not isinstance(packages, list):
        raise ReleaseValidationError(f"{path} has no package list")
    matches = [
        item for item in packages if isinstance(item, dict) and item.get("name") == package_name
    ]
    if len(matches) != 1:
        raise ReleaseValidationError(f"{path} must contain exactly one {package_name} package")
    value = matches[0].get("version")
    if not isinstance(value, str):
        raise ReleaseValidationError(f"{path} {package_name} version must be a string")
    return value


def _validate_python_projects(root: Path, version: ReleaseVersion) -> None:
    root_lock_version = _locked_package_version(
        root / "uv.lock",
        package_name=_DISTRIBUTION_NAME,
    )
    if root_lock_version != version.package:
        raise ReleaseValidationError(
            f"uv.lock {_DISTRIBUTION_NAME} version {root_lock_version!r} "
            f"does not match {version.package!r}"
        )

    api_path = root / "services" / "api" / "pyproject.toml"
    api_document = _read_toml_document(api_path)
    project = api_document.get("project")
    if not isinstance(project, dict):
        raise ReleaseValidationError(f"{api_path} has no project table")
    api_version = project.get("version")
    if api_version != version.package:
        raise ReleaseValidationError(
            f"{api_path} project.version {api_version!r} does not match {version.package!r}"
        )
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ReleaseValidationError(f"{api_path} project.dependencies must be a string list")
    engine_dependencies = [
        item for item in dependencies if _ENGINE_DEPENDENCY_RE.match(item.strip())
    ]
    expected_dependency = f"{_DISTRIBUTION_NAME}=={version.package}"
    if engine_dependencies != [expected_dependency]:
        raise ReleaseValidationError(f"{api_path} must contain exactly {expected_dependency!r}")

    api_lock = root / "services" / "api" / "uv.lock"
    for package_name in (_API_DISTRIBUTION_NAME, _DISTRIBUTION_NAME):
        locked_version = _locked_package_version(api_lock, package_name=package_name)
        if locked_version != version.package:
            raise ReleaseValidationError(
                f"{api_lock} {package_name} version {locked_version!r} "
                f"does not match {version.package!r}"
            )


def _validate_npm_project(
    root: Path,
    *,
    directory: str,
    package_name: str,
    expected_version: str,
) -> None:
    project_root = root / "apps" / directory
    package_path = project_root / "package.json"
    package = _read_json_document(package_path)
    if package.get("name") != package_name:
        raise ReleaseValidationError(f"{package_path} name does not match {package_name!r}")
    if package.get("version") != expected_version:
        raise ReleaseValidationError(
            f"{package_path} version {package.get('version')!r} does not match {expected_version!r}"
        )

    lock_path = project_root / "package-lock.json"
    lock = _read_json_document(lock_path)
    packages = lock.get("packages")
    locked_root = packages.get("") if isinstance(packages, dict) else None
    if not isinstance(locked_root, dict):
        raise ReleaseValidationError(f"{lock_path} has no root package entry")
    for source, name, value in (
        (lock_path, "name", lock.get("name")),
        (lock_path, "version", lock.get("version")),
        (lock_path, "root package name", locked_root.get("name")),
        (lock_path, "root package version", locked_root.get("version")),
    ):
        expected = package_name if "name" in name else expected_version
        if value != expected:
            raise ReleaseValidationError(f"{source} {name} {value!r} does not match {expected!r}")


def _read_swift_static_string(path: Path, *, name: str) -> str:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseValidationError(f"cannot read {path}") from exc
    pattern = re.compile(
        rf'^\s*static\s+let\s+{re.escape(name)}\s*=\s*"([^"\\]+)"\s*$',
        re.MULTILINE,
    )
    matches = pattern.findall(source)
    if len(matches) != 1:
        raise ReleaseValidationError(f"{path} must declare one literal static let {name}")
    return matches[0]


def _validate_companion_version(root: Path) -> None:
    plist_path = root / "apps" / "companion-macos" / "AppBundle" / "Info.plist"
    try:
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        raise ReleaseValidationError(f"cannot read valid plist from {plist_path}") from exc
    swift_path = (
        root
        / "apps"
        / "companion-macos"
        / "Sources"
        / "SixSentencesCompanion"
        / "CompanionRelease.swift"
    )
    comparisons = (
        (
            "CFBundleShortVersionString",
            "release",
            _read_swift_static_string(swift_path, name="release"),
        ),
        ("CFBundleVersion", "build", _read_swift_static_string(swift_path, name="build")),
    )
    for plist_key, swift_name, swift_value in comparisons:
        plist_value = info.get(plist_key) if isinstance(info, dict) else None
        if not isinstance(plist_value, str) or not plist_value:
            raise ReleaseValidationError(f"{plist_path} {plist_key} must be a non-empty string")
        if plist_value != swift_value:
            raise ReleaseValidationError(
                f"Companion {plist_key} {plist_value!r} does not match "
                f"CompanionApplicationVersion.{swift_name} {swift_value!r}"
            )


def validate_component_versions(root: Path, version: ReleaseVersion) -> None:
    """Fail closed unless every released component has coherent version metadata."""

    _validate_python_projects(root, version)
    _validate_npm_project(
        root,
        directory="web",
        package_name=_WEB_PACKAGE_NAME,
        expected_version=version.public,
    )
    _validate_npm_project(
        root,
        directory="browser-extension",
        package_name=_EXTENSION_PACKAGE_NAME,
        expected_version=version.public,
    )
    _validate_companion_version(root)


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
    version = release_version(_read_project_version(root / "pyproject.toml"))
    if tag != version.tag:
        raise ReleaseValidationError(f"tag {tag!r} does not match expected {version.tag!r}")
    validate_component_versions(root, version)
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
