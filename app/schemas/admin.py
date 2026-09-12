"""
Pydantic schemas for SaaS Admin review actions and pending doctor listings.
"""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DoctorReviewRequest(BaseModel):
    """
    Schema for reviewing a doctor application.

    Feedback is required when action is 'reject'.
    """

    action: Literal["approve", "reject"] = Field(
        ..., description="Review action: 'approve' or 'reject'"
    )
    feedback: Optional[str] = Field(
        None,
        max_length=2000,
        description="Feedback or reason for rejection/approval. Required when rejecting.",
    )

    @model_validator(mode="after")
    def validate_feedback_on_rejection(self) -> "DoctorReviewRequest":
        if self.action == "reject":
            if not self.feedback or not self.feedback.strip():
                raise ValueError("Feedback is mandatory when rejecting a doctor application.")
        return self


class DoctorReviewResponse(BaseModel):
    """Response returned after reviewing a doctor application."""

    message: str
    doctor_id: UUID
    user_id: UUID
    new_status: str
    admin_feedback: Optional[str] = None
    reviewed_at: datetime


class PendingDoctorListItem(BaseModel):
    """Brief doctor information for admin pending queue."""

    model_config = ConfigDict(from_attributes=True)

    doctor_id: UUID
    user_id: UUID
    email: str
    full_name: Optional[str] = None
    father_name: Optional[str] = None
    pmdc_registration_number: Optional[str] = None
    specialization: Optional[str] = None
    license_number: Optional[str] = None
    years_of_experience: Optional[int] = None
    submitted_at: Optional[datetime] = None
    status: str
    document_count: int = 0


class PendingDoctorListResponse(BaseModel):
    """List response of pending doctor applications."""

    total: int
    items: List[PendingDoctorListItem]


class DoctorStatusCounts(BaseModel):
    total: int = 0
    pending: int = 0
    active: int = 0
    rejected: int = 0


class AllDoctorsListResponse(BaseModel):
    """List response of all doctors with counts breakdown."""

    total: int
    counts: DoctorStatusCounts
    status_counts: Optional[DoctorStatusCounts] = None
    items: List[PendingDoctorListItem]


class PendingDoctorDetailResponse(BaseModel):
    """Detailed doctor profile for admin review."""

    model_config = ConfigDict(from_attributes=True)

    doctor_id: UUID
    user_id: UUID
    email: str
    status: str
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
    documents: List = []


class PatientAdminListItem(BaseModel):
    """Patient summary for SaaS Admin management."""

    model_config = ConfigDict(from_attributes=True)

    patient_id: Optional[UUID] = None
    user_id: UUID
    email: str
    status: str
    is_active: bool
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    age: Optional[int] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    address: Optional[str] = None
    created_at: Optional[datetime] = None
    consultations_count: int = 0
    documents_count: int = 0


class PatientAdminListResponse(BaseModel):
    """Paginated list of patients for SaaS Admin."""

    total: int
    items: List[PatientAdminListItem]

