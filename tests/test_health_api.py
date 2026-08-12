from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import app
from catia_agent.config import settings


client = TestClient(app)

TOOLS_URL = f"{settings.api_prefix}/tools"
AGENT_RUNS_PATH = f"{settings.api_prefix}/agent/runs"


def test_health_returns_application_information() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "healthy"
    assert body["service"] == "CATIA Agent API"
    assert body["version"] == settings.app_version
    assert body["environment"] == settings.environment


def test_health_returns_request_id_header() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    request_id = response.headers.get("X-Request-ID")

    assert request_id is not None
    assert request_id != ""


def test_custom_request_id_is_preserved() -> None:
    response = client.get(
        "/health",
        headers={"X-Request-ID": "pytest-request-001"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "pytest-request-001"


def test_swagger_documentation_is_available() -> None:
    response = client.get("/docs")

    assert response.status_code == 200


def test_openapi_schema_is_available() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200

    body = response.json()

    assert body["info"]["title"] == "CATIA Agent API"
    assert AGENT_RUNS_PATH in body["paths"]
    assert (
        f"{AGENT_RUNS_PATH}/{{run_id}}/action-results"
        in body["paths"]
    )
    assert TOOLS_URL in body["paths"]
    assert "/ready" in body["paths"]


def test_readiness_returns_200_when_ready() -> None:
    fake_report = {
        "status": "ready",
        "ready": True,
        "dependencies": {
            "ollama": {"status": "available", "service": "ollama"},
            "catiaMcp": {"status": "available", "service": "catia-mcp"},
        },
    }

    with patch(
        "app.get_readiness_report",
        new=AsyncMock(return_value=fake_report),
    ):
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_readiness_returns_503_when_not_ready() -> None:
    fake_report = {
        "status": "not_ready",
        "ready": False,
        "dependencies": {
            "ollama": {
                "status": "unavailable",
                "service": "ollama",
            },
            "catiaMcp": {
                "status": "available",
                "service": "catia-mcp",
            },
        },
    }

    with patch(
        "app.get_readiness_report",
        new=AsyncMock(return_value=fake_report),
    ):
        response = client.get("/ready")

    assert response.status_code == 503
    # /ready intentionally returns the raw readiness report, not the
    # standard {"detail": ...} error envelope used elsewhere.
    assert response.json()["ready"] is False


def test_list_catia_tools_returns_catalog() -> None:
    fake_tools = [
        {
            "name": "open_cad_document",
            "description": "Open a CATIA document.",
            "inputSchema": {},
            "source": "catia-mcp",
        }
    ]

    with patch(
        "app.get_mcp_tool_catalog",
        new=AsyncMock(return_value=fake_tools),
    ):
        response = client.get(TOOLS_URL)

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "available"
    assert body["toolCount"] == 1


def test_list_catia_tools_returns_503_when_mcp_unavailable() -> None:
    from catia_agent.mcp_tools import McpToolDiscoveryError

    with patch(
        "app.get_mcp_tool_catalog",
        new=AsyncMock(
            side_effect=McpToolDiscoveryError(
                "Could not connect to the CATIA MCP server."
            )
        ),
    ):
        response = client.get(TOOLS_URL)

    assert response.status_code == 503
    assert (
        response.json()["detail"]["errorCode"]
        == "MCP_REQUEST_FAILED"
    )


def test_clear_tool_cache_returns_cleared_status() -> None:
    with patch("app.clear_mcp_tool_cache") as mocked_clear:
        response = client.post(f"{TOOLS_URL}/cache/clear")

    assert response.status_code == 200
    assert response.json()["status"] == "cleared"
    mocked_clear.assert_called_once()
