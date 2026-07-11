"""Standalone diagnostic probe for the configured OpenAI-compatible LLM provider.

This module is intentionally separate from the review/calibration services so a
human operator can validate credentials, base URL, and model wiring without
running a full pipeline job first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

from backend.config import settings
from backend.llm_client import request_chat_text


@dataclass(frozen=True)
class LLMProbeResult:
    """Structured result returned by the LLM configuration probe.

    Args:
        success: Whether the provider call completed and returned a parseable response.
        mode: The current configured review execution mode.
        base_url: The effective OpenAI-compatible base URL that was targeted.
        model: The effective model name sent to the provider.
        status_code: HTTP status code when a response was received, else None.
        message: Human-readable diagnosis for CLI output.
        response_text: Returned model text excerpt when available.
        error_type: Stable machine-readable category for failures, else None.
    """

    success: bool
    mode: str
    base_url: str
    model: str
    status_code: int | None
    message: str
    response_text: str | None = None
    error_type: str | None = None


async def probe_llm_config(
    *,
    prompt: str,
    timeout_seconds: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LLMProbeResult:
    """Probe the configured OpenAI-compatible provider with one tiny request.

    Args:
        prompt: Small user prompt sent to the provider.
        timeout_seconds: Optional timeout override for the outbound request.
        transport: Optional custom transport for tests.

    Returns:
        An ``LLMProbeResult`` describing success or an actionable failure.
    """
    api_key = settings.effective_llm_api_key
    base_url = settings.effective_llm_base_url
    model = settings.review_model_name
    mode = settings.review_execution_mode

    if not api_key:
        return LLMProbeResult(
            success=False,
            mode=mode,
            base_url=base_url,
            model=model,
            status_code=None,
            error_type="missing_api_key",
            message=(
                "No LLM API key configured. Set LLM_API_KEY or FEATHERLESS_API_KEY "
                "before probing external provider access."
            ),
        )

    timeout = timeout_seconds or settings.review_timeout_seconds
    original_builder = None
    if transport is not None:
        from backend import llm_client

        original_builder = llm_client._build_async_openai_client
        llm_client._build_async_openai_client = lambda timeout_seconds=None: llm_client.AsyncOpenAI(
            api_key=api_key,
            base_url=llm_client._sdk_base_url(),
            timeout=timeout_seconds or settings.review_timeout_seconds,
            http_client=httpx.AsyncClient(
                transport=transport,
                timeout=timeout_seconds or settings.review_timeout_seconds,
            ),
        )

    try:
        response_text = await request_chat_text(
            system_message=(
                "You are a connectivity probe. Reply with a short plain-text answer only."
            ),
            prompt=prompt,
            timeout_seconds=timeout,
        )
    except RuntimeError as exc:
        error_text = str(exc)
        error_type = "invalid_payload"
        if error_text == "llm_provider_not_configured":
            error_type = "missing_api_key"
            message = (
                "No LLM API key configured. Set LLM_API_KEY or FEATHERLESS_API_KEY "
                "before probing external provider access."
            )
        else:
            message = error_text
        return LLMProbeResult(
            success=False,
            mode=mode,
            base_url=base_url,
            model=model,
            status_code=None,
            error_type=error_type,
            message=message,
        )
    except APIConnectionError as exc:
        return LLMProbeResult(
            success=False,
            mode=mode,
            base_url=base_url,
            model=model,
            status_code=None,
            error_type="connection_error",
            message=(
                f"Could not connect to {base_url}. Check DNS, network reachability, "
                f"and provider hostname. Upstream error: {_exception_detail(exc)}"
            ),
        )
    except APITimeoutError as exc:
        return LLMProbeResult(
            success=False,
            mode=mode,
            base_url=base_url,
            model=model,
            status_code=None,
            error_type="timeout",
            message=(
                f"Provider request timed out after {timeout} seconds. Check REVIEW_TIMEOUT_SECONDS "
                f"or provider latency. Upstream error: {_exception_detail(exc)}"
            ),
        )
    except APIStatusError as exc:
        return LLMProbeResult(
            success=False,
            mode=mode,
            base_url=base_url,
            model=model,
            status_code=exc.status_code,
            error_type="http_error",
            message=(
                f"Provider returned HTTP {exc.status_code}. "
                f"{_extract_error_detail_from_response(exc.response)}"
            ),
        )
    finally:
        if original_builder is not None:
            from backend import llm_client

            llm_client._build_async_openai_client = original_builder

    return LLMProbeResult(
        success=True,
        mode=mode,
        base_url=base_url,
        model=model,
        status_code=200,
        message="LLM probe succeeded.",
        response_text=response_text,
    )


def _extract_error_detail_from_response(response: httpx.Response) -> str:
    """Return the most useful provider error detail available from an HTTP response."""
    try:
        payload = response.json()
    except json.JSONDecodeError:
        text = response.text.strip()
        return text[:300] if text else "Provider returned an empty error response body."

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        err_type = error.get("type")
        detail_parts = [str(part) for part in (message, code, err_type) if part]
        if detail_parts:
            return " | ".join(detail_parts)

    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return json.dumps(payload, ensure_ascii=False)[:300]


def _exception_detail(exc: Exception) -> str:
    """Return the most specific available message from an SDK exception chain."""
    cause = exc.__cause__
    if cause is not None and str(cause).strip():
        return str(cause)
    detail = str(exc).strip()
    return detail or exc.__class__.__name__


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the LLM probe utility."""
    parser = argparse.ArgumentParser(
        description="Probe the configured OpenAI-compatible LLM endpoint.",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: probe-ok",
        help="Small probe prompt to send to the provider.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional timeout override in seconds.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output instead of human-readable text.",
    )
    return parser


def _render_text_result(result: LLMProbeResult) -> str:
    """Render a human-friendly CLI summary for one probe result."""
    lines = [
        f"success: {result.success}",
        f"mode: {result.mode}",
        f"base_url: {result.base_url}",
        f"model: {result.model}",
    ]
    if result.status_code is not None:
        lines.append(f"status_code: {result.status_code}")
    if result.error_type:
        lines.append(f"error_type: {result.error_type}")
    lines.append(f"message: {result.message}")
    if result.response_text:
        lines.append(f"response_text: {result.response_text}")
    return "\n".join(lines)


def main() -> None:
    """Run the CLI probe and exit with status 0 on success, else 1."""
    args = _build_parser().parse_args()
    result = asyncio.run(
        probe_llm_config(
            prompt=args.prompt,
            timeout_seconds=args.timeout,
        )
    )
    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        print(_render_text_result(result))
    raise SystemExit(0 if result.success else 1)


if __name__ == "__main__":
    main()
