
import asyncio
import logging
import os
import re
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.dataset_tools import (
    query_dataset,
    preview_dataset_mutation,
    confirm_dataset_mutation,
    cancel_dataset_mutation,
)

logger = logging.getLogger(__name__)

EXCHANGE_API_URL = os.getenv(
    "EXCHANGE_API_URL",
    "http://localhost:8001",
).rstrip("/")

WEATHER_MCP_URL = os.getenv(
    "WEATHER_MCP_URL",
    os.getenv("WEATHER_API_URL", "http://localhost:8002/mcp"),
).rstrip("/")


EXCHANGE_TOOL = {
    "type": "function",
    "name": "convert_currency",
    "description": (
        "Convert an amount between currencies using the exchange-rate "
        "service. Use three-letter currency codes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "base_currency": {"type": "string"},
            "quote_currency": {"type": "string"},
            "amount": {"type": "number"},
        },
        "required": [
            "base_currency",
            "quote_currency",
            "amount",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}


DATASET_QUERY_TOOL = {
    "type": "function",
    "name": "query_dataset",
    "description": (
        "Query the active uploaded CSV using a PostgreSQL SELECT query. "
        "Use the exact active dataset table name from the instructions. "
        "Never use the dataset UUID as the table name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A read-only PostgreSQL SELECT query against "
                    "the active dataset table."
                ),
            },
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
    "strict": True,
}


DATASET_PREVIEW_TOOL = {
    "type": "function",
    "name": "preview_dataset_mutation",
    "description": (
        "Preview an UPDATE or DELETE against the active CSV dataset. "
        "No changes are made during preview. Never claim that a change "
        "was applied; the user must confirm using the application UI."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["update", "delete"],
            },
            "conditions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": [
                                "=",
                                "!=",
                                ">",
                                ">=",
                                "<",
                                "<=",
                                "in",
                                "not_in",
                                "is_null",
                                "is_not_null",
                            ],
                        },
                        "value": {},
                    },
                    "required": [
                        "column",
                        "operator",
                        "value",
                    ],
                    "additionalProperties": False,
                },
            },
            "values": {
                "type": ["object", "null"],
                "description": (
                    "Column-value pairs for UPDATE; null for DELETE."
                ),
                "additionalProperties": True,
            },
        },
        "required": [
            "operation",
            "conditions",
            "values",
        ],
        "additionalProperties": False,
    },
    "strict": False,
}


DATASET_CONFIRM_TOOL = {
    "type": "function",
    "name": "confirm_dataset_mutation",
    "description": (
        "Confirm a previously previewed dataset mutation. "
        "This tool is not exposed to automatic model execution; "
        "confirmation must come through the application endpoint."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pending_id": {"type": "string"},
        },
        "required": ["pending_id"],
        "additionalProperties": False,
    },
    "strict": True,
}


DATASET_CANCEL_TOOL = {
    "type": "function",
    "name": "cancel_dataset_mutation",
    "description": (
        "Cancel a previously previewed dataset mutation without "
        "changing dataset records."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pending_id": {"type": "string"},
        },
        "required": ["pending_id"],
        "additionalProperties": False,
    },
    "strict": True,
}


def safe_tool_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return f"weather_{cleaned}"[:64]


async def discover_weather_tool() -> list[dict[str, Any]]:
    discovered = []

    async with streamable_http_client(WEATHER_MCP_URL) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(
            read_stream,
            write_stream,
        ) as client:
            await client.initialize()
            result = await client.list_tools()

            for tool in result.tools:
                original_name = tool.name
                schema = getattr(tool, "inputSchema", None)

                if schema is None:
                    schema = getattr(tool, "input_schema", None)

                if not isinstance(schema, dict):
                    schema = {
                        "type": "object",
                        "properties": {},
                    }

                discovered.append({
                    "definition": {
                        "type": "function",
                        "name": safe_tool_name(original_name),
                        "description": (
                            tool.description or original_name
                        ),
                        "parameters": schema,
                        "strict": False,
                    },
                    "original_name": original_name,
                })

    return discovered


def get_available_tools():
    tools = [
        EXCHANGE_TOOL,
        DATASET_QUERY_TOOL,
        DATASET_PREVIEW_TOOL,
        DATASET_CONFIRM_TOOL,
        DATASET_CANCEL_TOOL,
    ]

    registry = {
        "convert_currency": {"kind": "rest"},
        "query_dataset": {"kind": "dataset_query"},
        "preview_dataset_mutation": {"kind": "dataset_preview"},
        "confirm_dataset_mutation": {"kind": "dataset_confirm"},
        "cancel_dataset_mutation": {"kind": "dataset_cancel"},
    }

    try:
        discovered = asyncio.run(discover_weather_tool())
        registered_count = 0

        for item in discovered:
            definition = item["definition"]
            name = definition["name"]

            if name in registry:
                logger.warning("Skipping duplicate tool name: %s", name)
                continue

            tools.append(definition)
            registry[name] = {
                "kind": "mcp",
                "original_name": item["original_name"],
            }
            registered_count += 1

        logger.info(
            "Registered %d tools; %d weather MCP tools discovered.",
            len(tools),
            registered_count,
        )

    except Exception:
        logger.exception(
            "Weather MCP discovery failed. Continuing without weather tools."
        )

    return tools, registry


async def invoke_weather_tool(
    original_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    async with streamable_http_client(WEATHER_MCP_URL) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(
            read_stream,
            write_stream,
        ) as client:
            await client.initialize()

            result = await client.call_tool(
                original_name,
                arguments,
            )

            structured = getattr(result, "structuredContent", None)

            if structured is None:
                structured = getattr(result, "structured_content", None)

            if structured is not None:
                output = structured
            else:
                output = [
                    getattr(block, "text", str(block))
                    for block in result.content
                ]

            is_error = getattr(result, "isError", None)

            if is_error is None:
                is_error = getattr(result, "is_error", False)

            return {
                "is_error": bool(is_error),
                "result": output,
            }


def execute_tools(
    name: str,
    arguments: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    active_dataset_id: str | None = None,
    active_dataset_table: str | None = None,
    allow_confirm: bool = False,
    db=None,
) -> Any:
    if name not in registry:
        raise ValueError(f"Unregistered tool: {name}")

    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")

    kind = registry[name]["kind"]

    if kind == "rest":
        response = httpx.post(
            f"{EXCHANGE_API_URL}/convert",
            json=arguments,
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json()

    if kind == "mcp":
        return asyncio.run(
            invoke_weather_tool(
                registry[name]["original_name"],
                arguments,
            )
        )

    # Confirmation and cancellation must be handled before the
    # general dataset-operation branch.
    if kind == "dataset_confirm":
        if not allow_confirm:
            raise ValueError(
                "Confirmation must come from an explicit application action."
            )

        if not active_dataset_id or db is None:
            raise ValueError(
                "An active dataset and database session are required."
            )

        return confirm_dataset_mutation(
            pending_id=arguments["pending_id"],
            dataset_id=active_dataset_id,
            db=db,
        )

    if kind == "dataset_cancel":
        if not active_dataset_id:
            raise ValueError("No active dataset is selected.")

        return cancel_dataset_mutation(
            pending_id=arguments["pending_id"],
            dataset_id=active_dataset_id,
        )

    if kind in {"dataset_query", "dataset_preview"}:
        if not active_dataset_id or not active_dataset_table:
            raise ValueError("Upload or select a CSV dataset first.")

        if db is None:
            raise ValueError("A database session is required.")

        if name == "query_dataset":
            return query_dataset(
                dataset_id=active_dataset_id,
                sql=arguments["sql"],
                db=db,
            )

        if name == "preview_dataset_mutation":
            return preview_dataset_mutation(
                dataset_id=active_dataset_id,
                operation=arguments["operation"],
                conditions=arguments["conditions"],
                values=arguments.get("values"),
                db=db,
            )

    raise ValueError(f"Unsupported tool type: {kind}")
