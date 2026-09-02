"""
Public authentication routes — signup, login, refresh, logout.

Used by patients and doctors. SaaS Admin uses a separate endpoint.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new patient or doctor account",
    description=(
        "Creates a new user account. Patients are activated immediately. "
        "Doctors are set to 'pending' status and require admin approval. "
        "The 'saas_admin' role cannot be registered through this endpoint."
    ),
    responses={
        201: {"description": "Account created successfully"},
        403: {"description": "Forbidden — attempted saas_admin signup"},
        409: {"description": "Conflict — email already exists"},
        422: {"description": "Validation error — weak password or invalid email"},
    },
)
async def signup(
    data: SignupRequest,
    session: AsyncSession = Depends(get_db),
) -> SignupResponse:
    return await AuthService.signup(session, data)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate a patient or doctor",
    description=(
        "Authenticates a user and returns JWT access and refresh tokens. "
        "The user's role is determined from the database — the client does "
        "not specify the role. Accounts that are pending, rejected, or "
        "suspended cannot log in."
    ),
    responses={
        200: {"description": "Login successful"},
        401: {"description": "Invalid email or password"},
        403: {"description": "Account is not active"},
    },
)
async def login(
    data: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    return await AuthService.login(session, data)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    description=(
        "Exchanges a valid refresh token for a new access/refresh token pair. "
        "The old refresh token is revoked (rotation). Reuse of a revoked "
        "refresh token revokes all tokens for the user (theft detection)."
    ),
    responses={
        200: {"description": "Tokens refreshed successfully"},
        401: {"description": "Invalid or expired refresh token"},
    },
)
async def refresh(
    data: RefreshRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await AuthService.refresh(session, data)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout — revoke refresh token",
    description=(
        "Revokes the provided refresh token. The access token remains valid "
        "until its short expiration. This supports clean session termination."
    ),
    responses={
        200: {"description": "Logged out successfully"},
    },
)
async def logout(
    data: LogoutRequest,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await AuthService.logout(session, data)
    return MessageResponse(message="Logged out successfully")
