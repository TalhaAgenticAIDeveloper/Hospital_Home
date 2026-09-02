"""
FastAPI dependencies for authentication and authorization.

Reusable dependencies for:
- Database sessions
- Current authenticated user extraction from JWT
- Role-based access control (RBAC)
"""

import uuid
from typing import List

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AuthenticationError, AuthorizationError, AccountInactiveError
from app.core.security import decode_token
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.repositories.user_repository import UserRepository

# ── Bearer Token Scheme ──────────────────────────────────────────────────────
security_scheme = HTTPBearer(
    scheme_name="Bearer",
    description="JWT access token",
    auto_error=False,
)


# ── Get Current User ─────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract and validate the current user from the Bearer token.

    Checks:
    1. Token exists and is valid
    2. Token type is "access" (not refresh)
    3. User exists in database
    4. Account is active
    5. Account status is active

    Returns:
        The authenticated User model instance.

    Raises:
        AuthenticationError: If token is missing, invalid, or expired.
        AccountInactiveError: If account is not active.
    """
    if credentials is None:
        raise AuthenticationError(detail="Authentication required")

    token = credentials.credentials

    try:
        payload = decode_token(token)
    except JWTError:
        raise AuthenticationError(detail="Invalid or expired token")

    # Must be an access token
    if payload.get("type") != "access":
        raise AuthenticationError(detail="Invalid token type")

    # Extract user ID
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise AuthenticationError(detail="Invalid token payload")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise AuthenticationError(detail="Invalid token payload")

    # Fetch user from database
    user = await UserRepository.get_by_id(session, user_id)
    if not user:
        raise AuthenticationError(detail="User not found")

    # Check account is active
    if not user.is_active:
        raise AccountInactiveError(detail="Account is not active")

    if user.status != UserStatus.ACTIVE:
        raise AccountInactiveError(detail="Account is not active")

    return user


# ── Role-Based Access Control ────────────────────────────────────────────────

class RoleChecker:
    """
    Dependency factory for role-based access control.

    Usage:
        @router.get("/admin-only", dependencies=[Depends(RoleChecker([UserRole.SAAS_ADMIN]))])
        async def admin_endpoint():
            ...

    Or as a parameter dependency:
        async def my_endpoint(user: User = Depends(RoleChecker([UserRole.DOCTOR]))):
            ...
    """

    def __init__(self, allowed_roles: List[UserRole]):
        self.allowed_roles = allowed_roles

    async def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role not in self.allowed_roles:
            raise AuthorizationError(
                detail="You do not have permission to access this resource"
            )
        return user


def require_role(*roles: UserRole):
    """
    Convenience function for creating role-checking dependencies.

    Usage:
        Depends(require_role(UserRole.DOCTOR))
        Depends(require_role(UserRole.SAAS_ADMIN, UserRole.DOCTOR))
    """
    return RoleChecker(list(roles))
