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
        (directory / f"{kind}.identity.json").write_text(
            json.dumps(
                {
                    "org.opencontainers.image.revision": "c" * 40,
                    "org.opencontainers.image.version": "v0.2.0-alpha.2",
                }
            )
        )
        sbom = {"SPDX": {"spdxVersion": "SPDX-2.3", "packages": [{"name": "synthetic"}]}}
        provenance = {"SLSA": {"buildType": "https://mobyproject.org/buildkit@v1"}}
        for suffix, value in (("sbom", sbom), ("provenance", provenance)):
            if platform_wrapper:
                value = {"linux/amd64": value}
            (directory / f"{kind}.{suffix}.json").write_text(json.dumps(value))
    (directory / "IMAGE_DIGESTS").write_text("\n".join(references) + "\n")
    (directory / "runtime-rehearsal.json").write_text(
        json.dumps(
            {
                "source_revision": "c" * 40,
                "api_reference": references[0],
                "web_reference": references[1],
                "fresh_start": True,
                "upgrade": True,
                "backup_restore": True,
                "erasure_replay": True,
                "preupgrade_snapshot_rollback": True,
                "synthetic_only": True,
            }
        )
    )


@pytest.mark.parametrize("wrapped", [False, True])
def test_complete_evidence_is_bound_to_revision_without_overwriting(
    tmp_path: Path, wrapped: bool
) -> None:
    fixture(tmp_path, platform_wrapper=wrapped)
    EVIDENCE.prepare(tmp_path, "v0.2.0-alpha.2", "c" * 40)
    receipt = json.loads((tmp_path / "images.json").read_text())
    assert receipt["source_revision"] == "c" * 40
    assert receipt["web_origin"] == "http://localhost"
    assert len((tmp_path / "IMAGE_SHA256SUMS").read_text().splitlines()) == 9
    with pytest.raises(FileExistsError):
        EVIDENCE.prepare(tmp_path, "v0.2.0-alpha.2", "c" * 40)


@pytest.mark.parametrize(
    "filename,value",
    [
        ("api.sbom.json", {}),
        ("api.sbom.json", {"SPDX": {"spdxVersion": "SPDX-2.3", "packages": []}}),
        ("web.provenance.json", {"SLSA": {"builder": "unverified"}}),
        ("api.build.json", {"containerimage.digest": "sha256:" + "f" * 64}),
        ("api.identity.json", {"org.opencontainers.image.revision": "d" * 40}),
        ("runtime-rehearsal.json", {"source_revision": "d" * 40}),
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
        '"ERROR: $image: not found"',
        "Registry absence was not confirmed",
        "runtime-rehearsal.json",
        "docker image inspect",
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_reference", "ghcr.io/example/community-api@sha256:" + "d" * 64),
        ("source_revision", "d" * 40),
        ("backup_restore", False),
        ("erasure_replay", False),
        ("preupgrade_snapshot_rollback", False),
    ],
)
def test_rehearsal_must_match_the_executed_published_images(
    tmp_path: Path, field: str, value: object
) -> None:
    fixture(tmp_path)
    path = tmp_path / "runtime-rehearsal.json"
    document = json.loads(path.read_text())
    document[field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        EVIDENCE.prepare(tmp_path, "v0.2.0-alpha.2", "c" * 40)
    assert not (tmp_path / "images.json").exists()


@pytest.mark.parametrize(
    "field",
    [
        "fresh_start",
        "upgrade",
        "backup_restore",
        "erasure_replay",
        "preupgrade_snapshot_rollback",
        "synthetic_only",
    ],
)
@pytest.mark.parametrize("value", [None, False, 1, "true"])
def test_runtime_acceptance_requires_explicit_boolean_evidence(
    tmp_path: Path, field: str, value: object
) -> None:
    fixture(tmp_path)
    path = tmp_path / "runtime-rehearsal.json"
    document = json.loads(path.read_text())
    if value is None:
        document.pop(field)
    else:
        document[field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="successful same-revision runtime rehearsal"):
        EVIDENCE.prepare(tmp_path, "v0.2.0-alpha.2", "c" * 40)
    assert not (tmp_path / "images.json").exists()


def test_runtime_rehearsal_is_a_required_ci_and_release_step() -> None:
    for name in ("ci.yml", "release.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text()
        assert "rehearse.py --confirm DISPOSABLE" in workflow
        assert (
            "--upgrade-from ghcr.io/sixsentences/community-api@sha256:"
            "31b374cfb4b45c2cceb6a609d3b0ec8853ee47cd0a3d1588dfa48305bcb3498d" in workflow
        )
        assert "NEXT_PUBLIC_APP_URL=http://localhost " in workflow
        assert "localhost:18080" not in workflow
    driver = (ROOT / "deploy/community/rehearse.py").read_text()
    assert "uuid4().hex" in driver
    assert "Refusing to reuse an existing Compose project" in driver
    assert '"127.0.0.1:80"' in driver
    assert "--env-file" in driver
    assert "restore.sh" in driver
    assert 'phase("verify")' in driver
    assert "capture_output=True" in driver


@pytest.mark.parametrize("kind", ["API", "web"])
def test_published_images_require_pinned_critical_vulnerability_scans(kind: str) -> None:
    workflow = (ROOT / ".github/workflows/publish-images.yml").read_text()
    scan_name = f"Scan the exact published {kind} digest for fixable critical vulnerabilities"
    scan = workflow.split(f"- name: {scan_name}\n", 1)[1].split("- name:", 1)[0]
    assert "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25" in scan
    assert "version: v0.69.3" in scan
    assert "scan-type: image" in scan
    assert f"image-ref: ${{{{ steps.built-images.outputs.{kind.lower()} }}}}" in scan
    assert "scanners: vuln" in scan
    assert "severity: CRITICAL" in scan
    assert "ignore-unfixed: true" in scan
    assert 'exit-code: "1"' in scan
    assert "continue-on-error" not in scan
    assert "if:" not in scan
    assert workflow.index('docker pull "$ref"') < workflow.index(scan_name)
    assert workflow.index(scan_name) < workflow.index("Validate SBOMs and write")
    assert workflow.index(scan_name) < workflow.index("gh release upload")
    assert 'printf \'%s=%s\\n\' "$kind" "$ref" >> "$GITHUB_OUTPUT"' in workflow


@pytest.mark.parametrize("name,job", [("ci.yml", "api"), ("release.yml", "application-api")])
def test_postgres_concurrency_contract_is_not_silently_skipped(name: str, job: str) -> None:
    workflow = (ROOT / ".github/workflows" / name).read_text()
    api = workflow.split(f"\n  {job}:\n", 1)[1].split("\n  web", 1)[0]
    assert "postgres:16-alpine@sha256:" in api
    assert "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777" in api
    assert "127.0.0.1:5432:5432" in api
    assert "POSTGRES_DB: community_ci" in api
    assert "POSTGRES_HOST_AUTH_METHOD: trust" in api
    assert "--health-cmd" in api
    step = api.split("- name: Test real PostgreSQL cutover and concurrent queue claims\n", 1)[1]
    step = step.split("- name:", 1)[0]
    assert (
        "SIX_TEST_POSTGRES_URL: postgresql+psycopg://postgres@127.0.0.1:5432/community_ci" in step
    )
    assert "uv run --frozen --no-sync pytest -q tests/test_postgres_runtime.py" in step
    assert "if:" not in step and "continue-on-error" not in step
