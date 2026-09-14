"""Whoever changes this tree has to be able to rewrite its manifest."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_audit() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_community_export.py"
    spec = importlib.util.spec_from_file_location("audit_community_export", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _load_audit()


def _tree(root: Path, content: str) -> None:
    """Write a minimal tree whose manifest matches it exactly."""

    (root / "kept.txt").write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    (root / AUDIT.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "files": [
                    {
                        "bytes": len(content.encode("utf-8")),
                        "mode": "0644",
                        "path": "kept.txt",
                        "sha256": digest,
                    }
                ],
                "format": 1,
                "namespace": "sixsentences_server",
                "source_commit": AUDIT.EXPECTED_SOURCE_COMMIT,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_a_changed_file_is_reported_with_the_command_that_fixes_it(tmp_path: Path) -> None:
    _tree(tmp_path, "before\n")
    AUDIT.verify_manifest(tmp_path, AUDIT.included_files(tmp_path))

    (tmp_path / "kept.txt").write_text("after\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as failure:
        AUDIT.verify_manifest(tmp_path, AUDIT.included_files(tmp_path))

    # A contributor who has never seen this manifest reads the failure, not the
    # script: the message has to name the way out.
    assert "kept.txt" in str(failure.value)
    assert "--refresh-manifest" in str(failure.value)


def test_refreshing_records_the_tree_and_names_what_it_recorded(tmp_path: Path) -> None:
    _tree(tmp_path, "before\n")
    (tmp_path / "kept.txt").write_text("after\n", encoding="utf-8")
    (tmp_path / "added.txt").write_text("new\n", encoding="utf-8")

    changed = AUDIT.refresh_manifest(tmp_path, AUDIT.included_files(tmp_path))

    assert changed == ("added:added.txt", "changed:kept.txt")
    AUDIT.verify_manifest(tmp_path, AUDIT.included_files(tmp_path))


def test_refreshing_keeps_the_export_identity(tmp_path: Path) -> None:
    """The refreshed manifest still names the export it descends from."""

    _tree(tmp_path, "before\n")
    AUDIT.refresh_manifest(tmp_path, AUDIT.included_files(tmp_path))
    payload = json.loads((tmp_path / AUDIT.MANIFEST_NAME).read_text(encoding="utf-8"))

    assert payload["source_commit"] == AUDIT.EXPECTED_SOURCE_COMMIT
    assert payload["format"] == 1
