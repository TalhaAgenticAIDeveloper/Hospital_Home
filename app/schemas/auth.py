"""
Pydantic v2 schemas for authentication requests and responses.

Password validation is centralized here so policy changes apply everywhere.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.core.config import get_settings
from app.schemas.user import UserBriefResponse

settings = get_settings()


# ── Request Schemas ──────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    """Patient or Doctor signup request."""

    email: EmailStr
    password: str
    role: Literal["patient", "doctor"]

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """
        Centralized password policy validation.

        Rules:
        - Minimum length (default 8)
        - Maximum length (default 128)
        - At least one uppercase letter
        - At least one lowercase letter
        - At least one digit
        - At least one special character
        """
        if len(v) < settings.PASSWORD_MIN_LENGTH:
            raise ValueError(
                f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters"
            )
        if len(v) > settings.PASSWORD_MAX_LENGTH:
            raise ValueError(
                f"Password must be at most {settings.PASSWORD_MAX_LENGTH} characters"
            )
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?`~]", v):
            raise ValueError("Password must contain at least one special character")
        return v


class LoginRequest(BaseModel):
    """Patient or Doctor login request. Role is NOT specified by the client."""

    email: EmailStr
    password: str


class AdminLoginRequest(BaseModel):
    """SaaS Admin login request."""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Logout request — revokes the refresh token."""

    refresh_token: str


# ── Response Schemas ─────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """JWT token pair response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    """Login response with tokens and user info."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserBriefResponse


class SignupResponse(BaseModel):
    """Signup success response."""

    message: str
    user: UserBriefResponse


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
