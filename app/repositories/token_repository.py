"""
Token repository — data access layer for RefreshToken model.

Handles refresh token storage, lookup, and revocation.
"""

import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class TokenRepository:
    """Data access methods for the RefreshToken model."""

    @staticmethod
    async def create(session: AsyncSession, token: RefreshToken) -> RefreshToken:
        """
        Store a new refresh token record.

        The caller is responsible for committing the transaction.
        """
        session.add(token)
        await session.flush()
        return token

    @staticmethod
    async def get_by_token_hash(
        session: AsyncSession, token_hash: str
    ) -> Optional[RefreshToken]:
        """Find a refresh token record by its SHA-256 hash."""
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def revoke(session: AsyncSession, token_id: uuid.UUID) -> None:
        """Revoke a specific refresh token by its ID."""
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(is_revoked=True)
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
        """
        Revoke all refresh tokens for a user.

        Used for "logout everywhere" and account security events.
        """
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.is_revoked == False,  # noqa: E712
            )
            .values(is_revoked=True)
        )
        await session.execute(stmt)
        await session.flush()
