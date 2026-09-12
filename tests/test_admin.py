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
from app.models.saas_admin import SaaSAdmin
from app.repositories.saas_admin_repository import SaaSAdminRepository
from tests.conftest import create_test_user, test_session_maker

ADMIN_LOGIN_URL = "/api/v1/admin/auth/login"


# ── Helper ───────────────────────────────────────────────────────────────────

async def create_admin_in_db(
    email: str = "admin@example.com",
    password: str = "AdminPassword123!",
):
    """Directly insert a SaaS Admin into the saas_admins test database table."""
    async with test_session_maker() as session:
        admin = SaaSAdmin(
            email=email.lower().strip(),
            password_hash=hash_password(password),
            full_name="SaaS Administrator",
            is_active=True,
            single_admin_lock=True,
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


# ── Overwrite & Single-Admin Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_overwrite_behavior(client: AsyncClient):
    """
    Overwriting an existing admin updates credentials and invalidates the old ones.
    """
    # 1. Create first admin
    async with test_session_maker() as session:
        admin1, overwritten1 = await SaaSAdminRepository.save_or_overwrite(
            session=session,
            email="original_admin@example.com",
            password_hash=hash_password("OldPassword123!"),
        )
        assert overwritten1 is False
        admin1_id = admin1.id

    # 2. Login with first admin credentials works
    login1 = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "original_admin@example.com", "password": "OldPassword123!"},
    )
    assert login1.status_code == 200

    # 3. Overwrite with new admin credentials
    async with test_session_maker() as session:
        admin2, overwritten2 = await SaaSAdminRepository.save_or_overwrite(
            session=session,
            email="new_admin@example.com",
            password_hash=hash_password("NewPassword123!"),
        )
        assert overwritten2 is True
        assert admin2.id == admin1_id  # UUID preserved for audit history
        assert admin2.email == "new_admin@example.com"

    # 4. Old credentials FAIL
    old_login = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "original_admin@example.com", "password": "OldPassword123!"},
    )
    assert old_login.status_code == 401

    # 5. New credentials SUCCEED
    new_login = await client.post(
        ADMIN_LOGIN_URL,
        json={"email": "new_admin@example.com", "password": "NewPassword123!"},
    )
    assert new_login.status_code == 200
    assert new_login.json()["user"]["email"] == "new_admin@example.com"


@pytest.mark.asyncio
async def test_single_admin_constraint():
    """Database constraint strictly prevents more than 1 admin from being created."""
    from sqlalchemy.exc import IntegrityError
    import uuid

    async with test_session_maker() as session:
        admin1 = SaaSAdmin(
            id=uuid.uuid4(),
            email="admin1@example.com",
            password_hash="hash1",
            single_admin_lock=True,
        )
        session.add(admin1)
        await session.commit()

        # Attempting to insert a 2nd admin with single_admin_lock=True must raise IntegrityError
        admin2 = SaaSAdmin(
            id=uuid.uuid4(),
            email="admin2@example.com",
            password_hash="hash2",
            single_admin_lock=True,
        )
        session.add(admin2)
        with pytest.raises(IntegrityError):
            await session.commit()

