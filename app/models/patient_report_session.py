"""
Patient Report Explainer Models.

Tracks medical report analysis sessions and follow-up interactive chat messages.
Restricted exclusively to patients for educational explanation and follow-up queries.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class PatientReportSession(TimestampMixin, Base):
    """
    Represents an uploaded medical report analysis session for a patient.
    Stores the extracted medical text, initial structured AI explanation,
    and associated conversation history.
    """

    __tablename__ = "patient_report_sessions"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Foreign Key -> Patient User
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # File Metadata
    filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    stored_filename: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    file_path: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
    )
    file_size: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    mime_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    extraction_method: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pdf_text",
        server_default="pdf_text",
    )

    # Extracted Text & Structured Explanation
    report_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    report_explanation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Relationships
    patient: Mapped["User"] = relationship(
        "User",
        foreign_keys=[patient_id],
    )
    messages: Mapped[List["PatientReportMessage"]] = relationship(
        "PatientReportMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="PatientReportMessage.created_at",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<PatientReportSession id={self.id} patient_id={self.patient_id} "
            f"filename={self.filename}>"
        )


class PatientReportMessage(Base):
    """
    Represents a single message in a report explainer conversation thread
    (either the initial report summary or interactive patient/assistant follow-up messages).
    """

    __tablename__ = "patient_report_messages"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Foreign Key -> Report Session
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_report_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Message Attributes
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,  # "user" or "assistant"
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_report_summary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationship
    session: Mapped["PatientReportSession"] = relationship(
        "PatientReportSession",
        back_populates="messages",
    )

    def __repr__(self) -> str:
        return (
            f"<PatientReportMessage id={self.id} session_id={self.session_id} "
            f"role={self.role}>"
        )
