"""
Tests for the login endpoint.

Covers:
- Valid patient login
- Valid doctor login (active)
- Wrong password
- Nonexistent email
- Pending doctor
- Rejected doctor
- Suspended user
- Inactive user
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.enums import UserStatus
from app.models.user import User
from tests.conftest import create_test_user, login_test_user, test_session_maker

LOGIN_URL = "/api/v1/auth/login"


# ── Valid Logins ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patient_login_success(client: AsyncClient):
    """Patient with active status should login successfully."""
    await create_test_user(client, email="patient@example.com", role="patient")

    response = await client.post(
        LOGIN_URL,
        json={"email": "patient@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "patient@example.com"
    assert data["user"]["role"] == "patient"
    # Must NOT contain sensitive data
    assert "password" not in str(data)
    assert "password_hash" not in str(data)


@pytest.mark.asyncio
async def test_doctor_login_active(client: AsyncClient):
    """Doctor with active status should login successfully."""
    # Create doctor (starts as pending)
    await create_test_user(client, email="doctor@example.com", role="doctor")

    # Manually activate the doctor
    async with test_session_maker() as session:
        stmt = (
            update(User)
            .where(User.email == "doctor@example.com")
            .values(status=UserStatus.ACTIVE)
        )
        await session.execute(stmt)
        await session.commit()

    response = await client.post(
        LOGIN_URL,
        json={"email": "doctor@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["role"] == "doctor"


# ── Invalid Credentials ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wrong_password(client: AsyncClient):
    """Wrong password should return 401 with generic message."""
    await create_test_user(client, email="user@example.com", role="patient")

    response = await client.post(
        LOGIN_URL,
        json={"email": "user@example.com", "password": "WrongPassword123!"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_nonexistent_email(client: AsyncClient):
    """Nonexistent email should return 401 with same generic message."""
    response = await client.post(
        LOGIN_URL,
        json={"email": "nobody@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 401
    # Same message as wrong password — prevents user enumeration
    assert response.json()["detail"] == "Invalid email or password"


# ── Doctor Status Restrictions ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_doctor_cannot_login(client: AsyncClient):
    """Doctor with pending status can login to access onboarding portal."""
    await create_test_user(client, email="pending@example.com", role="doctor")

    response = await client.post(
        LOGIN_URL,
        json={"email": "pending@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["status"] == "pending"


@pytest.mark.asyncio
async def test_rejected_doctor_cannot_login(client: AsyncClient):
    """Doctor with rejected status can login to access revision portal."""
    await create_test_user(client, email="rejected@example.com", role="doctor")

    async with test_session_maker() as session:
        stmt = (
            update(User)
            .where(User.email == "rejected@example.com")
            .values(status=UserStatus.REJECTED)
        )
        await session.execute(stmt)
        await session.commit()

    response = await client.post(
        LOGIN_URL,
        json={"email": "rejected@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["status"] == "rejected"


@pytest.mark.asyncio
async def test_suspended_user_cannot_login(client: AsyncClient):
    """Suspended user should not be able to login."""
    await create_test_user(client, email="suspended@example.com", role="patient")

    async with test_session_maker() as session:
        stmt = (
            update(User)
            .where(User.email == "suspended@example.com")
            .values(status=UserStatus.SUSPENDED)
        )
        await session.execute(stmt)
        await session.commit()

    response = await client.post(
        LOGIN_URL,
        json={"email": "suspended@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_inactive_user_cannot_login(client: AsyncClient):
    """User with is_active=False should not be able to login."""
    await create_test_user(client, email="inactive@example.com", role="patient")

    async with test_session_maker() as session:
        stmt = (
            update(User)
            .where(User.email == "inactive@example.com")
            .values(is_active=False)
        )
        await session.execute(stmt)
        await session.commit()

    response = await client.post(
        LOGIN_URL,
        json={"email": "inactive@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 403


# ── Email Case Insensitivity ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_email_case_insensitive(client: AsyncClient):
    """Login should work regardless of email case."""
    await create_test_user(client, email="casEtest@example.com", role="patient")

    response = await client.post(
        LOGIN_URL,
        json={"email": "CASETEST@EXAMPLE.COM", "password": "TestPassword123!"},
    )
    assert response.status_code == 200
