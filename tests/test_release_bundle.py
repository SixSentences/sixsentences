"""Synthetic contracts for complete immutable-release draft publication."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_image_evidence import provenance_fixture
from test_repository_automation import _write_release_metadata_tree, _write_sdist

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_bundle_tests", ROOT / ".github/scripts/release_bundle.py"
)
assert SPEC is not None and SPEC.loader is not None
BUNDLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUNDLE)
TAG = "v0.1.0-alpha.1"
REVISION = "c" * 40


def write_json(path: Path, value: object) -> None:
    """Write only synthetic fixture data."""
    path.write_text(json.dumps(value), encoding="utf-8")


def checksums(directory: Path, name: str, excluded: set[str]) -> None:
    """Regenerate fixture checksums after an intentional semantic mutation."""
    paths = [path for path in sorted(directory.iterdir()) if path.name not in excluded | {name}]
    (directory / name).write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in paths)
    )


@pytest.fixture
def bundle_inputs(tmp_path: Path) -> dict[str, object]:
    """Build an inert source tree and all fifteen public assets, without provider calls."""
    root = tmp_path / "source"
    root.mkdir()
    _write_release_metadata_tree(root)
    engine = tmp_path / "engine"
    images = tmp_path / "images"
    preview = tmp_path / BUNDLE.PREVIEW
    engine.mkdir()
    images.mkdir()
    wheel = engine / "sixsentences_engine-0.1.0a1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "sixsentences_engine-0.1.0a1.dist-info/METADATA",
            "Name: sixsentences-engine\nVersion: 0.1.0a1\n",
        )
        archive.writestr("sixsentences/py.typed", "")
    _write_sdist(engine / "sixsentences_engine-0.1.0a1.tar.gz", include_lockfile=True)
    write_json(
        engine / "sixsentences-engine-0.1.0-alpha.1.cdx.json",
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "metadata": {"component": {"name": "sixsentences-engine", "version": "0.1.0a1"}},
            "components": [{"name": name} for name in ("duckdb", "httpx", "pydantic")],
        },
    )
    (engine / "RELEASE_NOTES.md").write_text("# Release\n")
    checksums(engine, "SHA256SUMS", {"RELEASE_NOTES.md"})
    preview.write_bytes(b"GIF89a-synthetic-preview")
    (root / "docs/assets").mkdir()
    (root / "docs/assets/sixsentences-overview.sha256").write_text(
        f"{hashlib.sha256(preview.read_bytes()).hexdigest()}  {BUNDLE.PREVIEW}\n"
    )
    references = [
        f"ghcr.io/sixsentences/community-{kind}@sha256:{letter * 64}"
        for kind, letter in (("api", "a"), ("web", "b"))
    ]
    (images / "IMAGE_DIGESTS").write_text("\n".join(references) + "\n")
    for kind in ("api", "web"):
        identity = {
            "org.opencontainers.image.revision": REVISION,
            "org.opencontainers.image.version": TAG,
            "org.opencontainers.image.source": "https://github.com/SixSentences/sixsentences",
        }
        write_json(images / f"{kind}.identity.json", identity)
        write_json(
            images / f"{kind}.sbom.json",
            {
                "linux/amd64": {
                    "SPDX": {"spdxVersion": "SPDX-2.3", "packages": [{"name": "synthetic"}]}
                }
            },
        )
        provenance = provenance_fixture()
        provenance["buildDefinition"]["externalParameters"]["request"]["args"] = {
            f"label:{key}": value for key, value in identity.items()
        }
        provenance["runDetails"]["metadata"]["buildkit_metadata"] = {
            "vcs": {"source": "https://github.com/SixSentences/sixsentences", "revision": REVISION}
        }
        write_json(images / f"{kind}.provenance.json", {"linux/amd64": {"SLSA": provenance}})
    baseline = {
        "original_api_reference": "ghcr.io/sixsentences/community-api@sha256:" + "e" * 64,
        "target_database_revision": "synthetic_baseline",
        "legacy_corpus_fixture": {"mode": "synthetic_duckdb_build", "seed": 42, "records": 1},
    }
    (root / "deploy/community").mkdir(parents=True)
    write_json(root / "deploy/community/alpha1-baseline-compatibility.json", baseline)
    runtime = {
        **dict.fromkeys(BUNDLE.RUNTIME_GATES, True),
        "schema_version": 1,
        "source_revision": REVISION,
        "seed": 42,
        "provider_calls": 0,
        "baseline_image": "sha256:" + "1" * 64,
        "baseline_bootstrap": baseline,
        "api_reference": references[0],
        "web_reference": references[1],
        "api_image": "sha256:" + "f" * 64,
        "web_image": "sha256:" + "d" * 64,
        "rollback_database_revision": "synthetic_baseline",
        "rollback_data_lossless": False,
        "schema_downgrade": False,
    }
    write_json(images / "runtime-rehearsal.json", runtime)
    write_json(
        images / "images.json",
        {
            "schema_version": 1,
            "tag": TAG,
            "source_revision": REVISION,
            "web_origin": "http://localhost",
            "images": {
                kind: {"reference": reference, "platform": "linux/amd64"}
                for kind, reference in zip(("api", "web"), references, strict=True)
            },
            "reproducibility": (
                "Locked source and recorded inputs; byte-identical rebuilds are not asserted."
            ),
        },
    )
    checksums(images, "IMAGE_SHA256SUMS", set())
    return {
        "root": root,
        "engine_dir": engine,
        "image_dir": images,
        "preview": preview,
        "tag": TAG,
        "revision": REVISION,
        "release_id": 123,
        "commit_date": date(2026, 9, 12),
    }


def remote_document(manifest: dict[str, object]) -> dict[str, object]:
    """Represent GitHub's draft response without network access or account data."""
    return {
        "id": 123,
        "tag_name": TAG,
        "target_commitish": "main",
        "draft": True,
        "immutable": False,
        "assets": [
            {
                "id": index,
                "name": asset["name"],
                "size": asset["size"],
                "state": "uploaded",
                "digest": f"sha256:{asset['sha256']}",
            }
            for index, asset in enumerate(manifest["assets"], 1)
        ],
    }


def copy_assets(inputs: dict[str, object], target: Path) -> None:
    """Copy synthetic public fixtures into a new mock download directory."""
    for key in ("engine_dir", "image_dir"):
        for path in inputs[key].iterdir():
            if path.name != "RELEASE_NOTES.md":
                shutil.copyfile(path, target / path.name)
    shutil.copyfile(inputs["preview"], target / BUNDLE.PREVIEW)


def test_complete_local_remote_and_actual_bytes(
    bundle_inputs: dict[str, object], tmp_path: Path
) -> None:
    manifest = BUNDLE.build_manifest(**bundle_inputs)
    assert len(manifest["assets"]) == 15
    assert "RELEASE_NOTES.md" not in {asset["name"] for asset in manifest["assets"]}
    assert len(BUNDLE.validate_remote_release(manifest, remote_document(manifest))) == 15
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    copy_assets(bundle_inputs, downloads)
    BUNDLE.validate_downloads(manifest, downloads)


def test_baseline_config_id_is_distinct_from_pinned_registry_reference(
    bundle_inputs: dict[str, object],
) -> None:
    runtime = json.loads((bundle_inputs["image_dir"] / "runtime-rehearsal.json").read_text())
    assert runtime["baseline_image"] == "sha256:" + "1" * 64
    assert runtime["baseline_bootstrap"]["original_api_reference"].endswith("e" * 64)
    assert BUNDLE.build_manifest(**bundle_inputs)["source_revision"] == REVISION


@pytest.mark.parametrize(
    "mutation",
    [
        "bad_id",
        "bad_revision",
        "bad_tag",
        "extra_field",
        "duplicate",
        "traversal",
        "size",
        "digest",
    ],
)
def test_manifest_is_bounded_and_cannot_redirect_remote_checks(
    bundle_inputs: dict[str, object], mutation: str
) -> None:
    manifest = BUNDLE.build_manifest(**bundle_inputs)
    if mutation == "bad_id":
        manifest["release_id"] = True
    elif mutation == "bad_revision":
        manifest["source_revision"] = "main"
    elif mutation == "bad_tag":
        manifest["tag"] = "../other"
    elif mutation == "extra_field":
        manifest["url"] = "https://example.invalid"
    elif mutation == "duplicate":
        manifest["assets"][0] = copy.deepcopy(manifest["assets"][1])
    elif mutation == "traversal":
        manifest["assets"][0]["name"] = "../escape"
    elif mutation == "size":
        manifest["assets"][0]["size"] = BUNDLE.MAX_FILE_BYTES + 1
    else:
        manifest["assets"][0]["sha256"] = "no-digest"
    with pytest.raises(ValueError):
        BUNDLE.validate_manifest(manifest)


@pytest.mark.parametrize("kind", ["engine_dir", "image_dir"])
@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink", "directory", "oversized"])
def test_local_inventory_and_file_safety(
    bundle_inputs: dict[str, object], kind: str, mutation: str
) -> None:
    directory = bundle_inputs[kind]
    selected = directory / ("SHA256SUMS" if kind == "engine_dir" else "IMAGE_DIGESTS")
    if mutation == "extra":
        (directory / "api.build.json").write_text("{}")
    elif mutation == "missing":
        selected.unlink()
    elif mutation == "symlink":
        selected.unlink()
        selected.symlink_to(bundle_inputs["preview"])
    elif mutation == "directory":
        selected.unlink()
        selected.mkdir()
    else:
        with selected.open("wb") as handle:
            handle.truncate(BUNDLE.MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError):
        BUNDLE.build_manifest(**bundle_inputs)


@pytest.mark.parametrize(
    "key,value",
    [
        ("seed", 0),
        ("seed", True),
        ("provider_calls", 1),
        ("provider_calls", False),
        ("baseline_bootstrap", {}),
        ("baseline_image", "other"),
        ("source_revision", "d" * 40),
        ("api_reference", "other"),
        ("api_image", "not-a-digest"),
        ("rollback_database_revision", "other"),
        ("rollback_data_lossless", True),
        ("schema_downgrade", True),
        ("unexpected_private_data", "synthetic"),
    ],
)
def test_runtime_contract_drift_fails(
    bundle_inputs: dict[str, object], key: str, value: object
) -> None:
    directory = bundle_inputs["image_dir"]
    runtime = json.loads((directory / "runtime-rehearsal.json").read_text())
    runtime[key] = value
    write_json(directory / "runtime-rehearsal.json", runtime)
    checksums(directory, "IMAGE_SHA256SUMS", set())
    with pytest.raises(ValueError):
        BUNDLE.build_manifest(**bundle_inputs)


@pytest.mark.parametrize("gate", BUNDLE.RUNTIME_GATES)
@pytest.mark.parametrize("value", [False, 1, None, "true"])
def test_all_eight_runtime_gates_require_true(
    bundle_inputs: dict[str, object], gate: str, value: object
) -> None:
    directory = bundle_inputs["image_dir"]
    runtime = json.loads((directory / "runtime-rehearsal.json").read_text())
    runtime[gate] = value
    write_json(directory / "runtime-rehearsal.json", runtime)
    checksums(directory, "IMAGE_SHA256SUMS", set())
    with pytest.raises(ValueError, match="runtime rehearsal"):
        BUNDLE.build_manifest(**bundle_inputs)


@pytest.mark.parametrize(
    "filename,mutation",
    [
        ("images.json", "tag"),
        ("images.json", "source_revision"),
        ("api.identity.json", "org.opencontainers.image.revision"),
        ("web.identity.json", "org.opencontainers.image.source"),
    ],
)
def test_image_receipts_bind_source_and_tag(
    bundle_inputs: dict[str, object], filename: str, mutation: str
) -> None:
    directory = bundle_inputs["image_dir"]
    document = json.loads((directory / filename).read_text())
    document[mutation] = "mismatch"
    write_json(directory / filename, document)
    checksums(directory, "IMAGE_SHA256SUMS", set())
    with pytest.raises(ValueError):
        BUNDLE.build_manifest(**bundle_inputs)


@pytest.mark.parametrize(
    "mutation",
    [
        "labels",
        "vcs",
        "empty_sbom",
        "duplicate_json",
        "nonfinite_json",
        "path_traversal_checksum",
        "duplicate_checksum",
        "bad_checksum",
        "notes",
        "package_version",
        "preview",
        "symlink_preview",
    ],
)
def test_semantic_and_malformed_inputs_fail(
    bundle_inputs: dict[str, object], mutation: str
) -> None:
    images, engine = bundle_inputs["image_dir"], bundle_inputs["engine_dir"]
    if mutation in ("labels", "vcs"):
        path = images / "api.provenance.json"
        document = json.loads(path.read_text())
        provenance = document["linux/amd64"]["SLSA"]
        if mutation == "labels":
            provenance["buildDefinition"]["externalParameters"]["request"]["args"][
                "label:org.opencontainers.image.version"
            ] = "other"
        else:
            provenance["runDetails"]["metadata"]["buildkit_metadata"]["vcs"]["revision"] = "d" * 40
        write_json(path, document)
    elif mutation == "empty_sbom":
        write_json(images / "api.sbom.json", {"SPDX": {"spdxVersion": "SPDX-2.3", "packages": []}})
    elif mutation in ("duplicate_json", "nonfinite_json"):
        (images / "images.json").write_text(
            '{"tag":1,"tag":2}' if mutation == "duplicate_json" else '{"value":NaN}'
        )
    elif mutation == "notes":
        (engine / "RELEASE_NOTES.md").write_text("wrong notes")
    elif mutation == "package_version":
        with zipfile.ZipFile(
            engine / "sixsentences_engine-0.1.0a1-py3-none-any.whl", "w"
        ) as archive:
            archive.writestr(
                "sixsentences_engine-0.1.0a1.dist-info/METADATA",
                "Name: sixsentences-engine\nVersion: 9.9.9\n",
            )
            archive.writestr("sixsentences/py.typed", "")
    elif mutation == "preview":
        bundle_inputs["preview"].write_bytes(b"GIF89a-wrong")
    elif mutation == "symlink_preview":
        bundle_inputs["preview"].unlink()
        bundle_inputs["preview"].symlink_to(engine / "RELEASE_NOTES.md")
    checksums(images, "IMAGE_SHA256SUMS", set())
    checksums(engine, "SHA256SUMS", {"RELEASE_NOTES.md"})
    checksum_path = images / "IMAGE_SHA256SUMS"
    if mutation == "path_traversal_checksum":
        checksum_path.write_text("a" * 64 + "  ../escape\n")
    elif mutation == "duplicate_checksum":
        checksum_path.write_text(checksum_path.read_text() * 2)
    elif mutation == "bad_checksum":
        checksum_path.write_text(checksum_path.read_text().replace(" ", "x", 1))
    with pytest.raises(ValueError):
        BUNDLE.build_manifest(**bundle_inputs)


@pytest.mark.parametrize(
    "key,value",
    [
        ("draft", False),
        ("draft", 1),
        ("immutable", True),
        ("immutable", 0),
        ("id", 124),
        ("id", True),
        ("tag_name", "v0.1.0-alpha.2"),
    ],
)
def test_remote_release_identity_and_mutability(
    bundle_inputs: dict[str, object], key: str, value: object
) -> None:
    manifest = BUNDLE.build_manifest(**bundle_inputs)
    remote = remote_document(manifest)
    remote[key] = value
    with pytest.raises(ValueError):
        BUNDLE.validate_remote_release(manifest, remote)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "duplicate_name",
        "duplicate_id",
        "path_traversal",
        "partial",
        "digest",
        "size",
        "boolean_size",
        "unhashed",
    ],
)
def test_remote_assets_are_exact_unique_uploaded_and_hashed(
    bundle_inputs: dict[str, object], mutation: str
) -> None:
    manifest = BUNDLE.build_manifest(**bundle_inputs)
    remote = remote_document(manifest)
    assets = remote["assets"]
    if mutation == "missing":
        assets.pop()
    elif mutation == "extra":
        assets.append(copy.deepcopy(assets[0]))
    elif mutation == "duplicate_name":
        assets[0]["name"] = assets[1]["name"]
    elif mutation == "duplicate_id":
        assets[0]["id"] = assets[1]["id"]
    elif mutation == "path_traversal":
        assets[0]["name"] = "../escape"
    elif mutation == "partial":
        assets[0]["state"] = "starter"
    elif mutation == "digest":
        assets[0]["digest"] = "sha256:" + "0" * 64
    elif mutation == "size":
        assets[0]["size"] += 1
    elif mutation == "boolean_size":
        assets[0]["size"] = True
    else:
        assets[0].pop("digest")
    with pytest.raises(ValueError):
        BUNDLE.validate_remote_release(manifest, remote)


@pytest.mark.parametrize("mutation", ["bytes", "missing", "extra", "symlink"])
def test_downloaded_bytes_are_not_replaced_by_metadata_claims(
    bundle_inputs: dict[str, object], tmp_path: Path, mutation: str
) -> None:
    manifest = BUNDLE.build_manifest(**bundle_inputs)
    directory = tmp_path / "download"
    directory.mkdir()
    copy_assets(bundle_inputs, directory)
    selected = directory / "images.json"
    if mutation == "bytes":
        selected.write_bytes(b"x" * selected.stat().st_size)
    elif mutation == "missing":
        selected.unlink()
    elif mutation == "extra":
        (directory / "unlisted.txt").write_text("synthetic")
    else:
        selected.unlink()
        selected.symlink_to(bundle_inputs["image_dir"] / "images.json")
    with pytest.raises(ValueError):
        BUNDLE.validate_downloads(manifest, directory)


@pytest.mark.parametrize("drift", [False, True])
def test_remote_orchestration_uses_bound_ids_fresh_directory_and_second_read(
    bundle_inputs: dict[str, object], monkeypatch: pytest.MonkeyPatch, drift: bool
) -> None:
    manifest = BUNDLE.build_manifest(**bundle_inputs)
    remote = remote_document(manifest)
    originals = {asset["id"]: asset["name"] for asset in remote["assets"]}
    reads: list[list[str]] = []
    downloaded: list[Path] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert command == [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "repos/SixSentences/sixsentences/releases/123",
        ]
        assert kwargs["timeout"] == 60
        reads.append(command)
        document = copy.deepcopy(remote)
        if drift and len(reads) == 2:
            document["assets"][0]["id"] = 999
        return SimpleNamespace(stdout=json.dumps(document).encode())

    def download(repository: str, asset_id: int, target: Path, expected_size: int) -> None:
        assert repository == BUNDLE.REPOSITORY and target.name == originals[asset_id]
        assert not target.exists()
        if not downloaded:
            assert list(target.parent.iterdir()) == []
        source = (
            bundle_inputs["preview"]
            if target.name == BUNDLE.PREVIEW
            else bundle_inputs["engine_dir"] / target.name
        )
        if not source.exists():
            source = bundle_inputs["image_dir"] / target.name
        payload = source.read_bytes()
        assert len(payload) == expected_size
        target.write_bytes(payload)
        downloaded.append(target)

    monkeypatch.setattr(BUNDLE.subprocess, "run", run)
    monkeypatch.setattr(BUNDLE, "_download_asset", download)
    if drift:
        with pytest.raises(ValueError, match="IDs changed"):
            BUNDLE.verify_remote_bundle(BUNDLE.REPOSITORY, manifest)
    else:
        BUNDLE.verify_remote_bundle(BUNDLE.REPOSITORY, manifest)
    assert len(reads) == 2 and len(downloaded) == 15
    assert not downloaded[0].parent.exists()


@pytest.mark.parametrize(
    "payload,size,success", [(b"abc", 3, True), (b"abcd", 3, False), (b"ab", 3, False)]
)
def test_asset_body_is_size_bounded_without_following_metadata_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes, size: int, success: bool
) -> None:
    real_popen = subprocess.Popen

    def popen(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        assert command == [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--header",
            "Accept: application/octet-stream",
            "repos/SixSentences/sixsentences/releases/assets/321",
        ]
        return real_popen(
            [sys.executable, "-c", f"import sys;sys.stdout.buffer.write({payload!r})"], **kwargs
        )

    monkeypatch.setattr(BUNDLE.subprocess, "Popen", popen)
    target = tmp_path / "synthetic.bin"
    if success:
        BUNDLE._download_asset(BUNDLE.REPOSITORY, 321, target, size)
        assert target.read_bytes() == payload
    else:
        with pytest.raises(ValueError):
            BUNDLE._download_asset(BUNDLE.REPOSITORY, 321, target, size)
        assert target.stat().st_size <= size


def test_cli_import_and_remote_failure_are_fail_closed(tmp_path: Path) -> None:
    script = ROOT / ".github/scripts/release_bundle.py"
    assert (
        subprocess.run(
            [sys.executable, str(script), "--help"], capture_output=True, check=False
        ).returncode
        == 0
    )
    malformed = tmp_path / "manifest.json"
    malformed.write_text("{}")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "verify-remote",
            "--repo",
            BUNDLE.REPOSITORY,
            "--manifest",
            str(malformed),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert b"publication remains blocked" in result.stderr
