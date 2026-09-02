"""
Pydantic v2 schemas for user responses.

These schemas control what user data is exposed in API responses.
Sensitive fields (password_hash, etc.) are never included.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class UserResponse(BaseModel):
    """Safe user representation for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    role: str
    status: str
    created_at: datetime


class UserBriefResponse(BaseModel):
    """Minimal user info returned with auth tokens."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    role: str
