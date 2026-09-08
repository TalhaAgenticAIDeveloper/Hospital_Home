"""
API endpoints for patient medical document management.

Patients can upload, list, delete, and download their medical documents.
Documents are stored persistently and can be attached to appointments.
"""

import uuid
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.logging import get_logger
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.patient_document import PatientDocumentResponse
from app.services.patient_document_service import PatientDocumentService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/patient/documents",
    tags=["Patient Medical Documents"],
)


@router.post(
    "",
    response_model=PatientDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a medical document",
    description=(
        "Upload a medical history document (PDF, JPEG, PNG). "
        "Maximum 5 documents per patient, each up to 50 MB. "
        "Optionally provide a label (e.g., 'Blood Test Report')."
    ),
)
async def upload_patient_document(
    file: UploadFile = File(
        ...,
        description="Medical document file (PDF, JPEG, or PNG)",
    ),
    label: Optional[str] = Form(
        None,
        max_length=255,
        description="User-friendly label for the document",
    ),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PatientDocumentResponse:
    document = await PatientDocumentService.upload_document(
        session, user, file, label
    )
    return PatientDocumentResponse.model_validate(document)


@router.get(
    "",
    response_model=List[PatientDocumentResponse],
    summary="List all uploaded medical documents",
    description="Returns all medical documents uploaded by the authenticated patient.",
)
async def list_patient_documents(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> List[PatientDocumentResponse]:
    documents = await PatientDocumentService.list_documents(session, user)
    return [PatientDocumentResponse.model_validate(doc) for doc in documents]


@router.delete(
    "/{document_id}",
    response_model=MessageResponse,
    summary="Delete an uploaded medical document",
    description=(
        "Permanently delete a medical document. The file is removed from "
        "storage and any meeting attachments referencing it will also be removed."
    ),
)
async def delete_patient_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await PatientDocumentService.delete_document(session, user, document_id)
    return MessageResponse(message="Document deleted successfully")


@router.get(
    "/{document_id}/download",
    summary="Download a medical document",
    description=(
        "Download or view an uploaded medical document. "
        "Only the document owner or a SaaS admin can access this endpoint."
    ),
)
async def download_patient_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    return await PatientDocumentService.download_document(
        session, user, document_id
    )
