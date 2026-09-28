"""
Consultation Summary repository — database operations for consultation summaries.
"""

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.consultation_summary import ConsultationSummary
from app.models.meeting import Meeting


class ConsultationSummaryRepository:
    """Handles persistence and retrieval of consultation summaries."""

    @staticmethod
    async def create_summary(
        session: AsyncSession,
        summary: ConsultationSummary,
    ) -> ConsultationSummary:
        """Persist a new consultation summary."""
        session.add(summary)
        await session.flush()
        await session.refresh(summary)
        return summary

    @staticmethod
    async def get_by_meeting_id(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> Optional[ConsultationSummary]:
        """Fetch the summary for a specific meeting."""
        query = (
            select(ConsultationSummary)
            .where(ConsultationSummary.meeting_id == meeting_id)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_patient_history(
        session: AsyncSession,
        patient_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ConsultationSummary], int]:
        """
        Fetch paginated consultation summaries for a patient,
        ordered by most recent first. Returns (summaries, total_count).
        """
        # Count query
        count_query = (
            select(func.count())
            .select_from(ConsultationSummary)
            .where(
                ConsultationSummary.patient_id == patient_id,
                ConsultationSummary.status == "completed",
            )
        )
        count_result = await session.execute(count_query)
        total = count_result.scalar_one()

        # Data query
        query = (
            select(ConsultationSummary)
            .where(
                ConsultationSummary.patient_id == patient_id,
                ConsultationSummary.status == "completed",
            )
            .order_by(ConsultationSummary.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(query)
        summaries = list(result.scalars().all())

        return summaries, total

    @staticmethod
    async def get_patient_history_for_doctor(
        session: AsyncSession,
        patient_id: uuid.UUID,
        doctor_id: uuid.UUID,
    ) -> List[ConsultationSummary]:
        """
        Fetch all completed consultation summaries for a specific patient.
        Used by the doctor to review patient's past visits before/during consultation.
        """
        query = (
            select(ConsultationSummary)
            .where(
                ConsultationSummary.patient_id == patient_id,
                ConsultationSummary.status == "completed",
            )
            .order_by(ConsultationSummary.created_at.desc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())
