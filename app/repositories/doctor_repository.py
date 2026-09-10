"""
Doctor repository — data access layer for DoctorProfile model.
"""

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.doctor_profile import DoctorProfile
from app.models.enums import UserRole, UserStatus
from app.models.user import User


class DoctorRepository:
    """Data access methods for doctor profiles."""

    @staticmethod
    async def get_profile_by_user_id(
        session: AsyncSession, user_id: uuid.UUID
    ) -> Optional[DoctorProfile]:
        """Fetch a doctor profile by the associated user ID."""
        stmt = (
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_profile_by_id(
        session: AsyncSession, profile_id: uuid.UUID
    ) -> Optional[DoctorProfile]:
        """Fetch a doctor profile by profile UUID."""
        stmt = (
            select(DoctorProfile)
            .where(DoctorProfile.id == profile_id)
            .options(
                selectinload(DoctorProfile.user),
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_pending_applications(
        session: AsyncSession, skip: int = 0, limit: int = 50
    ) -> Tuple[List[DoctorProfile], int]:
        """
        Fetch pending doctor applications that have been submitted.

        Returns (profiles, total_count).
        """
        base_query = (
            select(DoctorProfile)
            .join(User, DoctorProfile.user_id == User.id)
            .where(
                User.status == UserStatus.PENDING,
                DoctorProfile.submitted_at.is_not(None),
            )
        )

        count_stmt = select(func.count()).select_from(base_query.subquery())
        count_result = await session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = (
            base_query.order_by(DoctorProfile.submitted_at.desc())
            .offset(skip)
            .limit(limit)
            .options(
                selectinload(DoctorProfile.user),
            )
        )
        result = await session.execute(stmt)
        profiles = list(result.scalars().all())

        return profiles, total

    @staticmethod
    async def list_all_doctors(
        session: AsyncSession,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[DoctorProfile], int, dict]:
        """
        Fetch doctors with optional status filtering and search query.
        Returns (profiles, filtered_total, counts_by_status).
        """
        base_query = (
            select(DoctorProfile)
            .join(User, DoctorProfile.user_id == User.id)
            .where(User.role == UserRole.DOCTOR)
        )

        # Status counts query
        counts = {"total": 0, "pending": 0, "active": 0, "rejected": 0}
        status_counts_stmt = (
            select(User.status, func.count(DoctorProfile.id))
            .join(User, DoctorProfile.user_id == User.id)
            .where(User.role == UserRole.DOCTOR)
            .group_by(User.status)
        )
        status_counts_res = await session.execute(status_counts_stmt)
        for st, cnt in status_counts_res.all():
            st_val = st.value if hasattr(st, "value") else str(st)
            if st_val in counts:
                counts[st_val] = cnt
            counts["total"] += cnt

        # Filter by status if provided and not "all"
        if status_filter and status_filter.lower() != "all":
            try:
                enum_status = UserStatus(status_filter.lower())
                base_query = base_query.where(User.status == enum_status)
            except ValueError:
                pass

        # Filter by search string if provided
        if search and search.strip():
            term = f"%{search.strip()}%"
            base_query = base_query.where(
                or_(
                    DoctorProfile.full_name.ilike(term),
                    DoctorProfile.father_name.ilike(term),
                    DoctorProfile.pmdc_registration_number.ilike(term),
                    User.email.ilike(term),
                    DoctorProfile.specialization.ilike(term),
                    DoctorProfile.license_number.ilike(term),
                )
            )

        # Count total matching query
        count_stmt = select(func.count()).select_from(base_query.subquery())
        count_result = await session.execute(count_stmt)
        filtered_total = count_result.scalar_one()

        # Query paginated with relationships
        stmt = (
            base_query.order_by(DoctorProfile.created_at.desc())
            .offset(skip)
            .limit(limit)
            .options(
                selectinload(DoctorProfile.user),
            )
        )
        result = await session.execute(stmt)
        profiles = list(result.scalars().all())

        return profiles, filtered_total, counts

    @staticmethod
    async def delete_doctor(
        session: AsyncSession, doctor_user_id: uuid.UUID
    ) -> bool:
        """
        Delete doctor user and cascade delete profile.
        """
        stmt = select(User).where(User.id == doctor_user_id, User.role == UserRole.DOCTOR)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            return False

        await session.delete(user)
        await session.commit()
        return True
