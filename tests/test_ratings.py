"""
Tests for Doctor Ratings and Feedback system.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import (
    create_and_login_patient,
    create_test_user,
    login_test_user,
    test_session_maker,
)


async def create_active_doctor(client: AsyncClient, email: str = "rated_doc@example.com") -> dict:
    """Helper to create and activate a doctor account with full profile."""
    await create_test_user(client, email=email, password="DoctorPassword123!", role="doctor")
    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE users SET status = 'active' WHERE email = :email"),
            {"email": email},
        )
        await session.execute(
            text(
                "UPDATE doctor_profiles SET full_name = 'Dr. Rating Specialist', specialization = 'Neurologist', "
                "qualification = 'MBBS, FCPS', years_of_experience = 10 "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": email},
        )
        await session.commit()
    return await login_test_user(client, email=email, password="DoctorPassword123!")


async def create_completed_meeting(client: AsyncClient, doc_email: str, pat_email: str):
    """Helper to book and complete a meeting between doctor and patient."""
    doc_auth = await create_active_doctor(client, doc_email)
    doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    batch_resp = await client.post(
        "/api/v1/meetings/availability/batch",
        json={
            "slot_date": tomorrow.isoformat(),
            "start_time": "14:00",
            "end_time": "14:30",
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
            "patient_notes": "General checkup",
        },
        headers=pat_headers,
    )
    meeting_id = book_resp.json()["id"]

    # End the meeting
    await client.post(
        f"/api/v1/meetings/{meeting_id}/end",
        json={"doctor_notes": "Consultation done."},
        headers=doc_headers,
    )

    return meeting_id, doctor_id, doc_headers, pat_headers


@pytest.mark.asyncio
async def test_submit_rating_success_and_doctor_average(client: AsyncClient):
    """Patient submits rating, average rating updates, and doctor directory returns it."""
    meeting_id, doctor_id, doc_headers, pat_headers = await create_completed_meeting(
        client, "doc_rating1@example.com", "pat_rating1@example.com"
    )

    # Patient submits 5-star rating
    resp = await client.post(
        f"/api/v1/ratings/{meeting_id}",
        json={"rating": 5, "feedback_text": "Excellent doctor! Very attentive."},
        headers=pat_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["rating"] == 5
    assert data["feedback_text"] == "Excellent doctor! Very attentive."
    assert data["meeting_id"] == meeting_id

    # Check rating can be retrieved
    get_resp = await client.get(f"/api/v1/ratings/{meeting_id}", headers=pat_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["rating"] == 5

    # Check directory includes average_rating
    dir_resp = await client.get("/api/v1/meetings/doctors", headers=pat_headers)
    assert dir_resp.status_code == 200
    docs = dir_resp.json()
    rated_doc = next((d for d in docs if d["doctor_id"] == doctor_id), None)
    assert rated_doc is not None
    assert rated_doc["average_rating"] == 5.0
    assert rated_doc["total_ratings"] == 1


@pytest.mark.asyncio
async def test_submit_duplicate_rating_rejected(client: AsyncClient):
    """Cannot rate the same meeting twice."""
    meeting_id, doctor_id, doc_headers, pat_headers = await create_completed_meeting(
        client, "doc_rating2@example.com", "pat_rating2@example.com"
    )

    # First rating succeeds
    resp1 = await client.post(
        f"/api/v1/ratings/{meeting_id}",
        json={"rating": 4, "feedback_text": "Good experience"},
        headers=pat_headers,
    )
    assert resp1.status_code == 201

    # Second rating is rejected with 409 Conflict
    resp2 = await client.post(
        f"/api/v1/ratings/{meeting_id}",
        json={"rating": 3, "feedback_text": "Changing my mind"},
        headers=pat_headers,
    )
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_doctor_cannot_rate_patient(client: AsyncClient):
    """Doctor cannot call submit rating (only patients allowed)."""
    meeting_id, doctor_id, doc_headers, pat_headers = await create_completed_meeting(
        client, "doc_rating3@example.com", "pat_rating3@example.com"
    )

    resp = await client.post(
        f"/api/v1/ratings/{meeting_id}",
        json={"rating": 5},
        headers=doc_headers,
    )
    assert resp.status_code == 403
