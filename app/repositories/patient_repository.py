"""
Patient repository — data access layer for PatientProfile model.
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.patient_profile import PatientProfile
from app.schemas.patient import PatientProfileUpdateRequest


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
