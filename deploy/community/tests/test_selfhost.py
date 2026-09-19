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
        for service in ("postgres", "api", "worker", "migrate", "web", "proxy"):
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
            self.assertEqual(values["SIX_PUBMED_ENABLED"], "false")
            self.assertEqual(values["SIX_PUBMED_EMAIL"], "")
            self.assertEqual(values["SIX_PUBMED_API_KEY"], "")
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
        self.assertGreaterEqual(source.count("six-community-erasure api verify"), 2)
        self.assertIn("six-community-erasure api replay", source)
        self.assertIn("current.startswith(candidate)", source)
        self.assertIn("candidate.startswith(current)", source)
        self.assertIn('LEDGER_SOURCE" == "backup"', source)
        self.assertIn("run --rm --no-deps -T migrate", source)
        replay_at = source.index("six-community-erasure api replay")
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

    def test_quickstart_rejects_bad_arguments_before_creating_anything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "deployment.env"
            environment = dict(os.environ)
            environment.pop("SIX_API_IMAGE", None)
            environment.pop("SIX_WEB_IMAGE", None)
            result = subprocess.run(
                [
                    "bash",
                    str(COMMUNITY / "quickstart.sh"),
                    "--pull",
                    "--env-file",
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SIX_API_IMAGE", result.stderr)
            self.assertFalse(target.exists())

    def test_quickstart_preflights_waits_for_health_and_bootstraps_one_owner(self) -> None:
        source = (COMMUNITY / "quickstart.sh").read_text(encoding="utf-8")
        preflight_at = source.index("preflight.sh")
        start_at = source.index("compose up --detach --wait")
        self.assertLess(preflight_at, start_at, "the preflight must run before the stack starts")
        self.assertIn("/health/ready", source)
        self.assertIn("auth create-owner", source)
        self.assertNotIn("--password", source)

    def test_quickstart_never_overwrites_an_existing_environment_file(self) -> None:
        source = (COMMUNITY / "quickstart.sh").read_text(encoding="utf-8")
        self.assertIn("Reusing the existing environment file", source)
        self.assertIn("init-env.sh", source)

    def test_makefile_shortcuts_use_the_selected_environment_file(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("ENV_FILE ?= .env.selfhost", makefile)
        self.assertIn("docker compose --env-file $(ENV_FILE)", makefile)
        for target in ("up:", "down:", "owner:", "preflight:", "doctor:", "backup:", "restore:"):
            self.assertIn(f"\n{target}", makefile)
        self.assertNotIn("--password", makefile)
        # A target shadowed by a like-named file stops working silently.
        phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
        for target in ("doctor", "restore", "backup", "up", "down"):
            self.assertIn(target, phony.split())

    def test_make_restore_asks_for_the_confirmation_instead_of_supplying_it(self) -> None:
        """A shortcut must not reach a destructive action faster than its script."""

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        restore = makefile.split("\nrestore:", maxsplit=1)[1].split("\nupdate:")[0]

        # The operator types RESTORE; the target forwards it, never invents it.
        self.assertIn('test "$(CONFIRM)" = "RESTORE"', restore)
        self.assertIn("--confirm RESTORE", restore)
        self.assertIn('test -n "$(BACKUP)"', restore)
        # And it says what is about to be destroyed before it is.
        self.assertIn("replaces the database", restore)

    def test_preflight_enforces_compose_version_for_gateway_selection(self) -> None:
        source = (COMMUNITY / "preflight.sh").read_text(encoding="utf-8")
        self.assertIn("Docker Compose 2.33.1 or newer", source)
        self.assertIn("COMPOSE_MAJOR == 2 && COMPOSE_MINOR < 33", source)

    def test_pubmed_is_explicitly_disabled_and_server_only_by_default(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        example = (ROOT / ".env.selfhost.example").read_text(encoding="utf-8")
        preflight = (COMMUNITY / "preflight.sh").read_text(encoding="utf-8")

        self.assertIn("SIX_PUBMED_ENABLED: ${SIX_PUBMED_ENABLED:-false}", compose)
        self.assertIn("SIX_PUBMED_EMAIL: ${SIX_PUBMED_EMAIL:-}", compose)
        self.assertIn("SIX_PUBMED_API_KEY: ${SIX_PUBMED_API_KEY:-}", compose)
        self.assertIn("SIX_PUBMED_ENABLED=false", example)
        self.assertNotIn("NEXT_PUBLIC_PUBMED", compose)
        self.assertIn("PubMed retrieval requires an NCBI contact email", preflight)

    def test_preflight_rejects_invalid_pubmed_contact_without_echoing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "deployment.env"
            initialized = subprocess.run(
                [
                    "bash",
                    str(COMMUNITY / "init-env.sh"),
                    "--local",
                    "--output",
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            source = target.read_text(encoding="utf-8")
            source = source.replace("SIX_PUBMED_ENABLED=false", "SIX_PUBMED_ENABLED=true")
            source = source.replace("SIX_PUBMED_EMAIL=", "SIX_PUBMED_EMAIL=private-invalid-token")
            target.write_text(source, encoding="utf-8")
            target.chmod(0o600)

            result = subprocess.run(
                ["bash", str(COMMUNITY / "preflight.sh"), str(target)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("valid contact email address", result.stderr)
            self.assertNotIn("private-invalid-token", result.stderr)


if __name__ == "__main__":
    unittest.main()
