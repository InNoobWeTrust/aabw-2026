"""Smoke tests for authentication and session identity behavior."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend import auth
from domain.auth import SessionIdentity
from domain.enums import UserRole


def test_session_identity_requires_judge_session_id_for_judge_role() -> None:
    """Judge identities must carry a non-empty judge_session_id."""
    with pytest.raises(ValueError, match="judge_session_id"):
        SessionIdentity(role=UserRole.JUDGE, judge_session_id=None)


def test_session_identity_allows_admin_without_judge_session_id() -> None:
    """Admin identities are global and do not require a judge_session_id."""
    identity = SessionIdentity(role=UserRole.ADMIN, judge_session_id=None)
    assert identity.is_admin is True
    assert identity.is_judge is False


def test_authenticate_password_returns_admin_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin password should resolve to ADMIN role without a judge session id."""
    monkeypatch.setattr(auth.settings, "admin_access_password", "admin-secret")
    monkeypatch.setattr(auth.settings, "judge_access_password", "judge-secret")
    monkeypatch.setattr(auth.settings, "access_password", None)

    identity = auth.authenticate_password("admin-secret")

    assert identity.role == UserRole.ADMIN
    assert identity.judge_session_id is None


def test_authenticate_password_returns_judge_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Judge password should resolve to JUDGE role with a generated session id."""
    monkeypatch.setattr(auth.settings, "admin_access_password", "admin-secret")
    monkeypatch.setattr(auth.settings, "judge_access_password", "judge-secret")
    monkeypatch.setattr(auth.settings, "access_password", None)

    identity = auth.authenticate_password("judge-secret")

    assert identity.role == UserRole.JUDGE
    assert isinstance(identity.judge_session_id, str)
    assert identity.judge_session_id


def test_authenticate_password_rejects_invalid_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown passwords should raise HTTP 401."""
    monkeypatch.setattr(auth.settings, "admin_access_password", "admin-secret")
    monkeypatch.setattr(auth.settings, "judge_access_password", "judge-secret")
    monkeypatch.setattr(auth.settings, "access_password", None)

    with pytest.raises(HTTPException) as exc_info:
        auth.authenticate_password("wrong-secret")

    assert exc_info.value.status_code == 401
