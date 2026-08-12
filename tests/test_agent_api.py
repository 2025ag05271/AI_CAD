from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import app
from catia_agent.config import settings
from catia_agent.graph import (
    AgentRunNotFoundError,
    AgentRunStateConflictError,
)


client = TestClient(app)

AGENT_RUNS_URL = f"{settings.api_prefix}/agent/runs"


def _action_results_url(run_id: str) -> str:
    return f"{AGENT_RUNS_URL}/{run_id}/action-results"


def test_start_agent_run_returns_action_required() -> None:
    fake_response = {
        "status": "action_required",
        "sessionId": "pytest-session-001",
        "runId": "run-001",
        "actionId": "action-001",
        "action": {
            "action": "open_cad_document",
            "arguments": {
                "filePath": "C:\\TestData\\Gearbox.CATProduct"
            },
        },
    }

    with patch(
        "app.start_catia_agent",
        new=AsyncMock(return_value=fake_response),
    ) as mocked_agent:
        response = client.post(
            AGENT_RUNS_URL,
            json={
                "sessionId": "pytest-session-001",
                "message": "Open C:\\TestData\\Gearbox.CATProduct",
            },
        )

    assert response.status_code == 200
    assert response.json() == fake_response
    mocked_agent.assert_awaited_once()


def test_start_agent_run_returns_non_agentic() -> None:
    fake_response = {
        "status": "non_agentic",
        "sessionId": "pytest-session-001",
        "runId": "run-001",
        "message": (
            "This request does not require a CATIA action. "
            "Route it to the general chat service."
        ),
    }

    with patch(
        "app.start_catia_agent",
        new=AsyncMock(return_value=fake_response),
    ):
        response = client.post(
            AGENT_RUNS_URL,
            json={
                "sessionId": "pytest-session-001",
                "message": "What is the capital of France?",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "non_agentic"


def test_start_agent_run_requires_session_id() -> None:
    response = client.post(
        AGENT_RUNS_URL,
        json={"message": "What is the active CATIA document?"},
    )

    assert response.status_code == 422


def test_start_agent_run_requires_message() -> None:
    response = client.post(
        AGENT_RUNS_URL,
        json={"sessionId": "pytest-session-001"},
    )

    assert response.status_code == 422


def test_invalid_catia_context_is_rejected() -> None:
    response = client.post(
        AGENT_RUNS_URL,
        json={
            "sessionId": "pytest-session-001",
            "message": "What is the active CATIA document?",
            "catiaContext": {
                "isRunning": "not-a-boolean",
                "openDocuments": [],
                "selectedObjects": [],
            },
        },
    )

    assert response.status_code == 422


def test_start_agent_run_maps_pending_action_to_409() -> None:
    with patch(
        "app.start_catia_agent",
        new=AsyncMock(
            side_effect=AgentRunStateConflictError(
                "This session already has a pending CATIA action."
            )
        ),
    ):
        response = client.post(
            AGENT_RUNS_URL,
            json={
                "sessionId": "pytest-session-001",
                "message": "Open the gearbox.",
            },
        )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["errorCode"]
        == "AGENT_RUN_STATE_CONFLICT"
    )


def test_start_agent_run_maps_generic_value_error_to_500() -> None:
    # ValueError -> 400/ACTION_VALIDATION_FAILED was removed; an
    # unexpected ValueError now falls through the request middleware's
    # generic handler as 500/INTERNAL_SERVER_ERROR (see README_Python.md
    # Section 7, "Codes ... no longer produced").
    with patch(
        "app.start_catia_agent",
        new=AsyncMock(
            side_effect=ValueError(
                "Unsupported CATIA document extension."
            )
        ),
    ):
        response = client.post(
            AGENT_RUNS_URL,
            json={
                "sessionId": "pytest-session-001",
                "message": "Open the gearbox.",
            },
        )

    assert response.status_code == 500
    assert (
        response.json()["detail"]["errorCode"]
        == "INTERNAL_SERVER_ERROR"
    )


def test_submit_successful_action_result() -> None:
    fake_response = {
        "status": "completed",
        "sessionId": "pytest-session-001",
        "runId": "run-001",
        "actionId": "action-001",
        "message": "Gearbox.CATProduct was opened successfully.",
        "data": {
            "documentName": "Gearbox.CATProduct",
            "fullPath": "C:\\TestData\\Gearbox.CATProduct",
        },
        # ActionCompletedResponse.completedActions defaults to [], so
        # FastAPI's response_model always fills it in on "completed".
        "completedActions": [],
    }

    with patch(
        "app.resume_catia_agent",
        new=AsyncMock(return_value=fake_response),
    ) as mocked_resume:
        response = client.post(
            _action_results_url("run-001"),
            json={
                "actionId": "action-001",
                "success": True,
                "data": {
                    "documentName": "Gearbox.CATProduct",
                    "fullPath": "C:\\TestData\\Gearbox.CATProduct",
                },
                "errorCode": None,
                "errorMessage": None,
            },
        )

    assert response.status_code == 200
    assert response.json() == fake_response
    mocked_resume.assert_awaited_once()


def test_submit_action_result_run_not_found() -> None:
    with patch(
        "app.resume_catia_agent",
        new=AsyncMock(
            side_effect=AgentRunNotFoundError(
                "No pending Agent run was found."
            )
        ),
    ):
        response = client.post(
            _action_results_url("missing-run"),
            json={
                "actionId": "action-001",
                "success": True,
            },
        )

    assert response.status_code == 404
    assert (
        response.json()["detail"]["errorCode"]
        == "AGENT_RUN_NOT_FOUND"
    )


def test_submit_action_result_state_conflict() -> None:
    with patch(
        "app.resume_catia_agent",
        new=AsyncMock(
            side_effect=AgentRunStateConflictError(
                "This Agent run is not waiting for an action result."
            )
        ),
    ):
        response = client.post(
            _action_results_url("run-001"),
            json={
                "actionId": "action-001",
                "success": True,
            },
        )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["errorCode"]
        == "AGENT_RUN_STATE_CONFLICT"
    )


def test_submit_action_result_resume_failure_is_not_conflict() -> None:
    # AGENT_RUN_RESUME_FAILED was removed; an unexpected ValueError from
    # the resume path now falls through to 500/INTERNAL_SERVER_ERROR,
    # distinct from the 409 state-conflict guards above (see
    # README_Python.md Section 7, "Codes ... no longer produced").
    with patch(
        "app.resume_catia_agent",
        new=AsyncMock(
            side_effect=ValueError(
                "Planner rejected the follow-up tool call."
            )
        ),
    ):
        response = client.post(
            _action_results_url("run-001"),
            json={
                "actionId": "action-001",
                "success": True,
            },
        )

    assert response.status_code == 500
    assert (
        response.json()["detail"]["errorCode"]
        == "INTERNAL_SERVER_ERROR"
    )
