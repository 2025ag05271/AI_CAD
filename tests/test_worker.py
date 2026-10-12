from __future__ import annotations

import pytest

import worker


@pytest.mark.asyncio
async def test_start_run_dispatches_and_returns_agent_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_start_catia_agent(**kwargs):
        captured.update(kwargs)
        return {"status": "action_required", "runId": "run-1"}

    monkeypatch.setattr(worker, "start_catia_agent", fake_start_catia_agent)

    response, should_exit = await worker.handle_message(
        {
            "protocolVersion": 1,
            "requestId": "request-1",
            "type": "start_run",
            "payload": {
                "sessionId": "session-1",
                "message": "Open the master drawing for 674089800A.",
            },
        }
    )

    assert should_exit is False
    assert response["type"] == "agent_response"
    assert response["requestId"] == "request-1"
    assert response["payload"]["status"] == "action_required"
    assert captured["session_id"] == "session-1"
    assert captured["user_message"] == "Open the master drawing for 674089800A."


@pytest.mark.asyncio
async def test_action_result_dispatches_to_graph_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_resume_catia_agent(run_id, action_result):
        captured["run_id"] = run_id
        captured["action_result"] = action_result
        return {"status": "completed", "runId": run_id}

    monkeypatch.setattr(worker, "resume_catia_agent", fake_resume_catia_agent)

    response, should_exit = await worker.handle_message(
        {
            "protocolVersion": 1,
            "requestId": "request-2",
            "type": "action_result",
            "payload": {
                "runId": "run-2",
                "actionResult": {
                    "actionId": "action-2",
                    "success": True,
                    "data": {"documentName": "Master.CATDrawing"},
                },
            },
        }
    )

    assert should_exit is False
    assert response["type"] == "agent_response"
    assert response["payload"]["status"] == "completed"
    assert captured["run_id"] == "run-2"
    assert captured["action_result"].actionId == "action-2"


@pytest.mark.asyncio
async def test_invalid_worker_request_returns_validation_error() -> None:
    response, should_exit = await worker.handle_message(
        {
            "protocolVersion": 2,
            "requestId": "request-3",
            "type": "start_run",
            "payload": {},
        }
    )

    assert should_exit is False
    assert response["type"] == "error"
    assert response["payload"]["errorCode"] == "REQUEST_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_shutdown_returns_acknowledgement() -> None:
    response, should_exit = await worker.handle_message(
        {
            "protocolVersion": 1,
            "requestId": "request-4",
            "type": "shutdown",
        }
    )

    assert should_exit is True
    assert response["type"] == "shutdown_complete"
    assert response["requestId"] == "request-4"