"""
Doctor service — business logic for doctor profile management and application submissions.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.models.doctor_profile import DoctorProfile
from app.models.enums import UserStatus
from app.models.user import User
from app.repositories.doctor_repository import DoctorRepository
from app.schemas.doctor import (
    DoctorApplicationStatusResponse,
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
        """Get the doctor's current profile."""
        profile = await DoctorService.get_or_create_profile(session, user)
        return DoctorProfileResponse(
            id=profile.id,
            user_id=user.id,
            email=user.email,
            status=user.status.value,
            full_name=profile.full_name,
            father_name=profile.father_name,
            pmdc_registration_number=profile.pmdc_registration_number,
            phone_number=profile.phone_number,
            specialization=profile.specialization,
            license_number=profile.license_number or profile.pmdc_registration_number,
            years_of_experience=profile.years_of_experience,
            qualification=profile.qualification,
            bio=profile.bio,
            submitted_at=profile.submitted_at,
            admin_feedback=profile.admin_feedback,
            reviewed_at=profile.reviewed_at,
        )

    @staticmethod
    async def update_profile(
        session: AsyncSession, user: User, data: DoctorProfileUpdateRequest
    ) -> DoctorProfileResponse:
        """Update doctor's mandatory verification and professional information."""
        profile = await DoctorService.get_or_create_profile(session, user)

        # 3 Mandatory Fields
        profile.full_name = data.full_name.strip()
        profile.father_name = data.father_name.strip()
        profile.pmdc_registration_number = data.pmdc_registration_number.strip()
        # Keep license_number in sync with PMDC number for legacy callers
        profile.license_number = data.pmdc_registration_number.strip()

        # Optional Fields
        profile.phone_number = data.phone_number.strip() if data.phone_number else None
        profile.specialization = data.specialization.strip() if data.specialization else None
        profile.years_of_experience = data.years_of_experience
        profile.qualification = data.qualification.strip() if data.qualification else None
        profile.bio = data.bio.strip() if data.bio else None

        await session.commit()
        logger.info(f"Doctor profile updated for user_id={user.id} pmdc={profile.pmdc_registration_number}")

        return await DoctorService.get_profile(session, user)

    @staticmethod
    async def submit_application(
        session: AsyncSession, user: User
    ) -> DoctorApplicationStatusResponse:
        """
        Submit the completed profile for SaaS Admin review.

        Validates that the 3 mandatory fields (Full Name, Father Name, PMDC Registration Number)
        are provided. No document upload is required.
        If previously rejected, resets status to PENDING and clears feedback.
        """
        profile = await DoctorService.get_or_create_profile(session, user)

        # Validate 3 mandatory fields
        missing_fields = []
        if not profile.full_name or not profile.full_name.strip():
            missing_fields.append("Full Name")
        if not profile.father_name or not profile.father_name.strip():
            missing_fields.append("Father Name")
        if not profile.pmdc_registration_number or not profile.pmdc_registration_number.strip():
            missing_fields.append("PMDC Registration Number")

        if missing_fields:
            raise ValidationError(
                detail=f"Please complete all mandatory profile fields before submitting: {', '.join(missing_fields)}"
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
            message="Application submitted successfully for administrative review.",
        )

    @staticmethod
    async def get_application_status(
        session: AsyncSession, user: User
    ) -> DoctorApplicationStatusResponse:
        """Check application status and any admin feedback."""
        profile = await DoctorService.get_or_create_profile(session, user)

        msg = "Your account is active."
        if user.status == UserStatus.PENDING:
            if profile.submitted_at:
                msg = "Your application is currently under review by our administration team."
            else:
                msg = "Please complete your mandatory profile details (Full Name, Father Name, PMDC Registration Number) to submit your application."
        elif user.status == UserStatus.REJECTED:
            msg = "Your application was rejected. Please review the feedback below, update your details, and re-submit."
        elif user.status == UserStatus.SUSPENDED:
            msg = "Your account has been suspended."

        return DoctorApplicationStatusResponse(
            status=user.status.value,
            submitted_at=profile.submitted_at,
            reviewed_at=profile.reviewed_at,
            admin_feedback=profile.admin_feedback,
            message=msg,
        )
