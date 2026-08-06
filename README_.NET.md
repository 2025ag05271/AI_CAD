# CATIA Agentic AI Client and Executor

## .NET Framework 4.8.1, WPF, FastAPI Client, Tool Dispatcher, and CATIA COM Architecture Guide

## 1. Purpose

This document explains the .NET side of the CATIA Agentic AI proof of concept. The .NET application receives a user command from the chat interface, sends the command and current CATIA context to the local FastAPI backend, receives a validated action, dispatches the action to an explicitly registered C# handler, performs the CATIA COM operation, and submits the structured result back to FastAPI.

The .NET layer is the final authority for CATIA execution. Neither Qwen, LangGraph, FastAPI, nor the MCP server directly invokes CATIA COM.

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
    +--> GET /ready
    +--> Collect CATIA context
    +--> POST /api/agent/runs
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
    |   POST /api/agent/runs/{runId}/action-results
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

`clarification_required`, `unsupported_request`, and `invalid_request` are
not shown above for clarity. They skip the dispatcher entirely and go
straight to `AgentCommandResult`, the same as `non_agentic` and `completed`.

## 3. Responsibilities

### WPF ViewModel

- Reads the user message.
- Always calls `ICatiaAgentCommandHandler` for every message. It no longer
  chooses between Chat mode and Agent mode; the Python orchestrator decides
  that per message based on the request content.
- Displays the handler response for agentic statuses and uses Chat fallback
  for `non_agentic`.

### `CatiaAgentCommandHandler`

- Verifies Agent readiness.
- Collects CATIA context.
- Starts the Agent run.
- Reads the response `status` and routes accordingly:
  - `action_required` runs the existing dispatch loop.
  - `non_agentic` calls the existing Chat API with the original user
    message and returns its answer. Python has not answered this message
    and never will; the `message` field on a `non_agentic` response is a
    routing note, not an answer, and must not be shown to the user.
  - `clarification_required`, `unsupported_request`, and `invalid_request`
    are returned to the user as-is using their `message` field.
- Executes actions through `ICatiaActionExecutor`.
- Submits results to FastAPI.
- Returns a final `AgentCommandResult`.

### `CatiaAgentApiClient`

- Owns REST communication with FastAPI.
- Reuses a managed `HttpClient`.
- Sends `X-Request-ID`.
- Deserializes Agent responses.
- Converts FastAPI errors into `CatiaAgentApiException`.

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
- Do not call FastAPI, MCP, Ollama, or Qwen.

## 4. Suggested project structure

```text
CatiaCopilotAgent/
|
|-- Client/
|   |-- ICatiaAgentApiClient.cs
|   |-- CatiaAgentApiClient.cs
|   |-- CatiaAgentApiException.cs
|
|-- Configuration/
|   |-- CatiaAgentOptions.cs
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
|   |   |-- OpenCadDocumentHandler.cs
|   |   |-- GetActiveDocumentHandler.cs
|   |   |-- CloseCadDocumentHandler.cs
|   |   |-- CreateCadDocumentHandler.cs
|   |-- Interfaces/
|   |   |-- ICatiaActionExecutor.cs
|   |   |-- IOpenCadDocumentHandler.cs
|   |   |-- IGetActiveDocumentHandler.cs
|   |   |-- ICloseCadDocumentHandler.cs
|   |   |-- ICreateCadDocumentHandler.cs
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
  "CatiaAgent": {
    "BaseAddress": "http://127.0.0.1:8000",
    "RequestTimeout": "00:01:00",
    "MaximumActionsPerRun": 20
  },
  "CatiaAgentTools": {
    "OpenCadDocumentEnabled": true,
    "GetActiveDocumentEnabled": true,
    "CloseCadDocumentEnabled": true,
    "CreateCadDocumentEnabled": true
  },
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft": "Warning",
      "System": "Warning",
      "System.Net.Http.HttpClient": "Warning"
    }
  }
}
```

### Loading configuration

`CatiaChatBotMain.ConfigureApplicationSettings` currently clears the
default sources and only registers environment variables:

```csharp
builder.Configuration.Sources.Clear();
builder.Configuration.AddEnvironmentVariables();
```

An `appsettings.json` file is **not** shipped or loaded today. All
`CatiaAgent` and `CatiaAgentTools` values fall back to the defaults
defined on `CatiaAgentOptions` and `CatiaToolOptions`, or must be
supplied via environment variables using the standard
`CatiaAgent__BaseAddress` / `CatiaAgentTools__OpenCadDocumentEnabled`
convention. Adding a JSON file provider is a planned improvement; when
it is added, remember that the host process is CATIA, so
`AppContext.BaseDirectory` will point at the CATIA install directory,
not this assembly's output folder.

### Options usage

- `CatiaAgentOptions` contains BaseAddress, timeout, and maximum actions.
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

Do not inject `CatiaToolOptions` directly unless the concrete value is explicitly registered.

## 7. Dependency injection

Call the extension once in `CatiaChatBotMain.ConfigureServices`:

```csharp
services.AddCatiaAgent(configuration);
```

The extension should register:

```text
CatiaAgentOptions
CatiaToolOptions
ICatiaAgentApiClient
ICatiaActionExecutor
ICatiaAgentCommandHandler
Managed HttpClient
```

`CatiaChatBotMain.ConfigureServices` registers the application-specific
CATIA services:

```csharp
services.AddSingleton(catiaApplication);
services.AddSingleton<CatiaExecutionGate>();
services.AddSingleton<ICatiaContextProvider, CatiaContextProvider>();
services.AddSingleton<IOpenCadDocumentHandler, OpenCadDocumentHandler>();
services.AddSingleton<IGetActiveDocumentHandler, GetActiveDocumentHandler>();
services.AddSingleton<ICloseCadDocumentHandler, CloseCadDocumentHandler>();
services.AddSingleton<ICreateCadDocumentHandler, CreateCadDocumentHandler>();
services.AddSingleton<IChatService, ChatService>();
services.AddSingleton<ChatViewModel>();
services.AddSingleton<ChatWindow>();
```

`OpenCadDocumentHandler`, `CreateCadDocumentHandler`, and
`CloseCadDocumentHandler` all take the singleton `CatiaExecutionGate`
through their constructors so every CATIA-mutating call is serialized
against the same semaphore. Any new handler that mutates CATIA state
must follow the same pattern.

`ChatViewModel` consumes both `ICatiaAgentCommandHandler` and `IChatService`.
The ViewModel always calls `ICatiaAgentCommandHandler` first, then falls
back to `IChatService` only when the handler returns a `non_agentic`
outcome.

The existing CATIA `Application` object should be acquired once and registered as the exact interop type requested by handler constructors.

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

That bypasses dependency injection and prevents the ViewModel from receiving the Agent command handler.

### ViewModel dependencies

`ChatViewModel` receives both dependencies. It always sends the user message
to the agent handler first and only calls chat fallback for `non_agentic`:

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

Every message, regardless of content, goes through the same call. There is
no mode toggle to check before calling it:

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

    _ =>
        new AgentCommandResult
        {
            Outcome = AgentCommandOutcome.Completed,
            SessionId = response.SessionId,
            RunId = response.RunId,
            Message = response.Message
                ?? "The CATIA Agent run completed."
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

## 9. FastAPI client interaction

The .NET client calls only FastAPI. It does not call MCP or Ollama directly.

### Readiness

```http
GET /ready
```

### Start a run

```http
POST /api/agent/runs
```

### Submit an action result

```http
POST /api/agent/runs/{runId}/action-results
```

Use a factory-managed or long-lived `HttpClient`. Do not instantiate a new client for each command.

The client should:

- Set BaseAddress from `CatiaAgentOptions`.
- Apply RequestTimeout.
- Send `X-Request-ID`.
- Use `System.Net.Http.Json` and `System.Text.Json`.
- Preserve cancellation tokens.
- Return useful API exceptions with HTTP status, body, and request ID.

### Response status contract

`POST /api/agent/runs` can return any of the following `status` values.
`AgentResponses.cs` should model all of them, including the two newer ones:

```text
action_required          A CATIA action is pending. Dispatch it, then call
                         the action-results endpoint.
completed                The dispatched action finished successfully.
execution_failed         The dispatched action failed. errorCode is set.
non_agentic              The message is not a CATIA action. Call the
                         existing Chat API with the original user message
                         and return its answer. Python has not answered
                         this message and never will.
clarification_required   Python needs more information before it can
                         prepare an action. missingField identifies what
                         is missing. Display message to the user.
unsupported_request      The message is CATIA-related but no tool
                         implements it yet. Display message to the user.
invalid_request          The message was empty or malformed.
```

Only `action_required` needs a follow-up call to the action-results
endpoint. Every other status is terminal for that turn.

### Multi-action runs

A single user message can require more than one CATIA action (for
example: "open Gearbox.CATProduct and then close it"). Python does not
ask Qwen to enumerate every step up front; it calls the planner again
after each successful action within the same `runId`. This means the
`POST /action-results` response for one action can itself return
another `action_required` payload with the same `runId` and a new
`actionId`. `CatiaAgentCommandHandler.HandleAsync` therefore loops on
`action_required` (capped by `CatiaAgentOptions.MaximumActionsPerRun`,
default 20) until Python returns a terminal status.

Any terminal response (`completed`, `execution_failed`, `non_agentic`,
`clarification_required`, `unsupported_request`) may include a
`completedActions` array carrying the per-action audit trail of the
run. `AgentRunResponse.CompletedActions` and
`AgentCommandResult.CompletedActions` model this shape. Consumers such
as the ViewModel can surface it when present and ignore it when absent.

### Error envelope

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

`CatiaAgentApiClient` extracts both `message` and `errorCode` from this
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
    "open_cad_document" when toolOptions.OpenCadDocumentEnabled =>
        await ExecuteOpenCadDocumentAsync(...),

    "get_active_document" when toolOptions.GetActiveDocumentEnabled =>
        await ExecuteGetActiveDocumentAsync(...),

    "close_cad_document" when toolOptions.CloseCadDocumentEnabled =>
        await ExecuteCloseCadDocumentAsync(...),

    "create_cad_document" when toolOptions.CreateCadDocumentEnabled =>
        await ExecuteCreateCadDocumentAsync(...),

    "open_cad_document" =>
        ActionExecutionResultRequest.Failed(
            actionId,
            "TOOL_DISABLED",
            "The open_cad_document tool is disabled."),

    _ =>
        ActionExecutionResultRequest.Failed(
            actionId,
            "UNSUPPORTED_ACTION",
            $"The action '{action.Name}' is not registered.")
};
```

Never use reflection to map a model-generated tool name to a method.

The C# allowlist is independent of MCP. If MCP advertises an unknown tool, C# rejects it until the tool is intentionally registered.

## 11. CATIA context provider

`ICatiaContextProvider` returns a snapshot containing:

```text
IsRunning
ActiveDocument
OpenDocuments
SelectedObjects
```

The provider receives `INFITF.Application` through its constructor.

For new or unsaved CATIA documents, `FullName` may be unavailable. Return `null` rather than failing the entire context operation.

The initial implementation may leave `SelectedObjects` empty. Selection support can be added later as a separate read capability.

## 12. CATIA COM object ownership

The existing CATIA application instance is obtained at startup and registered as a singleton:

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

`CatiaExecutionGate` serializes operations but does not move work to an STA thread. If CATIA calls may originate from arbitrary continuations or worker threads, use an established STA dispatcher or marshal execution to the CATIA-owned thread.

Keep cancellation tokens flowing through the orchestration, but recognize that a COM call may not be cancellable after it begins.

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

## 13. Error codes

Use stable machine-readable error codes:

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

```text
AGENT_RUN_STATE_CONFLICT
AGENT_RUN_NOT_FOUND
AGENT_RUN_RESUME_FAILED
ACTION_VALIDATION_FAILED
ACTION_RESULT_VALIDATION_FAILED
TOOL_SCHEMA_VALIDATION_FAILED
MCP_TOOL_DISCOVERY_FAILED
OLLAMA_REQUEST_FAILED
OLLAMA_CONNECTION_FAILED
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

A `WinExe` normally has no visible console, so `AddConsole()` is mainly useful when output is captured or a console is attached.

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
  FastAPI `detail.errorCode` is visible.
- `CatiaActionDispatcher` entry, completion, and every caught exception
  branch.

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

Avoid logging complete CAD paths or sensitive user commands at normal information level.

## 15. Current tool implementations

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

### `get_active_document`

Return the active document name, path, and type. If CATIA has no open document, return a successful result with null document fields. The Python completion node can convert this into a friendly message.

### `close_cad_document`

C# supports the following close target modes:

- `active`
- `named`
- `all`

For `named`, `documentName` is required. The handler resolves by document
name and full path matching and returns the closed document list.

The result payload must use the field name `closedDocuments` (camelCase,
plural) and it must be a JSON array of document name strings, even when
only one document was closed or when the array is empty. The Python
completion node reads this exact field to build the user-facing message
("Gearbox.CATProduct was closed successfully." for a single entry, or
"N CATIA documents were closed successfully." for multiple). Any other
field name (`closed_documents`, `ClosedDocuments`, `documents`, and so
on) will silently fall through to a generic completion message.

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
`null` rather than omitting the field, so the DTO shape stays consistent
with `open_cad_document`. This tool only creates the in-memory document;
saving it to disk is a separate, not-yet-implemented tool.

## 16. Adding a new tool

Every new tool must be implemented on both the Python and .NET sides.

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

Add `IListOpenDocumentsHandler` to the `CatiaActionDispatcher` constructor.

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

### Step 9: Complete the Python changes

The Python MCP definition, Pydantic model, explicit action mapping, policy checks, and completion handling must all exist before the C# tool is considered enabled.

### Step 10: Test the complete lifecycle

Verify:

```text
Natural-language command
→ Correct MCP tool discovered
→ Qwen selects correct tool
→ Python validates
→ C# dispatcher accepts
→ CATIA handler executes
→ Result submitted
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

## 17. GitHub Copilot-style tool selection

A future WPF tool list can expose checkboxes such as:

```text
[x] open_cad_document
[x] get_active_document
[x] list_open_documents
[ ] move_component
[ ] save_document
```

The checkbox state should update runtime tool policy, ideally through a dedicated tool-permission service. C# remains the final authority even if Python still advertises a disabled MCP tool.

For a better user experience, later send the enabled tool names to FastAPI so the backend filters MCP tools before Qwen sees them.

## 18. Recommended tool expansion order

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

Modifying and persistent operations should require approval, preview, audit logs, and additional policy checks.

## 19. Design rules

1. Resolve `ChatWindow` and `ChatViewModel` through DI.
2. Register the CATIA `Application` object once.
3. Use constructor injection for CATIA handlers.
4. Reuse `HttpClient` through `IHttpClientFactory`.
5. Permit only explicitly registered action names.
6. Validate arguments independently in C#.
7. Return stable result and error DTOs.
8. Serialize CATIA actions to prevent concurrent COM modifications.
9. Keep Chat routing and Agent routing explicit.
10. Never execute model-generated C#, VBA, Python, PowerShell, or reflection-based method names.

## 20. End-to-end verification checklist

Before validating an Agent command, confirm:

```text
[ ] Ollama is running
[ ] Qwen3 4B is installed
[ ] MCP server is running on 127.0.0.1:8001
[ ] FastAPI is running on 127.0.0.1:8000
[ ] GET /ready returns 200
[ ] Required environment variables are set (no appsettings.json today)
[ ] CATIA Application COM object is registered
[ ] ChatWindow is resolved from DI
[ ] ChatViewModel receives ICatiaAgentCommandHandler
[ ] Tool is enabled in CatiaAgentTools
[ ] Tool handler is registered
[ ] Dispatcher contains an explicit action branch
[ ] Python MCP and Pydantic mappings exist
[ ] AgentResponses.cs models non_agentic and clarification_required
[ ] AgentResponses.cs models completedActions on terminal responses
[ ] AgentCommandOutcome includes RunStateConflict and CompletedActions surface through
[ ] ChatViewModel routes non_agentic outcomes to ChatService fallback
[ ] CatiaAgentCommandHandler returns NonAgentic outcome for non_agentic status
[ ] CatiaAgentCommandHandler loops on action_required until a terminal status
[ ] CatiaAgentApiException.ErrorCode carries the FastAPI detail.errorCode
[ ] CatiaForegroundHelper is called after successful open/create
```
