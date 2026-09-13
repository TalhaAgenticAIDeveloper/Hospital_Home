"""
SaaS Admin doctor management and application review routes.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import (
    AllDoctorsListResponse,
    DoctorReviewRequest,
    DoctorReviewResponse,
    PendingDoctorDetailResponse,
    PendingDoctorListResponse,
)
from app.services.admin_service import AdminService

# ── Main Admin Router (requires Bearer auth) ────────────────────────────────
router = APIRouter(
    prefix="/admin/doctors",
    tags=["Admin Doctor Review"],
    dependencies=[Depends(require_role(UserRole.SAAS_ADMIN))],
)


@router.get(
    "",
    response_model=AllDoctorsListResponse,
    summary="List all doctors with filtering and search",
    description="Retrieve all doctors with optional status filter (all, pending, active, rejected) and search query.",
)
async def list_doctors(
    status: str | None = Query(None, description="Filter by status: 'pending', 'active', 'rejected', or 'all'"),
    search: str | None = Query(None, description="Search term for name, father name, PMDC number, email, specialization"),
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Limit for pagination"),
    session: AsyncSession = Depends(get_db),
) -> AllDoctorsListResponse:
    return await AdminService.list_all_doctors(
        session, status=status, search=search, skip=skip, limit=limit
    )


@router.get(
    "/pending",
    response_model=PendingDoctorListResponse,
    summary="List pending doctor applications",
    description="Retrieve all submitted doctor applications awaiting SaaS Admin verification and approval.",
)
async def list_pending_doctors(
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Limit for pagination"),
    session: AsyncSession = Depends(get_db),
) -> PendingDoctorListResponse:
    return await AdminService.list_pending_doctors(session, skip=skip, limit=limit)


@router.get(
    "/{doctor_user_id}",
    response_model=PendingDoctorDetailResponse,
    summary="Get doctor application details",
    description="View full verification details (Full Name, Father Name, PMDC Registration Number) for a specific doctor.",
)
async def get_doctor_detail(
    doctor_user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> PendingDoctorDetailResponse:
    return await AdminService.get_doctor_detail(session, doctor_user_id)


@router.delete(
    "/{doctor_user_id}",
    summary="Delete a doctor account",
    description="Permanently delete a doctor user account and associated profile.",
)
async def delete_doctor(
    doctor_user_id: uuid.UUID,
    admin_user: User = Depends(require_role(UserRole.SAAS_ADMIN)),
    session: AsyncSession = Depends(get_db),
):
    return await AdminService.delete_doctor(session, doctor_user_id)


@router.post(
    "/{doctor_user_id}/review",
    response_model=DoctorReviewResponse,
    summary="Approve or reject doctor application with feedback",
    description=(
        "Review a doctor application. If approved, doctor status becomes 'active'. "
        "If rejected, doctor status becomes 'rejected' and feedback is recorded for the doctor to review."
    ),
)
async def review_doctor(
    doctor_user_id: uuid.UUID,
    data: DoctorReviewRequest,
    admin_user: User = Depends(require_role(UserRole.SAAS_ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> DoctorReviewResponse:
    return await AdminService.review_doctor(session, doctor_user_id, admin_user, data)
