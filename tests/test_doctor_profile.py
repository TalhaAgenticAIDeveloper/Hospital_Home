"""
Tests for doctor profile management, document uploads, and application submissions.
"""

import io
import pytest
from httpx import AsyncClient

from tests.conftest import create_and_login_doctor, create_and_login_patient

PROFILE_URL = "/api/v1/doctors/profile"
DOCUMENTS_URL = "/api/v1/doctors/documents"
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
    assert data["documents"] == []


@pytest.mark.asyncio
async def test_update_doctor_profile(client: AsyncClient):
    """Doctor should be able to update professional details."""
    auth_data = await create_and_login_doctor(client, email="doc2@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    profile_payload = {
        "full_name": "Dr. John Doe",
        "phone_number": "+1234567890",
        "specialization": "Cardiology",
        "license_number": "MED-12345",
        "years_of_experience": 10,
        "qualification": "MBBS, MD - Cardiology",
        "bio": "Experienced cardiologist with 10+ years in clinical practice.",
    }

    resp = await client.put(PROFILE_URL, json=profile_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["full_name"] == "Dr. John Doe"
    assert data["specialization"] == "Cardiology"
    assert data["license_number"] == "MED-12345"
    assert data["years_of_experience"] == 10


@pytest.mark.asyncio
async def test_upload_and_list_doctor_documents(client: AsyncClient):
    """Doctor should be able to upload documents and list them."""
    auth_data = await create_and_login_doctor(client, email="doc3@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    fake_file_content = b"%PDF-1.4 test document content"
    files = {
        "file": ("license.pdf", io.BytesIO(fake_file_content), "application/pdf"),
    }
    data = {"document_type": "medical_license"}

    resp = await client.post(DOCUMENTS_URL, files=files, data=data, headers=headers)
    assert resp.status_code == 201
    doc_data = resp.json()
    assert doc_data["original_filename"] == "license.pdf"
    assert doc_data["document_type"] == "medical_license"
    doc_id = doc_data["id"]

    # List documents
    list_resp = await client.get(DOCUMENTS_URL, headers=headers)
    assert list_resp.status_code == 200
    docs = list_resp.json()
    assert len(docs) == 1
    assert docs[0]["id"] == doc_id

    # Delete document
    del_resp = await client.delete(f"{DOCUMENTS_URL}/{doc_id}", headers=headers)
    assert del_resp.status_code == 200

    # Verify list is empty
    list_resp2 = await client.get(DOCUMENTS_URL, headers=headers)
    assert len(list_resp2.json()) == 0


@pytest.mark.asyncio
async def test_upload_invalid_file_type_rejected(client: AsyncClient):
    """Disallowed file MIME types should be rejected with 422."""
    auth_data = await create_and_login_doctor(client, email="doc4@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    files = {
        "file": ("script.sh", io.BytesIO(b"echo 'malicious'"), "text/x-shellscript"),
    }
    data = {"document_type": "medical_license"}

    resp = await client.post(DOCUMENTS_URL, files=files, data=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_application_requires_profile_and_documents(client: AsyncClient):
    """Application submission should fail if profile or documents are missing."""
    auth_data = await create_and_login_doctor(client, email="doc5@example.com")
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Submit with empty profile
    resp1 = await client.post(SUBMIT_URL, headers=headers)
    assert resp1.status_code == 422

    # Fill profile
    profile_payload = {
        "full_name": "Dr. Alice Smith",
        "phone_number": "+9876543210",
        "specialization": "Dermatology",
        "license_number": "DERM-9988",
        "years_of_experience": 5,
        "qualification": "MBBS, MD",
        "bio": "Dermatologist specialist",
    }
    await client.put(PROFILE_URL, json=profile_payload, headers=headers)

    # Submit without documents
    resp2 = await client.post(SUBMIT_URL, headers=headers)
    assert resp2.status_code == 422

    # Upload document
    files = {
        "file": ("license.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfakeimage"), "image/png"),
    }
    await client.post(DOCUMENTS_URL, files=files, data={"document_type": "medical_license"}, headers=headers)

    # Submit with profile + document
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
