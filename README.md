# CATIA Agentic AI Backend

## Python, FastAPI, LangGraph, Ollama, Qwen3, and MCP Architecture Guide

## 1. Purpose

This document explains the Python side of the CATIA Agentic AI proof of concept. The backend receives a natural-language CATIA command, discovers approved tools from an MCP server, asks a local Qwen3 model to select a tool, validates the proposed action, pauses the LangGraph workflow while an external C# executor performs the CATIA operation, and then resumes the workflow with the execution result.

The Python backend does not execute CATIA COM automation. CATIA operations remain in the .NET application.

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
    +--> Ollama API :11434 --> Qwen3 4B
    |                           |
    |                           +--> Native tool call
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

- **FastAPI** exposes the local REST API, OpenAPI schema, Swagger UI, health checks, readiness checks, and diagnostic endpoints.
- **LangGraph** manages state, routing, interruption, resumption, and completion of Agent runs.
- **Ollama** hosts the local Qwen model and exposes its local inference API.
- **Qwen3** interprets the user command and selects an available tool through native tool calling.
- **MCP server** is the authoritative catalog of approved CATIA tool names, descriptions, and argument schemas.
- **Pydantic** validates API payloads, model-selected actions, CATIA context, and external execution results.
- **The .NET client** remains the final executor and calls the CATIA COM API.

## 3. Local services and ports

```text
FastAPI Agent API    http://127.0.0.1:8000
CATIA MCP server     http://127.0.0.1:8001/mcp
Ollama API           http://127.0.0.1:11434
FastAPI Swagger      http://127.0.0.1:8000/docs
FastAPI OpenAPI      http://127.0.0.1:8000/openapi.json
```

All POC services are bound to loopback addresses. They are not intended to be exposed directly to the network.

## 4. Technology stack

- Python 3.11
- FastAPI
- Uvicorn
- LangGraph
- Pydantic 2
- Pydantic Settings
- FastMCP
- Ollama Python client
- Ollama for Windows
- Qwen3 4B

The initial Conda environment was created with:

```powershell
conda create -n catia-agent python=3.11 -y
conda activate catia-agent
```

Typical dependencies:

```powershell
python -m pip install ollama pydantic pydantic-settings
python -m pip install fastapi "uvicorn[standard]" langgraph
python -m pip install "fastmcp>=3.4,<4"
```

## 5. Suggested project structure

```text
catia-agent-poc/
|
|-- .env
|-- .env.example
|-- .gitignore
|-- app.py
|-- run_api.py
|
|-- catia_agent/
|   |-- __init__.py
|   |-- config.py
|   |-- graph.py
|   |-- logging_config.py
|   |-- mcp_tools.py
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
|   |-- test_health_api.py
|   |-- test_live_integration.py
|   |-- test_planner_validation.py
|
|-- test_mcp_call.py
|-- test_mcp_discovery.py
|-- test_planner.py
```

The older hard-coded tool registry should be removed or retained only as a temporary legacy reference after MCP discovery is stable.

## 6. Configuration

### `.env`

```dotenv
CATIA_AGENT_APP_NAME=CATIA Agent API
CATIA_AGENT_APP_VERSION=0.2.0
CATIA_AGENT_ENVIRONMENT=development

CATIA_AGENT_API_HOST=127.0.0.1
CATIA_AGENT_API_PORT=8000

CATIA_AGENT_OLLAMA_BASE_URL=http://127.0.0.1:11434
CATIA_AGENT_OLLAMA_MODEL=qwen3:4b

CATIA_AGENT_MCP_SERVER_URL=http://127.0.0.1:8001/mcp
CATIA_AGENT_MCP_TOOL_CACHE_TTL_SECONDS=60

CATIA_AGENT_LOG_LEVEL=INFO
CATIA_AGENT_ENABLE_JSON_LOGS=true
```

### Configuration principles

- Keep `.env` out of source control.
- Commit `.env.example` with safe defaults.
- Load settings once through `pydantic-settings`.
- Restart FastAPI after changing cached process-level settings.
- Do not hard-code model names or service URLs across modules.

## 7. Core API endpoints

### Liveness

```http
GET /health
```

Confirms that FastAPI is running. This should not perform expensive dependency checks.

### Readiness

```http
GET /ready
```

Verifies that:

- Ollama is reachable.
- `qwen3:4b` is installed.
- The CATIA MCP server is reachable.
- The MCP server exposes tools.

### Tool diagnostics

```http
GET /api/tools
GET /api/tools?refresh=true
POST /api/tools/cache/clear
```

These endpoints expose MCP-discovered tool metadata through Swagger and Postman. The cache-clear operation is a `POST`, not a browser `GET`.

### Start an Agent run

```http
POST /api/agent/runs
```

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

Example response:

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

### Submit an execution result

```http
POST /api/agent/runs/{runId}/action-results
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
  }
}
```

## 8. LangGraph workflow

The current graph follows this state machine:

```text
START
  |
  v
plan_action
  |
  +--> unsupported or invalid
  |       |
  |       v
  |   prepare_terminal_response
  |       |
  |       v
  |      END
  |
  +--> action_required
          |
          v
      prepare_action
          |
          v
      await_action_result
          |
          | interrupt
          v
      External C# execution
          |
          | resume with Command(resume=...)
          v
      prepare_completion
          |
          v
         END
```

### State fields

Typical state contains:

```text
session_id
run_id
action_id
user_message
catia_context
planner_result
pending_action
action_result
final_response
```

### Identifier semantics

- `sessionId` identifies the conversation or client session.
- `runId` identifies one Agent workflow.
- `actionId` identifies one externally executed CATIA action.

A future multi-action workflow can use one `runId` with multiple `actionId` values.

### Persistence limitation

The POC uses `InMemorySaver`. Pending runs are lost when FastAPI restarts or Uvicorn reloads. Before production use, replace it with a persistent checkpointer such as SQLite or an approved enterprise data store.

## 9. Native Qwen tool calling

Tool selection uses Ollama's native `tools` parameter. Qwen receives function definitions rather than being prompted to write arbitrary JSON.

Conceptual tool schema:

```json
{
  "type": "function",
  "function": {
    "name": "open_cad_document",
    "description": "Open an existing CATIA document using an absolute Windows path.",
    "parameters": {
      "type": "object",
      "required": ["filePath"],
      "properties": {
        "filePath": {
          "type": "string"
        }
      }
    }
  }
}
```

Qwen selects a tool, but Qwen never executes it. The application validates the tool call and returns it to the .NET executor.

## 10. MCP architecture

### MCP role

The MCP server is the authoritative source for:

- Tool names
- Tool descriptions
- JSON input schemas
- Tool-level argument validation
- Tool discovery

The MCP server does not call CATIA in the current Option 1 architecture. It prepares typed action data for the external WPF and C# executor.

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
Convert MCP schemas to Ollama function schemas
    |
    v
Qwen native tool selection
```

### Tool caching

The backend caches converted MCP tool definitions for a configurable TTL. An `asyncio.Lock` prevents several concurrent requests from refreshing the cache simultaneously.

The cache is only a performance optimization. The MCP server remains the source of truth.

## 11. CATIA runtime context

The .NET client may send a snapshot containing:

```text
isRunning
activeDocument
openDocuments
selectedObjects
```

The context helps Qwen interpret commands. It is not authoritative because CATIA state may change between collection and execution. Read operations such as `get_active_document` must still be executed by the .NET CATIA layer when the user asks for current information.

## 12. Validation layers

Every action passes through several independent checks:

```text
MCP schema
    |
    v
Qwen tool selection
    |
    v
Pydantic action model
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

This layered approach is intentional. A model-selected tool is not trusted merely because it has a valid name.

Examples of deterministic Python validation:

- Absolute Windows path required
- Approved CATIA extension required
- Exact typed action model required
- Unknown MCP tools rejected until explicitly integrated
- Returned `actionId` must match the pending action

## 13. Logging and observability

Structured JSON logs should include:

```text
requestId
sessionId
runId
actionId
selectedTool
status
durationMs
errorCode
```

Recommended events:

```text
application_started
http_request_started
mcp_tool_discovery_started
mcp_tool_discovery_completed
mcp_tool_cache_refreshed
qwen_tool_selection_started
qwen_tool_selected
agent_run_planned
action_result_received
agent_run_resumed
http_request_completed
```

Do not log full prompts, model reasoning, CAD contents, or sensitive file paths at normal information level.

FastAPI returns `X-Request-ID` so the .NET client and Python logs can correlate the same request.

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

Before enabling Agent mode, verify:

```text
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8000/ready
GET http://127.0.0.1:8000/api/tools
```

Use Postman for manual API testing. Keep Swagger current at `/docs` and OpenAPI current at `/openapi.json`.

## 15. Adding a new CATIA tool

Use the following sequence for every new tool.

### Step 1: Define the MCP tool

Add a typed function to `catia_mcp/server.py`:

```python
@mcp.tool
def list_open_documents() -> dict[str, object]:
    """Prepare a request to list all open CATIA documents."""

    return {
        "status": "action_prepared",
        "action": "list_open_documents",
        "arguments": {},
        "executor": "wpf_csharp",
        "executed": False,
    }
```

The function name becomes the tool name. The docstring becomes part of the description. Typed parameters generate the input schema.

### Step 2: Add the Pydantic action model

In `catia_agent/models.py`:

```python
class ListOpenDocumentsArguments(BaseModel):
    pass


class ListOpenDocumentsAction(BaseModel):
    action: Literal["list_open_documents"]
    arguments: ListOpenDocumentsArguments
```

Add the new action to the `CatiaAction` union.

### Step 3: Add explicit approval mapping

In `create_typed_action()` inside `planner.py`:

```python
if tool_name == "list_open_documents":
    return ListOpenDocumentsAction.model_validate(raw_action)
```

Do not automatically approve every tool discovered from MCP.

### Step 4: Add deterministic policy checks

For tools with arguments, validate:

- Required identifiers
- Allowed units
- Numeric ranges
- Absolute paths
- Approved directories
- Supported document types
- Risk classification

### Step 5: Add completion-message handling

In the LangGraph completion node, translate the external result into a concise user-facing response.

### Step 6: Restart MCP and refresh tools

```http
POST /api/tools/cache/clear
```

Then verify:

```http
GET /api/tools?refresh=true
```

### Step 7: Test in Postman

Test:

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

Add modifying and persistent operations only after approvals are implemented:

```text
move_component
change_parameter
replace_component
update_document
save_document
save_document_as
close_without_saving
```

## 17. Production considerations

Before production use, address:

- Persistent LangGraph checkpoint storage
- Authentication between .NET and FastAPI
- Authentication or controlled local transport for MCP
- Tool-level authorization
- Approved-directory allowlists
- Human approval for modifying or persistent actions
- Request-size limits
- Rate limits and concurrency limits
- Audit retention
- Model and tool evaluation dataset
- Versioned API contracts
- Recovery after application restart
- Installer and service lifecycle management
- Corporate data and model-governance requirements

## 18. Design rules

1. Qwen selects tools but never executes CATIA.
2. MCP advertises tools but does not automatically authorize C# execution.
3. Python validates every selected action.
4. C# independently validates and allowlists every action.
5. CATIA COM logic remains deterministic and testable.
6. Chat mode and Agent mode remain explicitly selected by the user.
7. The model must not generate Python, C#, VBA, or PowerShell for immediate execution.
8. New tools are disabled until all Python and C# integration steps are complete.
