"""
Test fixtures for the authentication backend.

Uses a real PostgreSQL test database for maximum fidelity.
Set TEST_DATABASE_URL in your environment or .env file.

Each test function gets a fresh database state via transaction rollback.
"""

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app

settings = get_settings()

# ── Test Database Engine ─────────────────────────────────────────────────────

TEST_DATABASE_URL = settings.TEST_DATABASE_URL or settings.DATABASE_URL

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

test_session_maker = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

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

    This runs once per test session.
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

    Uses DELETE instead of DROP/CREATE for speed.
    """
    yield
    async with test_session_maker() as session:
        # Delete in reverse FK order
        await session.execute(
            __import__("sqlalchemy").text("DELETE FROM doctor_profiles")
        )
        await session.execute(
            __import__("sqlalchemy").text("DELETE FROM refresh_tokens")
        )
        await session.execute(
            __import__("sqlalchemy").text("DELETE FROM users")
        )
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
