"""
Public authentication routes — signup, login, refresh, logout, me,
OTP verification, and password reset.

Used by patients and doctors. SaaS Admin uses a separate endpoint.
"""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_any_status
from app.core.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    ResetPasswordRequest,
    SendOTPRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    VerifyOTPRequest,
    VerifyOTPResponse,
)
from app.schemas.doctor import DoctorProfileResponse
from app.schemas.patient import PatientProfileResponse
from app.schemas.user import MeResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── OTP Endpoints ────────────────────────────────────────────────────────────

@router.post(
    "/send-otp",
    response_model=MessageResponse,
    summary="Send OTP verification code to email",
    description=(
        "Sends a 6-digit OTP to the specified email address. "
        "For signup: checks email is not already registered. "
        "For reset_password: sends OTP if email exists (no error if not, to prevent enumeration)."
    ),
    responses={
        200: {"description": "OTP sent successfully"},
        409: {"description": "Conflict — email already exists (signup only)"},
        422: {"description": "Validation error — SMTP failure or invalid email"},
    },
)
async def send_otp(
    data: SendOTPRequest,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await AuthService.send_otp(session, data.email, data.purpose)
    return MessageResponse(message=result["message"])


@router.post(
    "/verify-otp",
    response_model=VerifyOTPResponse,
    summary="Verify OTP code",
    description=(
        "Verifies a 6-digit OTP for the given email and purpose. "
        "Returns verified=true if the OTP is valid and not expired."
    ),
    responses={
        200: {"description": "OTP verified successfully"},
        401: {"description": "Invalid or expired OTP"},
    },
)
async def verify_otp(
    data: VerifyOTPRequest,
    session: AsyncSession = Depends(get_db),
) -> VerifyOTPResponse:
    result = await AuthService.verify_otp(session, data.email, data.otp, data.purpose)
    return VerifyOTPResponse(message=result["message"], verified=result["verified"])


# ── Password Reset Endpoints ────────────────────────────────────────────────

@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Request password reset OTP",
    description=(
        "Sends a password-reset OTP to the provided email. "
        "Always returns success to prevent email enumeration."
    ),
    responses={
        200: {"description": "Reset OTP sent (if email exists)"},
    },
)
async def forgot_password(
    data: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await AuthService.send_reset_otp(session, data.email)
    return MessageResponse(message=result["message"])


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Reset password with OTP",
    description=(
        "Resets the user's password after verifying the OTP. "
        "All existing sessions are revoked for security."
    ),
    responses={
        200: {"description": "Password reset successfully"},
        401: {"description": "Invalid or expired OTP"},
        422: {"description": "Validation error — weak password"},
    },
)
async def reset_password(
    data: ResetPasswordRequest,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await AuthService.reset_password(
        session, data.email, data.otp, data.new_password
    )
    return MessageResponse(message=result["message"])


# ── Existing Auth Endpoints ──────────────────────────────────────────────────

@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new patient or doctor account",
    description=(
        "Creates a new user account. Requires prior email OTP verification. "
        "Patients are activated immediately. "
        "Doctors are set to 'pending' status and proceed to onboarding. "
        "The 'saas_admin' role cannot be registered through this endpoint."
    ),
    responses={
        201: {"description": "Account created successfully"},
        403: {"description": "Forbidden — attempted saas_admin signup"},
        409: {"description": "Conflict — email already exists"},
        422: {"description": "Validation error — weak password, invalid email, or unverified email"},
    },
)
async def signup(
    data: SignupRequest,
    session: AsyncSession = Depends(get_db),
) -> SignupResponse:
    return await AuthService.signup(session, data)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate a patient or doctor",
    description=(
        "Authenticates a user and returns JWT access and refresh tokens. "
        "The user's role is determined from the database — the client does "
        "not specify the role."
    ),
    responses={
        200: {"description": "Login successful"},
        401: {"description": "Invalid email or password"},
        403: {"description": "Account is suspended or deactivated"},
    },
)
async def login(
    data: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    return await AuthService.login(session, data)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    description=(
        "Exchanges a valid refresh token for a new access/refresh token pair. "
        "The old refresh token is revoked (rotation). Reuse of a revoked "
        "refresh token revokes all tokens for the user (theft detection)."
    ),
    responses={
        200: {"description": "Tokens refreshed successfully"},
        401: {"description": "Invalid or expired refresh token"},
    },
)
async def refresh(
    data: RefreshRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await AuthService.refresh(session, data)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout — revoke refresh token and delete cookies",
    description=(
        "Revokes the provided refresh token and clears session cookies. The access token remains valid "
        "until its short expiration. This supports clean session termination."
    ),
    responses={
        200: {"description": "Logged out successfully"},
    },
)
async def logout(
    data: LogoutRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await AuthService.logout(session, data)
    # Clear cookies
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/")
    response.delete_cookie(key="token", path="/")
    response.delete_cookie(key="session", path="/")
    return MessageResponse(message="Logged out successfully")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Get current user profile and status",
    description="Retrieve the authenticated user's account information, role, status, and doctor profile/feedback if applicable.",
)
async def get_me(
    user: User = Depends(get_current_user_any_status),
    session: AsyncSession = Depends(get_db),
) -> MeResponse:
    doctor_profile_resp = None
    patient_profile_resp = None

    if user.role == UserRole.DOCTOR:
        profile = await DoctorRepository.get_profile_by_user_id(session, user.id)
        if profile:
            doctor_profile_resp = DoctorProfileResponse(
                id=profile.id,
                user_id=user.id,
                email=user.email,
                status=user.status.value,
                full_name=profile.full_name,
                father_name=profile.father_name,
                pmdc_registration_number=profile.pmdc_registration_number,
                phone_number=profile.phone_number,
                specialization=profile.specialization,
                license_number=profile.license_number or profile.pmdc_registration_number,
                years_of_experience=profile.years_of_experience,
                qualification=profile.qualification,
                bio=profile.bio,
                submitted_at=profile.submitted_at,
                admin_feedback=profile.admin_feedback,
                reviewed_at=profile.reviewed_at,
            )
    elif user.role == UserRole.PATIENT:
        p_profile = await PatientRepository.get_profile_by_user_id(session, user.id)
        if p_profile:
            patient_profile_resp = PatientProfileResponse(
                id=p_profile.id,
                user_id=user.id,
                email=user.email,
                full_name=p_profile.full_name,
                age=p_profile.age,
                date_of_birth=p_profile.date_of_birth,
                gender=p_profile.gender,
                blood_group=p_profile.blood_group,
                address=p_profile.address,
                is_completed=p_profile.is_completed,
                created_at=p_profile.created_at,
                updated_at=p_profile.updated_at,
            )

    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role.value,
        status=user.status.value,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        doctor_profile=doctor_profile_resp,
        patient_profile=patient_profile_resp,
    )
