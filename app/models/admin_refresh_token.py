"""
Admin refresh token model — session tokens for SaaS Administrator.

Stored separately from user refresh tokens for strict architectural decoupling.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.saas_admin import SaaSAdmin


class AdminRefreshToken(TimestampMixin, Base):
    """
    Refresh tokens specifically issued to the SaaS Admin.
    """

    __tablename__ = "admin_refresh_tokens"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Key ──────────────────────────────────────────────────────
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("saas_admins.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # ── Token Data ───────────────────────────────────────────────────────
    token_hash: Mapped[str] = mapped_column(
        String(512),
        index=True,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    device_info: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        default=None,
    )

    # ── Relationships ────────────────────────────────────────────────────
    admin: Mapped["SaaSAdmin"] = relationship("SaaSAdmin", foreign_keys=[admin_id])

    def __repr__(self) -> str:
        return f"<AdminRefreshToken id={self.id} admin_id={self.admin_id} revoked={self.is_revoked}>"
