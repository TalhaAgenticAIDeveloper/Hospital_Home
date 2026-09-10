"""
Pydantic schemas for Doctor Availability, Booking, Meetings, and Transcripts.
"""

import uuid
from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import MeetingStatus
from app.schemas.patient_document import MeetingDocumentResponse


# ── Availability Schemas ─────────────────────────────────────────────────────

class AvailabilitySlotCreate(BaseModel):
    """Single availability time slot."""
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_times(self) -> "AvailabilitySlotCreate":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be strictly before end_time")
        return self


class AvailabilityBatchCreateRequest(BaseModel):
    """
    Generate slots for a given date and time window with a slot duration.
    Supports either (slot_date, start_time, end_time, timezone_offset_minutes)
    or direct ISO (start_datetime, end_datetime).
    """
    slot_date: Optional[date] = Field(None, description="Date for availability (YYYY-MM-DD)")
    start_time: Optional[time] = Field(None, description="Window start time (HH:MM)")
    end_time: Optional[time] = Field(None, description="Window end time (HH:MM)")
    start_datetime: Optional[datetime] = Field(None, description="Full ISO window start")
    end_datetime: Optional[datetime] = Field(None, description="Full ISO window end")
    slot_duration_minutes: int = Field(30, ge=15, le=120, description="Duration in minutes (15-120)")
    timezone_offset_minutes: Optional[int] = Field(0, description="Client timezone offset in minutes")

    @model_validator(mode="after")
    def validate_time_window(self) -> "AvailabilityBatchCreateRequest":
        if self.start_datetime and self.end_datetime:
            if self.start_datetime >= self.end_datetime:
                raise ValueError("start_datetime must be before end_datetime")
        elif self.start_time and self.end_time and self.slot_date:
            if self.start_time >= self.end_time:
                raise ValueError("start_time must be before end_time")
        else:
            raise ValueError("Either (start_datetime, end_datetime) or (slot_date, start_time, end_time) must be provided")
        return self


class AvailabilityResponse(BaseModel):
    """Response schema for doctor availability slot."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doctor_id: uuid.UUID
    start_time: datetime
    end_time: datetime
    is_booked: bool
    created_at: datetime


# ── Doctor Directory Schemas ─────────────────────────────────────────────────

class DoctorDirectoryItemResponse(BaseModel):
    """Doctor profile item displayed in patient booking directory."""
    doctor_id: uuid.UUID
    email: str
    full_name: Optional[str] = None
    specialization: Optional[str] = None
    qualification: Optional[str] = None
    years_of_experience: Optional[int] = None
    bio: Optional[str] = None
    available_slots_count: int = 0
    average_rating: Optional[float] = None
    total_ratings: int = 0



# ── Meeting Booking Schemas ──────────────────────────────────────────────────

class MeetingBookRequest(BaseModel):
    """Payload for patient to book a meeting slot."""
    doctor_id: uuid.UUID
    availability_id: Optional[uuid.UUID] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    patient_notes: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Reason for visit or symptoms (required)",
    )
    document_ids: Optional[List[uuid.UUID]] = Field(
        None,
        max_length=5,
        description="IDs of patient's uploaded medical documents to attach to this appointment",
    )

    @model_validator(mode="after")
    def validate_booking_target(self) -> "MeetingBookRequest":
        if not self.availability_id and (not self.start_time or not self.end_time):
            raise ValueError("Either availability_id or start_time/end_time must be provided")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class MeetingResponse(BaseModel):
    """Meeting details returned to authenticated doctor or patient."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    room_id: str
    doctor_id: uuid.UUID
    doctor_name: Optional[str] = None
    doctor_specialization: Optional[str] = None
    doctor_email: str
    patient_id: uuid.UUID
    patient_name: Optional[str] = None
    patient_email: str
    start_time: datetime
    end_time: datetime
    status: MeetingStatus
    patient_notes: Optional[str] = None
    doctor_notes: Optional[str] = None
    has_transcript: bool = False
    attached_documents: List[MeetingDocumentResponse] = []
    created_at: datetime


# ── Meeting Completion Schemas ─────────────────────────────────────────────

class MeetingEndRequest(BaseModel):
    """Payload when ending a consultation."""
    doctor_notes: Optional[str] = Field(None, max_length=5000, description="Optional clinical notes added by the doctor")
    segments: Optional[List[dict]] = Field(default_factory=list, description="Legacy field for backward compatibility")


class MeetingEndResponse(BaseModel):
    """Response returned when a consultation is completed."""
    meeting_id: uuid.UUID
    room_id: str
    doctor_notes: Optional[str] = None
    status: MeetingStatus
    completed_at: Optional[datetime] = None
    message: str = "Consultation completed successfully"


# Backwards compatibility aliases
MeetingEndAndSaveTranscriptRequest = MeetingEndRequest


class MeetingTranscriptResponse(BaseModel):
    """Legacy response for meeting completion."""
    meeting_id: uuid.UUID
    room_id: str
    transcript_text: Optional[str] = None
    doctor_notes: Optional[str] = None
    status: MeetingStatus
    completed_at: Optional[datetime] = None


class TranscriptSegment(BaseModel):
    """Legacy speech segment schema."""
    speaker: str = Field(..., description="'doctor' or 'patient'")
    speaker_name: str = Field(..., description="Display name of the speaker")
    text: str = Field(..., description="Text segment")
    timestamp: str = Field(..., description="Timestamp in HH:MM:SS format")
    language: Optional[str] = Field("en-US")


class SessionTranscriptSaveRequest(BaseModel):
    """Legacy schema for session saving."""
    session_number: int = Field(1)
    doctor_notes: Optional[str] = None
    segments: Optional[List[dict]] = Field(default_factory=list)


class SessionTranscriptResponse(BaseModel):
    """Legacy schema for session saving."""
    meeting_id: uuid.UUID
    session_number: int
    transcript_path: str = ""
    message: str = "Session completed"


# ── Weekly Schedule Schemas ──────────────────────────────────────────────────

class WeeklyScheduleSlot(BaseModel):
    """A single day's availability in the weekly template."""
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday, 1=Tuesday, ..., 6=Sunday")
    start_time: str = Field(..., description="Start time in HH:MM format (24h)")
    end_time: str = Field(..., description="End time in HH:MM format (24h)")
    slot_duration_minutes: int = Field(30, ge=15, le=120, description="Duration in minutes (15-120)")
    is_active: bool = Field(True, description="Whether this day is enabled")

    @model_validator(mode="after")
    def validate_times(self) -> "WeeklyScheduleSlot":
        from datetime import time as dt_time
        try:
            h1, m1 = map(int, self.start_time.split(":"))
            h2, m2 = map(int, self.end_time.split(":"))
            t1 = dt_time(h1, m1)
            t2 = dt_time(h2, m2)
        except (ValueError, IndexError):
            raise ValueError("Times must be in HH:MM format")
        if t1 >= t2:
            raise ValueError("start_time must be before end_time")
        return self


class WeeklyScheduleSaveRequest(BaseModel):
    """Save the full weekly schedule template (replaces existing)."""
    schedule: List[WeeklyScheduleSlot] = Field(..., description="List of day schedules (active days only)")


class WeeklyScheduleResponse(BaseModel):
    """Response for a single day's schedule."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doctor_id: uuid.UUID
    day_of_week: int
    start_time: str
    end_time: str
    slot_duration_minutes: int
    is_active: bool


class GenerateWeekSlotsRequest(BaseModel):
    """Trigger bulk slot generation from saved weekly template."""
    weeks_ahead: int = Field(1, ge=1, le=4, description="Number of weeks ahead to generate slots for (1-4)")
    timezone_offset_minutes: int = Field(0, description="Client timezone offset in minutes (e.g. -300 for UTC+5)")

