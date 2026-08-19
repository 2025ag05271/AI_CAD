from unittest.mock import AsyncMock

import httpx
import pytest

from catia_agent.model_providers import (
    AzureFoundryRequestError,
    AzureFoundryToolCallingModelClient,
    ModelProviderTimeoutError,
    ModelResponseFormatError,
)


def _build_client(
    *,
    enable_thinking: bool = False,
) -> AzureFoundryToolCallingModelClient:
    return AzureFoundryToolCallingModelClient(
        endpoint="https://example.services.ai.azure.com/models",
        api_key="token",
        deployment_name="qwen3.6-27b-instruct",
        api_version="2024-05-01-preview",
        timeout_seconds=5,
        temperature=0,
        max_tokens=2048,
        enable_thinking=enable_thinking,
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
async def test_select_tool_normalizes_real_vllm_reasoning_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Shape actually returned by the Azure AI Foundry vLLM deployment:
    # arguments is a JSON-encoded string, and a large "reasoning" field
    # accompanies the tool call even though the prompt asks for none.
    fake_message = {
        "role": "assistant",
        "content": "",
        "refusal": None,
        "tool_calls": [
            {
                "id": "chatcmpl-tool-9761220fc49a4cf1",
                "type": "function",
                "function": {
                    "name": "request_clarification",
                    "arguments": (
                        '{"question": "What type of document?", '
                        '"missingField": "documentType"}'
                    ),
                },
            }
        ],
        "reasoning": "The user is asking two things...",
    }

    client = _build_client()

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

    result = await client.select_tool(messages=[], tools=[])

    assert result == {"message": fake_message}


@pytest.mark.asyncio
async def test_select_tool_logs_usage_and_cached_tokens(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _build_client()

    fake_message = {"role": "assistant", "tool_calls": []}

    fake_response = httpx.Response(
        status_code=200,
        json={
            "choices": [{"message": fake_message}],
            "usage": {
                "prompt_tokens": 1500,
                "completion_tokens": 20,
                "total_tokens": 1520,
                "prompt_tokens_details": {
                    "cached_tokens": 1400,
                },
            },
        },
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

    with caplog.at_level("INFO"):
        await client.select_tool(messages=[], tools=[])

    usage_records = [
        record
        for record in caplog.records
        if record.message == "azure_foundry_usage"
    ]

    assert len(usage_records) == 1
    assert usage_records[0].promptTokens == 1500
    assert usage_records[0].completionTokens == 20
    assert usage_records[0].totalTokens == 1520
    assert usage_records[0].cachedTokens == 1400


@pytest.mark.asyncio
async def test_select_tool_logs_usage_with_missing_cached_tokens(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _build_client()

    fake_response = httpx.Response(
        status_code=200,
        json={
            "choices": [
                {"message": {"role": "assistant", "tool_calls": []}}
            ],
        },
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

    with caplog.at_level("INFO"):
        await client.select_tool(messages=[], tools=[])

    usage_records = [
        record
        for record in caplog.records
        if record.message == "azure_foundry_usage"
    ]

    assert len(usage_records) == 1
    assert usage_records[0].cachedTokens is None


@pytest.mark.asyncio
async def test_select_tool_logs_request_payload_at_debug_level(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _build_client()

    fake_response = httpx.Response(
        status_code=200,
        json={
            "choices": [
                {"message": {"role": "assistant", "tool_calls": []}}
            ],
        },
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

    messages = [{"role": "user", "content": "Open the gearbox."}]
    tools = [
        {
            "type": "function",
            "function": {"name": "open_cad_document"},
        }
    ]

    with caplog.at_level("DEBUG"):
        await client.select_tool(messages=messages, tools=tools)

    payload_records = [
        record
        for record in caplog.records
        if record.message == "azure_foundry_request_payload"
    ]

    assert len(payload_records) == 1
    assert payload_records[0].payload["messages"] == messages
    assert payload_records[0].payload["tools"] == tools
    assert payload_records[0].payload["tool_choice"] == "required"



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
    assert sent_payload["max_tokens"] == 2048
    # The deployment is resolved by the URL; some Azure AI Foundry
    # deployments reject an unexpected "model" field in the body.
    assert "model" not in sent_payload


@pytest.mark.asyncio
async def test_select_tool_disables_thinking_by_default(
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

    assert sent_payload["chat_template_kwargs"] == {
        "enable_thinking": False,
    }


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
