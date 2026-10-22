# CATIA Agent Client and Executor

## .NET Framework 4.8.1, WPF, JSON Worker Client, Tool Dispatcher, and CATIA COM Architecture Guide

## 1. Purpose

This document describes the .NET side of the CATIA Agent. The WPF
application starts a long-lived Python worker executable and exchanges
versioned JSON Lines over redirected standard input/output. It receives a
validated action, dispatches it to an explicitly registered C# handler,
performs the CATIA COM operation, and sends the structured result back to
the worker using the same JSON protocol.

The .NET layer remains the final authority for CATIA execution. Python
does not invoke CATIA COM. FastAPI and MCP are not started in the current
deployment.

The Python backend uses Qwen 3.6 27B hosted on Azure AI Foundry as its
tool-calling model, behind a provider-neutral abstraction, so a future
provider change requires no .NET change. The `status`, `errorCode`, and
payload contracts documented here do not depend on which model is in
use. See the Python guide for details.

## 2. High-level architecture

```text
User enters command
    |
    v
ChatViewModel.SendMessageAsync
    |
    v
ICatiaAgentCommandHandler.HandleAsync
    |
    +--> Start CatiaAgentWorker.exe
    +--> Collect CATIA context
    +--> Send start_run JSON
            |
            v
   Orchestrator decision (Python)
            |
    +-------+-------+
    |               |
    v               v
non_agentic   action_required
    |               |
    v               v
Existing      CatiaActionDispatcher
remote Chat          |
API (GPT-4)   +--> Tool enabled?
    |         +--> Tool registered?
    |         +--> Arguments valid?
    |               |
    |               v
    |       Typed CATIA handler
    |               |
    |               v
    |   Constructor-injected INFITF.Application
    |               |
    |               v
    |            CATIA COM
    |               |
    |               v
    |   ActionExecutionResultRequest
    |               |
    |               v
    |   Send action_result JSON
    |               |
    |               v
    |     completed or execution_failed
    |               |
    +-------+-------+
            |
            v
     AgentCommandResult
            |
            v
      ChatMessage in WPF
```

`clarification_required`, `unsupported_request`, `planning_error`, and
`invalid_request` skip the dispatcher and go straight to
`AgentCommandResult`, as do `non_agentic` and `completed`.

## 3. Responsibilities

### WPF ViewModel

- Reads the user message.
- Always calls `ICatiaAgentCommandHandler` for every message. It does not
  choose between Chat mode and Agent mode; the Python orchestrator decides
  that per message based on the request content.
- Displays the handler response for agentic statuses and uses Chat
  fallback for `non_agentic`.

### `CatiaAgentCommandHandler`

- Starts the worker, waits for its `ready` JSON message, and monitors its
  process lifetime.
- Collects CATIA context.
- Sends a `start_run` JSON message and reads the matching response.
- Reads the response `status` and routes accordingly:
  - `action_required` runs the existing dispatch loop.
  - `non_agentic` calls the existing Chat API with the original user
    message and returns its answer. Python has not answered this message
    and never will; the `message` field on a `non_agentic` response is a
    routing note, not an answer, and must not be shown to the user.
  - `clarification_required`, `unsupported_request`, `planning_error`,
    and `invalid_request` are returned to the user as-is using their
    `message` field. They are terminal and never require a follow-up call.
- Executes actions through `ICatiaActionExecutor`.
- Sends action results to the worker as JSON.
- Returns a final `AgentCommandResult`.

### `PythonAgentWorkerClient`

- Owns the worker process and its redirected standard streams.
- Writes one UTF-8 JSON object plus newline per request and reads one
  response line per request.
- Keeps standard output dedicated to protocol data and drains standard
  error for diagnostics.
- Correlates each response using `requestId` and tolerates unknown JSON
  properties for forward-compatible payloads.

### `CatiaActionDispatcher`

- Maintains the explicit C# action allowlist.
- Checks whether a tool is enabled.
- Deserializes arguments into typed DTOs.
- Invokes the matching handler.
- Converts exceptions into stable error codes.

### CATIA handlers

- Receive the existing `INFITF.Application` through constructors.
- Own deterministic CATIA COM logic.
- Return typed result DTOs.
- Do not call FastAPI, MCP, or any model provider.

## 4. Suggested project structure

```text
CatiaCopilotAgent/
|
|-- Worker/
|   |-- IPythonAgentWorkerClient.cs
|   |-- PythonAgentWorkerClient.cs
|   |-- PythonWorkerProtocol.cs
|
|-- Configuration/
|   |-- CatiaAgentWorkerOptions.cs
|
|-- Context/
|   |-- ICatiaContextProvider.cs
|   |-- CatiaContextProvider.cs
|
|-- Contracts/
|   |-- AgentRequests.cs
|   |-- AgentResponses.cs
|   |-- ActionExecutionContracts.cs
|   |-- CatiaContextContracts.cs
|
|-- DependencyInjection/
|   |-- ServiceCollectionExtensions.cs
|
|-- Execution/
|   |-- CatiaExecutionGate.cs
|   |-- CatiaForegroundHelper.cs
|   |-- CatiaToolExecutionException.cs
|   |-- CatiaToolOptions.cs
|   |-- Helpers/
|   |   |-- CatiaActionDispatcher.cs
|   |   |-- OpenMasterDrawingHandler.cs
|   |-- Interfaces/
|   |   |-- ICatiaActionExecutor.cs
|   |   |-- IOpenMasterDrawingHandler.cs
|
|-- Orchestration/
|   |-- AgentCommandResult.cs
|   |-- ICatiaAgentCommandHandler.cs
|   |-- CatiaAgentCommandHandler.cs
```

## 5. Application startup

`CatiaChatBot` is packaged as a WPF class library (`OutputType=Library`,
`TargetFrameworkVersion=v4.8.1`) that is loaded by a host CATIA add-in.
The composition root is `CatiaChatBotMain.ExecuteCoreAction`, which is
called by the host and receives the already-resolved CATIA
`INFITF.Application` instance as a parameter. There is no `Program.cs`
or `[STAThread] Main` in this project.

The project file targets classic MSBuild (non-SDK style) with:

```xml
<PropertyGroup>
  <OutputType>Library</OutputType>
  <TargetFrameworkVersion>v4.8.1</TargetFrameworkVersion>
  <LangVersion>latest</LangVersion>
</PropertyGroup>
```

WPF, nullable reference types, and modern C# features are enabled via
`LangVersion=latest` and the classic WPF references / XAML build items
already present in the project. `ImplicitUsings` and SDK-style
`<UseWPF>` are not available on .NET Framework 4.8.1.

### Startup sequence

```text
Host CATIA add-in (STA thread)
    |
    v
Call CatiaChatBotMain.ExecuteCoreAction(catiaApplication)
    |
    v
Create HostApplicationBuilder
    |
    v
Configure configuration sources (environment variables)
    |
    v
Configure Microsoft.Extensions.Logging (Debug + Console)
    |
    v
Register Agent services and CATIA handlers
    |
    v
Build and start IHost
    |
    v
Create System.Windows.Application
    |
    v
Resolve ChatWindow from DI
    |
    v
Run WPF message loop
    |
    v
Stop Host on shutdown
```

The CATIA `INFITF.Application` COM object is created by the host on its
original STA thread and handed to `ExecuteCoreAction`. Host startup and
shutdown use `GetAwaiter().GetResult()` so WPF and CATIA COM stay on
that same STA thread.

## 6. Configuration

### `appsettings.json`

```json
{
  "CatiaAgentWorker": {
    "ExecutablePath": "<application directory>\\CatiaAgentWorker.exe",
    "StartupTimeout": "00:00:30",
    "MaximumMessageBytes": 1048576
  },
  "CatiaAgentTools": {
    "OpenMasterDrawingEnabled": true
  },
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft": "Warning",
      "System": "Warning",
      "System.Diagnostics.Process": "Warning"
    }
  }
}
```

The worker executable is launched as a child process with redirected
standard input, output, and error. No BaseAddress, API prefix, or local
port is configured. The .NET host supplies Azure AI Foundry settings
through its environment; never put the bearer key in the executable or
source-controlled configuration.

### Loading configuration

`CatiaChatBotMain.ConfigureApplicationSettings` currently clears the
default sources and only registers environment variables:

```csharp
builder.Configuration.Sources.Clear();
builder.Configuration.AddEnvironmentVariables();
```

An `appsettings.json` file is not shipped or loaded today. Bind worker
options from the host configuration/environment using the standard
`CatiaAgentWorker__ExecutablePath` and
`CatiaAgentTools__OpenMasterDrawingEnabled` names. Resolve the executable
relative to the application/add-in directory, not the CATIA installation
directory.

### Options usage

- `CatiaAgentWorkerOptions` contains the executable path, startup timeout,
  and message-size limit.
- `CatiaToolOptions` contains enabled and disabled tool flags.
- `IOptions<T>` is sufficient for process-lifetime configuration.
- `IOptionsMonitor<T>` is preferred for reloadable tool settings.

`CatiaActionDispatcher` should inject:

```csharp
IOptionsMonitor<CatiaToolOptions>
```

and read:

```csharp
CatiaToolOptions toolOptions =
    _toolOptionsMonitor.CurrentValue;
```

Do not inject `CatiaToolOptions` directly unless the concrete value is
explicitly registered.

## 7. Dependency injection

Register the worker client once in `CatiaChatBotMain.ConfigureServices`:

```csharp
services.AddPythonAgentWorker(configuration);
```

The extension should register:

```text
CatiaAgentWorkerOptions
CatiaToolOptions
IPythonAgentWorkerClient
ICatiaActionExecutor
ICatiaAgentCommandHandler
Managed Python worker process
```

`CatiaChatBotMain.ConfigureServices` registers the application-specific
CATIA services:

```csharp
services.AddSingleton(catiaApplication);
services.AddSingleton<CatiaExecutionGate>();
services.AddSingleton<ICatiaContextProvider, CatiaContextProvider>();
services.AddSingleton<IOpenMasterDrawingHandler, OpenMasterDrawingHandler>();
services.AddSingleton<IPythonAgentWorkerClient, PythonAgentWorkerClient>();
services.AddSingleton<IChatService, ChatService>();
services.AddSingleton<ChatViewModel>();
services.AddSingleton<ChatWindow>();
```

`OpenCadDocumentHandler`, `CreateCadDocumentHandler`, and
`CloseCadDocumentHandler` all take the singleton `CatiaExecutionGate`
through their constructors so every CATIA-mutating call is serialized
against the same semaphore. Any new handler that mutates CATIA state
must follow the same pattern.

`ChatViewModel` consumes both `ICatiaAgentCommandHandler` and
`IChatService`. The ViewModel always calls `ICatiaAgentCommandHandler`
first, then falls back to `IChatService` only when the handler returns a
`non_agentic` outcome.

The existing CATIA `Application` object should be acquired once and
registered as the exact interop type requested by handler constructors.

## 8. WPF integration

`ChatWindow` should receive the ViewModel through constructor injection:

```csharp
public ChatWindow(ChatViewModel viewModel)
{
    InitializeComponent();

    _viewModel = viewModel;
    DataContext = _viewModel;
}
```

Do not manually construct:

```csharp
new ChatViewModel(new ChatService())
```

That bypasses dependency injection and prevents the ViewModel from
receiving the Agent command handler.

### ViewModel dependencies

`ChatViewModel` receives both dependencies. It always sends the user
message to the agent handler first and only calls chat fallback for
`non_agentic`:

```csharp
public ChatViewModel(
    IChatService chatService,
    ICatiaAgentCommandHandler agentCommandHandler)
{
    _chatService = chatService;
    _agentCommandHandler = agentCommandHandler;
}
```

### Unified call line

Every message, regardless of content, goes through the same call. There
is no mode toggle to check before calling it:

```csharp
AgentCommandResult result =
    await _agentCommandHandler.HandleAsync(
        messageText,
        _agentSessionId);

Messages.Add(
    new ChatMessage(
        result.Message,
        ChatMessageSender.Bot,
        DateTimeOffset.Now));
```

### Status-based routing (split between handler and ViewModel)

```csharp
AgentRunResponse response =
    await _apiClient.StartRunAsync(
        sessionId,
        message,
        catiaContext,
        cancellationToken);

return response.Status switch
{
    "action_required" =>
        await RunActionLoopAsync(
            response,
            cancellationToken),

    "non_agentic" =>
        new AgentCommandResult
        {
            Outcome = AgentCommandOutcome.NonAgentic,
            SessionId = response.SessionId,
            RunId = response.RunId,
            Message = response.Message
                ?? "This request does not require a CATIA action."
        },

    "planning_error" =>
        new AgentCommandResult
        {
            Outcome = AgentCommandOutcome.PlanningError,
            SessionId = response.SessionId,
            RunId = response.RunId,
            Message = response.Message
                ?? "The CATIA Agent could not plan this request.",
            ErrorCode = response.ErrorCode
        },

    _ =>
        new AgentCommandResult
        {
            Outcome = AgentCommandOutcome.Completed,
            SessionId = response.SessionId,
            RunId = response.RunId,
            Message = response.Message
                ?? "The CATIA Agent run completed.",
            CompletedActions = response.CompletedActions
        },
};
```

```csharp
AgentCommandResult result =
    await _agentCommandHandler.HandleAsync(
        messageText,
        _agentSessionId);

if (result.Outcome == AgentCommandOutcome.NonAgentic)
{
    ChatResponse chatResponse =
        await _chatService.GetResponseAsync(
            messageText,
            cancellationToken);

    Messages.Add(
        new ChatMessage(
            chatResponse.Answer,
            ChatMessageSender.Bot,
            DateTimeOffset.Now,
            chatResponse.Sources));
}
```

Routing is decided entirely by the Python orchestrator's response
`status`, not by any local mode flag. There is no `AssistantMode` enum,
toggle, or equivalent in this codebase, and none should be added: any
such flag would immediately drift out of sync with the orchestrator.
If the UI later needs to visually indicate which path a response took
(for example, a small "via CATIA Agent" vs "via Chat" badge), derive
that purely from the returned `AgentCommandOutcome`, not from a
separate mode field.

## 9. JSON worker interaction

The .NET client does not call FastAPI, MCP, or Ollama. It launches the
Python worker and uses the versioned JSON Lines contract in
[WORKER_PROTOCOL.md](WORKER_PROTOCOL.md).

Configure `ProcessStartInfo` with `UseShellExecute = false`, redirected
standard input/output/error, and `CreateNoWindow = true`. Read stdout
line-by-line as protocol data and drain stderr separately for logs. Wait
for the worker's `ready` message before accepting commands. The worker
warms the bundled BGE model before sending `ready`.

Set `CATIA_AGENT_RUNTIME_MODE=worker` in `ProcessStartInfo.Environment`.
Supply the Azure AI Foundry settings through that environment collection;
do not ship the bearer key in the executable or source-controlled files.

Send `start_run` with the user's message and CATIA context. When the
response status is `action_required`, execute the explicitly registered
`open_master_drawing` handler and send an `action_result` message with
the same `runId` and `actionId`. The worker then returns the terminal
Agent response. Keep the process alive for the WPF session so pending
LangGraph state survives between those messages.

The protocol carries `requestId` for correlation and returns a JSON
`error` message with `status`, `errorCode`, and `message` for transport
or request failures. Agent outcomes remain in the response payload and
use the existing status contract below.

When `clarification_required.options` is present, render each option's
`label` as a selectable WPF choice. Send only its `choiceId` with the
original `sessionId`; the worker resolves the one-time choice back to
the bundled catalog record. The option includes `partNumber`,
`plmStandardTitle`, `componentTitleFamily`, and `documentKind` for UI
context. Do not send a URL from the UI; the worker intentionally keeps
the hyperlink out of the option payload.

### Session identifier constraints

`sessionId` in a `start_run` payload must match `^[A-Za-z0-9._:-]+$`
and be no longer than 128 characters. A GUID such as
`Guid.NewGuid().ToString("N")` is safe. Do not include spaces, slashes,
or unicode letters.

### Response status contract

The worker's `agent_response` payload can return any of the following
`status` values. `AgentResponses.cs` must model all of them:

```text
action_required          A CATIA action is pending. Dispatch it, then
                         execute the handler, then send `action_result` JSON.
completed                The dispatched action (or the whole sequence)
                         finished successfully.
execution_failed         The dispatched action failed. errorCode and
                         errorMessage are set.
non_agentic              The message is not a CATIA action. Call the
                         existing Chat API with the original user
                         message and return its answer. Python has not
                         answered this message and never will.
                         Response also carries routeTarget and
                         routeReason for observability.
clarification_required   Python needs more information before it can
                         prepare an action. missingField identifies what
                         is missing. Display message to the user.
unsupported_request      The message is CATIA-related but no tool
                         implements it, or the per-run action cap was
                         hit (reason = action_limit_reached). Display
                         message to the user; requestedCapability and
                         reason are informational.
planning_error           The model returned no decision, multiple
                         decisions, an invalid tool, or invalid tool
                         arguments. This is a normal 200 response with
                         a stable internal errorCode. Display message
                         to the user; do not retry automatically.
invalid_request          The message was empty or malformed after
                         normalization.
```

Only `action_required` requires a follow-up call to the action-results
endpoint. Every other status is terminal for that turn.

`planning_error` carries an internal `errorCode` from this fixed set,
useful for diagnostics but not intended for user display:

```text
MODEL_RETURNED_NO_DECISION
MODEL_RETURNED_MULTIPLE_DECISIONS
MODEL_RETURNED_INVALID_TOOL
MODEL_RETURNED_INVALID_ARGUMENTS
```

### Multi-action runs

A single user message can require more than one CATIA action (for
example: "open Gearbox.CATProduct and then close it"). Python does not
ask the model to enumerate every step up front; it calls the planner
again after each successful action within the same `runId` when
follow-up planning is required. For high-confidence single-action
requests, Python can finalize immediately after the first successful
action. The current worker exposes only one CATIA action and has
multi-action sequences disabled by default, so .NET normally receives a
terminal response after its `action_result`. If sequencing is enabled in
a future release, each result message may return another
`action_required` with the same `runId` and a new `actionId`; the handler
should continue until a terminal status.

### `completedActions` on terminal responses

Every terminal response can carry a `completedActions` array capturing
the per-action audit trail of the run. Each entry has this exact
shape:

```json
{
  "action": "open_cad_document",
  "actionId": "9b3f...",
  "arguments": { "filePath": "C:\\TestData\\Gearbox.CATProduct" },
  "message": "Gearbox.CATProduct was opened successfully.",
  "data": { "documentName": "Gearbox.CATProduct" }
}
```

`arguments` is populated with the exact arguments Python dispatched to
the C# executor (Python uses it internally for a duplicate-action guard)
and is part of the response contract. `data` may be `null`.

Presence rules to model in `AgentResponses.cs`:

- `completed` and `execution_failed` always include `completedActions`.
  The array may be empty (for example, `execution_failed` on the first
  action).
- `non_agentic`, `clarification_required`, `unsupported_request`,
  `planning_error`, `invalid_request` include `completedActions` only
  when at least one action has already succeeded. Model it as nullable
  on those statuses.

`AgentRunResponse.CompletedActions` and
`AgentCommandResult.CompletedActions` model this shape. Consumers such
as the ViewModel can surface it when present and ignore it when absent.

### Error envelope

Every non-2xx response from the Agent and Tools endpoints uses this
envelope:

```json
{
  "detail": {
    "status": "<short_status>",
    "errorCode": "<STABLE_CODE>",
    "message": "<human-readable>",
    "technicalDetail": "<optional, dev/test only>",
    "validationErrors": [ ... optional Pydantic error list ... ]
  }
}
```

`technicalDetail` is only populated when the backend is running with
`CATIA_AGENT_INCLUDE_TECHNICAL_DETAILS_IN_ERRORS=true` (dev/test).
`validationErrors` is populated when the failure came from Pydantic. The
client must tolerate both fields being present or absent, and it should
log `technicalDetail` when present because it is otherwise not returned.

`CatiaAgentApiClient` extracts `message` and `errorCode` from this
envelope and exposes them through `CatiaAgentApiException.ErrorCode`.
`CatiaAgentCommandHandler` maps two error paths explicitly:

- HTTP 409 or `errorCode = AGENT_RUN_STATE_CONFLICT` →
  `AgentCommandOutcome.RunStateConflict` with a user-friendly "a
  previous action is still pending" message.
- Any other non-2xx → `AgentCommandOutcome.BackendUnavailable` with the
  extracted `errorCode` propagated for logging.

## 10. Action dispatch and security

The dispatcher must use an explicit switch:

```csharp
return action.Name switch
{
    "open_master_drawing" when toolOptions.OpenMasterDrawingEnabled =>
        await ExecuteOpenMasterDrawingAsync(...),

    "open_master_drawing" =>
        ActionExecutionResultRequest.Failed(
            actionId,
            "TOOL_DISABLED",
            "The open_master_drawing tool is disabled."),

    _ =>
        ActionExecutionResultRequest.Failed(
            actionId,
            "UNSUPPORTED_ACTION",
            $"The action '{action.Name}' is not registered.")
};
```

Never use reflection to map a model-generated tool name to a method.

The C# allowlist remains independent of Python. The current worker only
plans `open_master_drawing`; the dispatcher must reject every other
executor action until it is intentionally re-enabled end to end.

Orchestrator-only tool calls (`route_to_general_chat`,
`request_clarification`, `report_unsupported_catia_action`,
`complete_request`) never reach the dispatcher. Python resolves them
into the `non_agentic`, `clarification_required`, `unsupported_request`,
or terminal `completed` responses before returning to .NET.

## 11. CATIA context provider

`ICatiaContextProvider` returns a snapshot containing:

```text
IsRunning
ActiveDocument
OpenDocuments
SelectedObjects
```

The provider receives `INFITF.Application` through its constructor.

For new or unsaved CATIA documents, `FullName` may be unavailable.
Return `null` rather than failing the entire context operation.

The initial implementation may leave `SelectedObjects` empty. Selection
support can be added later as a separate read capability.

The Python `CatiaContext` model enforces:

- When `isRunning` is `false`, `activeDocument`, `openDocuments`, and
  `selectedObjects` must all be empty.
- When `activeDocument` is set and `openDocuments` is non-empty, the
  active document must appear in `openDocuments`.
- `openDocuments` is capped at 500 entries and `selectedObjects` at
  1000; keep the provider well under these caps.

Violating these produces a `REQUEST_VALIDATION_FAILED` worker error
before planning begins.

## 12. CATIA COM object ownership

The existing CATIA application instance is obtained at startup and
registered as a singleton:

```csharp
CatiaApplication catiaApplication =
    GetExistingCatiaApplication(targetGuid);

services.AddSingleton(catiaApplication);
```

Handlers receive it through constructors:

```csharp
public OpenCadDocumentHandler(
    INFITF.Application catiaApplication)
{
    _catiaApplication = catiaApplication;
}
```

Do not reacquire CATIA separately in every tool handler.

### COM threading

`CatiaExecutionGate` serializes operations but does not move work to an
STA thread. If CATIA calls may originate from arbitrary continuations
or worker threads, use an established STA dispatcher or marshal
execution to the CATIA-owned thread.

Keep cancellation tokens flowing through the orchestration, but
recognize that a COM call may not be cancellable after it begins.

### OS-level foreground handoff

Because `CatiaChatBot` runs inside the CATIA process, the WPF chat
window and the CATIA main frame share one Win32 message pump. After a
successful `Documents.Open` or `Documents.Add`, CATIA's internal state
is correct but its top-level frame never receives `WM_ACTIVATEAPP` —
the WPF window is still the OS foreground window, so CATIA does not
repaint its client area until the WPF window is closed or minimized.
`INFITF.Window.Activate()` only changes the active MDI child inside
CATIA; it does not change the OS foreground.

`CatiaForegroundHelper.TryBringCatiaToForeground()` closes that gap by
calling Win32 `SetForegroundWindow` on the `CNEXT` process's main
window handle (and `ShowWindow(..., SW_RESTORE)` first if it is
minimized). It is called at the end of `OpenCadDocumentHandler` and
`CreateCadDocumentHandler`, wrapped in `try/catch` so any failure
(process gone, shell policy blocking foreground steal, and so on) does
not fail the CATIA tool call.

This is intentionally not called from `CloseCadDocumentHandler`: after
a close the user is typically about to send another chat command, and
stealing focus back to CATIA would force them to click the chat window
again.

## 13. Legacy FastAPI error codes

The following HTTP error table is retained for the old prototype only.
The worker's current JSON `error` envelope is defined in
[WORKER_PROTOCOL.md](WORKER_PROTOCOL.md).

Stable machine-readable error codes emitted by the C# executor:

```text
CATIA_NOT_RUNNING
CATIA_BUSY
FILE_NOT_FOUND
ACCESS_DENIED
INVALID_FILE_PATH
RELATIVE_PATH_NOT_ALLOWED
UNSUPPORTED_DOCUMENT_TYPE
DOCUMENT_OPEN_FAILED
DOCUMENT_CREATE_FAILED
DOCUMENT_CLOSE_FAILED
CLOSE_ALL_PARTIAL_FAILURE
DOCUMENT_NAME_REQUIRED
DOCUMENT_NOT_OPEN
AMBIGUOUS_DOCUMENT_NAME
INVALID_CLOSE_TARGET
NO_ACTIVE_DOCUMENT
GET_ACTIVE_DOCUMENT_FAILED
INVALID_ACTION_ARGUMENTS
TOOL_DISABLED
UNSUPPORTED_ACTION
CATIA_COM_ERROR
CATIA_EXECUTION_FAILED
```

Codes surfaced from the FastAPI backend and preserved by
`CatiaAgentApiException.ErrorCode`:

| HTTP | errorCode                          | Meaning                                                                                                                 |
| ---- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 400  | `INVALID_CONTENT_LENGTH`         | Malformed`Content-Length` header on the request.                                                                      |
| 401  | `ADMIN_API_KEY_REQUIRED`         | An admin endpoint was called without`X-Admin-API-Key`.                                                                |
| 403  | `ADMIN_API_KEY_INVALID`          | The supplied admin key did not match.                                                                                   |
| 404  | `AGENT_RUN_NOT_FOUND`            | No pending run matches the supplied`runId`, or it expired after waiting longer than `CATIA_AGENT_PENDING_RUN_TIMEOUT_SECONDS`. |
| 409  | `AGENT_RUN_STATE_CONFLICT`       | Run/session state is incompatible with the request (start-while-pending, another request for the same session still in progress, mismatched runId or actionId, run not paused). |
| 413  | `REQUEST_BODY_TOO_LARGE`         | Request body exceeded the configured size limit.                                                                        |
| 422  | `REQUEST_VALIDATION_FAILED`      | The request payload failed FastAPI/Pydantic validation.                                                                 |
| 422  | `MODEL_SCHEMA_VALIDATION_FAILED` | An internal Pydantic model rejected a tool call or action result.                                                       |
| 500  | `INTERNAL_SERVER_ERROR`          | Unhandled exception. Logged with a stack trace, not exposed to the client.                                              |
| 503  | `MCP_REQUEST_FAILED`             | The CATIA MCP tool service is unreachable or returned an invalid catalog.                                               |
| 503  | `MODEL_PROVIDER_FAILED`          | The configured tool-calling model provider failed (non-timeout).                                                        |
| 504  | `MODEL_REQUEST_TIMED_OUT`        | The model did not respond within the configured timeout.                                                                |
| 504  | `REQUEST_TIMED_OUT`              | FastAPI's whole-request timeout elapsed before the handler completed.                                                   |

Only `AGENT_RUN_STATE_CONFLICT` deserves the dedicated
`RunStateConflict` outcome. Every other code above should be routed to
`BackendUnavailable` with the `errorCode` retained for logs.

The following codes were emitted by earlier backend builds and are no
longer produced. Any code path that special-cased them can be
simplified:

```text
AGENT_RUN_RESUME_FAILED          → now MODEL_PROVIDER_FAILED, MCP_REQUEST_FAILED, or MODEL_SCHEMA_VALIDATION_FAILED depending on cause
ACTION_VALIDATION_FAILED         → now planning_error (200) or MODEL_SCHEMA_VALIDATION_FAILED
ACTION_RESULT_VALIDATION_FAILED  → now REQUEST_VALIDATION_FAILED
TOOL_SCHEMA_VALIDATION_FAILED    → now MODEL_SCHEMA_VALIDATION_FAILED
MCP_TOOL_DISCOVERY_FAILED        → now MCP_REQUEST_FAILED
OLLAMA_CONNECTION_FAILED         → now MODEL_PROVIDER_FAILED or MODEL_REQUEST_TIMED_OUT
OLLAMA_REQUEST_FAILED            → removed; the backend no longer uses Ollama for the model
```

Do not return raw COM objects or exception instances to FastAPI.

Example failure payload:

```json
{
  "actionId": "action-001",
  "success": false,
  "data": null,
  "errorCode": "CATIA_NOT_RUNNING",
  "errorMessage": "CATIA is not currently running."
}
```

`errorCode` and `errorMessage` are required when `success` is `false`
and must be `null` when `success` is `true`. The Python side rejects
inconsistent payloads with `REQUEST_VALIDATION_FAILED`.

## 14. Logging

With the current Microsoft logging setup:

```csharp
logging.AddDebug();
logging.AddConsole();
```

`ILogger<T>` logs appear in:

```text
Visual Studio
→ View
→ Output
→ Show output from: Debug
```

Because `CatiaChatBot` runs in-process inside CATIA, `AddConsole()` is
only useful when a console is attached or output is captured by a
supervising host.

`AppFileLogger` is separate from `ILogger<T>` and writes plain-text
breadcrumbs to `C:\Temp\AI_Logs\CatiaChatBot_status.log`. It is used
as a low-friction diagnostic trail at every boundary that matters for
debugging the .NET ↔ FastAPI conversation:

- `ChatViewModel.SendMessageAsync` fires (one line per user press).
- `CatiaAgentCommandHandler` entry, readiness result, `StartRun`
  response, every loop iteration with `RunId`/`ActionNumber`/`Status`,
  and each executed action's `Success`/`ErrorCode`.
- `CatiaAgentApiClient` request start, response summary (method, path,
  status code, `X-Request-ID`, duration), deserialized
  `AgentRunResponse` summary, and truncated non-success body so the
  FastAPI `detail.errorCode` (and `detail.technicalDetail` when
  present) is visible.
- `CatiaActionDispatcher` entry, completion, and every caught
  exception branch.

To persist all injected `ILogger<T>` logs alongside these breadcrumbs,
add a file provider such as Serilog and eventually standardize on one
logging pipeline.

Recommended fields:

```text
RequestId
SessionId
RunId
ActionId
Action
DurationMs
Success
ErrorCode
```

Avoid logging complete CAD paths or sensitive user commands at normal
information level.

## 15. Legacy tool references

Only `open_master_drawing` is enabled in the current worker. The other
handler descriptions below are retained as historical references and
must not be registered or advertised until re-enabled end to end.

### `open_cad_document`

C# should validate:

- Path is present.
- Path is fully qualified.
- Extension is CATPart, CATProduct, or CATDrawing.
- File exists.
- Access is allowed.

Then call the existing CATIA COM logic and return:

```json
{
  "documentName": "Gearbox.CATProduct",
  "fullPath": "C:\\TestData\\Gearbox.CATProduct",
  "documentType": "CATProduct"
}
```

Python uses `data.documentName` to build the user-facing success message.

### `get_active_document`

Return the active document name, path, and type. If CATIA has no open
document, return a successful result with null document fields. Python's
completion node emits "CATIA does not currently have an active
document." for that case; if `documentName` is set it emits "The active
CATIA document is X."

### `close_cad_document`

C# supports the following close target modes:

- `active`
- `named`
- `all`

For `named`, `documentName` is required. The handler resolves by
document name and full path matching and returns the closed document
list.

The result payload must use the field name `closedDocuments` (camelCase,
plural) and it must be a JSON array of document name strings, even when
only one document was closed or when the array is empty. The Python
completion node reads this exact field to build the user-facing message
("Gearbox.CATProduct closed successfully." for a single entry). Any
other field name (`closed_documents`, `ClosedDocuments`, `documents`,
and so on) will silently fall through to a generic completion message.

```json
{
  "closedDocuments": ["Gearbox.CATProduct"]
}
```

### `create_cad_document`

C# should validate:

- `documentType` is one of `CATProduct`, `CATPart`, or `CATDrawing`.

Then call the existing CATIA COM logic (for example, `Documents.Add(...)`)
and return:

```json
{
  "documentName": "Part1.CATPart",
  "fullPath": null,
  "documentType": "CATPart"
}
```

A newly created document has no `fullPath` until it is saved. Return
`null` rather than omitting the field, so the DTO shape stays
consistent with `open_cad_document`. This tool only creates the
in-memory document; saving it to disk is a separate, not-yet-implemented
tool.

### `download_cad`

C# should support the three input variants passed by Python:

- **PLM object identifier shape**

  - `partNumber`: values such as `674089800A` or `X670830400C`
  - `partRevision`:
    - non-`X` part numbers: `00`, `01`, etc.
    - `X`-prefixed part numbers: **hyphenated only**, for example `00-01`
- **CAD identifier shape**

  - `partNumber`: values such as `6740898`
  - `partRevision`: values such as `CAD000`, `CAD002`
  - `documentType` (optional): `CATPart`, `CATProduct`, `CATDrawing`
- **Title lookup shape**

  - `partTitle` only, for example `anchor plate` or `retractor`
- **Save path**

  - `savePath` is always sent by Python.
  - If the user did not specify a location, Python defaults it to
    `C:\Temp`.
  - If the user specifies a location, that absolute Windows path is
    passed through to .NET.

Python already enforces these shape rules and normalizes `documentType`
aliases (for example `part`, `product`, `drawing`) before .NET receives
the action. .NET should still validate defensively and then dispatch to
the appropriate PLM/TIF lookup path based on which shape is present.

Expected inbound action arguments:

```json
{
    "partNumber": "X670830400C",
    "partRevision": "00-01",
    "documentType": null,
    "partTitle": null,
    "savePath": "C:\\Temp"
}
```

Possible success payload (example shape; keep fields stable once chosen):

```json
{
    "documentName": "AnchorPlate.CATPart",
    "partNumber": "X670830400C",
    "partRevision": "00-01",
    "documentType": "CATPart",
    "fullPath": "C:\\PLMCache\\AnchorPlate.CATPart"
}
```

The Python completion message builder prioritizes `documentName`, then
`partNumber + partRevision`, then `partNumber`, so returning these fields
improves user-facing responses.

### `start_model`

C# should support two deterministic argument shapes:

- `SAB` and `DU`

  - `outsystemsId` is required.
  - User may have referred to it as Outsystems ID, AO ID, or ID.
  - Python normalizes this to `outsystemsId` before dispatch.
- `SEATBELT`

  - `partNumber` and `partRevision` are required.
  - `outsystemsId` is not used for this model family.
- `modelType` (required)

  - Canonical values sent by Python: `SAB`, `DU`, `SEATBELT`
  - Python maps aliases before .NET receives the action:
    - `SAB`: side airbag, airbag
    - `DU`: driver unit, steering wheel
    - `SEATBELT`: seatbelt, buckle

IC start model (`IC`, inflatable curtain, curtain airbag) is blocked on
the Python side and returned as `unsupported_request`. .NET should still
validate defensively and reject unsupported model families if they ever
arrive due to upstream drift.

For `SEATBELT`, use a part number such as `674089800A`, `6687874100A`,
or `X6565654500A` and the corresponding revision. Non-`X` part numbers
use revisions like `00` or `01`. `X`-prefixed part numbers require a
hyphenated revision like `00-01`.

Expected inbound action arguments:

```json
{
    "outsystemsId": "2245",
    "modelType": "SAB",
  	"partNumber" : "665656500A",
  	"partRevision" : "00"
  	"savePath": "C:\\Temp"
}
```

Possible success payload (example shape; keep fields stable once chosen):

```json
{
    "success_message": "SAB model started successfully."
}
```

Python reads only `success_message` for user-facing completion text.
Keep this payload minimal and stable so the graph can render the final
message directly.

### `open_master_drawing`

Opens a master drawing using either an explicit part number, or a title
resolved against the bundled GPM catalog. `partRevision` is `null` when
the user did not give one.

```json
{
    "partNumber": "674089800A",
    "partRevision": null
}
```

```json
{
    "partNumber": "674089800A",
    "partRevision": "01"
}
```

For title lookup, Python resolves the unique matching record and sends
the hyperlink's document number and URL:

```json
{
    "partNumber": "6481212",
    "partRevision": null,
    "partTitle": "backshell",
    "plmStandardTitle": "BACKSHELL, XXX",
    "masterDrawingOrOgdLabel": "6481212",
    "masterDrawingOrOgdUrl": "<PLM drawing hyperlink from the catalog>"
}
```

In this case `partNumber` is the drawing/OGD identifier shown by the
spreadsheet hyperlink, not necessarily the user's input part number.
`masterDrawingOrOgdUrl` is extracted from that hyperlink. If several
different drawings match the title, Python asks for clarification and
does not send an action to .NET. A title-based catalog entry currently
selects the listed released document; a specific revision should use the
part-number path instead.

For direct part-number requests, Python guarantees `partNumber` is
present, trimmed, upper-cased, and actually typed by the user. A
revision-only request never reaches .NET; Python asks for a part number.
.NET should validate defensively:

- Reject a missing or empty `partNumber` with
  `INVALID_ACTION_ARGUMENTS`.
- When `masterDrawingOrOgdUrl` is present, independently require HTTPS,
  the approved PLM host, and a supported drawing/PartDrawing path before
  opening it. Do not accept an arbitrary URL merely because Python sent
  it.
- When `partRevision` is `null`, apply the business rule for "no
  revision given" (for example, open the latest released master
  drawing), and report which revision was actually opened in the
  result.

Suggested success payload (keep fields stable once chosen):

```json
{
    "documentName": "674089800A_MD.CATDrawing",
    "partNumber": "674089800A",
    "partRevision": "01",
    "fullPath": "C:\\PLMCache\\674089800A_MD.CATDrawing"
}
```

Python builds the completion message from `documentName`, then
`partNumber` + `partRevision`, then `partNumber`. Suggested failure codes
include `MASTER_DRAWING_NOT_FOUND`, along with the existing
`CATIA_NOT_RUNNING` and `DOCUMENT_OPEN_FAILED`. Call
`CatiaForegroundHelper.TryBringCatiaToForeground()` after a successful
open, as `OpenCadDocumentHandler` does.

## 16. Future tool integration

Any future action must be implemented in the Python worker and .NET
dispatcher. Add its schema to the in-memory catalog; MCP is optional and
is not part of the current transport.

### Step 1: Define argument and result DTOs

Example for `list_open_documents`:

```csharp
public sealed record ListOpenDocumentsArguments
{
}

public sealed record OpenDocumentInfo
{
    public required string Name { get; init; }
    public string? FullPath { get; init; }
    public string? DocumentType { get; init; }
}
```

### Step 2: Define a handler interface

```csharp
public interface IListOpenDocumentsHandler
{
    Task<IReadOnlyList<OpenDocumentInfo>> GetAsync(
        CancellationToken cancellationToken = default);
}
```

### Step 3: Implement deterministic CATIA logic

```csharp
public sealed class ListOpenDocumentsHandler
    : IListOpenDocumentsHandler
{
    private readonly INFITF.Application _catiaApplication;

    public ListOpenDocumentsHandler(
        INFITF.Application catiaApplication)
    {
        _catiaApplication = catiaApplication;
    }

    public Task<IReadOnlyList<OpenDocumentInfo>> GetAsync(
        CancellationToken cancellationToken = default)
    {
        // Enumerate CATIA Documents and return DTOs.
        throw new NotImplementedException();
    }
}
```

### Step 4: Add a tool enablement property

```csharp
public bool ListOpenDocumentsEnabled { get; init; } = true;
```

Add it to `CatiaAgentTools` in `appsettings.json`.

### Step 5: Inject the handler into the dispatcher

Add `IListOpenDocumentsHandler` to the `CatiaActionDispatcher`
constructor.

### Step 6: Add the explicit dispatch branch

```csharp
"list_open_documents"
    when toolOptions.ListOpenDocumentsEnabled =>
    await ExecuteListOpenDocumentsAsync(
        actionId,
        cancellationToken),
```

Add a disabled branch and retain the unknown-action fallback.

### Step 7: Convert the handler result

Return a normal serializable object through:

```csharp
ActionExecutionResultRequest.Completed(
    actionId,
    new
    {
        documents = result
    });
```

### Step 8: Register the handler

```csharp
services.AddSingleton<
    IListOpenDocumentsHandler,
    ListOpenDocumentsHandler>();
```

### Step 9: Complete the Python worker changes

The in-memory catalog definition, Pydantic model, action validation,
and completion-message handling must exist before the .NET action is
enabled. Only add MCP registration if MCP is intentionally introduced
as a future catalog provider.

### Step 10: Test the complete lifecycle

Verify:

```text
Natural-language command
→ Worker selects the in-memory tool
→ Planner model selects correct tool
→ Python validates
→ C# dispatcher accepts
→ CATIA handler executes
→ Result sent as JSON
→ LangGraph completes
→ WPF displays response
```

Also test:

- Tool disabled
- Unknown action
- Invalid arguments
- CATIA unavailable
- Handler exception
- User cancellation
- FastAPI unavailable
- `planning_error` fallback (for example, model returns no decision)

## 17. Archived multi-tool UI example

The following checkbox list is a future concept only; the current
worker exposes `open_master_drawing` and no tool-selection UI.

A future WPF tool list can expose checkboxes such as:

```text
[x] open_cad_document
[x] get_active_document
[x] download_cad
[x] list_open_documents
[ ] move_component
[ ] save_document
```

The checkbox state should update runtime tool policy, ideally through a
dedicated tool-permission service. C# remains the final authority even
if Python still advertises a disabled MCP tool.

If multiple actions are later enabled, keep permission enforcement in
the .NET dispatcher and expose only approved actions to the worker.

## 18. Future tool expansion order

### Read-only

```text
list_open_documents
get_document_info
get_product_tree
find_component
get_component_position
get_component_cog
```

### Reversible session operations

```text
activate_document
hide_component
show_component
```

### Modifying operations

```text
move_component
change_parameter
replace_component
update_document
```

### Persistent or destructive operations

```text
save_document
save_document_as
close_without_saving
delete_feature
release_to_plm
```

Modifying and persistent operations must require approval, preview,
audit logs, and additional policy checks — the Python side is expected
to introduce a separate planner with an interrupt-based approval step
before these ship.

## 19. Design rules

1. Resolve `ChatWindow` and `ChatViewModel` through DI.
2. Start one persistent Python worker with redirected standard streams.
3. Keep stdout exclusively for versioned JSON Lines; drain stderr.
4. Register the CATIA `Application` object once.
5. Use constructor injection for CATIA handlers.
6. Permit only the explicitly registered `open_master_drawing` action.
7. Validate arguments independently in C#.
8. Return stable result and error DTOs through `action_result` JSON.
9. Serialize CATIA COM operations on the CATIA-owned STA thread.
10. Keep chat routing driven by the returned Agent `status`.
11. Never execute model-generated code or reflection-based method names.

## 20. Worker verification checklist

Before validating a CATIA command, confirm:

```text
[ ] CatiaAgentWorker.exe is present and starts without a console window
[ ] The .NET process redirects stdin, stdout, and stderr
[ ] The first worker output is protocolVersion 1 / type ready
[ ] The BGE model asset is bundled and loads before ready
[ ] Azure settings are supplied through the process environment
[ ] Worker stdout contains only JSON Lines
[ ] Only open_master_drawing is enabled in the .NET dispatcher
[ ] CATIA COM is executed only by the C# handler
[ ] action_result uses the same runId and actionId as action_required
[ ] Shutdown sends a JSON shutdown message and waits for process exit
```

## 21. Legacy HTTP deployment

The former FastAPI/MCP deployment checklist and REST contract are no
longer applicable to the current worker deployment. See
[WORKER_PROTOCOL.md](WORKER_PROTOCOL.md) for the active cross-language
contract.
