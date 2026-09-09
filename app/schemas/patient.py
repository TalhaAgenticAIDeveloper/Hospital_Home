"""
Pydantic schemas for Patient profile.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PatientProfileUpdateRequest(BaseModel):
    """Request schema for updating patient personal details."""

    full_name: Optional[str] = Field(None, max_length=255, description="Full legal name of the patient")
    age: Optional[int] = Field(None, ge=0, le=130, description="Patient age in years")
    date_of_birth: Optional[str] = Field(None, max_length=20, description="Date of birth (YYYY-MM-DD)")
    gender: Optional[str] = Field(None, max_length=20, description="Gender (e.g. Male, Female, Other)")
    blood_group: Optional[str] = Field(None, max_length=10, description="Blood group (e.g. A+, B+, O+, AB-)")
    address: Optional[str] = Field(None, max_length=500, description="Residential city or address")


class PatientProfileResponse(BaseModel):
    """Full patient profile response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    email: Optional[str] = None
    full_name: Optional[str] = None
    age: Optional[int] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    address: Optional[str] = None
    is_completed: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
