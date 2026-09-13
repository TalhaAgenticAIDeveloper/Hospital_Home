"""
SaaS Admin model — dedicated table for platform administrative accounts.

Strictly separated from the public users table. Enforces a single-admin limit
via database constraint and provides properties for RBAC interface compatibility.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.enums import UserRole, UserStatus


class SaaSAdmin(TimestampMixin, Base):
    """
    Dedicated table for the SaaS Administrator.

    Constraints:
    - Exactly one SaaS Admin can exist in the platform at any given time.
    - Guaranteed by `single_admin_lock` UNIQUE constraint.
    - Script re-runs overwrite the existing admin's email and password hash in-place.
    """

    __tablename__ = "saas_admins"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Credentials ──────────────────────────────────────────────────────
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        default="SaaS Administrator",
        nullable=False,
    )

    # ── Account State ────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # ── Single-Admin Singleton Lock ──────────────────────────────────────
    # Only one row with single_admin_lock=True can ever exist in this table.
    single_admin_lock: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        unique=True,
        nullable=False,
    )

    # ── RBAC / Interface Compatibility Properties ────────────────────────
    @property
    def role(self) -> UserRole:
        """Compatibility property for role checks."""
        return UserRole.SAAS_ADMIN

    @property
    def status(self) -> UserStatus:
        """Compatibility property for status checks."""
        return UserStatus.ACTIVE if self.is_active else UserStatus.SUSPENDED

    def __repr__(self) -> str:
        return f"<SaaSAdmin id={self.id} email={self.email} active={self.is_active}>"
