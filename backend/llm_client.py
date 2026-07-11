"""Shared OpenAI SDK-backed client for OpenAI-compatible provider calls.

This module centralizes request construction, timeout handling, and payload
parsing so review, calibration, assistant, and probe paths do not each carry
slightly different HTTP/JSON integration logic.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from openai import AsyncOpenAI

from backend.config import settings


async def request_chat_json(
    *,
    system_message: str,
    prompt: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Request a JSON object response from the configured OpenAI-compatible provider.

    Args:
        system_message: System instruction sent as the first chat message.
        prompt: User prompt sent as the second chat message.
        timeout_seconds: Optional timeout override in seconds.

    Returns:
        The parsed JSON object returned by the provider.

    Raises:
        RuntimeError: If provider credentials are missing, the payload is empty,
            or the returned content is not valid JSON.
    """
    content = await _request_chat_content(
        system_message=_structured_system_message(system_message),
        prompt=prompt,
        timeout_seconds=timeout_seconds,
        structured_output=True,
    )
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"llm_provider_invalid_json: {exc}") from exc


async def request_chat_text(
    *,
    system_message: str,
    prompt: str,
    timeout_seconds: float | None = None,
) -> str:
    """Request a plain text response from the configured OpenAI-compatible provider.

    Args:
        system_message: System instruction sent as the first chat message.
        prompt: User prompt sent as the second chat message.
        timeout_seconds: Optional timeout override in seconds.

    Returns:
        The extracted assistant message content.

    Raises:
        RuntimeError: If provider credentials are missing or the provider returns
            no readable content.
    """
    return await _request_chat_content(
        system_message=system_message,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
        structured_output=False,
    )


async def _request_chat_content(
    *,
    system_message: str,
    prompt: str,
    timeout_seconds: float | None,
    structured_output: bool,
) -> str:
    """Execute one chat request and extract structured or plain assistant content."""
    _require_api_key()
    client = _build_async_openai_client(timeout_seconds=timeout_seconds)
    request_kwargs: dict[str, Any] = {
        "model": settings.review_model_name,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if structured_output:
        request_kwargs["tools"] = [_structured_output_tool_spec()]

    response = await client.chat.completions.create(**request_kwargs)
    if structured_output:
        return _extract_structured_output_content(response)
    return _extract_message_content(response)


def _build_async_openai_client(timeout_seconds: float | None = None) -> AsyncOpenAI:
    """Construct the SDK client using the effective OpenAI-compatible settings."""
    return AsyncOpenAI(
        api_key=_require_api_key(),
        base_url=_sdk_base_url(),
        timeout=timeout_seconds or settings.review_timeout_seconds,
    )


def _sdk_base_url() -> str:
    """Return the provider base URL normalized for the OpenAI SDK.

    The repo's environment contract stores the provider host root without the
    final ``/v1`` segment. The OpenAI SDK expects a base URL that already ends
    at the API version prefix.
    """
    base_url = settings.effective_llm_base_url.rstrip("/")
    if PurePosixPath(base_url).name == "v1":
        return base_url
    return f"{base_url}/v1"


def _require_api_key() -> str:
    """Return the configured provider API key or raise a stable runtime error."""
    api_key = settings.effective_llm_api_key
    if not api_key:
        raise RuntimeError("llm_provider_not_configured")
    return api_key


def _structured_output_tool_spec() -> dict[str, Any]:
    """Return the single tool schema used to capture structured JSON output."""
    return {
        "type": "function",
        "function": {
            "name": "emit_structured_response",
            "description": "Return the final structured JSON response as function arguments.",
            "parameters": {
                "type": "object",
                "additionalProperties": True,
            },
        },
    }


def _structured_system_message(system_message: str) -> str:
    """Augment a system prompt with explicit instructions to return the tool call.

    Args:
        system_message: The caller-provided base system instruction.

    Returns:
        The augmented system prompt telling the model to call the structured-output tool.
    """
    return (
        f"{system_message}\n\n"
        "You must return the final answer by calling the tool `emit_structured_response`. "
        "Do not answer in plain text when the tool is available. Put the full JSON object "
        "in the tool arguments."
    )


def _extract_structured_output_content(response: Any) -> str:
    """Extract structured output, preferring tool-call arguments over message content."""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("llm_provider_invalid_payload: missing choices")

    message = getattr(choices[0], "message", None)
    if message is None:
        raise RuntimeError("llm_provider_invalid_payload: missing message")

    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, list) and tool_calls:
        function = getattr(tool_calls[0], "function", None)
        arguments = getattr(function, "arguments", None) if function is not None else None
        if isinstance(arguments, str) and arguments.strip():
            return arguments.strip()

    return _extract_message_content(response)


def _extract_message_content(response: Any) -> str:
    """Extract readable text from the first assistant message in an SDK response.

    Args:
        response: The object returned by ``AsyncOpenAI.chat.completions.create``.

    Returns:
        The extracted assistant message content.

    Raises:
        RuntimeError: If the response has no choices or no readable message content.
    """
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("llm_provider_invalid_payload: missing choices")

    message = getattr(choices[0], "message", None)
    if message is None:
        raise RuntimeError("llm_provider_invalid_payload: missing message")

    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        joined = "".join(text_parts).strip()
        if joined:
            return joined

    raise RuntimeError("llm_provider_invalid_payload: missing readable message content")
