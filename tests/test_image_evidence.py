"""Synthetic validation of digest-bound container release evidence."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "image_evidence", ROOT / ".github/scripts/image_evidence.py"
)
assert SPEC is not None and SPEC.loader is not None
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


def fixture(directory: Path, *, platform_wrapper: bool = False) -> None:
    """Create only inert evidence, never a real registry or API credential."""
    references = []
    for kind, digest in (("api", "a" * 64), ("web", "b" * 64)):
        references.append(f"ghcr.io/example/community-{kind}@sha256:{digest}")
        (directory / f"{kind}.build.json").write_text(
            json.dumps({"containerimage.digest": f"sha256:{digest}"})
        )
        sbom = {"SPDX": {"spdxVersion": "SPDX-2.3", "packages": [{"name": "synthetic"}]}}
        provenance = {"SLSA": {"buildType": "https://mobyproject.org/buildkit@v1"}}
        for suffix, value in (("sbom", sbom), ("provenance", provenance)):
            if platform_wrapper:
                value = {"linux/amd64": value}
            (directory / f"{kind}.{suffix}.json").write_text(json.dumps(value))
    (directory / "IMAGE_DIGESTS").write_text("\n".join(references) + "\n")


@pytest.mark.parametrize("wrapped", [False, True])
def test_complete_evidence_is_bound_to_revision_without_overwriting(
    tmp_path: Path, wrapped: bool
) -> None:
    fixture(tmp_path, platform_wrapper=wrapped)
    EVIDENCE.prepare(tmp_path, "v0.2.0-alpha.2", "c" * 40)
    receipt = json.loads((tmp_path / "images.json").read_text())
    assert receipt["source_revision"] == "c" * 40
    assert receipt["web_origin"] == "http://localhost"
    assert len((tmp_path / "IMAGE_SHA256SUMS").read_text().splitlines()) == 6
    with pytest.raises(FileExistsError):
        EVIDENCE.prepare(tmp_path, "v0.2.0-alpha.2", "c" * 40)


@pytest.mark.parametrize(
    "filename,value",
    [
        ("api.sbom.json", {}),
        ("api.sbom.json", {"SPDX": {"spdxVersion": "SPDX-2.3", "packages": []}}),
        ("web.provenance.json", {"SLSA": {"builder": "unverified"}}),
        ("api.build.json", {"containerimage.digest": "sha256:" + "f" * 64}),
    ],
)
def test_incomplete_or_mismatched_evidence_fails_before_receipt(
    tmp_path: Path, filename: str, value: object
) -> None:
    fixture(tmp_path)
    (tmp_path / filename).write_text(json.dumps(value))
    with pytest.raises(ValueError):
        EVIDENCE.prepare(tmp_path, "v0.2.0-alpha.2", "c" * 40)
    assert not (tmp_path / "images.json").exists()


def test_image_publication_requires_main_signature_ancestry_approval_and_no_overwrite() -> None:
    workflow = (ROOT / ".github/workflows/publish-images.yml").read_text()
    for guard in (
        "refs/heads/main",
        "git verify-tag",
        "git merge-base --is-ancestor",
        "environment: community-release",
        "Refusing to replace an existing image tag",
        "--sbom=true",
        "--provenance=mode=max",
        "--platform linux/amd64",
        "IMAGE_SHA256SUMS",
    ):
        assert guard in workflow
    assert "--clobber" not in workflow
    assert "latest" not in workflow
    assert workflow.index("git verify-tag") < workflow.index("packages: write")
    assert "ref: ${{ needs.validate.outputs.sha }}" in workflow


def test_runtime_rehearsal_is_a_required_ci_and_release_step() -> None:
    for name in ("ci.yml", "release.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text()
        assert "rehearse.py --confirm DISPOSABLE" in workflow
        assert "--upgrade-from ghcr.io/sixsentences/community-api:v0.2.0-alpha.1" in workflow
        assert "NEXT_PUBLIC_APP_URL=http://localhost:18080" in workflow
    driver = (ROOT / "deploy/community/rehearse.py").read_text()
    assert "uuid4().hex" in driver
    assert "Refusing to reuse an existing Compose project" in driver
    assert '"127.0.0.1:18080"' in driver
    assert "--env-file" in driver
    assert "restore.sh" in driver
    assert 'phase("verify")' in driver
    assert "capture_output=True" in driver
