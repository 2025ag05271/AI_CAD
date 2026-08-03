from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


def test_start_agent_run_returns_action() -> None:
    fake_response = {
        "status": "action_required",
        "sessionId": "pytest-session-001",
        "runId": "run-001",
        "actionId": "action-001",
        "action": {
            "action": "open_cad_document",
            "arguments": {
                "filePath": (
                    "C:\\TestData\\Gearbox.CATProduct"
                )
            },
        },
    }

    with patch(
        "app.start_catia_agent",
        new=AsyncMock(
            return_value=fake_response
        ),
    ) as mocked_agent:
        response = client.post(
            "/api/agent/runs",
            json={
                "sessionId": "pytest-session-001",
                "message": (
                    "Open "
                    "C:\\TestData\\Gearbox.CATProduct"
                ),
                "catiaContext": {
                    "isRunning": True,
                    "activeDocument": None,
                    "openDocuments": [],
                    "selectedObjects": [],
                },
            },
        )

    assert response.status_code == 200
    assert response.json() == fake_response

    mocked_agent.assert_awaited_once()


def test_start_agent_run_requires_session_id() -> None:
    response = client.post(
        "/api/agent/runs",
        json={
            "message": (
                "What is the active CATIA document?"
            )
        },
    )

    assert response.status_code == 422


def test_start_agent_run_requires_message() -> None:
    response = client.post(
        "/api/agent/runs",
        json={
            "sessionId": "pytest-session-001"
        },
    )

    assert response.status_code == 422


def test_invalid_catia_context_is_rejected() -> None:
    response = client.post(
        "/api/agent/runs",
        json={
            "sessionId": "pytest-session-001",
            "message": (
                "What is the active CATIA document?"
            ),
            "catiaContext": {
                "isRunning": "not-a-boolean",
                "openDocuments": [],
                "selectedObjects": [],
            },
        },
    )

    assert response.status_code == 422


def test_submit_successful_action_result() -> None:
    fake_response = {
        "status": "completed",
        "sessionId": "pytest-session-001",
        "runId": "run-001",
        "actionId": "action-001",
        "message": (
            "Gearbox.CATProduct was opened "
            "successfully."
        ),
        "data": {
            "documentName": "Gearbox.CATProduct",
            "fullPath": (
                "C:\\TestData\\Gearbox.CATProduct"
            ),
        },
    }

    with patch(
        "app.resume_catia_agent",
        new=AsyncMock(
            return_value=fake_response
        ),
    ) as mocked_resume:
        response = client.post(
            (
                "/api/agent/runs/run-001/"
                "action-results"
            ),
            json={
                "actionId": "action-001",
                "success": True,
                "data": {
                    "documentName": (
                        "Gearbox.CATProduct"
                    ),
                    "fullPath": (
                        "C:\\TestData\\"
                        "Gearbox.CATProduct"
                    ),
                },
                "errorCode": None,
                "errorMessage": None,
            },
        )

    assert response.status_code == 200
    assert response.json() == fake_response

    mocked_resume.assert_awaited_once()