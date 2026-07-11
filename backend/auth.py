"""Authentication: password verification, JWT token creation, and FastAPI dependencies.

All password comparison uses constant-time hmac.compare_digest. Never use ==
for password or token comparison in this module.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from backend.config import settings
from domain.auth import SessionIdentity
from domain.enums import UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

TOKEN_VERSION = 1


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison using hmac.compare_digest."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def authenticate_password(password: str) -> SessionIdentity:
    """Authenticate a password against configured judge and admin credentials.

    Comparison order (admin first, then judge) is intentional:
      - If the admin password is configured and matches, return an ADMIN identity.
      - Otherwise, if the judge password matches, return a JUDGE identity with
        a freshly generated anonymous judge_session_id.
      - If neither matches, raise HTTP 401.

    Args:
        password: The plain-text password from the login request.

    Returns:
        A SessionIdentity with the resolved role and session identifier.

    Raises:
        HTTPException(401): If the password matches neither credential.
    """
    if settings.has_admin_password and _constant_time_compare(
        password,
        settings.admin_access_password,  # type: ignore[arg-type]
    ):
        return SessionIdentity(role=UserRole.ADMIN, judge_session_id=None)

    if _constant_time_compare(password, settings.effective_judge_password):
        return SessionIdentity(role=UserRole.JUDGE, judge_session_id=uuid4().hex)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid password",
    )


def create_access_token(identity: SessionIdentity) -> str:
    """Create a signed JWT for an authenticated session identity.

    The token payload carries role, judge_session_id (if judge), a
    token_version for future key rotation, and an expiration timestamp.

    Args:
        identity: The resolved SessionIdentity from authentication.

    Returns:
        An HS256-signed JWT string.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours)
    payload: dict[str, object] = {
        "role": identity.role.value,
        "judge_session_id": identity.judge_session_id,
        "token_version": TOKEN_VERSION,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def _decode_token(token: str) -> dict[str, object]:
    """Decode and validate a JWT, returning its claims payload.

    Args:
        token: The raw JWT string from the Authorization header.

    Returns:
        The decoded claims dict.

    Raises:
        HTTPException(401): If the token is expired, malformed, or invalid.
    """
    try:
        return jwt.decode(  # type: ignore[no-any-return]
            token, settings.jwt_secret_key, algorithms=["HS256"]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None


def _parse_identity(payload: dict[str, object]) -> SessionIdentity:
    """Parse a decoded JWT payload into a SessionIdentity model.

    Args:
        payload: The decoded JWT claims dict.

    Returns:
        A validated SessionIdentity.

    Raises:
        HTTPException(401): If required claims are missing or malformed.
    """
    role_raw = payload.get("role")
    if not isinstance(role_raw, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing valid 'role' claim",
        )

    try:
        role = UserRole(role_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unknown role: {role_raw}",
        ) from None

    session_id_raw = payload.get("judge_session_id")
    judge_session_id: str | None = str(session_id_raw) if isinstance(session_id_raw, str) else None

    try:
        return SessionIdentity(
            role=role,
            judge_session_id=judge_session_id,
            token_version=int(payload.get("token_version", 1)),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from None


def get_current_identity(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> SessionIdentity:
    """FastAPI dependency: decode the Bearer token and return a SessionIdentity.

    This is the foundational auth dependency. All protected routes should use
    this directly (for any-authenticated-user endpoints) or compose it through
    require_admin_identity.

    Args:
        token: The Bearer token extracted from the Authorization header.

    Returns:
        The resolved SessionIdentity for the caller.
    """
    payload = _decode_token(token)
    return _parse_identity(payload)


def require_authenticated_identity(
    identity: Annotated[SessionIdentity, Depends(get_current_identity)],
) -> SessionIdentity:
    """FastAPI dependency: require any valid authenticated identity.

    Use this for endpoints accessible to both JUDGE and ADMIN roles
    (e.g., job upload, status polling).

    Args:
        identity: The resolved session identity.

    Returns:
        The caller's SessionIdentity.
    """
    return identity


def require_authenticated_identity_optional_query(
    request: Request,
) -> SessionIdentity:
    """FastAPI dependency: require any valid authenticated identity, allowing query param token.

    Useful for media streams where custom headers cannot be sent (e.g. video tags).
    """
    from fastapi.security.utils import get_authorization_scheme_param

    # Check Authorization header first
    authorization = request.headers.get("Authorization")
    if authorization:
        scheme, token = get_authorization_scheme_param(authorization)
        if scheme.lower() == "bearer":
            payload = _decode_token(token)
            return _parse_identity(payload)

    # Check query parameter next
    token = request.query_params.get("token")
    if token:
        payload = _decode_token(token)
        return _parse_identity(payload)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_admin_identity(
    identity: Annotated[SessionIdentity, Depends(get_current_identity)],
) -> SessionIdentity:
    """FastAPI dependency: require an ADMIN role identity.

    Use this for admin-only endpoints (global job listing, management).
    Returns 403 if the caller is not an admin.

    Args:
        identity: The resolved session identity.

    Returns:
        The caller's SessionIdentity (guaranteed ADMIN role).

    Raises:
        HTTPException(403): If the caller does not have the ADMIN role.
    """
    if not identity.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return identity
