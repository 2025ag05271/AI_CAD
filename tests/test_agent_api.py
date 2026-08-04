from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


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
            "/api/agent/runs",
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
            "/api/agent/runs",
            json={
                "sessionId": "pytest-session-001",
                "message": "What is the capital of France?",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "non_agentic"


def test_start_agent_run_requires_session_id() -> None:
    response = client.post(
        "/api/agent/runs",
        json={"message": "What is the active CATIA document?"},
    )

    assert response.status_code == 422


def test_start_agent_run_requires_message() -> None:
    response = client.post(
        "/api/agent/runs",
        json={"sessionId": "pytest-session-001"},
    )

    assert response.status_code == 422


def test_invalid_catia_context_is_rejected() -> None:
    response = client.post(
        "/api/agent/runs",
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


def test_start_agent_run_maps_value_error_to_400() -> None:
    with patch(
        "app.start_catia_agent",
        new=AsyncMock(
            side_effect=ValueError(
                "This session already has a pending CATIA action."
            )
        ),
    ):
        response = client.post(
            "/api/agent/runs",
            json={
                "sessionId": "pytest-session-001",
                "message": "Open the gearbox.",
            },
        )

    assert response.status_code == 400
    assert (
        response.json()["detail"]["errorCode"]
        == "ACTION_VALIDATION_FAILED"
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
    }

    with patch(
        "app.resume_catia_agent",
        new=AsyncMock(return_value=fake_response),
    ) as mocked_resume:
        response = client.post(
            "/api/agent/runs/run-001/action-results",
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
            side_effect=KeyError("No pending Agent run was found.")
        ),
    ):
        response = client.post(
            "/api/agent/runs/missing-run/action-results",
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
            side_effect=ValueError(
                "This Agent run is not waiting for an action result."
            )
        ),
    ):
        response = client.post(
            "/api/agent/runs/run-001/action-results",
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
