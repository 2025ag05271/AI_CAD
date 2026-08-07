from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

import ollama

from catia_agent.config import settings
from catia_agent.logging_config import get_logger
from catia_agent.mcp_tools import (
    McpError,
    get_mcp_tool_catalog,
)


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Shared helpers
# -------------------------------------------------------------------------

def _duration_ms(
    started_at: float,
) -> float:
    return round(
        (
            time.perf_counter()
            - started_at
        )
        * 1000,
        2,
    )


class ReadinessCheck(Protocol):
    """
    Contract implemented by dependency readiness checks.
    """

    @property
    def name(self) -> str:
        ...

    async def check(
        self,
    ) -> dict[str, Any]:
        ...


# -------------------------------------------------------------------------
# Ollama readiness
# -------------------------------------------------------------------------

class OllamaReadinessCheck:
    """
    Verify that Ollama is reachable and the configured model exists.

    This class can later be replaced by an Azure model readiness check
    without changing the aggregate readiness-report implementation.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
    ) -> None:
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds

        self._client = ollama.AsyncClient(
            host=base_url
        )

    @property
    def name(self) -> str:
        return "modelProvider"

    async def check(
        self,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()

        try:
            async with asyncio.timeout(
                self._timeout_seconds
            ):
                response = await self._client.list()

        except TimeoutError:
            duration_ms = _duration_ms(
                started_at
            )

            logger.warning(
                "ollama_readiness_timed_out",
                extra={
                    "timeoutSeconds": (
                        self._timeout_seconds
                    ),
                    "durationMs": duration_ms,
                },
            )

            return {
                "status": "unavailable",
                "service": "ollama",
                "message": (
                    "The local Ollama service did not respond "
                    "within the configured timeout."
                ),
                "durationMs": duration_ms,
            }

        except Exception as exc:
            duration_ms = _duration_ms(
                started_at
            )

            logger.warning(
                "ollama_readiness_failed",
                extra={
                    "durationMs": duration_ms,
                    "exceptionType": (
                        type(exc).__name__
                    ),
                },
            )

            result: dict[str, Any] = {
                "status": "unavailable",
                "service": "ollama",
                "message": (
                    "The local Ollama service could not be "
                    "reached."
                ),
                "durationMs": duration_ms,
            }

            if (
                settings
                .include_technical_details_in_errors
            ):
                result["technicalDetail"] = str(exc)

            return result

        installed_models: list[str] = []

        response_models = getattr(
            response,
            "models",
            [],
        )

        for model in response_models:
            model_name = getattr(
                model,
                "model",
                None,
            )

            if model_name is None:
                model_name = getattr(
                    model,
                    "name",
                    None,
                )

            if isinstance(model_name, str):
                installed_models.append(
                    model_name
                )

        model_available = any(
            installed_model == self._model_name
            or installed_model.startswith(
                f"{self._model_name}:"
            )
            for installed_model
            in installed_models
        )

        duration_ms = _duration_ms(
            started_at
        )

        if not model_available:
            logger.warning(
                "ollama_model_unavailable",
                extra={
                    "model": self._model_name,
                    "durationMs": duration_ms,
                },
            )

            return {
                "status": "unavailable",
                "service": "ollama",
                "model": self._model_name,
                "message": (
                    f"The required model "
                    f"'{self._model_name}' is not installed."
                ),
                "installedModels": installed_models,
                "durationMs": duration_ms,
            }

        return {
            "status": "available",
            "service": "ollama",
            "model": self._model_name,
            "installedModels": installed_models,
            "durationMs": duration_ms,
        }


# -------------------------------------------------------------------------
# MCP readiness
# -------------------------------------------------------------------------

class McpReadinessCheck:
    """
    Verify that the CATIA MCP server is reachable and exposes at least
    one valid tool.
    """

    @property
    def name(self) -> str:
        return "catiaMcp"

    async def check(
        self,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()

        try:
            tools = await get_mcp_tool_catalog(
                force_refresh=True
            )

        except McpError as exc:
            duration_ms = _duration_ms(
                started_at
            )

            logger.warning(
                "mcp_readiness_failed",
                extra={
                    "durationMs": duration_ms,
                    "exceptionType": (
                        type(exc).__name__
                    ),
                },
            )

            result: dict[str, Any] = {
                "status": "unavailable",
                "service": "catia-mcp",
                "message": (
                    "The CATIA MCP tool catalog is "
                    "unavailable."
                ),
                "durationMs": duration_ms,
            }

            if (
                settings
                .include_technical_details_in_errors
            ):
                result["technicalDetail"] = str(exc)

            return result

        except Exception as exc:
            duration_ms = _duration_ms(
                started_at
            )

            logger.exception(
                "mcp_readiness_unexpected_failure",
                extra={
                    "durationMs": duration_ms,
                    "exceptionType": (
                        type(exc).__name__
                    ),
                },
            )

            result = {
                "status": "unavailable",
                "service": "catia-mcp",
                "message": (
                    "An unexpected error occurred while "
                    "checking the CATIA MCP server."
                ),
                "durationMs": duration_ms,
            }

            if (
                settings
                .include_technical_details_in_errors
            ):
                result["technicalDetail"] = str(exc)

            return result

        duration_ms = _duration_ms(
            started_at
        )

        if not tools:
            logger.warning(
                "mcp_readiness_empty_catalog",
                extra={
                    "durationMs": duration_ms,
                },
            )

            return {
                "status": "unavailable",
                "service": "catia-mcp",
                "message": (
                    "The CATIA MCP server exposed no tools."
                ),
                "toolCount": 0,
                "toolNames": [],
                "durationMs": duration_ms,
            }

        tool_names = [
            tool["name"]
            for tool in tools
        ]

        return {
            "status": "available",
            "service": "catia-mcp",
            "toolCount": len(tools),
            "toolNames": tool_names,
            "durationMs": duration_ms,
        }
        
        
async def get_readiness_report() -> dict[str, Any]:
    """
    Run all dependency readiness checks concurrently and return
    the combined application readiness status.
    """

    ollama_readiness_check = OllamaReadinessCheck(
        base_url=settings.ollama_base_url,
        model_name=settings.ollama_model,
        timeout_seconds=(
            settings.ollama_request_timeout_seconds
        ),
    )

    mcp_readiness_check = McpReadinessCheck()
    
    started_at = time.perf_counter()

    checks: tuple[ReadinessCheck, ...] = (
        ollama_readiness_check,
        mcp_readiness_check,
    )

    results = await asyncio.gather(
        *(
            readiness_check.check()
            for readiness_check in checks
        ),
        return_exceptions=True,
    )

    dependencies: dict[
        str,
        dict[str, Any],
    ] = {}

    for readiness_check, result in zip(
        checks,
        results,
        strict=True,
    ):
        if isinstance(result, BaseException):
            logger.error(
                "readiness_check_unhandled_failure",
                extra={
                    "dependency": (
                        readiness_check.name
                    ),
                    "exceptionType": (
                        type(result).__name__
                    ),
                },
            )

            dependency_result: dict[
                str,
                Any,
            ] = {
                "status": "unavailable",
                "message": (
                    "The dependency readiness check "
                    "failed unexpectedly."
                ),
            }

            if (
                settings
                .include_technical_details_in_errors
            ):
                dependency_result[
                    "technicalDetail"
                ] = str(result)

            dependencies[
                readiness_check.name
            ] = dependency_result

        else:
            dependencies[
                readiness_check.name
            ] = result

    ready = all(
        dependency.get("status") == "available"
        for dependency in dependencies.values()
    )

    duration_ms = round(
        (
            time.perf_counter()
            - started_at
        )
        * 1000,
        2,
    )

    return {
        "status": (
            "ready"
            if ready
            else "not_ready"
        ),
        "ready": ready,
        "dependencies": dependencies,
        "durationMs": duration_ms,
    }