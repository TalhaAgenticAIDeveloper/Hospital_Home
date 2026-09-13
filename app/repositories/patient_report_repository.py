"""
Repository for Patient Medical Report Explainer database operations.

Centralizes all database operations for patient report sessions and follow-up messages.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.patient_report_session import PatientReportMessage, PatientReportSession


class PatientReportRepository:
    """Data access methods for PatientReportSession and PatientReportMessage."""

    @staticmethod
    async def create_session(
        session: AsyncSession,
        report_session: PatientReportSession,
    ) -> PatientReportSession:
        """Persist a new patient report session record."""
        session.add(report_session)
        await session.flush()
        await session.refresh(report_session)
        return report_session

    @staticmethod
    async def get_session_by_id(
        session: AsyncSession,
        session_id: uuid.UUID,
        patient_id: Optional[uuid.UUID] = None,
    ) -> Optional[PatientReportSession]:
        """
        Fetch a report session by ID.
        If patient_id is provided, enforces that the session belongs to that patient.
        """
        query = (
            select(PatientReportSession)
            .options(selectinload(PatientReportSession.messages))
            .where(PatientReportSession.id == session_id)
        )
        if patient_id is not None:
            query = query.where(PatientReportSession.patient_id == patient_id)

        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_sessions_by_patient(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> List[PatientReportSession]:
        """
        List all report sessions uploaded by a patient, ordered by updated_at descending.
        Preloads messages for message count.
        """
        query = (
            select(PatientReportSession)
            .options(selectinload(PatientReportSession.messages))
            .where(PatientReportSession.patient_id == patient_id)
            .order_by(PatientReportSession.updated_at.desc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def delete_session(
        session: AsyncSession,
        report_session: PatientReportSession,
    ) -> None:
        """Delete a report session and all related messages via CASCADE."""
        await session.delete(report_session)
        await session.flush()

    @staticmethod
    async def get_messages_by_session_id(
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> List[PatientReportMessage]:
        """Fetch all messages for a report session in chronological order."""
        query = (
            select(PatientReportMessage)
            .where(PatientReportMessage.session_id == session_id)
            .order_by(PatientReportMessage.created_at.asc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def add_message(
        session: AsyncSession,
        message: PatientReportMessage,
    ) -> PatientReportMessage:
        """Add a chat message and update session updated_at."""
        session.add(message)
        await session.flush()
        await session.refresh(message)

        # Update parent session's updated_at timestamp
        query = select(PatientReportSession).where(PatientReportSession.id == message.session_id)
        res = await session.execute(query)
        parent_session = res.scalar_one_or_none()
        if parent_session:
            parent_session.updated_at = datetime.now(timezone.utc)
            await session.flush()

        return message
