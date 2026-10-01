#!/usr/bin/env python3
"""Exercise fresh startup, released-image upgrade and restore in isolated volumes.

Requires an already running Docker engine and explicit disposable confirmation.
Never starts a daemon, accepts existing environments, or targets existing stacks.
"""

from __future__ import annotations

import argparse
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


def validate_image(image: str) -> str:
    """Require a credential-free explicit image reference, never an option."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*(?::[A-Za-z0-9_.-]+|@sha256:[0-9a-f]{64})", image):
        raise ValueError("an explicit image tag or digest is required")
    return image


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

        def phase(name: str) -> None:
            run(
                compose
                + [
                    "exec",
                    "-T",
                    "--env",
                    "SIX_COMMUNITY_REHEARSAL=DISPOSABLE",
                    "api",
                    "python",
                    "-",
                    name,
                ],
                data=fixture,
            )

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

        try:
            # Resolve the old release once and record its immutable local image ID.
            run(["docker", "pull", args.upgrade_from])
            baseline = run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", args.upgrade_from]
            ).strip()
            environment["SIX_API_IMAGE"] = args.upgrade_from
            run(compose + ["up", "--no-build", "--detach", "--wait", "--wait-timeout", "180"])
            health()
            phase("seed")
            run(compose + ["stop", "--timeout", "30", "api", "worker"])
            environment["SIX_API_IMAGE"] = args.api_image
            run(compose + ["run", "--rm", "--no-deps", "-T", "migrate"])
            run(compose + ["up", "--no-build", "--detach", "--wait", "--wait-timeout", "180"])
            health()
            phase("upgrade")
            run(["bash", str(COMMUNITY / "backup.sh")])
            backups = sorted((directory / "backups").glob("[0-9]*"))
            if len(backups) != 1 or not backups[0].is_dir():
                raise RuntimeError("Expected one complete isolated backup")
            phase("mutate")
            run(
                [
                    "bash",
                    str(COMMUNITY / "restore.sh"),
                    "--backup",
                    str(backups[0]),
                    "--confirm",
                    "RESTORE",
                ]
            )
            health()
            phase("verify")
            # Separately prove the candidate migrates an empty database, not
            # merely the released database used by the upgrade path above.
            run(compose + ["down", "--volumes", "--remove-orphans", "--timeout", "30"])
            run(compose + ["up", "--no-build", "--detach", "--wait", "--wait-timeout", "180"])
            health()
            phase("seed")
            phase("upgrade")
            phase("verify")
            revision = run(["git", "rev-parse", "HEAD"]).strip()
            receipt = {
                "schema_version": 1,
                "source_revision": revision,
                "seed": 42,
                "baseline_image": baseline,
                "api_reference": args.api_image,
                "web_reference": args.web_image,
                "api_image": run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", args.api_image]
                ).strip(),
                "web_image": run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", args.web_image]
                ).strip(),
                "fresh_start": True,
                "upgrade": True,
                "backup_restore": True,
                "synthetic_only": True,
                "provider_calls": 0,
            }
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(receipt, handle, indent=2, sort_keys=True)
                handle.write("\n")
            print("Fresh startup, released-image upgrade and isolated restore passed.")
        finally:
            # The random project was verified absent before creation. These are
            # only this invocation's synthetic volumes, never operator state.
            run(compose + ["down", "--volumes", "--remove-orphans", "--timeout", "30"])


if __name__ == "__main__":
    main()
