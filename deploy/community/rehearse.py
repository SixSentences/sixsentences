#!/usr/bin/env python3
"""Exercise startup, upgrade, restore and snapshot rollback in isolated volumes.

Requires an already running Docker engine and explicit disposable confirmation.
Never starts a daemon, accepts existing environments, or targets existing stacks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
COMMUNITY = ROOT / "deploy/community"


def baseline_bootstrap_evidence(api_reference: str) -> dict[str, object]:
    """Require the reviewed single-literal fix before bootstrapping the old schema."""
    contract = json.loads((COMMUNITY / "alpha1-baseline-compatibility.json").read_text())
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version") != 1
        or contract.get("mode") != "candidate_migrator_boolean_default_fix"
        or contract.get("original_api_reference") != api_reference
        or contract.get("target_database_revision") != "20260912_0001"
        or contract.get("invalid_default_sqlstate") != "42804"
        or contract.get("unmodified_original_installer") is not False
        or contract.get("legacy_corpus_fixture")
        != {
            "mode": "synthetic_duckdb_build",
            "records": 1,
            "seed": 42,
        }
        or contract.get("migration_path")
        != "services/api/alembic/versions/20260912_0001_community_baseline.py"
    ):
        raise RuntimeError("Unsupported historical baseline compatibility contract")
    compatible = (ROOT / contract["migration_path"]).read_bytes()
    prefix = b"sa.Column('library_suppressed', sa.Boolean(), "
    original_literal = prefix + b"server_default=sa.text('0'), nullable=False)"
    compatible_literal = prefix + b"server_default=sa.false(), nullable=False)"
    if compatible.count(compatible_literal) != 1:
        raise RuntimeError("Expected exactly one reviewed Boolean-default correction")
    original = compatible.replace(compatible_literal, original_literal, 1)
    if hashlib.sha256(compatible).hexdigest() != contract.get(
        "compatible_migration_sha256"
    ) or hashlib.sha256(original).hexdigest() != contract.get("original_migration_sha256"):
        raise RuntimeError("Historical baseline differs beyond the reviewed Boolean literal")
    return contract


def validate_image(image: str) -> str:
    """Require a credential-free explicit image reference, never an option."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*(?::[A-Za-z0-9_.-]+|@sha256:[0-9a-f]{64})", image):
        raise ValueError("an explicit image tag or digest is required")
    return image


def sanitized_service_states(payload: str) -> list[dict[str, object]]:
    """Allowlist Compose status fields without exposing container configuration."""
    try:
        document = json.loads(payload)
        rows = document if isinstance(document, list) else [document]
    except ValueError:
        try:
            rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
        except ValueError:
            return []
    result: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("Service"), str):
            continue
        if row["Service"] not in {
            "api",
            "worker",
            "migrate",
            "postgres",
            "web",
            "proxy",
        }:
            continue
        state = row.get("State")
        health = row.get("Health")
        exit_code = row.get("ExitCode")
        result.append(
            {
                "service": row["Service"],
                "state": state
                if isinstance(state, str)
                and state
                in {
                    "created",
                    "running",
                    "paused",
                    "restarting",
                    "removing",
                    "exited",
                    "dead",
                }
                else "unavailable",
                "health": health
                if isinstance(health, str)
                and health
                in {
                    "healthy",
                    "unhealthy",
                    "starting",
                }
                else "unavailable",
                "exit_code": exit_code if type(exit_code) is int else "unavailable",
            }
        )
    return result


def sanitized_migration_failure(payload: str) -> dict[str, object]:
    """Expose only known exception names and public migration source locations."""
    exceptions = (
        "AttributeError",
        "ConnectionRefusedError",
        "FileNotFoundError",
        "ImportError",
        "IntegrityError",
        "KeyError",
        "ModuleNotFoundError",
        "NameError",
        "OperationalError",
        "PermissionError",
        "ProgrammingError",
        "RuntimeError",
        "SyntaxError",
        "TypeError",
        "ValueError",
        "DuplicateTable",
        "DuplicateColumn",
        "UndefinedTable",
        "UndefinedColumn",
        "InvalidSchemaName",
        "InsufficientPrivilege",
        "ReadOnlySqlTransaction",
        "InvalidPassword",
    )
    migrations = ROOT / "services/api/alembic"
    public_filenames = {path.name for path in migrations.rglob("*.py")}
    frames = []
    for filename, line in re.findall(r'File "[^"\n]*/([^/"\n]+\.py)", line ([0-9]{1,6})', payload):
        if filename in public_filenames:
            frames.append({"file": filename, "line": int(line)})
    return {
        "exception_types": [
            name for name in exceptions if re.search(rf"\b{re.escape(name)}[\s:(]", payload)
        ],
        "public_migration_frames": frames[-20:],
    }


def sanitized_restore_failure(payload: str) -> list[str]:
    """Keep only fixed restore-phase labels, never shell commands or journal bytes."""
    allowed = {
        "configuration",
        "backup_manifest",
        "archive_validation",
        "staging",
        "candidate_journal_verification",
        "stop_services",
        "live_journal_selection",
        "database_restore",
        "application_restore",
        "erasure_replay",
        "service_restart",
        "cleanup",
    }
    return sorted(
        {
            match
            for match in re.findall(r"(?m)^SIX_RESTORE_FAILURE_PHASE=([a-z_]+)$", payload)
            if match in allowed
        }
    )


def main() -> None:
    """Run the rehearsals and retain only a sanitized success receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", choices=["DISPOSABLE"], required=True)
    parser.add_argument("--api-image", type=validate_image, required=True)
    parser.add_argument("--web-image", type=validate_image, required=True)
    parser.add_argument("--upgrade-from", type=validate_image, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report already exists; use a fresh path")
    baseline_bootstrap = baseline_bootstrap_evidence(args.upgrade_from)
    project = f"six-rehearsal-{uuid4().hex[:12]}"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SIX_", "POSTGRES_", "COMPOSE_"))
    }
    environment["COMPOSE_PROJECT_NAME"] = project
    environment["SIX_HTTP_PORT"] = "127.0.0.1:80"
    environment["SIX_HTTPS_PORT"] = "127.0.0.1:18443"
    environment["SIX_WEB_IMAGE"] = args.web_image

    def run(command: list[str], *, data: bytes | None = None) -> str:
        result = subprocess.run(
            command, cwd=ROOT, env=environment, input=data, capture_output=True, timeout=600
        )
        if result.returncode:
            if command[:2] == ["bash", str(COMMUNITY / "restore.sh")]:
                print(
                    json.dumps(
                        {
                            "restore_failure_phases": sanitized_restore_failure(
                                result.stderr.decode(errors="replace")
                            ),
                        }
                    )
                )
            # Do not leak env, database URLs, credentials or HTTP responses.
            raise RuntimeError(f"Rehearsal command failed: {command[0]} (exit {result.returncode})")
        return result.stdout.decode()

    run(["docker", "info", "--format", "{{.ServerVersion}}"])
    for resource in (["ps", "-aq"], ["volume", "ls", "-q"], ["network", "ls", "-q"]):
        if run(
            ["docker", *resource, "--filter", f"label=com.docker.compose.project={project}"]
        ).strip():
            raise RuntimeError("Refusing to reuse an existing Compose project")
    with tempfile.TemporaryDirectory(prefix="six-community-rehearsal-") as scratch:
        directory = Path(scratch)
        env_file = directory / "rehearsal.env"
        run(["bash", str(COMMUNITY / "init-env.sh"), "--local", "--output", str(env_file)])
        config = env_file.read_text(encoding="utf-8")
        config = re.sub(
            r"(?m)^SIX_BACKUP_DIR=.*$", f"SIX_BACKUP_DIR={directory / 'backups'}", config
        )
        env_file.write_text(config, encoding="utf-8")
        environment["SIX_SELFHOST_ENV_FILE"] = str(env_file)
        compose = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "--file",
            str(ROOT / "compose.yaml"),
        ]
        fixture = (COMMUNITY / "rehearsal_fixture.py").read_bytes()

        def phase(name: str, *arguments: str, user: str | None = None) -> str:
            try:
                return run(
                    compose
                    + [
                        "exec",
                        "-T",
                        *(["--user", user] if user is not None else []),
                        "--env",
                        "SIX_COMMUNITY_REHEARSAL=DISPOSABLE",
                        "api",
                        "python",
                        "-",
                        name,
                        *arguments,
                    ],
                    data=fixture,
                )
            except RuntimeError:
                raise RuntimeError(f"Synthetic rehearsal phase failed: {name}") from None

        def health() -> None:
            for path in ("/login", "/api/health/ready"):
                for attempt in range(30):
                    try:
                        with urllib.request.urlopen(
                            f"http://localhost{path}", timeout=5
                        ) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    if attempt == 29:
                        raise RuntimeError("Loopback health did not become ready")
                    time.sleep(1)

        def backup() -> Path:
            before = set((directory / "backups").glob("[0-9]*"))
            run(["bash", str(COMMUNITY / "backup.sh")])
            created = set((directory / "backups").glob("[0-9]*")) - before
            if len(created) != 1 or not (snapshot := created.pop()).is_dir():
                raise RuntimeError("Expected one new complete isolated backup")
            return snapshot

        def restore(snapshot: Path) -> None:
            run(
                [
                    "bash",
                    str(COMMUNITY / "restore.sh"),
                    "--backup",
                    str(snapshot),
                    "--confirm",
                    "RESTORE",
                ]
            )
            health()

        try:
            # Resolve the old release once and record its immutable local image ID.
            run(["docker", "pull", args.upgrade_from])
            baseline = run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", args.upgrade_from]
            ).strip()
            # The published alpha.1 baseline uses BOOLEAN DEFAULT 0, rejected
            # by PostgreSQL (42804). Apply only the hash-bound literal fix to
            # the original revision using the candidate's actual Alembic
            # migrator, then run the unchanged published alpha.1 image.
            # This is not proof that the unmodified alpha.1 installer works.
            environment["SIX_API_IMAGE"] = args.api_image
            run(compose + ["up", "--no-build", "--detach", "--wait", "postgres"])
            run(
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "migrate",
                    "alembic",
                    "upgrade",
                    "20260912_0001",
                ]
            )
            environment["SIX_API_IMAGE"] = args.upgrade_from
            # Unlike the candidate, alpha.1 readiness requires a local corpus.
            # Build and verify one real synthetic record before starting that
            # unchanged image. This is not a skipped or mocked health check.
            run(
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--env",
                    "SIX_COMMUNITY_REHEARSAL=DISPOSABLE",
                    "migrate",
                    "python",
                    "-",
                    "bootstrap-corpus",
                ],
                data=fixture,
            )
            run(compose + ["up", "--no-build", "--detach", "--wait", "--wait-timeout", "180"])
            health()
            phase("seed")
            phase("prepare-rollback")
            baseline_revision = phase("database-revision").strip()
            if baseline_revision != baseline_bootstrap["target_database_revision"]:
                raise RuntimeError("Bootstrap did not produce the original database revision")
            preupgrade_snapshot = backup()
            run(compose + ["stop", "--timeout", "30", "api", "worker"])
            environment["SIX_API_IMAGE"] = args.api_image
            run(compose + ["run", "--rm", "--no-deps", "-T", "migrate"])
            run(compose + ["up", "--no-build", "--detach", "--wait", "--wait-timeout", "180"])
            health()
            phase("upgrade")
            phase("prepare-erasure-pinboard")
            candidate_snapshot = backup()
            phase("mutate")
            erasure = json.loads(phase("erase"))
            if (
                not isinstance(erasure, dict)
                or set(erasure) != {"signature"}
                or not isinstance(erasure["signature"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", erasure["signature"])
            ):
                raise RuntimeError("Missing authenticated synthetic post-backup erasure")
            phase("verify-root-permission-denied", user="0:0")
            print("Synthetic journal read is denied to UID 0 without capabilities.")
            restore(candidate_snapshot)
            phase("verify")
            phase("verify-erasure", erasure["signature"])
            # Supported rollback means restoring the complete pre-upgrade
            # snapshot with the released API, not downgrading a migrated DB.
            # Keep the independent privacy volume: post-snapshot erasures must
            # still be applied by the released API before traffic resumes.
            run(compose + ["stop", "--timeout", "30", "api", "worker"])
            environment["SIX_API_IMAGE"] = args.upgrade_from
            restore(preupgrade_snapshot)
            if phase("database-revision").strip() != baseline_revision:
                raise RuntimeError("Rollback did not restore the released database revision")
            phase("verify-rollback", erasure["signature"])
            # Separately prove the candidate migrates an empty database, not
            # merely the released database used by the upgrade path above.
            run(compose + ["down", "--volumes", "--remove-orphans", "--timeout", "30"])
            environment["SIX_API_IMAGE"] = args.api_image
            run(compose + ["up", "--no-build", "--detach", "--wait", "--wait-timeout", "180"])
            health()
            phase("verify-no-corpus")
            phase("seed")
            phase("upgrade")
            phase("verify")
            revision = run(["git", "rev-parse", "HEAD"]).strip()
            receipt = {
                "schema_version": 1,
                "source_revision": revision,
                "seed": 42,
                "baseline_image": baseline,
                "baseline_bootstrap": baseline_bootstrap,
                "api_reference": args.api_image,
                "web_reference": args.web_image,
                "api_image": run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", args.api_image]
                ).strip(),
                "web_image": run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", args.web_image]
                ).strip(),
                "fresh_start": True,
                "synthetic_corpus_bootstrap": True,
                "fresh_candidate_without_corpus": True,
                "upgrade": True,
                "backup_restore": True,
                "erasure_replay": True,
                "preupgrade_snapshot_rollback": True,
                "rollback_database_revision": baseline_revision,
                "rollback_data_lossless": False,
                "schema_downgrade": False,
                "synthetic_only": True,
                "provider_calls": 0,
            }
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(receipt, handle, indent=2, sort_keys=True)
                handle.write("\n")
            print("Fresh startup, upgrade, restore and pre-upgrade snapshot rollback passed.")
        except Exception:
            # No container logs, environment, labels, URLs or raw Docker error
            # text: retain just enough status to identify the failing service.
            try:
                status = run(compose + ["ps", "--all", "--format", "json"])
                print(json.dumps({"service_states": sanitized_service_states(status)}))
                migration_output = run(compose + ["logs", "--no-color", "--tail", "200", "migrate"])
                print(
                    json.dumps({"migration_failure": sanitized_migration_failure(migration_output)})
                )
            except Exception:
                print("Service-state diagnostics unavailable")
            raise
        finally:
            # The random project was verified absent before creation. These are
            # only this invocation's synthetic volumes, never operator state.
            run(compose + ["down", "--volumes", "--remove-orphans", "--timeout", "30"])


if __name__ == "__main__":
    main()
