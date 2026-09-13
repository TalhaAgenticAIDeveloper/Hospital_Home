"""
Prescription repository — database operations for prescriptions and medicine schedules.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.doctor_profile import DoctorProfile
from app.models.patient_profile import PatientProfile
from app.models.prescription import Prescription, PrescriptionMedicine
from app.models.user import User


class PrescriptionRepository:
    """Handles persistence and retrieval of prescriptions and medicine intake schedules."""

    @staticmethod
    async def create_prescription(
        session: AsyncSession,
        prescription: Prescription,
    ) -> Prescription:
        """Persist a new prescription along with its medicines."""
        session.add(prescription)
        await session.flush()
        await session.refresh(prescription)
        return prescription

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        prescription_id: uuid.UUID,
    ) -> Optional[Prescription]:
        """Fetch a prescription by ID, eager loading medicines and parties."""
        query = (
            select(Prescription)
            .where(Prescription.id == prescription_id)
            .options(
                selectinload(Prescription.medicines),
                selectinload(Prescription.doctor).selectinload(User.doctor_profile),
                selectinload(Prescription.patient).selectinload(User.patient_profile),
            )
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_meeting_id(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> Optional[Prescription]:
        """Fetch prescription for a specific meeting, eager loading medicines."""
        query = (
            select(Prescription)
            .where(Prescription.meeting_id == meeting_id)
            .options(
                selectinload(Prescription.medicines),
                selectinload(Prescription.doctor).selectinload(User.doctor_profile),
                selectinload(Prescription.patient).selectinload(User.patient_profile),
            )
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_patient_id(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> List[Prescription]:
        """List all prescriptions issued to a patient, ordered newest first."""
        query = (
            select(Prescription)
            .where(Prescription.patient_id == patient_id)
            .options(
                selectinload(Prescription.medicines),
                selectinload(Prescription.doctor).selectinload(User.doctor_profile),
            )
            .order_by(Prescription.created_at.desc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def list_by_doctor_id(
        session: AsyncSession,
        doctor_id: uuid.UUID,
    ) -> List[Prescription]:
        """List all prescriptions issued by a doctor, ordered newest first."""
        query = (
            select(Prescription)
            .where(Prescription.doctor_id == doctor_id)
            .options(
                selectinload(Prescription.medicines),
                selectinload(Prescription.patient).selectinload(User.patient_profile),
            )
            .order_by(Prescription.created_at.desc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())
