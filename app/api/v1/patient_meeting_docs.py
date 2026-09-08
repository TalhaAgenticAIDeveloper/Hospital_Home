"""
API endpoints for doctors to view patient documents attached to their meetings.

When a patient books an appointment and selects documents to share,
the consulting doctor can view and download those specific documents
through these endpoints.
"""

import os
import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.logging import get_logger
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.meeting_repository import MeetingRepository
from app.repositories.patient_document_repository import PatientDocumentRepository
from app.schemas.patient_document import MeetingDocumentResponse

logger = get_logger(__name__)

router = APIRouter(
    prefix="/meetings",
    tags=["Meeting Patient Documents"],
)


@router.get(
    "/{meeting_id}/patient-documents",
    response_model=List[MeetingDocumentResponse],
    summary="List patient documents attached to a meeting",
    description=(
        "Returns all patient medical documents that the patient selected "
        "to share when booking this appointment. Only accessible by the "
        "consulting doctor, the patient, or a SaaS admin."
    ),
)
async def get_meeting_patient_documents(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> List[MeetingDocumentResponse]:
    # Fetch meeting and verify access
    meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
    if not meeting:
        raise NotFoundError("Meeting not found")

    if (
        user.id not in (meeting.doctor_id, meeting.patient_id)
        and user.role != UserRole.SAAS_ADMIN
    ):
        raise AuthorizationError("You are not authorized to access this meeting's documents")

    # Fetch attached documents
    meeting_docs = await MeetingRepository.get_meeting_documents(session, meeting_id)

    result = []
    for md in meeting_docs:
        pd = md.patient_document
        if pd:
            result.append(
                MeetingDocumentResponse(
                    id=md.id,
                    meeting_id=md.meeting_id,
                    patient_document_id=md.patient_document_id,
                    label=pd.label,
                    original_filename=pd.original_filename,
                    file_size=pd.file_size,
                    mime_type=pd.mime_type,
                    uploaded_at=pd.created_at,
                )
            )

    return result


@router.get(
    "/{meeting_id}/patient-documents/{document_id}/download",
    summary="Download a patient document attached to a meeting",
    description=(
        "Download a specific patient medical document that was attached to "
        "this meeting. Only the consulting doctor, the patient, or a SaaS admin "
        "can download the document."
    ),
)
async def download_meeting_patient_document(
    meeting_id: uuid.UUID,
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    # Fetch meeting and verify access
    meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
    if not meeting:
        raise NotFoundError("Meeting not found")

    if (
        user.id not in (meeting.doctor_id, meeting.patient_id)
        and user.role != UserRole.SAAS_ADMIN
    ):
        raise AuthorizationError("You are not authorized to access this meeting's documents")

    # Verify the document is actually attached to this meeting
    meeting_docs = await MeetingRepository.get_meeting_documents(session, meeting_id)
    target_doc = None
    for md in meeting_docs:
        if md.patient_document_id == document_id:
            target_doc = md.patient_document
            break

    if not target_doc:
        raise NotFoundError(
            "Document not found or not attached to this meeting"
        )

    if not os.path.exists(target_doc.file_path):
        raise NotFoundError("Document file not found on server")

    return FileResponse(
        path=target_doc.file_path,
        media_type=target_doc.mime_type,
        filename=target_doc.original_filename,
    )
