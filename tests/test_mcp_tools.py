from types import SimpleNamespace

import pytest

import catia_agent.mcp_tools as mcp_tools
from catia_agent.mcp_tools import (
    McpConnectionError,
    McpToolDiscoveryError,
    McpToolSchemaError,
    clear_mcp_tool_cache,
    convert_mcp_tool_to_ollama,
    get_tool_cache_status,
    is_tool_cache_valid,
    load_ollama_tools_from_mcp,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_mcp_tool_cache()
    yield
    clear_mcp_tool_cache()


def _fake_mcp_tool(
    name: str = "open_cad_document",
    description: str = "Open a CATIA document.",
    input_schema: dict | None = None,
):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=(
            input_schema
            if input_schema is not None
            else {
                "type": "object",
                "properties": {
                    "filePath": {"type": "string"},
                },
                "required": ["filePath"],
            }
        ),
    )


# ---------------------------------------------------------
# convert_mcp_tool_to_ollama
# ---------------------------------------------------------

def test_convert_mcp_tool_to_ollama_shape() -> None:
    ollama_tool = convert_mcp_tool_to_ollama(_fake_mcp_tool())

    assert ollama_tool["type"] == "function"
    assert ollama_tool["function"]["name"] == "open_cad_document"
    assert (
        ollama_tool["function"]["description"]
        == "Open a CATIA document."
    )
    assert "filePath" in (
        ollama_tool["function"]["parameters"]["properties"]
    )


def test_convert_mcp_tool_to_ollama_defaults_missing_schema() -> None:
    ollama_tool = convert_mcp_tool_to_ollama(
        SimpleNamespace(
            name="get_active_document",
            description="",
            inputSchema=None,
        )
    )

    parameters = ollama_tool["function"]["parameters"]

    assert parameters == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_convert_mcp_tool_to_ollama_requires_name() -> None:
    with pytest.raises(McpToolSchemaError):
        convert_mcp_tool_to_ollama(
            SimpleNamespace(name=None, description="", inputSchema={})
        )


# ---------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------

def test_tool_cache_is_initially_invalid() -> None:
    assert is_tool_cache_valid() is False

    status = get_tool_cache_status()

    assert status["cached"] is False
    assert status["toolCount"] == 0


@pytest.mark.asyncio
@pytest.mark.skip(reason="The worker uses an in-memory catalog; MCP discovery is disabled.")
async def test_load_ollama_tools_from_mcp_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def fake_discover_tools():
        nonlocal call_count
        call_count += 1
        return [_fake_mcp_tool()]

    monkeypatch.setattr(
        mcp_tools.tool_catalog_service,
        "discover_tools",
        fake_discover_tools,
    )

    first = await load_ollama_tools_from_mcp()
    second = await load_ollama_tools_from_mcp()

    assert call_count == 1
    assert first == second
    assert is_tool_cache_valid() is True


@pytest.mark.asyncio
@pytest.mark.skip(reason="The worker uses an in-memory catalog; MCP discovery is disabled.")
async def test_load_ollama_tools_from_mcp_force_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def fake_discover_tools():
        nonlocal call_count
        call_count += 1
        return [_fake_mcp_tool()]

    monkeypatch.setattr(
        mcp_tools.tool_catalog_service,
        "discover_tools",
        fake_discover_tools,
    )

    await load_ollama_tools_from_mcp()
    await load_ollama_tools_from_mcp(force_refresh=True)

    assert call_count == 2


@pytest.mark.asyncio
async def test_discover_mcp_tools_wraps_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, *_exc_info):
            return False

    monkeypatch.setattr(mcp_tools, "Client", FailingClient)

    with pytest.raises(McpConnectionError):
        await mcp_tools.discover_mcp_tools()


def test_clear_mcp_tool_cache_resets_status() -> None:
    mcp_tools._cached_ollama_tools = [{"type": "function"}]
    mcp_tools._cache_created_at = 123.0

    clear_mcp_tool_cache()

    assert get_tool_cache_status()["cached"] is False
