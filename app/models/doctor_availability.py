"""
Doctor Availability model — represents available time slots defined by doctors.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.meeting import Meeting
    from app.models.user import User


class DoctorAvailability(TimestampMixin, Base):
    """
    Availability slots configured by doctors.

    Can be booked by patients for 1-to-1 video/audio meetings.
    """

    __tablename__ = "doctor_availabilities"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Key (Doctor user ID) ─────────────────────────────────────
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Slot Timings ─────────────────────────────────────────────────────
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # ── Booking State ────────────────────────────────────────────────────
    is_booked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
        index=True,
    )

    # ── Relationships ────────────────────────────────────────────────────
    doctor: Mapped["User"] = relationship(
        "User",
        foreign_keys=[doctor_id],
        lazy="selectin",
    )
    meeting: Mapped[Optional["Meeting"]] = relationship(
        "Meeting",
        back_populates="availability",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_doctor_availability_slot", "doctor_id", "start_time", "end_time"),
    )

    def __repr__(self) -> str:
        return f"<DoctorAvailability id={self.id} doctor_id={self.doctor_id} start={self.start_time} booked={self.is_booked}>"
