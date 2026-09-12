"""Model Context Protocol glue: JSON-RPC 2.0 over Streamable HTTP.

The protocol frame lives here, pure and testable; tool execution is injected
by the API app, which owns tenancy, the corpus and the audit helpers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import sixsentences_server

PROTOCOL_VERSION = "2025-03-26"

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_runs",
        "description": (
            "List the workspace's completed systematic literature searches "
            "with their PRISMA 2020 flow counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "search_literature",
        "description": (
            "Search the workspace's pinned research-literature corpus with a "
            "boolean query. Returns ranked works whose scores decompose into "
            "explainable signals, never a black-box number."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        'Boolean query, e.g. (transformer OR "large language model") AND screening'
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "default": 10,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_run_record",
        "description": (
            "Get one completed search's auditable record: the frozen protocol, "
            "PRISMA counts, the citable methods paragraph and every work with "
            "its screening verdict and written reason."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The run's public id."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_bibliography",
        "description": "Get the included works of a completed search as BibTeX or RIS.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The run's public id."},
                "format": {
                    "type": "string",
                    "enum": ["bibtex", "ris"],
                    "default": "bibtex",
                },
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
]


class McpToolError(Exception):
    """A tool-level failure the caller sees as isError content."""


def _error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def handle_rpc(
    payload: Any, call_tool: Callable[[str, dict[str, Any]], Any]
) -> tuple[int, dict[str, Any] | None]:
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return 400, _error(None, -32600, "invalid JSON-RPC 2.0 request")
    rpc_id = payload.get("id")
    method = str(payload.get("method") or "")
    params = payload.get("params") or {}

    if method == "initialize":
        return 200, {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "sixsentences",
                    "version": sixsentences_server.__version__,
                },
                "instructions": (
                    "Verified-literature tools for one SixSentences_ workspace. "
                    "Every run record reconstructs from an append-only audit log; "
                    "search results carry explainable score signals."
                ),
            },
        }
    if method == "ping":
        return 200, {"jsonrpc": "2.0", "id": rpc_id, "result": {}}
    if method.startswith("notifications/"):
        return 202, None
    if rpc_id is None:
        return 400, _error(None, -32600, "requests need an id")
    if method == "tools/list":
        return 200, {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": TOOL_SCHEMAS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return 200, _error(rpc_id, -32602, "arguments must be an object")
        if name not in {tool["name"] for tool in TOOL_SCHEMAS}:
            return 200, _error(rpc_id, -32602, f"unknown tool: {name}")
        try:
            result = call_tool(name, arguments)
        except McpToolError as exc:
            return 200, {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        text = (
            result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        )
        return 200, {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    return 200, _error(rpc_id, -32601, f"method not found: {method}")
