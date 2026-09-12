"""
Tests for SaaS Admin patient management (listing, searching, and deleting patients).
"""

import uuid
import pytest
from httpx import AsyncClient

from tests.conftest import (
    create_and_login_admin,
    create_and_login_doctor,
    create_and_login_patient,
)

PATIENTS_ADMIN_URL = "/api/v1/admin/patients"
PATIENT_PROFILE_URL = "/api/v1/patient/profile"


@pytest.mark.asyncio
async def test_admin_list_and_search_patients(client: AsyncClient):
    """SaaS Admin should be able to list registered patients and search by name/email."""
    admin_auth = await create_and_login_admin(client, email="admin_patient_test@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    # Create two patients and update profile for one
    pat1_auth = await create_and_login_patient(client, email="ali.khan@example.com")
    pat1_token = pat1_auth["access_token"]
    await client.put(
        PATIENT_PROFILE_URL,
        json={
            "full_name": "Ali Khan",
            "age": 28,
            "gender": "male",
            "blood_group": "B+",
            "address": "Lahore, Pakistan",
        },
        headers={"Authorization": f"Bearer {pat1_token}"},
    )

    pat2_auth = await create_and_login_patient(client, email="sara.ahmed@example.com")
    pat2_token = pat2_auth["access_token"]
    await client.put(
        PATIENT_PROFILE_URL,
        json={
            "full_name": "Sara Ahmed",
            "age": 24,
            "gender": "female",
            "blood_group": "O+",
            "address": "Karachi, Pakistan",
        },
        headers={"Authorization": f"Bearer {pat2_token}"},
    )

    # List all patients
    resp = await client.get(PATIENTS_ADMIN_URL, headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    emails = [item["email"] for item in data["items"]]
    assert "ali.khan@example.com" in emails
    assert "sara.ahmed@example.com" in emails

    # Find Ali Khan in the results
    ali_item = next(item for item in data["items"] if item["email"] == "ali.khan@example.com")
    assert ali_item["full_name"] == "Ali Khan"
    assert ali_item["blood_group"] == "B+"
    assert ali_item["gender"] == "male"
    assert "consultations_count" in ali_item
    assert "documents_count" in ali_item

    # Search by name "Ali"
    search_resp = await client.get(f"{PATIENTS_ADMIN_URL}?search=Ali", headers=admin_headers)
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert any(item["email"] == "ali.khan@example.com" for item in search_data["items"])
    assert not any(item["email"] == "sara.ahmed@example.com" for item in search_data["items"])


@pytest.mark.asyncio
async def test_admin_delete_patient_success(client: AsyncClient):
    """SaaS Admin can permanently delete a patient."""
    admin_auth = await create_and_login_admin(client, email="admin_del_test@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    # Create patient
    patient_auth = await create_and_login_patient(client, email="delete_me@example.com")
    patient_user_id = patient_auth["user"]["id"]

    # Delete patient as admin
    del_resp = await client.delete(f"{PATIENTS_ADMIN_URL}/{patient_user_id}", headers=admin_headers)
    assert del_resp.status_code == 200
    assert "deleted successfully" in del_resp.json()["message"]

    # Verify patient is gone from list
    list_resp = await client.get(PATIENTS_ADMIN_URL, headers=admin_headers)
    assert list_resp.status_code == 200
    emails = [item["email"] for item in list_resp.json()["items"]]
    assert "delete_me@example.com" not in emails

    # Attempting to delete again returns 404
    del_again = await client.delete(f"{PATIENTS_ADMIN_URL}/{patient_user_id}", headers=admin_headers)
    assert del_again.status_code == 404


@pytest.mark.asyncio
async def test_admin_delete_patient_not_found(client: AsyncClient):
    """Attempting to delete a non-existent patient returns 404."""
    admin_auth = await create_and_login_admin(client, email="admin_404_test@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    random_id = uuid.uuid4()
    del_resp = await client.delete(f"{PATIENTS_ADMIN_URL}/{random_id}", headers=admin_headers)
    assert del_resp.status_code == 404


@pytest.mark.asyncio
async def test_unauthorized_access_to_patient_admin(client: AsyncClient):
    """Patients and Doctors cannot access patient admin endpoints."""
    patient_auth = await create_and_login_patient(client, email="regular_patient@example.com")
    patient_headers = {"Authorization": f"Bearer {patient_auth['access_token']}"}

    doc_auth = await create_and_login_doctor(client, email="regular_doctor@example.com")
    doc_headers = {"Authorization": f"Bearer {doc_auth['access_token']}"}

    # Patient tries GET /admin/patients -> 403
    p_resp = await client.get(PATIENTS_ADMIN_URL, headers=patient_headers)
    assert p_resp.status_code == 403

    # Doctor tries GET /admin/patients -> 403
    d_resp = await client.get(PATIENTS_ADMIN_URL, headers=doc_headers)
    assert d_resp.status_code == 403

    # Unauthenticated tries GET /admin/patients -> 401
    anon_resp = await client.get(PATIENTS_ADMIN_URL)
    assert anon_resp.status_code == 401
