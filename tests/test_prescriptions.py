"""
Tests for Doctor Prescriptions and Medicine Schedules.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import (
    create_and_login_patient,
    create_test_user,
    login_test_user,
    test_session_maker,
)


async def create_active_doctor(client: AsyncClient, email: str = "doc_rx@example.com") -> dict:
    """Helper to create and activate a doctor account."""
    await create_test_user(client, email=email, password="DoctorPassword123!", role="doctor")
    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE users SET status = 'active' WHERE email = :email"),
            {"email": email},
        )
        await session.execute(
            text(
                "UPDATE doctor_profiles SET full_name = 'Dr. Prescription Specialist', specialization = 'General Physician', "
                "qualification = 'MBBS' "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": email},
        )
        await session.commit()
    return await login_test_user(client, email=email, password="DoctorPassword123!")


async def create_completed_meeting(client: AsyncClient, doc_email: str, pat_email: str):
    """Helper to book and complete a meeting."""
    doc_auth = await create_active_doctor(client, doc_email)
    doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    batch_resp = await client.post(
        "/api/v1/meetings/availability/batch",
        json={
            "slot_date": tomorrow.isoformat(),
            "start_time": "15:00",
            "end_time": "15:30",
            "slot_duration_minutes": 30,
        },
        headers=doc_headers,
    )
    slot_id = batch_resp.json()[0]["id"]
    doctor_id = batch_resp.json()[0]["doctor_id"]

    patient_auth = await create_and_login_patient(client, pat_email)
    pat_headers = {"Authorization": f"Bearer {patient_auth['access_token']}"}

    book_resp = await client.post(
        "/api/v1/meetings/book",
        json={
            "doctor_id": doctor_id,
            "availability_id": slot_id,
            "patient_notes": "Cough and cold",
        },
        headers=pat_headers,
    )
    meeting_id = book_resp.json()["id"]

    # End the meeting
    await client.post(
        f"/api/v1/meetings/{meeting_id}/end",
        json={"doctor_notes": "Examination complete."},
        headers=doc_headers,
    )

    return meeting_id, doctor_id, doc_headers, pat_headers


@pytest.mark.asyncio
async def test_create_and_fetch_prescription(client: AsyncClient):
    """Doctor creates prescription with 4 time slots, and patient retrieves it."""
    meeting_id, doctor_id, doc_headers, pat_headers = await create_completed_meeting(
        client, "rx_doc1@example.com", "rx_pat1@example.com"
    )

    start = date.today()
    end = start + timedelta(days=5)

    rx_payload = {
        "meeting_id": meeting_id,
        "notes": "Drink plenty of water and rest.",
        "medicines": [
            {
                "medicine_name": "Augmentin 625mg (1 tablet)",
                "morning": True,
                "morning_time": "08:00:00",
                "morning_before_meal": False,
                "afternoon": False,
                "evening": False,
                "night": True,
                "night_time": "20:00:00",
                "night_before_meal": False,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
            {
                "medicine_name": "Panadol 500mg (1 tablet for fever)",
                "morning": True,
                "morning_time": "08:30:00",
                "morning_before_meal": False,
                "afternoon": True,
                "afternoon_time": "14:00:00",
                "afternoon_before_meal": False,
                "evening": True,
                "evening_time": "18:00:00",
                "evening_before_meal": False,
                "night": True,
                "night_time": "22:00:00",
                "night_before_meal": False,
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=3)).isoformat(),
            },
        ],
    }

    # Doctor posts prescription
    create_resp = await client.post("/api/v1/prescriptions", json=rx_payload, headers=doc_headers)
    assert create_resp.status_code == 201
    rx_data = create_resp.json()
    assert rx_data["meeting_id"] == meeting_id
    assert len(rx_data["medicines"]) == 2
    assert rx_data["medicines"][0]["medicine_name"] == "Augmentin 625mg (1 tablet)"
    assert rx_data["medicines"][0]["morning"] is True
    assert rx_data["medicines"][0]["night"] is True

    # Patient fetches by meeting ID
    meeting_rx_resp = await client.get(f"/api/v1/prescriptions/meeting/{meeting_id}", headers=pat_headers)
    assert meeting_rx_resp.status_code == 200
    assert meeting_rx_resp.json()["id"] == rx_data["id"]

    # Patient lists my prescriptions
    my_rx_resp = await client.get("/api/v1/prescriptions/my", headers=pat_headers)
    assert my_rx_resp.status_code == 200
    my_list = my_rx_resp.json()
    assert len(my_list) >= 1
    assert any(p["id"] == rx_data["id"] for p in my_list)


@pytest.mark.asyncio
async def test_patient_cannot_create_prescription(client: AsyncClient):
    """Patient cannot create prescription (forbidden)."""
    meeting_id, doctor_id, doc_headers, pat_headers = await create_completed_meeting(
        client, "rx_doc2@example.com", "rx_pat2@example.com"
    )

    start = date.today()
    payload = {
        "meeting_id": meeting_id,
        "medicines": [
            {
                "medicine_name": "Test Med",
                "morning": True,
                "morning_time": "09:00:00",
                "start_date": start.isoformat(),
                "end_date": start.isoformat(),
            }
        ],
    }

    resp = await client.post("/api/v1/prescriptions", json=payload, headers=pat_headers)
    assert resp.status_code == 403
