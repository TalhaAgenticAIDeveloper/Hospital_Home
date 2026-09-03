"""
Doctor profile model — professional information and admin review data.

Separating this from the User model keeps authentication clean and allows
the doctor domain to evolve independently.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.doctor_document import DoctorDocument
    from app.models.user import User


class DoctorProfile(TimestampMixin, Base):
    """
    Doctor-specific professional data and admin review tracking.

    Linked 1:1 with User. Created at signup, populated by doctor,
    reviewed by admin.
    """

    __tablename__ = "doctor_profiles"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Key (one-to-one with User) ───────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # ── Professional Information ─────────────────────────────────────────
    full_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None,
    )
    phone_number: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, default=None,
    )
    specialization: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None,
    )
    license_number: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, default=None,
    )
    years_of_experience: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None,
    )
    qualification: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, default=None,
    )
    bio: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )

    # ── Application Tracking ─────────────────────────────────────────────
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )

    # ── Admin Review ─────────────────────────────────────────────────────
    admin_feedback: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    # ── Relationships ────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="doctor_profile",
        foreign_keys=[user_id],
    )
    documents: Mapped[List["DoctorDocument"]] = relationship(
        "DoctorDocument",
        back_populates="doctor_profile",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<DoctorProfile id={self.id} user_id={self.user_id}>"
