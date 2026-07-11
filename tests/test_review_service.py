"""Tests for async review orchestration and provider-mode selection."""

from __future__ import annotations

import pytest

from backend.config import settings
from backend.review_service import ReviewService
from backend.review_store import FileSystemReviewStore
from domain.enums import ReviewStage, ReviewStatus, ReviewVerdict


@pytest.mark.asyncio
async def test_review_service_uses_local_fallback_by_default(tmp_path, monkeypatch):
    """Without provider credentials, the local review factory should be used."""
    store = FileSystemReviewStore(tmp_path)
    service = ReviewService(store)

    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "featherless_api_key", None)

    def builder(_emit):
        return lambda: ("# Local Review", {"verdict": "approved", "summary": "ok"})

    service.schedule_review(
        job_id="job-local",
        stage=ReviewStage.POSE,
        context_manifest={"metrics": {"detection_rate": 1.0}},
        review_factory_builder=builder,
    )

    await service._tasks[("job-local", ReviewStage.POSE.value)]
    review = store.get_review("job-local", ReviewStage.POSE)
    assert review.status == ReviewStatus.COMPLETED
    assert review.verdict == ReviewVerdict.APPROVED
    assert review.summary == "ok"


@pytest.mark.asyncio
async def test_review_service_uses_external_mode_when_credentials_exist(tmp_path, monkeypatch):
    """When provider credentials are present, the external review path should be called."""
    store = FileSystemReviewStore(tmp_path)
    service = ReviewService(store)

    monkeypatch.setattr(settings, "llm_api_key", "test-provider-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://example.test")
    monkeypatch.setattr(settings, "llm_model_name", "deepseek-v4-pro")

    async def fake_external(job_id, stage, context_manifest):
        assert job_id == "job-external"
        assert stage == ReviewStage.RETARGET
        assert context_manifest["metrics"]["joint_limit_violations"] == 0
        return "# External Review", {"verdict": "needs_review", "summary": "external"}

    monkeypatch.setattr(service, "_run_external_review", fake_external)

    def builder(_emit):
        return lambda: (_ for _ in ()).throw(AssertionError("fallback should not run"))

    service.schedule_review(
        job_id="job-external",
        stage=ReviewStage.RETARGET,
        context_manifest={"metrics": {"joint_limit_violations": 0}},
        review_factory_builder=builder,
    )

    await service._tasks[("job-external", ReviewStage.RETARGET.value)]
    review = store.get_review("job-external", ReviewStage.RETARGET)
    assert review.status == ReviewStatus.COMPLETED
    assert review.verdict == ReviewVerdict.NEEDS_REVIEW
    assert review.summary == "external"


@pytest.mark.asyncio
async def test_review_service_falls_back_locally_when_external_review_raises(tmp_path, monkeypatch):
    """External review failures should degrade to the deterministic local review path."""
    store = FileSystemReviewStore(tmp_path)
    service = ReviewService(store)

    monkeypatch.setattr(settings, "llm_api_key", "test-provider-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://example.test")
    monkeypatch.setattr(settings, "llm_model_name", "deepseek-v4-pro")

    async def fake_external(job_id, stage, context_manifest):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_run_external_review", fake_external)

    def builder(_emit):
        return lambda: ("# Local Review", {"verdict": "rejected", "summary": "fallback"})

    service.schedule_review(
        job_id="job-fail",
        stage=ReviewStage.POSE,
        context_manifest={"metrics": {"detection_rate": 0.2}},
        review_factory_builder=builder,
    )

    await service._tasks[("job-fail", ReviewStage.POSE.value)]
    review = store.get_review("job-fail", ReviewStage.POSE)
    assert review.status == ReviewStatus.COMPLETED
    assert review.verdict == ReviewVerdict.REJECTED
    assert review.summary == "fallback"
    assert review.error is None


def test_review_execution_mode_property(monkeypatch):
    """Review execution mode should switch when a provider key exists."""
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "featherless_api_key", None)
    assert settings.review_execution_mode == "local_fallback"

    monkeypatch.setattr(settings, "llm_api_key", "provider-key")
    assert settings.review_execution_mode == "openai_compatible"
