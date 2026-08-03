from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


def test_health_returns_application_information() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "healthy"
    assert body["service"] == "CATIA Agent API"
    assert body["model"] == "qwen3:4b"

    assert (
        body["mcpToolDiscoveryEndpoint"]
        == "/api/tools"
    )

    assert body["readinessEndpoint"] == "/ready"


def test_health_returns_request_id_header() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    request_id = response.headers.get(
        "X-Request-ID"
    )

    assert request_id is not None
    assert request_id != ""


def test_custom_request_id_is_preserved() -> None:
    response = client.get(
        "/health",
        headers={
            "X-Request-ID": "pytest-request-001",
        },
    )

    assert response.status_code == 200

    assert (
        response.headers["X-Request-ID"]
        == "pytest-request-001"
    )


def test_swagger_documentation_is_available() -> None:
    response = client.get("/docs")

    assert response.status_code == 200


def test_openapi_schema_is_available() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200

    body = response.json()

    assert body["info"]["title"] == (
        "CATIA Agent API"
    )

    assert "/api/agent/runs" in body["paths"]

    assert (
        "/api/agent/runs/{run_id}/action-results"
        in body["paths"]
    )

    assert "/api/tools" in body["paths"]
    assert "/ready" in body["paths"]