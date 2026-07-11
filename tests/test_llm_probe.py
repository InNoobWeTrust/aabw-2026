"""Tests for the standalone LLM configuration probe utility."""

from __future__ import annotations

import httpx
import pytest

from backend import llm_probe


@pytest.mark.asyncio
async def test_probe_llm_config_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid OpenAI-compatible response should produce a success result."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://provider.example/v1/chat/completions")
        assert request.headers["Authorization"] == "Bearer probe-key"
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "probe-ok",
                    }
                }
            ]
        }
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(llm_probe.settings, "llm_api_key", "probe-key")
    monkeypatch.setattr(llm_probe.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_probe.settings, "llm_model_name", "provider-model")

    result = await llm_probe.probe_llm_config(
        prompt="say probe-ok",
        transport=httpx.MockTransport(handler),
    )

    assert result.success is True
    assert result.base_url == "https://provider.example"
    assert result.model == "provider-model"
    assert result.response_text == "probe-ok"
    assert result.error_type is None


@pytest.mark.asyncio
async def test_probe_llm_config_reports_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing credentials should fail fast before making a network request."""
    monkeypatch.setattr(llm_probe.settings, "llm_api_key", None)
    monkeypatch.setattr(llm_probe.settings, "featherless_api_key", None)
    monkeypatch.setattr(llm_probe.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_probe.settings, "llm_model_name", "provider-model")

    result = await llm_probe.probe_llm_config(prompt="hello")

    assert result.success is False
    assert result.error_type == "missing_api_key"
    assert "LLM_API_KEY" in result.message


@pytest.mark.asyncio
async def test_probe_llm_config_reports_provider_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider HTTP failures should surface status code and body detail."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "invalid api key", "type": "invalid_request_error"}},
        )

    monkeypatch.setattr(llm_probe.settings, "llm_api_key", "bad-key")
    monkeypatch.setattr(llm_probe.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_probe.settings, "llm_model_name", "provider-model")

    result = await llm_probe.probe_llm_config(
        prompt="hello",
        transport=httpx.MockTransport(handler),
    )

    assert result.success is False
    assert result.status_code == 401
    assert result.error_type == "http_error"
    assert "invalid api key" in result.message


@pytest.mark.asyncio
async def test_probe_llm_config_reports_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network failures should be converted into actionable diagnostics."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failed", request=request)

    monkeypatch.setattr(llm_probe.settings, "llm_api_key", "probe-key")
    monkeypatch.setattr(llm_probe.settings, "llm_base_url", "https://provider.example")
    monkeypatch.setattr(llm_probe.settings, "llm_model_name", "provider-model")

    result = await llm_probe.probe_llm_config(
        prompt="hello",
        transport=httpx.MockTransport(handler),
    )

    assert result.success is False
    assert result.error_type == "connection_error"
    assert "dns failed" in result.message
