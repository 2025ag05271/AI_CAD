import asyncio
import time
from typing import Any

from fastmcp import Client


from catia_agent.config import settings
from catia_agent.logging_config import get_logger


logger = get_logger(__name__)

MCP_SERVER_URL = settings.mcp_server_url
MCP_TOOL_CACHE_TTL_SECONDS = (
    settings.mcp_tool_cache_ttl_seconds
)


class McpToolDiscoveryError(RuntimeError):
    """
    Raised when the Agent cannot discover tools from the
    configured CATIA MCP server.
    """


_cached_ollama_tools: list[dict[str, Any]] | None = None
_cache_created_at: float | None = None
_cache_lock = asyncio.Lock()


async def discover_mcp_tools() -> list:
    logger.info(
        "mcp_tool_discovery_started",
        extra={
            "mcpServer": MCP_SERVER_URL,
        },
    )

    client = Client(MCP_SERVER_URL)

    try:
        async with client:
            tools = await client.list_tools()

    except Exception as exc:
        logger.exception(
            "mcp_tool_discovery_failed",
            extra={
                "mcpServer": MCP_SERVER_URL,
            },
        )

        raise McpToolDiscoveryError(
            "Could not connect to the CATIA MCP server at "
            f"{MCP_SERVER_URL}."
        ) from exc

    if not tools:
        logger.error(
            "mcp_tool_discovery_empty",
            extra={
                "mcpServer": MCP_SERVER_URL,
            },
        )

        raise McpToolDiscoveryError(
            "The CATIA MCP server did not expose any tools."
        )

    logger.info(
        "mcp_tool_discovery_completed",
        extra={
            "mcpServer": MCP_SERVER_URL,
            "toolCount": len(tools),
            "toolNames": [
                getattr(tool, "name", "unknown")
                for tool in tools
            ],
        },
    )

    return tools


def convert_mcp_tool_to_ollama(
    mcp_tool: Any,
) -> dict[str, Any]:
    """
    Convert an MCP tool definition into the native function
    schema expected by Ollama tool calling.
    """

    tool_name = getattr(
        mcp_tool,
        "name",
        None,
    )

    tool_description = (
        getattr(
            mcp_tool,
            "description",
            None,
        )
        or ""
    )

    input_schema = getattr(
        mcp_tool,
        "inputSchema",
        None,
    )

    if input_schema is None:
        input_schema = getattr(
            mcp_tool,
            "input_schema",
            None,
        )

    if not tool_name:
        raise McpToolDiscoveryError(
            "The MCP server returned a tool without a name."
        )

    if input_schema is None:
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_description,
            "parameters": input_schema,
        },
    }


def is_tool_cache_valid() -> bool:
    if _cached_ollama_tools is None:
        return False

    if _cache_created_at is None:
        return False

    cache_age = time.monotonic() - _cache_created_at

    return cache_age < MCP_TOOL_CACHE_TTL_SECONDS


async def load_ollama_tools_from_mcp(
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    Return Ollama-compatible tool definitions.

    MCP discovery is cached for a short time to avoid opening
    a new MCP connection for every Agent request.
    """

    global _cached_ollama_tools
    global _cache_created_at

    if not force_refresh and is_tool_cache_valid():
        return list(_cached_ollama_tools or [])

    async with _cache_lock:
        # Another request may have refreshed the cache while
        # this request waited for the lock.
        if not force_refresh and is_tool_cache_valid():
            logger.debug(
                "mcp_tool_cache_hit",
                extra=get_tool_cache_status(),
            )

            return list(_cached_ollama_tools or [])

        mcp_tools = await discover_mcp_tools()

        converted_tools = [
            convert_mcp_tool_to_ollama(tool)
            for tool in mcp_tools
        ]

        _cached_ollama_tools = converted_tools
        _cache_created_at = time.monotonic()

        logger.info(
            "mcp_tool_cache_refreshed",
            extra={
                "toolCount": len(converted_tools),
                "ttlSeconds": MCP_TOOL_CACHE_TTL_SECONDS,
            },
        )

        return list(converted_tools)


def clear_mcp_tool_cache() -> None:
    """
    Clear the in-memory MCP tool cache.

    The next request will perform fresh MCP discovery.
    """

    global _cached_ollama_tools
    global _cache_created_at

    _cached_ollama_tools = None
    _cache_created_at = None
    
    logger.info("mcp_tool_cache_cleared")


def get_tool_cache_status() -> dict[str, Any]:
    if _cached_ollama_tools is None:
        return {
            "cached": False,
            "toolCount": 0,
            "ageSeconds": None,
            "ttlSeconds": MCP_TOOL_CACHE_TTL_SECONDS,
        }

    age_seconds = None

    if _cache_created_at is not None:
        age_seconds = round(
            time.monotonic() - _cache_created_at,
            2,
        )

    return {
        "cached": True,
        "toolCount": len(_cached_ollama_tools),
        "ageSeconds": age_seconds,
        "ttlSeconds": MCP_TOOL_CACHE_TTL_SECONDS,
    }


async def get_mcp_tool_catalog(
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    Return an API-friendly representation of the CATIA tools.
    """

    ollama_tools = await load_ollama_tools_from_mcp(
        force_refresh=force_refresh
    )

    catalog: list[dict[str, Any]] = []

    for tool in ollama_tools:
        function = tool["function"]

        catalog.append(
            {
                "name": function["name"],
                "description": function["description"],
                "inputSchema": function["parameters"],
                "source": "catia-mcp",
            }
        )

    return catalog