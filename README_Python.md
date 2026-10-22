# CATIA Agent Python Worker

## Python, LangGraph, JSON IPC, and Model-Provider Architecture Guide

## 1. Purpose

The current deployment packages the Python agent as a Windows executable
launched by the WPF/.NET client. The long-running worker exchanges
versioned JSON Lines over redirected standard input and output. It uses
LangGraph to route each message, selects from an in-memory catalog with
one enabled CATIA action (`open_master_drawing`), validates the action,
and pauses while .NET performs the CATIA operation. .NET then returns the
result as JSON and the graph resumes.

Python does not execute CATIA COM automation. The .NET action dispatcher
remains the only CATIA executor.

The tool-calling model is Qwen 3.6 27B hosted on Azure AI Foundry,
reached over an OpenAI-compatible chat-completions endpoint with
bearer-token authentication. It sits behind a provider-neutral
`ToolCallingModelClient` protocol (see `catia_agent/model_providers.py`),
so a future provider change does not touch the graph, tool catalog,
worker protocol, or validation policy. The exact qllama BGE-small GGUF is
loaded in-process through `llama-cpp-python` and bundled into the worker
executable. The Ollama service is not required. See Sections 4 and 8.1.

## 2. High-level architecture

```text
WPF / .NET client
  |
  | launch persistent worker; JSON Lines over redirected stdin/stdout
  v
CatiaAgentWorker.exe
  |
  +--> BGE-small GGUF loaded in-process
  +--> LangGraph planner --> Azure AI Foundry / Qwen 3.6 27B
  +--> In-memory tool catalog: open_master_drawing only
  +--> Pydantic and deterministic policy validation
  |
  v
action_required JSON response
  |
  | .NET dispatches action and executes CATIA COM
  v
action_result JSON request
  |
  v
LangGraph resumes --> terminal JSON response
```

### Responsibilities

- **The Python worker** owns one long-lived LangGraph process and a
  newline-delimited JSON protocol. It does not open a local HTTP listener.
- **LangGraph** manages state, routing, interruption, resumption, and
  completion of Agent runs.
- **The in-memory catalog** exposes only `open_master_drawing`. The
  catalog boundary is the extension point for a future MCP provider; MCP
  is not started or contacted in this deployment.
- **ToolCallingModelClient** is a provider-neutral interface, currently
  implemented only by `AzureFoundryToolCallingModelClient` (Qwen 3.6 27B
  on Azure AI Foundry).
- **The bundled GGUF model** classifies ask-vs-agent intent in-process;
  no Ollama service is required.
- **Pydantic** validates worker payloads, orchestrator and executor tool
  calls, CATIA context, and external execution results.
- **The .NET client** remains the final executor and calls the CATIA COM
  API.

## 3. Local services and ports

The worker opens no local TCP port. Its only local communication channel
is redirected standard input/output managed by the .NET parent process.
Azure AI Foundry remains a remote HTTPS dependency for tool selection.

The old FastAPI and MCP modules remain in the source tree for the legacy
prototype and tests, but the packaged worker does not import or launch
them. `run_api.py` now starts the JSON worker rather than Uvicorn.

## 4. Technology stack

- Python 3.11
- LangGraph
- Pydantic 2
- Pydantic Settings
- httpx (Azure AI Foundry HTTP client)
- truststore (uses the OS certificate store for TLS verification, required behind TLS-inspecting corporate proxies)
- Qwen 3.6 27B on Azure AI Foundry (tool-calling model)
- llama-cpp-python (in-process GGUF inference)
- NumPy (embedding classifier)
- PyInstaller (build-only; packages the worker and GGUF asset)

Environment setup used for development:

```powershell
conda create -n catia-agent python=3.11 -y
conda activate catia-agent
```

Typical dependencies:

```powershell
python -m pip install -r requirements-worker.txt
```

Build the executable and bundle the exact GGUF model from the local
Ollama model cache:

```powershell
.
\build_worker.ps1
```

The build verifies the model SHA-256 and includes it in
`dist/CatiaAgentWorker.exe`. Runtime does not require Ollama. For a
source run, `CATIA_AGENT_INTENT_CLASSIFIER_GGUF_PATH` can point to the
GGUF file.

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
|-- WORKER_PROTOCOL.md
|-- worker.py
|-- run_api.py                 # compatibility launcher for worker.py
|-- build_worker.ps1
|-- catia_agent_worker.spec
|-- scripts/prepare_worker_model.py
|-- scripts/convert_gpm_catalog.py
|-- data/gpm_master_drawings.v1.json
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
|   |-- tool_catalog.py        # in-memory open_master_drawing tool only
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
by `pytest.ini`). Unit tests do not require local services. Live planner
tests require Azure AI Foundry. The packaged worker does not require
FastAPI, MCP, or Ollama. `show_graph.py` remains useful for visualizing
the compiled LangGraph.

The current in-memory catalog exposes only `open_master_drawing`. MCP is
not queried or started by the worker; the retained API/MCP modules are
legacy prototype code and are not included in the executable.

Title-only requests use the versioned GPM JSON catalog. It contains only
the component family, PLM standard title, keywords, hyperlink label,
resolved drawing number, and approved drawing/OGD URL. Contact and
unrelated spreadsheet columns are excluded. The converter expands
Excel-merged cells, deduplicates rows, and filters out specifications
and other non-drawing links.

## 6. Configuration

### Environment variables

All settings use the `CATIA_AGENT_` prefix and are loaded once by
`pydantic-settings` (see `catia_agent/config.py`). A local `.env` file is
supported for development.

The worker uses the Azure provider, GGUF classifier, logging, and graph
settings shown below. FastAPI/MCP endpoint and admin settings belong only
to the explicitly selected `legacy_api` runtime and are not required by
the packaged worker.

Representative `.env` (see `.env.example` for the full list):

```dotenv
CATIA_AGENT_APP_NAME=CATIA Agent Worker
CATIA_AGENT_APP_VERSION=1.0.0
CATIA_AGENT_ENVIRONMENT=development
CATIA_AGENT_RUNTIME_MODE=worker

CATIA_AGENT_MODEL_TEMPERATURE=0

CATIA_AGENT_AZURE_FOUNDRY_ENDPOINT=
CATIA_AGENT_AZURE_FOUNDRY_API_KEY=
CATIA_AGENT_AZURE_FOUNDRY_DEPLOYMENT_NAME=qwen3.6-27b-instruct
CATIA_AGENT_AZURE_FOUNDRY_API_VERSION=2024-05-01-preview
CATIA_AGENT_AZURE_FOUNDRY_REQUEST_TIMEOUT_SECONDS=45
CATIA_AGENT_AZURE_FOUNDRY_MAX_TOKENS=2048
CATIA_AGENT_AZURE_FOUNDRY_ENABLE_THINKING=false

CATIA_AGENT_ENABLE_EMBEDDING_INTENT_CLASSIFIER=true
CATIA_AGENT_INTENT_CLASSIFIER_MODEL=qllama/bge-small-en-v1.5
CATIA_AGENT_INTENT_CLASSIFIER_GGUF_PATH=
CATIA_AGENT_INTENT_CLASSIFIER_TIMEOUT_SECONDS=5
CATIA_AGENT_INTENT_CLASSIFIER_FAILURE_COOLDOWN_SECONDS=30
CATIA_AGENT_INTENT_CLASSIFIER_TOP_K=3
CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_THRESHOLD=0.48
CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_MARGIN=0.03

CATIA_AGENT_MAX_CONVERSATION_HISTORY_MESSAGES=20
CATIA_AGENT_ENABLE_CONVERSATION_HISTORY=false
CATIA_AGENT_MAX_ACTIONS_PER_RUN=20
CATIA_AGENT_ENABLE_MULTI_ACTION_SEQUENCES=false
CATIA_AGENT_MAX_COMPLETED_ACTION_RESULT_BYTES=262144
CATIA_AGENT_PENDING_RUN_TIMEOUT_SECONDS=3600

CATIA_AGENT_LOG_LEVEL=INFO
CATIA_AGENT_ENABLE_JSON_LOGS=true
CATIA_AGENT_INCLUDE_TECHNICAL_DETAILS_IN_ERRORS=false

```

### Configuration principles

- Keep `.env` out of source control.
- Commit `.env.example` with safe defaults.
- Settings are cached for the life of the Python process. Restart the
  application after changing environment variables.
- Do not hard-code model names or service URLs across modules.
- `CATIA_AGENT_AZURE_FOUNDRY_ENDPOINT`, `CATIA_AGENT_AZURE_FOUNDRY_API_KEY`,
  and `CATIA_AGENT_AZURE_FOUNDRY_DEPLOYMENT_NAME` are required;
  `Settings.validate_model_provider_configuration` fails fast at startup
  if any of them is empty.
- `CATIA_AGENT_AZURE_FOUNDRY_API_KEY` is a bearer token, stored as a
  Pydantic `SecretStr` and included in the logging redaction list; never
  print or log it directly.
- `CATIA_AGENT_ENABLE_CONVERSATION_HISTORY` (default `false`) and
  `CATIA_AGENT_ENABLE_MULTI_ACTION_SEQUENCES` (default `false`) are
  independent switches described in Section 8.2.

### Worker timeouts

The worker has no HTTP request timeout. It processes one JSON message at
a time and remains alive while .NET executes CATIA. The relevant budgets
are:

| Setting | Default | Bounds |
| --- | --- | --- |
| `INTENT_CLASSIFIER_TIMEOUT_SECONDS` | 5 | Local embedding call |
| `AZURE_FOUNDRY_REQUEST_TIMEOUT_SECONDS` | 45 | One model call |
| `PENDING_RUN_TIMEOUT_SECONDS` | 3600 | Time a run may wait for .NET to report a CATIA result |

The worker warms the local GGUF model before it emits `ready`. Raise
`PENDING_RUN_TIMEOUT_SECONDS` if CATIA operations can take longer than
an hour. The JSON protocol and process lifecycle are documented in
[WORKER_PROTOCOL.md](WORKER_PROTOCOL.md).

### Environment policy

`Settings.validate_environment_policy` enforces production-safe defaults
for the retained `legacy_api` runtime. The current `worker` runtime does
not host API docs or MCP endpoints and skips those server-only checks.
When `CATIA_AGENT_RUNTIME_MODE=legacy_api` and
`CATIA_AGENT_ENVIRONMENT=production`:

- `CATIA_AGENT_ENABLE_API_DOCS` must be `false`.
- `CATIA_AGENT_INCLUDE_TECHNICAL_DETAILS_IN_ERRORS` must be `false`
  (technical exception messages are never returned to clients).
- If `CATIA_AGENT_ENABLE_TOOL_CACHE_CLEAR_ENDPOINT` is `true`,
  `CATIA_AGENT_ADMIN_API_KEY` must be set.

The application fails to start if any of these are violated.

### Administrative authorization

The legacy `POST /api/v1/tools/cache/clear` endpoint is protected by an
`X-Admin-API-Key` header when `CATIA_AGENT_ADMIN_API_KEY` is configured.
The check uses a constant-time comparison. When the key is empty (local
development only), the endpoint is unauthenticated so Postman and Swagger
can exercise it. In production the key is mandatory (see above).

## 7. Legacy FastAPI HTTP contract (not used by the worker)

The following API material documents the retained prototype and its
tests. The WPF client must use the JSON worker protocol instead; it must
not call these HTTP endpoints.

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

- The Azure AI Foundry endpoint accepts an authenticated one-token
  chat-completion request (`AzureFoundryReadinessCheck`).
- The CATIA MCP server is reachable and exposes at least one usable tool.

The local Ollama embedding service is deliberately not a readiness
dependency: the classifier degrades to model-based routing when it is
unavailable.

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
| 404  | `AGENT_RUN_NOT_FOUND`            | action-results                   | No pending run matches the supplied`runId`, or it expired after `CATIA_AGENT_PENDING_RUN_TIMEOUT_SECONDS`. |
| 409  | `AGENT_RUN_STATE_CONFLICT`       | `/agent/runs`, action-results  | The session or run is not in a state compatible with the request (see Section 8).        |
| 413  | `REQUEST_BODY_TOO_LARGE`         | request middleware               | Request body exceeds`CATIA_AGENT_REQUEST_BODY_MAX_BYTES`.                              |
| 422  | `REQUEST_VALIDATION_FAILED`      | FastAPI request validation       | The submitted payload did not match the API contract.                                    |
| 422  | `MODEL_SCHEMA_VALIDATION_FAILED` | planner and record-action-result | The model returned a tool call or the executor returned a result that failed Pydantic.   |
| 500  | `INTERNAL_SERVER_ERROR`          | request middleware               | An unhandled exception was caught. Logged with a stack trace, not exposed to the client. |
| 503  | `MCP_REQUEST_FAILED`             | any endpoint that touches MCP    | The CATIA MCP tool service was unreachable or returned an invalid catalog.               |
| 503  | `MODEL_PROVIDER_FAILED`          | planner                          | The Azure AI Foundry model endpoint failed (non-timeout).                                |
| 504  | `MODEL_REQUEST_TIMED_OUT`        | planner                          | The model did not respond within`CATIA_AGENT_AZURE_FOUNDRY_REQUEST_TIMEOUT_SECONDS`.   |
| 504  | `REQUEST_TIMED_OUT`              | request middleware               | The request did not complete within`CATIA_AGENT_REQUEST_TIMEOUT_SECONDS`.              |

`AGENT_RUN_STATE_CONFLICT` is deliberately narrow — only the four
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
  `catia_agent/intent_classifier.py`) embeds the user message with the
  bundled qllama BGE-small GGUF through `llama-cpp-python` and compares
  it against two curated sets
   of exemplar phrases — concrete CATIA action requests vs. general or
   informational messages — using cosine similarity. If the message
   scores confidently as a CATIA action request
   (`CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_THRESHOLD`, with at least
   `CATIA_AGENT_INTENT_CLASSIFIER_AGENTIC_MARGIN` separation over the
   general-chat score), the run proceeds to the model as normal. If it
   confidently scores as general/informational, `plan_action_node`
   returns a `non_agentic` result immediately and **the model is never
   called for that turn** — this is a full Azure Foundry chat
  round trip saved per non-agentic message. Embeddings run in-process;
  no local Ollama service is contacted.

   Long, unusual absolute file paths (real CATIA files can live many
   folders deep, for example under a synced OneDrive tree) dilute the
   embedding signal and can pull both intent scores toward a
   compressed, nearly-tied high band. `_strip_file_paths` in
   `catia_agent/intent_classifier.py` replaces any Windows or UNC
   path (quoted or unquoted) with a flat placeholder before embedding
   — applied identically to the exemplar phrases and to the incoming
   message — so classification is judged on the surrounding
   natural-language wording rather than on an arbitrarily long,
   path-specific token sequence. The exemplar set also includes a few
   longer, conversational phrasings ("it lives in this path ...",
   "it's located at ...") in addition to short imperative commands.
2. **Model tool selection (fallback and safety net).** If the
   embedding classifier is disabled
   (`CATIA_AGENT_ENABLE_EMBEDDING_INTENT_CLASSIFIER=false`), if it is
  uncertain (score below the threshold/margin), or if the local
  embedding call fails or exceeds
   `CATIA_AGENT_INTENT_CLASSIFIER_TIMEOUT_SECONDS` (default 5s), the
   run falls back to letting the model make the routing decision
   through a tool call. After a failure the classifier is skipped for
   `CATIA_AGENT_INTENT_CLASSIFIER_FAILURE_COOLDOWN_SECONDS` (default
  30s), so a local model failure doesn't add latency to every request.

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
`graph.py`. Four run-state guards raise
`AgentRunStateConflictError`, which the API surfaces as HTTP 409 with
`errorCode: AGENT_RUN_STATE_CONFLICT`:

- Starting a new run on `POST /api/v1/agent/runs` while the same session
  still has a pending action result (that has not yet expired).
- Sending a request for a session while another request for the same
  session is still being processed (for example, a double-click or a
  client retry). Requests are not queued; the second one fails fast.
- Submitting an action result whose `runId` does not match the currently
  pending run for that session.
- Submitting an action result to a run that is not paused at
  `await_action_result`, or whose `actionId` does not match the pending
  one.

Any other error surfaced from the resume path (planner validation, MCP
unavailability, model outage, unknown-tool policy rejection) maps to its
own dedicated status code and error code (see Section 7.5) so it cannot
be mistaken for a state conflict.

### Pending-run lifecycle

- A paused run records `action_requested_at`. After
  `CATIA_AGENT_PENDING_RUN_TIMEOUT_SECONDS` (default 3600) it is expired:
  a new `POST /agent/runs` on that session releases it and proceeds, and
  a late `POST .../action-results` for it returns 404
  `AGENT_RUN_NOT_FOUND`. Set this above your slowest CATIA operation.
- If a start request is cancelled after the graph paused (for example,
  by the request timeout), the client never received the `actionId`, so
  the pending action is released immediately instead of locking the
  session. Logged as `pending_run_abandoned`.
- Every new run clears leftover `pending_action`/`action_id` state from
  the previous run, so a failure mid-run cannot make the session look
  paused.
- The `runId -> sessionId` map only holds runs that are actually waiting
  for a result; terminal runs are never added or are removed on finish.
- Executor result `data` larger than
  `CATIA_AGENT_MAX_COMPLETED_ACTION_RESULT_BYTES` is rejected with 422
  `REQUEST_VALIDATION_FAILED`.

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

`AzureFoundryToolCallingModelClient` is the only implementation, for
Qwen 3.6 27B hosted on Azure AI Foundry. It POSTs an OpenAI-compatible
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
`active_model_name`, just not sent on the wire. Forcing
`tool_choice: "required"` structurally forces the model to return a
tool call on every well-formed request, which removes one whole failure
mode (`MODEL_RETURNED_NO_DECISION` on the very first planning pass)
that the small local model was prone to. HTTP failures raise
`AzureFoundryRequestError` (a `ModelProviderError` subclass, so no new
API error code is introduced) and timeouts raise
`ModelProviderTimeoutError`.

The payload also sets `chat_template_kwargs.enable_thinking` from
`CATIA_AGENT_AZURE_FOUNDRY_ENABLE_THINKING` (default `false`). The vLLM
deployment behind this model is a reasoning model: with thinking left
on, every response carries a very large `reasoning` field alongside
`tool_calls`, burning a large share of `max_tokens` on chain-of-thought
that is never read (the system prompt already forbids reasoning/free-form
output) and materially increasing latency and truncation risk. This
field, when present, is safely ignored — `select_tool` only reads
`message.tool_calls`, and `_extract_tool_call` already parses
`function.arguments` whether it arrives as a JSON object or a
JSON-encoded string (this vLLM deployment).

`build_model_client(settings)` constructs the client; the result is
exposed as the module-level `model_client`. A future provider only needs
to implement `select_tool` and be returned from that factory — nothing
else in the graph, MCP layer, or API contracts changes.

### 9.1 Prompt/prefix caching (Azure Foundry)

The `messages` and `tools` sent to the model are large and mostly
identical between calls (the system prompt and tool schemas do not
change turn to turn), so the request is deliberately structured to
maximize KV-cache reuse on the Azure Foundry vLLM backend:

- **Tool ordering is deterministic.** `plan_catia_action` sorts the
  combined MCP + orchestrator tool list by function name
  (`available_tools = sorted(...)` in `catia_agent/planner.py`)
  instead of concatenating them in catalog order. The MCP tool catalog
  is periodically refreshed (`CATIA_AGENT_MCP_TOOL_CACHE_TTL_SECONDS`)
  and nothing guarantees the remote server returns tools in the same
  order every time; sorting removes that as a source of prefix churn.
- **Static content comes first.** The system prompt text
  (`_build_system_prompt`/`_build_followup_system_prompt`) is a fixed
  string literal with no per-request interpolation. Dynamic content
  (CATIA context, completed-actions summary, conversation history,
  retry hints, the final user message) is appended after it, not
  interleaved with it, so the longest possible prefix of the rendered
  prompt stays byte-identical across calls.
- **No client-side cache opt-in exists for this backend.** Unlike
  Anthropic's explicit cache-breakpoint API, OpenAI-compatible
  vLLM-style prefix caching is transparent: the server reuses KV-cache
  state automatically whenever an incoming prompt's prefix matches a
  previous one, with no request field required. There is nothing to
  "enable" beyond keeping that prefix stable, which the two points
  above are for.
- **Observability.** `AzureFoundryToolCallingModelClient.select_tool`
  logs an `azure_foundry_usage` event with `promptTokens`,
  `completionTokens`, `totalTokens`, and `cachedTokens` (read from the
  OpenAI-shaped `usage.prompt_tokens_details.cached_tokens` field, when
  the backend returns it) after every call, so cache effectiveness can
  be verified from the logs rather than assumed. `cachedTokens` being
  consistently `null`/`0` across repeated same-prefix calls means the
  backend either does not have prefix caching enabled or does not
  expose this field, not that the client is doing anything wrong.

The model selects a tool. It never executes it. The application validates
the tool call and, for CATIA executor tools, returns it to the .NET
executor.

## 10. MCP architecture (disabled)

The current worker does not connect to or start an MCP server. Only
`open_master_drawing` is registered in the in-memory catalog at
`catia_agent/tool_catalog.py`. The MCP source is retained as a future
integration option; all other CATIA tool registrations are disabled and
`python -m catia_mcp.server` does not start a listener.

### Future MCP role

If MCP is reintroduced, it can provide the authoritative source for:

- Executor tool names
- Tool descriptions
- JSON input schemas
- Tool-level argument validation
- Tool discovery

The MCP server must not call CATIA. It should prepare typed action data
for the external WPF and C# executor.

### Current worker tool

```text
open_master_drawing     Prepare opening the master drawing of a part.
                        Accepts partNumber/revision or a partTitle resolved
                        from the bundled GPM catalog.
```

Ambiguous title matches produce `clarification_required` with a
structured `options` list. The worker retains option IDs for the
originating session; .NET can present them as clickable choices and send
the selected `choiceId` back. The worker resolves that one-time ID from
the trusted catalog and creates the action without another model call.

The remaining tool descriptions and MCP implementation notes in this
section document retired prototype capabilities and are not exposed by
the worker.

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

`open_master_drawing` opens the master drawing of a part. Accepted inputs:

- `partNumber` only, for example "open the master drawing for 674089800A"
- `partNumber` + `partRevision`, for example "open master drawing
  674089800A revision 01"

Both values are trimmed and upper-cased. They may contain letters,
digits, `.`, `_` and `-` (part number up to 50 characters, revision up to
20). This format check is deliberately loose; tighten
`MASTER_DRAWING_PART_NUMBER_RE` / `MASTER_DRAWING_REVISION_RE` in
`catia_agent/utils.py` once the numbering scheme is confirmed.

The planner enforces the input rules deterministically
(`_guard_master_drawing_arguments` in `catia_agent/planner.py`), so they
do not depend on the model following the tool description:

- A missing `partNumber` (for example, the user gave only a revision)
  returns `clarification_required` with `missingField: "partNumber"`.
- A `partNumber` that does not appear in anything the user wrote (in the
  current message or earlier user turns) is treated as invented, and the
  same clarification is returned.
- A `partRevision` the user never wrote is dropped, so the drawing opens
  without a revision rather than an invented one.

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

### MCP guardrails

The tool definitions MCP returns are text the model reads and trusts,
so they are treated as untrusted input. Every catalog refresh passes
through `apply_tool_guardrails` in `catia_agent/mcp_guardrails.py`
before it is cached or shown to the model. Each tool is checked as
follows, and a tool that fails a check is dropped and logged as
`mcp_tool_rejected` (`securityEvent: true`):

1. **Allowlist.** Only names in `APPROVED_CATIA_TOOL_NAMES` are kept.
   Unknown tools used to fail every request with a 500; now they are
   dropped.
2. **Content inspection (tool poisoning).** Rejects descriptions or
   schema text containing prompt-injection markers ("ignore previous
   instructions", `<system>`, "always call this tool", "do not tell the
   user", URLs) or hidden Unicode (zero-width, bidi override, tag
   characters). Size limits apply: `CATIA_AGENT_MCP_MAX_TOOL_COUNT`,
   `..._MAX_TOOL_DESCRIPTION_CHARACTERS`, `..._MAX_TOOL_SCHEMA_BYTES`
   and `..._MAX_TOOL_PROPERTIES`.
3. **Definition pinning (rug pull).** The SHA-256 of each tool's name,
   description and input schema must match
   `catia_agent/mcp_tool_manifest.json`.
   `CATIA_AGENT_MCP_TOOL_PINNING_MODE` controls what happens on a
   mismatch: `enforce` (default; mandatory in production) drops the
   tool, `warn` logs and keeps it, `off` skips the check. If the
   manifest is missing, `enforce` fails closed.

If no tool survives, catalog discovery fails with 503
`MCP_REQUEST_FAILED` rather than letting the model plan with an
unverified catalog.

Whenever a tool in `catia_mcp/server.py` is intentionally changed,
re-pin it and commit the manifest:

```powershell
python -m catia_agent.mcp_guardrails pin
```

The command lists tools in-process from the server source (no network)
and refuses to pin unsafe or unapproved tools.
`tests/test_mcp_guardrails.py` fails if the manifest is out of date.

**Authentication.** When `CATIA_AGENT_MCP_API_KEY` is set, the MCP
server requires it as a bearer token (`SharedSecretTokenVerifier`,
constant-time compare) and the agent sends it. Requests with a missing
or wrong token get 401, and a wrong token is logged as
`mcp_auth_rejected`. The key is required in production. Both processes
read the same `.env`, so set it once.

**Audit.** The MCP server logs every MCP request as
`mcp_request_completed` or `mcp_request_failed`, with `method` and
`durationMs`. FastMCP's host and origin protection is on by default for
the loopback binding.

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
- Unknown, poisoned, oversized or unpinned MCP tools are dropped before
  the model sees them (see "MCP guardrails" in Section 10).
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

Every worker log is written to standard error and, when
`CATIA_AGENT_ENABLE_FILE_LOGGING=true` (default), to a daily file in
`CATIA_AGENT_LOG_DIRECTORY` (default `C:\Temp\AI_Logs`, created if
missing): `CatiaAgentWorker_YYYYMMDD.log`. Files older than
`CATIA_AGENT_LOG_FILE_RETENTION_DAYS` are deleted. Tests disable file
logging (`tests/conftest.py`).

New logging code should use the shared helpers in
`catia_agent/logging_config.py` so the log shape can be changed in one
place:

```python
from catia_agent.logging_config import get_logger, log_event, log_duration, Stopwatch

logger = get_logger(__name__)

log_event(logger, "something_happened", tool="open_cad_document")

with log_duration(logger, "agent_run_start", sessionId=sid) as result:
    response = await start(...)
    result["status"] = response["status"]
# -> agent_run_start_completed {durationMs, status, ...}
#    or agent_run_start_failed {durationMs, exceptionType, ...}

stopwatch = Stopwatch(); ...; stopwatch.elapsed_ms
```

Timings logged today (`durationMs` unless noted):

| Event | Measures |
| --- | --- |
| `http_request_completed` (+ `http_request_slow` above `CATIA_AGENT_SLOW_REQUEST_THRESHOLD_MS`) | Whole HTTP request |
| `agent_run_start_completed` / `_failed` | `POST /agent/runs` end to end |
| `agent_run_resume_completed` / `_failed` | `POST .../action-results` end to end |
| `plan_action_node_completed` | One planning step, incl. duplicate retry |
| `model_tool_selected` | Azure model round trip as seen by the planner |
| `azure_foundry_usage` | Raw Azure HTTP call, with token counts |
| `intent_classified`, `intent_exemplars_embedded` | Embedding classifier |
| `mcp_tool_discovery_completed` | MCP catalog fetch |
| `catia_action_execution_measured` (`catiaExecutionMs`) | Time from handing an action to .NET until its result arrived, i.e. CATIA execution time |

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
pending_run_abandoned
intent_classifier_embedding_failed
```

Sensitive field names (`authorization`, `apiKey`, `adminApiKey`,
`accessToken`, `refreshToken`, `password`, `secret`) are redacted before
being serialized to log records. Do not log full prompts, model
reasoning, CAD contents, or absolute file paths at normal information
level.

## 14. Building and launching the worker

Build on Windows with Python 3.11 and the worker requirements installed:

```powershell
conda activate catia-agent
.\build_worker.ps1
```

The script copies the exact GGUF from the local Ollama model cache,
verifies its pinned SHA-256, and produces `dist/CatiaAgentWorker.exe`.
The executable does not start FastAPI, MCP, or Ollama. The WPF process
starts it with redirected standard input/output and follows
[WORKER_PROTOCOL.md](WORKER_PROTOCOL.md). `python worker.py` can be used
for source-level development with the same protocol.

To refresh the checked-in GPM snapshot from this Excel-exported HTML
workbook, keep the sibling `GPMData_files` directory beside the `.xls`
and run:

```powershell
python scripts/convert_gpm_catalog.py --source "C:\Data\GPMData.xls"
```

Review the generated JSON diff and record count before distributing the
updated catalog. The next executable build bundles that versioned file.

## 15. Adding a new CATIA tool

The current worker supports only `open_master_drawing`. The steps below
describe the future MCP-backed extension path; they do not enable a tool
in the worker by themselves. A worker tool must also be added to the
in-memory catalog, typed Python policy, .NET dispatcher, protocol tests,
and the .NET result contract.

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

In `catia_agent/mcp_guardrails.py`:

- Add `"list_open_documents"` to `APPROVED_CATIA_TOOL_NAMES`.

In `catia_agent/planner.py`:

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

### Step 6: Pin and refresh tools (future MCP provider only)

```powershell
python -m catia_agent.mcp_guardrails pin
```

Then restart the MCP server and refresh the catalog:

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

## 16. Future tool expansion roadmap (disabled)

None of the following capabilities are currently exposed by the worker.
The only enabled CATIA executor action is `open_master_drawing`.

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

The worker retains several production-oriented guardrails:

- Versioned JSON Lines messages with request IDs and a 1 MiB input limit.
- Typed Pydantic contracts, identifier validation, and a provider-neutral
  model-client abstraction.
- Structured JSON logging with automatic redaction of sensitive field
  names.
- One-tool in-memory catalog, deterministic Python validation, and the
  independent .NET dispatcher allowlist.

The following items must still be addressed before broad production
rollout:

- **Persistent LangGraph checkpoint storage.** The current
  `InMemorySaver` loses all pending runs, conversation history, and the
  `runId -> sessionId` map on restart. This is the largest remaining gap.
- **Persistent `runId -> sessionId` mapping** consistent with the chosen
  checkpoint store.
- **Worker process recovery.** Pending LangGraph state is in-memory and
  is lost if the worker exits; the .NET host must not replay an action
  whose completion is unknown.
- **Process launch security.** Restrict executable/configuration ACLs,
  protect Azure credentials in the host environment, and validate the
  worker binary before deployment.
- **Tool-level authorization** for modifying operations.
- **Approved-directory allowlists** enforced in Python in addition to C#.
- **Human approval for modifying or persistent actions** (see Section 16).
- **Rate limits and concurrency limits** in the WPF host/worker protocol.
- **Audit retention** for `completedActions` and terminal responses.
- **A model and tool evaluation dataset** exercised on every model or
  prompt change.
- **Protocol versioning and compatibility tests** before changing the
  JSON worker messages.
- **Recovery-after-restart procedures** aligned with persistent
  checkpointing if runs must survive worker restarts.
- **Installer and worker lifecycle management** for the executable and
  bundled model.
- **Corporate data and model-governance sign-off**, especially before
  switching to a cloud-hosted model.

## 18. Design rules

1. The model selects actions but never executes CATIA.
2. The in-memory catalog exposes only `open_master_drawing`.
3. Python validates every selected action.
4. C# independently validates and allowlists the action.
5. CATIA COM logic remains deterministic and testable.
6. The model chooses chat vs agentic routing; the client never picks a
  mode.
7. The model must not generate Python, C#, VBA, or PowerShell for
   immediate execution.
8. New tools are disabled until all Python and C# integration steps are
   complete.
9. The planner is coupled to `ToolCallingModelClient`, not to any
  specific provider. Provider changes must not leak into the graph,
  worker protocol, or CATIA executor contract.
