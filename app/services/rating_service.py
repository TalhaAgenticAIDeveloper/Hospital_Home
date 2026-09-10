"""
Rating service — business logic for patient-to-doctor ratings and feedback.
"""

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.doctor_rating import DoctorRating
from app.models.enums import MeetingStatus, UserRole
from app.models.user import User
from app.repositories.meeting_repository import MeetingRepository
from app.repositories.rating_repository import RatingRepository
from app.schemas.rating import RatingCreateRequest, RatingResponse

logger = get_logger(__name__)


class RatingService:
    """Service managing consultation ratings and doctor score updates."""

    @staticmethod
    async def submit_rating(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        current_user: User,
        payload: RatingCreateRequest,
    ) -> RatingResponse:
        """
        Submit a 1-5 star rating and optional feedback for a completed meeting.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        # Must be completed
        if meeting.status != MeetingStatus.COMPLETED:
            raise ValidationError("Ratings can only be submitted for completed consultations")

        # Only the patient can rate the doctor
        if meeting.patient_id != current_user.id:
            raise AuthorizationError("Only the patient who attended this consultation can submit a rating")

        # Ensure no duplicate rating for this meeting
        existing = await RatingRepository.get_by_meeting_id(session, meeting_id)
        if existing:
            raise ConflictError("A rating has already been submitted for this consultation")

        rating = DoctorRating(
            meeting_id=meeting.id,
            doctor_id=meeting.doctor_id,
            patient_id=meeting.patient_id,
            rating=payload.rating,
            feedback_text=payload.feedback_text.strip() if payload.feedback_text else None,
        )

        saved_rating = await RatingRepository.create_rating(session, rating)

        # Recalculate doctor's overall average and total count
        avg_rating, total_count = await RatingRepository.recalculate_and_update_doctor_rating(
            session, meeting.doctor_id
        )

        await session.commit()
        await session.refresh(saved_rating)

        logger.info(
            f"doctor_rating_submitted: doctor_id={meeting.doctor_id} patient_id={current_user.id} "
            f"rating={payload.rating} new_avg={avg_rating} total={total_count}"
        )

        return RatingResponse.model_validate(saved_rating)

    @staticmethod
    async def get_meeting_rating(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        current_user: User,
    ) -> RatingResponse:
        """
        Retrieve rating for a specific meeting.
        Accessible by the patient, the doctor, or an admin.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if (
            current_user.role != UserRole.SAAS_ADMIN
            and meeting.patient_id != current_user.id
            and meeting.doctor_id != current_user.id
        ):
            raise AuthorizationError("You are not authorized to view this rating")

        rating = await RatingRepository.get_by_meeting_id(session, meeting_id)
        if not rating:
            raise NotFoundError("No rating has been submitted for this meeting yet")

        return RatingResponse.model_validate(rating)
