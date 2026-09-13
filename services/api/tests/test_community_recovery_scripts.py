"""Execute the shipped recovery shell entrypoints far enough to parse and dispatch."""

import os
import subprocess
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_backup_and_restore_scripts_parse_and_expose_help() -> None:
    for relative in ("deploy/community/backup.sh", "deploy/community/restore.sh"):
        script = SERVICE_ROOT / relative
        assert os.access(script, os.X_OK)
        subprocess.run(["bash", "-n", str(script)], check=True)
        completed = subprocess.run(
            ["bash", str(script), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "Usage:" in completed.stderr


def test_restore_refuses_to_dispatch_without_explicit_confirmation(tmp_path: Path) -> None:
    script = SERVICE_ROOT / "deploy/community/restore.sh"
    completed = subprocess.run(
        ["bash", str(script), "--backup", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "--confirm RESTORE" in completed.stderr
