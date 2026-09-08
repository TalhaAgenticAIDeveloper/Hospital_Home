"""
Meeting model — represents scheduled 1-to-1 video/audio telemedicine consultations.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.enums import MeetingStatus

if TYPE_CHECKING:
    from app.models.doctor_availability import DoctorAvailability
    from app.models.meeting_document import MeetingDocument
    from app.models.user import User


class Meeting(TimestampMixin, Base):
    """
    1-to-1 Online Consultation Session between a Doctor and Patient.
    """

    __tablename__ = "meetings"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Unique Room Code for WebRTC Signaling ────────────────────────────
    room_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    # ── Participants ─────────────────────────────────────────────────────
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
        index=True,
    )

    # ── Availability Slot (Optional link) ────────────────────────────────
    availability_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("doctor_availabilities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Meeting Window ───────────────────────────────────────────────────
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ── Status ───────────────────────────────────────────────────────────
    status: Mapped[MeetingStatus] = mapped_column(
        Enum(
            MeetingStatus,
            name="meeting_status",
            values_callable=lambda x: [e.value for e in x],
            create_constraint=True,
        ),
        default=MeetingStatus.SCHEDULED,
        server_default=MeetingStatus.SCHEDULED.value,
        nullable=False,
        index=True,
    )

    # ── Consultation & Clinical Notes ────────────────────────────────────
    patient_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    doctor_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    # ── Meeting Transcript (Urdu & English UTF-8) ─────────────────────────
    transcript_path: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
        default=None,
    )
    transcript_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    # ── Relationships ────────────────────────────────────────────────────
    doctor: Mapped["User"] = relationship(
        "User",
        foreign_keys=[doctor_id],
        lazy="selectin",
    )
    patient: Mapped["User"] = relationship(
        "User",
        foreign_keys=[patient_id],
        lazy="selectin",
    )
    availability: Mapped[Optional["DoctorAvailability"]] = relationship(
        "DoctorAvailability",
        back_populates="meeting",
        foreign_keys=[availability_id],
        lazy="selectin",
    )
    attached_documents: Mapped[List["MeetingDocument"]] = relationship(
        "MeetingDocument",
        back_populates="meeting",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_meetings_doctor_status", "doctor_id", "status"),
        Index("ix_meetings_patient_status", "patient_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Meeting id={self.id} room_id={self.room_id} doctor={self.doctor_id} patient={self.patient_id} status={self.status}>"
