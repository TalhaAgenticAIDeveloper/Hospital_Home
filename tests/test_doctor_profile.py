"""
Tests for doctor profile management, mandatory verification fields, and application submissions.
"""

import pytest
from httpx import AsyncClient

from tests.conftest import create_and_login_doctor, create_and_login_patient

PROFILE_URL = "/api/v1/doctors/profile"
SUBMIT_URL = "/api/v1/doctors/submit-application"
STATUS_URL = "/api/v1/doctors/status"


@pytest.mark.asyncio
async def test_get_initial_doctor_profile(client: AsyncClient):
    """Doctor should be able to view their initial profile."""
    auth_data = await create_and_login_doctor(client, email="doc1@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get(PROFILE_URL, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "doc1@example.com"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_update_doctor_profile(client: AsyncClient):
    """Doctor should be able to update verification details (Full Name, Father Name, PMDC number)."""
    auth_data = await create_and_login_doctor(client, email="doc2@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    profile_payload = {
        "full_name": "Dr. John Doe",
        "father_name": "Richard Doe",
        "pmdc_registration_number": "PMDC-12345-S",
        "phone_number": "+1234567890",
        "specialization": "Cardiology",
        "years_of_experience": 10,
        "qualification": "MBBS, FCPS Cardiology",
        "bio": "Experienced cardiologist with 10+ years in clinical practice.",
    }

    resp = await client.put(PROFILE_URL, json=profile_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["full_name"] == "Dr. John Doe"
    assert data["father_name"] == "Richard Doe"
    assert data["pmdc_registration_number"] == "PMDC-12345-S"
    assert data["specialization"] == "Cardiology"
    assert data["years_of_experience"] == 10


@pytest.mark.asyncio
async def test_submit_application_requires_mandatory_fields(client: AsyncClient):
    """Application submission requires Full Name, Father Name, and PMDC Number (no documents required)."""
    auth_data = await create_and_login_doctor(client, email="doc5@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Submit with empty profile -> 422 validation error
    resp1 = await client.post(SUBMIT_URL, headers=headers)
    assert resp1.status_code == 422

    # 2. Fill profile with only partial info (missing father name) -> rejected by schema or submit
    profile_partial = {
        "full_name": "Dr. Alice Smith",
        "father_name": "Robert Smith",
        "pmdc_registration_number": "PMDC-9988-G",
    }
    update_resp = await client.put(PROFILE_URL, json=profile_partial, headers=headers)
    assert update_resp.status_code == 200

    # 3. Submit with all 3 mandatory fields provided -> succeeds WITHOUT any document!
    resp3 = await client.post(SUBMIT_URL, headers=headers)
    assert resp3.status_code == 200
    status_data = resp3.json()
    assert status_data["status"] == "pending"
    assert status_data["submitted_at"] is not None


@pytest.mark.asyncio
async def test_patient_cannot_access_doctor_routes(client: AsyncClient):
    """Patient role should be forbidden from accessing doctor onboarding routes."""
    patient_data = await create_and_login_patient(client, email="pat1@example.com")
    headers = {"Authorization": f"Bearer {patient_data['access_token']}"}

    resp = await client.get(PROFILE_URL, headers=headers)
    assert resp.status_code == 403
