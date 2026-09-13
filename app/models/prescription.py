"""
Prescription models — prescriptions and detailed medicine schedules.
"""

import uuid
from datetime import date, time
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.meeting import Meeting
    from app.models.user import User


class Prescription(TimestampMixin, Base):
    """
    Prescription issued by a doctor after a consultation meeting.
    Linked 1:1 with Meeting.
    """

    __tablename__ = "prescriptions"

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
        unique=True,
        nullable=False,
        index=True,
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
        index=True,
    )

    # ── Instructions / Notes ─────────────────────────────────────────────
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    # ── Relationships ────────────────────────────────────────────────────
    medicines: Mapped[List["PrescriptionMedicine"]] = relationship(
        "PrescriptionMedicine",
        back_populates="prescription",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    meeting: Mapped["Meeting"] = relationship(
        "Meeting",
        foreign_keys=[meeting_id],
    )
    doctor: Mapped["User"] = relationship(
        "User",
        foreign_keys=[doctor_id],
    )
    patient: Mapped["User"] = relationship(
        "User",
        foreign_keys=[patient_id],
    )

    def __repr__(self) -> str:
        return f"<Prescription id={self.id} meeting_id={self.meeting_id} doctor_id={self.doctor_id}>"


class PrescriptionMedicine(TimestampMixin, Base):
    """
    Individual medicine item with timed intake schedules (Morning, Afternoon, Evening, Night).
    """

    __tablename__ = "prescription_medicines"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Key ──────────────────────────────────────────────────────
    prescription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prescriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Medicine Info ────────────────────────────────────────────────────
    medicine_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # ── Time Slots ───────────────────────────────────────────────────────
    # Morning
    morning: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    morning_time: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True, default=None,
    )
    morning_before_meal: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )

    # Afternoon
    afternoon: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    afternoon_time: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True, default=None,
    )
    afternoon_before_meal: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )

    # Evening
    evening: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    evening_time: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True, default=None,
    )
    evening_before_meal: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )

    # Night
    night: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    night_time: Mapped[Optional[time]] = mapped_column(
        Time, nullable=True, default=None,
    )
    night_before_meal: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )

    # ── Date Range ───────────────────────────────────────────────────────
    start_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )
    end_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────
    prescription: Mapped["Prescription"] = relationship(
        "Prescription",
        back_populates="medicines",
    )

    def __repr__(self) -> str:
        return f"<PrescriptionMedicine id={self.id} name={self.medicine_name}>"
