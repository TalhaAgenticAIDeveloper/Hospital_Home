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
    """Doctor should be able to update verification details (Full Name, Father Name, PMDC number, Fee)."""
    auth_data = await create_and_login_doctor(client, email="doc2@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    profile_payload = {
        "full_name": "Dr. John Doe",
        "father_name": "Richard Doe",
        "pmdc_registration_number": "PMDC-12345-S",
        "consultation_fee": 1500.0,
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
    assert data["consultation_fee"] == 1500.0
    assert data["specialization"] == "Cardiology"
    assert data["years_of_experience"] == 10


@pytest.mark.asyncio
async def test_submit_application_requires_mandatory_fields(client: AsyncClient):
    """Application submission requires Full Name, Father Name, PMDC Number, and Consultation Fee."""
    auth_data = await create_and_login_doctor(client, email="doc5@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Submit with empty profile -> 422 validation error
    resp1 = await client.post(SUBMIT_URL, headers=headers)
    assert resp1.status_code == 422

    # 2. Fill profile with missing consultation fee -> submit still fails with 422
    profile_partial = {
        "full_name": "Dr. Alice Smith",
        "father_name": "Robert Smith",
        "pmdc_registration_number": "PMDC-9988-G",
    }
    update_resp = await client.put(PROFILE_URL, json=profile_partial, headers=headers)
    assert update_resp.status_code == 200

    resp2 = await client.post(SUBMIT_URL, headers=headers)
    assert resp2.status_code == 422
    assert "Consultation Fee" in resp2.json()["detail"]

    # 3. Add consultation fee -> submit succeeds!
    await client.put(PROFILE_URL, json={"consultation_fee": 2000.0}, headers=headers)
    resp3 = await client.post(SUBMIT_URL, headers=headers)
    assert resp3.status_code == 200
    status_data = resp3.json()
    assert status_data["status"] == "pending"
    assert status_data["submitted_at"] is not None


@pytest.mark.asyncio
async def test_approved_doctor_credentials_locked(client: AsyncClient):
    """Approved active doctors cannot edit Full Name, Father Name, or PMDC Number, but consultation fee remains editable."""
    from tests.conftest import create_and_login_admin

    admin_auth = await create_and_login_admin(client, email="admin_lock@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    doc_auth = await create_and_login_doctor(client, email="doc_lock@example.com")
    doc_token = doc_auth["access_token"]
    doc_user_id = doc_auth["user"]["id"]
    doc_headers = {"Authorization": f"Bearer {doc_token}"}

    # Populate and submit
    await client.put(
        PROFILE_URL,
        json={
            "full_name": "Dr. Original Name",
            "father_name": "Original Father",
            "pmdc_registration_number": "PMDC-LOCKED-01",
            "consultation_fee": 1800.0,
            "bio": "Initial bio",
        },
        headers=doc_headers,
    )
    await client.post(SUBMIT_URL, headers=doc_headers)

    # Admin approves doctor
    approve_resp = await client.post(
        f"/api/v1/admin/doctors/{doc_user_id}/review",
        json={"action": "approve", "feedback": "Approved"},
        headers=admin_headers,
    )
    assert approve_resp.status_code == 200

    # Doctor is now ACTIVE. Doctor tries to alter locked credentials and change fee & bio
    update_attempt = await client.put(
        PROFILE_URL,
        json={
            "full_name": "Dr. Hacked Name",
            "father_name": "Hacked Father",
            "pmdc_registration_number": "PMDC-HACKED-99",
            "consultation_fee": 2500.0,
            "bio": "Updated bio after approval",
        },
        headers=doc_headers,
    )
    assert update_attempt.status_code == 200
    updated_data = update_attempt.json()

    # Verified credentials MUST remain unchanged (locked)
    assert updated_data["full_name"] == "Dr. Original Name"
    assert updated_data["father_name"] == "Original Father"
    assert updated_data["pmdc_registration_number"] == "PMDC-LOCKED-01"

    # Consultation fee and bio MUST be updated
    assert updated_data["consultation_fee"] == 2500.0
    assert updated_data["bio"] == "Updated bio after approval"


@pytest.mark.asyncio
async def test_patient_cannot_access_doctor_routes(client: AsyncClient):
    """Patient role should be forbidden from accessing doctor onboarding routes."""
    patient_data = await create_and_login_patient(client, email="pat1@example.com")
    headers = {"Authorization": f"Bearer {patient_data['access_token']}"}

    resp = await client.get(PROFILE_URL, headers=headers)
    assert resp.status_code == 403
