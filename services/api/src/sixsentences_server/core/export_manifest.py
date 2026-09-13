"""Portable descriptions of the exported columns and retained relationship IDs."""

from typing import Any

from sixsentences_server.core.db import Base


def export_schema(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Describe only fields actually included, with explicit unavailable references."""
    aliases = {"users": "account", "orgs": "workspace"}
    reverse = {value: key for key, value in aliases.items()}
    result: dict[str, Any] = {}
    for name, rows in tables.items():
        model_table = Base.metadata.tables.get(reverse.get(name, name))
        present = sorted({key for row in rows for key in row})
        fields = []
        for key in present:
            column = model_table.c.get(key) if model_table is not None else None
            references = []
            if column is not None:
                for foreign_key in column.foreign_keys:
                    target, target_column = foreign_key.target_fullname.rsplit(".", 1)
                    exported_target = aliases.get(target, target)
                    references.append(
                        {
                            "table": exported_target,
                            "column": target_column,
                            "included": exported_target in tables,
                        }
                    )
            fields.append(
                {
                    "name": key,
                    "type": str(column.type) if column is not None else "JSON value",
                    "primary_key": bool(column.primary_key) if column is not None else False,
                    "references": references,
                }
            )
        result[name] = {
            "file": f"data/{name}.json",
            "row_count": len(rows),
            "fields": fields,
        }
    return {
        "version": 1,
        "encoding": "UTF-8",
        "tables": result,
        "notes": [
            "IDs retain their values from this workspace; do not assume global uniqueness.",
            "Unavailable targets and null references can reflect deliberate privacy restrictions.",
            "JSON arrays and objects retain their nested structure; timestamps use ISO 8601.",
        ],
    }
