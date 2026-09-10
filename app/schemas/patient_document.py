"""
Pydantic schemas for patient medical document management.

These schemas control request validation and API response serialization
for patient document upload, listing, and meeting attachment flows.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PatientDocumentResponse(BaseModel):
    """Schema for a patient's uploaded medical document."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    label: Optional[str] = None
    original_filename: str
    file_size: int
    mime_type: str
    ai_summary: Optional[str] = None
    ai_summary_status: Optional[str] = None
    ai_summary_generated_at: Optional[datetime] = None
    created_at: datetime


class MeetingDocumentResponse(BaseModel):
    """Schema for a patient document attached to a specific meeting."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    patient_document_id: uuid.UUID
    # Nested patient document details
    label: Optional[str] = None
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    ai_summary: Optional[str] = None
    ai_summary_status: Optional[str] = None
    ai_summary_generated_at: Optional[datetime] = None


class DocumentSummaryResponse(BaseModel):
    """Response schema for AI document summarization (Groq LLM)."""

    document_id: uuid.UUID
    meeting_id: Optional[uuid.UUID] = None
    label: Optional[str] = None
    original_filename: str
    status: str  # 'completed' | 'unclear' | 'failed'
    summary: str
    is_cached: bool = False
    generated_at: datetime
