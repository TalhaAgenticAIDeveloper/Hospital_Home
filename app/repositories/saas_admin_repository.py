"""
Repository for SaaS Admin database operations.

Centralizes all database queries for the SaaSAdmin model, including
enforcing the single-admin constraint and in-place credential overwrites.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_refresh_token import AdminRefreshToken
from app.models.saas_admin import SaaSAdmin


class SaaSAdminRepository:
    """Data access methods for the SaaSAdmin model."""

    @staticmethod
    async def get_admin(session: AsyncSession) -> Optional[SaaSAdmin]:
        """Fetch the single registered SaaS Admin account if one exists."""
        stmt = select(SaaSAdmin).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(
        session: AsyncSession, admin_id: uuid.UUID
    ) -> Optional[SaaSAdmin]:
        """Fetch SaaS Admin by unique UUID."""
        stmt = select(SaaSAdmin).where(SaaSAdmin.id == admin_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(
        session: AsyncSession, email: str
    ) -> Optional[SaaSAdmin]:
        """Fetch SaaS Admin by email address (case-insensitive)."""
        stmt = select(SaaSAdmin).where(SaaSAdmin.email == email.lower().strip())
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def save_or_overwrite(
        session: AsyncSession,
        email: str,
        password_hash: str,
        full_name: str = "SaaS Administrator",
    ) -> Tuple[SaaSAdmin, bool]:
        """
        Create a new SaaS Admin or overwrite the existing one.

        If an admin already exists:
        - Updates email, password_hash, and full_name in-place.
        - Reactivates account if inactive.
        - Immediately revokes all active refresh tokens/sessions.
        - Preserves the admin's UUID so existing audit logs (e.g. reviewed_by) remain valid.
        - Returns (admin, True) indicating overwrite occurred.

        If no admin exists:
        - Inserts the first SaaS Admin.
        - Returns (admin, False).
        """
        existing = await SaaSAdminRepository.get_admin(session)

        if existing:
            # Overwrite existing admin
            existing.email = email.lower().strip()
            existing.password_hash = password_hash
            existing.full_name = full_name
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)

            # Invalidate all prior sessions for this admin
            revoke_stmt = (
                update(AdminRefreshToken)
                .where(AdminRefreshToken.admin_id == existing.id)
                .values(is_revoked=True, updated_at=datetime.now(timezone.utc))
            )
            await session.execute(revoke_stmt)
            await session.commit()
            return existing, True
        else:
            # Create the single SaaS Admin
            admin = SaaSAdmin(
                id=uuid.uuid4(),
                email=email.lower().strip(),
                password_hash=password_hash,
                full_name=full_name,
                is_active=True,
                single_admin_lock=True,
            )
            session.add(admin)
            await session.commit()
            return admin, False

    @staticmethod
    async def update_last_login(
        session: AsyncSession, admin_id: uuid.UUID
    ) -> None:
        """Update last login timestamp for the SaaS Admin."""
        stmt = (
            update(SaaSAdmin)
            .where(SaaSAdmin.id == admin_id)
            .values(last_login_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()
