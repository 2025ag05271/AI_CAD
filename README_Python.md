# CATIA Agent Backend

## Python, FastAPI, LangGraph, MCP, and Model-Provider Architecture Guide

## 1. Purpose

This document describes the Python side of the CATIA Agent. The backend
receives a natural-language CATIA command, discovers approved tools from an
MCP server, asks a tool-calling model to select a tool, validates the
proposed action through several deterministic layers, pauses the LangGraph
workflow while an external C# executor performs the CATIA operation, and
then resumes the workflow with the execution result.

The Python backend does not execute CATIA COM automation. CATIA operations
remain in the external .NET application.

The model provider is selected by `CATIA_AGENT_MODEL_PROVIDER` and
implemented behind a provider-neutral `ToolCallingModelClient` protocol
(see `catia_agent/model_providers.py`), so switching providers never
changes the graph, MCP catalog, API contracts, or validation policy.
Local development defaults to a local Ollama installation; the intended
production provider is Qwen 3.6 27B hosted on Azure AI Foundry, reached
over an OpenAI-compatible chat-completions endpoint with bearer-token
authentication. See Sections 4 and 9.

## 2. High-level architecture

```text
WPF / .NET client
    |
    | REST request
    v
FastAPI Agent API :8000
    |
    v
LangGraph workflow
    |
    +--> MCP client --> CATIA MCP server :8001
    |                       |
    |                       +--> Tool names
    |                       +--> Descriptions
    |                       +--> JSON input schemas
    |
    +--> ToolCallingModelClient --> Ollama :11434 (dev)
    |                            --> Azure AI Foundry (prod) --> Qwen 3.6 27B
    |                                                    |
    |                                                    +--> Native tool call
    |
    +--> Pydantic and policy validation
    |
    v
action_required response
    |
    | External C# execution result
    v
LangGraph resumes
    |
    v
completed or execution_failed response
```

### Responsibilities

- **FastAPI** exposes the REST API, versioned under `/api/v1`, plus health,
  readiness, tool diagnostics, an OpenAPI schema, and Swagger UI.
- **LangGraph** manages state, routing, interruption, resumption, and
  completion of Agent runs.
- **MCP server** is the authoritative catalog of approved CATIA executor
  tool names, descriptions, and argument schemas.
- **ToolCallingModelClient** is a provider-neutral interface implemented
  by `OllamaToolCallingModelClient` (local development) and
  `AzureFoundryToolCallingModelClient` (Qwen 3.6 27B on Azure AI
  Foundry); `CATIA_AGENT_MODEL_PROVIDER` selects which one is wired up,
  with no changes to the rest of the codebase.
- **Ollama** provides the local development tool-calling model.
  **Azure AI Foundry** provides the production tool-calling model.
- **Pydantic** validates API payloads, orchestrator and executor tool
  calls, CATIA context, and external execution results.
- **The .NET client** remains the final executor and calls the CATIA COM
  API.

## 3. Local services and ports

```text
FastAPI Agent API    http://127.0.0.1:8000
CATIA MCP server     http://127.0.0.1:8001/mcp
Ollama API           http://127.0.0.1:11434 (CATIA_AGENT_MODEL_PROVIDER=ollama)
FastAPI Swagger      http://127.0.0.1:8000/docs
FastAPI OpenAPI      http://127.0.0.1:8000/openapi.json
```

When `CATIA_AGENT_MODEL_PROVIDER=azure_foundry`, the model endpoint is
the configured `CATIA_AGENT_AZURE_FOUNDRY_ENDPOINT` instead of the local
Ollama URL; there is no fixed local port for it.

All development services are bound to loopback addresses. In production
`enable_api_docs` is disabled by policy (see Section 6), and `/docs`,
`/redoc`, and `/openapi.json` are not served.

## 4. Technology stack

- Python 3.11
- FastAPI
- Uvicorn
- LangGraph
- Pydantic 2
- Pydantic Settings
- FastMCP
- httpx (Azure AI Foundry HTTP client)
- truststore (uses the OS certificate store for TLS verification, required behind TLS-inspecting corporate proxies)
- Ollama Python client
- Ollama for Windows (local development)
- Qwen 3.6 27B on Azure AI Foundry (production model)
- langchain-ollama + NumPy (embedding-based ask-vs-agent intent classifier, Section 8.1)
- qllama/bge-small-en-v1.5 pulled in local Ollama (embedding model; required even when CATIA_AGENT_MODEL_PROVIDER is azure_foundry)

Environment setup used for development:

```powershell
conda create -n catia-agent python=3.11 -y
conda activate catia-agent
```

Typical dependencies:

```powershell
python -m pip install ollama pydantic pydantic-settings
python -m pip install fastapi "uvicorn[standard]" langgraph
python -m pip install "fastmcp>=3.4,<4"
python -m pip install httpx
python -m pip install truststore
python -m pip install langchain-ollama numpy
```

Pull the embedding model used by the ask-vs-agent classifier (Section
8.1) in your local Ollama install:

```powershell
ollama pull qllama/bge-small-en-v1.5
```

spaCy is no longer a dependency (see Section 8.2).

## 5. Project structure

```text
catia-agent-poc/
|
|-- .env
|-- .env.example
|-- .gitignore
|-- app.py
|-- pytest.ini
|-- README_.NET.md
|-- README_Python.md
|-- run_api.py
|-- show_graph.py
|
|-- catia_agent/
|   |-- __init__.py
|   |-- config.py
|   |-- graph.py
|   |-- graph_classification.py
|   |-- graph_completion.py
|   |-- graph_nodes.py
|   |-- graph_responses.py
|   |-- graph_session.py
|   |-- graph_state.py
|   |-- graph_terminal.py
|   |-- logging_config.py
|   |-- mcp_tools.py
|   |-- model_providers.py
|   |-- models.py
|   |-- planner.py
|   |-- readiness.py
|
|-- catia_mcp/
|   |-- __init__.py
|   |-- server.py
|
|-- tests/
|   |-- __init__.py
|   |-- test_agent_api.py
|   |-- test_graph.py
|   |-- test_health_api.py
|   |-- test_mcp_server.py
|   |-- test_mcp_tools.py
|   |-- test_model_providers.py
|   |-- test_models.py
|   |-- test_planner.py
|   |-- test_readiness.py
```

Automated tests live under `tests/` and are run with `pytest` (configured
by `pytest.ini`). Tests marked `integration` require a local Ollama and the
CATIA MCP server; the rest run without them. `show_graph.py` remains useful
for visualizing the compiled LangGraph, and Swagger and Postman are the
recommended way to exercise the API end-to-end.

The previous hard-coded tool registry has been removed. MCP is the sole
source of executor tool definitions.

## 6. Configuration

### Environment variables

All settings use the `CATIA_AGENT_` prefix and are loaded once by
`pydantic-settings` (see `catia_agent/config.py`). A local `.env` file is
supported for development.

Representative `.env` (see `.env.example` for the full list):

```dotenv
CATIA_AGENT_APP_NAME=CATIA Agent API
CATIA_AGENT_APP_VERSION=1.0.0
CATIA_AGENT_ENVIRONMENT=development

CATIA_AGENT_API_HOST=127.0.0.1
CATIA_AGENT_API_PORT=8000
CATIA_AGENT_API_PREFIX=/api/v1
CATIA_AGENT_ENABLE_API_DOCS=true
CATIA_AGENT_REQUEST_TIMEOUT_SECONDS=60
CATIA_AGENT_REQUEST_BODY_MAX_BYTES=1048576

CATIA_AGENT_MAX_USER_MESSAGE_CHARACTERS=10000
CATIA_AGENT_MAX_SESSION_ID_CHARACTERS=128
CATIA_AGENT_MAX_REQUEST_ID_CHARACTERS=128

CATIA_AGENT_MODEL_PROVIDER=azure_foundry
CATIA_AGENT_MODEL_TEMPERATURE=0

CATIA_AGENT_OLLAMA_BASE_URL=http://127.0.0.1:11434
CATIA_AGENT_OLLAMA_MODEL=gemma4:latest
CATIA_AGENT_OLLAMA_REQUEST_TIMEOUT_SECONDS=60

CATIA_AGENT_AZURE_FOUNDRY_ENDPOINT=
CATIA_AGENT_AZURE_FOUNDRY_API_KEY=
CATIA_AGENT_AZURE_FOUNDRY_DEPLOYMENT_NAME=qwen3.6-27b-instruct
CATIA_AGENT_AZURE_FOUNDRY_API_VERSION=2024-05-01-preview
CATIA_AGENT_AZURE_FOUNDRY_REQUEST_TIMEOUT_SECONDS=60
CATIA_AGENT_AZURE_FOUNDRY_MAX_TOKENS=2048
CATIA_AGENT_AZURE_FOUNDRY_ENABLE_THINKING=false

CATIA_AGENT_MCP_SERVER_URL=http://127.0.0.1:8001/mcp
CATIA_AGENT_MCP_REQUEST_TIMEOUT_SECONDS=15
CATIA_AGENT_MCP_TOOL_CACHE_TTL_SECONDS=60
CATIA_AGENT_READINESS_FORCE_MCP_REFRESH=false

CATIA_AGENT_ENABLE_EMBEDDING_INTENT_CLASSIFIER=true
CATIA_AGENT_INTENT_CLASSIFIER_MODEL=qllama/bge-small-en-v1.5
CATIA_AGENT_INTENT_CLASSIFIER_TOP_K=3
CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_THRESHOLD=0.48
CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_MARGIN=0.03

CATIA_AGENT_MAX_CONVERSATION_HISTORY_MESSAGES=20
CATIA_AGENT_ENABLE_CONVERSATION_HISTORY=false
CATIA_AGENT_MAX_ACTIONS_PER_RUN=20
CATIA_AGENT_ENABLE_MULTI_ACTION_SEQUENCES=false
CATIA_AGENT_MAX_COMPLETED_ACTION_RESULT_BYTES=262144
CATIA_AGENT_PENDING_RUN_TIMEOUT_SECONDS=1800

CATIA_AGENT_LOG_LEVEL=INFO
CATIA_AGENT_ENABLE_JSON_LOGS=true
CATIA_AGENT_INCLUDE_TECHNICAL_DETAILS_IN_ERRORS=false

CATIA_AGENT_ENABLE_TOOL_CATALOG_ENDPOINT=true
CATIA_AGENT_ENABLE_TOOL_CACHE_CLEAR_ENDPOINT=true
CATIA_AGENT_ADMIN_API_KEY=
```

### Configuration principles

- Keep `.env` out of source control.
- Commit `.env.example` with safe defaults.
- Settings are cached for the life of the Python process. Restart the
  application after changing environment variables.
- Do not hard-code model names or service URLs across modules.
- `CATIA_AGENT_MODEL_PROVIDER` defaults to `azure_foundry`. This requires
  `CATIA_AGENT_AZURE_FOUNDRY_ENDPOINT`, `CATIA_AGENT_AZURE_FOUNDRY_API_KEY`,
  and `CATIA_AGENT_AZURE_FOUNDRY_DEPLOYMENT_NAME` to be filled in;
  `Settings.validate_model_provider_configuration` fails fast at startup
  if `azure_foundry` is selected while any of those three are empty.
  Switch it to `ollama` for offline local development with no external
  network dependency (no Azure settings required in that case).
- `CATIA_AGENT_AZURE_FOUNDRY_API_KEY` is a bearer token, stored as a
  Pydantic `SecretStr` and included in the logging redaction list; never
  print or log it directly.
- `CATIA_AGENT_ENABLE_CONVERSATION_HISTORY` (default `false`) and
  `CATIA_AGENT_ENABLE_MULTI_ACTION_SEQUENCES` (default `false`) are
  independent switches described in Section 8.2.

### Environment policy

`Settings.validate_environment_policy` enforces production-safe defaults.
When `CATIA_AGENT_ENVIRONMENT=production`:

- `CATIA_AGENT_ENABLE_API_DOCS` must be `false`.
- `CATIA_AGENT_INCLUDE_TECHNICAL_DETAILS_IN_ERRORS` must be `false`
  (technical exception messages are never returned to clients).
- If `CATIA_AGENT_ENABLE_TOOL_CACHE_CLEAR_ENDPOINT` is `true`,
  `CATIA_AGENT_ADMIN_API_KEY` must be set.

The application fails to start if any of these are violated.

### Administrative authorization

The `POST /api/v1/tools/cache/clear` endpoint is protected by an
`X-Admin-API-Key` header when `CATIA_AGENT_ADMIN_API_KEY` is configured.
The check uses a constant-time comparison. When the key is empty (local
development only), the endpoint is unauthenticated so Postman and Swagger
can exercise it. In production the key is mandatory (see above).

## 7. Core API endpoints

All Agent and tool endpoints are versioned under `CATIA_AGENT_API_PREFIX`
(`/api/v1` by default). Health endpoints are unversioned.

### Liveness

```http
GET /health
```

Returns `{"status": "healthy", ...}`. This must remain cheap and must not
perform dependency checks.

### Readiness

```http
GET /ready
```

Runs the configured `ReadinessCheck` implementations concurrently and
returns `200` when every check reports `available`, otherwise `503`.
The current checks verify:

- The model provider is reachable and the configured model is installed.
- The CATIA MCP server is reachable and exposes at least one usable tool.

The Ollama readiness check is implemented by `OllamaReadinessCheck` and
can be swapped for an Azure equivalent without changing the aggregate
report (see `catia_agent/readiness.py`).

### Tool diagnostics

```http
GET  /api/v1/tools
GET  /api/v1/tools?refresh=true
POST /api/v1/tools/cache/clear         (X-Admin-API-Key required in prod)
```

These endpoints expose the MCP-discovered tool metadata that the planner
sees. `/api/v1/tools` is disabled entirely by
`CATIA_AGENT_ENABLE_TOOL_CATALOG_ENDPOINT=false`. The cache-clear endpoint
requires the admin key when configured.

### Start an Agent run

```http
POST /api/v1/agent/runs
```

The caller does not select a chat or agent mode. Every message goes through
the same orchestrator, which decides whether the request needs a CATIA
action or is a general conversational query (see Section 8.1).

Example request:

```json
{
  "sessionId": "session-001",
  "message": "Open C:\\TestData\\Gearbox.CATProduct",
  "catiaContext": {
    "isRunning": true,
    "activeDocument": null,
    "openDocuments": [],
    "selectedObjects": []
  }
}
```

Example agentic response:

```json
{
  "status": "action_required",
  "sessionId": "session-001",
  "runId": "generated-run-id",
  "actionId": "generated-action-id",
  "action": {
    "action": "open_cad_document",
    "arguments": {
      "filePath": "C:\\TestData\\Gearbox.CATProduct"
    }
  }
}
```

Example chat response, for a message such as "what file types can you
open?":

```json
{
  "status": "non_agentic",
  "sessionId": "session-001",
  "runId": "generated-run-id",
  "message": "This request does not require a CATIA execution action.",
  "routeTarget": "general_chat",
  "routeReason": "general_conversation"
}
```

On `non_agentic`, the Python backend has not answered the user's message
and never will. The `message` field is a routing note for logs, not an
answer. The caller is responsible for sending the original message to its
own general-purpose chat model (for example, GPT-4) and returning that
answer to the user.

Example response after a multi-action request such as "open
Gearbox.CATProduct and then close it" completes both steps (see
Section 8.2):

```json
{
  "status": "completed",
  "sessionId": "session-001",
  "runId": "generated-run-id",
  "actionId": "generated-action-id-2",
  "message": "Gearbox.CATProduct was opened successfully. Gearbox.CATProduct closed successfully.",
  "data": {"closedDocuments": ["Gearbox.CATProduct"]},
  "completedActions": [
    {
      "action": "open_cad_document",
      "actionId": "generated-action-id-1",
      "arguments": {"filePath": "C:\\TestData\\Gearbox.CATProduct"},
      "message": "Gearbox.CATProduct was opened successfully.",
      "data": {"documentName": "Gearbox.CATProduct"}
    },
    {
      "action": "close_cad_document",
      "actionId": "generated-action-id-2",
      "arguments": {"targetMode": "active", "documentName": null},
      "message": "Gearbox.CATProduct closed successfully.",
      "data": {"closedDocuments": ["Gearbox.CATProduct"]}
    }
  ]
}
```

`completedActions` appears on any terminal response
(`completed`, `execution_failed`, `non_agentic`,
`clarification_required`, `unsupported_request`, `planning_error`) once
at least one action in the sequence has already succeeded. On `completed`
and `execution_failed` responses the field is always present (possibly
empty). On the other statuses it is omitted entirely when the sequence
never got past the first step.

### Submit an execution result

```http
POST /api/v1/agent/runs/{runId}/action-results
```

Example success request:

```json
{
  "actionId": "generated-action-id",
  "success": true,
  "data": {
    "documentName": "Gearbox.CATProduct",
    "fullPath": "C:\\TestData\\Gearbox.CATProduct",
    "documentType": "CATProduct"
  },
  "errorCode": null,
  "errorMessage": null
}
```

Example failure request:

```json
{
  "actionId": "generated-action-id",
  "success": false,
  "data": null,
  "errorCode": "CATIA_DOCUMENT_NOT_FOUND",
  "errorMessage": "The requested CATIA document could not be located."
}
```

`errorCode` and `errorMessage` are required when `success` is `false` and
forbidden when `success` is `true` (`ActionExecutionResultRequest`
enforces this at the Pydantic level).

Example completion:

```json
{
  "status": "completed",
  "sessionId": "session-001",
  "runId": "generated-run-id",
  "actionId": "generated-action-id",
  "message": "Gearbox.CATProduct was opened successfully.",
  "data": {
    "documentName": "Gearbox.CATProduct",
    "fullPath": "C:\\TestData\\Gearbox.CATProduct"
  },
  "completedActions": [ ... ]
}
```

### Error contract

Every non-2xx response uses the same envelope:

```json
{
  "detail": {
    "status": "<short_status>",
    "errorCode": "<STABLE_CODE>",
    "message": "<human-readable>",
    "technicalDetail": "<optional, dev/test only>",
    "validationErrors": [ ... ]
  }
}
```

`technicalDetail` is only included when
`CATIA_AGENT_INCLUDE_TECHNICAL_DETAILS_IN_ERRORS=true`. Production must
keep this disabled. `validationErrors` is included when the failure comes
from Pydantic and carries the underlying error list.

Stable error codes produced by the API:

| HTTP | errorCode                          | Source                           | Meaning                                                                                  |
| ---- | ---------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------- |
| 400  | `INVALID_CONTENT_LENGTH`         | request middleware               | The`Content-Length` header could not be parsed.                                        |
| 401  | `ADMIN_API_KEY_REQUIRED`         | admin dependency                 | An admin endpoint was called without`X-Admin-API-Key`.                                 |
| 403  | `ADMIN_API_KEY_INVALID`          | admin dependency                 | The supplied admin key did not match (constant-time compare).                            |
| 404  | `AGENT_RUN_NOT_FOUND`            | action-results                   | No pending run matches the supplied`runId`.                                            |
| 409  | `AGENT_RUN_STATE_CONFLICT`       | `/agent/runs`, action-results  | The session or run is not in a state compatible with the request (see Section 8).        |
| 413  | `REQUEST_BODY_TOO_LARGE`         | request middleware               | Request body exceeds`CATIA_AGENT_REQUEST_BODY_MAX_BYTES`.                              |
| 422  | `REQUEST_VALIDATION_FAILED`      | FastAPI request validation       | The submitted payload did not match the API contract.                                    |
| 422  | `MODEL_SCHEMA_VALIDATION_FAILED` | planner and record-action-result | The model returned a tool call or the executor returned a result that failed Pydantic.   |
| 500  | `INTERNAL_SERVER_ERROR`          | request middleware               | An unhandled exception was caught. Logged with a stack trace, not exposed to the client. |
| 503  | `MCP_REQUEST_FAILED`             | any endpoint that touches MCP    | The CATIA MCP tool service was unreachable or returned an invalid catalog.               |
| 503  | `MODEL_PROVIDER_FAILED`          | planner                          | The configured tool-calling model provider failed (non-timeout).                         |
| 503  | `OLLAMA_REQUEST_FAILED`          | planner                          | Ollama returned an error while the current provider was making a request.                |
| 504  | `MODEL_REQUEST_TIMED_OUT`        | planner                          | The model did not respond within`CATIA_AGENT_OLLAMA_REQUEST_TIMEOUT_SECONDS`.          |
| 504  | `REQUEST_TIMED_OUT`              | request middleware               | The request did not complete within`CATIA_AGENT_REQUEST_TIMEOUT_SECONDS`.              |

`AGENT_RUN_STATE_CONFLICT` is deliberately narrow — only the three
run-state guards enumerated in Section 8 raise it. All other failures
surfaced from resume paths (planner failures, MCP outage, model outage,
unknown-tool rejection) map to their own dedicated codes above.

Planner failures produce a normal 200 response with
`status: "planning_error"` and an internal `errorCode` from the following
set (see `PlanningErrorResult`):

- `MODEL_RETURNED_NO_DECISION` — only on the first planning pass. If the
  model returns no tool call on a *follow-up* pass and at least one
  action has already completed, the planner either treats it as an
  implicit `complete_request` or returns `planning_error`, depending on
  follow-up confidence (see Section 8.2).
- `MODEL_RETURNED_MULTIPLE_DECISIONS`
- `MODEL_RETURNED_INVALID_TOOL`
- `MODEL_RETURNED_INVALID_ARGUMENTS`

These are terminal Agent results, not HTTP errors, and are safe to render
to the user because they contain no technical detail. When one occurs
mid-sequence, the response includes the accumulated `completedActions`
so the caller can still show what succeeded before the planner failed.

Every response includes the `X-Request-ID` header. The middleware accepts
a caller-supplied `X-Request-ID` if it matches
`^[A-Za-z0-9._:-]+$` and is within the configured length; otherwise a
fresh UUID is used so logs and headers cannot be poisoned.

## 8. LangGraph workflow

The graph implementation is split across several modules so no single
file exceeds roughly 400 lines:

```text
catia_agent/graph_state.py           CatiaAgentState, run exceptions,
                                      TERMINAL_READ_ONLY_TOOLS
catia_agent/graph_classification.py  the lexical planning-mode classifier
catia_agent/graph_responses.py       response-building helpers shared by
                                      multiple nodes
catia_agent/graph_session.py         run/session bookkeeping
                                      (_run_sessions, state snapshot
                                      helpers)
catia_agent/graph_nodes.py           plan_action_node, prepare_action_node,
                                      await_action_result_node
catia_agent/graph_completion.py      record_action_result_node (the
                                      sequential multi-action bookkeeping
                                      in Section 8.2)
catia_agent/graph_terminal.py        prepare_sequence_completed_response_node,
                                      prepare_terminal_response_node
catia_agent/graph.py                 routing, graph construction, and the
                                      public start_catia_agent /
                                      resume_catia_agent entry points
```

`catia_agent/graph.py` re-exports `AgentRunStateConflictError`,
`AgentRunNotFoundError`, `CatiaAgentState`, `TERMINAL_READ_ONLY_TOOLS`,
and `get_session_id_for_run` from the modules above, so existing
`from catia_agent.graph import ...` call sites (app.py, tests) do not
need to know about the split. New code that only needs one of these
should still prefer importing from `catia_agent.graph` for that reason.

The compiled graph (see `catia_agent/graph.py`):

```text
START
  |
  v
plan_action
  |
  +--> action_required
  |       |
  |       v
  |   prepare_action
  |       |
  |       v
  |   await_action_result
  |       |
  |       | interrupt
  |       v
  |   External C# execution
  |       |
  |       | resume with Command(resume=...)
  |       v
  |   record_action_result
  |       |
  |       +--> failed --------------------------> END
  |       |
  |       +--> succeeded (terminal read tool) --> END
  |       |
  |       +--> succeeded, loop back ---> plan_action
  |
  +--> request_completed (from complete_request or duplicate guard)
  |       |
  |       v
  |   prepare_sequence_completed
  |       |
  |       v
  |      END
  |
  +--> non_agentic, clarification_required,
       unsupported_request, invalid_request, planning_error
          |
          v
      prepare_terminal_response
          |
          v
         END
```

### 8.1 Chat vs agentic orchestration

The .NET client sends only the user message. It does not choose a chat or
agent mode. Deciding that is the orchestrator's job.

That ask-vs-agent decision is made in two stages:

1. **Embedding classifier (primary, default on).** Before the first
   planner call of a run, `is_agentic_request` (see
   `catia_agent/intent_classifier.py`) embeds the user message with a
   local Ollama model (`CATIA_AGENT_INTENT_CLASSIFIER_MODEL`, default
   `qllama/bge-small-en-v1.5`) and compares it against two curated sets
   of exemplar phrases — concrete CATIA action requests vs. general or
   informational messages — using cosine similarity. If the message
   scores confidently as a CATIA action request
   (`CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_THRESHOLD`, with at least
   `CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_MARGIN` separation over the
   general-chat score), the run proceeds to the model as normal. If it
   confidently scores as general/informational, `plan_action_node`
   returns a `non_agentic` result immediately and **the model is never
   called for that turn** — this is a full Azure Foundry/Ollama chat
   round trip saved per non-agentic message. This step requires a
   local Ollama install with the embedding model pulled, even when
   `CATIA_AGENT_MODEL_PROVIDER` is `azure_foundry`, since embeddings
   always run locally regardless of the chat provider.
2. **Model tool selection (fallback and safety net).** If the
   embedding classifier is disabled
   (`CATIA_AGENT_ENABLE_EMBEDDING_INTENT_CLASSIFIER=false`), if it is
   uncertain (score below the threshold/margin), or if the local
   Ollama embedding call fails for any reason (for example, Ollama is
   not running), the run falls back to today's behavior: the model
   makes the routing decision through a tool call and never answers
   the user directly.

- If the model selects a CATIA MCP tool, the request is agentic. It
  proceeds through `prepare_action` → `await_action_result` →
  `record_action_result`.
- If the model determines the request does not require a CATIA action, it
  must call the `route_to_general_chat` orchestrator tool. This returns
  `non_agentic` through `prepare_terminal_response`. The `.message` field
  is a routing note, not an answer, and the caller must route the original
  message to a separate general-purpose chat model and return that
  model's answer to the user. The embedding classifier short-circuit
  above produces the exact same `non_agentic` shape, so callers do not
  need to distinguish which stage decided this.
- If the request clearly requires execution but no approved tool supports
  it, the model must call `report_unsupported_catia_action`. This returns
  `unsupported_request` with a `requestedCapability` and `reason`.
- If required information is missing or ambiguous (for example, "close a
  document" with multiple open documents), the model must call
  `request_clarification`. This returns `clarification_required` with a
  `question` and `missingField`.
- During follow-up planning after one or more actions have already
  completed in the same run, the model can also call `complete_request` to
  signal that nothing remains. This produces a `RequestCompletedResult`
  which routes to `prepare_sequence_completed`.

Routing through explicit tool calls (`route_to_general_chat`,
`report_unsupported_catia_action`, `request_clarification`,
`complete_request`) rather than free-form text keeps the decision
structured and reliable. These four tools are orchestrator-only and are
defined in `catia_agent/planner.py`; they are deliberately not published
by the MCP server because they are not CATIA executor tools.

### 8.2 Sequential multi-action requests

`CATIA_AGENT_ENABLE_MULTI_ACTION_SEQUENCES` (default `false`) is a hard
switch on everything described in this section. When it is `false`,
`record_action_result_node` (see `catia_agent/graph_completion.py`)
finalizes every run as `completed` immediately after its first
successful action, regardless of the planning-mode classification below
— a compound request such as "open X, then close X" only opens X and
stops. This caps every run at exactly one planner call, so disabling it
directly reduces model calls: a request that would otherwise need N
follow-up planner round-trips instead needs none. Set it to `true` to
restore the sequential behavior documented in the rest of this section.

A single message can require more than one CATIA action, for example
"open Gearbox.CATProduct and Frame.CATProduct, then close XYZ.CATPart".
The model is not asked to enumerate every step up front. Instead, the
planner is called again after each action succeeds, with that action's
outcome added to the conversation context, so it can decide what (if
anything) from the original request still remains:

Before the first planning call, the graph also performs a deterministic,
zero-model-cost, dependency-free request-shape classification (see
`_classify_planning_mode` in `catia_agent/graph.py`). This previously used
a spaCy dependency-parse classifier; it was replaced with a lightweight
lexical classifier because spaCy added a ~30MB model download and
non-trivial per-process load time without being meaningfully more
reliable for this narrow job. The classifier splits the message on
sequence connectors ("then", "after that", "followed by", "next", ";",
"and") and checks whether each resulting clause opens with a CATIA action
verb (after stripping conversational filler such as "please", "can you",
"I need to"), rather than doing full part-of-speech tagging.
This classifier is not the ask-vs-agent decision maker; that decision is
made primarily by the embedding classifier described in Section 8.1, with
the model's route_to_general_chat/CATIA tool selection as the fallback
and safety net (see Section 8.1 and the system prompt in
`catia_agent/planner.py`). It only supplies a cheap, cautious
signal for whether the follow-up planning loop below can be skipped after
the first action succeeds.

If the message is a high-confidence single CATIA action request, a fast path
is enabled and the graph finalizes immediately after that first successful
action without issuing a follow-up planner call. If the request is compound
or uncertain, the existing follow-up planning loop is used. Because the
classifier is deliberately conservative, uncertain or multi-verb messages
default to the follow-up loop; the Azure-hosted Qwen 3.6 27B model is
materially more reliable than the earlier small local model at obeying the
goal-delta follow-up prompting (see below), so leaning on that loop more
often is now a good trade rather than a latency risk. The duplicate-action
guard and silent-model recovery described later in this section are the
real deterministic safety net and do not depend on classifier accuracy.

When a message has more than one clause but only one clause opens with a
CATIA verb, the classifier can emit `single_action_fast_path` with medium
confidence and signal `mixed_predicates_single_catia_intent`.

If that signal is present, exactly one CATIA action slot is expected, and
the selected action is `start_model`, the run now short-circuits after the
first successful action without issuing the follow-up planner pass. This
avoids unnecessary duplicate-action retries on single-intent start-model
requests phrased with auxiliary or mixed verbs.

1. `plan_action` runs, the model proposes one action, it executes.
2. On success, `record_action_result` appends the action's structured
   record and its assistant-role progress message and loops back to
   `plan_action`.
3. `plan_action` runs again with the original message plus the progress
   messages and an explicit list of already-completed actions with their
   arguments. If the model proposes another CATIA action, the loop
   repeats. If the model calls `complete_request`, the run finalizes as
   `completed` with every step's message joined together.

Six things can end the sequence early:

- **A failure stops it immediately.** If any action fails,
  `record_action_result` writes the final response directly and the run
  ends as `execution_failed`. Actions not yet attempted are never
  attempted. The response combines what succeeded so far with the failure.
- **`request_clarification` or `route_to_general_chat` can appear
  mid-sequence.** Both stop the sequence the same way a failure does, but
  with their own status.
- **Terminal read-only tool short-circuit.** If the action that just
  succeeded is a member of `TERMINAL_READ_ONLY_TOOLS` in
  `catia_agent/graph_state.py` (currently only `get_active_document`),
  `record_action_result` builds the `completed` response directly and
  skips the follow-up planning pass. This is logged as
  `planner_skipped_terminal_read`. Read tools answer a question with a
  discrete data payload and do not naturally chain into a next action,
  so a follow-up planner call would only add latency and expose the
  duplicate-action and silent-model failure modes described below. The
  documented trade-off is that compound "query-then-act" requests such
  as "what is the active document, then close it" require a second
  user turn; the natural phrasing "close the active document" already
  bypasses the query and executes in one step.
- **Duplicate-action guard with one-shot corrective retry.** If the
  planner proposes an action whose name AND arguments exactly match one
  already completed in the same run, `plan_action` logs
  `planner_duplicate_action_detected` and calls the planner once more
  with a targeted corrective system message that names the duplicated
  action and re-anchors the goal-delta reasoning. If the retry produces
  a different action (or `complete_request`), the run continues normally
  and logs `planner_duplicate_retry_recovered`. If the retry duplicates
  again, the guard falls back to `RequestCompletedResult`, the run
  finalizes as `completed` with what has been done so far, and
  `planner_duplicate_retry_failed` is logged. This is the hard rail
  against small tool-calling models re-selecting a just-completed tool
  on the follow-up planning pass.
- **Silent-model recovery.** If the model returns no tool call at all
  on a follow-up planning pass (empty `tool_calls`) and at least one
  action has already completed in the run, the planner treats that as
  an implicit `complete_request` and finalizes the run as `completed`
  only when follow-up certainty is not high.
  For high-confidence `follow_up_required` runs, this fallback is
  blocked and the planner returns `planning_error` instead so explicit
  multi-step requests cannot be silently truncated.
  This is logged as `planner_no_decision_treated_as_complete`. On the
  first planning pass (no completed actions yet), an empty response is
  still a real failure and finalizes as `planning_error` with
  `MODEL_RETURNED_NO_DECISION`.
- **A safety cap (`CATIA_AGENT_MAX_ACTIONS_PER_RUN`, default 20) stops
  runaway sequences.** If a planning error or model quirk keeps proposing
  *different* actions indefinitely (bypassing the duplicate guard), the
  run stops with `unsupported_request` and `reason: action_limit_reached`
  once the cap is reached, reporting everything completed up to that
  point.
- **Expected-action exhaustion short-circuit.** For high-confidence
  `follow_up_required` requests, the graph computes expected CATIA action
  slots from the initial lexical clause classification. After each
  successful action, if completed action count has reached that expected
  count, the run finalizes directly as `completed` without an extra
  planner call for `complete_request`. This reduces latency on compound
  requests that are already fully satisfied.

Every follow-up planning call after a successful action is given a
"goal-delta" system message: the original user request is quoted, the
actions already completed are listed with their exact arguments, and the
model is asked to compare the two and either select the single next
remaining CATIA action or call `complete_request`. Small local models
do not always obey this framing, which is why the
duplicate-action guard (with corrective retry) and the silent-model
recovery above are enforced deterministically in the graph rather than
left as prompt hints alone. See `catia_agent/graph.py` and
`catia_agent/planner.py`.

Follow-up calls also use a shorter, dedicated system prompt
(`_build_followup_system_prompt` in `catia_agent/planner.py`) instead of
the full first-turn prompt. The ask-vs-agent framing and the
question-vs-command examples in the first-turn prompt
(`_build_system_prompt`) only matter for deciding whether the original
message is agentic at all; by the time a follow-up call happens, that is
already settled, and repeating that guidance on every follow-up call is
wasted prompt tokens on every remaining step of a compound request. The
leaner prompt keeps only what still applies mid-sequence: exactly one
tool call, no free-form text, and the orchestrator tools that can
legitimately still fire (`request_clarification`,
`report_unsupported_catia_action`, `route_to_general_chat` — see the
mid-sequence bullet above). The goal-delta comparison itself is not
duplicated in either prompt; it lives solely in the per-turn system
message described above.

This calls the planner multiple times (once per action) rather than
asking the model to return several tool calls in one response. Each call
stays as simple and reliable as a single-action request; nothing about
`plan_catia_action` itself changes to support this; the looping and
progress-tracking live entirely in `graph.py`. See Section 16 for why
this is not the same thing as splitting into a supervisor and specialized
sub-agents.

In high-confidence single-action requests, the fast path above avoids the
extra follow-up planner call and reduces latency for common one-step
commands while preserving the same terminal API contracts.

### State fields

Typical state contains:

```text
session_id
run_id
action_id
user_message
catia_context
messages
sequence_progress
planner_result
pending_action
action_result
completed_actions
final_response
```

`messages` accumulates the conversation as `{"role", "content"}` pairs
after every completed or terminal turn. The planner receives a trimmed
window of this history (`CATIA_AGENT_MAX_CONVERSATION_HISTORY_MESSAGES`)
so it can resolve references such as "the first one" or "that document"
against what the Agent previously said.

`CATIA_AGENT_ENABLE_CONVERSATION_HISTORY` (default `false`) gates
whether `messages` from earlier runs in the same session is carried
into a new run at all (see `start_catia_agent` in `catia_agent/graph.py`).
When `false`, every run starts with an empty `messages` list regardless
of what a previous run in the same session recorded, so the planner has
no memory of earlier turns and cannot resolve references like "the one I
just opened" from a prior message. This does not affect
`sequence_progress`, which is scoped to a single run and needed for that
run's own multi-action follow-up planning regardless of this setting.

`completed_actions` and `sequence_progress` exist only for the lifetime
of one run (see Section 8.2). `completed_actions` accumulates a
structured entry per successful action (`action`, `actionId`,
`arguments`, `message`, `data`) for the final `completedActions`
response field and for the duplicate-action guard in `plan_action`.
`sequence_progress` holds one short assistant-role turn per completed
action so the planner sees progress on its next call within the same
run. Both are reset to empty at the start of every new run; they are
never carried over into session-level `messages` history.

### Identifier semantics

- `sessionId` identifies the conversation or client session. It is the
  LangGraph thread key, so conversation history persists across multiple
  Agent runs within the same session.
- `runId` identifies one Agent workflow within a session, which may
  involve more than one action (see Section 8.2).
- `actionId` identifies one externally executed CATIA action. A single
  `runId` can have several `actionId` values in sequence.

Because the thread is keyed by `sessionId` rather than `runId`, the
action-results endpoint (which only receives a `runId`) resolves the
owning session through an in-memory `runId -> sessionId` map in
`graph.py`. Three run-state guards raise
`AgentRunStateConflictError`, which the API surfaces as HTTP 409 with
`errorCode: AGENT_RUN_STATE_CONFLICT`:

- Starting a new run on `POST /api/v1/agent/runs` while the same session
  still has a pending action result.
- Submitting an action result whose `runId` does not match the currently
  pending run for that session.
- Submitting an action result to a run that is not paused at
  `await_action_result`, or whose `actionId` does not match the pending
  one.

Any other error surfaced from the resume path (planner validation, MCP
unavailability, model outage, unknown-tool policy rejection) maps to its
own dedicated status code and error code (see Section 7.5) so it cannot
be mistaken for a state conflict.

### Persistence limitation

The current build uses LangGraph's `InMemorySaver`. Conversation history,
pending runs, and the `runId -> sessionId` map are all lost when FastAPI
restarts or Uvicorn reloads. Replacing the checkpointer with a persistent
store (SQLite for a single node, or an approved enterprise datastore for
horizontal scale) is a prerequisite for production traffic. This is the
single largest remaining production gap in the Python backend; see
Section 17.

## 9. Native tool calling and the model-provider abstraction

Tool selection uses the model provider's native tool-calling interface.
The model receives typed function definitions rather than being prompted
to emit arbitrary JSON.

Conceptual tool schema:

```json
{
  "type": "function",
  "function": {
    "name": "open_cad_document",
    "description": "Prepare an action to open an existing CATIA document.",
    "parameters": {
      "type": "object",
      "required": ["filePath"],
      "properties": {"filePath": {"type": "string"}},
      "additionalProperties": false
    }
  }
}
```

The planner talks to the model through a provider-neutral
`ToolCallingModelClient` protocol (see `catia_agent/model_providers.py`):

```python
class ToolCallingModelClient(Protocol):
    async def select_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any: ...
```

Every implementation normalizes its provider-specific response into the
same shape (an object or dict exposing `message.tool_calls`, each tool
call exposing `function.name` / `function.arguments`) so `planner.py`
never branches on provider.

`OllamaToolCallingModelClient` wraps `ollama.AsyncClient`, applies
`CATIA_AGENT_OLLAMA_REQUEST_TIMEOUT_SECONDS` via `asyncio.timeout`, and
raises `ModelProviderTimeoutError` on timeout. It is used for local
development (`CATIA_AGENT_MODEL_PROVIDER=ollama`).

`AzureFoundryToolCallingModelClient` is the production implementation for
Qwen 3.6 27B hosted on Azure AI Foundry
(`CATIA_AGENT_MODEL_PROVIDER=azure_foundry`). It POSTs an OpenAI-compatible
chat-completions request to `{CATIA_AGENT_AZURE_FOUNDRY_ENDPOINT}/chat/completions`
over `httpx.AsyncClient`, authenticated with
`Authorization: Bearer {CATIA_AGENT_AZURE_FOUNDRY_API_KEY}`, and sets
`tool_choice: "required"` plus `max_tokens` from
`CATIA_AGENT_AZURE_FOUNDRY_MAX_TOKENS`. The request body deliberately
omits a `model` field: the deployment is already resolved by the
endpoint URL for this single-model serverless deployment, and some
Azure AI Foundry deployments reject an unexpected `model` value in the
body. `azure_foundry_deployment_name` is still required in settings —
it is used for logs, the `/ready` diagnostic payload, and
`active_model_name`, just not sent on the wire. Unlike Ollama, forcing
`tool_choice: "required"` structurally forces the model to return a
tool call on every well-formed request, which removes one whole failure
mode (`MODEL_RETURNED_NO_DECISION` on the very first planning pass)
that the small local model was prone to. HTTP failures raise
`AzureFoundryRequestError` (a `ModelProviderError` subclass, so no new
API error code is introduced) and timeouts raise
`ModelProviderTimeoutError`, exactly like the Ollama client.

The payload also sets `chat_template_kwargs.enable_thinking` from
`CATIA_AGENT_AZURE_FOUNDRY_ENABLE_THINKING` (default `false`). The vLLM
deployment behind this model is a reasoning model: with thinking left
on, every response carries a very large `reasoning` field alongside
`tool_calls`, burning a large share of `max_tokens` on chain-of-thought
that is never read (the system prompt already forbids reasoning/free-form
output) and materially increasing latency and truncation risk. This
field, when present, is safely ignored — `select_tool` only reads
`message.tool_calls`, and `_extract_tool_call` already parses
`function.arguments` whether the provider sends it as a JSON object
(Ollama) or a JSON-encoded string (this vLLM deployment).

`build_model_client(settings)` selects and constructs the concrete client
based on `CATIA_AGENT_MODEL_PROVIDER`; the result is exposed as the
module-level `model_client`. A future third provider only needs to
implement `select_tool` and be added to that factory — nothing else in
the graph, MCP layer, or API contracts changes.

The model selects a tool. It never executes it. The application validates
the tool call and, for CATIA executor tools, returns it to the .NET
executor.

## 10. MCP architecture

### MCP role

The MCP server is the authoritative source for:

- Executor tool names
- Tool descriptions
- JSON input schemas
- Tool-level argument validation
- Tool discovery

The MCP server does not call CATIA. It prepares typed action data for
the external WPF and C# executor.

### Currently published MCP tools

```text
open_cad_document       Prepare opening a CATPart, CATProduct, or CATDrawing
                        by absolute Windows path.
close_cad_document      Prepare closing the active, a named, or all open
                        documents. targetMode is required.
create_cad_document     Prepare creating a new, unsaved CATPart, CATProduct,
                        or CATDrawing document. No file path; saving is a
                        separate action.
download_cad            Prepare downloading a CAD document from PLM/Enovia.
                        Accepts either (partNumber + partRevision) or
                        partTitle, with optional documentType and savePath.
start_model             Prepare launching an approved CATIA start model.
                        Requires modelType and family-specific identifiers.
                        SAB/DU require outsystemsId + afisNumber;
                        customName is optional for SAB/DU.
                        SEATBELT requires partNumber + partRevision.
```

`download_cad` is the single approved tool for user intents such as
"download from PLM", "get from Enovia", "fetch CAD", or "retrieve from
PLM". Enovia and PLM are treated as the same system.

The tool accepts three deterministic input shapes:

- **PLM object identifier**
  - `partNumber`: `674089800A` or `X670830400C`
  - `partRevision`: `00`, `01`, and for `X...` part numbers also `00-02`
- **CAD identifier**
  - `partNumber`: `6740898`
  - `partRevision`: `CAD000`, `CAD002`
  - `documentType` (optional): `CATPart`, `CATProduct`, `CATDrawing`
    or aliases `part`, `product`, `assembly`, `drawing`
- **Title lookup**
  - `partTitle` only, for example `anchor plate` or `retractor`

`partTitle` cannot be combined with `partNumber` or `partRevision`.
When `partNumber` is supplied, `partRevision` is required.

`savePath` is optional. When omitted, it defaults to `C:\Temp`.
When provided, it must be an absolute Windows path. The resolved value
is always included in the action arguments returned to .NET.

`start_model` supports these model families and aliases:

- `SAB`: `side airbag`, `airbag`, `softcover`, `hardcover`, `wireharness`
- `DU`: `driver unit`, `steering wheel`
- `SEATBELT`: `seatbelt`, `buckle`

`SAB` and `DU` require `outsystemsId` and `afisNumber`.
`customName` is optional for `SAB` and `DU`.
The user may refer to `outsystemsId` as Outsystems ID, AO ID, or simply ID.

`SEATBELT` does not use `outsystemsId`. It requires `partNumber` and
`partRevision` instead, and must not include `afisNumber` or `customName`.
The accepted seatbelt part-number shape is a 9- or 10-digit numeric body
plus trailing letter (optionally `X`-prefixed), for example `674089800A` or
`6687874100A`, or an `X`-prefixed version such as `X6565654500A`.
Non-`X` seatbelt part numbers use revisions like `00` or `01`.
`X`-prefixed seatbelt part numbers require a hyphenated revision such as
`00-01`.

IC start model (`IC`, `inflatable curtain`, `curtain airbag`) is currently
unsupported. Requests that ask to start IC return
`unsupported_request` with message `Currently IC start model is not supported.`

### Orchestrator-only tools (not on MCP)

These four tools are planning control signals defined in
`catia_agent/planner.py`. They are added to the tool list handed to the
model at plan time but are never published by the MCP server:

```text
route_to_general_chat            Route the message to a general chat model.
request_clarification            Ask the user for a missing detail.
report_unsupported_catia_action  Report a CATIA operation with no approved tool.
complete_request                 During follow-up planning, signal that all
                                 requested CATIA actions are done.
```

### MCP discovery flow

```text
Agent backend
    |
    v
FastMCP Client.list_tools()
    |
    v
MCP tool definitions
    |
    v
Convert MCP schemas to model-provider function schemas
    |
    v
Model native tool selection
```

### Tool caching

The backend caches converted MCP tool definitions for
`CATIA_AGENT_MCP_TOOL_CACHE_TTL_SECONDS`. An `asyncio.Lock` prevents
concurrent requests from refreshing the cache simultaneously. The cache
is only a performance optimization; the MCP server remains the source of
truth. Operators can force a refresh via `GET /api/v1/tools?refresh=true`
or `POST /api/v1/tools/cache/clear` (admin key required in production).

`GET /ready` reuses this same cache (subject to the TTL) instead of
forcing a fresh MCP round trip on every call, unless
`CATIA_AGENT_READINESS_FORCE_MCP_REFRESH` is set to `true`.

## 11. CATIA runtime context

The .NET client may send a snapshot containing:

```text
isRunning
activeDocument
openDocuments
selectedObjects
capturedAt
contextVersion
```

`CatiaContext` (see `catia_agent/models.py`) enforces the invariants:

- When `isRunning` is `false`, `activeDocument`, `openDocuments`, and
  `selectedObjects` must all be empty.
- When `activeDocument` is set and `openDocuments` is non-empty, the
  active document must appear in `openDocuments`.
- `openDocuments` and `selectedObjects` are bounded (500 and 1000
  entries respectively) to prevent oversized planning prompts.

The context helps the model interpret commands. It is not authoritative
because CATIA state may change between collection and execution. Read
operations such as `get_active_document` must still be executed by the
.NET CATIA layer when the user asks for current information.

## 12. Validation layers

Every action passes through several independent checks:

```text
MCP schema
    |
    v
Model tool selection
    |
    v
Pydantic action model (executor or orchestrator)
    |
    v
Python deterministic policy
    |
    v
C# action allowlist
    |
    v
C# deterministic validation
    |
    v
CATIA COM
```

This layered approach is intentional. A model-selected tool is not
trusted merely because it has a valid name.

Examples of deterministic Python validation:

- Absolute Windows path required for document operations.
- Approved CATIA extension required (`.catpart`, `.catproduct`,
  `.catdrawing`).
- `download_cad` enforces strict shape and format rules:
  - `partNumber + partRevision` OR `partTitle` (mutually exclusive)
  - PLM part numbers: `\d{9}[A-Z]` or `X\d{9}[A-Z]`
  - PLM revisions: `\d{2}` (or `\d{2}-\d{2}` for `X...` part numbers)
  - CAD part numbers: `\d{7}` with revisions in `CAD\d{3}` format
  - `documentType` normalization: `part/product/assembly/drawing` ->
    `CATPart/CATProduct/CATDrawing`
  - `savePath` defaults to `C:\Temp` and must be absolute when set
- Exact typed action model required for each tool.
- Unknown MCP tools rejected (the planner raises if MCP publishes a tool
  outside `APPROVED_CATIA_TOOL_NAMES`).
- Returned `actionId` on `POST .../action-results` must match the pending
  action.
- `ActionExecutionResultRequest` enforces that `errorCode` and
  `errorMessage` are present when and only when `success` is `false`.
- Duplicate-action guard with corrective retry: an action whose name
  and arguments exactly match one already completed in the current run
  triggers a targeted retry; if the model still duplicates, the run is
  finalized as `completed` (Section 8.2).
- Silent-model recovery: an empty tool-call response on a follow-up
  planning pass (after ≥1 completed action) is treated as an implicit
  `complete_request` (Section 8.2).
- Terminal read-only tool short-circuit: read tools listed in
  `TERMINAL_READ_ONLY_TOOLS` in `catia_agent/graph_state.py` (currently
  `get_active_document`) finalize the run as `completed` immediately
  after a successful C# result without another planner call
  (Section 8.2).
- `complete_request` from the model is only accepted after at least one
  action has already completed in the same run; otherwise it is rejected
  as a planning error (`MODEL_RETURNED_INVALID_TOOL`).
- Session and request identifiers are constrained by
  `^[A-Za-z0-9._:-]+$` and configurable length limits.

## 13. Logging and observability

The application emits structured JSON logs when
`CATIA_AGENT_ENABLE_JSON_LOGS=true` (see `catia_agent/logging_config.py`).
Every log record automatically carries any of the following that are
bound in the current async context:

```text
requestId
sessionId
runId
actionId
```

`X-Request-ID` is echoed on every response for cross-service correlation.
The middleware only accepts a caller-supplied value when it matches
`^[A-Za-z0-9._:-]+$` and fits within the configured maximum length;
otherwise a fresh UUID is used.

Representative events emitted today:

```text
application_started
application_stopped
http_request_started
http_request_completed
http_request_timed_out
http_request_validation_failed
http_request_unhandled_exception
mcp_request_failed
mcp_readiness_failed
model_tool_selection_started
model_tool_selected
model_returned_no_decision
model_returned_multiple_decisions
model_tool_call_format_invalid
model_selected_unknown_tool
model_tool_arguments_invalid
model_request_timed_out
model_provider_failed
ollama_request_failed
ollama_readiness_timed_out
ollama_readiness_failed
ollama_model_unavailable
azure_foundry_request_failed
azure_foundry_readiness_timed_out
azure_foundry_readiness_failed
azure_foundry_readiness_rejected
mcp_approved_tools_missing
planner_duplicate_action_detected
planner_duplicate_retry_recovered
planner_duplicate_retry_failed
planner_no_decision_treated_as_complete
planner_no_decision_blocked_followup_completion
planner_skipped_terminal_read
planning_mode_classified
action_result_recorded
planner_followup_skipped_single_action
planner_followup_required
planner_followup_skipped_expected_actions_exhausted
action_result_received
agent_run_planned
agent_run_resumed
```

Sensitive field names (`authorization`, `apiKey`, `adminApiKey`,
`accessToken`, `refreshToken`, `password`, `secret`) are redacted before
being serialized to log records. Do not log full prompts, model
reasoning, CAD contents, or absolute file paths at normal information
level.

## 14. Starting the services

### Terminal 1: MCP server

```powershell
conda activate catia-agent
python -m catia_mcp.server
```

### Terminal 2: FastAPI

```powershell
conda activate catia-agent
python run_api.py
```

Ollama normally runs as a native Windows background application.

Before exercising the Agent, verify:

```text
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8000/ready
GET http://127.0.0.1:8000/api/v1/tools
```

Use Postman for manual API testing. Swagger is at `/docs` and OpenAPI at
`/openapi.json` when `enable_api_docs` is enabled.

## 15. Adding a new CATIA tool

Use the following sequence for every new executor tool.

### Step 1: Define the MCP tool

Add a typed function to `catia_mcp/server.py`, following the pattern in
that file (typed parameters, docstring, and
`create_executor_action(...)` for the return payload):

```python
@mcp.tool
def list_open_documents() -> dict[str, Any]:
    """Prepare a request to list all open CATIA documents."""

    return create_executor_action(
        action="list_open_documents",
        arguments={},
    )
```

The function name becomes the tool name. The docstring becomes part of
the description. Typed parameters generate the input schema.

### Step 2: Add the Pydantic action model

In `catia_agent/models.py`:

```python
class ListOpenDocumentsArguments(InternalModel):
    pass


class ListOpenDocumentsAction(InternalModel):
    action: Literal["list_open_documents"]
    arguments: ListOpenDocumentsArguments
```

Add the new action to the `CatiaAction` union.

### Step 3: Extend the approved-tool set and dispatcher

In `catia_agent/planner.py`:

- Add `"list_open_documents"` to `APPROVED_CATIA_TOOL_NAMES`.
- Add a branch in `create_typed_action` that validates and returns
  `ListOpenDocumentsAction`.
- Add the new action type to the `isinstance(...)` check at the bottom
  of `plan_catia_action` so it produces `ActionRequiredResult`.

MCP discovery does not automatically approve a tool for execution.

If the new tool is a **pure read** — it answers a question with a
discrete data payload and does not naturally chain into a follow-up
CATIA action — also add its name to `TERMINAL_READ_ONLY_TOOLS` in
`catia_agent/graph_state.py`. Read tools currently in that set:
`get_active_document`. Candidates as they land: `list_open_documents`,
`get_document_info`, `get_component_position`, `get_component_cog`.
Write or mutating tools (`open_cad_document`, `close_cad_document`,
`create_cad_document`, and any future modifying operation) must **not**
be added to that set — they legitimately chain in compound messages and
need the follow-up planner call to keep working.

### Step 4: Add deterministic policy checks

For tools with arguments, validate at least:

- Required identifiers
- Allowed units
- Numeric ranges
- Absolute paths
- Approved directories
- Supported document types
- Risk classification

### Step 5: Add completion-message handling

In `_build_action_success_message` inside `catia_agent/graph.py`,
translate the external result into a concise user-facing sentence.

### Step 6: Restart MCP and refresh tools

```http
POST /api/v1/tools/cache/clear
GET  /api/v1/tools?refresh=true
```

### Step 7: Test

Add unit tests under `tests/` covering:

- Supported natural-language variants
- Unsupported variants
- Missing arguments
- Invalid arguments
- Successful C# result
- Failed C# result
- Mismatched action ID

### Step 8: Implement the C# executor side

The C# steps are documented in the companion .NET guide.

## 16. Recommended tool expansion order

Start with read-only tools:

```text
list_open_documents
get_document_info
get_product_tree
find_component
get_component_position
get_component_cog
```

Then add reversible session operations:

```text
activate_document
hide_component
show_component
```

Add modifying and persistent operations only after approvals are
implemented:

```text
move_component
change_parameter
replace_component
update_document
save_document
save_document_as
close_without_saving
```

### When to split into a supervisor and specialized sub-agents

Do not confuse this with the sequential multi-action loop in Section 8.2.
That loop still uses one planner, one system prompt, and one tool list;
it just calls the planner more than once for a single compound message.
A supervisor and sub-agent split is a different thing: multiple
*distinct* planners, each with its own system prompt and its own subset
of tools.

The current design intentionally uses a single planner: one system
prompt and one tool list, with all tool-specific behavior living in each
MCP tool's own docstring rather than the shared prompt (see
`catia_mcp/server.py`). This keeps the shared prompt's size independent
of tool count and resolves chat-vs-agentic routing plus tool selection in
a single model call.

Do not split this into a supervisor plus specialized sub-agents just
because the tool count grows. At the current tool count, every tool
belongs to one domain (CATIA document lifecycle), so a supervisor would
have nothing meaningful to route between, and it would add a second
sequential model call to every agentic message for no behavioral
benefit.

The real trigger is the first modifying or persistent operation above
(`move_component`, `change_parameter`, `save_document`, and similar).
Those need a fundamentally different policy than today's read and
lifecycle tools: an approval or confirmation gate, preview, and audit
logging. That is the point to introduce a second planner (for example,
`modifying_operations_planner`) with its own system prompt and an
interrupt-based approval step, behind a thin supervisor that routes based
on risk category. Split then because of differing safety requirements,
not because of prompt length or tool count alone.

## 17. Production considerations

The Python backend already ships with several production-oriented
guardrails:

- Environment-policy enforcement in `Settings.validate_environment_policy`
  (docs disabled, technical detail hidden, admin key required for the
  cache-clear endpoint when enabled).
- Stable error contract with correlation IDs on every response.
- Per-request timeouts, body-size limits, identifier validation, and a
  provider-neutral model-client abstraction.
- Structured JSON logging with automatic redaction of sensitive field
  names.
- Deterministic validation layers between the model and the C# executor.

The following items must still be addressed before broad production
rollout:

- **Persistent LangGraph checkpoint storage.** The current
  `InMemorySaver` loses all pending runs, conversation history, and the
  `runId -> sessionId` map on restart. This is the largest remaining gap.
- **Persistent `runId -> sessionId` mapping** consistent with the chosen
  checkpoint store.
- **Authentication between .NET and FastAPI** on the Agent endpoints
  (the admin key today only protects the cache-clear endpoint).
- **Authentication or a controlled local transport for MCP.**
- **Tool-level authorization** for modifying operations.
- **Approved-directory allowlists** enforced in Python in addition to C#.
- **Human approval for modifying or persistent actions** (see Section 16).
- **Rate limits and concurrency limits** at the ingress layer.
- **Audit retention** for `completedActions` and terminal responses.
- **A model and tool evaluation dataset** exercised on every model or
  prompt change.
- **Versioned API contracts** (the current prefix is `/api/v1`; add
  `/api/v2` when a breaking change is required rather than mutating v1).
- **Recovery-after-restart procedures** aligned with the persistent
  checkpoint store.
- **Installer and service lifecycle management** for MCP, FastAPI, and
  the chosen model provider.
- **Corporate data and model-governance sign-off**, especially before
  switching to a cloud-hosted model.

## 18. Design rules

1. The model selects tools but never executes CATIA.
2. MCP advertises tools but does not automatically authorize execution.
3. Python validates every selected action.
4. C# independently validates and allowlists every action.
5. CATIA COM logic remains deterministic and testable.
6. The model chooses chat vs agentic routing by calling exactly one
   orchestrator or executor tool; the client never picks a mode.
7. The model must not generate Python, C#, VBA, or PowerShell for
   immediate execution.
8. New tools are disabled until all Python and C# integration steps are
   complete.
9. The planner is coupled to `ToolCallingModelClient`, not to any
   specific provider. Provider changes must not leak into the graph, the
   MCP layer, or the API contracts.
