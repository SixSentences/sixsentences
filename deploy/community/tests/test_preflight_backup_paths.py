"""Check backup paths without creating backups or changing target permissions."""

from __future__ import annotations

import stat
import subprocess
import unittest
from pathlib import Path

import test_preflight_config_contract as config_contract


class PreflightBackupPathTests(unittest.TestCase):
    """Reuse the isolated preflight fixture without inheriting its test methods."""

    def setUp(self) -> None:
        """Create only temporary directories and links with an allowlisted command path."""
        self.fixture = config_contract.PreflightConfigurationContractTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.root = self.fixture.root
        self.checkout = self.fixture.checkout
        self.external = self.root / "external backups"
        self.external.mkdir()
        self.checkout_alias = self.root / "checkout-alias"
        self.checkout_alias.symlink_to(self.checkout, target_is_directory=True)
        self.external_alias = self.root / "external-alias"
        self.external_alias.symlink_to(self.external, target_is_directory=True)

    def _directories(self) -> dict[Path, int]:
        """Record fixture directory names and modes without following directory links."""
        return {
            path.relative_to(self.root): stat.S_IMODE(path.stat().st_mode)
            for path in (self.root, *self.root.rglob("*"))
            if not path.is_symlink() and path.is_dir()
        }

    def _run_path(self, path: str) -> subprocess.CompletedProcess[str]:
        """Run only preflight and assert that all fixture directory modes stay intact."""
        self.fixture.values["SIX_BACKUP_DIR"] = path
        self.fixture._write()
        before = self._directories()
        result = self.fixture._run()
        self.assertEqual(self._directories(), before, "Preflight changed backup directories.")
        return result

    def _assert_rejected(self, path: str) -> None:
        """Require the path guard to fail before even the Docker stub is called."""
        self.fixture._assert_rejected(self._run_path(path), key="SIX_BACKUP_DIR")

    def _assert_accepted(self, path: str) -> None:
        """Allow only the fixture's Compose version and configuration validation calls."""
        self.fixture._assert_configured(self._run_path(path), self.fixture.target)

    def test_relative_paths_and_filesystem_root_are_rejected(self) -> None:
        """Absolute syntax alone must not permit changing root-directory permissions."""
        for path in ("backups", "./backups", "../backups", "/", "//", "////", "/./", "/../"):
            with self.subTest(path=path):
                self._assert_rejected(path)

    def test_root_aliases_and_parent_components_are_rejected(self) -> None:
        """Physical root aliases remain unsafe after trailing or missing components."""
        alias = self.root / "root-alias"
        alias.symlink_to("/", target_is_directory=True)
        root_via_parents = f"{self.root}/" + "/".join(".." for _ in self.root.parts[1:])
        for path in (
            str(alias),
            f"{alias}/",
            f"{alias}/./",
            f"{alias}/missing/..",
            root_via_parents,
        ):
            with self.subTest(path=path):
                self._assert_rejected(path)

    def test_checkout_and_its_descendants_are_rejected(self) -> None:
        """Neither existing checkout directories nor prospective backup children qualify."""
        for path in (
            str(self.checkout),
            f"{self.checkout}/",
            f"{self.checkout}/.",
            f"{self.checkout}/deploy",
            f"{self.checkout}/missing/deep/backups",
        ):
            with self.subTest(path=path):
                self._assert_rejected(path)

    def test_dot_components_cannot_hide_a_checkout_target(self) -> None:
        """Resolve traversal through existing and not-yet-created directory components."""
        for path in (
            f"{self.external}/../checkout",
            f"{self.external}/../checkout/backups",
            f"{self.root}//./checkout/backups",
            f"{self.root}/missing/../checkout/backups",
            f"{self.root}/missing/deep/../../checkout/backups",
        ):
            with self.subTest(path=path):
                self._assert_rejected(path)

    def test_symlinks_cannot_hide_a_checkout_target(self) -> None:
        """Resolve links even after missing components and before processing parent steps."""
        nested_alias = self.root / "nested-checkout-alias"
        nested_alias.symlink_to(self.checkout / "deploy", target_is_directory=True)
        for path in (
            str(self.checkout_alias),
            f"{self.checkout_alias}/backups",
            f"{self.checkout_alias}/missing/deep/backups",
            f"{self.root}/missing/../checkout-alias/backups",
            f"{self.root}/missing/deeper/../../checkout-alias/backups",
            f"{nested_alias}/../backups",
        ):
            with self.subTest(path=path):
                self._assert_rejected(path)

    def test_broken_links_and_file_components_are_rejected(self) -> None:
        """Existing non-directories must not be treated as missing backup parents."""
        regular = self.external / "regular-file"
        regular.write_text("synthetic fixture\n", encoding="utf-8")
        broken = self.root / "broken-link"
        broken.symlink_to(self.root / "missing-target", target_is_directory=True)
        file_alias = self.root / "file-link"
        file_alias.symlink_to(regular)
        for path in (
            str(regular),
            f"{regular}/backups",
            f"{regular}/../backups",
            str(broken),
            f"{broken}/backups",
            f"{broken}/../backups",
            f"{file_alias}/backups",
        ):
            with self.subTest(path=path):
                self._assert_rejected(path)

    def test_physical_parent_controls_cannot_hide_a_checkout_link(self) -> None:
        """Preserve physical path bytes even when the configured alias is plain ASCII."""
        for index, ending in enumerate(("\n", "\n\n", "\r")):
            with self.subTest(ending=repr(ending)):
                physical = self.root / f"physical-parent{ending}"
                physical.mkdir()
                (physical / "checkout-link").symlink_to(self.checkout, target_is_directory=True)
                alias = self.root / f"plain-parent-alias-{index}"
                alias.symlink_to(physical, target_is_directory=True)
                result = self._run_path(f"{alias}/checkout-link/backups")
                self.fixture._assert_rejected(result, key="SIX_BACKUP_DIR")
                self.assertIn("control characters", result.stderr)

    def test_symlink_cycles_are_rejected(self) -> None:
        """A link cycle fails closed instead of being interpreted as a new directory."""
        first = self.root / "cycle-first"
        second = self.root / "cycle-second"
        first.symlink_to(second, target_is_directory=True)
        second.symlink_to(first, target_is_directory=True)
        for path in (f"{first}/backups", f"{first}/../backups"):
            with self.subTest(path=path):
                self._assert_rejected(path)

    def test_external_existing_and_missing_paths_are_accepted_without_writes(self) -> None:
        """Preserve spaces, missing parents, trailing separators and nearby sibling names."""
        for path in (
            str(self.external),
            f"{self.external}/",
            f"{self.external}//./",
            f"{self.external}/missing/deep/backups",
            f"{self.root}/new backup root/missing/backups",
            f"{self.root}/checkout-sibling/backups",
            f"{self.checkout}/../external backups/new",
            f"{self.external}/missing/../backups",
        ):
            with self.subTest(path=path):
                self._assert_accepted(path)

    def test_external_symlink_parents_are_accepted_without_writes(self) -> None:
        """Keep physical parent aliases such as macOS /var usable for external backups."""
        nested = self.external / "nested"
        nested.mkdir()
        nested_alias = self.root / "nested-external-alias"
        nested_alias.symlink_to(nested, target_is_directory=True)
        unicode_parent = self.root / "external-\u00fc"
        unicode_parent.mkdir()
        unicode_alias = self.root / "unicode-parent-alias"
        unicode_alias.symlink_to(unicode_parent, target_is_directory=True)
        for path in (
            f"{self.external_alias}/backups",
            f"{self.external_alias}/missing/deep/backups",
            f"{self.root}/missing/../external-alias/backups",
            f"{nested_alias}/../backups",
            f"{unicode_alias}/backups",
        ):
            with self.subTest(path=path):
                self._assert_accepted(path)


if __name__ == "__main__":
    unittest.main()
