"""
Doctor onboarding and profile routes.

Endpoints for doctors to update their professional details, upload verification
documents, check their application status, and view SaaS Admin rejection feedback.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_doctor
from app.core.database import get_db
from app.models.enums import DocumentType
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.doctor import (
    DoctorApplicationStatusResponse,
    DoctorDocumentResponse,
    DoctorProfileResponse,
    DoctorProfileUpdateRequest,
)
from app.services.doctor_service import DoctorService

router = APIRouter(prefix="/doctors", tags=["Doctor Profile & Onboarding"])


@router.get(
    "/profile",
    response_model=DoctorProfileResponse,
    summary="Get doctor profile and documents",
    description="Retrieve the authenticated doctor's full profile, uploaded documents, review status, and admin feedback.",
)
async def get_my_profile(
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> DoctorProfileResponse:
    return await DoctorService.get_profile(session, user)


@router.put(
    "/profile",
    response_model=DoctorProfileResponse,
    summary="Update doctor professional information",
    description="Update professional credentials (name, specialization, license number, experience, qualifications, bio).",
)
async def update_my_profile(
    data: DoctorProfileUpdateRequest,
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> DoctorProfileResponse:
    return await DoctorService.update_profile(session, user, data)


@router.post(
    "/documents",
    response_model=DoctorDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload verification document",
    description="Upload a document (medical license, degree certificate, ID proof) for verification.",
)
async def upload_document(
    document_type: DocumentType = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> DoctorDocumentResponse:
    return await DoctorService.upload_document(session, user, file, document_type)


@router.get(
    "/documents",
    response_model=List[DoctorDocumentResponse],
    summary="List uploaded verification documents",
    description="List all documents uploaded by the authenticated doctor.",
)
async def list_documents(
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> List[DoctorDocumentResponse]:
    return await DoctorService.list_documents(session, user)


@router.delete(
    "/documents/{doc_id}",
    response_model=MessageResponse,
    summary="Delete an uploaded document",
    description="Remove an uploaded document and delete the file from storage.",
)
async def delete_document(
    doc_id: uuid.UUID,
    user: User = Depends(get_current_doctor),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await DoctorService.delete_document(session, user, doc_id)
    return MessageResponse(message="Document deleted successfully")


@router.post(
    "/submit-application",
    response_model=DoctorApplicationStatusResponse,
    summary="Submit application for SaaS Admin review",
    description=(
        "Submit profile and documents for approval. Validates that mandatory fields "
        "and at least one document are present. Clears any previous rejection feedback."
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
