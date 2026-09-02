"""
Tests for admin authentication.

Covers:
- Admin login (pre-created in DB)
- Duplicate admin
- Patient trying admin endpoint
- Doctor trying admin endpoint
- Wrong admin password
"""

import pytest
from httpx import AsyncClient

from app.core.security import hash_password
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from tests.conftest import create_test_user, test_session_maker

ADMIN_LOGIN_URL = "/api/v1/admin/auth/login"


# ── Helper ───────────────────────────────────────────────────────────────────

async def create_admin_in_db(
    email: str = "admin@example.com",
    password: str = "AdminPassword123!",
):
    """Directly insert a SaaS Admin into the test database."""
    async with test_session_maker() as session:
        admin = User(
            email=email.lower().strip(),
            password_hash=hash_password(password),
            role=UserRole.SAAS_ADMIN,
            status=UserStatus.ACTIVE,
            is_active=True,
        )
        session.add(admin)
        await session.commit()


# ── Admin Login ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_login_success(client: AsyncClient):
    """SaaS Admin should be able to login via admin endpoint."""
    await create_admin_in_db()

    response = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "admin@example.com", "password": "AdminPassword123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["role"] == "saas_admin"


@pytest.mark.asyncio
async def test_admin_wrong_password(client: AsyncClient):
    """Wrong password should return 401."""
    await create_admin_in_db()

    response = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "admin@example.com", "password": "WrongPassword123!"},
    )
    assert response.status_code == 401


# ── Non-Admin Rejected ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patient_at_admin_endpoint(client: AsyncClient):
    """Patient trying admin login should be rejected."""
    await create_test_user(client, email="patient@example.com", role="patient")

    response = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "patient@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_doctor_at_admin_endpoint(client: AsyncClient):
    """Doctor trying admin login should be rejected."""
    await create_test_user(client, email="doctor@example.com", role="doctor")

    response = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "doctor@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 401


# ── Nonexistent Admin ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nonexistent_admin(client: AsyncClient):
    """Nonexistent email at admin endpoint should return 401."""
    response = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "nobody@example.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 401
