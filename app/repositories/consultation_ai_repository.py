"""
Consultation AI repository — database operations for transcripts and AI extractions.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.consultation_ai_extraction import ConsultationAIExtraction
from app.models.consultation_transcript import ConsultationTranscript


class ConsultationAIRepository:
    """Handles persistence and retrieval of consultation transcripts and AI extractions."""

    # ── Transcript Operations ────────────────────────────────────────────

    @staticmethod
    async def get_transcript_by_meeting_id(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> Optional[ConsultationTranscript]:
        """Fetch transcript for a meeting."""
        query = (
            select(ConsultationTranscript)
            .where(ConsultationTranscript.meeting_id == meeting_id)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def create_transcript(
        session: AsyncSession,
        transcript: ConsultationTranscript,
    ) -> ConsultationTranscript:
        """Create or update a transcript record."""
        session.add(transcript)
        await session.flush()
        await session.refresh(transcript)
        return transcript

    @staticmethod
    async def update_transcript_audio_path(
        session: AsyncSession,
        transcript: ConsultationTranscript,
        role: str,
        audio_path: str,
    ) -> ConsultationTranscript:
        """Update the audio file path for a specific role (doctor/patient)."""
        if role == "doctor":
            transcript.doctor_audio_path = audio_path
        else:
            transcript.patient_audio_path = audio_path
        await session.flush()
        return transcript

    # ── Extraction Operations ────────────────────────────────────────────

    @staticmethod
    async def get_next_extraction_version(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> int:
        """Get the next version number for a new extraction."""
        query = (
            select(func.coalesce(func.max(ConsultationAIExtraction.version), 0))
            .where(ConsultationAIExtraction.meeting_id == meeting_id)
        )
        result = await session.execute(query)
        current_max = result.scalar_one()
        return current_max + 1

    @staticmethod
    async def create_extraction(
        session: AsyncSession,
        extraction: ConsultationAIExtraction,
    ) -> ConsultationAIExtraction:
        """Persist a new extraction version."""
        session.add(extraction)
        await session.flush()
        await session.refresh(extraction)
        return extraction

    @staticmethod
    async def get_extraction_by_id(
        session: AsyncSession,
        extraction_id: uuid.UUID,
    ) -> Optional[ConsultationAIExtraction]:
        """Fetch an extraction by its ID."""
        query = (
            select(ConsultationAIExtraction)
            .where(ConsultationAIExtraction.id == extraction_id)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_latest_extraction(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> Optional[ConsultationAIExtraction]:
        """Fetch the latest extraction version for a meeting."""
        query = (
            select(ConsultationAIExtraction)
            .where(ConsultationAIExtraction.meeting_id == meeting_id)
            .order_by(ConsultationAIExtraction.version.desc())
            .limit(1)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_extraction_by_version(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        version: int,
    ) -> Optional[ConsultationAIExtraction]:
        """Fetch a specific extraction version for a meeting."""
        query = (
            select(ConsultationAIExtraction)
            .where(
                ConsultationAIExtraction.meeting_id == meeting_id,
                ConsultationAIExtraction.version == version,
            )
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_extraction_versions(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> List[ConsultationAIExtraction]:
        """List all extraction versions for a meeting, ordered by version asc."""
        query = (
            select(ConsultationAIExtraction)
            .where(ConsultationAIExtraction.meeting_id == meeting_id)
            .order_by(ConsultationAIExtraction.version.asc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_approved_extraction(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> Optional[ConsultationAIExtraction]:
        """Fetch the approved extraction for a meeting (if any)."""
        query = (
            select(ConsultationAIExtraction)
            .where(
                ConsultationAIExtraction.meeting_id == meeting_id,
                ConsultationAIExtraction.is_approved == True,  # noqa: E712
            )
            .limit(1)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()
