"""
Patient profile model — personal and demographic information.

Separating this from the User model keeps authentication clean and allows
patient profile data to evolve independently.
"""

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class PatientProfile(TimestampMixin, Base):
    """
    Patient-specific personal and demographic information.

    Linked 1:1 with User. Created or populated during onboarding,
    and editable by the patient anytime from their dashboard.
    """

    __tablename__ = "patient_profiles"

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

    # ── Personal & Demographic Information ───────────────────────────────
    full_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None,
    )
    age: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None,
    )
    date_of_birth: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, default=None,
    )
    gender: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, default=None,
    )
    blood_group: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, default=None,
    )
    address: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, default=None,
    )

    # ── Status ───────────────────────────────────────────────────────────
    is_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="patient_profile",
        foreign_keys=[user_id],
    )

    def __repr__(self) -> str:
        return f"<PatientProfile id={self.id} user_id={self.user_id} full_name={self.full_name}>"
