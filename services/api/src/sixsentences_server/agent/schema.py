"""Strict validation for the JSON Schema subset used by agent tools.

Tool schemas are server-owned, but their values are model-produced and cross a
trust boundary.  This module deliberately implements only the small schema
vocabulary the agent runtime can enforce.  Unsupported keywords fail when a
tool is registered instead of being shown to the model and silently ignored at
execution time.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, Final, cast

_MAX_SCHEMA_DEPTH: Final = 8
_MAX_IDENTITY_ITEMS: Final = 100
_MAX_IDENTITY_STRING_CHARS: Final = 24_000
_MAX_ARGUMENT_DEPTH: Final = 12
_MAX_ARGUMENT_CONTAINER_ITEMS: Final = 1_000
_MAX_ARGUMENT_NODES: Final = 5_000
_MAX_ARGUMENT_STRING_CHARS: Final = 120_000
_MAX_ARGUMENT_TOTAL_STRING_CHARS: Final = 240_000
_SUPPORTED_TYPES: Final = frozenset({"array", "boolean", "integer", "number", "object", "string"})
_SUPPORTED_KEYWORDS: Final = frozenset(
    {
        "$id",
        "$schema",
        "additionalProperties",
        "const",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "properties",
        "required",
        "title",
        "type",
        "uniqueItems",
    }
)


def validate_tool_schema(schema: Mapping[str, Any]) -> None:
    """Validate one tool schema before it becomes model-visible.

    The root must describe an object. Nested schemas may omit ``type`` when an
    ``enum`` or ``const`` alone is sufficient, matching JSON Schema semantics.
    """

    _validate_schema_node(schema, path="schema", depth=0)
    if schema.get("type", "object") != "object":
        raise ValueError("tool schema root must have type 'object'")


def tool_argument_error(
    schema: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> str:
    """Return the first model-safe validation error, or an empty string."""

    try:
        canonical = canonical_tool_arguments(arguments)
    except ValueError:
        return "Tool arguments must contain bounded, finite JSON values."
    return _value_error(schema, canonical, path="arguments", depth=0)


def canonical_tool_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return an isolated canonical JSON snapshot of model tool arguments.

    The agent decision parser produces JSON-shaped values, but callbacks are
    ordinary Python functions and may mutate nested containers.  A canonical
    round trip gives authorization, preflight, signatures, events and execution
    one immutable source payload.  Callers must still hand each callback its own
    deep copy of this returned snapshot.
    """

    budget = {"nodes": 0, "string_chars": 0}
    _validate_argument_json(arguments, path="arguments", depth=0, budget=budget)
    try:
        encoded = json.dumps(
            dict(arguments),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("tool arguments must contain finite JSON values") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - root is forced above
        raise ValueError("tool arguments must be a JSON object")
    return cast(dict[str, Any], decoded)


def _validate_argument_json(
    value: Any,
    *,
    path: str,
    depth: int,
    budget: dict[str, int],
) -> None:
    """Fail closed before serializing an open or partially open argument tree."""

    if depth > _MAX_ARGUMENT_DEPTH:
        raise ValueError(f"{path} exceeds the argument nesting limit")
    budget["nodes"] += 1
    if budget["nodes"] > _MAX_ARGUMENT_NODES:
        raise ValueError("tool arguments exceed the node limit")

    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be a finite JSON number")
        return
    if isinstance(value, str):
        if len(value) > _MAX_ARGUMENT_STRING_CHARS:
            raise ValueError(f"{path} exceeds the argument string limit")
        budget["string_chars"] += len(value)
        if budget["string_chars"] > _MAX_ARGUMENT_TOTAL_STRING_CHARS:
            raise ValueError("tool arguments exceed the total string limit")
        return
    if isinstance(value, list):
        if len(value) > _MAX_ARGUMENT_CONTAINER_ITEMS:
            raise ValueError(f"{path} exceeds the argument item limit")
        for index, item in enumerate(value):
            _validate_argument_json(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                budget=budget,
            )
        return
    if isinstance(value, dict):
        if len(value) > _MAX_ARGUMENT_CONTAINER_ITEMS:
            raise ValueError(f"{path} exceeds the argument property limit")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} object keys must be strings")
            _validate_argument_json(
                key,
                path=f"{path}.<key>",
                depth=depth + 1,
                budget=budget,
            )
            _validate_argument_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
                budget=budget,
            )
        return
    raise ValueError(f"{path} must contain JSON values")


def _validate_schema_node(
    schema: Mapping[str, Any],
    *,
    path: str,
    depth: int,
) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        raise ValueError(f"{path} exceeds the maximum nesting depth")
    unknown = sorted(str(key) for key in schema if key not in _SUPPORTED_KEYWORDS)
    if unknown:
        raise ValueError(f"{path} uses unsupported keyword(s): {', '.join(unknown)}")

    schema_type = schema.get("type", "object") if depth == 0 else schema.get("type")
    if schema_type is not None and schema_type not in _SUPPORTED_TYPES:
        raise ValueError(f"{path}.type is unsupported")

    keyword_types = {
        "properties": {"object"},
        "required": {"object"},
        "additionalProperties": {"object"},
        "minProperties": {"object"},
        "maxProperties": {"object"},
        "items": {"array"},
        "minItems": {"array"},
        "maxItems": {"array"},
        "uniqueItems": {"array"},
        "minLength": {"string"},
        "maxLength": {"string"},
        "minimum": {"integer", "number"},
        "maximum": {"integer", "number"},
        "exclusiveMinimum": {"integer", "number"},
        "exclusiveMaximum": {"integer", "number"},
    }
    for keyword, allowed_types in keyword_types.items():
        if keyword in schema and schema_type not in allowed_types:
            allowed = " or ".join(sorted(allowed_types))
            raise ValueError(f"{path}.{keyword} requires {allowed} type")

    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ValueError(f"{path}.enum must be a non-empty list")
    if isinstance(enum, list):
        identities = [_json_identity(item, path=f"{path}.enum") for item in enum]
        if len(set(identities)) != len(identities):
            raise ValueError(f"{path}.enum values must be unique")
        if schema_type is not None and any(
            not _matches_type(str(schema_type), item) for item in enum
        ):
            raise ValueError(f"{path}.enum values must match {schema_type} type")
    if "const" in schema:
        _json_identity(schema["const"], path=f"{path}.const")
        if schema_type is not None and not _matches_type(
            str(schema_type),
            schema["const"],
        ):
            raise ValueError(f"{path}.const must match {schema_type} type")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise ValueError(f"{path}.properties must be an object")
        for raw_name, child in properties.items():
            name = str(raw_name)
            if not isinstance(raw_name, str) or not raw_name:
                raise ValueError(f"{path}.properties keys must be non-empty strings")
            if not isinstance(child, Mapping):
                raise ValueError(f"{path}.properties.{name} must be an object")
            _validate_schema_node(
                child,
                path=f"{path}.properties.{name}",
                depth=depth + 1,
            )

    required = schema.get("required")
    if required is not None:
        if (
            not isinstance(required, list)
            or any(not isinstance(name, str) or not name for name in required)
            or len(set(required)) != len(required)
        ):
            raise ValueError(f"{path}.required must contain unique non-empty strings")
        property_names = set(properties) if isinstance(properties, Mapping) else set()
        missing_properties = [name for name in required if name not in property_names]
        if missing_properties:
            raise ValueError(
                f"{path}.required names undeclared properties: " + ", ".join(missing_properties)
            )

    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        raise ValueError(f"{path}.additionalProperties must be boolean")

    items = schema.get("items")
    if items is not None:
        if not isinstance(items, Mapping):
            raise ValueError(f"{path}.items requires an object schema and array type")
        _validate_schema_node(items, path=f"{path}.items", depth=depth + 1)

    for name in (
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
    ):
        value = schema.get(name)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f"{path}.{name} must be a non-negative integer")
    for minimum_name, maximum_name in (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
        ("minProperties", "maxProperties"),
    ):
        minimum = schema.get(minimum_name)
        maximum = schema.get(maximum_name)
        if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
            raise ValueError(f"{path}.{minimum_name} must not exceed {maximum_name}")

    for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        value = schema.get(name)
        if value is not None and not _finite_number(value):
            raise ValueError(f"{path}.{name} must be a finite number")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if _finite_number(minimum) and _finite_number(maximum):
        finite_minimum = cast(int | float, minimum)
        finite_maximum = cast(int | float, maximum)
        if finite_minimum > finite_maximum:
            raise ValueError(f"{path}.minimum must not exceed maximum")

    unique_items = schema.get("uniqueItems")
    if unique_items is not None and not isinstance(unique_items, bool):
        raise ValueError(f"{path}.uniqueItems must be boolean")


def _finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _value_error(
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: str,
    depth: int,
) -> str:
    if depth > _MAX_SCHEMA_DEPTH:
        return f"Tool argument {path!r} is nested too deeply."

    if "const" in schema:
        try:
            const_matches = _json_identity(value, path=path) == _json_identity(
                schema["const"],
                path=path,
            )
        except ValueError:
            const_matches = False
        if not const_matches:
            return f"Tool argument {path!r} does not match the required value."
    enum = schema.get("enum")
    if isinstance(enum, list):
        try:
            value_identity = _json_identity(value, path=path)
            enum_matches = any(value_identity == _json_identity(item, path=path) for item in enum)
        except ValueError:
            enum_matches = False
        if not enum_matches:
            return f"Tool argument {path!r} is not one of the allowed values."

    expected = schema.get("type", "object") if depth == 0 else schema.get("type")
    if expected is not None and not _matches_type(str(expected), value):
        return f"Tool argument {path!r} must have JSON type {expected}."

    if expected == "object":
        assert isinstance(value, dict)
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        required_names = required if isinstance(required, list) else []
        missing = [name for name in required_names if name not in value]
        if missing:
            return f"Missing required tool arguments at {path!r}: " + ", ".join(missing)
        if schema.get("additionalProperties") is False:
            unknown = [str(name) for name in value if name not in property_map]
            if unknown:
                return f"Unknown tool arguments at {path!r}: " + ", ".join(unknown)
        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if isinstance(min_properties, int) and len(value) < min_properties:
            return f"Tool argument {path!r} has too few properties."
        if isinstance(max_properties, int) and len(value) > max_properties:
            return f"Tool argument {path!r} has too many properties."
        for name, item in value.items():
            child = property_map.get(name)
            if isinstance(child, Mapping):
                error = _value_error(
                    child,
                    item,
                    path=f"{path}.{name}",
                    depth=depth + 1,
                )
                if error:
                    return error

    if expected == "array":
        assert isinstance(value, list)
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            return f"Tool argument {path!r} has too few items."
        if isinstance(max_items, int) and len(value) > max_items:
            return f"Tool argument {path!r} has too many items."
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                error = _value_error(
                    items,
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                )
                if error:
                    return error
        if schema.get("uniqueItems") is True:
            try:
                canonical = [
                    _json_identity(item, path=f"{path}[{index}]")
                    for index, item in enumerate(value)
                ]
            except ValueError:
                return f"Tool argument {path!r} contains an invalid JSON value."
            if len(set(canonical)) != len(canonical):
                return f"Tool argument {path!r} must contain unique items."

    if expected == "string":
        assert isinstance(value, str)
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            return f"Tool argument {path!r} is too short."
        if isinstance(max_length, int) and len(value) > max_length:
            return f"Tool argument {path!r} is too long."
    if expected in {"integer", "number"}:
        if not _finite_number(value):
            return f"Tool argument {path!r} must be a finite JSON number."
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if _finite_number(minimum) and value < minimum:
            return f"Tool argument {path!r} is below the allowed minimum."
        if _finite_number(maximum) and value > maximum:
            return f"Tool argument {path!r} exceeds the allowed maximum."
        if _finite_number(exclusive_minimum) and value <= exclusive_minimum:
            return f"Tool argument {path!r} must be greater than the allowed boundary."
        if _finite_number(exclusive_maximum) and value >= exclusive_maximum:
            return f"Tool argument {path!r} must be below the allowed boundary."

    return ""


def _matches_type(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return _finite_number(value)
    return False


def _json_identity(
    value: Any,
    *,
    path: str,
    depth: int = 0,
) -> tuple[Any, ...]:
    """Return a hashable JSON-semantic identity with booleans kept distinct.

    Python considers ``True == 1`` while JSON Schema does not. Numeric ``1`` and
    ``1.0`` do compare equally, matching JSON Schema's mathematical-number
    semantics. The helper also rejects values JSON cannot represent.
    """

    if depth > _MAX_SCHEMA_DEPTH:
        raise ValueError(f"{path} exceeds the JSON nesting limit")
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int):
        return ("number", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be a finite JSON number")
        return ("number", value)
    if isinstance(value, str):
        if len(value) > _MAX_IDENTITY_STRING_CHARS:
            raise ValueError(f"{path} exceeds the JSON string limit")
        return ("string", value)
    if isinstance(value, list):
        if len(value) > _MAX_IDENTITY_ITEMS:
            raise ValueError(f"{path} exceeds the JSON item limit")
        return (
            "array",
            tuple(_json_identity(item, path=f"{path}[]", depth=depth + 1) for item in value),
        )
    if isinstance(value, dict):
        if len(value) > _MAX_IDENTITY_ITEMS:
            raise ValueError(f"{path} exceeds the JSON property limit")
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{path} object keys must be strings")
        return (
            "object",
            tuple(
                (
                    key,
                    _json_identity(
                        value[key],
                        path=f"{path}.{key}",
                        depth=depth + 1,
                    ),
                )
                for key in sorted(value)
            ),
        )
    raise ValueError(f"{path} contains a non-JSON value")
