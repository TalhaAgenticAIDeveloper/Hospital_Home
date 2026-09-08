"""
Patient document service — business logic for medical document management.

Handles upload validation, storage, retrieval, and deletion of patient
medical history documents. Enforces limits (5 docs max, 50 MB per file).
"""

import os
import uuid
from typing import List

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.core.file_upload import delete_file_from_disk, save_upload_file, validate_file
from app.core.logging import get_logger
from app.models.enums import UserRole
from app.models.patient_document import PatientDocument
from app.models.user import User
from app.repositories.patient_document_repository import PatientDocumentRepository

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
PATIENT_DOCUMENTS_DIR = os.path.join("uploads", "patient_documents")
MAX_DOCUMENTS_PER_PATIENT = 5
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Allowed MIME types for patient medical documents
ALLOWED_MIME_TYPES = [
    "application/pdf",
    "image/jpeg",
    "image/png",
]


class PatientDocumentService:
    """Service layer for patient medical document operations."""

    @staticmethod
    def _ensure_upload_dir():
        """Create the patient documents upload directory if it doesn't exist."""
        os.makedirs(PATIENT_DOCUMENTS_DIR, exist_ok=True)

    @staticmethod
    async def upload_document(
        session: AsyncSession,
        patient_user: User,
        file: UploadFile,
        label: str | None = None,
    ) -> PatientDocument:
        """
        Validate, save, and record a patient medical document.

        Enforces:
        - Only patients can upload
        - Max 5 documents per patient
        - Max 50 MB per file
        - Only PDF, JPEG, PNG allowed
        """
        if patient_user.role != UserRole.PATIENT:
            raise AuthorizationError("Only patients can upload medical documents")

        # Check document count limit
        current_count = await PatientDocumentRepository.count_by_patient_id(
            session, patient_user.id
        )
        if current_count >= MAX_DOCUMENTS_PER_PATIENT:
            raise ValidationError(
                f"You have reached the maximum limit of {MAX_DOCUMENTS_PER_PATIENT} documents. "
                "Please delete an existing document before uploading a new one."
            )

        # Validate file type and size
        validate_file(
            file,
            max_size_bytes=MAX_FILE_SIZE_BYTES,
            allowed_types=ALLOWED_MIME_TYPES,
        )

        # Save file to disk
        PatientDocumentService._ensure_upload_dir()
        orig_name, stored_name, file_path, file_size, mime_type = (
            await save_upload_file(file, custom_dir=PATIENT_DOCUMENTS_DIR)
        )

        # Create database record
        document = PatientDocument(
            id=uuid.uuid4(),
            patient_id=patient_user.id,
            label=label.strip() if label else None,
            original_filename=orig_name,
            stored_filename=stored_name,
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
        )

        try:
            created = await PatientDocumentRepository.create(session, document)
            await session.commit()
        except Exception:
            await session.rollback()
            delete_file_from_disk(file_path)
            raise

        logger.info(
            f"Patient document uploaded: user_id={patient_user.id} "
            f"file={orig_name} size={file_size}"
        )
        return created

    @staticmethod
    async def list_documents(
        session: AsyncSession,
        patient_user: User,
    ) -> List[PatientDocument]:
        """List all medical documents for the authenticated patient."""
        if patient_user.role != UserRole.PATIENT:
            raise AuthorizationError("Only patients can access medical documents")

        return await PatientDocumentRepository.list_by_patient_id(
            session, patient_user.id
        )

    @staticmethod
    async def delete_document(
        session: AsyncSession,
        patient_user: User,
        document_id: uuid.UUID,
    ) -> None:
        """
        Delete a patient's uploaded document.

        Verifies ownership before deletion. Removes file from disk and DB.
        """
        if patient_user.role != UserRole.PATIENT:
            raise AuthorizationError("Only patients can delete their medical documents")

        document = await PatientDocumentRepository.get_by_id(session, document_id)

        if not document or document.patient_id != patient_user.id:
            raise NotFoundError("Document not found")

        # Delete from disk
        delete_file_from_disk(document.file_path)

        # Delete from database
        await PatientDocumentRepository.delete(session, document)
        await session.commit()

        logger.info(
            f"Patient document deleted: user_id={patient_user.id} "
            f"doc_id={document_id}"
        )

    @staticmethod
    async def download_document(
        session: AsyncSession,
        requesting_user: User,
        document_id: uuid.UUID,
        inline: bool = False,
    ) -> FileResponse:
        """
        Stream a patient document for download or inline browser viewing.

        Access control:
        - The document owner (patient) can always download their own documents
        - Doctors can download documents that were attached to their meetings
        - SaaS admins can download any document

        For doctor access, this is checked at the endpoint level via the
        meeting-specific download endpoint.
        """
        document = await PatientDocumentRepository.get_by_id(session, document_id)

        if not document:
            raise NotFoundError("Document not found")

        # Ownership check for patients
        if requesting_user.role == UserRole.PATIENT:
            if document.patient_id != requesting_user.id:
                raise AuthorizationError("You can only access your own documents")
        elif requesting_user.role == UserRole.SAAS_ADMIN:
            pass  # Admin has full access
        else:
            # For doctors, access is handled via the meeting-document endpoint
            raise AuthorizationError("Unauthorized access to patient document")

        if not os.path.exists(document.file_path):
            raise NotFoundError("Document file not found on server")

        content_disposition_type = "inline" if inline else "attachment"
        return FileResponse(
            path=document.file_path,
            media_type=document.mime_type,
            filename=document.original_filename,
            content_disposition_type=content_disposition_type,
        )
