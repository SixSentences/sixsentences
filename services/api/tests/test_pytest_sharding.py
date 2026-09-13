"""Regression tests for complete deterministic CI test partitioning."""

from itertools import combinations

import pytest
from _pytest_sharding import (
    ShardSpec,
    parse_shard_spec,
    select_shard_nodeids,
    shard_for_nodeid,
)


def test_sharding_defaults_to_the_original_unsharded_collection() -> None:
    nodeids = [
        "tests/test_alpha.py::test_first",
        "tests/test_alpha.py::test_second[value]",
        "tests/test_beta.py::test_third",
    ]

    spec = parse_shard_spec(None, None)

    assert spec == ShardSpec(count=1, index=0)
    assert select_shard_nodeids(nodeids, spec) == nodeids


def test_four_shards_are_deterministic_disjoint_and_complete() -> None:
    nodeids = [
        f"tests/test_module_{module:03d}.py::test_case[{case}]"
        for module in range(64)
        for case in range(8)
    ]
    first = [select_shard_nodeids(nodeids, ShardSpec(count=4, index=index)) for index in range(4)]
    repeated = [
        select_shard_nodeids(nodeids, ShardSpec(count=4, index=index)) for index in range(4)
    ]

    assert first == repeated
    assert all(first)
    assert sum(len(shard) for shard in first) == len(nodeids)
    assert set().union(*(set(shard) for shard in first)) == set(nodeids)
    for left, right in combinations(first, 2):
        assert set(left).isdisjoint(right)


def test_sha256_assignment_is_stable_for_known_nodeids() -> None:
    expected = {
        "tests/test_known.py::test_case[4]": 0,
        "tests/test_known.py::test_case[0]": 1,
        "tests/test_known.py::test_case[3]": 2,
        "tests/test_known.py::test_case[1]": 3,
        "tests/test_unicode.py::test_case[grüner-✓]": 3,
    }

    assert {nodeid: shard_for_nodeid(nodeid, 4) for nodeid in expected} == expected


@pytest.mark.parametrize(
    ("count", "index", "message"),
    [
        (None, 0, "must be provided together"),
        (4, None, "must be provided together"),
        (0, 0, "must be at least 1"),
        (-1, 0, "must be at least 1"),
        (4, -1, "must be between 0 and 3"),
        (4, 4, "must be between 0 and 3"),
    ],
)
def test_invalid_shard_options_fail_closed(
    count: int | None,
    index: int | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_shard_spec(count, index)
