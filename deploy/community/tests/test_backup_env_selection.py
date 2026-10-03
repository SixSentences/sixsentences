"""Exercise backup argument dispatch without reaching any backup operation."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

COMMUNITY = Path(__file__).resolve().parents[1]
PREFLIGHT_EXIT = 73


class BackupEnvironmentSelectionTests(unittest.TestCase):
    """Run the actual script prefix against an always-aborting preflight stub."""

    def setUp(self) -> None:
        """Build a synthetic dispatch-only checkout with no backup code or environment."""
        temporary = tempfile.TemporaryDirectory(prefix="six-backup-dispatch-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        scripts = self.root / "deploy" / "community"
        scripts.mkdir(parents=True)
        self.arguments = self.root / "preflight-arguments"
        self.unexpected = self.root / "unexpected-continuation"
        self.script = scripts / "backup.sh"
        source = (COMMUNITY / "backup.sh").read_text(encoding="utf-8")
        prefix, boundary, _ = source.partition("\nenv_value() {")
        self.assertTrue(boundary, "The fixture must stop before the first backup helper.")
        self.script.write_text(
            prefix + '\nprintf "unexpected continuation\\n" > "$BACKUP_TEST_UNEXPECTED"\nexit 99\n',
            encoding="utf-8",
        )
        preflight = scripts / "preflight.sh"
        preflight.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            'printf "%s\\0" "$@" > "$BACKUP_TEST_ARGUMENTS"\n'
            f"exit {PREFLIGHT_EXIT}\n",
            encoding="utf-8",
        )
        preflight.chmod(0o700)
        self.bash = shutil.which("bash")
        dirname = shutil.which("dirname")
        self.assertIsNotNone(self.bash)
        self.assertIsNotNone(dirname)
        commands = self.root / "commands"
        commands.mkdir()
        (commands / "dirname").symlink_to(str(dirname))
        # No inherited configuration, shell startup hooks, Docker, or backup tools.
        self.environment = {
            "PATH": str(commands),
            "LC_ALL": "C",
            "BACKUP_TEST_ARGUMENTS": str(self.arguments),
            "BACKUP_TEST_UNEXPECTED": str(self.unexpected),
        }

    def _run(
        self, arguments: list[str], override: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        if override is not None:
            environment["SIX_SELFHOST_ENV_FILE"] = override
        result = subprocess.run(
            [str(self.bash), str(self.script), *arguments],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertFalse(self.unexpected.exists(), "Dispatch continued after preflight failure.")
        return result

    def _assert_selected(
        self, arguments: list[str], selected: str, override: str | None = None
    ) -> None:
        result = self._run(arguments, override)
        self.assertEqual(result.returncode, PREFLIGHT_EXIT, result.stderr)
        self.assertEqual(self.arguments.read_bytes(), selected.encode() + b"\0")
        self.assertEqual(result.stdout, "")

    def test_default_environment_is_repository_relative(self) -> None:
        """No override preserves the existing checkout-local default."""
        self._assert_selected([], str(self.root / ".env.selfhost"))

    def test_environment_override_is_preserved(self) -> None:
        """The existing environment-variable interface remains supported."""
        selected = "/synthetic deployment/custom.env"
        self._assert_selected([], selected, override=selected)

    def test_empty_environment_override_retains_the_existing_default(self) -> None:
        """An empty optional environment variable keeps its previous fallback."""
        self._assert_selected([], str(self.root / ".env.selfhost"), override="")

    def test_positional_path_is_forwarded_without_reinterpretation(self) -> None:
        """Relative paths, whitespace, and shell punctuation remain one literal argument."""
        selected = "./synthetic configs/$(printf unexpected);file.env"
        self._assert_selected([selected], selected)

    def test_positional_path_takes_precedence_over_the_environment(self) -> None:
        """The selected Makefile or quickstart deployment wins over ambient selection."""
        selected = "/synthetic deployment/explicit.env"
        self._assert_selected([selected], selected, override="/synthetic/other.env")

    def test_explicit_empty_argument_is_rejected_before_preflight(self) -> None:
        """An explicit missing target must never fall back to a different deployment."""
        result = self._run([""], override="/synthetic/other.env")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.arguments.exists())
        self.assertIn("must not be empty", result.stderr)

    def test_extra_arguments_are_rejected_before_preflight(self) -> None:
        """An ambiguous invocation cannot select or operate on any deployment."""
        result = self._run(["/synthetic/one.env", "/synthetic/two.env"])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.arguments.exists())
        self.assertIn("Usage:", result.stderr)

    def test_an_empty_extra_argument_is_still_rejected(self) -> None:
        """Argument count is checked even when the extra argument is empty."""
        result = self._run(["/synthetic/one.env", ""])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.arguments.exists())

    def test_make_backup_preserves_a_path_with_spaces(self) -> None:
        """The documented shortcut forwards one selected path without running a backup."""
        selected = "/synthetic deployment/selected.env"
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", "backup", f"ENV_FILE={selected}"],
            cwd=COMMUNITY.parents[1],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            shlex.split(result.stdout), ["bash", "deploy/community/backup.sh", selected]
        )


if __name__ == "__main__":
    unittest.main()
