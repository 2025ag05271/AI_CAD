import ollama
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from catia_agent.graph import (
    AgentRunStateConflictError,
    get_session_id_for_run,
    resume_catia_agent,
    start_catia_agent,
)
from catia_agent.models import (
    ActionExecutionResultRequest,
    AgentRunRequest,
)
from catia_agent.mcp_tools import (
    MCP_SERVER_URL,
    McpToolDiscoveryError,
    clear_mcp_tool_cache,
    get_mcp_tool_catalog,
    get_tool_cache_status,
)
from catia_agent.readiness import (
    get_readiness_report,
)
from catia_agent.planner import MODEL_NAME

import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from catia_agent.config import settings
from catia_agent.logging_config import (
    action_id_context,
    create_request_id,
    get_logger,
    request_id_context,
    run_id_context,
    session_id_context,
    setup_logging,
)

setup_logging()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "application_started",
        extra={
            "application": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "ollamaModel": settings.ollama_model,
            "ollamaBaseUrl":
                settings.ollama_base_url,
            "mcpServerUrl":
                settings.mcp_server_url,
        },
    )

    yield

    logger.info(
        "application_stopped",
        extra={
            "application": settings.app_name,
        },
    )

app = FastAPI(
    title=settings.app_name,
    description=(
        "Local Agent backend that converts natural-language "
        "CATIA commands into validated external actions."
    ),
    version=settings.app_version,
    lifespan=lifespan,
)

@app.middleware("http")
async def request_logging_middleware(
    request: Request,
    call_next,
):
    request_id = (
        request.headers.get("X-Request-ID")
        or create_request_id()
    )

    request_token = request_id_context.set(
        request_id
    )

    started_at = time.perf_counter()
    status_code = 500

    logger.info(
        "http_request_started",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query,
        },
    )

    try:
        response = await call_next(request)
        status_code = response.status_code

        response.headers["X-Request-ID"] = (
            request_id
        )

        return response

    except Exception:
        logger.exception(
            "http_request_unhandled_exception",
            extra={
                "method": request.method,
                "path": request.url.path,
            },
        )

        raise

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
                "statusCode": status_code,
                "durationMs": duration_ms,
            },
        )

        request_id_context.reset(
            request_token
        )

@app.get(
    "/ready",
    summary="Check Agent dependencies",
    description=(
        "Verify that Ollama, the configured Qwen model, and "
        "the CATIA MCP server are available. Use this endpoint "
        "before enabling Agent mode in the client application."
    ),
    tags=["Health"],
)
async def get_readiness() -> dict:
    report = await get_readiness_report()

    if not report["ready"]:
        raise HTTPException(
            status_code=503,
            detail=report,
        )

    return report

@app.get(
    "/health",
    summary="Check Agent API health",
    tags=["Health"],
)
def get_health() -> dict:
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "model": settings.ollama_model,
        "mcpToolDiscoveryEndpoint": "/api/tools",
        "readinessEndpoint": "/ready",
    }

@app.post(
    "/api/agent/runs",
    summary="Start a CATIA Agent run",
    description=(
        "Interpret a natural-language message and either return a "
        "validated CATIA action for external execution, or a "
        "non_agentic routing decision when the message does not "
        "require a CATIA action. The orchestrator decides which of "
        "the two applies; callers do not need to select a mode. On "
        "non_agentic, the caller is responsible for routing the "
        "original message to its own general-purpose chat model."
    ),
    tags=["Agent Runs"],
)
async def create_agent_run(
    request: AgentRunRequest,
) -> dict:
    session_token = session_id_context.set(
        request.sessionId
    )

    try:
        response = await start_catia_agent(
            session_id=request.sessionId,
            user_message=request.message,
            catia_context=request.catiaContext,
        )

        logger.info(
            "agent_run_planned",
            extra={
                "runId": response.get("runId"),
                "actionId":
                    response.get("actionId"),
                "status":
                    response.get("status"),
                "selectedTool": (
                    response
                    .get("action", {})
                    .get("action")
                ),
            },
        )

        return response

    except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "status": "invalid_model_output",
                    "errorCode": "TOOL_SCHEMA_VALIDATION_FAILED",
                    "message": (
                        "Qwen returned a CATIA tool call that "
                        "did not match the required schema."
                    ),
                    "validationErrors": exc.errors(),
                },
            ) from exc
        
    except McpToolDiscoveryError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "mcp_unavailable",
                    "errorCode": "MCP_TOOL_DISCOVERY_FAILED",
                    "message": str(exc),
                },
            ) from exc

    except AgentRunStateConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "status": "run_state_conflict",
                    "errorCode": "AGENT_RUN_STATE_CONFLICT",
                    "message": str(exc),
                },
            ) from exc

    except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "status": "action_rejected",
                    "errorCode": "ACTION_VALIDATION_FAILED",
                    "message": str(exc),
                },
            ) from exc
    
    except ollama.ResponseError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "model_unavailable",
                    "errorCode": "OLLAMA_REQUEST_FAILED",
                    "message": str(exc),
                },
            ) from exc
    
    except ConnectionError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "model_unavailable",
                    "errorCode": "OLLAMA_CONNECTION_FAILED",
                    "message": (
                        "The CATIA Agent could not connect "
                        "to the local Ollama service."
                    ),
                },
            ) from exc

    finally:
        session_id_context.reset(
            session_token
        )    

@app.post(
    "/api/agent/runs/{run_id}/action-results",
    summary="Submit a CATIA action result",
    description=(
        "Resume a paused Agent run using the result returned "
        "by the external C# CATIA executor."
    ),
    tags=["Agent Runs"],
)
async def submit_action_result(
    run_id: str,
    request: ActionExecutionResultRequest,
) -> dict:
    """
    Resume a paused LangGraph run using the execution result
    returned by the external CATIA executor.

    During the POC, the result can be submitted manually
    through Swagger. Later, WPF will call this endpoint after
    executing the C# CATIA COM action.
    """

    session_id = get_session_id_for_run(run_id)
    session_token = (
        session_id_context.set(session_id)
        if session_id is not None
        else None
    )

    try:
        logger.info(
            "action_result_received",
            extra={
                "runId": run_id,
                "actionId": request.actionId,
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
                "runId": run_id,
                "actionId": request.actionId,
                "status": response.get("status"),
            },
        )

        return response

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "run_not_found",
                "errorCode": "AGENT_RUN_NOT_FOUND",
                "message": str(exc),
            },
        ) from exc

    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "invalid_action_result",
                "errorCode":
                    "ACTION_RESULT_VALIDATION_FAILED",
                "message": (
                    "The submitted CATIA action result "
                    "is invalid."
                ),
                "validationErrors": exc.errors(),
            },
        ) from exc

    except AgentRunStateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "run_state_conflict",
                "errorCode": "AGENT_RUN_STATE_CONFLICT",
                "message": str(exc),
            },
        ) from exc

    except McpToolDiscoveryError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "mcp_unavailable",
                "errorCode": "MCP_TOOL_DISCOVERY_FAILED",
                "message": str(exc),
            },
        ) from exc

    except ollama.ResponseError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "model_unavailable",
                "errorCode": "OLLAMA_REQUEST_FAILED",
                "message": str(exc),
            },
        ) from exc

    except ConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "model_unavailable",
                "errorCode": "OLLAMA_CONNECTION_FAILED",
                "message": (
                    "The CATIA Agent could not connect "
                    "to the local Ollama service."
                ),
            },
        ) from exc

    except ValueError as exc:
        # Planner / policy validation raised while resuming the run.
        # This is not a state conflict; surface it as a resume failure
        # so the caller does not treat it as "still pending".
        logger.exception(
            "agent_run_resume_failed",
            extra={
                "runId": run_id,
                "actionId": request.actionId,
            },
        )

        raise HTTPException(
            status_code=422,
            detail={
                "status": "resume_failed",
                "errorCode": "AGENT_RUN_RESUME_FAILED",
                "message": str(exc),
            },
        ) from exc

    finally:
        if session_token is not None:
            session_id_context.reset(session_token)
        
        
@app.get(
    "/api/tools",
    summary="List discovered CATIA tools",
    description=(
        "Return the CATIA tools discovered through the MCP "
        "server. Results may be served from the short-lived "
        "in-memory tool cache."
    ),
    tags=["CATIA Tools"],
)
async def list_catia_tools(
    refresh: bool = False,
) -> dict:
    try:
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

    except McpToolDiscoveryError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "mcp_unavailable",
                "errorCode": "MCP_CONNECTION_FAILED",
                "message": str(exc),
            },
        ) from exc
        
@app.post(
    "/api/tools/cache/clear",
    summary="Clear the MCP tool cache",
    description=(
        "Clear the in-memory CATIA MCP tool cache. "
        "The next Agent request or tool-list request will "
        "rediscover tools from the MCP server."
    ),
    tags=["CATIA Tools"],
)
async def clear_tool_cache() -> dict:
    clear_mcp_tool_cache()

    return {
        "status": "cleared",
        "message": (
            "The CATIA MCP tool cache was cleared."
        ),
        "cache": get_tool_cache_status(),
    }