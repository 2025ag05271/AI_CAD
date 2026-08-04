from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from catia_agent.mcp_tools import McpToolDiscoveryError
from catia_agent.planner import MODEL_NAME
from catia_agent.readiness import (
    check_mcp_readiness,
    check_ollama_readiness,
    get_readiness_report,
)


@pytest.mark.asyncio
async def test_check_ollama_readiness_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_list_response = SimpleNamespace(
        models=[
            SimpleNamespace(model=f"{MODEL_NAME}"),
            SimpleNamespace(model="llama3:8b"),
        ]
    )

    monkeypatch.setattr(
        "catia_agent.readiness.ollama_client.list",
        AsyncMock(return_value=fake_list_response),
    )

    result = await check_ollama_readiness()

    assert result["status"] == "available"
    assert result["model"] == MODEL_NAME


@pytest.mark.asyncio
async def test_check_ollama_readiness_model_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_list_response = SimpleNamespace(
        models=[SimpleNamespace(model="llama3:8b")]
    )

    monkeypatch.setattr(
        "catia_agent.readiness.ollama_client.list",
        AsyncMock(return_value=fake_list_response),
    )

    result = await check_ollama_readiness()

    assert result["status"] == "unavailable"
    assert "installedModels" in result


@pytest.mark.asyncio
async def test_check_ollama_readiness_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.readiness.ollama_client.list",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    )

    result = await check_ollama_readiness()

    assert result["status"] == "unavailable"
    assert "technicalDetail" in result


@pytest.mark.asyncio
async def test_check_mcp_readiness_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.readiness.get_mcp_tool_catalog",
        AsyncMock(
            return_value=[{"name": "open_cad_document"}]
        ),
    )

    result = await check_mcp_readiness()

    assert result["status"] == "available"
    assert result["toolCount"] == 1


@pytest.mark.asyncio
async def test_check_mcp_readiness_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.readiness.get_mcp_tool_catalog",
        AsyncMock(
            side_effect=McpToolDiscoveryError(
                "Could not connect to the CATIA MCP server."
            )
        ),
    )

    result = await check_mcp_readiness()

    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_get_readiness_report_is_ready_only_when_both_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.readiness.check_ollama_readiness",
        AsyncMock(return_value={"status": "available"}),
    )

    monkeypatch.setattr(
        "catia_agent.readiness.check_mcp_readiness",
        AsyncMock(return_value={"status": "unavailable"}),
    )

    report = await get_readiness_report()

    assert report["ready"] is False
    assert report["status"] == "not_ready"
