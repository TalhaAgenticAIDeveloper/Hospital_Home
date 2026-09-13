"""
Doctor onboarding and profile routes.

Endpoints for doctors to update their mandatory verification details (Full Name,
Father Name, PMDC Registration Number), check their application status, and view
SaaS Admin review feedback.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor
from app.core.database import get_db
from app.models.user import User
from app.schemas.doctor import (
    DoctorApplicationStatusResponse,
    DoctorProfileResponse,
    DoctorProfileUpdateRequest,
)
from app.services.doctor_service import DoctorService

router = APIRouter(prefix="/doctors", tags=["Doctor Profile & Onboarding"])


@router.get(
    "/profile",
    response_model=DoctorProfileResponse,
    summary="Get doctor profile",
    description="Retrieve the authenticated doctor's profile, verification status, and admin feedback.",
)
async def get_my_profile(
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> DoctorProfileResponse:
    return await DoctorService.get_profile(session, user)


@router.put(
    "/profile",
    response_model=DoctorProfileResponse,
    summary="Update doctor profile",
    description="Update verification details (Full Name, Father Name, PMDC Registration Number) and optional credentials.",
)
async def update_my_profile(
    data: DoctorProfileUpdateRequest,
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> DoctorProfileResponse:
    return await DoctorService.update_profile(session, user, data)


@router.post(
    "/submit-application",
    response_model=DoctorApplicationStatusResponse,
    summary="Submit application for SaaS Admin review",
    description=(
        "Submit profile for approval. Validates that Full Name, Father Name, and PMDC "
        "Registration Number are present. Clears any previous rejection feedback."
    ),
)
async def submit_application(
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> DoctorApplicationStatusResponse:
    return await DoctorService.submit_application(session, user)


@router.get(
    "/status",
    response_model=DoctorApplicationStatusResponse,
    summary="Check application status and review feedback",
    description="Check whether the application is pending, approved, or rejected with admin feedback.",
)
async def check_status(
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> DoctorApplicationStatusResponse:
    return await DoctorService.get_application_status(session, user)
