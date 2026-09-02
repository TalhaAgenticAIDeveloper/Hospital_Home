"""
Admin service — business logic for SaaS Admin authentication.

Admin accounts cannot be created via public signup. They are created
via the CLI script (app.scripts.create_admin).
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    verify_password,
    hash_password,
)
from app.models.enums import UserRole, UserStatus
from app.models.refresh_token import RefreshToken
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import AdminLoginRequest, LoginResponse
from app.schemas.user import UserBriefResponse

logger = get_logger(__name__)


class AdminService:
    """Handles SaaS Admin authentication workflows."""

    @staticmethod
    async def admin_login(
        session: AsyncSession, data: AdminLoginRequest
    ) -> LoginResponse:
        """
        Authenticate a SaaS Admin.

        Only users with role=SAAS_ADMIN can use this endpoint.

        Raises:
            AuthenticationError: Invalid credentials or not an admin.
        """
        normalized_email = data.email.lower().strip()

        user = await UserRepository.get_by_email(session, normalized_email)
        if not user:
            # Timing-safe: still hash to prevent timing attacks
            hash_password("dummy-password-for-timing")
            raise AuthenticationError()

        # Must be a SaaS Admin
        if user.role != UserRole.SAAS_ADMIN:
            hash_password("dummy-password-for-timing")
            raise AuthenticationError()

        # Verify password
        if not verify_password(data.password, user.password_hash):
            logger.info("admin_login_failure: invalid credentials")
            raise AuthenticationError()

        # Check status
        if user.status != UserStatus.ACTIVE or not user.is_active:
            raise AuthenticationError(detail="Account is not active")

        # Generate tokens
        access_token = create_access_token(
            sub=str(user.id),
            role=user.role.value,
        )
        refresh_token = create_refresh_token(
            sub=str(user.id),
            role=user.role.value,
        )

        # Store refresh token hash
        decoded = decode_token(refresh_token)
        token_record = RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=datetime.fromtimestamp(decoded["exp"], tz=timezone.utc),
        )

        try:
            await TokenRepository.create(session, token_record)
            await UserRepository.update_last_login(session, user.id)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        logger.info("admin_login_success")

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user=UserBriefResponse(
                id=user.id,
                email=user.email,
                role=user.role.value,
            ),
        )
