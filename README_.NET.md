# CATIA Agentic AI Client and Executor

## .NET 9, WPF, FastAPI Client, Tool Dispatcher, and CATIA COM Architecture Guide

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
- Displays the final response returned by the handler.

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
|   |-- CatiaActionDispatcher.cs
|   |-- CatiaExecutionGate.cs
|   |-- CatiaToolExecutionException.cs
|   |-- CatiaToolOptions.cs
|   |-- ICatiaActionExecutor.cs
|   |-- IOpenCadDocumentHandler.cs
|   |-- OpenCadDocumentHandler.cs
|   |-- IGetActiveDocumentHandler.cs
|   |-- GetActiveDocumentHandler.cs
|
|-- Orchestration/
|   |-- AgentCommandResult.cs
|   |-- ICatiaAgentCommandHandler.cs
|   |-- CatiaAgentCommandHandler.cs
```

## 5. Application startup

The application began as a Console project and was converted to a Windows application with WPF enabled. Therefore, `Program.cs` is the composition root rather than `App.xaml`.

The project file should contain:

```xml
<PropertyGroup>
  <OutputType>WinExe</OutputType>
  <TargetFramework>net9.0-windows</TargetFramework>
  <UseWPF>true</UseWPF>
  <Nullable>enable</Nullable>
  <ImplicitUsings>enable</ImplicitUsings>
</PropertyGroup>

<ItemGroup>
  <None Update="appsettings.json">
    <CopyToOutputDirectory>PreserveNewest</CopyToOutputDirectory>
  </None>
</ItemGroup>
```

### Startup sequence

```text
STA Main thread
    |
    v
Receive CATIA instance GUID
    |
    v
Resolve existing CATIA Application COM object
    |
    v
Create HostApplicationBuilder
    |
    v
Load appsettings.json
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

Keep `[STAThread]` on `Main`. Use a synchronous `Main` with `GetAwaiter().GetResult()` around Host startup and shutdown so WPF and the initial CATIA COM object remain on the original STA thread.

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
    "GetActiveDocumentEnabled": true
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

`Program.cs` loads configuration from the executable directory:

```csharp
builder.Configuration.Sources.Clear();

builder.Configuration.SetBasePath(
    AppContext.BaseDirectory);

builder.Configuration.AddJsonFile(
    "appsettings.json",
    optional: false,
    reloadOnChange: true);

builder.Configuration.AddEnvironmentVariables();
```

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

Call the extension once in `Program.cs`:

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

`Program.cs` should register the application-specific CATIA services:

```csharp
services.AddSingleton(catiaApplication);
services.AddSingleton<CatiaExecutionGate>();
services.AddSingleton<ICatiaContextProvider, CatiaContextProvider>();
services.AddSingleton<IOpenCadDocumentHandler, OpenCadDocumentHandler>();
services.AddSingleton<IGetActiveDocumentHandler, GetActiveDocumentHandler>();
services.AddSingleton<IChatService, ChatService>();
services.AddSingleton<ChatViewModel>();
services.AddSingleton<ChatWindow>();
```

`IChatService` is now consumed by `CatiaAgentCommandHandler`, not by
`ChatViewModel`. The handler calls it only when a run comes back
`non_agentic`, not on every message.

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

`IChatService` moves out of `ChatViewModel` and into `CatiaAgentCommandHandler`,
since the decision to call it now happens after the backend responds, not
before the request is sent:

```csharp
public ChatViewModel(
    ICatiaAgentCommandHandler agentCommandHandler)
{
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
        DateTimeOffset.Now,
        result.Sources));
```

### Status-based routing (inside `CatiaAgentCommandHandler`)

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
        await FallBackToChatServiceAsync(
            message,
            cancellationToken),

    _ =>
        AgentCommandResult.FromMessage(
            response.Message),
};
```

```csharp
private async Task<AgentCommandResult> FallBackToChatServiceAsync(
    string message,
    CancellationToken cancellationToken)
{
    ChatResponse chatResponse =
        await _chatService.GetResponseAsync(
            message,
            cancellationToken);

    return AgentCommandResult.FromMessage(
        chatResponse.Answer,
        chatResponse.Sources);
}
```

The `AssistantMode` enum and toggle are no longer required for routing.
Keep them only if the UI still wants to visually indicate which path a
response took (for example, a small "via CATIA Agent" vs "via Chat" badge).

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

## 10. Action dispatch and security

The dispatcher must use an explicit switch:

```csharp
return action.Name switch
{
    "open_cad_document" when toolOptions.OpenCadDocumentEnabled =>
        await ExecuteOpenCadDocumentAsync(...),

    "get_active_document" when toolOptions.GetActiveDocumentEnabled =>
        await ExecuteGetActiveDocumentAsync(...),

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
NO_ACTIVE_DOCUMENT
GET_ACTIVE_DOCUMENT_FAILED
INVALID_ACTION_ARGUMENTS
TOOL_DISABLED
UNSUPPORTED_ACTION
CATIA_COM_ERROR
CATIA_EXECUTION_FAILED
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

`AppFileLogger` is separate from `ILogger<T>`. To persist all injected logs, add a file provider such as Serilog and eventually standardize on one logging pipeline.

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
[ ] appsettings.json is copied to output
[ ] CATIA Application COM object is registered
[ ] ChatWindow is resolved from DI
[ ] ChatViewModel receives ICatiaAgentCommandHandler
[ ] Tool is enabled in CatiaAgentTools
[ ] Tool handler is registered
[ ] Dispatcher contains an explicit action branch
[ ] Python MCP and Pydantic mappings exist
[ ] AgentResponses.cs models non_agentic and clarification_required
[ ] CatiaAgentCommandHandler falls back to the Chat API on non_agentic
```
