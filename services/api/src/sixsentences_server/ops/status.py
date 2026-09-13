"""Operational capacity signals shared by health checks and write guards."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


class StorageCapacityError(RuntimeError):
    """The host cannot safely accept another persistent write."""


def storage_status(data_dir: Path, reserve_bytes: int) -> dict[str, Any]:
    """Return disk capacity without exposing host paths."""

    data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(data_dir)
    reserve = max(0, reserve_bytes)
    status = "ok" if usage.free >= reserve else "critical"
    return {
        "status": status,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "reserve_bytes": reserve,
        "percent_used": round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
    }


def require_storage_capacity(
    data_dir: Path,
    reserve_bytes: int,
    incoming_bytes: int,
) -> None:
    """Keep the configured emergency reserve after the pending write."""

    status = storage_status(data_dir, reserve_bytes)
    required = status["reserve_bytes"] + max(0, incoming_bytes)
    if status["free_bytes"] < required:
        raise StorageCapacityError(
            "Storage is temporarily unavailable; the operator has been notified."
        )
