"""
Doctor repository — data access layer for DoctorProfile and DoctorDocument models.
"""

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.file_upload import delete_file_from_disk
from app.models.doctor_document import DoctorDocument
from app.models.doctor_profile import DoctorProfile
from app.models.enums import UserRole, UserStatus
from app.models.user import User


class DoctorRepository:
    """Data access methods for doctor profiles and documents."""

    @staticmethod
    async def get_profile_by_user_id(
        session: AsyncSession, user_id: uuid.UUID
    ) -> Optional[DoctorProfile]:
        """Fetch a doctor profile by the associated user ID."""
        stmt = (
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_id)
            .options(selectinload(DoctorProfile.documents))
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
                selectinload(DoctorProfile.documents),
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
        # Base filter: user status is PENDING and submitted_at is not null
        base_query = (
            select(DoctorProfile)
            .join(User, DoctorProfile.user_id == User.id)
            .where(
                User.status == UserStatus.PENDING,
                DoctorProfile.submitted_at.is_not(None),
            )
        )

        # Count total
        count_stmt = select(func.count()).select_from(base_query.subquery())
        count_result = await session.execute(count_stmt)
        total = count_result.scalar_one()

        # Query paginated
        stmt = (
            base_query.order_by(DoctorProfile.submitted_at.desc())
            .offset(skip)
            .limit(limit)
            .options(
                selectinload(DoctorProfile.documents),
                selectinload(DoctorProfile.user),
            )
        )
        result = await session.execute(stmt)
        profiles = list(result.scalars().all())

        return profiles, total

    @staticmethod
    async def add_document(
        session: AsyncSession, document: DoctorDocument
    ) -> DoctorDocument:
        """Persist a doctor document record."""
        session.add(document)
        await session.flush()
        return document

    @staticmethod
    async def get_document_by_id(
        session: AsyncSession, doc_id: uuid.UUID
    ) -> Optional[DoctorDocument]:
        """Find a document by ID."""
        stmt = select(DoctorDocument).where(DoctorDocument.id == doc_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_documents_by_profile_id(
        session: AsyncSession, profile_id: uuid.UUID
    ) -> List[DoctorDocument]:
        """Fetch all documents belonging to a doctor profile."""
        stmt = (
            select(DoctorDocument)
            .where(DoctorDocument.doctor_profile_id == profile_id)
            .order_by(DoctorDocument.created_at.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def delete_document(
        session: AsyncSession, document: DoctorDocument
    ) -> None:
        """Remove a document record."""
        await session.delete(document)
        await session.flush()

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

        # Status counts query (independent of pagination and current status filter)
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
                selectinload(DoctorProfile.documents),
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
        Delete doctor user, cascade delete profile & documents, and clean up files on disk.
        """
        stmt = select(User).where(User.id == doctor_user_id, User.role == UserRole.DOCTOR)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            return False

        profile = await DoctorRepository.get_profile_by_user_id(session, doctor_user_id)
        if profile and profile.documents:
            for doc in profile.documents:
                delete_file_from_disk(doc.file_path)

        await session.delete(user)
        await session.commit()
        return True

