"""
ConsultationAIExtraction model — stores versioned AI-extracted structured data
from consultation transcripts with doctor review/approval workflow.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class ConsultationAIExtraction(TimestampMixin, Base):
    """
    Versioned AI extraction of structured medical data from a consultation transcript.

    Each extraction attempt creates a new version row (never overwrites).
    Only ONE version per meeting can be approved (is_approved=True).
    On approval, a Prescription record is created and linked via prescription_id.
    """

    __tablename__ = "consultation_ai_extractions"

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
        nullable=False,
        index=True,
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("consultation_transcripts.id", ondelete="CASCADE"),
        nullable=False,
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Versioning ───────────────────────────────────────────────────────
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    # ── Extraction Data (Pydantic-validated JSONB) ───────────────────────
    extraction_data: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=None,
    )

    # ── Raw LLM Response (audit only, never exposed via API) ─────────────
    raw_llm_response: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )

    # ── Processing Status ────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="processing", server_default="processing",
    )
    confidence_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None,
    )
    llm_model_used: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, default=None,
    )
    total_chunks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1",
    )
    processing_time_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )

    # ── Approval Workflow ────────────────────────────────────────────────
    is_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    approved_extraction_data: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=None,
    )
    doctor_approval_notes: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )
    prescription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prescriptions.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        default=None,
    )

    # ── Relationships ────────────────────────────────────────────────────
    meeting = relationship("Meeting", foreign_keys=[meeting_id], lazy="selectin")
    transcript = relationship(
        "ConsultationTranscript", foreign_keys=[transcript_id], lazy="selectin",
    )
    doctor = relationship("User", foreign_keys=[doctor_id])
    patient = relationship("User", foreign_keys=[patient_id])
    prescription = relationship("Prescription", foreign_keys=[prescription_id])

    # ── Composite Indexes ────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "ix_consultation_ai_extractions_meeting_version",
            "meeting_id",
            "version",
            unique=True,
        ),
        Index(
            "ix_consultation_ai_extractions_status",
            "status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ConsultationAIExtraction id={self.id} meeting_id={self.meeting_id} "
            f"version={self.version} status={self.status} approved={self.is_approved}>"
        )
