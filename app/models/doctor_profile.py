"""
Doctor profile model — skeleton for future professional information.

This model is intentionally minimal. Professional fields (license number,
specialization, documents, etc.) will be added when the doctor approval
workflow is implemented.

Separating this from the User model keeps authentication clean and allows
the doctor domain to evolve independently.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class DoctorProfile(TimestampMixin, Base):
    """
    Placeholder for doctor-specific professional data.

    Future fields may include:
        - license_number
        - specialization
        - qualification
        - document_urls
        - verified_at
        - admin_notes
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

    # ── Relationships ────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="doctor_profile",
    )

    def __repr__(self) -> str:
        return f"<DoctorProfile id={self.id} user_id={self.user_id}>"
