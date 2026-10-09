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
        "create_cad_document",
        "close_cad_document",
        "download_cad",
        "start_model",
        "open_master_drawing",
    }


# ---------------------------------------------------------
# open_master_drawing
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_open_master_drawing_prepares_action_with_optional_revision() -> None:
    async with Client(mcp) as client:
        number_only = await client.call_tool(
            "open_master_drawing",
            {"partNumber": " 674089800a "},
        )
        with_revision = await client.call_tool(
            "open_master_drawing",
            {"partNumber": "674089800A", "partRevision": "01"},
        )

    assert number_only.data["action"] == "open_master_drawing"
    assert number_only.data["arguments"] == {
        "partNumber": "674089800A",
        "partRevision": None,
    }
    assert with_revision.data["arguments"]["partRevision"] == "01"


@pytest.mark.asyncio
async def test_open_master_drawing_rejects_revision_without_part_number() -> None:
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="requires partNumber"):
            await client.call_tool(
                "open_master_drawing",
                {"partRevision": "01"},
            )


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
        with pytest.raises(ToolError, match="absolute Windows path"):
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
# download_cad
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_download_cad_accepts_plm_shape() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "download_cad",
            {
                "partNumber": "674089800A",
                "partRevision": "00",
            },
        )

    assert result.data["action"] == "download_cad"
    assert result.data["arguments"]["partNumber"] == "674089800A"
    assert result.data["arguments"]["partRevision"] == "00"
    assert result.data["arguments"]["savePath"] == "C:\\Temp"


@pytest.mark.asyncio
async def test_download_cad_accepts_x_prefixed_hyphen_revision() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "download_cad",
            {
                "partNumber": "X670830400C",
                "partRevision": "00-02",
            },
        )

    assert result.data["arguments"]["partRevision"] == "00-02"


@pytest.mark.asyncio
async def test_download_cad_rejects_x_prefixed_non_hyphen_revision() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="hyphenated form",
        ):
            await client.call_tool(
                "download_cad",
                {
                    "partNumber": "X670830400C",
                    "partRevision": "00",
                },
            )


@pytest.mark.asyncio
async def test_download_cad_accepts_cad_shape_and_normalizes_type() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "download_cad",
            {
                "partNumber": "6740898",
                "partRevision": "CAD002",
                "documentType": "part",
            },
        )

    assert result.data["arguments"]["documentType"] == "CATPart"


@pytest.mark.asyncio
async def test_download_cad_accepts_title_shape() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "download_cad",
            {
                "partTitle": "anchor plate",
            },
        )

    assert result.data["arguments"]["partTitle"] == "anchor plate"


@pytest.mark.asyncio
async def test_download_cad_accepts_explicit_save_path() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "download_cad",
            {
                "partNumber": "6740898",
                "partRevision": "CAD002",
                "savePath": "C:\\Downloads\\CATIA",
            },
        )

    assert result.data["arguments"]["savePath"] == "C:\\Downloads\\CATIA"


@pytest.mark.asyncio
async def test_download_cad_rejects_relative_save_path() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="absolute Windows path",
        ):
            await client.call_tool(
                "download_cad",
                {
                    "partNumber": "6740898",
                    "partRevision": "CAD002",
                    "savePath": "Downloads\\CATIA",
                },
            )


@pytest.mark.asyncio
async def test_download_cad_rejects_missing_revision() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="partRevision is required",
        ):
            await client.call_tool(
                "download_cad",
                {
                    "partNumber": "674089800A",
                },
            )


@pytest.mark.asyncio
async def test_download_cad_rejects_non_x_hyphen_revision() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="PLM part numbers require",
        ):
            await client.call_tool(
                "download_cad",
                {
                    "partNumber": "674089800A",
                    "partRevision": "00-02",
                },
            )


# ---------------------------------------------------------
# start_model
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_start_model_accepts_alias_and_id() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "start_model",
            {
                "modelType": "side airbag",
                "outsystemsId": "AO-2245",
                "afisNumber": "AFIS-1001",
            },
        )

    assert result.data["action"] == "start_model"
    assert result.data["arguments"]["outsystemsId"] == "AO-2245"
    assert result.data["arguments"]["afisNumber"] == "AFIS-1001"
    assert result.data["arguments"]["modelType"] == "SAB"


@pytest.mark.asyncio
async def test_start_model_accepts_seatbelt_part_shape() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "start_model",
            {
                "modelType": "seatbelt",
                "partNumber": "6687874100A",
                "partRevision": "00",
            },
        )

    assert result.data["action"] == "start_model"
    assert result.data["arguments"]["partNumber"] == "6687874100A"
    assert result.data["arguments"]["partRevision"] == "00"
    assert result.data["arguments"]["outsystemsId"] is None


@pytest.mark.asyncio
async def test_start_model_accepts_9_digit_seatbelt_part_shape() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "start_model",
            {
                "modelType": "seatbelt",
                "partNumber": "674089800A",
                "partRevision": "01",
            },
        )

    assert result.data["arguments"]["partNumber"] == "674089800A"
    assert result.data["arguments"]["partRevision"] == "01"


@pytest.mark.asyncio
async def test_start_model_accepts_x_prefixed_seatbelt_part_shape() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "start_model",
            {
                "modelType": "buckle",
                "partNumber": "X6565654500A",
                "partRevision": "00-01",
            },
        )

    assert result.data["arguments"]["partRevision"] == "00-01"


@pytest.mark.asyncio
async def test_start_model_rejects_ic() -> None:
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match="IC start model",
        ):
            await client.call_tool(
                "start_model",
                {
                    "outsystemsId": "2245",
                    "modelType": "inflatable curtain",
                },
            )
