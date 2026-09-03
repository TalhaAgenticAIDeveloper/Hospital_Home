"""
Pydantic schemas for Doctor profile, document management, and application status.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentType


class DoctorProfileUpdateRequest(BaseModel):
    """Request schema for updating doctor professional information."""

    full_name: str = Field(..., min_length=2, max_length=255, description="Full name of the doctor")
    phone_number: str = Field(..., min_length=5, max_length=20, description="Contact phone number")
    specialization: str = Field(..., min_length=2, max_length=255, description="Medical specialization")
    license_number: str = Field(..., min_length=2, max_length=100, description="Medical registration/license number")
    years_of_experience: int = Field(..., ge=0, le=70, description="Years of professional experience")
    qualification: str = Field(..., min_length=2, max_length=500, description="Educational and medical qualifications")
    bio: Optional[str] = Field(None, max_length=2000, description="Professional biography / summary")


class DoctorDocumentResponse(BaseModel):
    """Schema representing an uploaded doctor document."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_type: DocumentType
    original_filename: str
    file_size: int
    mime_type: str
    created_at: datetime


class DoctorProfileResponse(BaseModel):
    """Full doctor profile response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    email: Optional[str] = None
    status: Optional[str] = None
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    specialization: Optional[str] = None
    license_number: Optional[str] = None
    years_of_experience: Optional[int] = None
    qualification: Optional[str] = None
    bio: Optional[str] = None
    submitted_at: Optional[datetime] = None
    admin_feedback: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    documents: List[DoctorDocumentResponse] = []


class DoctorApplicationStatusResponse(BaseModel):
    """Summary of doctor application status and admin feedback."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    admin_feedback: Optional[str] = None
    message: Optional[str] = None
