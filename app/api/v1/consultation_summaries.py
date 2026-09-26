"""
API endpoints for Consultation Summaries — patient visit history,
doctor review of patient past consultations, and meeting-level summaries.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_doctor, get_current_user, get_db
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.consultation_summary import (
    ConsultationHistoryResponse,
    ConsultationSummaryResponse,
)
from app.services.consultation_summary_service import ConsultationSummaryService

logger = get_logger(__name__)

router = APIRouter(tags=["Consultation Summaries"])


# ── Patient: Own Visit History ───────────────────────────────────────────────


@router.get(
    "/consultation-summaries/my-history",
    response_model=ConsultationHistoryResponse,
    summary="Get my consultation history (patient)",
)
async def get_my_consultation_history(
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ConsultationHistoryResponse:
    """
    Fetch the current patient's consultation visit history with summaries.
    Ordered by most recent first.
    """
    return await ConsultationSummaryService.get_patient_history(
        session=session,
        patient_id=user.id,
        user=user,
        limit=limit,
        offset=offset,
    )


# ── Doctor: View Patient History ─────────────────────────────────────────────


@router.get(
    "/consultation-summaries/patient/{patient_id}/history",
    response_model=List[ConsultationSummaryResponse],
    summary="View a patient's consultation history (doctor)",
)
async def get_patient_history_for_doctor(
    patient_id: uuid.UUID,
    user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> List[ConsultationSummaryResponse]:
    """
    Doctor views a patient's full consultation history.
    Shows all past visit summaries so the doctor has context for the current consultation.
    """
    return await ConsultationSummaryService.get_patient_history_for_doctor(
        session=session,
        patient_id=patient_id,
        doctor_id=user.id,
        user=user,
    )


# ── Single Meeting Summary ──────────────────────────────────────────────────


@router.get(
    "/consultation-summaries/meeting/{meeting_id}",
    response_model=ConsultationSummaryResponse,
    summary="Get consultation summary for a meeting",
)
async def get_meeting_summary(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ConsultationSummaryResponse:
    """
    Fetch the AI-generated consultation summary for a specific meeting.
    Both doctor and patient of the meeting can view it.
    """
    return await ConsultationSummaryService.get_summary_for_meeting(
        session=session,
        meeting_id=meeting_id,
        user=user,
    )
