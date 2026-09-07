"""
Pydantic schemas for Doctor Availability, Booking, Meetings, and Transcripts.
"""

import uuid
from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import MeetingStatus


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


# ── Meeting Booking Schemas ──────────────────────────────────────────────────

class MeetingBookRequest(BaseModel):
    """Payload for patient to book a meeting slot."""
    doctor_id: uuid.UUID
    availability_id: Optional[uuid.UUID] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    patient_notes: Optional[str] = Field(None, max_length=2000, description="Reason for visit or symptoms")

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
    created_at: datetime


# ── Transcript Schemas ───────────────────────────────────────────────────────

class TranscriptSegment(BaseModel):
    """A speech segment spoken by a participant in English or Urdu."""
    speaker: str = Field(..., description="'doctor' or 'patient'")
    speaker_name: str = Field(..., description="Display name of the speaker")
    text: str = Field(..., description="Transcribed text in Urdu or English")
    timestamp: str = Field(..., description="Timestamp in HH:MM:SS format")
    language: Optional[str] = Field("en-US", description="'ur-PK' or 'en-US'")


class MeetingEndAndSaveTranscriptRequest(BaseModel):
    """Payload when completing a meeting and saving transcript."""
    doctor_notes: Optional[str] = Field(None, max_length=5000)
    segments: Optional[List[TranscriptSegment]] = Field(default_factory=list)


class MeetingTranscriptResponse(BaseModel):
    """Meeting transcript content."""
    meeting_id: uuid.UUID
    room_id: str
    transcript_text: Optional[str] = None
    doctor_notes: Optional[str] = None
    status: MeetingStatus
    completed_at: Optional[datetime] = None


class SessionTranscriptSaveRequest(BaseModel):
    """Payload for saving a per-session transcript (one join/leave cycle)."""
    session_number: int = Field(..., ge=1, description="Session number (1, 2, 3...)")
    doctor_notes: Optional[str] = Field(None, max_length=5000)
    segments: Optional[List[TranscriptSegment]] = Field(default_factory=list)


class SessionTranscriptResponse(BaseModel):
    """Response after saving a session transcript."""
    meeting_id: uuid.UUID
    session_number: int
    transcript_path: str
    message: str

