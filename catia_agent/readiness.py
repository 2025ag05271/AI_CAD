from typing import Any

import ollama

from catia_agent.mcp_tools import (
    McpToolDiscoveryError,
    get_mcp_tool_catalog,
)
from catia_agent.planner import MODEL_NAME
from catia_agent.config import settings
from catia_agent.logging_config import get_logger

logger = get_logger(__name__)

ollama_client = ollama.AsyncClient(
    host=settings.ollama_base_url
)

async def check_ollama_readiness() -> dict[str, Any]:
    """
    Verify that Ollama is reachable and that the configured
    Qwen model is installed.
    """

    try:
        response = await ollama_client.list()
    except Exception as exc:
        return {
            "status": "unavailable",
            "service": "ollama",
            "message": (
                "The local Ollama service could not be reached."
            ),
            "technicalDetail": str(exc),
        }

    installed_models: list[str] = []

    for model in response.models:
        model_name = getattr(
            model,
            "model",
            None,
        )

        if model_name:
            installed_models.append(model_name)

    model_available = any(
        installed_model == MODEL_NAME
        or installed_model.startswith(
            f"{MODEL_NAME}:"
        )
        for installed_model in installed_models
    )

    if not model_available:
        return {
            "status": "unavailable",
            "service": "ollama",
            "message": (
                f"The required model {MODEL_NAME} "
                "is not installed."
            ),
            "installedModels": installed_models,
        }

    return {
        "status": "available",
        "service": "ollama",
        "model": MODEL_NAME,
        "installedModels": installed_models,
    }


async def check_mcp_readiness() -> dict[str, Any]:
    """
    Verify that the CATIA MCP server is reachable and exposes
    at least one tool.
    """

    try:
        tools = await get_mcp_tool_catalog(
            force_refresh=True
        )
    except McpToolDiscoveryError as exc:
        return {
            "status": "unavailable",
            "service": "catia-mcp",
            "message": str(exc),
        }

    return {
        "status": "available",
        "service": "catia-mcp",
        "toolCount": len(tools),
        "toolNames": [
            tool["name"]
            for tool in tools
        ],
    }


async def get_readiness_report() -> dict[str, Any]:
    """
    Return the overall Agent-mode readiness report.
    """

    ollama_status = await check_ollama_readiness()
    mcp_status = await check_mcp_readiness()

    dependencies = {
        "ollama": ollama_status,
        "catiaMcp": mcp_status,
    }

    ready = all(
        dependency["status"] == "available"
        for dependency in dependencies.values()
    )

    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "dependencies": dependencies,
    }