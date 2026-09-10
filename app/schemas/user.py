"""
Pydantic v2 schemas for user responses.

These schemas control what user data is exposed in API responses.
Sensitive fields (password_hash, etc.) are never included.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.doctor import DoctorProfileResponse
from app.schemas.patient import PatientProfileResponse


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
    status: Optional[str] = None


class MeResponse(BaseModel):
    """Current authenticated user profile details."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    role: str
    status: str
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None
    doctor_profile: Optional[DoctorProfileResponse] = None
    patient_profile: Optional[PatientProfileResponse] = None
