from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from catia_agent.mcp_tools import McpToolDiscoveryError
import catia_agent.readiness as readiness
from catia_agent.readiness import (
    AzureFoundryReadinessCheck,
    McpReadinessCheck,
    close_model_provider_readiness_check,
    get_readiness_report,
)


@pytest.fixture(autouse=True)
def _reset_model_provider_readiness_cache():
    readiness._cached_model_provider_readiness_check = None
    yield
    readiness._cached_model_provider_readiness_check = None


@pytest.mark.asyncio
async def test_azure_foundry_readiness_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = AzureFoundryReadinessCheck(
        endpoint="https://example.services.ai.azure.com/models",
        api_key="token",
        deployment_name="qwen3.6-27b-instruct",
        api_version="2024-05-01-preview",
        timeout_seconds=5,
    )

    fake_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {}}]},
        request=httpx.Request(
            "POST",
            "https://example.services.ai.azure.com/models/chat/completions",
        ),
    )

    monkeypatch.setattr(
        check._client,
        "post",
        AsyncMock(return_value=fake_response),
    )

    result = await check.check()

    assert result["status"] == "available"
    assert result["model"] == "qwen3.6-27b-instruct"


@pytest.mark.asyncio
async def test_azure_foundry_readiness_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = AzureFoundryReadinessCheck(
        endpoint="https://example.services.ai.azure.com/models",
        api_key="bad-token",
        deployment_name="qwen3.6-27b-instruct",
        api_version="2024-05-01-preview",
        timeout_seconds=5,
    )

    fake_response = httpx.Response(
        status_code=401,
        json={"error": "unauthorized"},
        request=httpx.Request(
            "POST",
            "https://example.services.ai.azure.com/models/chat/completions",
        ),
    )

    monkeypatch.setattr(
        check._client,
        "post",
        AsyncMock(return_value=fake_response),
    )

    result = await check.check()

    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_azure_foundry_readiness_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = AzureFoundryReadinessCheck(
        endpoint="https://example.services.ai.azure.com/models",
        api_key="token",
        deployment_name="qwen3.6-27b-instruct",
        api_version="2024-05-01-preview",
        timeout_seconds=5,
    )

    monkeypatch.setattr(
        check._client,
        "post",
        AsyncMock(side_effect=httpx.ConnectError("refused")),
    )

    result = await check.check()

    assert result["status"] == "unavailable"


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

    result = await McpReadinessCheck().check()

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

    result = await McpReadinessCheck().check()

    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_get_readiness_report_is_ready_only_when_both_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "catia_agent.readiness._build_model_provider_readiness_check",
        lambda: SimpleNamespace(
            name="modelProvider",
            check=AsyncMock(return_value={"status": "available"}),
        ),
    )

    monkeypatch.setattr(
        "catia_agent.readiness.McpReadinessCheck",
        lambda: SimpleNamespace(
            name="catiaMcp",
            check=AsyncMock(return_value={"status": "unavailable"}),
        ),
    )

    report = await get_readiness_report()

    assert report["ready"] is False
    assert report["status"] == "not_ready"


def test_build_model_provider_readiness_check_is_cached() -> None:
    first = readiness._build_model_provider_readiness_check()
    second = readiness._build_model_provider_readiness_check()

    assert first is second


@pytest.mark.asyncio
async def test_close_model_provider_readiness_check_closes_and_clears_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = readiness._build_model_provider_readiness_check()

    aclose_mock = AsyncMock()
    monkeypatch.setattr(check, "aclose", aclose_mock)

    await close_model_provider_readiness_check()

    aclose_mock.assert_awaited_once()
    assert readiness._cached_model_provider_readiness_check is None

