"""
Pydantic schemas for the Consultation AI system.

Covers:
- Transcript segments and responses
- AI extraction data structures (medications, diagnoses, symptoms, etc.)
- API request/response models
- Approval workflow schemas
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Transcript Schemas
# ═══════════════════════════════════════════════════════════════════════════════


class TranscriptSegment(BaseModel):
    """A single speaker-labeled segment from the interleaved transcript."""
    speaker: str = Field(..., description="'doctor' or 'patient'")
    text: str
    start_time: float = Field(..., description="Seconds from start of recording")
    end_time: float = Field(..., description="Seconds from start of recording")
    language: Optional[str] = Field(None, description="ISO language code, e.g. 'ur', 'en'")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class AudioUploadResponse(BaseModel):
    """Response after uploading an audio file."""
    meeting_id: uuid.UUID
    role: str = Field(..., description="'doctor' or 'patient'")
    audio_path: str
    message: str


class TranscriptionStatusResponse(BaseModel):
    """Current transcription processing status."""
    meeting_id: uuid.UUID
    transcription_status: str
    has_doctor_audio: bool
    has_patient_audio: bool
    error_message: Optional[str] = None


class TranscriptResponse(BaseModel):
    """Full transcript data for a meeting."""
    meeting_id: uuid.UUID
    transcription_status: str
    transcription_model: Optional[str] = None
    segments: List[TranscriptSegment] = []
    full_text: Optional[str] = None
    processing_time_ms: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction Data Schemas (inner structures stored in JSONB)
# ═══════════════════════════════════════════════════════════════════════════════


class EvidenceRef(BaseModel):
    """Reference to a transcript excerpt as evidence for an extracted item."""
    speaker: str = Field(..., description="'doctor' or 'patient'")
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    transcript_excerpt: Optional[str] = None


class ExtractedMedication(BaseModel):
    """A medication extracted from the doctor's instructions."""
    medication_name: str
    dose_value: Optional[str] = None
    dose_unit: Optional[str] = None
    route: Optional[str] = Field(
        None, description="oral, injection, topical, inhaler, iv, im, sc, etc."
    )
    frequency: Optional[str] = Field(
        None,
        description="once_daily, twice_daily, thrice_daily, four_times_daily, "
                    "every_4h, every_6h, every_8h, every_12h, as_needed, stat, etc."
    )
    times_per_day: Optional[int] = None
    timings: Optional[List[str]] = Field(
        None, description="List of: morning, afternoon, evening, night"
    )
    meal_relation: Optional[str] = Field(
        None, description="before_meal, after_meal, with_meal, empty_stomach"
    )
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    duration_days: Optional[int] = None
    special_instructions: Optional[str] = None
    status: str = Field(
        "new", description="new, continued, stopped, modified"
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence: List[EvidenceRef] = []


class ExtractedDiagnosis(BaseModel):
    """A diagnosis extracted from the doctor's statements."""
    diagnosis_name: str
    certainty: str = Field(
        "confirmed",
        description="confirmed, suspected, differential, ruled_out"
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence: List[EvidenceRef] = []


class ExtractedSymptom(BaseModel):
    """A symptom/complaint extracted from the conversation."""
    symptom: str
    reported_by: str = Field(
        "patient", description="patient or doctor_observed"
    )
    duration: Optional[str] = None
    severity: Optional[str] = None


class ExtractedTest(BaseModel):
    """A medical test/investigation ordered by the doctor."""
    test_name: str
    urgency: Optional[str] = Field(
        None, description="routine, urgent, stat"
    )
    reason: Optional[str] = None
    evidence: List[EvidenceRef] = []


class ExtractedFollowUp(BaseModel):
    """Follow-up instructions from the doctor."""
    follow_up_date: Optional[str] = None
    follow_up_period: Optional[str] = Field(
        None, description="e.g., '1 week', '2 weeks', '1 month'"
    )
    instructions: Optional[str] = None


class DoctorInstruction(BaseModel):
    """General patient instruction from the doctor."""
    category: str = Field(
        ...,
        description="diet, activity, hydration, rest, monitoring, "
                    "precaution, lifestyle, other"
    )
    instruction_text: str


class UncertainItem(BaseModel):
    """An item the AI could not extract with confidence."""
    field: str = Field(
        ..., description="Which section: medication, diagnosis, symptom, test, etc."
    )
    extracted_value: Optional[str] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = Field(..., description="Why this item is uncertain")
    evidence: Optional[EvidenceRef] = None


class ConsultationExtractionResult(BaseModel):
    """
    Complete structured extraction from a consultation transcript.
    This is the core schema stored in extraction_data JSONB.
    """
    medications: List[ExtractedMedication] = []
    diagnoses: List[ExtractedDiagnosis] = []
    symptoms: List[ExtractedSymptom] = []
    tests: List[ExtractedTest] = []
    follow_ups: List[ExtractedFollowUp] = []
    doctor_instructions: List[DoctorInstruction] = []
    uncertain_items: List[UncertainItem] = []
    consultation_summary: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# API Request / Response Schemas
# ═══════════════════════════════════════════════════════════════════════════════


class ExtractionResponse(BaseModel):
    """API response for a single extraction version."""
    id: uuid.UUID
    meeting_id: uuid.UUID
    version: int
    status: str
    extraction_data: Optional[ConsultationExtractionResult] = None
    confidence_score: Optional[float] = None
    llm_model_used: Optional[str] = None
    total_chunks: int = 1
    processing_time_ms: Optional[int] = None
    error_message: Optional[str] = None
    is_approved: bool = False
    approved_at: Optional[datetime] = None
    approved_extraction_data: Optional[ConsultationExtractionResult] = None
    doctor_approval_notes: Optional[str] = None
    prescription_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ExtractionVersionSummary(BaseModel):
    """Summary of an extraction version for the versions list endpoint."""
    id: uuid.UUID
    version: int
    status: str
    is_approved: bool
    confidence_score: Optional[float] = None
    total_chunks: int = 1
    created_at: Optional[datetime] = None


class ExtractionVersionsResponse(BaseModel):
    """List of all extraction versions for a meeting."""
    meeting_id: uuid.UUID
    versions: List[ExtractionVersionSummary] = []


class ApproveExtractionRequest(BaseModel):
    """Doctor submits edited extraction data for approval → creates Prescription."""
    edited_extraction: ConsultationExtractionResult
    notes: Optional[str] = Field(
        None, max_length=5000, description="Doctor's notes about the approval"
    )


class ConsultationAIStatusResponse(BaseModel):
    """Combined status of transcription and extraction for a meeting."""
    meeting_id: uuid.UUID
    transcription_status: Optional[str] = None
    has_doctor_audio: bool = False
    has_patient_audio: bool = False
    extraction_status: Optional[str] = None
    has_approved_extraction: bool = False
    latest_extraction_version: Optional[int] = None
    prescription_id: Optional[uuid.UUID] = None
