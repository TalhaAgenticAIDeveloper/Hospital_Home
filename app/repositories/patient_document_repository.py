"""
Repository for Patient Document database operations.

All database queries for patient medical documents are centralized here.
No business logic — only SQL operations via SQLAlchemy.
"""

import uuid
from typing import List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_document import PatientDocument


class PatientDocumentRepository:
    """Data access methods for the PatientDocument model."""

    @staticmethod
    async def create(
        session: AsyncSession,
        document: PatientDocument,
    ) -> PatientDocument:
        """Persist a new patient document record."""
        session.add(document)
        await session.flush()
        await session.refresh(document)
        return document

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        document_id: uuid.UUID,
    ) -> Optional[PatientDocument]:
        """Fetch a specific patient document by ID."""
        query = select(PatientDocument).where(PatientDocument.id == document_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_patient_id(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> List[PatientDocument]:
        """List all documents uploaded by a patient, ordered by creation date."""
        query = (
            select(PatientDocument)
            .where(PatientDocument.patient_id == patient_id)
            .order_by(PatientDocument.created_at.desc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def count_by_patient_id(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> int:
        """Count how many documents a patient has uploaded."""
        query = (
            select(func.count(PatientDocument.id))
            .where(PatientDocument.patient_id == patient_id)
        )
        result = await session.execute(query)
        return result.scalar_one()

    @staticmethod
    async def get_documents_by_ids(
        session: AsyncSession,
        document_ids: List[uuid.UUID],
        patient_id: uuid.UUID,
    ) -> List[PatientDocument]:
        """
        Fetch multiple documents by their IDs, filtered by patient ownership.

        Used during booking to validate that all selected document IDs
        belong to the booking patient.
        """
        if not document_ids:
            return []

        query = (
            select(PatientDocument)
            .where(
                and_(
                    PatientDocument.id.in_(document_ids),
                    PatientDocument.patient_id == patient_id,
                )
            )
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def delete(
        session: AsyncSession,
        document: PatientDocument,
    ) -> None:
        """Delete a patient document record from the database."""
        await session.delete(document)
        await session.flush()
