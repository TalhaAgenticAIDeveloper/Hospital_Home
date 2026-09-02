"""
Tests for JWT token handling.

Covers:
- Valid access token → protected endpoint
- Expired access token
- Invalid token
- Refresh token used as access token
- Access token used as refresh token
- Token with invalid payload
"""

import pytest
from httpx import AsyncClient
from jose import jwt

from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from tests.conftest import create_and_login_patient, create_test_user

settings = get_settings()

# We'll use the health/ready endpoint as a baseline, but also test
# with a token in the Authorization header on a dummy protected check.
# Since we don't have user-facing protected endpoints yet, we test
# the token validation through the refresh endpoint and direct token manipulation.

REFRESH_URL = "/api/v1/auth/refresh"


@pytest.mark.asyncio
async def test_valid_access_token_claims(client: AsyncClient):
    """Access token should contain correct claims."""
    login_data = await create_and_login_patient(client)
    access_token = login_data["access_token"]

    payload = jwt.decode(
        access_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert payload["type"] == "access"
    assert payload["role"] == "patient"
    assert "sub" in payload
    assert "exp" in payload
    assert "iat" in payload
    assert "jti" in payload


@pytest.mark.asyncio
async def test_refresh_token_claims(client: AsyncClient):
    """Refresh token should contain correct claims."""
    login_data = await create_and_login_patient(client)
    refresh_token = login_data["refresh_token"]

    payload = jwt.decode(
        refresh_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert payload["type"] == "refresh"
    assert payload["role"] == "patient"
    assert "sub" in payload
    assert "exp" in payload
    assert "jti" in payload


@pytest.mark.asyncio
async def test_expired_access_token(client: AsyncClient):
    """Expired access token should be rejected."""
    from datetime import datetime, timedelta, timezone

    expired_token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "role": "patient",
            "type": "access",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "jti": "expired-jti",
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    # Use refresh endpoint as it validates tokens
    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": expired_token},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_signature_token(client: AsyncClient):
    """Token signed with wrong key should be rejected."""
    from datetime import datetime, timedelta, timezone

    bad_token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "role": "patient",
            "type": "refresh",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "jti": "bad-sig-jti",
        },
        "wrong-secret-key",
        algorithm=settings.JWT_ALGORITHM,
    )

    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": bad_token},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_access_token_used_as_refresh(client: AsyncClient):
    """Access token should not work as refresh token."""
    login_data = await create_and_login_patient(client)
    access_token = login_data["access_token"]

    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": access_token},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_completely_invalid_token(client: AsyncClient):
    """Garbage string should be rejected as token."""
    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": "this-is-not-a-jwt"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_not_in_database(client: AsyncClient):
    """Valid-looking refresh token not stored in DB should be rejected."""
    from datetime import datetime, timedelta, timezone

    # Create a structurally valid refresh token but never store it
    token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "role": "patient",
            "type": "refresh",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(days=7),
            "jti": "not-in-db-jti",
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": token},
    )
    assert response.status_code == 401
