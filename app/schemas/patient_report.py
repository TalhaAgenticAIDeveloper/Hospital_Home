"""
Pydantic schemas for Patient Medical Report Explainer.

Defines schemas for report upload, session listing, session details,
and follow-up chat conversations.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PatientReportMessageResponse(BaseModel):
    """Schema for an individual chat message in a report explainer session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    is_report_summary: bool = False
    created_at: datetime


class PatientReportSessionSummary(BaseModel):
    """Compact summary of a patient's report session for listing in sidebar."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    extraction_method: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class PatientReportSessionDetail(BaseModel):
    """Full detail of a report session including extracted text and chat history."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    filename: str
    extraction_method: str
    report_text: str
    report_explanation: str
    created_at: datetime
    updated_at: datetime
    messages: List[PatientReportMessageResponse] = []


class PatientReportUploadResponse(BaseModel):
    """Response returned upon successful report upload and analysis."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = True
    session_id: uuid.UUID
    filename: str
    extraction_method: str
    explanation: str
    created_at: datetime
    messages: List[PatientReportMessageResponse] = []


class PatientReportChatRequest(BaseModel):
    """Request payload for sending a follow-up question to the AI assistant."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Patient follow-up question regarding their uploaded medical report.",
    )


class PatientReportChatResponse(BaseModel):
    """Response payload returned when AI answers a follow-up question."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = True
    session_id: uuid.UUID
    reply: str
    message: PatientReportMessageResponse
    all_messages: List[PatientReportMessageResponse]
