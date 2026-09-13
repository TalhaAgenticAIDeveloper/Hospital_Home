"""
Tests for SaaS Admin doctor application review with feedback.
"""

import pytest
from httpx import AsyncClient

from tests.conftest import (
    create_and_login_admin,
    create_and_login_doctor,
    create_and_login_patient,
)

ADMIN_PENDING_URL = "/api/v1/admin/doctors/pending"
ADMIN_REVIEW_BASE = "/api/v1/admin/doctors"
PROFILE_URL = "/api/v1/doctors/profile"
SUBMIT_URL = "/api/v1/doctors/submit-application"
ME_URL = "/api/v1/auth/me"


async def setup_submitted_doctor(client: AsyncClient, email: str = "applicant@example.com") -> tuple[dict, str]:
    """Helper to create, populate, and submit a doctor application."""
    doc_auth = await create_and_login_doctor(client, email=email)
    doc_token = doc_auth["access_token"]
    headers = {"Authorization": f"Bearer {doc_token}"}

    # Update profile with mandatory verification fields
    profile_payload = {
        "full_name": "Dr. Sarah Connor",
        "father_name": "John Connor",
        "pmdc_registration_number": "PMDC-7788-S",
        "phone_number": "+1122334455",
        "specialization": "Neurology",
        "years_of_experience": 8,
        "qualification": "MBBS, MD - Neurology",
        "bio": "Specialist in neurological disorders",
    }
    await client.put(PROFILE_URL, json=profile_payload, headers=headers)

    # Submit application directly (no documents needed)
    await client.post(SUBMIT_URL, headers=headers)

    return doc_auth, doc_token


@pytest.mark.asyncio
async def test_admin_list_and_view_pending_doctors(client: AsyncClient):
    """Admin should see submitted doctor applications with PMDC and father name."""
    admin_auth = await create_and_login_admin(client, email="admin1@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    # Setup submitted doctor
    doc_auth, _ = await setup_submitted_doctor(client, email="doctor1@example.com")
    doctor_user_id = doc_auth["user"]["id"]

    # Admin lists pending doctors
    list_resp = await client.get(ADMIN_PENDING_URL, headers=admin_headers)
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] == 1
    assert data["items"][0]["email"] == "doctor1@example.com"
    assert data["items"][0]["pmdc_registration_number"] == "PMDC-7788-S"

    # Admin views doctor detail
    detail_resp = await client.get(f"{ADMIN_REVIEW_BASE}/{doctor_user_id}", headers=admin_headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["full_name"] == "Dr. Sarah Connor"
    assert detail["father_name"] == "John Connor"
    assert detail["pmdc_registration_number"] == "PMDC-7788-S"
    assert detail["specialization"] == "Neurology"


@pytest.mark.asyncio
async def test_admin_reject_with_feedback_and_doctor_views_feedback(client: AsyncClient):
    """Admin rejects doctor with feedback reason; doctor sees feedback and re-submits."""
    admin_auth = await create_and_login_admin(client, email="admin2@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    doc_auth, doc_token = await setup_submitted_doctor(client, email="doctor2@example.com")
    doc_user_id = doc_auth["user"]["id"]
    doc_headers = {"Authorization": f"Bearer {doc_token}"}

    # 1. Admin attempts to reject without feedback -> 422
    invalid_review = {"action": "reject", "feedback": ""}
    err_resp = await client.post(
        f"{ADMIN_REVIEW_BASE}/{doc_user_id}/review",
        json=invalid_review,
        headers=admin_headers,
    )
    assert err_resp.status_code == 422

    # 2. Admin rejects with valid feedback reason
    feedback_text = "PMDC registration number could not be verified on the portal. Please provide your correct PMDC number."
    review_payload = {
        "action": "reject",
        "feedback": feedback_text,
    }
    review_resp = await client.post(
        f"{ADMIN_REVIEW_BASE}/{doc_user_id}/review",
        json=review_payload,
        headers=admin_headers,
    )
    assert review_resp.status_code == 200
    review_data = review_resp.json()
    assert review_data["new_status"] == "rejected"
    assert review_data["admin_feedback"] == feedback_text

    # 3. Doctor checks profile / auth me and sees rejection feedback
    me_resp = await client.get(ME_URL, headers=doc_headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["status"] == "rejected"
    assert me_data["doctor_profile"]["admin_feedback"] == feedback_text

    # 4. Doctor updates PMDC registration number and re-submits
    update_payload = {
        "full_name": "Dr. Sarah Connor",
        "father_name": "John Connor",
        "pmdc_registration_number": "PMDC-9999-VERIFIED",
    }
    await client.put(PROFILE_URL, json=update_payload, headers=doc_headers)

    resubmit_resp = await client.post(SUBMIT_URL, headers=doc_headers)
    assert resubmit_resp.status_code == 200
    assert resubmit_resp.json()["status"] == "pending"

    # Feedback is now cleared for fresh review
    me_resp2 = await client.get(ME_URL, headers=doc_headers)
    assert me_resp2.json()["status"] == "pending"
    assert me_resp2.json()["doctor_profile"]["admin_feedback"] is None


@pytest.mark.asyncio
async def test_admin_approves_doctor(client: AsyncClient):
    """Admin approves doctor application, transitioning status to ACTIVE."""
    admin_auth = await create_and_login_admin(client, email="admin3@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    doc_auth, doc_token = await setup_submitted_doctor(client, email="doctor3@example.com")
    doc_user_id = doc_auth["user"]["id"]
    doc_headers = {"Authorization": f"Bearer {doc_token}"}

    approve_payload = {
        "action": "approve",
        "feedback": "PMDC registration and credentials verified successfully.",
    }
    review_resp = await client.post(
        f"{ADMIN_REVIEW_BASE}/{doc_user_id}/review",
        json=approve_payload,
        headers=admin_headers,
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["new_status"] == "active"

    # Doctor status is now active
    me_resp = await client.get(ME_URL, headers=doc_headers)
    assert me_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_non_admin_cannot_access_review_routes(client: AsyncClient):
    """Doctors and patients cannot access SaaS Admin review endpoints."""
    doc_auth, doc_token = await setup_submitted_doctor(client, email="doctor4@example.com")
    doc_headers = {"Authorization": f"Bearer {doc_token}"}

    resp = await client.get(ADMIN_PENDING_URL, headers=doc_headers)
    assert resp.status_code == 403

    pat_auth = await create_and_login_patient(client, email="pat2@example.com")
    pat_headers = {"Authorization": f"Bearer {pat_auth['access_token']}"}

    resp2 = await client.get(ADMIN_PENDING_URL, headers=pat_headers)
    assert resp2.status_code == 403
