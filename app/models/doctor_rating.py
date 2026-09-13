"""
Doctor rating model — patient feedback and 1-5 star ratings for meetings.
"""

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.meeting import Meeting
    from app.models.user import User


class DoctorRating(TimestampMixin, Base):
    """
    Patient rating and feedback for a doctor following a completed meeting.
    Strictly 1 rating per meeting.
    """

    __tablename__ = "doctor_ratings"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_doctor_ratings_rating_range"),
    )

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

    # ── Rating Data ──────────────────────────────────────────────────────
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    feedback_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    # ── Relationships ────────────────────────────────────────────────────
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
        return f"<DoctorRating id={self.id} doctor_id={self.doctor_id} rating={self.rating}>"
