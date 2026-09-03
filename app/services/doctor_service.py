"""
Doctor service — business logic for doctor profile management, document uploads, and application submissions.
"""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.file_upload import delete_file_from_disk, save_upload_file, validate_file
from app.core.logging import get_logger
from app.models.doctor_document import DoctorDocument
from app.models.doctor_profile import DoctorProfile
from app.models.enums import DocumentType, UserStatus
from app.models.user import User
from app.repositories.doctor_repository import DoctorRepository
from app.schemas.doctor import (
    DoctorApplicationStatusResponse,
    DoctorDocumentResponse,
    DoctorProfileResponse,
    DoctorProfileUpdateRequest,
)

logger = get_logger(__name__)


class DoctorService:
    """Handles doctor profile and onboarding operations."""

    @staticmethod
    async def get_or_create_profile(
        session: AsyncSession, user: User
    ) -> DoctorProfile:
        """Ensure doctor profile exists for the user."""
        profile = await DoctorRepository.get_profile_by_user_id(session, user.id)
        if not profile:
            profile = DoctorProfile(user_id=user.id)
            session.add(profile)
            await session.flush()
        return profile

    @staticmethod
    async def get_profile(
        session: AsyncSession, user: User
    ) -> DoctorProfileResponse:
        """Get the doctor's current profile and documents."""
        profile = await DoctorService.get_or_create_profile(session, user)
        return DoctorProfileResponse(
            id=profile.id,
            user_id=user.id,
            email=user.email,
            status=user.status.value,
            full_name=profile.full_name,
            phone_number=profile.phone_number,
            specialization=profile.specialization,
            license_number=profile.license_number,
            years_of_experience=profile.years_of_experience,
            qualification=profile.qualification,
            bio=profile.bio,
            submitted_at=profile.submitted_at,
            admin_feedback=profile.admin_feedback,
            reviewed_at=profile.reviewed_at,
            documents=[
                DoctorDocumentResponse.model_validate(doc)
                for doc in profile.documents
            ],
        )

    @staticmethod
    async def update_profile(
        session: AsyncSession, user: User, data: DoctorProfileUpdateRequest
    ) -> DoctorProfileResponse:
        """Update doctor's professional information."""
        profile = await DoctorService.get_or_create_profile(session, user)

        profile.full_name = data.full_name.strip()
        profile.phone_number = data.phone_number.strip()
        profile.specialization = data.specialization.strip()
        profile.license_number = data.license_number.strip()
        profile.years_of_experience = data.years_of_experience
        profile.qualification = data.qualification.strip()
        profile.bio = data.bio.strip() if data.bio else None

        await session.commit()
        logger.info(f"Doctor profile updated for user_id={user.id}")

        return await DoctorService.get_profile(session, user)

    @staticmethod
    async def upload_document(
        session: AsyncSession,
        user: User,
        file: UploadFile,
        document_type: DocumentType,
    ) -> DoctorDocumentResponse:
        """Validate, save, and record an uploaded document."""
        profile = await DoctorService.get_or_create_profile(session, user)

        # Validate file
        validate_file(file)

        # Save to disk
        orig_name, stored_name, file_path, file_size, mime_type = (
            await save_upload_file(file)
        )

        # Create database record
        doc_record = DoctorDocument(
            doctor_profile_id=profile.id,
            document_type=document_type,
            original_filename=orig_name,
            stored_filename=stored_name,
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
        )

        try:
            await DoctorRepository.add_document(session, doc_record)
            await session.commit()
        except Exception:
            await session.rollback()
            delete_file_from_disk(file_path)
            raise

        logger.info(
            f"Document uploaded for user_id={user.id} type={document_type.value}"
        )
        return DoctorDocumentResponse.model_validate(doc_record)

    @staticmethod
    async def list_documents(
        session: AsyncSession, user: User
    ) -> List[DoctorDocumentResponse]:
        """List all documents uploaded by the doctor."""
        profile = await DoctorService.get_or_create_profile(session, user)
        docs = await DoctorRepository.get_documents_by_profile_id(
            session, profile.id
        )
        return [DoctorDocumentResponse.model_validate(doc) for doc in docs]

    @staticmethod
    async def delete_document(
        session: AsyncSession, user: User, doc_id: uuid.UUID
    ) -> None:
        """Delete an uploaded document."""
        profile = await DoctorService.get_or_create_profile(session, user)
        doc = await DoctorRepository.get_document_by_id(session, doc_id)

        if not doc or doc.doctor_profile_id != profile.id:
            raise NotFoundError(detail="Document not found")

        # Delete from disk and DB
        delete_file_from_disk(doc.file_path)
        await DoctorRepository.delete_document(session, doc)
        await session.commit()
        logger.info(f"Document {doc_id} deleted for user_id={user.id}")

    @staticmethod
    async def submit_application(
        session: AsyncSession, user: User
    ) -> DoctorApplicationStatusResponse:
        """
        Submit the completed profile and documents for SaaS Admin review.

        Validates that required details and documents are present.
        If previously rejected, resets status to PENDING and clears feedback.
        """
        profile = await DoctorService.get_or_create_profile(session, user)

        # Validate mandatory fields
        missing_fields = []
        if not profile.full_name:
            missing_fields.append("full_name")
        if not profile.phone_number:
            missing_fields.append("phone_number")
        if not profile.specialization:
            missing_fields.append("specialization")
        if not profile.license_number:
            missing_fields.append("license_number")
        if not profile.qualification:
            missing_fields.append("qualification")

        if missing_fields:
            raise ValidationError(
                detail=f"Please complete your profile before submitting. Missing: {', '.join(missing_fields)}"
            )

        # Validate that at least one document is uploaded
        docs = await DoctorRepository.get_documents_by_profile_id(
            session, profile.id
        )
        if not docs:
            raise ValidationError(
                detail="Please upload at least one verification document (e.g. medical license) before submitting."
            )

        now = datetime.now(timezone.utc)
        profile.submitted_at = now
        profile.admin_feedback = None  # Clear previous rejection feedback on re-submission
        user.status = UserStatus.PENDING

        await session.commit()
        logger.info(f"Doctor application submitted for user_id={user.id}")

        return DoctorApplicationStatusResponse(
            status=user.status.value,
            submitted_at=now,
            reviewed_at=profile.reviewed_at,
            admin_feedback=None,
            message="Application submitted successfully for review.",
        )

    @staticmethod
    async def get_application_status(
        session: AsyncSession, user: User
    ) -> DoctorApplicationStatusResponse:
        """Check application status and any feedback."""
        profile = await DoctorService.get_or_create_profile(session, user)

        msg = "Your account is active."
        if user.status == UserStatus.PENDING:
            if profile.submitted_at:
                msg = "Your application is currently under review by our admin team."
            else:
                msg = "Please complete your profile and upload documents to submit your application."
        elif user.status == UserStatus.REJECTED:
            msg = "Your application was rejected. Please review the feedback below, update your details/documents, and re-submit."
        elif user.status == UserStatus.SUSPENDED:
            msg = "Your account has been suspended."

        return DoctorApplicationStatusResponse(
            status=user.status.value,
            submitted_at=profile.submitted_at,
            reviewed_at=profile.reviewed_at,
            admin_feedback=profile.admin_feedback,
            message=msg,
        )
