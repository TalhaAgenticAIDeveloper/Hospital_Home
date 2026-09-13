"""
API endpoints for medical prescriptions and medicine dosage schedules.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_active_doctor,
    get_current_user,
    get_db,
    require_role,
)
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.prescription import (
    PrescriptionCreateRequest,
    PrescriptionResponse,
)
from app.services.prescription_service import PrescriptionService

router = APIRouter(prefix="/prescriptions", tags=["Prescriptions & Medicine Schedules"])


@router.post(
    "",
    response_model=PrescriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Doctor issues prescription for a consultation",
)
async def create_prescription(
    payload: PrescriptionCreateRequest,
    current_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> PrescriptionResponse:
    """
    Doctor creates a prescription with detailed morning/afternoon/evening/night
    dosage schedules and meal instructions. Triggers automated reminder scheduling.
    """
    return await PrescriptionService.create_prescription(
        session=session,
        current_user=current_user,
        payload=payload,
    )


@router.get(
    "/meeting/{meeting_id}",
    response_model=PrescriptionResponse,
    summary="Get prescription for a consultation meeting",
)
async def get_meeting_prescription(
    meeting_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PrescriptionResponse:
    """
    Fetch the prescription issued for a specific meeting.
    Accessible by the patient, the doctor, or an admin.
    """
    return await PrescriptionService.get_by_meeting_id(
        session=session,
        meeting_id=meeting_id,
        current_user=current_user,
    )


@router.get(
    "/my",
    response_model=List[PrescriptionResponse],
    summary="Patient lists all their prescriptions",
)
async def get_patient_prescriptions(
    current_user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> List[PrescriptionResponse]:
    """
    Patient fetches all prescriptions issued to them across all past consultations.
    """
    return await PrescriptionService.list_patient_prescriptions(
        session=session,
        current_user=current_user,
    )
