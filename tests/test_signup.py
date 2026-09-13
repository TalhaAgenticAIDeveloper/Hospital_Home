"""
Tests for the signup endpoint.

Covers:
- Valid patient signup
- Valid doctor signup
- Duplicate email
- Invalid email
- Weak password variants
- Attempted saas_admin signup
- Invalid role
- Missing fields
"""

import pytest
from httpx import AsyncClient

from tests.conftest import create_test_user, test_session_maker
from datetime import datetime, timezone, timedelta
from app.models.email_verification import EmailVerification
from app.core.security import hash_token

SIGNUP_URL = "/api/v1/auth/signup"


async def pre_verify(email: str):
    async with test_session_maker() as session:
        v = EmailVerification(
            email=email.lower().strip(),
            otp_hash=hash_token("123456"),
            purpose="signup",
            is_used=True,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        session.add(v)
        await session.commit()


# ── Valid Signups ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patient_signup_success(client: AsyncClient):
    """Patient signup should succeed with status=active."""
    await pre_verify("patient@example.com")
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "patient@example.com",
            "password": "StrongPassword123!",
            "role": "patient",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Patient account created successfully"
    assert data["user"]["email"] == "patient@example.com"
    assert data["user"]["role"] == "patient"
    assert "id" in data["user"]
    # Must NOT contain sensitive data
    assert "password" not in data
    assert "password_hash" not in data


@pytest.mark.asyncio
async def test_doctor_signup_success(client: AsyncClient):
    """Doctor signup should succeed with status=pending."""
    await pre_verify("doctor@example.com")
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "doctor@example.com",
            "password": "StrongPassword123!",
            "role": "doctor",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "doctor account created" in data["message"].lower()
    assert data["user"]["email"] == "doctor@example.com"
    assert data["user"]["role"] == "doctor"


# ── Duplicate Email ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_email_rejected(client: AsyncClient):
    """Signup with an already-registered email should return 409."""
    await create_test_user(client, email="dup@example.com", role="patient")

    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "dup@example.com",
            "password": "StrongPassword123!",
            "role": "patient",
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_email_case_insensitive(client: AsyncClient):
    """Email uniqueness should be case-insensitive."""
    await create_test_user(client, email="user@example.com", role="patient")

    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "USER@example.com",
            "password": "StrongPassword123!",
            "role": "patient",
        },
    )
    assert response.status_code == 409


# ── Invalid Email ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_email_rejected(client: AsyncClient):
    """Signup with an invalid email format should return 422."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "not-an-email",
            "password": "StrongPassword123!",
            "role": "patient",
        },
    )
    assert response.status_code == 422


# ── Weak Passwords ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_password_too_short(client: AsyncClient):
    """Password below minimum length should be rejected."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "short@example.com",
            "password": "Sh1!",
            "role": "patient",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_password_no_uppercase(client: AsyncClient):
    """Password without uppercase should be rejected."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "noup@example.com",
            "password": "lowercase123!",
            "role": "patient",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_password_no_digit(client: AsyncClient):
    """Password without a digit should be rejected."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "nodigit@example.com",
            "password": "NoDigitHere!",
            "role": "patient",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_password_no_special(client: AsyncClient):
    """Password without a special character should be rejected."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "nospecial@example.com",
            "password": "NoSpecial123",
            "role": "patient",
        },
    )
    assert response.status_code == 422


# ── SaaS Admin Signup Blocked ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_saas_admin_signup_rejected(client: AsyncClient):
    """Attempting to signup as saas_admin must be rejected."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "admin@example.com",
            "password": "StrongPassword123!",
            "role": "saas_admin",
        },
    )
    # Literal type restriction returns 422 (validation error)
    assert response.status_code == 422


# ── Invalid Role ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_role_rejected(client: AsyncClient):
    """Signup with an invalid role should return 422."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "invalid@example.com",
            "password": "StrongPassword123!",
            "role": "superuser",
        },
    )
    assert response.status_code == 422


# ── Missing Fields ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_email(client: AsyncClient):
    """Signup without email should return 422."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "password": "StrongPassword123!",
            "role": "patient",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_missing_password(client: AsyncClient):
    """Signup without password should return 422."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "nopwd@example.com",
            "role": "patient",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_missing_role(client: AsyncClient):
    """Signup without role should return 422."""
    response = await client.post(
        SIGNUP_URL,
        json={
            "email": "norole@example.com",
            "password": "StrongPassword123!",
        },
    )
    assert response.status_code == 422
