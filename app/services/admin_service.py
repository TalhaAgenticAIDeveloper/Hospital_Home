"""
Admin service — business logic for SaaS Admin authentication and doctor application review.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.admin_refresh_token import AdminRefreshToken
from app.models.enums import UserRole, UserStatus
from app.models.refresh_token import RefreshToken
from app.models.saas_admin import SaaSAdmin
from app.models.user import User
from app.repositories.admin_token_repository import AdminTokenRepository
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.saas_admin_repository import SaaSAdminRepository
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.admin import (
    AllDoctorsListResponse,
    DoctorReviewRequest,
    DoctorReviewResponse,
    DoctorStatusCounts,
    PatientAdminListItem,
    PatientAdminListResponse,
    PendingDoctorDetailResponse,
    PendingDoctorListItem,
    PendingDoctorListResponse,
)
from app.schemas.auth import AdminLoginRequest, LoginResponse
from app.schemas.user import UserBriefResponse

logger = get_logger(__name__)


class AdminService:
    """Handles SaaS Admin authentication and management workflows."""

    @staticmethod
    async def admin_login(
        session: AsyncSession, data: AdminLoginRequest
    ) -> LoginResponse:
        """
        Authenticate a SaaS Admin from the dedicated saas_admins table.

        Raises:
            AuthenticationError: Invalid credentials or account inactive.
        """
        normalized_email = data.email.lower().strip()

        admin = await SaaSAdminRepository.get_by_email(session, normalized_email)
        if not admin:
            # Timing-safe: still hash to prevent timing attacks
            hash_password("dummy-password-for-timing")
            raise AuthenticationError()

        # Verify password
        if not verify_password(data.password, admin.password_hash):
            logger.info("admin_login_failure: invalid credentials")
            raise AuthenticationError()

        # Check status
        if not admin.is_active:
            raise AuthenticationError(detail="Account is not active")

        # Generate tokens
        access_token = create_access_token(
            sub=str(admin.id),
            role=UserRole.SAAS_ADMIN.value,
        )
        refresh_token = create_refresh_token(
            sub=str(admin.id),
            role=UserRole.SAAS_ADMIN.value,
        )

        # Store refresh token hash in admin_refresh_tokens
        decoded = decode_token(refresh_token)
        token_record = AdminRefreshToken(
            admin_id=admin.id,
            token_hash=hash_token(refresh_token),
            expires_at=datetime.fromtimestamp(decoded["exp"], tz=timezone.utc),
        )

        try:
            await AdminTokenRepository.create(session, token_record)
            await SaaSAdminRepository.update_last_login(session, admin.id)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        logger.info(f"admin_login_success: email={admin.email}")

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user=UserBriefResponse(
                id=admin.id,
                email=admin.email,
                role=UserRole.SAAS_ADMIN.value,
                status=UserStatus.ACTIVE.value,
            ),
        )


    # ── Doctor Application Review ────────────────────────────────────────

    @staticmethod
    async def list_pending_doctors(
        session: AsyncSession, skip: int = 0, limit: int = 50
    ) -> PendingDoctorListResponse:
        """List all pending doctor applications awaiting SaaS Admin review."""
        profiles, total = await DoctorRepository.get_pending_applications(
            session, skip=skip, limit=limit
        )

        items = []
        for profile in profiles:
            items.append(
                PendingDoctorListItem(
                    doctor_id=profile.id,
                    user_id=profile.user_id,
                    email=profile.user.email if profile.user else "",
                    full_name=profile.full_name,
                    father_name=profile.father_name,
                    pmdc_registration_number=profile.pmdc_registration_number,
                    specialization=profile.specialization,
                    license_number=profile.license_number or profile.pmdc_registration_number,
                    years_of_experience=profile.years_of_experience,
                    submitted_at=profile.submitted_at,
                    status=profile.user.status.value if profile.user else "pending",
                    document_count=0,
                )
            )

        return PendingDoctorListResponse(total=total, items=items)

    @staticmethod
    async def list_all_doctors(
        session: AsyncSession,
        status: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> AllDoctorsListResponse:
        """List all doctors with status breakdown counts and optional filters."""
        profiles, filtered_total, counts = await DoctorRepository.list_all_doctors(
            session, status_filter=status, search=search, skip=skip, limit=limit
        )

        items = []
        for profile in profiles:
            items.append(
                PendingDoctorListItem(
                    doctor_id=profile.id,
                    user_id=profile.user_id,
                    email=profile.user.email if profile.user else "",
                    full_name=profile.full_name,
                    father_name=profile.father_name,
                    pmdc_registration_number=profile.pmdc_registration_number,
                    specialization=profile.specialization,
                    license_number=profile.license_number or profile.pmdc_registration_number,
                    years_of_experience=profile.years_of_experience,
                    submitted_at=profile.submitted_at,
                    status=profile.user.status.value if profile.user else "pending",
                    document_count=0,
                )
            )

        counts_obj = DoctorStatusCounts(**counts)
        return AllDoctorsListResponse(
            total=filtered_total,
            counts=counts_obj,
            status_counts=counts_obj,
            items=items,
        )

    @staticmethod
    async def delete_doctor(
        session: AsyncSession, doctor_user_id: uuid.UUID
    ) -> dict:
        """Delete doctor account, profile, documents, and disk files."""
        deleted = await DoctorRepository.delete_doctor(session, doctor_user_id)
        if not deleted:
            raise NotFoundError(detail="Doctor not found")
        logger.info(f"Doctor deleted successfully: doctor_user_id={doctor_user_id}")
        return {"message": "Doctor account deleted successfully"}

    @staticmethod
    async def get_doctor_detail(
        session: AsyncSession, doctor_user_id: uuid.UUID
    ) -> PendingDoctorDetailResponse:
        """Get complete doctor profile with uploaded documents for review."""
        user = await UserRepository.get_by_id(session, doctor_user_id)
        if not user or user.role != UserRole.DOCTOR:
            raise NotFoundError(detail="Doctor not found")

        profile = await DoctorRepository.get_profile_by_user_id(session, doctor_user_id)
        if not profile:
            raise NotFoundError(detail="Doctor profile not found")

        return PendingDoctorDetailResponse(
            doctor_id=profile.id,
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
            documents=[],
        )

    @staticmethod
    async def review_doctor(
        session: AsyncSession,
        doctor_user_id: uuid.UUID,
        admin_user: User | SaaSAdmin,
        data: DoctorReviewRequest,
    ) -> DoctorReviewResponse:
        """
        Approve or reject a doctor application.

        - Approve: status becomes ACTIVE (doctor can log in to full platform).
        - Reject: status becomes REJECTED and admin_feedback is recorded so the
          doctor can see the reason and re-submit.
        """
        user = await UserRepository.get_by_id(session, doctor_user_id)
        if not user or user.role != UserRole.DOCTOR:
            raise NotFoundError(detail="Doctor account not found")

        profile = await DoctorRepository.get_profile_by_user_id(session, doctor_user_id)
        if not profile:
            raise NotFoundError(detail="Doctor profile not found")

        now = datetime.now(timezone.utc)
        profile.reviewed_at = now
        profile.reviewed_by = admin_user.id

        if data.action == "approve":
            user.status = UserStatus.ACTIVE
            profile.admin_feedback = (
                data.feedback.strip() if data.feedback else None
            )
            msg = "Doctor application approved successfully."
            logger.info(
                f"Doctor application APPROVED: doctor_user_id={user.id} by admin={admin_user.id}"
            )
        elif data.action == "reject":
            if not data.feedback or not data.feedback.strip():
                raise ValidationError(
                    detail="Feedback reason is required when rejecting an application."
                )
            user.status = UserStatus.REJECTED
            profile.admin_feedback = data.feedback.strip()
            msg = "Doctor application rejected with feedback."
            logger.info(
                f"Doctor application REJECTED: doctor_user_id={user.id} by admin={admin_user.id}"
            )
        else:
            raise ValidationError(detail=f"Invalid action '{data.action}'")

        await session.commit()

        return DoctorReviewResponse(
            message=msg,
            doctor_id=profile.id,
            user_id=user.id,
            new_status=user.status.value,
            admin_feedback=profile.admin_feedback,
            reviewed_at=now,
        )

    # ── Patient Management ───────────────────────────────────────────────

    @staticmethod
    async def list_patients(
        session: AsyncSession,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> PatientAdminListResponse:
        """List all patients with profile data and activity counts for SaaS Admin."""
        items_raw, total = await PatientRepository.list_patients(
            session, search=search, skip=skip, limit=limit
        )
        items = [PatientAdminListItem(**item) for item in items_raw]
        return PatientAdminListResponse(total=total, items=items)

    @staticmethod
    async def delete_patient(
        session: AsyncSession, patient_user_id: uuid.UUID
    ) -> dict:
        """Permanently delete a patient user account and attached resources."""
        deleted = await PatientRepository.delete_patient(session, patient_user_id)
        if not deleted:
            raise NotFoundError(detail="Patient not found")
        logger.info(f"Patient deleted successfully: patient_user_id={patient_user_id}")
        return {"message": "Patient account deleted successfully"}

