import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from catia_mcp.server import mcp


# ---------------------------------------------------------
# Discovery
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_server_exposes_all_expected_tools() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()

    tool_names = {tool.name for tool in tools}

    assert tool_names == {
        "open_cad_document",
        "get_active_document",
        "create_cad_document",
        "close_cad_document",
        "request_clarification",
        "route_to_general_chat",
    }


# ---------------------------------------------------------
# open_cad_document
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_open_cad_document_prepares_action() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "open_cad_document",
            {"filePath": "C:\\TestData\\Gearbox.CATProduct"},
        )

    assert result.data["action"] == "open_cad_document"
    assert result.data["executed"] is False


@pytest.mark.asyncio
async def test_open_cad_document_rejects_relative_path() -> None:
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="must be absolute"):
            await client.call_tool(
                "open_cad_document",
                {"filePath": "TestData\\Gearbox.CATProduct"},
            )


@pytest.mark.asyncio
async def test_open_cad_document_rejects_bad_extension() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="Unsupported CATIA document extension",
        ):
            await client.call_tool(
                "open_cad_document",
                {"filePath": "C:\\TestData\\Gearbox.pdf"},
            )


# ---------------------------------------------------------
# create_cad_document
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_create_cad_document_prepares_action() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_cad_document",
            {"documentType": "CATPart"},
        )

    assert result.data["arguments"]["documentType"] == "CATPart"


# ---------------------------------------------------------
# close_cad_document
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_close_cad_document_active_mode() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "close_cad_document",
            {"targetMode": "active"},
        )

    assert result.data["arguments"]["documentName"] is None


@pytest.mark.asyncio
async def test_close_cad_document_named_mode_requires_name() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="documentName is required",
        ):
            await client.call_tool(
                "close_cad_document",
                {"targetMode": "named"},
            )


# ---------------------------------------------------------
# request_clarification
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_request_clarification_requires_non_blank_question() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="clarification question is required",
        ):
            await client.call_tool(
                "request_clarification",
                {"question": "   ", "missingField": "documentName"},
            )


# ---------------------------------------------------------
# route_to_general_chat
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_route_to_general_chat_returns_non_agentic_status() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "route_to_general_chat",
            {},
        )

    assert result.data["status"] == "non_agentic"
    assert result.data["executed"] is False
