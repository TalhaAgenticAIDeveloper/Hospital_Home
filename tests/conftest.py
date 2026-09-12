"""
Test fixtures for the authentication backend.

Uses a real PostgreSQL test database for maximum fidelity.
Set TEST_DATABASE_URL in your environment or .env file.

Each test function gets a fresh database state.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.enums import UserRole, UserStatus
from app.models.user import User

settings = get_settings()

import os
import tempfile
from unittest.mock import patch

from sqlalchemy.pool import NullPool

# ── Test Database Engine ─────────────────────────────────────────────────────

TEST_DATABASE_URL = settings.TEST_DATABASE_URL or settings.DATABASE_URL

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

test_session_maker = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def isolate_test_upload_dir():
    """
    Isolate uploaded patient documents during test runs into a temporary directory.
    Automatically cleaned up after tests, preventing pollution of the real uploads/ dir.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_patient_dir = os.path.join(temp_dir, "patient_documents")
        os.makedirs(temp_patient_dir, exist_ok=True)
        with patch("app.services.patient_document_service.PATIENT_DOCUMENTS_DIR", temp_patient_dir):
            yield temp_patient_dir


@pytest_asyncio.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()



@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """
    Create all tables before tests, drop them after.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables():
    """
    Clean all table data between tests for isolation.

    Uses DELETE in reverse FK order.
    """
    yield
    async with test_session_maker() as session:
        await session.execute(text("DELETE FROM admin_refresh_tokens"))
        await session.execute(text("DELETE FROM saas_admins"))
        await session.execute(text("DELETE FROM meetings"))
        await session.execute(text("DELETE FROM doctor_availabilities"))
        await session.execute(text("DELETE FROM doctor_profiles"))
        await session.execute(text("DELETE FROM email_verifications"))
        await session.execute(text("DELETE FROM refresh_tokens"))
        await session.execute(text("DELETE FROM users"))
        await session.commit()



@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a test database session."""
    async with test_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Provide an async HTTP test client with the test database.

    Overrides the get_db dependency to use the test database.
    """
    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Helper Functions ─────────────────────────────────────────────────────────

async def create_test_user(
    client: AsyncClient,
    email: str = "testuser@example.com",
    password: str = "TestPassword123!",
    role: str = "patient",
) -> dict:
    """Helper to create a test user via the signup endpoint."""
    from app.models.email_verification import EmailVerification
    from app.core.security import hash_token

    # Pre-verify the email so signup succeeds in tests
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

    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": password,
            "role": role,
        },
    )
    return response.json()


async def login_test_user(
    client: AsyncClient,
    email: str = "testuser@example.com",
    password: str = "TestPassword123!",
) -> dict:
    """Helper to login a test user and return the response."""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
        },
    )
    return response.json()


async def create_and_login_patient(
    client: AsyncClient,
    email: str = "patient@example.com",
    password: str = "TestPassword123!",
) -> dict:
    """Helper to create and login a patient, returning tokens."""
    await create_test_user(client, email=email, password=password, role="patient")
    return await login_test_user(client, email=email, password=password)


async def create_and_login_doctor(
    client: AsyncClient,
    email: str = "doctor@example.com",
    password: str = "TestPassword123!",
) -> dict:
    """Helper to create and login a doctor, returning tokens."""
    await create_test_user(client, email=email, password=password, role="doctor")
    return await login_test_user(client, email=email, password=password)


async def create_and_login_admin(
    client: AsyncClient,
    email: str = "admin@example.com",
    password: str = "AdminPassword123!",
) -> dict:
    """Helper to insert and login a SaaS Admin, returning tokens."""
    from app.models.saas_admin import SaaSAdmin

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

    resp = await client.post(
        "/api/v1/admin/auth/login",
        json={"email": email, "password": password},
    )
    return resp.json()

