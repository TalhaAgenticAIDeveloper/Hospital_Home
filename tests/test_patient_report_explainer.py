"""
Integration tests for Patient Medical Report Explainer feature.

Tests:
- Patient uploads a medical report, receives structured AI explanation, and creates session.
- Doctors and unauthenticated users are forbidden from accessing report explainer.
- Patient lists sessions, retrieves session detail and conversation history.
- Patient sends follow-up chat messages and receives answers.
- Patient deletes a report session.
"""

import io
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.enums import UserRole, UserStatus
from app.models.user import User
from tests.conftest import test_session_maker, create_test_user


async def create_and_login_patient(client: AsyncClient, email: str = "report_patient@example.com") -> dict:
    """Helper to create and log in a patient user."""
    await create_test_user(client, email=email, password="PatientPassword123!", role="patient")
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "PatientPassword123!"},
    )
    return login_resp.json()


async def create_and_login_doctor(client: AsyncClient, email: str = "report_doctor@example.com") -> dict:
    """Helper to create and log in an active doctor user."""
    await create_test_user(client, email=email, password="DoctorPassword123!", role="doctor")
    async with test_session_maker() as session:
        stmt = update(User).where(User.email == email).values(status=UserStatus.ACTIVE)
        await session.execute(stmt)
        await session.commit()

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "DoctorPassword123!"},
    )
    return login_resp.json()


MOCK_EXPLANATION = """## 📋 Quick Summary
This is a standard Complete Blood Count (CBC) report. Overall, most of your blood markers are healthy.

## 🔬 Test Results Breakdown
### Hemoglobin
- **Status:** ✅ Normal (Your Value: **14.2 g/dL**)
- **Normal Range:** 13.5 - 17.5 g/dL
- **What it Means:** Checks oxygen-carrying protein in your blood.
- **Effects & What to Know:** Your level is within the healthy range.

## 🩺 Questions for Your Doctor
1. When should I repeat my routine blood work?
"""

MOCK_CHAT_REPLY = "Eating a paratha occasionally is generally fine if your lipid profile and blood glucose are normal, but moderation is advised."


@pytest.mark.asyncio
async def test_patient_report_explainer_lifecycle(client: AsyncClient):
    """Verify full end-to-end report explainer flow for a patient."""
    patient = await create_and_login_patient(client, "explainer_patient@example.com")
    patient_headers = {"Authorization": f"Bearer {patient['access_token']}"}

    # 1. Doctor attempting to access -> 403 Forbidden
    doctor = await create_and_login_doctor(client, "forbidden_doctor@example.com")
    doc_headers = {"Authorization": f"Bearer {doctor['access_token']}"}

    doc_check = await client.get("/api/v1/patient/reports/sessions", headers=doc_headers)
    assert doc_check.status_code == 403, "Doctors must not be allowed to access patient report explainer"

    # 2. Upload report as patient (Mocking extract_report_content and generate_explanation)
    with patch(
        "app.services.patient_report_explainer_service.PatientReportExplainerService.extract_report_content",
        return_value=("Hemoglobin: 14.2 g/dL (Normal 13.5-17.5)\nWBC: 6,500 /uL", "pdf_text"),
    ), patch(
        "app.services.patient_report_explainer_service.PatientReportExplainerService.generate_explanation",
        return_value=MOCK_EXPLANATION,
    ):
        dummy_pdf = io.BytesIO(b"%PDF-1.4 dummy pdf content for testing")
        upload_resp = await client.post(
            "/api/v1/patient/reports/upload",
            files={"file": ("cbc_report.pdf", dummy_pdf, "application/pdf")},
            headers=patient_headers,
        )
        assert upload_resp.status_code == 201, f"Upload failed: {upload_resp.text}"
        data = upload_resp.json()
        assert data["success"] is True
        assert data["filename"] == "cbc_report.pdf"
        assert "Quick Summary" in data["explanation"]
        assert len(data["messages"]) == 1
        assert data["messages"][0]["is_report_summary"] is True
        session_id = data["session_id"]

    # 3. List sessions for patient
    list_resp = await client.get("/api/v1/patient/reports/sessions", headers=patient_headers)
    assert list_resp.status_code == 200
    sessions_list = list_resp.json()
    assert len(sessions_list) >= 1
    assert any(s["id"] == session_id for s in sessions_list)

    # 4. Get session detail
    detail_resp = await client.get(f"/api/v1/patient/reports/sessions/{session_id}", headers=patient_headers)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["id"] == session_id
    assert len(detail_data["messages"]) == 1

    # 5. Follow-up chat question
    with patch(
        "app.services.patient_report_explainer_service.PatientReportExplainerService._call_groq_api",
        return_value=MOCK_CHAT_REPLY,
    ):
        chat_resp = await client.post(
            f"/api/v1/patient/reports/sessions/{session_id}/chat",
            json={"message": "Can I eat a paratha with these results?"},
            headers=patient_headers,
        )
        assert chat_resp.status_code == 200
        chat_data = chat_resp.json()
        assert chat_data["success"] is True
        assert chat_data["reply"] == MOCK_CHAT_REPLY
        assert len(chat_data["all_messages"]) == 3  # Initial summary + user question + AI reply

    # 6. Delete session
    del_resp = await client.delete(f"/api/v1/patient/reports/sessions/{session_id}", headers=patient_headers)
    assert del_resp.status_code == 200

    # 7. Verify session is gone
    check_del = await client.get(f"/api/v1/patient/reports/sessions/{session_id}", headers=patient_headers)
    assert check_del.status_code == 404
