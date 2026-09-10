"""
Doctor Weekly Schedule model — stores recurring weekly availability templates.
"""

import uuid
from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, SmallInteger, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class DoctorWeeklySchedule(TimestampMixin, Base):
    """
    Recurring weekly availability template configured by doctors.

    Each row represents one day's schedule. A doctor can have
    0-7 rows (one per day of week). Slots are generated from
    this template in bulk for upcoming dates.
    """

    __tablename__ = "doctor_weekly_schedules"

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

    # ── Day of Week (0=Monday, 6=Sunday — ISO standard) ─────────────────
    day_of_week: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )

    # ── Time Window ──────────────────────────────────────────────────────
    start_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )
    end_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )

    # ── Slot Duration ────────────────────────────────────────────────────
    slot_duration_minutes: Mapped[int] = mapped_column(
        Integer,
        default=30,
        nullable=False,
    )

    # ── Active Toggle ────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────
    doctor: Mapped["User"] = relationship(
        "User",
        foreign_keys=[doctor_id],
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_weekly_schedule_doctor_day", "doctor_id", "day_of_week", unique=True),
    )

    def __repr__(self) -> str:
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day_name = days[self.day_of_week] if 0 <= self.day_of_week <= 6 else "?"
        return f"<DoctorWeeklySchedule {day_name} {self.start_time}-{self.end_time} doctor={self.doctor_id}>"
