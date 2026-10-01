#!/usr/bin/env python3
"""Verify a complete release bundle before an immutable draft is published."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

REPOSITORY = "SixSentences/sixsentences"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
PREVIEW = "sixsentences-overview.gif"
IMAGE_NAMES = frozenset(
    {"IMAGE_DIGESTS", "IMAGE_SHA256SUMS", "images.json", "runtime-rehearsal.json"}
    | {
        f"{kind}.{suffix}.json"
        for kind in ("api", "web")
        for suffix in ("identity", "sbom", "provenance")
    }
)
RUNTIME_GATES = (
    "fresh_start",
    "upgrade",
    "backup_restore",
    "erasure_replay",
    "preupgrade_snapshot_rollback",
    "synthetic_only",
    "synthetic_corpus_bootstrap",
    "fresh_candidate_without_corpus",
)


class BundleValidationError(ValueError):
    """The complete draft bundle is missing, malformed, or inconsistent."""


def _helper(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_bundle_{name}", Path(__file__).with_name(f"{name}.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RELEASE = _helper("release")
IMAGES = _helper("image_evidence")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleValidationError(message)


def _object(value: object) -> dict[str, Any]:
    _require(isinstance(value, dict), "expected a JSON object")
    assert isinstance(value, dict)
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _json(payload: bytes) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise BundleValidationError("non-finite JSON value")

    return _object(
        json.loads(payload, object_pairs_hook=_unique_object, parse_constant=reject_constant)
    )


def _read(path: Path, *, limit: int = MAX_FILE_BYTES) -> bytes:
    info = path.lstat()
    _require(
        stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit, "invalid bounded regular file"
    )
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
        opened = os.fstat(handle.fileno())
        _require(
            stat.S_ISREG(opened.st_mode)
            and (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
            "file changed during validation",
        )
        payload = handle.read(limit + 1)
    _require(len(payload) == info.st_size, "file changed during validation")
    return payload


def _directory(directory: Path, names: set[str] | frozenset[str]) -> dict[str, bytes]:
    _require(stat.S_ISDIR(directory.lstat().st_mode), "invalid evidence directory")
    _require(
        {path.name for path in directory.iterdir()} == names, "evidence file inventory mismatch"
    )
    result: dict[str, bytes] = {}
    total = 0
    for name in sorted(names):
        total += (directory / name).lstat().st_size
        _require(total <= MAX_TOTAL_BYTES, "oversized evidence bundle")
        result[name] = _read(directory / name)
    return result


def _checksums(payload: bytes, files: dict[str, bytes], expected: set[str]) -> None:
    _require(len(payload) <= 32768, "oversized checksum manifest")
    entries: dict[str, str] = {}
    for line in payload.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9_.-]*)", line)
        _require(match is not None, "malformed checksum entry")
        assert match is not None
        digest, name = match.groups()
        _require(name not in entries and name in expected, "duplicate or unexpected checksum entry")
        entries[name] = digest
    _require(set(entries) == expected, "incomplete checksum manifest")
    for name, digest in entries.items():
        _require(hashlib.sha256(files[name]).hexdigest() == digest, "checksum mismatch")


def _archive_bounds(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.infolist()
        _require(len(entries) <= 10000, "oversized wheel inventory")
        _require(
            len({entry.filename for entry in entries}) == len(entries), "duplicate wheel entry"
        )
        _require(
            sum(entry.file_size for entry in entries) <= MAX_TOTAL_BYTES, "oversized wheel contents"
        )
        _require(
            not any(stat.S_ISLNK(entry.external_attr >> 16) for entry in entries), "wheel symlink"
        )
    with tarfile.open(sdist, "r:gz") as archive:
        names: set[str] = set()
        total = 0
        for member in archive:
            _require(member.name not in names, "duplicate sdist entry")
            names.add(member.name)
            total += member.size
            _require(len(names) <= 10000 and total <= MAX_TOTAL_BYTES, "oversized sdist contents")


def _image_evidence(files: dict[str, bytes], root: Path, tag: str, revision: str) -> None:
    _checksums(files["IMAGE_SHA256SUMS"], files, set(IMAGE_NAMES) - {"IMAGE_SHA256SUMS"})
    refs = files["IMAGE_DIGESTS"].decode("utf-8").splitlines()
    _require(len(refs) == 2, "expected exactly two image references")
    expected_images: dict[str, object] = {}
    for kind, reference in zip(("api", "web"), refs, strict=True):
        _require(
            re.fullmatch(
                rf"ghcr\.io/sixsentences/community-{kind}@sha256:[0-9a-f]{{64}}", reference
            )
            is not None,
            "invalid image reference",
        )
        identity = _json(files[f"{kind}.identity.json"])
        _require(
            identity
            == {
                "org.opencontainers.image.revision": revision,
                "org.opencontainers.image.version": tag,
                "org.opencontainers.image.source": f"https://github.com/{REPOSITORY}",
            },
            "image identity mismatch",
        )
        sbom = IMAGES._predicate(_json(files[f"{kind}.sbom.json"]), "SPDX")
        _require(
            sbom.get("spdxVersion") == "SPDX-2.3"
            and isinstance(sbom.get("packages"), list)
            and bool(sbom["packages"]),
            "invalid SPDX inventory",
        )
        provenance = IMAGES._predicate(_json(files[f"{kind}.provenance.json"]), "SLSA")
        IMAGES._validate_buildkit_provenance(provenance)
        if "buildDefinition" in provenance:
            arguments = provenance["buildDefinition"]["externalParameters"]["request"]["args"]
            vcs = provenance["runDetails"]["metadata"].get("buildkit_metadata", {}).get("vcs")
        else:
            arguments = provenance["invocation"]["parameters"]["args"]
            vcs = (
                provenance["metadata"]
                .get("https://mobyproject.org/buildkit@v1#metadata", {})
                .get("vcs")
            )
        for label, value in identity.items():
            _require(arguments.get(f"label:{label}") == value, "provenance label mismatch")
        vcs = _object(vcs)
        _require(
            vcs.get("revision") == revision
            and vcs.get("source") == f"https://github.com/{REPOSITORY}",
            "provenance source mismatch",
        )
        expected_images[kind] = {"reference": reference, "platform": "linux/amd64"}
    receipt = _json(files["images.json"])
    _require(
        set(receipt)
        == {"schema_version", "tag", "source_revision", "web_origin", "images", "reproducibility"},
        "unexpected image receipt fields",
    )
    _require(
        type(receipt.get("schema_version")) is int
        and receipt["schema_version"] == 1
        and receipt.get("tag") == tag
        and receipt.get("source_revision") == revision
        and receipt.get("web_origin") == "http://localhost"
        and receipt.get("images") == expected_images,
        "image receipt mismatch",
    )
    _require(
        receipt.get("reproducibility")
        == "Locked source and recorded inputs; byte-identical rebuilds are not asserted.",
        "invalid reproducibility scope",
    )
    runtime = _json(files["runtime-rehearsal.json"])
    expected_fields = set(RUNTIME_GATES) | {
        "schema_version",
        "source_revision",
        "seed",
        "baseline_image",
        "baseline_bootstrap",
        "api_reference",
        "web_reference",
        "api_image",
        "web_image",
        "rollback_database_revision",
        "rollback_data_lossless",
        "schema_downgrade",
        "provider_calls",
    }
    _require(set(runtime) == expected_fields, "unexpected runtime receipt fields")
    _require(
        all(runtime.get(key) is True for key in RUNTIME_GATES), "unsuccessful runtime rehearsal"
    )
    _require(
        type(runtime.get("schema_version")) is int
        and runtime["schema_version"] == 1
        and runtime.get("source_revision") == revision,
        "runtime source mismatch",
    )
    _require(
        type(runtime.get("seed")) is int
        and runtime["seed"] == 42
        and type(runtime.get("provider_calls")) is int
        and runtime["provider_calls"] == 0,
        "runtime seed or provider calls mismatch",
    )
    baseline = _json(_read(root / "deploy/community/alpha1-baseline-compatibility.json"))
    _require(
        runtime.get("baseline_bootstrap") == baseline
        and runtime.get("rollback_database_revision") == baseline.get("target_database_revision")
        and runtime.get("rollback_data_lossless") is False
        and runtime.get("schema_downgrade") is False,
        "runtime rollback contract mismatch",
    )
    _require(
        isinstance(runtime.get("baseline_image"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["baseline_image"]) is not None,
        "invalid runtime baseline image identity",
    )
    for kind, reference in zip(("api", "web"), refs, strict=True):
        _require(runtime.get(f"{kind}_reference") == reference, "runtime image reference mismatch")
        _require(
            isinstance(runtime.get(f"{kind}_image"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", runtime[f"{kind}_image"]) is not None,
            "invalid runtime image identity",
        )


def expected_names(tag: str) -> set[str]:
    """Return exactly the fifteen allowed public assets for a canonical tag."""
    match = re.fullmatch(
        r"v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))(?:-(alpha|beta|rc)\.(0|[1-9][0-9]*))?",
        tag,
    )
    _require(match is not None, "invalid release tag")
    assert match is not None
    base, kind, number = match.groups()
    package = base + ({"alpha": "a", "beta": "b", "rc": "rc"}[kind] + number if kind else "")
    return set(IMAGE_NAMES) | {
        PREVIEW,
        "SHA256SUMS",
        f"sixsentences_engine-{package}-py3-none-any.whl",
        f"sixsentences_engine-{package}.tar.gz",
        f"sixsentences-engine-{tag[1:]}.cdx.json",
    }


def build_manifest(
    *,
    root: Path,
    engine_dir: Path,
    image_dir: Path,
    preview: Path,
    tag: str,
    revision: str,
    release_id: int,
    commit_date: date,
) -> dict[str, Any]:
    """Validate local inputs without synthesizing image build or runtime receipts."""
    _require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "invalid source revision")
    _require(type(release_id) is int and release_id > 0, "invalid release ID")
    version = RELEASE.validate_files(root, tag=tag, commit_date=commit_date)
    engine_names = expected_names(tag) - set(IMAGE_NAMES) - {PREVIEW}
    engine = _directory(engine_dir, engine_names | {"RELEASE_NOTES.md"})
    _checksums(engine["SHA256SUMS"], engine, engine_names - {"SHA256SUMS"})
    wheel = engine_dir / f"sixsentences_engine-{version.package}-py3-none-any.whl"
    sdist = engine_dir / f"sixsentences_engine-{version.package}.tar.gz"
    _archive_bounds(wheel, sdist)
    RELEASE._verify_wheel(wheel, package_version=version.package)
    RELEASE._verify_sdist(sdist, package_version=version.package)
    RELEASE._verify_sbom(
        engine_dir / f"sixsentences-engine-{version.public}.cdx.json",
        package_version=version.package,
    )
    _require(
        engine.pop("RELEASE_NOTES.md") == _read(root / "docs/releases" / f"{tag}.md"),
        "release notes mismatch",
    )
    images = _directory(image_dir, IMAGE_NAMES)
    _image_evidence(images, root, tag, revision)
    preview_payload = _read(preview)
    _require(
        preview.name == PREVIEW and preview_payload[:6] in (b"GIF87a", b"GIF89a"),
        "invalid preview GIF",
    )
    _checksums(
        _read(root / "docs/assets/sixsentences-overview.sha256", limit=32768),
        {PREVIEW: preview_payload},
        {PREVIEW},
    )
    files = engine | images | {PREVIEW: preview_payload}
    _require(sum(map(len, files.values())) <= MAX_TOTAL_BYTES, "oversized complete bundle")
    manifest = {
        "schema_version": 1,
        "release_id": release_id,
        "tag": tag,
        "source_revision": revision,
        "assets": [
            {"name": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in sorted(files.items())
        ],
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Require the bounded, exact-name contract consumed by remote validation."""
    _require(
        set(manifest) == {"schema_version", "release_id", "tag", "source_revision", "assets"},
        "invalid bundle manifest fields",
    )
    _require(
        type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
        "invalid bundle schema",
    )
    _require(
        type(manifest.get("release_id")) is int and manifest["release_id"] > 0, "invalid release ID"
    )
    _require(
        isinstance(manifest.get("source_revision"), str)
        and re.fullmatch(r"[0-9a-f]{40}", manifest["source_revision"]) is not None,
        "invalid source revision",
    )
    tag = manifest.get("tag")
    _require(isinstance(tag, str), "invalid tag")
    assert isinstance(tag, str)
    assets = manifest.get("assets")
    _require(isinstance(assets, list) and len(assets) == 15, "expected fifteen bundle assets")
    assert isinstance(assets, list)
    result: dict[str, dict[str, Any]] = {}
    for value in assets:
        asset = _object(value)
        _require(set(asset) == {"name", "size", "sha256"}, "invalid asset contract")
        name = asset.get("name")
        _require(
            isinstance(name, str) and name in expected_names(tag) and name not in result,
            "invalid or duplicate asset name",
        )
        _require(
            type(asset.get("size")) is int and 0 < asset["size"] <= MAX_FILE_BYTES,
            "invalid asset size",
        )
        _require(
            isinstance(asset.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) is not None,
            "invalid asset digest",
        )
        assert isinstance(name, str)
        result[name] = asset
    _require(set(result) == expected_names(tag), "incomplete asset contract")
    _require(
        sum(int(asset["size"]) for asset in result.values()) <= MAX_TOTAL_BYTES,
        "oversized bundle contract",
    )
    return result


def validate_remote_release(manifest: dict[str, Any], document: dict[str, Any]) -> dict[str, int]:
    """Bind the full uploaded asset inventory to the same still-editable draft."""
    expected = validate_manifest(manifest)
    _require(
        type(document.get("id")) is int
        and document["id"] == manifest["release_id"]
        and document.get("tag_name") == manifest["tag"],
        "remote release identity mismatch",
    )
    _require(
        document.get("draft") is True and document.get("immutable") is False,
        "release must remain a mutable draft",
    )
    assets = document.get("assets")
    _require(isinstance(assets, list) and len(assets) == 15, "remote asset inventory mismatch")
    assert isinstance(assets, list)
    ids: dict[str, int] = {}
    for value in assets:
        asset = _object(value)
        name = asset.get("name")
        _require(
            isinstance(name, str) and name in expected and name not in ids,
            "unexpected or duplicate remote asset",
        )
        assert isinstance(name, str)
        asset_id = asset.get("id")
        _require(
            type(asset_id) is int and asset_id > 0 and asset_id not in ids.values(),
            "invalid or duplicate remote asset ID",
        )
        _require(
            asset.get("state") == "uploaded"
            and type(asset.get("size")) is int
            and asset["size"] == expected[name]["size"]
            and asset.get("digest") == f"sha256:{expected[name]['sha256']}",
            "remote asset state, size or digest mismatch",
        )
        assert isinstance(asset_id, int)
        ids[name] = asset_id
    return ids


def validate_downloads(manifest: dict[str, Any], directory: Path) -> None:
    """Check actual freshly downloaded bytes, not only GitHub asset metadata."""
    expected = validate_manifest(manifest)
    files = _directory(directory, set(expected))
    for name, payload in files.items():
        _require(
            len(payload) == expected[name]["size"]
            and hashlib.sha256(payload).hexdigest() == expected[name]["sha256"],
            "downloaded asset mismatch",
        )


def _download_asset(repository: str, asset_id: int, target: Path, expected_size: int) -> None:
    command = [
        "gh",
        "api",
        "--hostname",
        "github.com",
        "--header",
        "Accept: application/octet-stream",
        f"repos/{repository}/releases/assets/{asset_id}",
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        assert process.stdout is not None
        try:
            deadline = time.monotonic() + 120
            size = 0
            with selectors.DefaultSelector() as selector, target.open("xb") as handle:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    _require(
                        remaining > 0 and bool(selector.select(remaining)),
                        "asset download timed out",
                    )
                    chunk = os.read(process.stdout.fileno(), min(65536, expected_size - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    _require(size <= expected_size, "oversized asset download")
                    handle.write(chunk)
            _require(
                process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0
                and size == expected_size,
                "incomplete asset download",
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def verify_remote_bundle(repository: str, manifest: dict[str, Any]) -> None:
    """Read GitHub twice around a download into a new empty private directory."""
    _require(repository == REPOSITORY, "unexpected release repository")
    assets = validate_manifest(manifest)
    command = [
        "gh",
        "api",
        "--hostname",
        "github.com",
        f"repos/{repository}/releases/{manifest['release_id']}",
    ]

    def read_release() -> dict[str, object]:
        payload = subprocess.run(command, check=True, capture_output=True, timeout=60).stdout
        _require(len(payload) <= 1024 * 1024, "oversized remote release metadata")
        return _json(payload)

    before = validate_remote_release(manifest, read_release())
    with tempfile.TemporaryDirectory(prefix="six-release-readback-") as temporary:
        directory = Path(temporary)
        _require(not any(directory.iterdir()), "download directory is not empty")
        for name, asset_id in sorted(before.items()):
            _download_asset(repository, asset_id, directory / name, assets[name]["size"])
        validate_downloads(manifest, directory)
        after = validate_remote_release(manifest, read_release())
        _require(before == after, "remote asset IDs changed during readback")


def main(argv: list[str] | None = None) -> int:
    """Validate local or remote draft assets; never publish or replace anything."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("verify-local")
    for name in ("root", "engine-dir", "image-dir", "preview", "out"):
        local.add_argument(f"--{name}", type=Path, required=True)
    local.add_argument("--tag", required=True)
    local.add_argument("--revision", required=True)
    local.add_argument("--release-id", required=True, type=int)
    remote = commands.add_parser("verify-remote")
    remote.add_argument("--repo", required=True)
    remote.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-local":
            head = subprocess.run(
                ["git", "-C", str(args.root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            _require(head == args.revision, "checkout does not match verified source")
            timestamp = subprocess.run(
                ["git", "-C", str(args.root), "show", "-s", "--format=%cI", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            manifest = build_manifest(
                root=args.root,
                engine_dir=args.engine_dir,
                image_dir=args.image_dir,
                preview=args.preview,
                tag=args.tag,
                revision=args.revision,
                release_id=args.release_id,
                commit_date=datetime.fromisoformat(timestamp).astimezone(UTC).date(),
            )
            with args.out.open("x", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2, sort_keys=True)
                handle.write("\n")
        else:
            verify_remote_bundle(args.repo, _json(_read(args.manifest, limit=32768)))
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ):
        print("Release bundle validation failed; publication remains blocked.", file=sys.stderr)
        return 1
    print("Complete fifteen-asset release bundle verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
