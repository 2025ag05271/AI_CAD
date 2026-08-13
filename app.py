from __future__ import annotations

# Must run before any module builds an httpx/ssl client (e.g. via
# catia_agent.graph -> model_providers), so it patches the OS
# certificate store into Python's ssl module in time. This is required
# behind TLS-inspecting corporate proxies whose root CA is trusted by
# Windows but not by Python's bundled certifi CA list.
import truststore

truststore.inject_into_ssl()

import asyncio
import hmac
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

import ollama
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    status,
)
from fastapi.exceptions import (
    RequestValidationError,
)
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from catia_agent.config import settings
from catia_agent.graph import (
    AgentRunNotFoundError,
    AgentRunStateConflictError,
    get_session_id_for_run,
    resume_catia_agent,
    start_catia_agent,
)
from catia_agent.logging_config import (
    bind_logging_context,
    get_logger,
    normalize_request_id,
    setup_logging,
)
from catia_agent.mcp_tools import (
    MCP_SERVER_URL,
    McpError,
    clear_mcp_tool_cache,
    get_mcp_tool_catalog,
    get_tool_cache_status,
)
from catia_agent.model_providers import (
    ModelProviderError,
    ModelProviderTimeoutError,
    model_client,
)
from catia_agent.models import (
    ActionExecutionResultRequest,
    AgentRunRequest,
    AgentRunResponse,
)
from catia_agent.readiness import (
    close_model_provider_readiness_check,
    get_readiness_report,
)


setup_logging()
logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

REQUEST_ID_HEADER = "X-Request-ID"
ADMIN_API_KEY_HEADER = "X-Admin-API-Key"


# -------------------------------------------------------------------------
# Application lifecycle
# -------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(
    application: FastAPI,
) -> AsyncIterator:
    
    logger.info(
        "application_started",
        extra={
            "application": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "apiPrefix": settings.api_prefix,
            "modelProvider": settings.model_provider,
            "model": settings.active_model_name,
            "mcpServerUrl": (
                settings.mcp_server_url
            ),
        },
    )

    yield

    model_client_aclose = getattr(
        model_client,
        "aclose",
        None,
    )

    if model_client_aclose is not None:
        try:
            await model_client_aclose()

        except Exception:
            logger.exception(
                "model_client_shutdown_failed"
            )

    try:
        await close_model_provider_readiness_check()

    except Exception:
        logger.exception(
            "readiness_check_shutdown_failed"
        )

    logger.info(
        "application_stopped",
        extra={
            "application": settings.app_name,
            "version": settings.app_version,
        },
    )


# -------------------------------------------------------------------------
# FastAPI application
# -------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    description=(
        "Local CATIA Agent backend that converts natural-language "
        "requests into validated external CATIA actions."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url=(
        "/docs"
        if settings.enable_api_docs
        else None
    ),
    redoc_url=(
        "/redoc"
        if settings.enable_api_docs
        else None
    ),
    openapi_url=(
        "/openapi.json"
        if settings.enable_api_docs
        else None
    ),
)


# -------------------------------------------------------------------------
# Routers
# -------------------------------------------------------------------------

health_router = APIRouter(
    tags=["Health"],
)

agent_router = APIRouter(
    prefix="/agent",
    tags=["Agent Runs"],
)

tools_router = APIRouter(
    prefix="/tools",
    tags=["CATIA Tools"],
)


# -------------------------------------------------------------------------
# Error response helpers
# -------------------------------------------------------------------------

def _sanitize_for_json(
    value: Any,
) -> Any:
    """
    Recursively coerce a value into something json.dumps can encode.

    Pydantic's ValidationError.errors() can embed raw exception
    instances in ctx.error for validators that raise ValueError, which
    are not JSON-serializable; render anything not already a plain
    JSON type as its string form instead of letting JSONResponse crash.
    """

    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    if isinstance(value, dict):
        return {
            str(key): _sanitize_for_json(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _sanitize_for_json(item)
            for item in value
        ]

    return str(value)


def create_error_response(
    *,
    request: Request,
    status_code: int,
    error_status: str,
    error_code: str,
    message: str,
    technical_detail: str | None = None,
    validation_errors: (
        Sequence[Any] | None # Updated from list[dict[str, Any]] to clear the Pylance warning
    ) = None,
) -> JSONResponse:
    detail: dict[str, Any] = {
        "status": error_status,
        "errorCode": error_code,
        "message": message,
    }

    if (
        technical_detail is not None
        and settings
        .include_technical_details_in_errors
    ):
        detail["technicalDetail"] = (
            technical_detail
        )

    if validation_errors is not None:
        detail["validationErrors"] = (
            _sanitize_for_json(
                list(validation_errors)
            )
        )

    response = JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
        },
    )

    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    if request_id:
        response.headers[
            REQUEST_ID_HEADER
        ] = request_id

    return response


# -------------------------------------------------------------------------
# Exception handlers
# -------------------------------------------------------------------------

@app.exception_handler(
    RequestValidationError
)
async def handle_request_validation_error(
    request: Request,
    exception: RequestValidationError,
) -> JSONResponse:
    logger.warning(
        "http_request_validation_failed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "validationErrorCount": len(
                exception.errors()
            ),
        },
    )

    return create_error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_status="invalid_request",
        error_code="REQUEST_VALIDATION_FAILED",
        message=(
            "The request payload does not match the "
            "required API contract."
        ),
        validation_errors=exception.errors(),
    )


@app.exception_handler(
    AgentRunNotFoundError
)
async def handle_run_not_found(
    request: Request,
    exception: AgentRunNotFoundError,
) -> JSONResponse:
    return create_error_response(
        request=request,
        status_code=status.HTTP_404_NOT_FOUND,
        error_status="run_not_found",
        error_code="AGENT_RUN_NOT_FOUND",
        message=str(exception).strip("'"),
    )


@app.exception_handler(
    AgentRunStateConflictError
)
async def handle_run_state_conflict(
    request: Request,
    exception: AgentRunStateConflictError,
) -> JSONResponse:
    return create_error_response(
        request=request,
        status_code=status.HTTP_409_CONFLICT,
        error_status="run_state_conflict",
        error_code="AGENT_RUN_STATE_CONFLICT",
        message=str(exception),
    )


@app.exception_handler(McpError)
async def handle_mcp_error(
    request: Request,
    exception: McpError,
) -> JSONResponse:
    logger.exception(
        "mcp_request_failed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "exceptionType": (
                type(exception).__name__
            ),
        },
    )

    return create_error_response(
        request=request,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        error_status="mcp_unavailable",
        error_code="MCP_REQUEST_FAILED",
        message=(
            "The CATIA MCP tool service is currently "
            "unavailable."
        ),
        technical_detail=str(exception),
    )


@app.exception_handler(
    ModelProviderTimeoutError
)
async def handle_model_timeout(
    request: Request,
    exception: ModelProviderTimeoutError,
) -> JSONResponse:
    logger.exception(
        "model_request_timed_out",
        extra={
            "method": request.method,
            "path": request.url.path,
        },
    )

    return create_error_response(
        request=request,
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        error_status="model_timeout",
        error_code="MODEL_REQUEST_TIMED_OUT",
        message=(
            "The configured model did not respond within "
            "the allowed time."
        ),
        technical_detail=str(exception),
    )


@app.exception_handler(ModelProviderError)
async def handle_model_provider_error(
    request: Request,
    exception: ModelProviderError,
) -> JSONResponse:
    logger.exception(
        "model_provider_failed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "exceptionType": (
                type(exception).__name__
            ),
        },
    )

    return create_error_response(
        request=request,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        error_status="model_unavailable",
        error_code="MODEL_PROVIDER_FAILED",
        message=(
            "The configured model provider is currently "
            "unavailable."
        ),
        technical_detail=str(exception),
    )


@app.exception_handler(
    ollama.ResponseError
)
async def handle_ollama_response_error(
    request: Request,
    exception: ollama.ResponseError,
) -> JSONResponse:
    logger.exception(
        "ollama_request_failed",
        extra={
            "method": request.method,
            "path": request.url.path,
        },
    )

    return create_error_response(
        request=request,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        error_status="model_unavailable",
        error_code="OLLAMA_REQUEST_FAILED",
        message=(
            "The local Ollama service could not process "
            "the model request."
        ),
        technical_detail=str(exception),
    )


@app.exception_handler(ValidationError)
async def handle_model_validation_error(
    request: Request,
    exception: ValidationError,
) -> JSONResponse:
    logger.warning(
        "internal_model_validation_failed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "validationErrorCount": len(
                exception.errors()
            ),
        },
    )

    return create_error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_status="invalid_model_output",
        error_code="MODEL_SCHEMA_VALIDATION_FAILED",
        message=(
            "A generated tool call or action result did "
            "not match the required schema."
        ),
        validation_errors=exception.errors(),
    )


# -------------------------------------------------------------------------
# Request middleware
# -------------------------------------------------------------------------

@app.middleware("http")
async def request_context_middleware(
    request: Request,
    call_next,
):
    request_id = normalize_request_id(
        request.headers.get(
            REQUEST_ID_HEADER
        )
    )

    request.state.request_id = request_id

    started_at = time.perf_counter()
    response_status = 500

    content_length_header = (
        request.headers.get(
            "content-length"
        )
    )

    if content_length_header:
        try:
            content_length = int(
                content_length_header
            )
        except ValueError:
            content_length = -1

        if content_length < 0:
            return create_error_response(
                request=request,
                status_code=(
                    status.HTTP_400_BAD_REQUEST
                ),
                error_status="invalid_request",
                error_code=(
                    "INVALID_CONTENT_LENGTH"
                ),
                message=(
                    "The Content-Length header is invalid."
                ),
            )

        if (
            content_length
            > settings.request_body_max_bytes
        ):
            return create_error_response(
                request=request,
                status_code=(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                ),
                error_status="request_too_large",
                error_code=(
                    "REQUEST_BODY_TOO_LARGE"
                ),
                message=(
                    "The request body exceeds the "
                    "configured size limit."
                ),
            )

    with bind_logging_context(
        request_id=request_id
    ):
        logger.info(
            "http_request_started",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
            },
        )

        try:
            async with asyncio.timeout(
                settings.request_timeout_seconds
            ):
                response = await call_next(
                    request
                )

            response_status = (
                response.status_code
            )

        except TimeoutError:
            response_status = (
                status.HTTP_504_GATEWAY_TIMEOUT
            )

            logger.warning(
                "http_request_timed_out",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "timeoutSeconds": (
                        settings
                        .request_timeout_seconds
                    ),
                },
            )

            response = create_error_response(
                request=request,
                status_code=response_status,
                error_status="request_timeout",
                error_code="REQUEST_TIMED_OUT",
                message=(
                    "The request did not complete within "
                    "the configured time limit."
                ),
            )

        except Exception:
            logger.exception(
                "http_request_unhandled_exception",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                },
            )

            response_status = (
                status.HTTP_500_INTERNAL_SERVER_ERROR
            )

            response = create_error_response(
                request=request,
                status_code=response_status,
                error_status="internal_error",
                error_code=(
                    "INTERNAL_SERVER_ERROR"
                ),
                message=(
                    "An unexpected internal error "
                    "occurred."
                ),
            )

        finally:
            duration_ms = round(
                (
                    time.perf_counter()
                    - started_at
                )
                * 1000,
                2,
            )

            logger.info(
                "http_request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "statusCode": response_status,
                    "durationMs": duration_ms,
                },
            )

        response.headers[
            REQUEST_ID_HEADER
        ] = request_id

        return response


# -------------------------------------------------------------------------
# Administrative authorization
# -------------------------------------------------------------------------

async def require_admin_access(
    supplied_api_key: str | None = Header(
        default=None,
        alias=ADMIN_API_KEY_HEADER,
    ),
) -> None:
    """
    Protect local administrative operations when an admin key is
    configured.

    In local development, an empty configured key leaves the endpoint
    accessible for Postman and Swagger testing.
    """

    configured_api_key = (
        settings.admin_api_key
    )

    if configured_api_key is None:
        return

    if supplied_api_key is None:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail={
                "status": "unauthorized",
                "errorCode": (
                    "ADMIN_API_KEY_REQUIRED"
                ),
                "message": (
                    "An administrative API key is required."
                ),
            },
        )

    if not hmac.compare_digest(
        supplied_api_key,
        configured_api_key,
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail={
                "status": "forbidden",
                "errorCode": (
                    "ADMIN_API_KEY_INVALID"
                ),
                "message": (
                    "The administrative API key is invalid."
                ),
            },
        )


# -------------------------------------------------------------------------
# Health endpoints
# -------------------------------------------------------------------------

@health_router.get(
    "/health",
    summary="Check API liveness",
)
async def get_health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }


@health_router.get(
    "/ready",
    summary="Check Agent dependencies",
)
async def get_readiness() -> JSONResponse:
    report = await get_readiness_report()

    response_status = (
        status.HTTP_200_OK
        if report["ready"]
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return JSONResponse(
        status_code=response_status,
        content=report,
    )


# -------------------------------------------------------------------------
# Agent endpoints
# -------------------------------------------------------------------------

@agent_router.post(
    "/runs",
    response_model=AgentRunResponse,
    summary="Start a CATIA Agent run",
    description=(
        "Interpret a natural-language message and return either an "
        "external CATIA action, a general-chat routing decision, a "
        "clarification request, or a terminal result."
    ),
)
async def create_agent_run(
    request: AgentRunRequest,
) -> AgentRunResponse:
    with bind_logging_context(
        session_id=request.sessionId
    ):
        response = await start_catia_agent(
            session_id=request.sessionId,
            user_message=request.message,
            catia_context=request.catiaContext,
        )

        logger.info(
            "agent_run_planned",
            extra={
                "runId": response.get(
                    "runId"
                ),
                "actionId": response.get(
                    "actionId"
                ),
                "status": response.get(
                    "status"
                ),
                "selectedTool": (
                    response
                    .get("action", {})
                    .get("action")
                ),
            },
        )

        return response  # type: ignore[return-value]


@agent_router.post(
    "/runs/{run_id}/action-results",
    response_model=AgentRunResponse,
    summary="Submit a CATIA action result",
    description=(
        "Resume a paused Agent run using the result returned by the "
        "external WPF C# CATIA executor."
    ),
)
async def submit_action_result(
    run_id: str,
    request: ActionExecutionResultRequest,
) -> AgentRunResponse:
    session_id = get_session_id_for_run(
        run_id
    )

    with bind_logging_context(
        session_id=session_id,
        run_id=run_id,
        action_id=request.actionId,
    ):
        logger.info(
            "action_result_received",
            extra={
                "success": request.success,
                "errorCode": request.errorCode,
            },
        )

        response = await resume_catia_agent(
            run_id=run_id,
            action_result=request,
        )

        logger.info(
            "agent_run_resumed",
            extra={
                "status": response.get(
                    "status"
                ),
                "nextActionId": response.get(
                    "actionId"
                ),
            },
        )

        return response  # type: ignore[return-value]


# -------------------------------------------------------------------------
# CATIA tool endpoints
# -------------------------------------------------------------------------

@tools_router.get(
    "",
    summary="List discovered CATIA tools",
)
async def list_catia_tools(
    refresh: bool = False,
) -> dict[str, Any]:
    tools = await get_mcp_tool_catalog(
        force_refresh=refresh
    )

    return {
        "status": "available",
        "source": "mcp",
        "mcpServer": MCP_SERVER_URL,
        "toolCount": len(tools),
        "cache": get_tool_cache_status(),
        "tools": tools,
    }


@tools_router.post(
    "/cache/clear",
    summary="Clear the MCP tool cache",
    dependencies=[
        Depends(require_admin_access)
    ],
)
async def clear_tool_cache() -> dict[str, Any]:
    clear_mcp_tool_cache()

    return {
        "status": "cleared",
        "message": (
            "The CATIA MCP tool cache was cleared."
        ),
        "cache": get_tool_cache_status(),
    }


# -------------------------------------------------------------------------
# Router registration
# -------------------------------------------------------------------------

app.include_router(
    health_router
)

app.include_router(
    agent_router,
    prefix=settings.api_prefix,
)

if settings.enable_tool_catalog_endpoint:
    app.include_router(
        tools_router,
        prefix=settings.api_prefix,
    )