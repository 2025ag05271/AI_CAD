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
|   |-- logging_config.py
|   |-- mcp_tools.py
|   |-- models.py
|   |-- planner.py
|   |-- readiness.py
|   |-- tool_registry_legacy.py
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
|   |-- test_models.py
|   |-- test_planner.py
|   |-- test_readiness.py
```

Automated tests live under `tests/` and are run with `pytest` (configured
by `pytest.ini`). Tests marked `integration` require a local Ollama and the
CATIA MCP server; the rest run without them. `show_graph.py` remains useful
for visualizing the compiled LangGraph, and Swagger and Postman are still
the recommended way to exercise the API end-to-end.

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
CATIA_AGENT_MAX_CONVERSATION_HISTORY_MESSAGES=20
CATIA_AGENT_MAX_ACTIONS_PER_RUN=20

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

Example chat response, for a message such as "what file types can you open?":

```json
{
  "status": "non_agentic",
  "sessionId": "session-001",
  "runId": "generated-run-id",
  "message": "This request does not require a CATIA action. Route it to the general chat service."
}
```

On `non_agentic`, the Python backend has not answered the user's message and
never will. The `.message` field is a routing note for logs, not an answer.
The caller is responsible for sending the original message to its own
general-purpose chat model (for example, GPT-4) and returning that answer to
the user instead.

Example response after a multi-action request such as "open Gearbox.CATProduct
and then close it" completes both steps (see Section 8.2):

```json
{
  "status": "completed",
  "sessionId": "session-001",
  "runId": "generated-run-id",
  "actionId": "generated-action-id-2",
  "message": "Gearbox.CATProduct was opened successfully. Gearbox.CATProduct was closed successfully.",
  "data": {
    "closedDocuments": ["Gearbox.CATProduct"]
  },
  "completedActions": [
    {
      "action": "open_cad_document",
      "actionId": "generated-action-id-1",
      "message": "Gearbox.CATProduct was opened successfully.",
      "data": {"documentName": "Gearbox.CATProduct"}
    },
    {
      "action": "close_cad_document",
      "actionId": "generated-action-id-2",
      "message": "Gearbox.CATProduct was closed successfully.",
      "data": {"closedDocuments": ["Gearbox.CATProduct"]}
    }
  ]
}
```

`completedActions` appears on any terminal response (`completed`,
`execution_failed`, `non_agentic`, `clarification_required`,
`unsupported_request`) once at least one action in the sequence has already
succeeded. It is omitted entirely when the sequence never got past the
first step.

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

### 7.5 Error contract

Every non-2xx response from `POST /api/agent/runs` and
`POST /api/agent/runs/{runId}/action-results` uses the same envelope:

```json
{
  "detail": {
    "status": "<short_status>",
    "errorCode": "<STABLE_CODE>",
    "message": "<human-readable>"
  }
}
```

Stable error codes and the endpoints that produce them:

| HTTP | errorCode                       | Where                | Meaning                                                                                                    |
|------|---------------------------------|----------------------|------------------------------------------------------------------------------------------------------------|
| 400  | `ACTION_VALIDATION_FAILED`      | `/runs`              | Deterministic Python policy rejected the model-selected action (path, extension, argument shape, and so on). |
| 409  | `AGENT_RUN_STATE_CONFLICT`      | `/runs`, action-results | The session or run is not in a state compatible with the request (see Section 8, run-state guards).       |
| 404  | `AGENT_RUN_NOT_FOUND`           | action-results       | No pending run matches the supplied `runId`.                                                               |
| 422  | `ACTION_RESULT_VALIDATION_FAILED` | action-results     | The submitted `ActionExecutionResultRequest` failed Pydantic validation.                                    |
| 422  | `TOOL_SCHEMA_VALIDATION_FAILED` | `/runs`              | Qwen returned a tool call that did not match the required Pydantic action model.                            |
| 422  | `AGENT_RUN_RESUME_FAILED`       | action-results       | A planner or validation error raised during resume that is not a state conflict.                            |
| 503  | `MCP_TOOL_DISCOVERY_FAILED`     | both                 | The MCP server was unreachable or returned no usable tool catalog.                                          |
| 503  | `OLLAMA_REQUEST_FAILED`         | both                 | Ollama returned an error while planning.                                                                    |
| 503  | `OLLAMA_CONNECTION_FAILED`      | both                 | The Ollama process could not be reached at all.                                                             |

`AGENT_RUN_STATE_CONFLICT` is deliberately narrow — only three specific
run-state guards raise it (see Section 8). Planner and executor errors
during resume surface as `AGENT_RUN_RESUME_FAILED` instead, so the .NET
client can distinguish "the run is in the wrong state" from "the planner
crashed" and route them differently in the UI.

## 8. LangGraph workflow

The current graph follows this state machine:

```text
START
  |
  v
plan_action
  |
  +--> non_agentic, clarification_required,
  |    unsupported (first action), or invalid
  |       |
  |       v
  |   prepare_terminal_response
  |       |
  |       v
  |      END
  |
  +--> unsupported (mid-sequence, nothing left to do)
  |       |
  |       v
  |   prepare_sequence_completed_response
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
      prepare_completion (records the result)
          |
          +--> failed --------------------> END
          |
          +--> succeeded, loop back ---> plan_action
```

### 8.1 Chat vs agentic orchestration

The .NET client sends only the user message. It does not choose a chat or
agent mode. Deciding that is the Python orchestrator's job, and Qwen only
makes the routing decision. It never answers the user's message itself:

- If Qwen selects a CATIA tool, the request is agentic. It proceeds through
  `prepare_action` → `await_action_result` → `prepare_completion` exactly as
  before.
- If Qwen determines the request does not require a CATIA action (a
  greeting, general question, or anything unrelated to CATIA), it must call
  the `route_to_general_chat` meta-tool instead of answering. This returns
  `non_agentic` through `prepare_terminal_response`. The `.message` field is
  a routing note, not an answer, and the caller must route the original
  message to a separate general-purpose chat model (for example, GPT-4) and
  return that model's answer to the user.
- If the request clearly needs a CATIA action but no available tool
  supports it yet, Qwen calls no tool at all, and the fallback
  `unsupported_request` result is returned. This case stays internal and is
  not rerouted, because it is still CATIA-specific, just not implemented.

Routing through an explicit tool call (`route_to_general_chat`) rather than
parsing free-form text keeps the decision structured and reliable, matches
how every other decision in this codebase is made (typed tool calls, never
free text), and avoids spending tokens on an answer that would only be
discarded. See `catia_agent/planner.py` and `catia_mcp/server.py`.

### 8.2 Sequential multi-action requests

A single message can require more than one CATIA action, for example "open
Gearbox.CATProduct and Frame.CATProduct, then close XYZ.CATPart". Qwen is
not asked to enumerate every step up front. Instead, the planner is called
again after each action succeeds, with that action's outcome added to the
conversation context, so it can decide what (if anything) from the original
request still remains:

1. `plan_action` runs, Qwen proposes one action, it executes.
2. On success, `prepare_completion` records the result and loops back to
   `plan_action` instead of finishing.
3. `plan_action` runs again with the original message plus a note of what
   was just completed. If Qwen proposes another action, the loop repeats.
   If Qwen decides nothing remains, the run finalizes as `completed` with
   every step's message joined together.

Four things can end the sequence early:

- **A failure stops it immediately.** If any action fails, the run ends
  right there as `execution_failed`. Actions not yet attempted are never
  attempted. The response combines what succeeded so far with the failure.
- **`request_clarification` or `route_to_general_chat` can appear
  mid-sequence.** For example, "open Gearbox.CATProduct, then close a
  document" completes the open, then asks which document to close via
  `clarification_required`. Both stop the sequence the same way a failure
  does, but with their own status instead of `execution_failed`.
- **A duplicate-action guard stops repeats.** If the planner proposes an
  action whose name AND arguments exactly match one already completed in
  the same run, `plan_action` intercepts it before it dispatches, logs
  `planner_duplicate_action_detected`, and finalizes the run as
  `completed` with what has been done so far. This is the hard rail
  against Qwen 3B re-selecting the just-completed tool on the follow-up
  planning pass; it fires even if the model ignores the explicit
  "do not repeat" system message described below.
- **A safety cap (`CATIA_AGENT_MAX_ACTIONS_PER_RUN`, default 20) stops
  runaway sequences.** If a planning error or model quirk keeps proposing
  *different* actions indefinitely (bypassing the duplicate guard), the
  run stops with `unsupported_request` once the cap is reached, reporting
  everything completed up to that point.

Every follow-up planning call after a successful action is given a
dedicated system message listing the actions already completed in the
run with their exact arguments, plus an explicit instruction not to
repeat them. Small local models like Qwen 3B do not always obey this,
which is why the duplicate-action guard above is enforced deterministically
in the graph rather than left as a prompt hint. See `catia_agent/graph.py`
and `catia_agent/planner.py`.

When Qwen returns more than one tool call on the same planning pass
(occasional Qwen 3B behavior on the follow-up call), the planner logs
`qwen_multiple_tool_calls` and picks a real CATIA tool over the
`route_to_general_chat` and `request_clarification` meta-tools. This
keeps the run alive instead of failing the whole planning call, and
matches the intent of the sequence loop: finish the compound request
first, only route to general chat when nothing agentic remains.

This calls the planner multiple times (once per action) rather than asking
Qwen to return several tool calls in one response. Each call stays as
simple and reliable as a single-action request; nothing about
`plan_catia_action` itself changes to support this; the looping and
progress-tracking live entirely in `graph.py`. See Section 16 for why this
is not the same thing as splitting into a supervisor and specialized
sub-agents.

### State fields

Typical state contains:

```text
session_id
run_id
action_id
user_message
catia_context
messages
planner_result
pending_action
action_result
completed_actions
sequence_progress
sequence_capped
final_response
```

`messages` accumulates the conversation as `{"role", "content"}` pairs after
every completed or terminal turn. The planner receives a trimmed window of
this history (`CATIA_AGENT_MAX_CONVERSATION_HISTORY_MESSAGES`) so it can
resolve references such as "the first one" or "that document" against what
the Agent previously said.

`completed_actions` and `sequence_progress` exist only for the lifetime of
one run (see Section 8.2). `completed_actions` accumulates a structured
entry per successful action (`action`, `actionId`, `arguments`, `message`,
`data`) for the final `completedActions` response field and for the
duplicate-action guard in `plan_action`. `sequence_progress` holds one
short assistant-role turn per completed action so the planner sees
progress on its next call within the same run. Both are reset to empty at
the start of every new run; they are never carried over into
session-level `messages` history.

### Identifier semantics

- `sessionId` identifies the conversation or client session. It is now the
  LangGraph thread key, so conversation history persists across multiple
  Agent runs within the same session.
- `runId` identifies one Agent workflow within a session, which may involve
  more than one action (see Section 8.2).
- `actionId` identifies one externally executed CATIA action. A single
  `runId` can have several `actionId` values in sequence.

Because the thread is keyed by `sessionId` rather than `runId`, the
action-results endpoint (which only receives a `runId`) resolves the owning
session through an in-memory `runId -> sessionId` map in `graph.py`. Three
run-state guards are enforced through a dedicated `AgentRunStateConflictError`,
which the API surfaces as HTTP 409 with `errorCode: AGENT_RUN_STATE_CONFLICT`:

- Starting a new run on `POST /api/agent/runs` while the same session
  still has a pending action result.
- Submitting an action result whose `runId` does not match the currently
  pending run for that session.
- Submitting an action result to a run that is not paused at
  `await_action_result`.

Any other error surfaced from the resume path (planner validation, MCP
unavailability, Ollama connection failure, unknown-tool policy rejection)
is mapped to its own dedicated status code and error code so it cannot be
mistaken for a state conflict. See Section 7.5.

### Persistence limitation

The POC uses `InMemorySaver`. Conversation history, pending runs, and the
`runId -> sessionId` map are all lost when FastAPI restarts or Uvicorn
reloads. Before production use, replace the checkpointer with a persistent
store such as SQLite or an approved enterprise data store.

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

### Currently implemented tools

```text
open_cad_document       Opens a CATPart, CATProduct, or CATDrawing by absolute path.
get_active_document     Requests the active CATIA document from the executor.
close_cad_document      Closes the active, a named, or all open documents.
create_cad_document     Creates a new, unsaved CATPart, CATProduct, or
                        CATDrawing document. Takes no file path; saving is
                        a separate action.
request_clarification   Asks the user for a missing detail (for example, which
                        document to close). Handled entirely inside the Python
                        Agent workflow and is never sent to the C# executor.
route_to_general_chat   Signals that the message is not a CATIA action. Handled
                        entirely inside the Python Agent workflow. The caller
                        must reroute the original message to a separate
                        general-purpose chat model; Python does not answer it.
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
- Duplicate-action guard: an action whose name and arguments exactly
  match one already completed in the current run is intercepted before
  dispatch and finalizes the run (see Section 8.2)
- Parallel tool calls fall back to a single primary tool: a real CATIA
  action is preferred over the `route_to_general_chat` and
  `request_clarification` meta-tools (see Section 8.2)

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
qwen_no_matching_tool
qwen_multiple_tool_calls
planner_duplicate_action_detected
agent_run_planned
action_result_received
agent_run_resumed
agent_run_resume_failed
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

### When to split into a supervisor and specialized sub-agents

Do not confuse this with the sequential multi-action loop in Section 8.2.
That loop still uses one planner, one system prompt, and one tool list; it
just calls the planner more than once for a single compound message. A
supervisor and sub-agent split is a different thing: multiple *distinct*
planners, each with its own system prompt and its own subset of tools.

The current design intentionally uses a single planner: one system prompt
and one tool list, with all tool-specific behavior living in each MCP
tool's own docstring rather than the shared prompt (see `catia_mcp/server.py`).
This keeps the shared prompt's size independent of tool count and resolves
chat-vs-agentic routing plus tool selection in a single Ollama call.

Do not split this into a supervisor plus specialized sub-agents just
because the tool count grows. At the current tool count, every tool belongs
to one domain (CATIA document lifecycle), so a supervisor would have
nothing meaningful to route between, and it would add a second sequential
model call to every agentic message for no behavioral benefit.

The real trigger is the first modifying or persistent operation above
(`move_component`, `change_parameter`, `save_document`, and similar). Those
need a fundamentally different policy than today's read and lifecycle
tools: an approval or confirmation gate, preview, and audit logging. That
is the point to introduce a second planner (for example,
`modifying_operations_planner`) with its own system prompt and an
interrupt-based approval step, behind a thin supervisor that routes based
on risk category. Split then because of differing safety requirements, not
because of prompt length or tool count alone.

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
