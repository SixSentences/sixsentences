"""Structural tests for the simple self-hosted deployment."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMMUNITY = ROOT / "deploy" / "community"
COMPOSE = ROOT / "compose.yaml"


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if separator:
            result[key] = value
    return result


class SelfHostDeploymentTests(unittest.TestCase):
    def test_compose_has_isolated_stateful_stack_and_fail_closed_inputs(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        for service in ("postgres", "api", "worker", "web", "proxy"):
            self.assertRegex(source, rf"(?m)^  {re.escape(service)}:$")
        for volume in ("postgres_data", "app_data", "privacy_data", "caddy_data"):
            self.assertIn(f"{volume}:", source)
        self.assertIn("internal: true", source)
        self.assertRegex(source, r"(?m)^  egress:$")
        self.assertIn("POSTGRES_PASSWORD:?", source)
        self.assertIn("SIX_CONNECTOR_ENCRYPTION_KEY:?", source)
        self.assertIn("condition: service_healthy", source)

        postgres_block = source.split("  postgres:\n", 1)[1].split("\n  api:", 1)[0]
        api_block = source.split("  api:\n", 1)[1].split("\n  worker:", 1)[0]
        worker_block = source.split("  worker:\n", 1)[1].split("\n  web:", 1)[0]
        self.assertNotIn("\n    ports:", postgres_block)
        self.assertNotIn("\n    ports:", api_block)
        self.assertNotIn("egress:", postgres_block)
        self.assertIn("egress:", api_block)
        self.assertIn("egress:", worker_block)
        self.assertGreaterEqual(source.count("gw_priority: 1"), 3)

    def test_public_proxy_does_not_log_capability_urls(self) -> None:
        source = (COMMUNITY / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("@api path /api /api/*", source)
        self.assertIn("reverse_proxy api:8000", source)
        self.assertIn("reverse_proxy web:3000", source)
        self.assertIn("max_size 167772160", source)
        self.assertIn("flush_interval -1", source)
        api_block = source.split("handle @api {", 1)[1].split("\n\t}", 1)[0]
        self.assertNotIn("encode ", api_block)
        self.assertIn("Referrer-Policy no-referrer", source)
        self.assertNotRegex(source, r"(?m)^\s*log\s*\{")

    def test_no_commercial_or_hosted_account_surface_is_configured(self) -> None:
        files = [
            COMPOSE,
            ROOT / ".env.selfhost.example",
            *sorted(
                path
                for path in COMMUNITY.rglob("*")
                if path.is_file() and path.suffix in {"", ".md", ".py", ".sh"}
            ),
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in files).casefold()
        denied = (
            "str" + "ipe",
            "bill" + "ing_portal",
            "check" + "out_session",
            "top" + "up",
            "wait" + "list",
            "rs" + "ync",
        )
        self.assertEqual([term for term in denied if term in text], [])

    def test_init_generates_distinct_secrets_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "deployment.env"
            command = [
                "bash",
                str(COMMUNITY / "init-env.sh"),
                "--domain",
                "research.example.org",
                "--output",
                str(target),
            ]
            first = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode, 0o600)
            values = read_env(target)
            self.assertEqual(values["SIX_ALLOW_INSECURE_LOCAL_HTTP"], "false")
            self.assertRegex(values["POSTGRES_PASSWORD"], r"^[0-9a-f]{64}$")
            self.assertRegex(values["SIX_ERASURE_LEDGER_HMAC_KEY"], r"^[0-9a-f]{64}$")
            self.assertRegex(
                values["SIX_CONNECTOR_ENCRYPTION_KEY"], r"^[A-Za-z0-9_-]{43}=?$"
            )
            self.assertNotEqual(
                values["POSTGRES_PASSWORD"], values["SIX_ERASURE_LEDGER_HMAC_KEY"]
            )
            second = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)

            if shutil.which("docker") is not None:
                version = subprocess.run(
                    ["docker", "compose", "version"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if version.returncode == 0:
                    preflight = subprocess.run(
                        ["bash", str(COMMUNITY / "preflight.sh"), str(target)],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "LC_ALL": "C"},
                    )
                    self.assertEqual(preflight.returncode, 0, preflight.stderr)

            local_target = Path(directory) / "local.env"
            local = subprocess.run(
                [
                    "bash",
                    str(COMMUNITY / "init-env.sh"),
                    "--local",
                    "--output",
                    str(local_target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(local.returncode, 0, local.stderr)
            self.assertEqual(
                read_env(local_target)["SIX_ALLOW_INSECURE_LOCAL_HTTP"], "true"
            )

    def test_web_http_exception_is_explicit_and_loopback_only(self) -> None:
        source = (ROOT / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('ARG SIX_ALLOW_INSECURE_LOCAL_HTTP="false"', source)
        self.assertIn("http://localhost", source)
        self.assertIn("http://127.0.0.1", source)
        self.assertIn('test "$SIX_ALLOW_INSECURE_LOCAL_HTTP" = "true"', source)
        self.assertNotIn("http://*", source)

    def test_restore_requires_exact_confirmation_before_docker(self) -> None:
        result = subprocess.run(
            ["bash", str(COMMUNITY / "restore.sh"), "--backup", "/nonexistent"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm RESTORE", result.stderr)

    def test_restore_replays_the_newest_authenticated_erasure_journal(self) -> None:
        source = (COMMUNITY / "restore.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("sixsentences.ops.erasure verify"), 2)
        self.assertIn("sixsentences.ops.erasure replay", source)
        self.assertIn("current.startswith(candidate)", source)
        self.assertIn("candidate.startswith(current)", source)
        self.assertIn('LEDGER_SOURCE" == "backup"', source)
        self.assertIn("/app/.venv/bin/alembic api upgrade head", source)
        replay_at = source.index("sixsentences.ops.erasure replay")
        proxy_start_at = source.index(
            'up --detach --wait --wait-timeout 180 worker web proxy'
        )
        self.assertLess(replay_at, proxy_start_at)

    def test_backup_dereferences_hardlinks_and_restarts_with_health_waits(self) -> None:
        source = (COMMUNITY / "backup.sh").read_text(encoding="utf-8")
        self.assertEqual(source.count("tar --hard-dereference -czf"), 2)
        self.assertNotIn('${SIX_BACKUP_DIR:-', source)
        self.assertIn("up --detach --wait --wait-timeout 180 api", source)
        for service in ("api", "worker", "web", "postgres", "proxy"):
            self.assertIn(f"{service}_image=%s", source)

    def test_backup_and_restore_share_an_atomic_operation_lock(self) -> None:
        lock_name = ".sixsentences-state-operation.lock"
        for script_name in ("backup.sh", "restore.sh"):
            source = (COMMUNITY / script_name).read_text(encoding="utf-8")
            self.assertIn(lock_name, source)
            self.assertIn('mkdir -m 700 "$LOCK_DIR"', source)

    def test_restore_validates_manifest_and_metadata_before_state_changes(self) -> None:
        source = (COMMUNITY / "restore.sh").read_text(encoding="utf-8")
        checksum_validation = source.index("SHA256SUMS must name each expected")
        service_stop = source.index('stop --timeout 30 proxy web')
        self.assertLess(checksum_validation, service_stop)
        self.assertIn('metadata_value format)" == "1"', source)
        self.assertIn('metadata_value database)" == "postgresql"', source)

    def test_all_shell_scripts_parse(self) -> None:
        scripts = sorted(COMMUNITY.glob("*.sh"))
        self.assertGreaterEqual(len(scripts), 4)
        for script in scripts:
            result = subprocess.run(
                ["bash", "-n", str(script)], check=False, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, f"{script.name}: {result.stderr}")

    def test_preflight_enforces_compose_version_for_gateway_selection(self) -> None:
        source = (COMMUNITY / "preflight.sh").read_text(encoding="utf-8")
        self.assertIn("Docker Compose 2.33.1 or newer", source)
        self.assertIn("COMPOSE_MAJOR == 2 && COMPOSE_MINOR < 33", source)


if __name__ == "__main__":
    unittest.main()
