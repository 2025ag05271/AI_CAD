from unittest.mock import AsyncMock

import httpx
import pytest

from catia_agent.model_providers import (
    AzureFoundryRequestError,
    AzureFoundryToolCallingModelClient,
    ModelProviderTimeoutError,
    ModelResponseFormatError,
)


def _build_client() -> AzureFoundryToolCallingModelClient:
    return AzureFoundryToolCallingModelClient(
        endpoint="https://example.services.ai.azure.com/models",
        api_key="token",
        deployment_name="qwen3.6-27b-instruct",
        api_version="2024-05-01-preview",
        timeout_seconds=5,
        temperature=0,
    )


@pytest.mark.asyncio
async def test_select_tool_normalizes_response_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client()

    fake_message = {
        "role": "assistant",
        "tool_calls": [
            {
                "function": {
                    "name": "open_cad_document",
                    "arguments": {
                        "filePath": "C:\\TestData\\Gearbox.CATProduct",
                    },
                }
            }
        ],
    }

    fake_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": fake_message}]},
        request=httpx.Request(
            "POST",
            "https://example.services.ai.azure.com/models/chat/completions",
        ),
    )

    monkeypatch.setattr(
        client._client,
        "post",
        AsyncMock(return_value=fake_response),
    )

    result = await client.select_tool(
        messages=[{"role": "user", "content": "Open the gearbox."}],
        tools=[],
    )

    assert result == {"message": fake_message}


@pytest.mark.asyncio
async def test_select_tool_forces_tool_choice_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client()

    fake_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {}}]},
        request=httpx.Request(
            "POST",
            "https://example.services.ai.azure.com/models/chat/completions",
        ),
    )

    post_mock = AsyncMock(return_value=fake_response)

    monkeypatch.setattr(client._client, "post", post_mock)

    await client.select_tool(messages=[], tools=[])

    sent_payload = post_mock.call_args.kwargs["json"]

    assert sent_payload["tool_choice"] == "required"
    assert sent_payload["model"] == "qwen3.6-27b-instruct"


@pytest.mark.asyncio
async def test_select_tool_raises_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client()

    monkeypatch.setattr(
        client._client,
        "post",
        AsyncMock(side_effect=httpx.ReadTimeout("timed out")),
    )

    with pytest.raises(ModelProviderTimeoutError):
        await client.select_tool(messages=[], tools=[])


@pytest.mark.asyncio
async def test_select_tool_raises_request_error_on_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client()

    monkeypatch.setattr(
        client._client,
        "post",
        AsyncMock(side_effect=httpx.ConnectError("refused")),
    )

    with pytest.raises(AzureFoundryRequestError):
        await client.select_tool(messages=[], tools=[])


@pytest.mark.asyncio
async def test_select_tool_raises_request_error_on_http_failure_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client()

    fake_response = httpx.Response(
        status_code=401,
        json={"error": "unauthorized"},
        request=httpx.Request(
            "POST",
            "https://example.services.ai.azure.com/models/chat/completions",
        ),
    )

    monkeypatch.setattr(
        client._client,
        "post",
        AsyncMock(return_value=fake_response),
    )

    with pytest.raises(AzureFoundryRequestError):
        await client.select_tool(messages=[], tools=[])


@pytest.mark.asyncio
async def test_select_tool_raises_format_error_on_missing_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client()

    fake_response = httpx.Response(
        status_code=200,
        json={"choices": []},
        request=httpx.Request(
            "POST",
            "https://example.services.ai.azure.com/models/chat/completions",
        ),
    )

    monkeypatch.setattr(
        client._client,
        "post",
        AsyncMock(return_value=fake_response),
    )

    with pytest.raises(ModelResponseFormatError):
        await client.select_tool(messages=[], tools=[])
