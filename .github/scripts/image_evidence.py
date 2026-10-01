#!/usr/bin/env python3
"""Validate public image evidence and bind immutable digests to one release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def _document(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError(f"invalid bounded evidence file: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise ValueError(f"missing evidence: {path.name}")
    return value


def _predicate(document: dict[str, object], name: str) -> dict[str, object]:
    platform = document.get("linux/amd64", document)
    predicate = platform.get(name) if isinstance(platform, dict) else None
    if not isinstance(predicate, dict) or not predicate:
        raise ValueError(f"missing linux/amd64 {name} predicate")
    return predicate


def prepare(directory: Path, tag: str, revision: str) -> None:
    """Require real SBOM/provenance payloads and write a non-overwriting receipt."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", tag):
        raise ValueError("invalid release tag")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid source revision")
    refs = (directory / "IMAGE_DIGESTS").read_text(encoding="utf-8").splitlines()
    if len(refs) != 2:
        raise ValueError("expected exactly two immutable image references")
    images = {}
    for kind, ref in zip(("api", "web"), refs, strict=True):
        match = re.fullmatch(rf"ghcr\.io/[a-z0-9-]+/community-{kind}@(sha256:[0-9a-f]{{64}})", ref)
        if match is None:
            raise ValueError(f"invalid {kind} digest reference")
        build = _document(directory / f"{kind}.build.json")
        if build.get("containerimage.digest") != match[1]:
            raise ValueError(f"{kind} digest does not match build receipt")
        identity = _document(directory / f"{kind}.identity.json")
        if identity.get("org.opencontainers.image.revision") != revision:
            raise ValueError(f"{kind} OCI revision does not match verified source")
        if identity.get("org.opencontainers.image.version") != tag:
            raise ValueError(f"{kind} OCI version does not match verified tag")
        sbom = _predicate(_document(directory / f"{kind}.sbom.json"), "SPDX")
        if not str(sbom.get("spdxVersion", "")).startswith("SPDX-") or not sbom.get("packages"):
            raise ValueError(f"{kind} SBOM has no package inventory")
        provenance = _predicate(_document(directory / f"{kind}.provenance.json"), "SLSA")
        build_type = provenance.get("buildType")
        if build_type is None and isinstance(provenance.get("buildDefinition"), dict):
            build_type = provenance["buildDefinition"].get("buildType")
        if not isinstance(build_type, str) or not build_type.startswith(
            "https://mobyproject.org/buildkit"
        ):
            raise ValueError(f"{kind} build provenance is missing")
        images[kind] = {"reference": ref, "platform": "linux/amd64"}
    runtime = _document(directory / "runtime-rehearsal.json")
    if runtime.get("source_revision") != revision or any(
        runtime.get(key) is not True
        for key in ("fresh_start", "upgrade", "backup_restore", "synthetic_only")
    ):
        raise ValueError("missing successful same-revision runtime rehearsal")
    if runtime.get("api_reference") != refs[0] or runtime.get("web_reference") != refs[1]:
        raise ValueError("runtime rehearsal did not execute the published digests")
    receipt = {
        "schema_version": 1,
        "tag": tag,
        "source_revision": revision,
        "web_origin": "http://localhost",
        "images": images,
        "reproducibility": (
            "Locked source and recorded inputs; byte-identical rebuilds are not asserted."
        ),
    }
    with (directory / "images.json").open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
    names = ["IMAGE_DIGESTS", "images.json", "runtime-rehearsal.json"] + [
        f"{kind}.{suffix}.json"
        for kind in ("api", "web")
        for suffix in ("sbom", "provenance", "identity")
    ]
    with (directory / "IMAGE_SHA256SUMS").open("x", encoding="utf-8") as handle:
        for name in sorted(names):
            handle.write(f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n")


def main() -> None:
    """Parse a release workflow's bounded evidence inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    prepare(args.directory, args.tag, args.revision)


if __name__ == "__main__":
    main()
