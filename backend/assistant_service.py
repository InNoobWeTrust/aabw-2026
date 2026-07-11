"""Reviewer-assistant chat service with a bounded agentic tool loop.

The assistant is intentionally separate from the single-shot review pipeline:
- single-shot stage reviews remain the canonical automated first pass
- this assistant helps a human reviewer investigate artifacts interactively

The loop is bounded by max turns and a small whitelist of local read-only tools.
When external credentials are available, the loop uses Featherless via Daytona
for each reasoning turn. Otherwise it falls back to a deterministic local helper.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from backend.assistant_store import FileSystemAssistantStore
from backend.config import settings
from backend.job_store import FileSystemJobStore
from backend.review_store import FileSystemReviewStore
from domain.enums import AssistantMessageRole, AssistantSessionStatus, ReviewStage
from domain.reviews import AssistantEvent, AssistantMessage, AssistantSessionSnapshot

_logger = logging.getLogger(__name__)


class ReviewAssistantService:
    """Manage persisted reviewer-assistant sessions and bounded tool loops."""

    def __init__(
        self,
        assistant_store: FileSystemAssistantStore,
        job_store: FileSystemJobStore,
        review_store: FileSystemReviewStore,
    ) -> None:
        self._assistant_store = assistant_store
        self._job_store = job_store
        self._review_store = review_store
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._tools = {
            "get_job_summary": self._tool_get_job_summary,
            "get_artifact_manifest": self._tool_get_artifact_manifest,
            "get_pose_review": self._tool_get_pose_review,
            "get_retarget_review": self._tool_get_retarget_review,
            "get_pose_metrics": self._tool_get_pose_metrics,
            "get_retarget_metrics": self._tool_get_retarget_metrics,
            "get_static_checks": self._tool_get_static_checks,
        }

    def create_session(self, job_id: str, *, title: str | None = None) -> AssistantSessionSnapshot:
        """Create a new assistant session attached to a job."""
        return self._assistant_store.create_session(
            job_id,
            provider=settings.review_provider_name,
            sandbox=settings.review_sandbox_name,
            title=title,
            metadata={"execution_mode": settings.review_execution_mode},
        )

    def submit_user_message(self, job_id: str, session_id: str, content: str) -> None:
        """Persist a user message and start one bounded assistant loop."""
        self._assistant_store.append_message(
            AssistantMessage(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                session_id=session_id,
                role=AssistantMessageRole.USER,
                content=content,
            )
        )
        self._assistant_store.append_event(
            AssistantEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                session_id=session_id,
                event="message",
                payload={"role": AssistantMessageRole.USER.value, "content": content},
            )
        )
        key = (job_id, session_id)
        if key in self._tasks and not self._tasks[key].done():
            return
        task = asyncio.create_task(self._run_loop(job_id, session_id))
        self._tasks[key] = task

    async def _run_loop(self, job_id: str, session_id: str) -> None:
        """Run one bounded tool-using assistant turn loop."""
        self._assistant_store.update_session(
            job_id, session_id, status=AssistantSessionStatus.RUNNING
        )
        self._emit(job_id, session_id, "status", {"status": AssistantSessionStatus.RUNNING.value})
        try:
            for _ in range(settings.assistant_max_turns):
                transcript = self._assistant_store.list_messages(job_id, session_id)
                action = await self._choose_next_action(job_id, session_id, transcript)
                action_type = action.get("type")
                if action_type == "tool":
                    tool_name = str(action.get("tool_name", ""))
                    arguments = action.get("arguments") or {}
                    tool_result = self._execute_tool(job_id, tool_name, arguments)
                    tool_payload = json.dumps(tool_result, ensure_ascii=False, indent=2)
                    tool_message = AssistantMessage(
                        at=datetime.now(timezone.utc),
                        job_id=job_id,
                        session_id=session_id,
                        role=AssistantMessageRole.TOOL,
                        name=tool_name,
                        content=tool_payload,
                    )
                    self._assistant_store.append_message(tool_message)
                    self._emit(
                        job_id,
                        session_id,
                        "tool",
                        {
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "result": tool_result,
                        },
                    )
                    continue

                content = str(action.get("content", "")).strip()
                summary = str(action.get("summary", "")).strip()
                if not content:
                    content = (
                        "I could not determine a confident answer from the available artifacts."
                    )
                assistant_message = AssistantMessage(
                    at=datetime.now(timezone.utc),
                    job_id=job_id,
                    session_id=session_id,
                    role=AssistantMessageRole.ASSISTANT,
                    content=content,
                    metadata={"summary": summary} if summary else {},
                )
                self._assistant_store.append_message(assistant_message)
                self._stream_assistant_message(job_id, session_id, content)
                self._assistant_store.update_session(
                    job_id,
                    session_id,
                    status=AssistantSessionStatus.IDLE,
                    last_error=None,
                )
                self._emit(
                    job_id, session_id, "done", {"status": AssistantSessionStatus.IDLE.value}
                )
                return

            fallback_content = (
                "I reached the turn limit while investigating. Please ask a narrower question or "
                "inspect the latest tool outputs in this session."
            )
            self._assistant_store.append_message(
                AssistantMessage(
                    at=datetime.now(timezone.utc),
                    job_id=job_id,
                    session_id=session_id,
                    role=AssistantMessageRole.ASSISTANT,
                    content=fallback_content,
                )
            )
            self._stream_assistant_message(job_id, session_id, fallback_content)
            self._assistant_store.update_session(
                job_id, session_id, status=AssistantSessionStatus.IDLE
            )
            self._emit(job_id, session_id, "done", {"status": AssistantSessionStatus.IDLE.value})
        except Exception as exc:  # pragma: no cover - defensive async guard
            _logger.exception("Assistant session failed job=%s session=%s", job_id, session_id)
            self._assistant_store.update_session(
                job_id,
                session_id,
                status=AssistantSessionStatus.FAILED,
                last_error=str(exc),
            )
            self._emit(job_id, session_id, "error", {"detail": str(exc)})
            self._emit(job_id, session_id, "done", {"status": AssistantSessionStatus.FAILED.value})

    async def _choose_next_action(
        self,
        job_id: str,
        session_id: str,
        transcript: list[AssistantMessage],
    ) -> dict[str, Any]:
        """Choose either a tool call or a final answer for the next loop turn."""
        if settings.review_execution_mode == "featherless_daytona":
            return await self._external_action(job_id, session_id, transcript)
        return self._local_action(job_id, session_id, transcript)

    def _local_action(
        self,
        job_id: str,
        session_id: str,
        transcript: list[AssistantMessage],
    ) -> dict[str, Any]:
        """Deterministic local fallback for developer and demo environments."""
        tool_messages = [m for m in transcript if m.role == AssistantMessageRole.TOOL]
        if not tool_messages:
            return {"type": "tool", "tool_name": "get_job_summary", "arguments": {}}

        latest_user = next(
            (m for m in reversed(transcript) if m.role == AssistantMessageRole.USER), None
        )
        latest_tool = tool_messages[-1]
        content = (
            "Starting from the automated reviews, here is the most relevant evidence "
            "I found for your "
            f"question{': ' + latest_user.content if latest_user else ''}.\n\n"
            f"Tool `{latest_tool.name}` returned:\n{latest_tool.content}\n\n"
            "You can ask me to inspect pose metrics, retarget metrics, artifact "
            "manifests, or the stored stage reviews in more detail."
        )
        return {"type": "final", "content": content, "summary": "Local assistant summary"}

    async def _external_action(
        self,
        job_id: str,
        session_id: str,
        transcript: list[AssistantMessage],
    ) -> dict[str, Any]:
        """Use Featherless via Daytona to choose the next action in the loop."""
        prompt = self._build_assistant_prompt(job_id, transcript)
        sandbox_id = await self._create_daytona_sandbox()
        try:
            code = _build_daytona_assistant_program(prompt)
            return await self._run_daytona_code(sandbox_id, code)
        finally:
            await self._delete_daytona_sandbox(sandbox_id)

    def _build_assistant_prompt(self, job_id: str, transcript: list[AssistantMessage]) -> str:
        """Build a bounded prompt describing tools and recent transcript context."""
        compact_transcript = []
        for message in transcript[-settings.assistant_max_messages :]:
            compact_transcript.append(
                {
                    "role": message.role.value,
                    "name": message.name,
                    "content": message.content[:2000],
                }
            )
        transcript_json = json.dumps(compact_transcript, ensure_ascii=False, indent=2)
        if len(transcript_json) > settings.assistant_max_context_chars:
            raise RuntimeError("assistant_context_budget_exceeded")

        tool_schema = json.dumps(
            {
                "tools": {
                    name: {
                        "description": desc,
                        "arguments": {},
                    }
                    for name, desc in _tool_descriptions().items()
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        return (
            f"You are a robotics review assistant helping a human investigate job {job_id}.\n"
            "You may either choose one read-only tool call or provide a final assistant answer.\n"
            "Return strict JSON only.\n\n"
            "Allowed tool action schema:\n"
            '{"type":"tool","tool_name":"get_job_summary","arguments":{}}\n\n'
            "Allowed final action schema:\n"
            '{"type":"final","content":"...","summary":"..."}\n\n'
            "Available tools:\n"
            f"{tool_schema}\n\n"
            "Transcript:\n"
            f"{transcript_json}"
        )

    def _execute_tool(
        self, job_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute one read-only assistant tool."""
        try:
            tool = self._tools[tool_name]
        except KeyError as exc:
            raise RuntimeError(f"Unknown assistant tool '{tool_name}'") from exc
        return tool(job_id, arguments)

    def _tool_get_job_summary(self, job_id: str, _: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._job_store.get_job(job_id)
        result = snapshot.result or {}
        return {
            "job_id": job_id,
            "status": snapshot.status.value,
            "stage": snapshot.stage.value,
            "message": snapshot.message,
            "pose": result.get("pose"),
            "retarget": result.get("retarget"),
        }

    def _tool_get_artifact_manifest(self, job_id: str, _: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._job_store.get_job(job_id)
        result = snapshot.result or {}
        return result.get("artifacts", {})

    def _tool_get_pose_review(self, job_id: str, _: dict[str, Any]) -> dict[str, Any]:
        return self._read_review(job_id, ReviewStage.POSE)

    def _tool_get_retarget_review(self, job_id: str, _: dict[str, Any]) -> dict[str, Any]:
        return self._read_review(job_id, ReviewStage.RETARGET)

    def _tool_get_pose_metrics(self, job_id: str, _: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._job_store.get_job(job_id)
        result = snapshot.result or {}
        return result.get("pose", {}).get("metrics", {})

    def _tool_get_retarget_metrics(self, job_id: str, _: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._job_store.get_job(job_id)
        result = snapshot.result or {}
        return result.get("retarget", {}).get("evaluation", {})

    def _tool_get_static_checks(self, job_id: str, _: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._job_store.get_job(job_id)
        result = snapshot.result or {}
        return result.get("static_checks", {})

    def _read_review(self, job_id: str, stage: ReviewStage) -> dict[str, Any]:
        if not self._review_store.review_exists(job_id, stage):
            return {"status": "missing", "stage": stage.value}
        snapshot = self._review_store.get_review(job_id, stage)
        payload_path = Path(snapshot.json_path) if snapshot.json_path else None
        payload = None
        if payload_path and payload_path.exists():
            payload = json.loads(payload_path.read_text("utf-8"))
        return {
            "stage": stage.value,
            "status": snapshot.status.value,
            "verdict": snapshot.verdict.value if snapshot.verdict else None,
            "summary": snapshot.summary,
            "payload": payload,
        }

    async def _create_daytona_sandbox(self) -> str:
        headers = {
            "Authorization": f"Bearer {settings.daytona_api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {}
        if settings.daytona_project_id:
            body["projectId"] = settings.daytona_project_id
        async with httpx.AsyncClient(timeout=settings.review_timeout_seconds) as client:
            response = await client.post(
                f"{settings.daytona_base_url}/api/sandbox",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        sandbox_id = data.get("id") or data.get("sandboxId")
        if not sandbox_id:
            raise RuntimeError("Daytona sandbox creation returned no sandbox id")
        return str(sandbox_id)

    async def _run_daytona_code(self, sandbox_id: str, code: str) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        url = f"{settings.daytona_proxy_base_url}/toolbox/{sandbox_id}/process/code-run"
        async with httpx.AsyncClient(timeout=settings.review_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json={"code": code})
            response.raise_for_status()
            data = response.json()
        result_text = data.get("result", "")
        if not result_text:
            raise RuntimeError("Daytona code run returned no result payload")
        return json.loads(result_text)

    async def _delete_daytona_sandbox(self, sandbox_id: str) -> None:
        headers = {"Authorization": f"Bearer {settings.daytona_api_key}"}
        async with httpx.AsyncClient(timeout=settings.review_timeout_seconds) as client:
            try:
                await client.delete(
                    f"{settings.daytona_base_url}/api/sandbox/{sandbox_id}",
                    headers=headers,
                )
            except Exception:  # pragma: no cover
                _logger.warning("Failed to delete Daytona sandbox %s", sandbox_id)

    def _stream_assistant_message(self, job_id: str, session_id: str, content: str) -> None:
        self._emit(job_id, session_id, "section", {"name": "assistant"})
        for chunk in _chunk_text(content, settings.review_stream_chunk_chars):
            self._emit(job_id, session_id, "token", {"text": chunk})
        self._emit(job_id, session_id, "message", {"role": AssistantMessageRole.ASSISTANT.value})

    def _emit(self, job_id: str, session_id: str, event: str, payload: dict[str, Any]) -> None:
        self._assistant_store.append_event(
            AssistantEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                session_id=session_id,
                event=event,
                payload=payload,
            )
        )


def _tool_descriptions() -> dict[str, str]:
    return {
        "get_job_summary": (
            "Read the high-level job result summary, including pose and retarget branches."
        ),
        "get_artifact_manifest": (
            "Read the artifact manifest and download URLs for available outputs."
        ),
        "get_pose_review": ("Read the stored pose-stage review snapshot and payload if available."),
        "get_retarget_review": (
            "Read the stored retarget-stage review snapshot and payload if available."
        ),
        "get_pose_metrics": ("Read pose-stage metrics such as detection rate and keypoint jitter."),
        "get_retarget_metrics": ("Read retarget evaluation metrics such as jerk and sudden jumps."),
        "get_static_checks": (
            "Read static dataset verification checks for the robot dataset branch."
        ),
    }


def _chunk_text(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]
    return [text[i : i + width] for i in range(0, len(text), width)] or [""]


def _build_daytona_assistant_program(prompt: str) -> str:
    prompt_json = json.dumps(prompt)
    model_json = json.dumps(settings.review_model_name)
    api_key_json = json.dumps(settings.featherless_api_key)
    base_url_json = json.dumps(settings.featherless_base_url.rstrip("/"))
    timeout_json = json.dumps(settings.review_timeout_seconds)
    system_message_json = json.dumps(
        "You are a robotics review assistant. Return strict JSON only."
    )

    return f"""
import json
import urllib.request

PROMPT = {prompt_json}
MODEL = {model_json}
API_KEY = {api_key_json}
BASE_URL = {base_url_json}
TIMEOUT = {timeout_json}
SYSTEM_MESSAGE = {system_message_json}

body = json.dumps({{
    "model": MODEL,
    "messages": [
        {{"role": "system", "content": SYSTEM_MESSAGE}},
        {{"role": "user", "content": PROMPT}},
    ],
    "temperature": 0.2,
    "response_format": {{"type": "json_object"}},
}}).encode("utf-8")

request = urllib.request.Request(
    BASE_URL + "/v1/chat/completions",
    data=body,
    headers={{
        "Authorization": f"Bearer {{API_KEY}}",
        "Content-Type": "application/json",
    }},
    method="POST",
)

with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
    raw = json.loads(response.read().decode("utf-8"))

message = raw["choices"][0]["message"]["content"]
payload = json.loads(message)
print(json.dumps(payload))
"""
