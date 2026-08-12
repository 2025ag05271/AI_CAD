from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx
import ollama

from catia_agent.config import Settings, settings
from catia_agent.logging_config import get_logger


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Exceptions
#
# These are provider-neutral so app.py and the planner can catch them
# without knowing which concrete client raised them.
# -------------------------------------------------------------------------

class ModelProviderError(RuntimeError):
    """
    Base exception for model-provider failures.
    """


class ModelProviderTimeoutError(ModelProviderError):
    """
    Raised when the configured model does not respond in time.
    """


class ModelResponseFormatError(ModelProviderError):
    """
    Raised when the model provider returns an unreadable response.
    """


# -------------------------------------------------------------------------
# Model-provider abstraction
# -------------------------------------------------------------------------

class ToolCallingModelClient(Protocol):
    """
    Provider-neutral interface used by the CATIA planner.

    Every implementation normalizes its provider-specific response into
    the same shape so planner.py never branches on provider: an object
    or dict exposing `message.tool_calls` (equivalently
    `["message"]["tool_calls"]`), where each tool call exposes
    `function.name` and `function.arguments`.

    Implementations own a single long-lived HTTP connection (or
    connection pool) constructed once in __init__, not per call to
    select_tool; `model_client` below is a process-lifetime singleton
    for this reason. Implementations that hold a network client should
    also expose an `async def aclose(self) -> None` so
    app.py's lifespan can release sockets/connections on shutdown; this
    is optional (checked with getattr) rather than part of the formal
    protocol so providers with nothing to close are not forced to add
    a no-op method.
    """

    async def select_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        ...


# -------------------------------------------------------------------------
# Ollama implementation (local development)
# -------------------------------------------------------------------------

class OllamaToolCallingModelClient:
    """
    Local Ollama implementation, used for offline development.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
        temperature: float,
    ) -> None:
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature

        self._client = ollama.AsyncClient(
            host=base_url
        )

    async def select_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        try:
            async with asyncio.timeout(
                self._timeout_seconds
            ):
                return await self._client.chat(
                    model=self._model_name,
                    messages=messages,
                    tools=tools,
                    think=False,
                    options={
                        "temperature": (
                            self._temperature
                        ),
                    },
                )

        except TimeoutError as exc:
            raise ModelProviderTimeoutError(
                "The model did not respond within "
                f"{self._timeout_seconds} seconds."
            ) from exc

    async def aclose(self) -> None:
        await self._client.close()


# -------------------------------------------------------------------------
# Azure AI Foundry implementation (cloud-hosted Qwen 3.6 27B)
# -------------------------------------------------------------------------

class AzureFoundryRequestError(ModelProviderError):
    """
    Raised when the Azure AI Foundry endpoint rejects or fails a
    request. Caught by the same generic ModelProviderError handler as
    every other provider, so no new API error code is introduced.
    """


class AzureFoundryToolCallingModelClient:
    """
    Azure AI Foundry model-inference implementation.

    Targets the OpenAI-compatible chat-completions endpoint exposed by
    an Azure AI Foundry serverless (Models-as-a-Service) deployment,
    authenticated with a bearer token. tool_choice is forced to
    "required" so, unlike Ollama, the model structurally cannot return
    zero tool calls on a well-formed request.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment_name: str,
        api_version: str,
        timeout_seconds: float,
        temperature: float,
    ) -> None:
        self._deployment_name = deployment_name
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

        self._chat_completions_url = (
            f"{endpoint.rstrip('/')}/chat/completions"
        )

        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            params={"api-version": api_version},
        )

    async def select_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        payload = {
            "model": self._deployment_name,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "temperature": self._temperature,
        }

        try:
            response = await self._client.post(
                self._chat_completions_url,
                json=payload,
            )

        except httpx.TimeoutException as exc:
            raise ModelProviderTimeoutError(
                "The Azure AI Foundry model did not respond "
                f"within {self._timeout_seconds} seconds."
            ) from exc

        except httpx.HTTPError as exc:
            raise AzureFoundryRequestError(
                "Could not reach the Azure AI Foundry model "
                "endpoint."
            ) from exc

        if response.status_code >= 400:
            logger.error(
                "azure_foundry_request_failed",
                extra={
                    "statusCode": response.status_code,
                },
            )

            raise AzureFoundryRequestError(
                "The Azure AI Foundry model endpoint returned "
                f"HTTP {response.status_code}."
            )

        try:
            body = response.json()

        except ValueError as exc:
            raise ModelResponseFormatError(
                "The Azure AI Foundry model returned a "
                "non-JSON response."
            ) from exc

        choices = body.get("choices")

        if not choices:
            raise ModelResponseFormatError(
                "The Azure AI Foundry model response contained "
                "no choices."
            )

        message = choices[0].get("message", {})

        return {"message": message}

    async def aclose(self) -> None:
        await self._client.aclose()


# -------------------------------------------------------------------------
# Factory
# -------------------------------------------------------------------------

def build_model_client(
    application_settings: Settings,
) -> ToolCallingModelClient:
    """
    Construct the ToolCallingModelClient selected by
    application_settings.model_provider.

    Callers outside this module should depend on the returned
    protocol, not on the concrete provider type.
    """

    if application_settings.model_provider == "azure_foundry":
        if (
            application_settings.azure_foundry_endpoint is None
            or application_settings.azure_foundry_api_key is None
            or application_settings.azure_foundry_deployment_name
            is None
        ):
            raise ValueError(
                "model_provider is 'azure_foundry' but the Azure "
                "AI Foundry settings are incomplete."
            )

        return AzureFoundryToolCallingModelClient(
            endpoint=application_settings.azure_foundry_endpoint,
            api_key=(
                application_settings
                .azure_foundry_api_key
                .get_secret_value()
            ),
            deployment_name=(
                application_settings
                .azure_foundry_deployment_name
            ),
            api_version=(
                application_settings.azure_foundry_api_version
            ),
            timeout_seconds=(
                application_settings
                .azure_foundry_request_timeout_seconds
            ),
            temperature=application_settings.model_temperature,
        )

    return OllamaToolCallingModelClient(
        base_url=application_settings.ollama_base_url,
        model_name=application_settings.ollama_model,
        timeout_seconds=(
            application_settings.ollama_request_timeout_seconds
        ),
        temperature=application_settings.model_temperature,
    )


# Process-lifetime singleton: constructed once so the underlying HTTP
# connection (httpx.AsyncClient for Azure, ollama.AsyncClient for
# Ollama) is reused across every planning turn instead of being
# recreated per request. app.py's lifespan shutdown hook closes it via
# aclose() when present.
model_client: ToolCallingModelClient = build_model_client(settings)

MODEL_NAME = settings.active_model_name
