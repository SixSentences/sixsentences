"""Boundary tests for the model-facing tool-schema subset."""

from __future__ import annotations

import pytest

from sixsentences_server.agent.schema import tool_argument_error, validate_tool_schema


def test_nested_closed_schema_accepts_one_valid_payload() -> None:
    schema = {
        "type": "object",
        "properties": {
            "edit": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 20},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["claim", "method"]},
                        "maxItems": 2,
                        "uniqueItems": True,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            }
        },
        "required": ["edit"],
        "additionalProperties": False,
    }

    validate_tool_schema(schema)

    assert tool_argument_error(schema, {"edit": {"path": "paper.tex", "tags": ["claim"]}}) == ""


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"edit": {}}, "Missing required"),
        ({"edit": {"path": "paper.tex", "extra": True}}, "Unknown tool arguments"),
        ({"edit": {"path": "x" * 21}}, "too long"),
        ({"edit": {"path": "paper.tex", "tags": ["claim", "claim"]}}, "unique"),
        ({"edit": {"path": "paper.tex", "tags": ["secret"]}}, "allowed values"),
    ],
)
def test_nested_constraints_fail_before_handler_input(
    arguments: dict[str, object],
    message: str,
) -> None:
    schema = {
        "type": "object",
        "properties": {
            "edit": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 20},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["claim", "method"]},
                        "maxItems": 2,
                        "uniqueItems": True,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            }
        },
        "required": ["edit"],
        "additionalProperties": False,
    }

    assert message in tool_argument_error(schema, arguments)


def test_enum_only_schema_keeps_json_boolean_distinct_from_number() -> None:
    schema = {
        "type": "object",
        "properties": {"choice": {"enum": [1]}},
        "required": ["choice"],
        "additionalProperties": False,
    }

    validate_tool_schema(schema)

    assert tool_argument_error(schema, {"choice": 1}) == ""
    assert "allowed values" in tool_argument_error(schema, {"choice": True})


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ({"type": "object", "oneOf": []}, "unsupported keyword"),
        (
            {"type": "object", "properties": {"value": {"minLength": 1}}},
            "minLength requires string type",
        ),
        (
            {"type": "object", "properties": {}, "required": ["missing"]},
            "undeclared properties",
        ),
        (
            {
                "type": "object",
                "properties": {"value": {"type": "string", "pattern": "^[a-z]+$"}},
            },
            "unsupported keyword",
        ),
        (
            {
                "type": "object",
                "properties": {"value": {"type": "number", "minimum": 2, "maximum": 1}},
            },
            "minimum must not exceed",
        ),
        (
            {"type": "object", "properties": {"value": {"enum": [1, 1.0]}}},
            "enum values must be unique",
        ),
        (
            {
                "type": "object",
                "properties": {"value": {"type": "string", "enum": [1]}},
            },
            "enum values must match string type",
        ),
    ],
)
def test_invalid_or_partially_enforced_schemas_fail_registration(
    schema: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_tool_schema(schema)


def test_numeric_bounds_reject_boolean_and_non_finite_magnitude() -> None:
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 0, "maximum": 10}},
        "required": ["count"],
        "additionalProperties": False,
    }

    assert "JSON type integer" in tool_argument_error(schema, {"count": True})
    assert "finite JSON number" in tool_argument_error(schema, {"count": 10**400})


def test_open_schema_rejects_non_finite_and_excessively_nested_json() -> None:
    schema = {"type": "object"}
    nested: object = "leaf"
    for _ in range(14):
        nested = {"next": nested}

    assert "finite JSON" in tool_argument_error(schema, {"value": float("nan")})
    assert "bounded, finite JSON" in tool_argument_error(schema, {"value": nested})


def test_enum_values_are_depth_bounded_at_registration() -> None:
    nested: object = "leaf"
    for _ in range(10):
        nested = [nested]
    schema = {
        "type": "object",
        "properties": {"choice": {"enum": [nested]}},
    }

    with pytest.raises(ValueError, match="nesting limit"):
        validate_tool_schema(schema)
