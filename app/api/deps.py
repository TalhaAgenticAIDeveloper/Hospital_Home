"""
FastAPI dependencies for authentication and authorization.

Reusable dependencies for:
- Database sessions
- Current authenticated user extraction from JWT
- Role-based access control (RBAC)
- Doctor onboarding access control
"""

import uuid
from typing import List

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import (
    AccountInactiveError,
    AuthenticationError,
    AuthorizationError,
)
from app.core.security import decode_token
from app.models.enums import UserRole, UserStatus
from app.models.saas_admin import SaaSAdmin
from app.models.user import User
from app.repositories.saas_admin_repository import SaaSAdminRepository
from app.repositories.user_repository import UserRepository

# ── Bearer Token Scheme ──────────────────────────────────────────────────────
security_scheme = HTTPBearer(
    scheme_name="Bearer",
    description="JWT access token",
    auto_error=False,
)


# ── Base User Dependency ─────────────────────────────────────────────────────

async def get_current_user_any_status(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    session: AsyncSession = Depends(get_db),
) -> User | SaaSAdmin:
    """
    Extract and validate user or admin from JWT token regardless of approval status.

    Checks:
    1. Token exists and is valid
    2. Token type is "access" (not refresh)
    3. User/Admin exists in database
    4. Account is active (is_active == True)
    5. Account is not SUSPENDED

    Returns:
        The authenticated User or SaaSAdmin model instance.
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

    # SaaS Admin token resolution
    if payload.get("role") == UserRole.SAAS_ADMIN.value:
        admin = await SaaSAdminRepository.get_by_id(session, user_id)
        if not admin:
            raise AuthenticationError(detail="SaaS Admin not found")
        if not admin.is_active:
            raise AccountInactiveError(detail="SaaS Admin account is deactivated")
        return admin

    # Regular User (Patient / Doctor) resolution
    user = await UserRepository.get_by_id(session, user_id)
    if not user:
        raise AuthenticationError(detail="User not found")

    # Suspended or disabled accounts are immediately blocked
    if not user.is_active or user.status == UserStatus.SUSPENDED:
        raise AccountInactiveError(detail="Account is suspended or deactivated")

    return user


# ── Active User Dependency ───────────────────────────────────────────────────

async def get_current_user(
    user: User | SaaSAdmin = Depends(get_current_user_any_status),
) -> User | SaaSAdmin:
    """
    Extract current user and ensure status is ACTIVE.

    Used for general platform and dashboard endpoints.
    """
    if user.status != UserStatus.ACTIVE:
        if user.status == UserStatus.PENDING:
            raise AccountInactiveError(
                detail="Account is pending approval. Please complete your profile and verification."
            )
        elif user.status == UserStatus.REJECTED:
            raise AccountInactiveError(
                detail="Account application was rejected. Please review feedback in your profile."
            )
        else:
            raise AccountInactiveError(detail="Account is not active")

    return user


# ── Dedicated SaaS Admin Dependency ──────────────────────────────────────────

async def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    session: AsyncSession = Depends(get_db),
) -> SaaSAdmin:
    """
    Extract and validate the authenticated SaaS Admin from JWT.

    Strictly requires role='saas_admin' and an active record in saas_admins table.
    """
    if credentials is None:
        raise AuthenticationError(detail="Authentication required")

    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise AuthenticationError(detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise AuthenticationError(detail="Invalid token type")

    if payload.get("role") != UserRole.SAAS_ADMIN.value:
        raise AuthorizationError(detail="Only SaaS Admin can access this resource")

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise AuthenticationError(detail="Invalid token payload")

    try:
        admin_id = uuid.UUID(user_id_str)
    except ValueError:
        raise AuthenticationError(detail="Invalid token payload")

    admin = await SaaSAdminRepository.get_by_id(session, admin_id)
    if not admin:
        raise AuthenticationError(detail="SaaS Admin not found")

    if not admin.is_active:
        raise AccountInactiveError(detail="SaaS Admin account is deactivated")

    return admin


# ── Doctor Specific Dependencies ─────────────────────────────────────────────

async def get_current_doctor(
    user: User | SaaSAdmin = Depends(get_current_user_any_status),
) -> User:
    """
    Dependency for doctor onboarding endpoints (profile, document upload, status).

    Allows PENDING, REJECTED, and ACTIVE doctors to access onboarding features.
    """
    if user.role != UserRole.DOCTOR or not isinstance(user, User):
        raise AuthorizationError(detail="Only doctors can access this resource")
    return user


async def get_current_active_doctor(
    user: User | SaaSAdmin = Depends(get_current_user),
) -> User:
    """
    Dependency for doctor clinical / dashboard endpoints.

    Requires role=DOCTOR and status=ACTIVE.
    """
    if user.role != UserRole.DOCTOR or not isinstance(user, User):
        raise AuthorizationError(detail="Only active doctors can access this resource")
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

    async def __call__(self, user: User | SaaSAdmin = Depends(get_current_user)) -> User | SaaSAdmin:
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

