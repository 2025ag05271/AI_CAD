from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from catia_agent.config import Settings, settings
from catia_agent.logging_config import Stopwatch, get_logger, log_event


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
    "required" so the model structurally cannot return zero tool calls
    on a well-formed request.
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
        max_tokens: int,
        enable_thinking: bool,
    ) -> None:
        self._deployment_name = deployment_name
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._enable_thinking = enable_thinking

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
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            # Qwen/vLLM-specific: suppresses the large chain-of-thought
            # "reasoning" field the model otherwise emits alongside the
            # tool call (see model_providers.py module notes).
            "chat_template_kwargs": {
                "enable_thinking": self._enable_thinking,
            },
        }

        # DEBUG only (never contains the bearer token, which is a
        # header, not part of the body): full request payload for
        # troubleshooting prompt shape/content issues against the
        # actual Azure Foundry deployment. Set
        # CATIA_AGENT_LOG_LEVEL=DEBUG to see this.
        logger.debug(
            "azure_foundry_request_payload",
            extra={"payload": payload},
        )

        stopwatch = Stopwatch()

        try:
            response = await self._client.post(
                self._chat_completions_url,
                json=payload,
            )

        except httpx.TimeoutException as exc:
            log_event(
                logger,
                "azure_foundry_request_timed_out",
                level=logging.WARNING,
                durationMs=stopwatch.elapsed_ms,
                timeoutSeconds=self._timeout_seconds,
            )
            raise ModelProviderTimeoutError(
                "The Azure AI Foundry model did not respond "
                f"within {self._timeout_seconds} seconds."
            ) from exc

        except httpx.HTTPError as exc:
            log_event(
                logger,
                "azure_foundry_connection_failed",
                level=logging.WARNING,
                durationMs=stopwatch.elapsed_ms,
                exceptionType=type(exc).__name__,
            )
            raise AzureFoundryRequestError(
                "Could not reach the Azure AI Foundry model "
                "endpoint."
            ) from exc

        duration_ms = stopwatch.elapsed_ms

        if response.status_code >= 400:
            logger.error(
                "azure_foundry_request_failed",
                extra={
                    "statusCode": response.status_code,
                    "durationMs": duration_ms,
                    "responseBody": response.text[:2000],
                },
            )

            raise AzureFoundryRequestError(
                "The Azure AI Foundry model endpoint returned "
                f"HTTP {response.status_code}: "
                f"{response.text[:2000]}"
            )

        try:
            body = response.json()

        except ValueError as exc:
            raise ModelResponseFormatError(
                "The Azure AI Foundry model returned a "
                "non-JSON response."
            ) from exc

        # Azure Foundry's vLLM backend can transparently reuse
        # KV-cache state for the portion of the prompt (tools/system
        # prompt) that matches a previous request byte-for-byte; there
        # is no client-side opt-in for this, so prompt construction
        # keeps that prefix stable (see planner.py) and this logs the
        # OpenAI-shaped usage.prompt_tokens_details.cached_tokens
        # field, when the backend returns it, as the only direct
        # evidence of whether that reuse is actually happening.
        usage = body.get("usage") or {}
        prompt_tokens_details = (
            usage.get("prompt_tokens_details") or {}
        )

        logger.info(
            "azure_foundry_usage",
            extra={
                "durationMs": duration_ms,
                "promptTokens": usage.get(
                    "prompt_tokens"
                ),
                "completionTokens": usage.get(
                    "completion_tokens"
                ),
                "totalTokens": usage.get(
                    "total_tokens"
                ),
                "cachedTokens": (
                    prompt_tokens_details.get(
                        "cached_tokens"
                    )
                ),
            },
        )

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
    Construct the Azure AI Foundry ToolCallingModelClient.

    Callers outside this module should depend on the returned
    protocol, not on the concrete provider type.
    """

    if (
        application_settings.azure_foundry_endpoint is None
        or application_settings.azure_foundry_api_key is None
        or application_settings.azure_foundry_deployment_name
        is None
    ):
        raise ValueError(
            "The Azure AI Foundry settings are incomplete."
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
        max_tokens=application_settings.azure_foundry_max_tokens,
        enable_thinking=(
            application_settings.azure_foundry_enable_thinking
        ),
    )


# Process-lifetime singleton so the httpx connection pool is reused
# across planning turns; app.py's lifespan closes it via aclose().
model_client: ToolCallingModelClient = build_model_client(settings)

MODEL_NAME = settings.active_model_name
