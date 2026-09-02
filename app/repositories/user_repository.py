"""
User repository — data access layer for User model.

All database queries for users are centralized here.
No business logic — only SQL operations via SQLAlchemy.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """Data access methods for the User model."""

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> Optional[User]:
        """Find a user by email address (case-insensitive)."""
        stmt = select(User).where(User.email == email.lower().strip())
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
        """Find a user by their UUID."""
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(session: AsyncSession, user: User) -> User:
        """
        Persist a new user to the database.

        The caller is responsible for committing the transaction.
        """
        session.add(user)
        await session.flush()
        return user

    @staticmethod
    async def update_last_login(session: AsyncSession, user_id: uuid.UUID) -> None:
        """Update the last_login_at timestamp for a user."""
        user = await UserRepository.get_by_id(session, user_id)
        if user:
            user.last_login_at = datetime.now(timezone.utc)
            await session.flush()

    @staticmethod
    async def email_exists(session: AsyncSession, email: str) -> bool:
        """Check if an email is already registered."""
        stmt = select(User.id).where(User.email == email.lower().strip())
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None
