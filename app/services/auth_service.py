"""
Authentication service — business logic for signup, login, refresh, logout,
email OTP verification, and password reset.

Orchestrates repositories and security utilities. All database operations
within a single business action are transactional.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from jose import JWTError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    AccountInactiveError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ValidationError,
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
from app.models.admin_refresh_token import AdminRefreshToken
from app.models.doctor_profile import DoctorProfile
from app.models.email_verification import EmailVerification
from app.models.enums import UserRole, UserStatus
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.admin_token_repository import AdminTokenRepository
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
from app.services.email_service import EmailService

logger = get_logger(__name__)
settings = get_settings()


class AuthService:
    """Handles patient/doctor authentication workflows."""

    # ── OTP Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _hash_otp(otp: str) -> str:
        """SHA-256 hash an OTP for secure storage."""
        return hashlib.sha256(otp.encode("utf-8")).hexdigest()

    # ── Send OTP ─────────────────────────────────────────────────────────

    @staticmethod
    async def send_otp(
        session: AsyncSession, email: str, purpose: str
    ) -> dict:
        """
        Generate and send an OTP to the given email.

        For signup: checks that email is NOT already registered.
        For reset_password: checks that email IS registered.

        Invalidates any previous unused OTPs for the same email+purpose.
        """
        normalized_email = email.lower().strip()

        # Purpose-specific validation
        if purpose == "signup":
            if await UserRepository.email_exists(session, normalized_email):
                raise ConflictError(
                    detail="An account with this email already exists"
                )
        elif purpose == "reset_password":
            user = await UserRepository.get_by_email(session, normalized_email)
            if not user:
                # Return success to prevent email enumeration
                return {"message": "If this email is registered, you will receive a verification code."}

        # Invalidate any previous unused OTPs for this email+purpose
        stmt = (
            select(EmailVerification)
            .where(
                EmailVerification.email == normalized_email,
                EmailVerification.purpose == purpose,
                EmailVerification.is_used == False,
            )
        )
        result = await session.execute(stmt)
        old_otps = result.scalars().all()
        for old_otp in old_otps:
            old_otp.is_used = True

        # Generate new OTP
        otp_code = EmailService.generate_otp()
        otp_hash = AuthService._hash_otp(otp_code)

        # Create verification record
        verification = EmailVerification(
            email=normalized_email,
            otp_hash=otp_hash,
            purpose=purpose,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
        )
        session.add(verification)

        try:
            await session.flush()
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        # Send email (after commit so we don't send on DB failure)
        try:
            await EmailService.send_otp_email(normalized_email, otp_code, purpose)
        except Exception as e:
            logger.error(f"send_otp_email_failed: {str(e)}")
            raise ValidationError(
                detail="Failed to send verification email. Please check your email address and try again."
            )

        logger.info(f"otp_sent: email={normalized_email} purpose={purpose}")
        return {"message": "Verification code sent to your email."}

    # ── Verify OTP ───────────────────────────────────────────────────────

    @staticmethod
    async def verify_otp(
        session: AsyncSession, email: str, otp: str, purpose: str
    ) -> dict:
        """
        Verify an OTP for the given email and purpose.

        Raises:
            AuthenticationError: If OTP is invalid, expired, or already used.
        """
        normalized_email = email.lower().strip()
        otp_hash = AuthService._hash_otp(otp)

        # Find matching OTP record
        stmt = (
            select(EmailVerification)
            .where(
                EmailVerification.email == normalized_email,
                EmailVerification.otp_hash == otp_hash,
                EmailVerification.purpose == purpose,
                EmailVerification.is_used == False,
            )
            .order_by(EmailVerification.created_at.desc())
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            raise AuthenticationError(detail="Invalid verification code. Please check and try again.")

        # Check expiration
        if record.expires_at < datetime.now(timezone.utc):
            record.is_used = True
            await session.commit()
            raise AuthenticationError(detail="Verification code has expired. Please request a new one.")

        # Mark as used
        record.is_used = True
        await session.commit()

        logger.info(f"otp_verified: email={normalized_email} purpose={purpose}")
        return {"message": "Email verified successfully.", "verified": True}

    # ── Signup ───────────────────────────────────────────────────────────

    @staticmethod
    async def signup(session: AsyncSession, data: SignupRequest) -> SignupResponse:
        """
        Register a new patient or doctor account.

        Patient → status = active (immediate access)
        Doctor  → status = pending (requires onboarding & admin approval)

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

        # Verify that email was OTP-verified for signup
        stmt = (
            select(EmailVerification)
            .where(
                EmailVerification.email == normalized_email,
                EmailVerification.purpose == "signup",
                EmailVerification.is_used == True,
            )
            .order_by(EmailVerification.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        verification = result.scalar_one_or_none()

        if not verification:
            raise ValidationError(
                detail="Email not verified. Please verify your email with an OTP first."
            )

        # Check that verification was recent (within 30 minutes)
        if verification.created_at < datetime.now(timezone.utc) - timedelta(minutes=30):
            raise ValidationError(
                detail="Email verification has expired. Please verify your email again."
            )

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

            # Create initial doctor profile record
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
            else "Doctor account created successfully. Please log in to complete your profile for PMDC verification."
        )

        return SignupResponse(
            message=status_message,
            user=UserBriefResponse(
                id=user.id,
                email=user.email,
                role=user.role.value,
                status=user.status.value,
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
            AccountInactiveError: Account is not active / suspended.
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

        logger.info(f"login_success: role={user.role.value} status={user.status.value}")

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user=UserBriefResponse(
                id=user.id,
                email=user.email,
                role=user.role.value,
                status=user.status.value,
            ),
        )

    # ── Forgot / Reset Password ──────────────────────────────────────────

    @staticmethod
    async def send_reset_otp(session: AsyncSession, email: str) -> dict:
        """
        Send a password-reset OTP. Delegates to send_otp with purpose='reset_password'.

        Always returns a success message to prevent email enumeration.
        """
        return await AuthService.send_otp(session, email, purpose="reset_password")

    @staticmethod
    async def reset_password(
        session: AsyncSession, email: str, otp: str, new_password: str
    ) -> dict:
        """
        Reset a user's password after OTP verification.

        Verifies the OTP, then updates the user's password hash.

        Raises:
            AuthenticationError: If OTP is invalid.
            ValidationError: If user not found (shouldn't happen after OTP verify).
        """
        normalized_email = email.lower().strip()
        otp_hash = AuthService._hash_otp(otp)

        # Find and verify the OTP
        stmt = (
            select(EmailVerification)
            .where(
                EmailVerification.email == normalized_email,
                EmailVerification.otp_hash == otp_hash,
                EmailVerification.purpose == "reset_password",
                EmailVerification.is_used == False,
            )
            .order_by(EmailVerification.created_at.desc())
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            raise AuthenticationError(detail="Invalid or expired verification code.")

        if record.expires_at < datetime.now(timezone.utc):
            record.is_used = True
            await session.commit()
            raise AuthenticationError(detail="Verification code has expired. Please request a new one.")

        # Mark OTP as used
        record.is_used = True

        # Update user password
        user = await UserRepository.get_by_email(session, normalized_email)
        if not user:
            raise ValidationError(detail="Account not found.")

        user.password_hash = hash_password(new_password)

        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        # Revoke all refresh tokens for security
        await TokenRepository.revoke_all_for_user(session, user.id)
        await session.commit()

        logger.info(f"password_reset_success: email={normalized_email}")
        return {"message": "Password has been reset successfully. You can now log in with your new password."}

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

        role = payload.get("role")

        # ── SaaS Admin Refresh Flow ──────────────────────────────────────────
        if role == UserRole.SAAS_ADMIN.value:
            stored_admin_token = await AdminTokenRepository.get_by_token_hash(
                session, hash_token(data.refresh_token)
            )
            if not stored_admin_token:
                raise AuthenticationError(detail="Invalid or expired refresh token")

            if stored_admin_token.is_revoked:
                await AdminTokenRepository.revoke_all_for_admin(session, stored_admin_token.admin_id)
                await session.commit()
                logger.warning("admin_refresh_token_reuse_detected: possible token theft")
                raise AuthenticationError(detail="Invalid or expired refresh token")

            if stored_admin_token.expires_at < datetime.now(timezone.utc):
                raise AuthenticationError(detail="Invalid or expired refresh token")

            await AdminTokenRepository.revoke(session, stored_admin_token.id)

            admin_id = payload["sub"]
            new_access_token = create_access_token(sub=admin_id, role=role)
            new_refresh_token = create_refresh_token(sub=admin_id, role=role)

            new_decoded = decode_token(new_refresh_token)
            new_token_record = AdminRefreshToken(
                admin_id=stored_admin_token.admin_id,
                token_hash=hash_token(new_refresh_token),
                expires_at=datetime.fromtimestamp(new_decoded["exp"], tz=timezone.utc),
            )

            try:
                await AdminTokenRepository.create(session, new_token_record)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

            logger.info("admin_token_refresh_success")
            return TokenResponse(
                access_token=new_access_token,
                refresh_token=new_refresh_token,
            )

        # ── Regular User Refresh Flow ────────────────────────────────────────
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
            return

        if payload.get("type") != "refresh":
            return

        role = payload.get("role")
        if role == UserRole.SAAS_ADMIN.value:
            stored_admin_token = await AdminTokenRepository.get_by_token_hash(
                session, hash_token(data.refresh_token)
            )
            if stored_admin_token and not stored_admin_token.is_revoked:
                await AdminTokenRepository.revoke(session, stored_admin_token.id)
                await session.commit()
                logger.info("admin_logout_success")
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
        Verify the user's account status allows authentication.

        - Inactive / Suspended users are denied.
        - Patients must be ACTIVE.
        - Doctors with PENDING/REJECTED status can log in to access onboarding/review info.
        """
        if not user.is_active or user.status == UserStatus.SUSPENDED:
            raise AccountInactiveError(detail="Account is suspended or inactive")

        if user.role == UserRole.PATIENT and user.status != UserStatus.ACTIVE:
            raise AccountInactiveError(detail="Account is not active")
