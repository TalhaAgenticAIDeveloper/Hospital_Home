"""
Tests for Doctor Availability, Booking, Telemedicine Meetings, and Bilingual Transcripts.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.models.enums import UserRole, UserStatus
from app.models.user import User
from tests.conftest import (
    create_and_login_patient,
    create_test_user,
    login_test_user,
    test_session_maker,
)


async def create_active_doctor(client: AsyncClient, email: str = "active_doc@example.com") -> dict:
    """Helper to create and activate a doctor account with full profile."""
    await create_test_user(client, email=email, password="DoctorPassword123!", role="doctor")
    
    # Update status to ACTIVE and populate profile in DB
    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE users SET status = 'active' WHERE email = :email"),
            {"email": email},
        )
        await session.execute(
            text(
                "UPDATE doctor_profiles SET full_name = 'Dr. Sarah Ahmed', specialization = 'Cardiologist', "
                "qualification = 'MBBS, FCPS', years_of_experience = 8, bio = 'Experienced heart specialist' "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": email},
        )
        await session.commit()

    return await login_test_user(client, email=email, password="DoctorPassword123!")


@pytest.mark.asyncio
async def test_doctor_create_availability_batch(client: AsyncClient):
    """Test doctor generating availability slots for upcoming day."""
    doc_auth = await create_active_doctor(client, "doc_avail@example.com")
    headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()

    payload = {
        "slot_date": tomorrow.isoformat(),
        "start_time": "10:00",
        "end_time": "12:00",
        "slot_duration_minutes": 30,
    }

    resp = await client.post("/api/v1/meetings/availability/batch", json=payload, headers=headers)
    assert resp.status_code == 201
    slots = resp.json()
    assert len(slots) == 4  # 10:00-10:30, 10:30-11:00, 11:00-11:30, 11:30-12:00
    assert all(s["is_booked"] is False for s in slots)


@pytest.mark.asyncio
async def test_patient_browse_doctors_and_slots(client: AsyncClient):
    """Test patient browsing active doctors directory and viewing available slots."""
    doc_auth = await create_active_doctor(client, "doc_browse@example.com")
    doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    # Add slot
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    await client.post(
        "/api/v1/meetings/availability/batch",
        json={
            "slot_date": tomorrow.isoformat(),
            "start_time": "14:00",
            "end_time": "15:00",
            "slot_duration_minutes": 30,
        },
        headers=doc_headers,
    )

    # Login patient
    patient_auth = await create_and_login_patient(client, "patient_browse@example.com")
    p_headers = {"Authorization": f"Bearer {patient_auth['access_token']}"}

    # Browse doctors
    doctors_resp = await client.get("/api/v1/meetings/doctors", headers=p_headers)
    assert doctors_resp.status_code == 200
    doctors = doctors_resp.json()
    assert len(doctors) >= 1
    target_doc = next(d for d in doctors if d["email"] == "doc_browse@example.com")
    assert target_doc["full_name"] == "Dr. Sarah Ahmed"
    assert target_doc["specialization"] == "Cardiologist"
    assert target_doc["available_slots_count"] == 2

    # View open slots
    slots_resp = await client.get(f"/api/v1/meetings/doctors/{target_doc['doctor_id']}/slots", headers=p_headers)
    assert slots_resp.status_code == 200
    slots = slots_resp.json()
    assert len(slots) == 2


@pytest.mark.asyncio
async def test_patient_booking_and_double_booking_prevention(client: AsyncClient):
    """Test patient booking a slot and verifying double booking is prevented."""
    doc_auth = await create_active_doctor(client, "doc_book@example.com")
    doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    batch_resp = await client.post(
        "/api/v1/meetings/availability/batch",
        json={
            "slot_date": tomorrow.isoformat(),
            "start_time": "09:00",
            "end_time": "09:30",
            "slot_duration_minutes": 30,
        },
        headers=doc_headers,
    )
    slot_id = batch_resp.json()[0]["id"]
    doctor_id = batch_resp.json()[0]["doctor_id"]

    # Patient 1 books
    patient1_auth = await create_and_login_patient(client, "patient1@example.com")
    p1_headers = {"Authorization": f"Bearer {patient1_auth['access_token']}"}

    book_resp = await client.post(
        "/api/v1/meetings/book",
        json={
            "doctor_id": doctor_id,
            "availability_id": slot_id,
            "patient_notes": "Severe migraine and fever",
        },
        headers=p1_headers,
    )
    assert book_resp.status_code == 201
    meeting = book_resp.json()
    assert meeting["status"] == "scheduled"
    assert meeting["patient_notes"] == "Severe migraine and fever"
    assert meeting["doctor_name"] == "Dr. Sarah Ahmed"

    # Patient 2 attempts to book the same slot without reason (should get 422)
    patient2_auth = await create_and_login_patient(client, "patient2@example.com")
    p2_headers = {"Authorization": f"Bearer {patient2_auth['access_token']}"}

    invalid_reason_resp = await client.post(
        "/api/v1/meetings/book",
        json={
            "doctor_id": doctor_id,
            "availability_id": slot_id,
        },
        headers=p2_headers,
    )
    assert invalid_reason_resp.status_code == 422

    # Patient 2 attempts to book the same slot with reason (should get 409 conflict)
    conflict_resp = await client.post(
        "/api/v1/meetings/book",
        json={
            "doctor_id": doctor_id,
            "availability_id": slot_id,
            "patient_notes": "Follow up consultation",
        },
        headers=p2_headers,
    )
    assert conflict_resp.status_code == 409
    assert "already been booked" in conflict_resp.json()["detail"]


@pytest.mark.asyncio
async def test_meeting_completion_and_notes(client: AsyncClient):
    """Test ending consultation meeting, doctor clinical notes persistence, and completed status."""
    doc_auth = await create_active_doctor(client, "doc_completion@example.com")
    doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=2)).date()
    batch_resp = await client.post(
        "/api/v1/meetings/availability/batch",
        json={
            "slot_date": tomorrow.isoformat(),
            "start_time": "16:00",
            "end_time": "16:30",
            "slot_duration_minutes": 30,
        },
        headers=doc_headers,
    )
    slot_id = batch_resp.json()[0]["id"]
    doctor_id = batch_resp.json()[0]["doctor_id"]

    patient_auth = await create_and_login_patient(client, "patient_completion@example.com")
    p_headers = {"Authorization": f"Bearer {patient_auth['access_token']}"}

    book_resp = await client.post(
        "/api/v1/meetings/book",
        json={
            "doctor_id": doctor_id,
            "availability_id": slot_id,
            "patient_notes": "Throat pain and fever",
        },
        headers=p_headers,
    )
    assert book_resp.status_code == 201
    meeting_id = book_resp.json()["id"]

    # Unauthorized user tries to end meeting
    other_patient = await create_and_login_patient(client, "other_completion@example.com")
    other_headers = {"Authorization": f"Bearer {other_patient['access_token']}"}
    unauth_resp = await client.post(
        f"/api/v1/meetings/{meeting_id}/end",
        json={"doctor_notes": "Hacker notes"},
        headers=other_headers,
    )
    assert unauth_resp.status_code == 403

    # Doctor ends meeting with clinical notes
    end_resp = await client.post(
        f"/api/v1/meetings/{meeting_id}/end",
        json={"doctor_notes": "Prescribed Paracetamol 500mg and advised 3 days rest."},
        headers=doc_headers,
    )
    assert end_resp.status_code == 200
    saved = end_resp.json()
    assert saved["status"] == "completed"
    assert saved["doctor_notes"] == "Prescribed Paracetamol 500mg and advised 3 days rest."

    # Verify meeting status via details endpoint
    details_resp = await client.get(f"/api/v1/meetings/{meeting_id}", headers=doc_headers)
    assert details_resp.status_code == 200
    assert details_resp.json()["status"] == "completed"
    assert details_resp.json()["doctor_notes"] == "Prescribed Paracetamol 500mg and advised 3 days rest."
