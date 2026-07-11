"""Pydantic models for authentication and session identity.

These models represent the identity carried in JWT claims and extracted by
FastAPI dependencies. They are shared between backend (auth.py, dependencies.py)
and pipeline (which may need session context for audit logging).
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from domain.enums import UserRole


class SessionIdentity(BaseModel):
    """The resolved identity of an authenticated caller.

    Extracted from a validated JWT by FastAPI dependency injection. Carries the
    caller's role and, for judge sessions, the anonymous session identifier
    that scopes all job operations.

    Validation rules:
        - JUDGE role requires a non-empty judge_session_id.
        - ADMIN role allows judge_session_id to be None (admin actions are not
          scoped to a single judge session).
        - token_version supports future key rotation; currently always 1.
    """

    role: UserRole
    judge_session_id: str | None
    token_version: int = 1

    @model_validator(mode="after")
    def _enforce_judge_session_id(self) -> SessionIdentity:
        if self.role == UserRole.JUDGE and not self.judge_session_id:
            raise ValueError("judge_session_id is required for JUDGE role")
        return self

    @property
    def is_admin(self) -> bool:
        """Return True if the caller has the ADMIN role."""
        return self.role == UserRole.ADMIN

    @property
    def is_judge(self) -> bool:
        """Return True if the caller has the JUDGE role."""
        return self.role == UserRole.JUDGE
