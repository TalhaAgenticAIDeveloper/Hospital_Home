"""
Meeting document model — junction table linking patient documents to meetings.

When a patient books an appointment, they can optionally select which of their
uploaded medical documents should be shared with the consulting doctor.
This model tracks those selections.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.meeting import Meeting
    from app.models.patient_document import PatientDocument


class MeetingDocument(TimestampMixin, Base):
    """
    Association between a Meeting and a PatientDocument.

    Tracks which patient documents were attached to a specific
    consultation appointment for the doctor to review.
    """

    __tablename__ = "meeting_documents"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Keys ─────────────────────────────────────────────────────
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    patient_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship(
        "Meeting",
        back_populates="attached_documents",
        foreign_keys=[meeting_id],
    )
    patient_document: Mapped["PatientDocument"] = relationship(
        "PatientDocument",
        lazy="selectin",
        foreign_keys=[patient_document_id],
    )

    # ── Table Constraints ────────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "patient_document_id",
            name="uq_meeting_patient_document",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MeetingDocument id={self.id} meeting={self.meeting_id} "
            f"doc={self.patient_document_id}>"
        )
