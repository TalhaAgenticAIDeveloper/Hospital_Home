"""
API endpoints for Patient profile management.

Allows patients to view and update their personal and demographic details.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.patient_repository import PatientRepository
from app.schemas.patient import (
    PatientProfileResponse,
    PatientProfileUpdateRequest,
)

router = APIRouter(prefix="/patient/profile", tags=["Patient Profile"])


@router.get(
    "",
    response_model=PatientProfileResponse,
    summary="Get patient profile",
    description="Retrieve the current authenticated patient's profile details.",
)
async def get_patient_profile(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PatientProfileResponse:
    profile = await PatientRepository.get_profile_by_user_id(session, user.id)
    if not profile:
        # Create an empty profile placeholder on-the-fly
        profile = await PatientRepository.create_or_update_profile(
            session, user.id, PatientProfileUpdateRequest()
        )

    return PatientProfileResponse(
        id=profile.id,
        user_id=user.id,
        email=user.email,
        full_name=profile.full_name,
        age=profile.age,
        date_of_birth=profile.date_of_birth,
        gender=profile.gender,
        blood_group=profile.blood_group,
        address=profile.address,
        is_completed=profile.is_completed,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.put(
    "",
    response_model=PatientProfileResponse,
    summary="Update patient profile",
    description="Update personal details for the authenticated patient.",
)
async def update_patient_profile(
    data: PatientProfileUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PatientProfileResponse:
    profile = await PatientRepository.create_or_update_profile(
        session, user.id, data
    )

    return PatientProfileResponse(
        id=profile.id,
        user_id=user.id,
        email=user.email,
        full_name=profile.full_name,
        age=profile.age,
        date_of_birth=profile.date_of_birth,
        gender=profile.gender,
        blood_group=profile.blood_group,
        address=profile.address,
        is_completed=profile.is_completed,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
