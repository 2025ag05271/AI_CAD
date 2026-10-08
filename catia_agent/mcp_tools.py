from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from fastmcp import Client

from catia_agent.config import settings
from catia_agent.logging_config import Stopwatch, get_logger


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Exceptions
# -------------------------------------------------------------------------

class McpError(RuntimeError):
    """
    Base exception for MCP-related failures.
    """


class McpConnectionError(McpError):
    """
    Raised when the configured MCP server cannot be reached.
    """


class McpToolDiscoveryError(McpError):
    """
    Raised when MCP tool discovery fails or returns an invalid catalog.
    """


class McpToolSchemaError(McpError):
    """
    Raised when an MCP tool definition cannot be converted into the
    schema required by the model provider.
    """


# -------------------------------------------------------------------------
# Tool catalog models
# -------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolCatalogSnapshot:
    """
    Immutable metadata describing the current cached tool catalog.
    """

    tools: tuple[dict[str, Any], ...]
    created_at: float

    def age_seconds(self) -> float:
        return max(
            0.0,
            time.monotonic() - self.created_at,
        )

    def is_valid(
        self,
        ttl_seconds: float,
    ) -> bool:
        return self.age_seconds() < ttl_seconds


# -------------------------------------------------------------------------
# MCP tool schema conversion
# -------------------------------------------------------------------------

def _get_tool_attribute(
    tool: Any,
    *attribute_names: str,
) -> Any:
    """
    Return the first available attribute from an MCP tool object.

    FastMCP versions may expose schema properties using either camelCase
    or snake_case names.
    """

    for attribute_name in attribute_names:
        value = getattr(
            tool,
            attribute_name,
            None,
        )

        if value is not None:
            return value

    if isinstance(tool, dict):
        for attribute_name in attribute_names:
            if attribute_name in tool:
                return tool[attribute_name]

    return None


def _normalize_input_schema(
    input_schema: Any,
    *,
    tool_name: str,
) -> dict[str, Any]:
    """
    Validate and normalize an MCP input schema.
    """

    if input_schema is None:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    if not isinstance(input_schema, dict):
        raise McpToolSchemaError(
            f"MCP tool '{tool_name}' returned an input schema "
            "that is not a JSON object."
        )

    normalized_schema = deepcopy(input_schema)

    schema_type = normalized_schema.get("type")

    if schema_type is None:
        normalized_schema["type"] = "object"
    elif schema_type != "object":
        raise McpToolSchemaError(
            f"MCP tool '{tool_name}' must use an object input schema."
        )

    properties = normalized_schema.get("properties")

    if properties is None:
        normalized_schema["properties"] = {}
    elif not isinstance(properties, dict):
        raise McpToolSchemaError(
            f"MCP tool '{tool_name}' has invalid schema properties."
        )

    required = normalized_schema.get("required")

    if required is None:
        normalized_schema["required"] = []
    elif not isinstance(required, list):
        raise McpToolSchemaError(
            f"MCP tool '{tool_name}' has an invalid required list."
        )

    normalized_schema.setdefault(
        "additionalProperties",
        False,
    )

    return normalized_schema


def convert_mcp_tool_to_ollama(
    mcp_tool: Any,
) -> dict[str, Any]:
    """
    Convert an MCP tool definition into an Ollama-compatible function
    schema.

    This conversion is isolated so a future model-provider adapter can
    use a different schema representation without changing MCP discovery.
    """

    tool_name = _get_tool_attribute(
        mcp_tool,
        "name",
    )

    if not isinstance(tool_name, str):
        raise McpToolSchemaError(
            "The MCP server returned a tool without a valid name."
        )

    tool_name = tool_name.strip()

    if not tool_name:
        raise McpToolSchemaError(
            "The MCP server returned a tool with an empty name."
        )

    tool_description = _get_tool_attribute(
        mcp_tool,
        "description",
    )

    if tool_description is None:
        tool_description = ""

    if not isinstance(tool_description, str):
        tool_description = str(tool_description)

    input_schema = _get_tool_attribute(
        mcp_tool,
        "inputSchema",
        "input_schema",
    )

    normalized_schema = _normalize_input_schema(
        input_schema,
        tool_name=tool_name,
    )

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_description.strip(),
            "parameters": normalized_schema,
        },
    }


# -------------------------------------------------------------------------
# Tool catalog service
# -------------------------------------------------------------------------

class McpToolCatalogService:
    """
    Discover, validate, convert, and cache tools from an MCP server.

    The service owns MCP connectivity and caching. It does not make
    planning decisions and does not authorize execution.
    """

    def __init__(
        self,
        *,
        server_url: str,
        cache_ttl_seconds: float,
        request_timeout_seconds: float,
    ) -> None:
        self._server_url = server_url
        self._cache_ttl_seconds = cache_ttl_seconds
        self._request_timeout_seconds = (
            request_timeout_seconds
        )

        self._snapshot: ToolCatalogSnapshot | None = None
        self._cache_lock = asyncio.Lock()

    @property
    def server_url(self) -> str:
        return self._server_url

    async def discover_tools(
        self,
    ) -> list[Any]:
        
        """
        Retrieve raw tool definitions from the MCP server.
        """

        logger.info(
            "mcp_tool_discovery_started",
            extra={
                "mcpServer": self._server_url,
            },
        )

        client = Client(self._server_url)

        stopwatch = Stopwatch()

        try:
            async with asyncio.timeout(
                self._request_timeout_seconds
            ):
                async with client:
                    tools = await client.list_tools()

        except TimeoutError as exc:
            logger.exception(
                "mcp_tool_discovery_timed_out",
                extra={
                    "mcpServer": self._server_url,
                    "timeoutSeconds": (
                        self._request_timeout_seconds
                    ),
                },
            )

            raise McpConnectionError(
                "The CATIA MCP server did not respond within "
                f"{self._request_timeout_seconds} seconds."
            ) from exc

        except Exception as exc:
            logger.exception(
                "mcp_tool_discovery_failed",
                extra={
                    "mcpServer": self._server_url,
                },
            )

            raise McpConnectionError(
                "Could not connect to the CATIA MCP server at "
                f"{self._server_url}."
            ) from exc

        duration_ms = stopwatch.elapsed_ms

        if not tools:
            logger.error(
                "mcp_tool_discovery_empty",
                extra={
                    "mcpServer": self._server_url,
                    "durationMs": duration_ms,
                },
            )

            raise McpToolDiscoveryError(
                "The CATIA MCP server did not expose any tools."
            )

        logger.info(
            "mcp_tool_discovery_completed",
            extra={
                "mcpServer": self._server_url,
                "toolCount": len(tools),
                "toolNames": [
                    _get_tool_attribute(
                        tool,
                        "name",
                    )
                    or "unknown"
                    for tool in tools
                ],
                "durationMs": duration_ms,
            },
        )

        return list(tools)

    async def _refresh_catalog(
        self,
    ) -> ToolCatalogSnapshot:
        raw_tools = await self.discover_tools()

        converted_tools: list[
            dict[str, Any]
        ] = []

        discovered_names: set[str] = set()

        for raw_tool in raw_tools:
            converted_tool = (
                convert_mcp_tool_to_ollama(
                    raw_tool
                )
            )

            tool_name = converted_tool[
                "function"
            ]["name"]

            if tool_name in discovered_names:
                raise McpToolDiscoveryError(
                    "The MCP server exposed duplicate tool name "
                    f"'{tool_name}'."
                )

            discovered_names.add(tool_name)
            converted_tools.append(converted_tool)

        snapshot = ToolCatalogSnapshot(
            tools=tuple(converted_tools),
            created_at=time.monotonic(),
        )

        self._snapshot = snapshot

        logger.info(
            "mcp_tool_cache_refreshed",
            extra={
                "mcpServer": self._server_url,
                "toolCount": len(converted_tools),
                "ttlSeconds": (
                    self._cache_ttl_seconds
                ),
            },
        )

        return snapshot

    async def get_model_tools(
        self,
        *,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Return model-compatible tool definitions.

        A deep copy is returned so callers cannot mutate the cached
        catalog.
        """

        snapshot = self._snapshot

        if (
            not force_refresh
            and snapshot is not None
            and snapshot.is_valid(
                self._cache_ttl_seconds
            )
        ):
            logger.debug(
                "mcp_tool_cache_hit",
                extra=self.get_cache_status(),
            )

            return deepcopy(
                list(snapshot.tools)
            )

        async with self._cache_lock:
            snapshot = self._snapshot

            if (
                not force_refresh
                and snapshot is not None
                and snapshot.is_valid(
                    self._cache_ttl_seconds
                )
            ):
                logger.debug(
                    "mcp_tool_cache_hit_after_lock",
                    extra=self.get_cache_status(),
                )

                return deepcopy(
                    list(snapshot.tools)
                )

            snapshot = await self._refresh_catalog()

            return deepcopy(
                list(snapshot.tools)
            )

    def clear_cache(self) -> None:
        """
        Clear the process-local MCP tool catalog cache.
        """

        self._snapshot = None

        logger.info(
            "mcp_tool_cache_cleared",
            extra={
                "mcpServer": self._server_url,
            },
        )

    def get_cache_status(
        self,
    ) -> dict[str, Any]:
        """
        Return non-sensitive cache diagnostics.
        """

        snapshot = self._snapshot

        if snapshot is None:
            return {
                "cached": False,
                "toolCount": 0,
                "ageSeconds": None,
                "ttlSeconds": (
                    self._cache_ttl_seconds
                ),
                "valid": False,
            }

        return {
            "cached": True,
            "toolCount": len(snapshot.tools),
            "ageSeconds": round(
                snapshot.age_seconds(),
                2,
            ),
            "ttlSeconds": (
                self._cache_ttl_seconds
            ),
            "valid": snapshot.is_valid(
                self._cache_ttl_seconds
            ),
        }

    async def get_api_catalog(
        self,
        *,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Return an API-friendly representation of discovered tools.
        """

        model_tools = await self.get_model_tools(
            force_refresh=force_refresh,
        )

        catalog: list[dict[str, Any]] = []

        for tool in model_tools:
            function = tool["function"]

            catalog.append(
                {
                    "name": function["name"],
                    "description": (
                        function["description"]
                    ),
                    "inputSchema": (
                        function["parameters"]
                    ),
                    "source": "catia-mcp",
                }
            )

        return catalog


# -------------------------------------------------------------------------
# Default application service
# -------------------------------------------------------------------------

MCP_SERVER_URL = settings.mcp_server_url
MCP_TOOL_CACHE_TTL_SECONDS = (
    settings.mcp_tool_cache_ttl_seconds
)

tool_catalog_service = McpToolCatalogService(
    server_url=settings.mcp_server_url,
    cache_ttl_seconds=(
        settings.mcp_tool_cache_ttl_seconds
    ),
    request_timeout_seconds=(
        settings.mcp_request_timeout_seconds
    ),
)


# -------------------------------------------------------------------------
# Backward-compatible public functions
# -------------------------------------------------------------------------

async def discover_mcp_tools() -> list: return await tool_catalog_service.discover_tools()


async def load_ollama_tools_from_mcp(
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    Compatibility entry point for the current Ollama planner.

    Later, the planner will depend on a model-neutral catalog method
    instead of an Ollama-named function.
    """

    return await tool_catalog_service.get_model_tools(
        force_refresh=force_refresh,
    )


def is_tool_cache_valid() -> bool:
    return bool(
        tool_catalog_service
        .get_cache_status()
        .get("valid")
    )


def clear_mcp_tool_cache() -> None:
    tool_catalog_service.clear_cache()


def get_tool_cache_status() -> dict[str, Any]:
    return tool_catalog_service.get_cache_status()


async def get_mcp_tool_catalog(
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    return await tool_catalog_service.get_api_catalog(
        force_refresh=force_refresh,
    )