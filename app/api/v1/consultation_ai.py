"""
API endpoints for Consultation AI — audio upload, transcription, extraction,
approval, and status polling.
"""

import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    UploadFile,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_doctor, get_current_user, get_db
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.consultation_ai import (
    ApproveExtractionRequest,
    AudioUploadResponse,
    ConsultationAIStatusResponse,
    ExtractionResponse,
    ExtractionVersionsResponse,
    TranscriptResponse,
    TranscriptionStatusResponse,
)
from app.schemas.prescription import PrescriptionResponse
from app.services.consultation_ai_service import ConsultationAIService
from app.services.transcription_service import TranscriptionService

logger = get_logger(__name__)

router = APIRouter(tags=["Consultation AI"])


# ── Audio Upload ─────────────────────────────────────────────────────────────


@router.post(
    "/meetings/{meeting_id}/upload-audio",
    response_model=AudioUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload consultation audio recording",
)
async def upload_audio(
    meeting_id: uuid.UUID,
    audio_file: UploadFile = File(..., description="Audio recording (webm, mp4, mp3, wav, ogg, m4a)"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AudioUploadResponse:
    """
    Upload own audio recording from a consultation.
    Each participant (doctor or patient) uploads their local audio separately.
    """
    return await TranscriptionService.upload_audio(
        session=session,
        meeting_id=meeting_id,
        user=user,
        audio_file=audio_file,
    )


# ── Transcription ───────────────────────────────────────────────────────────


@router.post(
    "/consultation-ai/{meeting_id}/transcribe",
    response_model=TranscriptionStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start transcription of consultation audio",
)
async def start_transcription(
    meeting_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> TranscriptionStatusResponse:
    """
    Initiate Groq Whisper transcription. Returns immediately with status=processing.
    Transcription runs in the background.
    """
    result = await TranscriptionService.transcribe_meeting(
        session=session,
        meeting_id=meeting_id,
        user=user,
    )

    # Schedule background pipeline
    background_tasks.add_task(
        TranscriptionService.run_transcription_pipeline,
        meeting_id,
    )

    return result


@router.get(
    "/consultation-ai/{meeting_id}/transcript",
    response_model=TranscriptResponse,
    summary="Get consultation transcript",
)
async def get_transcript(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> TranscriptResponse:
    """
    Fetch the structured transcript for a consultation.
    Accessible by doctor, patient (of that meeting), or admin.
    """
    return await TranscriptionService.get_transcript(
        session=session,
        meeting_id=meeting_id,
        user=user,
    )


# ── AI Extraction ───────────────────────────────────────────────────────────


@router.post(
    "/consultation-ai/{meeting_id}/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate AI extraction from transcript",
)
async def start_extraction(
    meeting_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> ExtractionResponse:
    """
    Start AI-powered extraction of medical data from the consultation transcript.
    Creates a new extraction version. Returns immediately with status=processing.
    """
    result = await ConsultationAIService.generate_extraction(
        session=session,
        meeting_id=meeting_id,
        user=user,
    )

    # Schedule background pipeline
    background_tasks.add_task(
        ConsultationAIService.run_extraction_pipeline,
        meeting_id,
        result.id,
    )

    return result


@router.get(
    "/consultation-ai/{meeting_id}/extraction",
    response_model=ExtractionResponse,
    summary="Get extraction data",
)
async def get_extraction(
    meeting_id: uuid.UUID,
    version: Optional[int] = Query(None, description="Specific version number"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ExtractionResponse:
    """
    Fetch the latest (or specific version) extraction for a consultation.
    Unapproved → doctor only. Approved → doctor, patient, admin.
    """
    return await ConsultationAIService.get_extraction(
        session=session,
        meeting_id=meeting_id,
        user=user,
        version=version,
    )


@router.get(
    "/consultation-ai/{meeting_id}/extraction/versions",
    response_model=ExtractionVersionsResponse,
    summary="List all extraction versions",
)
async def list_extraction_versions(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> ExtractionVersionsResponse:
    """List all extraction versions for a consultation meeting."""
    return await ConsultationAIService.list_extraction_versions(
        session=session,
        meeting_id=meeting_id,
        user=user,
    )


# ── Approval ────────────────────────────────────────────────────────────────


@router.post(
    "/consultation-ai/{meeting_id}/approve",
    response_model=PrescriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve extraction and create prescription",
)
async def approve_extraction(
    meeting_id: uuid.UUID,
    payload: ApproveExtractionRequest,
    user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> PrescriptionResponse:
    """
    Doctor approves the AI extraction (with optional edits) → creates an official
    Prescription and PrescriptionMedicine records. Schedules reminder emails.
    Idempotent: returns existing prescription if already approved.
    """
    return await ConsultationAIService.approve_extraction(
        session=session,
        meeting_id=meeting_id,
        user=user,
        request=payload,
    )


# ── Status ──────────────────────────────────────────────────────────────────


@router.get(
    "/consultation-ai/{meeting_id}/status",
    response_model=ConsultationAIStatusResponse,
    summary="Get consultation AI processing status",
)
async def get_consultation_ai_status(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ConsultationAIStatusResponse:
    """
    Poll the current status of transcription and extraction for a meeting.
    """
    return await ConsultationAIService.get_consultation_ai_status(
        session=session,
        meeting_id=meeting_id,
        user=user,
    )
