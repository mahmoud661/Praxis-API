"""Unit tests for `LiteLLMClient`.

Exercises three things:
  1. `_parse_row` correctly extracts a `ModelInfo` from a realistic
     `/model/info` row, tolerating missing fields with sane defaults.
  2. `list_models` HTTPs against the mock transport, returns a dict
     keyed by model name, and respects the 5-minute cache.
  3. `force_refresh=True` bypasses the cache."""

import json
from typing import Any

import httpx
import pytest

from app.infrastructure.llm.litellm_client import (
    LiteLLMClient,
    ModelInfo,
    _parse_row,
)


class _FakeLogger:
    """Captures structlog-style calls. Tests don't assert on log
    output, but every log path needs SOMETHING that responds to the
    expected methods so the client doesn't crash."""

    def info(self, *a: object, **kw: object) -> None: pass
    def warning(self, *a: object, **kw: object) -> None: pass
    def error(self, *a: object, **kw: object) -> None: pass
    def debug(self, *a: object, **kw: object) -> None: pass


def test_parse_row_minimal():
    row: dict[str, Any] = {
        "model_name": "claude-sonnet",
        "model_info": {
            "litellm_provider": "anthropic",
            "max_input_tokens": 200000,
            "max_output_tokens": 64000,
            "supports_vision": True,
            "supports_pdf_input": True,
            "supports_function_calling": True,
            "supports_response_schema": True,
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        },
    }
    info = _parse_row(row)
    assert info is not None
    assert info.model_name == "claude-sonnet"
    assert info.provider == "anthropic"
    assert info.max_input_tokens == 200000
    assert info.supports_vision is True
    assert info.supports_pdf_input is True
    # Defaults: not in the response → False (audio) or True
    # (system_messages, which is opt-out via explicit False).
    assert info.supports_audio_input is False
    assert info.supports_system_messages is True


def test_parse_row_skipped_without_name():
    assert _parse_row({"model_info": {}}) is None
    assert _parse_row({"model_name": ""}) is None
    assert _parse_row("not a dict") is None  # type: ignore[arg-type]


def test_supported_modalities_derives_set():
    info = ModelInfo(
        model_name="m",
        provider="p",
        max_input_tokens=0,
        max_output_tokens=0,
        supports_vision=True,
        supports_pdf_input=False,
        supports_audio_input=True,
        supports_function_calling=False,
        supports_tool_choice=False,
        supports_response_schema=False,
        supports_prompt_caching=False,
        supports_system_messages=True,
        input_cost_per_token=0.0,
        output_cost_per_token=0.0,
    )
    # Text is always supported; vision adds image; audio_in adds audio.
    assert info.supported_modalities == {"text", "image", "audio"}


@pytest.mark.asyncio
async def test_list_models_fetches_then_caches():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.url.path == "/model/info"
        assert request.headers["Authorization"] == "Bearer test-master-key"
        body = {
            "data": [
                {
                    "model_name": "claude-sonnet",
                    "model_info": {
                        "litellm_provider": "anthropic",
                        "max_input_tokens": 200000,
                        "max_output_tokens": 64000,
                        "supports_vision": True,
                        "supports_pdf_input": True,
                    },
                },
                {
                    "model_name": "gpt-5",
                    "model_info": {
                        "litellm_provider": "openai",
                        "max_input_tokens": 400000,
                        "max_output_tokens": 16000,
                    },
                },
            ]
        }
        return httpx.Response(200, content=json.dumps(body))

    transport = httpx.MockTransport(handler)
    client = LiteLLMClient(
        base_url="http://litellm.test",
        master_key="test-master-key",
        logger=_FakeLogger(),
        client=httpx.AsyncClient(transport=transport),
    )

    # First call hits the network.
    first = await client.list_models()
    assert set(first.keys()) == {"claude-sonnet", "gpt-5"}
    assert first["claude-sonnet"].supports_vision is True

    # Second call within the cache window returns the same dict
    # without a new HTTP request.
    second = await client.list_models()
    assert second is first  # same instance — cache returns it directly
    assert call_count == 1

    # `force_refresh=True` bypasses the cache.
    third = await client.list_models(force_refresh=True)
    assert third is not first  # different instance — re-fetched
    assert call_count == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_list_models_propagates_http_errors():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"upstream down")

    transport = httpx.MockTransport(handler)
    client = LiteLLMClient(
        base_url="http://litellm.test",
        master_key=None,  # exercise the no-auth-header branch
        logger=_FakeLogger(),
        client=httpx.AsyncClient(transport=transport),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.list_models()

    await client.aclose()


@pytest.mark.asyncio
async def test_list_models_handles_empty_data_list():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"data": []}')

    transport = httpx.MockTransport(handler)
    client = LiteLLMClient(
        base_url="http://litellm.test",
        master_key="k",
        logger=_FakeLogger(),
        client=httpx.AsyncClient(transport=transport),
    )

    models = await client.list_models()
    assert models == {}

    await client.aclose()
