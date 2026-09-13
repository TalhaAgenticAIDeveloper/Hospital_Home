"""
Repository for Admin Refresh Token database operations.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_refresh_token import AdminRefreshToken


class AdminTokenRepository:
    """Data access methods for AdminRefreshToken model."""

    @staticmethod
    async def create(
        session: AsyncSession, token: AdminRefreshToken
    ) -> AdminRefreshToken:
        """Persist a new admin refresh token record."""
        session.add(token)
        await session.flush()
        return token

    @staticmethod
    async def get_by_token_hash(
        session: AsyncSession, token_hash: str
    ) -> Optional[AdminRefreshToken]:
        """Look up an admin refresh token by its SHA-256 hash."""
        stmt = select(AdminRefreshToken).where(
            AdminRefreshToken.token_hash == token_hash
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def revoke(session: AsyncSession, token_id: uuid.UUID) -> None:
        """Mark an admin refresh token as revoked."""
        stmt = (
            update(AdminRefreshToken)
            .where(AdminRefreshToken.id == token_id)
            .values(is_revoked=True, updated_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def revoke_all_for_admin(
        session: AsyncSession, admin_id: uuid.UUID
    ) -> None:
        """Revoke all refresh tokens for the admin (e.g. during rotation, theft, or overwrite)."""
        stmt = (
            update(AdminRefreshToken)
            .where(AdminRefreshToken.admin_id == admin_id)
            .values(is_revoked=True, updated_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.flush()
