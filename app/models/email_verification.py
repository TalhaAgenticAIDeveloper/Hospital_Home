"""
Email Verification model — stores hashed OTPs for signup and password-reset flows.

OTPs are stored as SHA-256 hashes (never plaintext) and expire after a
configurable number of minutes.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailVerification(Base):
    """
    OTP verification record.

    Each record represents a single OTP sent to an email address for a
    specific purpose (signup verification or password reset).
    """

    __tablename__ = "email_verifications"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Verification Fields ──────────────────────────────────────────────
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )
    otp_hash: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="signup | reset_password",
    )

    # ── Status ───────────────────────────────────────────────────────────
    is_used: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )

    # ── Timestamps ───────────────────────────────────────────────────────
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Table Constraints ────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_email_verifications_email_purpose", "email", "purpose"),
    )

    def __repr__(self) -> str:
        return f"<EmailVerification id={self.id} email={self.email} purpose={self.purpose}>"
