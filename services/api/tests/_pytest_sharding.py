"""Deterministic, opt-in pytest sharding for isolated CI runners."""

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

import pytest


@dataclass(frozen=True, slots=True)
class ShardSpec:
    """A validated zero-based shard selection."""

    count: int
    index: int


def parse_shard_spec(count: int | None, index: int | None) -> ShardSpec:
    """Validate paired CLI values while preserving an unsharded default."""

    if count is None and index is None:
        return ShardSpec(count=1, index=0)
    if count is None or index is None:
        raise ValueError("--shard-count and --shard-index must be provided together")
    if count < 1:
        raise ValueError("--shard-count must be at least 1")
    if index < 0 or index >= count:
        raise ValueError(f"--shard-index must be between 0 and {count - 1}")
    return ShardSpec(count=count, index=index)


def shard_for_nodeid(nodeid: str, shard_count: int) -> int:
    """Map a final pytest node ID to exactly one stable zero-based shard."""

    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    digest = sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % shard_count


def select_shard_nodeids(nodeids: Sequence[str], spec: ShardSpec) -> list[str]:
    """Select one shard without changing collection order."""

    if spec.count == 1:
        return list(nodeids)
    return [nodeid for nodeid in nodeids if shard_for_nodeid(nodeid, spec.count) == spec.index]


_SHARD_SPEC = pytest.StashKey[ShardSpec]()


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register opt-in sharding arguments."""

    group = parser.getgroup("sixsentences-sharding")
    group.addoption(
        "--shard-count",
        type=int,
        default=None,
        metavar="COUNT",
        help="Total number of deterministic pytest shards.",
    )
    group.addoption(
        "--shard-index",
        type=int,
        default=None,
        metavar="INDEX",
        help="Zero-based deterministic pytest shard to run.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Fail closed on incomplete or invalid shard configuration."""

    try:
        spec = parse_shard_spec(
            config.getoption("shard_count"),
            config.getoption("shard_index"),
        )
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config.stash[_SHARD_SPEC] = spec


def pytest_report_header(config: pytest.Config) -> str | None:
    """Make the active deterministic partition visible in CI logs."""

    spec = config.stash[_SHARD_SPEC]
    if spec.count == 1:
        return None
    return (
        f"deterministic test shard {spec.index + 1}/{spec.count} (SHA-256 of final pytest node IDs)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect every item owned by another deterministic shard."""

    spec = config.stash[_SHARD_SPEC]
    if spec.count == 1:
        return

    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        belongs_to_shard = shard_for_nodeid(item.nodeid, spec.count) == spec.index
        target = selected if belongs_to_shard else deselected
        target.append(item)

    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
