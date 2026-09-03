"""
SaaS Admin doctor management and application review routes.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.database import get_db
from app.core.exceptions import AuthenticationError, AuthorizationError, NotFoundError
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.doctor_repository import DoctorRepository
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

# ── Document Download Router (uses query-token auth or header for browser access) ──────
document_router = APIRouter(
    prefix="/admin/doctors",
    tags=["Admin Doctor Review"],
)


@router.get(
    "",
    response_model=AllDoctorsListResponse,
    summary="List all doctors with filtering and search",
    description="Retrieve all doctors with optional status filter (all, pending, active, rejected) and search query.",
)
async def list_doctors(
    status: str | None = Query(None, description="Filter by status: 'pending', 'active', 'rejected', or 'all'"),
    search: str | None = Query(None, description="Search term for name, email, specialization, license"),
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
    summary="Get doctor application details and documents",
    description="View full professional information and uploaded verification documents for a specific doctor.",
)
async def get_doctor_detail(
    doctor_user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> PendingDoctorDetailResponse:
    return await AdminService.get_doctor_detail(session, doctor_user_id)


@router.delete(
    "/{doctor_user_id}",
    summary="Delete a doctor account and documents",
    description="Permanently delete a doctor user account, associated profile, uploaded documents and files on disk.",
)
async def delete_doctor(
    doctor_user_id: uuid.UUID,
    admin_user: User = Depends(require_role(UserRole.SAAS_ADMIN)),
    session: AsyncSession = Depends(get_db),
):
    return await AdminService.delete_doctor(session, doctor_user_id)



@document_router.get(
    "/documents/{document_id}/download",
    summary="Download or view a doctor's uploaded document",
    description=(
        "Serve the actual document file (PDF, image) for the SaaS Admin to view or download. "
        "Use query parameter ?inline=true to view in browser, otherwise triggers download. "
        "Requires JWT access token via ?token= query parameter or Authorization header."
    ),
)
async def download_document(
    document_id: uuid.UUID,
    request: Request,
    inline: bool = Query(False, description="If true, display in browser instead of downloading"),
    token: str | None = Query(None, description="JWT access token for authentication"),
    session: AsyncSession = Depends(get_db),
):
    from jose import JWTError as JoseJWTError
    from app.core.security import decode_token

    auth_token = token
    if not auth_token and "authorization" in request.headers:
        header_val = request.headers["authorization"]
        if header_val.lower().startswith("bearer "):
            auth_token = header_val[7:].strip()

    if not auth_token:
        raise AuthenticationError(detail="Authentication token required")

    try:
        payload = decode_token(auth_token)
    except (JoseJWTError, Exception):
        raise AuthenticationError(detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise AuthenticationError(detail="Invalid token type")

    role = payload.get("role", "")
    if role != UserRole.SAAS_ADMIN.value:
        raise AuthorizationError(detail="Insufficient permissions")

    doc = await DoctorRepository.get_document_by_id(session, document_id)
    if not doc:
        raise NotFoundError(detail="Document not found")

    file_path = Path(doc.file_path)
    if not file_path.exists() or not file_path.is_file():
        # Fallback relative to current working directory
        if not file_path.is_absolute():
            potential = Path.cwd() / file_path
            if potential.exists() and potential.is_file():
                file_path = potential
            else:
                raise NotFoundError(detail="Document file not found on server")
        else:
            raise NotFoundError(detail="Document file not found on server")

    disposition = "inline" if inline else "attachment"

    return FileResponse(
        path=str(file_path),
        filename=doc.original_filename,
        media_type=doc.mime_type or "application/octet-stream",
        content_disposition_type=disposition,
    )


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
