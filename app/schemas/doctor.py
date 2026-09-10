"""
Pydantic schemas for Doctor profile and application status.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DoctorProfileUpdateRequest(BaseModel):
    """Request schema for updating doctor professional information."""

    full_name: str = Field(..., min_length=2, max_length=255, description="Full name of the doctor")
    father_name: str = Field(..., min_length=2, max_length=255, description="Father's name of the doctor")
    pmdc_registration_number: str = Field(..., min_length=2, max_length=100, description="PMDC registration number")
    phone_number: Optional[str] = Field(None, max_length=20, description="Contact phone number")
    specialization: Optional[str] = Field(None, max_length=255, description="Medical specialization")
    license_number: Optional[str] = Field(None, max_length=100, description="Medical registration/license number")
    years_of_experience: Optional[int] = Field(None, ge=0, le=70, description="Years of professional experience")
    qualification: Optional[str] = Field(None, max_length=500, description="Educational and medical qualifications")
    bio: Optional[str] = Field(None, max_length=2000, description="Professional biography / summary")


class DoctorProfileResponse(BaseModel):
    """Full doctor profile response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    email: Optional[str] = None
    status: Optional[str] = None
    full_name: Optional[str] = None
    father_name: Optional[str] = None
    pmdc_registration_number: Optional[str] = None
    phone_number: Optional[str] = None
    specialization: Optional[str] = None
    license_number: Optional[str] = None
    years_of_experience: Optional[int] = None
    qualification: Optional[str] = None
    bio: Optional[str] = None
    submitted_at: Optional[datetime] = None
    admin_feedback: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class DoctorApplicationStatusResponse(BaseModel):
    """Summary of doctor application status and admin feedback."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    admin_feedback: Optional[str] = None
    message: Optional[str] = None
