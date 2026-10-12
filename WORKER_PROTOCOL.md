# CATIA Agent Worker Protocol

The WPF client launches one long-running `CatiaAgentWorker.exe` process. The worker communicates over redirected standard input and output using UTF-8 JSON Lines. There is no local HTTP listener in this deployment. Standard output is reserved for protocol messages; diagnostics are written to standard error and the configured log file.

The worker emits a `ready` message only after loading the bundled BGE model and its intent exemplars. Keep the process alive for the WPF session because graph state, pending action state, and run-to-session mappings live in memory.

## Process configuration

Set `UseShellExecute = false`, `RedirectStandardInput = true`, `RedirectStandardOutput = true`, `RedirectStandardError = true`, and `CreateNoWindow = true` in `ProcessStartInfo`. Drain standard error independently so a full stderr pipe cannot block the worker. Send one complete JSON object followed by a newline and read one response line per request.

The process needs the Azure AI Foundry endpoint, bearer key, deployment name, and other `CATIA_AGENT_AZURE_FOUNDRY_*` settings in its environment. Do not place the bearer key in the executable or log it. The BGE GGUF is bundled into the executable by `build_worker.ps1`.

Set `CATIA_AGENT_RUNTIME_MODE=worker` in the child process environment. This selects worker-safe production configuration and skips validation for the disabled FastAPI/MCP endpoints.

## Ready

First worker output after startup and model warmup:

```json
{"protocolVersion":1,"type":"ready","payload":{}}
```

If the process exits before this message, treat startup as failed. A ready message confirms that the local worker and bundled classifier initialized; it does not probe Azure AI Foundry.

## Start a run

Input:

```json
{"protocolVersion":1,"requestId":"req-001","type":"start_run","payload":{"sessionId":"session-001","message":"Open the master drawing for 674089800A revision 01","catiaContext":{"isRunning":true,"activeDocument":null,"openDocuments":[],"selectedObjects":[]}}}
```

The `payload` uses the existing `AgentRunRequest` shape. `catiaContext` may be omitted or `null`.

Output:

```json
{"protocolVersion":1,"requestId":"req-001","type":"agent_response","payload":{"status":"action_required","sessionId":"session-001","runId":"run-id","actionId":"action-id","action":{"action":"open_master_drawing","arguments":{"partNumber":"674089800A","partRevision":"01"}}}}
```

The only enabled CATIA executor action is `open_master_drawing`. The response can also be a terminal Agent status such as `non_agentic`, `clarification_required`, `unsupported_request`, `planning_error`, or `invalid_request`. Preserve the existing status-based chat fallback behavior.

## Submit the .NET result

After the .NET dispatcher executes the action, send:

```json
{"protocolVersion":1,"requestId":"req-002","type":"action_result","payload":{"runId":"run-id","actionResult":{"actionId":"action-id","success":true,"data":{"documentName":"674089800A_MD.CATDrawing","partNumber":"674089800A","partRevision":"01"},"errorCode":null,"errorMessage":null}}}
```

For failure, set `success` to `false` and provide both `errorCode` and `errorMessage`; `data` may be `null`. The worker validates this using the existing `ActionExecutionResultRequest` model and returns the same terminal Agent response shape used by the former API.

## Errors and shutdown

Protocol and request failures use:

```json
{"protocolVersion":1,"requestId":"req-003","type":"error","payload":{"status":422,"errorCode":"REQUEST_VALIDATION_FAILED","message":"The request payload did not match the Agent contract."}}
```

`requestId` is echoed when it can be safely read. Each input line is limited to 1 MiB. To shut down cleanly, send:

```json
{"protocolVersion":1,"requestId":"req-004","type":"shutdown","payload":{}}
```

The worker responds with `shutdown_complete` and exits. A process restart loses in-memory pending runs; the .NET host must not automatically replay an action whose completion is unknown.

## Future MCP provision

The planner currently receives the single built-in `open_master_drawing` schema from `catia_agent/tool_catalog.py`. MCP is not started or contacted. Keep the catalog boundary as the extension point for a future MCP-backed provider; do not add MCP transport to the current worker path.
