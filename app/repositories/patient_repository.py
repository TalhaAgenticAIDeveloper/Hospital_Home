"""
Patient repository — data access layer for PatientProfile model.
"""

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.file_upload import delete_file_from_disk
from app.core.logging import get_logger
from app.models.enums import UserRole
from app.models.meeting import Meeting
from app.models.patient_document import PatientDocument
from app.models.patient_profile import PatientProfile
from app.models.user import User
from app.schemas.patient import PatientProfileUpdateRequest

logger = get_logger(__name__)


class PatientRepository:
    """Data access methods for patient profiles."""

    @staticmethod
    async def get_profile_by_user_id(
        session: AsyncSession, user_id: uuid.UUID
    ) -> Optional[PatientProfile]:
        """Fetch a patient profile by the associated user ID."""
        stmt = (
            select(PatientProfile)
            .where(PatientProfile.user_id == user_id)
            .options(selectinload(PatientProfile.user))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create_or_update_profile(
        session: AsyncSession,
        user_id: uuid.UUID,
        data: PatientProfileUpdateRequest,
    ) -> PatientProfile:
        """Create or update patient profile with the provided fields."""
        profile = await PatientRepository.get_profile_by_user_id(session, user_id)

        update_fields = data.model_dump(exclude_unset=True)

        if not profile:
            profile = PatientProfile(
                user_id=user_id,
                **update_fields,
            )
            # Mark as completed if full_name is present
            if profile.full_name and profile.full_name.strip():
                profile.is_completed = True
            session.add(profile)
        else:
            for field, value in update_fields.items():
                setattr(profile, field, value)
            if profile.full_name and profile.full_name.strip():
                profile.is_completed = True

        await session.commit()
        await session.refresh(profile)
        return profile

    @staticmethod
    async def list_patients(
        session: AsyncSession,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[dict], int]:
        """
        List all patients with profile information and activity counts.
        Supports search across full_name, email, address, gender, or blood group.
        """
        consultations_count_sub = (
            select(func.count(Meeting.id))
            .where(Meeting.patient_id == User.id)
            .correlate(User)
            .scalar_subquery()
        )
        documents_count_sub = (
            select(func.count(PatientDocument.id))
            .where(PatientDocument.patient_id == User.id)
            .correlate(User)
            .scalar_subquery()
        )

        base_query = (
            select(
                User,
                PatientProfile,
                consultations_count_sub.label("consultations_count"),
                documents_count_sub.label("documents_count"),
            )
            .outerjoin(PatientProfile, PatientProfile.user_id == User.id)
            .where(User.role == UserRole.PATIENT)
        )

        count_query = (
            select(func.count(User.id))
            .outerjoin(PatientProfile, PatientProfile.user_id == User.id)
            .where(User.role == UserRole.PATIENT)
        )

        if search and search.strip():
            term = f"%{search.strip()}%"
            search_filter = or_(
                User.email.ilike(term),
                PatientProfile.full_name.ilike(term),
                PatientProfile.address.ilike(term),
                PatientProfile.gender.ilike(term),
                PatientProfile.blood_group.ilike(term),
            )
            base_query = base_query.where(search_filter)
            count_query = count_query.where(search_filter)

        total_res = await session.execute(count_query)
        total = total_res.scalar_one()

        query = (
            base_query.order_by(User.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await session.execute(query)
        rows = result.all()

        items = []
        for user, profile, cons_count, docs_count in rows:
            items.append({
                "patient_id": profile.id if profile else None,
                "user_id": user.id,
                "email": user.email,
                "status": user.status.value,
                "is_active": user.is_active,
                "full_name": profile.full_name if profile else None,
                "age": profile.age if profile else None,
                "date_of_birth": profile.date_of_birth if profile else None,
                "gender": profile.gender if profile else None,
                "blood_group": profile.blood_group if profile else None,
                "address": profile.address if profile else None,
                "created_at": user.created_at,
                "consultations_count": cons_count or 0,
                "documents_count": docs_count or 0,
            })

        return items, total

    @staticmethod
    async def delete_patient(
        session: AsyncSession, patient_user_id: uuid.UUID
    ) -> bool:
        """
        Permanently delete a patient user account, clean up attached files on disk,
        and cascade-delete all DB relations.
        """
        stmt = select(User).where(User.id == patient_user_id, User.role == UserRole.PATIENT)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            return False

        # Clean up files on disk from patient_documents
        doc_stmt = select(PatientDocument.file_path).where(PatientDocument.patient_id == patient_user_id)
        doc_result = await session.execute(doc_stmt)
        file_paths = doc_result.scalars().all()
        for fp in file_paths:
            try:
                delete_file_from_disk(fp)
            except Exception as e:
                logger.warning(f"Could not delete patient document file {fp}: {e}")

        await session.delete(user)
        await session.commit()
        return True

