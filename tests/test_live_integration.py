import pytest

from catia_agent.mcp_tools import (
    get_mcp_tool_catalog,
)
from catia_agent.models import (
    ActionRequiredResult,
)
from catia_agent.planner import (
    plan_catia_action,
)


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_mcp_tool_discovery() -> None:
    tools = await get_mcp_tool_catalog(
        force_refresh=True
    )

    tool_names = {
        tool["name"]
        for tool in tools
    }

    assert "open_cad_document" in tool_names
    assert "get_active_document" in tool_names


@pytest.mark.asyncio
async def test_live_qwen_selects_open_document() -> None:
    result = await plan_catia_action(
        user_command=(
            "Open "
            "C:\\TestData\\Gearbox.CATProduct"
        )
    )

    assert isinstance(
        result,
        ActionRequiredResult,
    )

    assert (
        result.action.action
        == "open_cad_document"
    )

    assert (
        result.action.arguments.filePath
        == "C:\\TestData\\Gearbox.CATProduct"
    )


@pytest.mark.asyncio
async def test_live_qwen_selects_active_document() -> None:
    result = await plan_catia_action(
        user_command=(
            "What is the active CATIA document?"
        )
    )

    assert isinstance(
        result,
        ActionRequiredResult,
    )

    assert (
        result.action.action
        == "get_active_document"
    )