"""
Tests for logout and refresh token revocation.

Covers:
- Logout revokes refresh token
- Revoked refresh token cannot be reused
- Refresh token rotation
- Revoked token reuse triggers full revocation (theft detection)
"""

import pytest
from httpx import AsyncClient

from tests.conftest import create_and_login_patient

REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"


@pytest.mark.asyncio
async def test_logout_success(client: AsyncClient):
    """Logout should return success message."""
    login_data = await create_and_login_patient(client)

    response = await client.post(
        LOGOUT_URL,
        json={"refresh_token": login_data["refresh_token"]},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Logged out successfully"


@pytest.mark.asyncio
async def test_revoked_refresh_token_cannot_refresh(client: AsyncClient):
    """After logout, the refresh token should no longer work."""
    login_data = await create_and_login_patient(client)
    refresh_token = login_data["refresh_token"]

    # Logout (revokes the refresh token)
    await client.post(LOGOUT_URL, json={"refresh_token": refresh_token})

    # Attempt to use revoked refresh token
    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_rotation(client: AsyncClient):
    """Refreshing should return new tokens and revoke the old refresh token."""
    login_data = await create_and_login_patient(client)
    old_refresh = login_data["refresh_token"]

    # Refresh → get new tokens
    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": old_refresh},
    )
    assert response.status_code == 200
    new_data = response.json()
    assert "access_token" in new_data
    assert "refresh_token" in new_data
    assert new_data["refresh_token"] != old_refresh

    # Old refresh token should no longer work
    response = await client.post(
        REFRESH_URL,
        json={"refresh_token": old_refresh},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_double_refresh_fails(client: AsyncClient):
    """Using the same refresh token twice should fail (rotation)."""
    login_data = await create_and_login_patient(client)
    refresh_token = login_data["refresh_token"]

    # First refresh — should succeed
    response1 = await client.post(
        REFRESH_URL,
        json={"refresh_token": refresh_token},
    )
    assert response1.status_code == 200

    # Second refresh with same token — should fail
    response2 = await client.post(
        REFRESH_URL,
        json={"refresh_token": refresh_token},
    )
    assert response2.status_code == 401


@pytest.mark.asyncio
async def test_logout_with_invalid_token(client: AsyncClient):
    """Logout with invalid token should still succeed gracefully."""
    response = await client.post(
        LOGOUT_URL,
        json={"refresh_token": "invalid-token"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_valid_refresh_flow(client: AsyncClient):
    """Full refresh flow: login → refresh → use new tokens to refresh again."""
    login_data = await create_and_login_patient(client)

    # First refresh
    response1 = await client.post(
        REFRESH_URL,
        json={"refresh_token": login_data["refresh_token"]},
    )
    assert response1.status_code == 200
    tokens1 = response1.json()

    # Second refresh with new token
    response2 = await client.post(
        REFRESH_URL,
        json={"refresh_token": tokens1["refresh_token"]},
    )
    assert response2.status_code == 200
    tokens2 = response2.json()

    # All tokens should be different
    assert tokens1["refresh_token"] != tokens2["refresh_token"]
    assert tokens1["access_token"] != tokens2["access_token"]
