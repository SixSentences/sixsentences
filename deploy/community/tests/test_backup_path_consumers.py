"""Check physical path consumption without executing backup or restore operations."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

COMMUNITY = Path(__file__).resolve().parents[1]
BACKUP_SOURCE = (COMMUNITY / "backup.sh").read_text(encoding="utf-8")
RESTORE_SOURCE = (COMMUNITY / "restore.sh").read_text(encoding="utf-8")


class BackupPathConsumerTests(unittest.TestCase):
    """Execute only the four source-extracted directory changes in synthetic trees."""

    def setUp(self) -> None:
        """Prepare deliberately different logical and physical resolutions, all local."""
        temporary = tempfile.TemporaryDirectory(prefix="six-backup-path-consumers-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        logical = self.root / "logical parent"
        physical = self.root / "physical parent"
        logical.mkdir()
        physical.mkdir()
        child = physical / "child directory"
        child.mkdir()
        alias = logical / "alias parent"
        alias.symlink_to(child, target_is_directory=True)
        shortcut = logical / "parent shortcut"
        shortcut.symlink_to(physical, target_is_directory=True)
        self.expected = physical / "target with spaces"
        self.expected.mkdir()
        (self.expected / "nested child").mkdir()
        different = logical / "target with spaces"
        different.mkdir()
        (different / "nested child").mkdir()
        self.assertNotEqual(self.expected, different)
        self.paths = (
            ("symlink followed by parent", alias / ".." / "target with spaces"),
            ("nested parent steps", alias / ".." / "target with spaces" / "nested child" / ".."),
            ("symlinked parent", shortcut / "target with spaces"),
            ("plain absolute path", self.expected),
        )
        for _, path in self.paths:
            self.assertEqual(path.resolve(strict=True), self.expected)
        newline_paths = []
        (physical / "newline target").mkdir()
        for count in (1, 2):
            name = "newline target" + "\n" * count
            target = physical / name
            target.mkdir()
            newline_paths.extend(
                (
                    (f"direct target ending in {count} newline bytes", target, target),
                    (f"alias parent and {count} newline bytes", shortcut / name, target),
                )
            )
        self.newline_paths = tuple(newline_paths)
        self.bash = shutil.which("bash")
        self.assertIsNotNone(self.bash)
        cdpath = self.root / "misleading search directory"
        (cdpath / "target with spaces").mkdir(parents=True)
        # Only Bash builtins are needed; no operator configuration or external tools.
        self.environment = {
            "PATH": str(self.root / "no-external-commands"),
            "LC_ALL": "C",
            "CDPATH": str(cdpath),
        }

    def _backup_changes(self) -> list[str]:
        lines = [
            line.strip()
            for line in BACKUP_SOURCE.splitlines()
            if line.strip().startswith("cd ") and '"$TEMP_DIR"' in line
        ]
        self.assertEqual(len(lines), 2, "Both checksum branches must be exercised.")
        for line in lines:
            self.assertRegex(line, r'^cd(?: -P)?(?: --)? "\$TEMP_DIR"$')
        return lines

    def _restore_assignment(self, result: str, variable: str) -> str:
        lines = [
            (index, line)
            for index, line in enumerate(RESTORE_SOURCE.splitlines())
            if line.startswith(f"{result}=")
        ]
        self.assertIn(len(lines), (1, 2))
        capture, strip = self._lossless_assignment(result, variable).splitlines()
        if lines[0][1] == capture:
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[1], (lines[0][0] + 1, strip))
            return "\n".join(line for _, line in lines)
        # Permit the exact previous read-only forms so their byte-loss regression
        # can be demonstrated against baseline source loaded only into memory.
        self.assertEqual(len(lines), 1)
        expression = lines[0][1]
        self.assertIn(
            expression,
            (
                f'{result}="$(CDPATH= cd -- "${variable}" && pwd -P)"',
                f'{result}="$(CDPATH= cd -P -- "${variable}" && pwd -P)"',
            ),
        )
        return expression

    def _lossless_assignment(self, result: str, variable: str) -> str:
        return (
            f'{result}="$(CDPATH= cd -P -- "${variable}" && printf \'%s/\' "$PWD")"\n'
            f'{result}="${{{result}%/}}"'
        )

    def _assert_physical(
        self,
        expression: str,
        variable: str,
        output: str | None = None,
        *,
        paths: tuple[tuple[str, Path, Path], ...] | None = None,
    ) -> None:
        # The extractors allow only a cd builtin or exact path capture/strip, never
        # a script prefix, checksum command, trap, file operation, or Docker call.
        report = f'printf "%s\\0" "${output or "PWD"}"'
        script = f"set -euo pipefail\n{expression}\n{report}\n"
        cases = (
            paths
            if paths is not None
            else tuple((label, path, self.expected) for label, path in self.paths)
        )
        for label, path, expected in cases:
            with self.subTest(path=label):
                result = subprocess.run(
                    [str(self.bash), "-c", script],
                    cwd=self.root,
                    env={**self.environment, variable: str(path)},
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                self.assertEqual(result.stdout, str(expected).encode() + b"\0")

    def test_sha256sum_branch_changes_to_the_physical_temporary_directory(self) -> None:
        """The first actual checksum-branch cd follows filesystem parent traversal."""
        self._assert_physical(self._backup_changes()[0], "TEMP_DIR")

    def test_shasum_branch_changes_to_the_physical_temporary_directory(self) -> None:
        """The fallback checksum-branch cd uses the same physical directory."""
        self._assert_physical(self._backup_changes()[1], "TEMP_DIR")

    def test_restore_canonical_root_matches_the_filesystem_destination(self) -> None:
        """The configured root cannot resolve to a different logical parent."""
        expression = self._restore_assignment("CANONICAL_ROOT", "CONFIGURED_BACKUP_ROOT")
        self._assert_physical(expression, "CONFIGURED_BACKUP_ROOT", "CANONICAL_ROOT")

    def test_restore_canonical_backup_matches_the_filesystem_destination(self) -> None:
        """The selected backup follows physical traversal before containment checks."""
        expression = self._restore_assignment("CANONICAL_BACKUP", "BACKUP_DIR")
        self._assert_physical(expression, "BACKUP_DIR", "CANONICAL_BACKUP")

    def test_checksum_branches_preserve_newline_path_bytes(self) -> None:
        """Both actual directory changes retain every physical target-name byte."""
        for index, expression in enumerate(self._backup_changes()):
            with self.subTest(branch=index):
                self._assert_physical(expression, "TEMP_DIR", paths=self.newline_paths)

    def test_restore_canonical_root_preserves_trailing_newlines(self) -> None:
        """Command substitution must not trim the canonical root's final bytes."""
        expression = self._restore_assignment("CANONICAL_ROOT", "CONFIGURED_BACKUP_ROOT")
        self._assert_physical(
            expression,
            "CONFIGURED_BACKUP_ROOT",
            "CANONICAL_ROOT",
            paths=self.newline_paths,
        )

    def test_restore_canonical_backup_preserves_trailing_newlines(self) -> None:
        """A backup ending in newline bytes must not select its existing trimmed sibling."""
        expression = self._restore_assignment("CANONICAL_BACKUP", "BACKUP_DIR")
        self._assert_physical(
            expression,
            "BACKUP_DIR",
            "CANONICAL_BACKUP",
            paths=self.newline_paths,
        )

    def test_all_four_consumers_explicitly_select_physical_lossless_paths(self) -> None:
        """Bind consumers to physical traversal and adjacent lossless path capture."""
        for index, expression in enumerate(self._backup_changes()):
            with self.subTest(branch=index):
                self.assertEqual(expression, 'cd -P -- "$TEMP_DIR"')
        for result, variable in (
            ("CANONICAL_ROOT", "CONFIGURED_BACKUP_ROOT"),
            ("CANONICAL_BACKUP", "BACKUP_DIR"),
        ):
            with self.subTest(assignment=result):
                self.assertEqual(
                    self._restore_assignment(result, variable),
                    self._lossless_assignment(result, variable),
                )


if __name__ == "__main__":
    unittest.main()
