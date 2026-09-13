"""
Rating repository — database operations for doctor ratings and feedback.
"""

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor_profile import DoctorProfile
from app.models.doctor_rating import DoctorRating


class RatingRepository:
    """Handles persistence and rating aggregation for doctor consultations."""

    @staticmethod
    async def create_rating(
        session: AsyncSession,
        rating: DoctorRating,
    ) -> DoctorRating:
        """Persist a new rating."""
        session.add(rating)
        await session.flush()
        await session.refresh(rating)
        return rating

    @staticmethod
    async def get_by_meeting_id(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> Optional[DoctorRating]:
        """Fetch rating for a given meeting."""
        query = select(DoctorRating).where(DoctorRating.meeting_id == meeting_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def recalculate_and_update_doctor_rating(
        session: AsyncSession,
        doctor_id: uuid.UUID,
    ) -> Tuple[Optional[float], int]:
        """
        Calculate the average rating and count of ratings for a doctor,
        and cache it on the DoctorProfile record.
        """
        calc_query = (
            select(
                func.avg(DoctorRating.rating).label("avg_rating"),
                func.count(DoctorRating.id).label("total_count"),
            )
            .where(DoctorRating.doctor_id == doctor_id)
        )
        res = await session.execute(calc_query)
        row = res.one_or_none()

        avg_val = None
        count_val = 0
        if row:
            raw_avg, count_val = row
            if raw_avg is not None:
                avg_val = round(float(raw_avg), 1)
            count_val = int(count_val) if count_val else 0

        # Update DoctorProfile
        update_stmt = (
            update(DoctorProfile)
            .where(DoctorProfile.user_id == doctor_id)
            .values(
                average_rating=avg_val,
                total_ratings=count_val,
            )
        )
        await session.execute(update_stmt)
        await session.flush()

        return avg_val, count_val

    @staticmethod
    async def list_doctor_ratings(
        session: AsyncSession,
        doctor_id: uuid.UUID,
        limit: int = 50,
    ) -> List[DoctorRating]:
        """List ratings received by a doctor, ordered by most recent."""
        query = (
            select(DoctorRating)
            .where(DoctorRating.doctor_id == doctor_id)
            .order_by(DoctorRating.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(query)
        return list(result.scalars().all())
