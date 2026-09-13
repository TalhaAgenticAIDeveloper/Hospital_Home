"""
API endpoints for Patient Medical Report Explainer.

Patients can upload medical laboratory and diagnostic reports,
receive an educational layman-friendly AI breakdown, and participate
in multi-turn follow-up chat conversations.

Strictly restricted to users with role PATIENT.
"""

import uuid
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_role
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.patient_report import (
    PatientReportChatRequest,
    PatientReportChatResponse,
    PatientReportSessionDetail,
    PatientReportSessionSummary,
    PatientReportUploadResponse,
)
from app.services.patient_report_explainer_service import PatientReportExplainerService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/patient/reports",
    tags=["Patient Medical Report Explainer"],
)


@router.post(
    "/upload",
    response_model=PatientReportUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and analyze a medical report (Patient only)",
    description=(
        "Upload a medical test report (PDF, PNG, JPG, JPEG) up to 20 MB. "
        "Extracts text, generates a layman-friendly clinical explanation, "
        "and creates an active conversation session."
    ),
)
async def upload_medical_report(
    file: UploadFile = File(
        ...,
        description="Medical report file (PDF, PNG, JPG, or JPEG)",
    ),
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientReportUploadResponse:
    if not file.filename:
        raise ValidationError("File name is missing.")

    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise ValidationError(f"Failed to read uploaded file: {exc}")

    return await PatientReportExplainerService.create_report_session(
        session=session,
        patient_user=user,
        file_bytes=file_bytes,
        filename=file.filename,
        mime_type=file.content_type,
    )


@router.get(
    "/sessions",
    response_model=List[PatientReportSessionSummary],
    summary="List patient's past report explainer sessions",
    description="Returns all report sessions uploaded by the authenticated patient.",
)
async def list_patient_report_sessions(
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> List[PatientReportSessionSummary]:
    return await PatientReportExplainerService.list_patient_sessions(
        session=session,
        patient_user=user,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=PatientReportSessionDetail,
    summary="Get report session details and chat history",
)
async def get_patient_report_session_detail(
    session_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientReportSessionDetail:
    return await PatientReportExplainerService.get_session_detail(
        session=session,
        patient_user=user,
        session_id=session_id,
    )


@router.post(
    "/sessions/{session_id}/chat",
    response_model=PatientReportChatResponse,
    summary="Ask follow-up questions regarding the medical report",
    description="Interactive chat grounded in the uploaded report context and past chat messages.",
)
async def chat_with_report_explainer(
    session_id: uuid.UUID,
    payload: PatientReportChatRequest,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientReportChatResponse:
    return await PatientReportExplainerService.chat(
        session=session,
        patient_user=user,
        session_id=session_id,
        message_text=payload.message,
    )


@router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
    summary="Delete a report explainer session",
)
async def delete_patient_report_session(
    session_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await PatientReportExplainerService.delete_session(
        session=session,
        patient_user=user,
        session_id=session_id,
    )
    return MessageResponse(message="Report session removed successfully.")
