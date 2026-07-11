"""Tests for the shared OpenAI SDK-backed LLM client adapter."""

from __future__ import annotations

import pytest

from backend import llm_client


class _FakeToolFunction:
    def __init__(self, arguments):
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, arguments):
        self.function = _FakeToolFunction(arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_arguments=None):
        self.content = content
        self.tool_calls = [] if tool_arguments is None else [_FakeToolCall(tool_arguments)]


class _FakeChoice:
    def __init__(self, content=None, tool_arguments=None):
        self.message = _FakeMessage(content=content, tool_arguments=tool_arguments)


class _FakeResponse:
    def __init__(self, content=None, tool_arguments=None):
        self.choices = [_FakeChoice(content=content, tool_arguments=tool_arguments)]


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeChat:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeClient:
    def __init__(self, response):
        self.chat = _FakeChat(response)


@pytest.mark.asyncio
async def test_request_chat_json_returns_parsed_tool_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON chat requests should parse the first tool-call arguments into a dict."""
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "probe-key")
    monkeypatch.setattr(llm_client.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_client.settings, "llm_model_name", "provider-model")

    fake_client = _FakeClient(_FakeResponse(tool_arguments='{"summary":"ok","verdict":"approved"}'))
    monkeypatch.setattr(
        llm_client,
        "_build_async_openai_client",
        lambda timeout_seconds=None: fake_client,
    )

    payload = await llm_client.request_chat_json(system_message="system", prompt="prompt")

    assert payload["summary"] == "ok"
    assert fake_client.chat.completions.last_kwargs["model"] == "provider-model"
    assert "tool_choice" not in fake_client.chat.completions.last_kwargs
    assert (
        fake_client.chat.completions.last_kwargs["tools"][0]["function"]["name"]
        == "emit_structured_response"
    )
    assert (
        "You must return the final answer by calling the tool"
        in (fake_client.chat.completions.last_kwargs["messages"][0]["content"])
    )


@pytest.mark.asyncio
async def test_request_chat_text_returns_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text chat requests should return the first message content unchanged."""
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "probe-key")
    monkeypatch.setattr(llm_client.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_client.settings, "llm_model_name", "provider-model")

    fake_client = _FakeClient(_FakeResponse("probe-ok"))
    monkeypatch.setattr(
        llm_client,
        "_build_async_openai_client",
        lambda timeout_seconds=None: fake_client,
    )

    response_text = await llm_client.request_chat_text(system_message="system", prompt="prompt")

    assert response_text == "probe-ok"
    assert "response_format" not in fake_client.chat.completions.last_kwargs


@pytest.mark.asyncio
async def test_request_chat_json_rejects_invalid_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured JSON requests should fail explicitly on malformed tool arguments and content."""
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "probe-key")
    monkeypatch.setattr(llm_client.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_client.settings, "llm_model_name", "provider-model")

    fake_client = _FakeClient(_FakeResponse(content="not-json", tool_arguments="also-not-json"))
    monkeypatch.setattr(
        llm_client,
        "_build_async_openai_client",
        lambda timeout_seconds=None: fake_client,
    )

    with pytest.raises(RuntimeError, match="llm_provider_invalid_json"):
        await llm_client.request_chat_json(system_message="system", prompt="prompt")


@pytest.mark.asyncio
async def test_request_chat_json_rejects_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing provider credentials should raise a clear runtime error."""
    monkeypatch.setattr(llm_client.settings, "llm_api_key", None)
    monkeypatch.setattr(llm_client.settings, "featherless_api_key", None)

    with pytest.raises(RuntimeError, match="llm_provider_not_configured"):
        await llm_client.request_chat_json(system_message="system", prompt="prompt")


@pytest.mark.asyncio
async def test_request_chat_json_falls_back_to_content_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON chat requests should still parse legacy content JSON when tool calls are absent."""
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "probe-key")
    monkeypatch.setattr(llm_client.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_client.settings, "llm_model_name", "provider-model")

    fake_client = _FakeClient(_FakeResponse('{"summary":"fallback"}'))
    monkeypatch.setattr(
        llm_client,
        "_build_async_openai_client",
        lambda timeout_seconds=None: fake_client,
    )

    payload = await llm_client.request_chat_json(system_message="system", prompt="prompt")

    assert payload["summary"] == "fallback"
