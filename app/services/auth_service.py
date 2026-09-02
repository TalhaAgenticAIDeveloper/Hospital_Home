"""
Authentication service — business logic for signup, login, refresh, and logout.

Orchestrates repositories and security utilities. All database operations
within a single business action are transactional.
"""

from datetime import datetime, timezone

from jose import JWTError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountInactiveError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
)
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.doctor_profile import DoctorProfile
from app.models.enums import UserRole, UserStatus
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
)
from app.schemas.user import UserBriefResponse

logger = get_logger(__name__)


class AuthService:
    """Handles patient/doctor authentication workflows."""

    # ── Signup ───────────────────────────────────────────────────────────

    @staticmethod
    async def signup(session: AsyncSession, data: SignupRequest) -> SignupResponse:
        """
        Register a new patient or doctor account.

        Patient → status = active (immediate access)
        Doctor  → status = pending (requires admin approval)

        Raises:
            AuthorizationError: If role is saas_admin.
            ConflictError: If email already exists.
        """
        # Block saas_admin signup (defense-in-depth — Literal type blocks it too)
        if data.role == UserRole.SAAS_ADMIN.value:
            raise AuthorizationError(detail="This role cannot be registered publicly")

        # Normalize email
        normalized_email = data.email.lower().strip()

        # App-level uniqueness check (DB constraint is the real safety net)
        if await UserRepository.email_exists(session, normalized_email):
            raise ConflictError(detail="An account with this email already exists")

        # Determine initial status based on role
        initial_status = (
            UserStatus.ACTIVE if data.role == UserRole.PATIENT.value
            else UserStatus.PENDING
        )

        # Create user
        user = User(
            email=normalized_email,
            password_hash=hash_password(data.password),
            role=UserRole(data.role),
            status=initial_status,
            is_active=True,
        )

        try:
            await UserRepository.create(session, user)

            # Create empty doctor profile for future use
            if data.role == UserRole.DOCTOR.value:
                doctor_profile = DoctorProfile(user_id=user.id)
                session.add(doctor_profile)
                await session.flush()

            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise ConflictError(detail="An account with this email already exists")

        logger.info(f"signup_success: role={user.role.value} status={user.status.value}")

        status_message = (
            "Patient account created successfully"
            if data.role == UserRole.PATIENT.value
            else "Doctor account created successfully. Your account is pending approval."
        )

        return SignupResponse(
            message=status_message,
            user=UserBriefResponse(
                id=user.id,
                email=user.email,
                role=user.role.value,
            ),
        )

    # ── Login ────────────────────────────────────────────────────────────

    @staticmethod
    async def login(session: AsyncSession, data: LoginRequest) -> LoginResponse:
        """
        Authenticate a patient or doctor.

        The role is determined from the database — never trusted from the client.

        Raises:
            AuthenticationError: Invalid credentials.
            AccountInactiveError: Account is not active.
        """
        normalized_email = data.email.lower().strip()

        # Find user — use generic error to prevent user enumeration
        user = await UserRepository.get_by_email(session, normalized_email)
        if not user:
            # Still hash to prevent timing attacks
            hash_password("dummy-password-for-timing")
            raise AuthenticationError()

        # Reject admin accounts from public login
        if user.role == UserRole.SAAS_ADMIN:
            # Same generic message — don't reveal that this is an admin account
            hash_password("dummy-password-for-timing")
            raise AuthenticationError()

        # Verify password
        if not verify_password(data.password, user.password_hash):
            logger.info("login_failure: invalid credentials")
            raise AuthenticationError()

        # Check account status
        AuthService._check_account_status(user)

        # Generate tokens
        access_token = create_access_token(
            sub=str(user.id),
            role=user.role.value,
        )
        refresh_token = create_refresh_token(
            sub=str(user.id),
            role=user.role.value,
        )

        # Store refresh token hash in DB
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

        logger.info(f"login_success: role={user.role.value}")

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

    # ── Refresh ──────────────────────────────────────────────────────────

    @staticmethod
    async def refresh(session: AsyncSession, data: RefreshRequest) -> TokenResponse:
        """
        Rotate refresh token and issue new token pair.

        The old refresh token is revoked (rotation). If the old token was
        already revoked, all user tokens are revoked (theft detection).

        Raises:
            AuthenticationError: Invalid or revoked refresh token.
        """
        try:
            payload = decode_token(data.refresh_token)
        except JWTError:
            raise AuthenticationError(detail="Invalid or expired refresh token")

        # Validate token type
        if payload.get("type") != "refresh":
            raise AuthenticationError(detail="Invalid token type")

        # Look up stored token hash
        stored_token = await TokenRepository.get_by_token_hash(
            session, hash_token(data.refresh_token)
        )
        if not stored_token:
            raise AuthenticationError(detail="Invalid or expired refresh token")

        # If already revoked → possible token theft, revoke all user tokens
        if stored_token.is_revoked:
            await TokenRepository.revoke_all_for_user(session, stored_token.user_id)
            await session.commit()
            logger.warning("refresh_token_reuse_detected: possible token theft")
            raise AuthenticationError(detail="Invalid or expired refresh token")

        # Check expiration
        if stored_token.expires_at < datetime.now(timezone.utc):
            raise AuthenticationError(detail="Invalid or expired refresh token")

        # Revoke old token (rotation)
        await TokenRepository.revoke(session, stored_token.id)

        # Issue new token pair
        user_id = payload["sub"]
        role = payload["role"]

        new_access_token = create_access_token(sub=user_id, role=role)
        new_refresh_token = create_refresh_token(sub=user_id, role=role)

        # Store new refresh token hash
        new_decoded = decode_token(new_refresh_token)
        new_token_record = RefreshToken(
            user_id=stored_token.user_id,
            token_hash=hash_token(new_refresh_token),
            expires_at=datetime.fromtimestamp(new_decoded["exp"], tz=timezone.utc),
        )

        try:
            await TokenRepository.create(session, new_token_record)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        logger.info("token_refresh_success")

        return TokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
        )

    # ── Logout ───────────────────────────────────────────────────────────

    @staticmethod
    async def logout(session: AsyncSession, data: LogoutRequest) -> None:
        """
        Revoke a refresh token (logout).

        The access token may remain valid until its short expiration,
        but the refresh token/session is immediately revoked.
        """
        try:
            payload = decode_token(data.refresh_token)
        except JWTError:
            # Silently succeed — client may be logging out an already-expired token
            return

        if payload.get("type") != "refresh":
            return

        stored_token = await TokenRepository.get_by_token_hash(
            session, hash_token(data.refresh_token)
        )
        if stored_token and not stored_token.is_revoked:
            await TokenRepository.revoke(session, stored_token.id)
            await session.commit()
            logger.info("logout_success")

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _check_account_status(user: User) -> None:
        """
        Verify the user's account status allows login.

        Raises:
            AccountInactiveError: If account cannot login.
        """
        if not user.is_active:
            raise AccountInactiveError(detail="Account is not active")

        if user.status == UserStatus.PENDING:
            raise AccountInactiveError(detail="Account is pending approval")
        elif user.status == UserStatus.REJECTED:
            raise AccountInactiveError(detail="Account is not active")
        elif user.status == UserStatus.SUSPENDED:
            raise AccountInactiveError(detail="Account is not active")
        elif user.status != UserStatus.ACTIVE:
            raise AccountInactiveError(detail="Account is not active")
