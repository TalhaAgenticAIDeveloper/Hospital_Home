"""
SaaS Admin patient management routes.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import PatientAdminListResponse
from app.services.admin_service import AdminService

router = APIRouter(
    prefix="/admin/patients",
    tags=["Admin Patient Management"],
    dependencies=[Depends(require_role(UserRole.SAAS_ADMIN))],
)


@router.get(
    "",
    response_model=PatientAdminListResponse,
    summary="List all patients with filtering and search",
    description="Retrieve registered patients with activity counts and optional search by name, email, address, gender, or blood group.",
)
async def list_patients(
    search: Optional[str] = Query(None, description="Search term for name, email, address, gender, blood group"),
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Limit for pagination"),
    session: AsyncSession = Depends(get_db),
) -> PatientAdminListResponse:
    return await AdminService.list_patients(
        session, search=search, skip=skip, limit=limit
    )


@router.delete(
    "/{patient_user_id}",
    summary="Delete a patient account",
    description="Permanently delete a patient user account, cascade-delete relations, and remove documents from disk.",
)
async def delete_patient(
    patient_user_id: uuid.UUID,
    admin_user: User = Depends(require_role(UserRole.SAAS_ADMIN)),
    session: AsyncSession = Depends(get_db),
):
    return await AdminService.delete_patient(session, patient_user_id)
