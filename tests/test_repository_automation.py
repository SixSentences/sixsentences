"""Synthetic tests for repository DCO and release automation."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import urllib.request
from datetime import date
from email.message import Message
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
CLA = _load_script("check_cla")
RELEASE = _load_script("release")
STATUS = _load_script("publish_status")
URI_HISTORY = _load_script("check_history_credential_uris")
REPOSITORY_ROOT = Path(__file__).parents[1]


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


def test_dco_accepts_two_official_noreply_forms_for_the_same_github_login() -> None:
    author = _identity("Lukas Buck <103962359+L4XB@users.noreply.github.com>")
    trailers = DCO.interpret_identity_trailers(
        """fix: synthetic contribution

Signed-off-by: L4XB <L4XB@users.noreply.github.com>
"""
    )

    assert DCO.validate_identities(author, trailers) == []


def test_dco_noreply_alias_never_matches_a_different_github_login() -> None:
    author = _identity("Alice Example <123+alice@users.noreply.github.com>")
    trailers = DCO.interpret_identity_trailers(
        """fix: synthetic contribution

Signed-off-by: Mallory Example <mallory@users.noreply.github.com>
"""
    )

    assert DCO.validate_identities(author, trailers) == [
        "commit author is not signed off: alice example <123+alice@users.noreply.github.com>"
    ]


def test_dco_does_not_relax_matching_for_non_github_email_addresses() -> None:
    author = _identity("Alice Example <alice@example.com>")
    trailers = DCO.interpret_identity_trailers(
        """fix: synthetic contribution

Signed-off-by: Alice Alias <alice@another.example>
"""
    )

    assert DCO.validate_identities(author, trailers) == [
        "commit author is not signed off: alice example <alice@example.com>"
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


def test_dependabot_owned_pull_request_rejects_human_commits() -> None:
    author = _identity("Alice Example <alice@example.com>")
    trailers = DCO.interpret_identity_trailers(
        """fix: synthetic change

Signed-off-by: Alice Example <alice@example.com>
"""
    )

    assert DCO.validate_identities(
        author,
        trailers,
        pull_request_author="dependabot[bot]",
    ) == ["Dependabot-owned pull requests may contain only Dependabot-authored commits"]


def test_dependabot_metadata_block_accepts_only_exact_trusted_final_footer() -> None:
    author = _identity("dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>")
    message = """chore(deps): bump next from 16.3.3 to 16.3.4

Bumps [next](https://github.com/vercel/next.js) from 16.3.3 to 16.3.4.

---
updated-dependencies:
- dependency-name: next
  dependency-version: 16.3.4
  dependency-type: direct:production
  update-type: version-update:semver-patch
...
Signed-off-by: dependabot[bot] <support@github.com>
"""

    assert DCO.interpret_identity_trailers(message).signoffs == ()

    trusted = DCO.interpret_identity_trailers(
        message,
        author=author,
        pull_request_author="dependabot[bot]",
    )
    assert (
        DCO.validate_identities(
            author,
            trusted,
            pull_request_author="dependabot[bot]",
        )
        == []
    )

    wrong_pr = DCO.interpret_identity_trailers(
        message,
        author=author,
        pull_request_author="human-contributor",
    )
    assert wrong_pr.signoffs == ()

    human = _identity("Alice Example <alice@example.com>")
    wrong_author = DCO.interpret_identity_trailers(
        message,
        author=human,
        pull_request_author="dependabot[bot]",
    )
    assert wrong_author.signoffs == ()

    noncanonical = DCO.interpret_identity_trailers(
        message.replace(
            "Signed-off-by: dependabot[bot] <support@github.com>",
            "signed-off-by: dependabot[bot] <support@github.com>",
        ),
        author=author,
        pull_request_author="dependabot[bot]",
    )
    assert noncanonical.signoffs == ()

    not_final = DCO.interpret_identity_trailers(
        message + "unexpected metadata tail\n",
        author=author,
        pull_request_author="dependabot[bot]",
    )
    assert not_final.signoffs == ()


def test_cla_acceptance_requires_the_pull_request_author_and_exact_comment() -> None:
    acceptance = CLA.ACCEPTANCE
    comments = [
        {"user": {"login": "reviewer"}, "body": acceptance},
        {"user": {"login": "contributor"}, "body": f"quoted: {acceptance}"},
        {"user": {"login": "contributor"}, "body": acceptance},
    ]

    assert CLA._accepted(comments, author="contributor") is True
    assert CLA._accepted(comments[:2], author="contributor") is False


def test_workflow_job_environment_avoids_step_only_runner_context() -> None:
    workflows = Path(__file__).parents[1] / ".github" / "workflows"
    invalid: list[str] = []
    for workflow in sorted(workflows.glob("*.yml")):
        in_job_environment = False
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("    env:"):
                in_job_environment = True
                continue
            if in_job_environment and line.strip() and len(line) - len(line.lstrip()) <= 4:
                in_job_environment = False
            if in_job_environment and "${{ runner." in line:
                invalid.append(f"{workflow.name}:{line_number}")

    assert invalid == []


def test_release_publication_requires_the_protected_environment() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    publish_job = workflow.split("\n  publish:\n", maxsplit=1)[1]

    assert "\n    environment: community-release\n" in publish_job
    assert (
        "\n    needs: [validate, build, attest, self-hosting, browser-extension-source, "
        "companion-macos-source, tag-security, preview]\n" in publish_job
    )
    assert "ref: ${{ needs.validate.outputs.release-sha }}" in publish_job


def test_release_dispatch_checks_out_and_verifies_one_explicit_signed_tag() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "\n  workflow_dispatch:\n" in workflow
    assert "\n  push:\n" not in workflow
    assert "ref: ${{ inputs.tag }}" not in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert workflow.count("ref: ${{ needs.validate.outputs.release-sha }}") >= 6
    assert '[[ "$WORKFLOW_REF" != "refs/heads/main" ]]' in workflow
    assert 'git worktree add --detach "$RUNNER_TEMP/release-source" "$release_sha"' in workflow
    assert 'git verify-tag "$RELEASE_TAG"' in workflow
    assert "release-maintainers.allowed_signers" in workflow
    assert "\n  tag-security:\n" in workflow
    assert "\n  preview:\n" in workflow


def test_security_workflow_scans_complete_history_without_provider_calls() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "security.yml").read_text(
        encoding="utf-8"
    )

    assert "trufflesecurity/trufflehog@363923b901c911a9164f50b6c423f47c15372b1c" in workflow
    assert "version: 3.97.4" in workflow
    assert 'base: ""' in workflow
    assert "head: ${{ github.sha }}" in workflow
    assert "--no-verification" in workflow
    assert "--results=verified,unknown,unverified" in workflow
    assert "--exclude-detectors=URI" in workflow
    assert "python .github/scripts/check_history_credential_uris.py" in workflow
    assert "--no-update" not in workflow
    assert "fetch-depth: 0" in workflow


def test_history_uri_scan_allows_only_explicit_reserved_domain_placeholders() -> None:
    unsafe_uri = (
        "https://" + "service-account:live-looking-value" + "@research.example.com/resource"
    )
    patch = f"""__SIX_COMMIT__aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
diff --git a/tests/example.py b/tests/example.py
+++ b/tests/example.py
@@ -0,0 +1,2 @@
+safe = "https://user:password@example.org/resource"
+unsafe = "{unsafe_uri}"
"""

    violations = URI_HISTORY.find_violations(patch)

    assert [violation.label() for violation in violations] == ["aaaaaaaaaaaa:tests/example.py:2"]


def test_history_uri_scan_allows_placeholder_credentials_on_reserved_subdomains() -> None:
    patch = """__SIX_COMMIT__dddddddddddddddddddddddddddddddddddddddd
diff --git a/tests/example.py b/tests/example.py
+++ b/tests/example.py
@@ -0,0 +1 @@
+safe = "redis://user:pass@cache.research.example.org/0"
"""

    assert URI_HISTORY.find_violations(patch) == []


def test_history_uri_scan_never_includes_the_detected_value_in_diagnostics() -> None:
    unsafe_uri = "https://" + "operator:do-not-print-this" + "@internal.example.com/api"
    patch = f"""__SIX_COMMIT__bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
diff --git a/config.txt b/config.txt
+++ b/config.txt
@@ -0,0 +1 @@
+endpoint={unsafe_uri}
"""

    [violation] = URI_HISTORY.find_violations(patch)

    assert violation.label() == "bbbbbbbbbbbb:config.txt:1"
    assert "do-not-print-this" not in violation.label()


@pytest.mark.parametrize(
    "scheme",
    ["postgresql", "postgres", "redis", "rediss", "mongodb", "mongodb+srv", "amqps"],
)
def test_history_uri_scan_covers_non_http_credential_schemes(scheme: str) -> None:
    unsafe_uri = f"{scheme}://" + "operator:do-not-print-this" + "@internal.example.com/data"
    patch = f"""__SIX_COMMIT__cccccccccccccccccccccccccccccccccccccccc
diff --git a/config.txt b/config.txt
+++ b/config.txt
@@ -0,0 +1 @@
+endpoint={unsafe_uri}
"""

    [violation] = URI_HISTORY.find_violations(patch)

    assert violation.label() == "cccccccccccc:config.txt:1"
    assert "do-not-print-this" not in violation.label()


def test_history_uri_scan_baseline_is_bound_to_an_immutable_location() -> None:
    reviewed = URI_HISTORY.Violation(
        commit="1cdbf0f27c764379c81aeb7b3297f7bef9e7213d",
        path="tests/test_repository_automation.py",
        line=304,
    )
    adjacent = URI_HISTORY.Violation(
        commit=reviewed.commit,
        path=reviewed.path,
        line=305,
    )

    assert URI_HISTORY.unreviewed_violations([reviewed, adjacent]) == [adjacent]


def test_cla_status_is_bound_to_the_exact_pull_request_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object | None]] = []

    def request(
        url: str, *, token: str, method: str = "GET", payload: object | None = None
    ) -> object:
        assert token == "synthetic-token"
        calls.append((url, method, payload))
        return {}

    monkeypatch.setattr(CLA, "_request", request)
    sha = "a" * 40

    CLA._set_status(
        "https://api.github.test",
        token="synthetic-token",
        repository="example/project",
        sha=sha,
        cla_sha="b" * 40,
        state="success",
    )

    assert calls == [
        (
            f"https://api.github.test/repos/example/project/statuses/{sha}",
            "POST",
            {
                "state": "success",
                "context": "CLA / acceptance",
                "description": "Accepted CLA v1.0 by the pull-request author.",
                "target_url": f"https://github.com/example/project/blob/{'b' * 40}/CLA.md",
            },
        )
    ]


def test_cla_exempts_only_the_trusted_dependency_bot() -> None:
    assert frozenset({"dependabot[bot]"}) == CLA.EXEMPT_AUTHORS


def test_cla_acceptance_names_the_exact_agreement_version() -> None:
    assert CLA.CLA_VERSION == "1.0"
    assert CLA.ACCEPTANCE == "I have read and agree to the SixSentences CLA v1.0."


def test_cla_replaces_a_stale_success_with_pending_before_reading_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: list[str] = []

    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    monkeypatch.setattr(CLA, "_pull_request_identity", lambda *args, **kwargs: ("alice", "a" * 40))
    monkeypatch.setattr(
        CLA,
        "_set_status",
        lambda *args, state, **kwargs: states.append(state),
    )

    def unavailable_comments(*args: object, **kwargs: object) -> list[dict[str, object]]:
        assert states == ["pending"]
        raise OSError("synthetic outage")

    monkeypatch.setattr(CLA, "_comments", unavailable_comments)

    result = CLA.main(
        [
            "--repository",
            "example/project",
            "--number",
            "42",
            "--cla-sha",
            "b" * 40,
            "--api-url",
            "https://api.github.test",
        ]
    )

    assert result == 2
    assert states == ["pending"]


def test_trusted_cla_workflow_cannot_be_manually_dispatched() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "cla.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "issue_comment:" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "statuses: write" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "github.event.pull_request.head.ref" not in workflow
    assert 'cla_source_sha="$(git rev-parse HEAD)"' in workflow


def test_trusted_dco_workflow_treats_contribution_commits_only_as_git_data() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "dco.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "statuses: write" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "github.event.pull_request.head.ref" not in workflow
    assert workflow.count("uses: actions/checkout@") == 1
    assert 'context "DCO / sign-off"' in workflow
    assert "--filter=blob:none" in workflow


def test_status_publisher_posts_only_the_validated_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}

    class Response:
        status = 201

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def urlopen(request: urllib.request.Request, *, timeout: int) -> Response:
        recorded["url"] = request.full_url
        recorded["method"] = request.method
        recorded["payload"] = json.loads(request.data or b"{}")
        recorded["timeout"] = timeout
        return Response()

    monkeypatch.setattr(STATUS.urllib.request, "urlopen", urlopen)
    sha = "a" * 40

    STATUS.publish_status(
        "https://api.github.test",
        token="synthetic-token",
        repository="example/project",
        sha=sha,
        context="DCO / sign-off",
        state="success",
        description="Every commit is signed off.",
        target_url="https://github.com/example/project/blob/main/DCO",
    )

    assert recorded == {
        "url": f"https://api.github.test/repos/example/project/statuses/{sha}",
        "method": "POST",
        "payload": {
            "state": "success",
            "context": "DCO / sign-off",
            "description": "Every commit is signed off.",
            "target_url": "https://github.com/example/project/blob/main/DCO",
        },
        "timeout": 30,
    }


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


def _write_sdist(path: Path, *, include_lockfile: bool) -> None:
    root = "sixsentences_engine-0.1.0a1"
    metadata = Message()
    metadata["Name"] = "sixsentences-engine"
    metadata["Version"] = "0.1.0a1"
    payloads = {f"{root}/PKG-INFO": metadata.as_bytes()}
    if include_lockfile:
        payloads[f"{root}/uv.lock"] = b"version = 1\n"
    with tarfile.open(path, mode="w:gz") as archive:
        for name, payload in payloads.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, fileobj=io.BytesIO(payload))


def test_sdist_gate_requires_lockfile(tmp_path: Path) -> None:
    archive = tmp_path / "sixsentences_engine-0.1.0a1.tar.gz"
    _write_sdist(archive, include_lockfile=False)

    with pytest.raises(RELEASE.ReleaseValidationError, match="uv.lock"):
        RELEASE._verify_sdist(archive, package_version="0.1.0a1")

    _write_sdist(archive, include_lockfile=True)
    RELEASE._verify_sdist(archive, package_version="0.1.0a1")
