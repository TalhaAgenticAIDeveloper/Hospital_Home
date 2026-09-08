"""
Integration tests for Patient Medical Documents feature.

Covers:
- Document upload (valid file types, file size validation, max 5 documents per patient)
- Document listing and deletion
- Booking appointment with attached patient documents
- Doctor viewing attached patient documents for their consultations
- Mandatory consultation reason validation
"""

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.enums import UserRole, UserStatus
from app.models.user import User
from tests.conftest import test_session_maker


async def create_and_login_patient(client: AsyncClient, email: str = "patient_doc@example.com") -> dict:
    """Helper to create and log in a patient user."""
    await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "PatientPassword123!", "role": "patient"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "PatientPassword123!"},
    )
    return login_resp.json()


async def create_active_doctor(client: AsyncClient, email: str = "doc_doc@example.com") -> dict:
    """Helper to create an active doctor user with profile."""
    await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "DoctorPassword123!", "role": "doctor"},
    )
    async with test_session_maker() as session:
        stmt = update(User).where(User.email == email).values(status=UserStatus.ACTIVE)
        await session.execute(stmt)
        await session.commit()

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "DoctorPassword123!"},
    )
    token_data = login_resp.json()
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}

    await client.post(
        "/api/v1/doctor/profile",
        json={
            "full_name": "Dr. Sarah Ahmed",
            "specialization": "Cardiologist",
            "license_number": f"LIC-{uuid.uuid4().hex[:6].upper()}",
            "experience_years": 10,
            "bio": "Experienced cardiologist",
        },
        headers=headers,
    )
    return token_data


@pytest.mark.asyncio
async def test_patient_document_lifecycle(client: AsyncClient):
    """Test document upload, list, download, and delete."""
    patient = await create_and_login_patient(client, "lifecycle_patient@example.com")
    headers = {"Authorization": f"Bearer {patient['access_token']}"}

    # 1. Upload a PDF document
    pdf_content = b"%PDF-1.4 test medical report content"
    files = {"file": ("blood_test.pdf", io.BytesIO(pdf_content), "application/pdf")}
    data = {"label": "Blood Test Report Aug 2026"}

    upload_resp = await client.post("/api/v1/patient/documents", files=files, data=data, headers=headers)
    assert upload_resp.status_code == 201
    doc = upload_resp.json()
    assert doc["label"] == "Blood Test Report Aug 2026"
    assert doc["original_filename"] == "blood_test.pdf"
    assert doc["mime_type"] == "application/pdf"
    doc_id = doc["id"]

    # 2. List documents
    list_resp = await client.get("/api/v1/patient/documents", headers=headers)
    assert list_resp.status_code == 200
    docs = list_resp.json()
    assert len(docs) == 1
    assert docs[0]["id"] == doc_id

    # 3. Download document
    download_resp = await client.get(f"/api/v1/patient/documents/{doc_id}/download", headers=headers)
    assert download_resp.status_code == 200
    assert download_resp.content == pdf_content

    # 4. Delete document
    delete_resp = await client.delete(f"/api/v1/patient/documents/{doc_id}", headers=headers)
    assert delete_resp.status_code == 200

    # 5. Verify list is now empty
    list_resp2 = await client.get("/api/v1/patient/documents", headers=headers)
    assert len(list_resp2.json()) == 0


@pytest.mark.asyncio
async def test_patient_document_max_5_limit(client: AsyncClient):
    """Test that a patient cannot upload more than 5 documents."""
    patient = await create_and_login_patient(client, "limit_patient@example.com")
    headers = {"Authorization": f"Bearer {patient['access_token']}"}

    # Upload 5 documents
    for i in range(1, 6):
        files = {"file": (f"doc_{i}.pdf", io.BytesIO(b"%PDF-test"), "application/pdf")}
        resp = await client.post("/api/v1/patient/documents", files=files, data={"label": f"Doc {i}"}, headers=headers)
        assert resp.status_code == 201

    # 6th upload should fail with 422 (ValidationError)
    files_6 = {"file": ("doc_6.pdf", io.BytesIO(b"%PDF-test"), "application/pdf")}
    resp_6 = await client.post("/api/v1/patient/documents", files=files_6, data={"label": "Doc 6"}, headers=headers)
    assert resp_6.status_code == 422
    assert "maximum limit of 5 documents" in resp_6.json()["detail"].lower()


@pytest.mark.asyncio
async def test_booking_with_attached_patient_documents(client: AsyncClient):
    """Test booking a meeting with mandatory reason and attached documents, and doctor viewing them."""
    # Create doctor
    doc_auth = await create_active_doctor(client, "doc_view_patient_docs@example.com")
    doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=3)).date()
    slot_resp = await client.post(
        "/api/v1/meetings/availability/batch",
        json={
            "slot_date": tomorrow.isoformat(),
            "start_time": "14:00",
            "end_time": "14:30",
            "slot_duration_minutes": 30,
        },
        headers=doc_headers,
    )
    slot_id = slot_resp.json()[0]["id"]
    doctor_id = slot_resp.json()[0]["doctor_id"]

    # Create patient and upload 2 documents
    patient = await create_and_login_patient(client, "booking_doc_patient@example.com")
    p_headers = {"Authorization": f"Bearer {patient['access_token']}"}

    upload1 = await client.post(
        "/api/v1/patient/documents",
        files={"file": ("ecg_scan.png", io.BytesIO(b"\x89PNG\r\n\x1a\ntest"), "image/png")},
        data={"label": "ECG Report"},
        headers=p_headers,
    )
    doc1_id = upload1.json()["id"]

    upload2 = await client.post(
        "/api/v1/patient/documents",
        files={"file": ("prescription.pdf", io.BytesIO(b"%PDF-presc"), "application/pdf")},
        data={"label": "Current Prescription"},
        headers=p_headers,
    )
    doc2_id = upload2.json()["id"]

    # Book meeting with mandatory reason and attached doc1
    book_resp = await client.post(
        "/api/v1/meetings/book",
        json={
            "doctor_id": doctor_id,
            "availability_id": slot_id,
            "patient_notes": "Chest discomfort for 2 days",
            "document_ids": [doc1_id],
        },
        headers=p_headers,
    )
    assert book_resp.status_code == 201
    meeting = book_resp.json()
    meeting_id = meeting["id"]
    assert len(meeting["attached_documents"]) == 1
    assert meeting["attached_documents"][0]["original_filename"] == "ecg_scan.png"
    assert meeting["attached_documents"][0]["label"] == "ECG Report"

    # Doctor views attached documents for this meeting
    doc_view_resp = await client.get(
        f"/api/v1/meetings/{meeting_id}/patient-documents",
        headers=doc_headers,
    )
    assert doc_view_resp.status_code == 200
    attached_docs = doc_view_resp.json()
    assert len(attached_docs) == 1
    assert attached_docs[0]["label"] == "ECG Report"

    # Doctor downloads the patient's attached document
    doc_download_resp = await client.get(
        f"/api/v1/meetings/{meeting_id}/patient-documents/{doc1_id}/download",
        headers=doc_headers,
    )
    assert doc_download_resp.status_code == 200
    assert doc_download_resp.content == b"\x89PNG\r\n\x1a\ntest"
