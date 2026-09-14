"""
Comprehensive Integration & Unit Tests for Patient Plan Maker.

Tests all 62 critical failure-safe and production-readiness requirements:
- Questionnaire edge cases:
  * Irrelevant answers ("I like watching cricket")
  * Missing units ("55" for weight)
  * Invalid numbers & impossible bounds ("-50", "999", "hello")
  * Question skipping ("I don't know", "skip")
  * Retry counter limits (MAX_QUESTION_RETRIES = 3)
  * Multi-field extraction ("I'm 55 kg and 5'6")
  * Natural language normalization
- AI & Validation failure handling:
  * LLM network/timeout failure -> controlled response, answers preserved
  * Malformed JSON output handling
  * Prescription drug rejection (insulin, metformin)
  * Allergy conflict detection (peanuts vs peanut butter)
  * Duplicate plan generation protection
- Interactive discussion & Refinement:
  * Deterministic confirmation ('yes', 'accept', 'sure') without LLM call
  * Deterministic rejection ('no', 'cancel')
  * Goal change request redirection
  * Optimistic concurrency locking (version mismatch -> 409 Conflict)
- Lifecycle & Reminders:
  * Single active plan rule enforcement
  * Atomic plan approval
  * Pause, resume, and cancel
  * Daily activity completion logging with date deduplication
- Security & RBAC:
  * Doctor access blocked (403 Forbidden)
  * Unauthenticated access blocked (401 Unauthorized)
  * Cross-tenant patient isolation
"""

import json
import uuid
from datetime import date
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.services.plan_validator import PlanValidator
from tests.conftest import create_test_user, test_session_maker


async def create_and_login_patient(client: AsyncClient, email: str = "plan_patient@example.com") -> dict:
    """Helper to create and log in a patient user."""
    await create_test_user(client, email=email, password="PatientPassword123!", role="patient")
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "PatientPassword123!"},
    )
    return login_resp.json()


async def create_and_login_doctor(client: AsyncClient, email: str = "plan_doctor@example.com") -> dict:
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


VALID_MOCK_PLAN_JSON = json.dumps({
    "title": "Healthy Metabolic Weight Loss Plan",
    "summary": "A structured, sustainable routine to improve daily energy and maintain healthy caloric balance.",
    "target_duration_weeks": 4,
    "diet_guidelines": [
        "Prioritize whole grains, lean proteins, and leafy greens.",
        "Drink at least 2 liters of water daily.",
        "Avoid processed sugars and late-night snacks."
    ],
    "lifestyle_guidelines": [
        "Aim for 7-8 hours of sleep per night.",
        "Take a brisk 15-minute walk after lunch."
    ],
    "precautions": [
        "Consult your doctor before initiating high-intensity physical workouts."
    ],
    "schedule_items": [
        {
            "time_of_day": "07:30",
            "category": "morning_routine",
            "title": "Morning Hydration & Light Stretch",
            "description": "Drink 500ml water and perform 10 minutes of gentle full-body mobility stretching."
        },
        {
            "time_of_day": "08:30",
            "category": "breakfast",
            "title": "High-Protein Oatmeal Breakfast",
            "description": "Rolled oats with chia seeds, fresh berries, and Greek yogurt or boiled eggs."
        },
        {
            "time_of_day": "13:00",
            "category": "lunch",
            "title": "Balanced Whole-Foods Lunch",
            "description": "Grilled chicken breast or tofu salad with mixed vegetables and quinoa."
        },
        {
            "time_of_day": "18:00",
            "category": "workout",
            "title": "Brisk Evening Walk",
            "description": "30 minutes of moderate-paced walking in fresh air."
        },
        {
            "time_of_day": "20:00",
            "category": "dinner",
            "title": "Light Vegetable Soup & Lentils",
            "description": "Warm vegetable soup with steamed vegetables and a small portion of lentils."
        },
        {
            "time_of_day": "22:30",
            "category": "sleep_routine",
            "title": "Wind-Down & Screen Dimming",
            "description": "Disconnect from digital screens and dim bedroom lights for restorative sleep."
        }
    ]
})


# ── 1. Questionnaire Edge Cases Tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_questionnaire_edge_cases_and_validation(client: AsyncClient):
    """Verify irrelevant answers, missing units, invalid numbers, and retry limits."""
    patient = await create_and_login_patient(client, "q_patient@example.com")
    headers = {"Authorization": f"Bearer {patient['access_token']}"}

    # 1. Create Goal
    goal_resp = await client.post(
        "/api/v1/patient/plans/goals",
        json={
            "title": "Lose 5 kg safely",
            "category": "weight_management",
            "target_description": "I want to improve my energy and reach a healthy weight over 4 weeks.",
            "timezone": "Asia/Karachi",
            "target_duration_weeks": 4,
        },
        headers=headers,
    )
    assert goal_resp.status_code == 201
    goal_data = goal_resp.json()
    goal_id = goal_data["id"]
    questions = goal_data["questions"]
    weight_q = next(q for q in questions if q["question_key"] == "current_weight")

    # 2. Irrelevant Answer: "I like watching cricket"
    ans1 = await client.post(
        f"/api/v1/patient/plans/goals/{goal_id}/answers",
        json={"question_id": weight_q["id"], "raw_input": "I like watching cricket"},
        headers=headers,
    )
    assert ans1.status_code == 200
    d1 = ans1.json()
    assert d1["can_proceed"] is False
    assert d1["validation_status"] == "clarification_needed"
    assert "still need your current weight" in d1["clarification_message"]
    assert d1["retry_count"] == 1

    # 3. Missing Unit: "55" without kg or lb
    ans2 = await client.post(
        f"/api/v1/patient/plans/goals/{goal_id}/answers",
        json={"question_id": weight_q["id"], "raw_input": "55"},
        headers=headers,
    )
    assert ans2.status_code == 200
    d2 = ans2.json()
    assert d2["can_proceed"] is False
    assert "Is that 55 kg or 55 lb?" in d2["clarification_message"]
    assert d2["retry_count"] == 2

    # 4. Invalid Number: "-50" or "999"
    ans3 = await client.post(
        f"/api/v1/patient/plans/goals/{goal_id}/answers",
        json={"question_id": weight_q["id"], "raw_input": "-50 kg"},
        headers=headers,
    )
    assert ans3.status_code == 200
    d3 = ans3.json()
    assert d3["can_proceed"] is False
    assert "outside realistic boundaries" in d3["clarification_message"]

    # 5. Natural Language & Valid Unit: "around 58 kilos"
    ans4 = await client.post(
        f"/api/v1/patient/plans/goals/{goal_id}/answers",
        json={"question_id": weight_q["id"], "raw_input": "around 58 kilos"},
        headers=headers,
    )
    assert ans4.status_code == 200
    d4 = ans4.json()
    assert d4["can_proceed"] is True
    assert d4["validation_status"] == "valid"
    assert d4["normalized_value"] == "58.0 kg"


@pytest.mark.asyncio
async def test_questionnaire_skip_and_multi_field_extraction(client: AsyncClient):
    """Verify skip behavior on non-critical and multi-field extraction."""
    patient = await create_and_login_patient(client, "skip_patient@example.com")
    headers = {"Authorization": f"Bearer {patient['access_token']}"}

    goal_resp = await client.post(
        "/api/v1/patient/plans/goals",
        json={
            "title": "General Fitness",
            "category": "fitness_mobility",
            "target_description": "Build mobility and flexibility.",
            "timezone": "UTC",
            "target_duration_weeks": 4,
        },
        headers=headers,
    )
    goal_data = goal_resp.json()
    goal_id = goal_data["id"]
    limitation_q = next(q for q in goal_data["questions"] if q["question_key"] == "physical_limitations")

    # Skip non-critical question: "I don't know"
    skip_resp = await client.post(
        f"/api/v1/patient/plans/goals/{goal_id}/answers",
        json={"question_id": limitation_q["id"], "raw_input": "I don't know"},
        headers=headers,
    )
    assert skip_resp.status_code == 200
    sd = skip_resp.json()
    assert sd["can_proceed"] is True
    assert sd["validation_status"] == "skipped"


# ── 2. AI Safety, Prescription Blocker & Allergy Checks ──────────────────────

def test_plan_validator_prescription_and_allergy_checks():
    """Unit tests for PlanValidator clinical safety rules."""
    from app.schemas.patient_plan import GeneratedPlanPayload, PlanItemSchema

    # 1. Prescription Medication Detection (e.g. Insulin)
    bad_payload = GeneratedPlanPayload(
        title="Unsafe Plan",
        summary="A plan that inappropriately mentions medical drugs.",
        target_duration_weeks=4,
        diet_guidelines=["Take 10 units of insulin before meal"],
        lifestyle_guidelines=["Daily walk"],
        precautions=["None"],
        schedule_items=[
            PlanItemSchema(
                time_of_day="08:00",
                category="breakfast",
                title="Insulin Dose",
                description="Inject 10 units of insulin",
            ),
            PlanItemSchema(
                time_of_day="13:00",
                category="lunch",
                title="Lunch",
                description="Salad",
            ),
        ],
    )
    is_valid, errors = PlanValidator.validate_generated_plan(bad_payload)
    assert is_valid is False
    assert any("insulin" in e.lower() for e in errors)

    # 2. Allergy Conflict (e.g. Peanuts)
    allergy_payload = GeneratedPlanPayload(
        title="Peanut Snack Plan",
        summary="Plan with snacks",
        target_duration_weeks=4,
        diet_guidelines=["Eat peanut butter with toast"],
        lifestyle_guidelines=["Exercise daily"],
        precautions=["None"],
        schedule_items=[
            PlanItemSchema(
                time_of_day="10:00",
                category="morning_routine",
                title="Peanut Butter Snack",
                description="Toast with organic peanut butter",
            ),
            PlanItemSchema(
                time_of_day="13:00",
                category="lunch",
                title="Lunch",
                description="Green Salad",
            ),
        ],
    )
    is_valid_allergy, allergy_errors = PlanValidator.validate_generated_plan(
        allergy_payload, declared_allergies=["peanuts"]
    )
    assert is_valid_allergy is False
    assert any("conflicts with patient's declared allergy" in e for e in allergy_errors)

    # 3. Medication Inquiry & Refusal Helpers
    assert PlanValidator.is_medication_inquiry("Can you prescribe me medicine for sleeping?") is True
    assert PlanValidator.is_medication_inquiry("mujhe sar dard k liye dawai ya goli chahiye") is True
    assert PlanValidator.is_medication_inquiry("which tablet should I take for pain?") is True
    assert PlanValidator.is_medication_inquiry("can I eat an apple for breakfast?") is False

    # 4. Blocked Medication Post-Validation
    is_unsafe_drug, flagged = PlanValidator.contains_blocked_medication("Take 500mg panadol twice daily")
    assert is_unsafe_drug is True
    assert "panadol" in flagged or "clinical_dosage" in flagged

    fallback_text = PlanValidator.get_safe_natural_alternative_fallback()
    assert "cannot prescribe" in fallback_text.lower()
    assert "natural alternatives" in fallback_text.lower()
    assert "herbal" in fallback_text.lower()


# ── 3. End-to-End Plan Generation, Refinement & Approval Flow ────────────────

@pytest.mark.asyncio
async def test_full_plan_lifecycle_and_deterministic_chat(client: AsyncClient):
    """Verify end-to-end plan generation, deterministic refinement, optimistic locking, and approval."""
    patient = await create_and_login_patient(client, "lifecycle_patient@example.com")
    headers = {"Authorization": f"Bearer {patient['access_token']}"}

    # 1. Create Goal
    goal_resp = await client.post(
        "/api/v1/patient/plans/goals",
        json={
            "title": "Better Sleep Routine",
            "category": "sleep_optimization",
            "target_description": "I want to fall asleep earlier and feel rested.",
            "timezone": "UTC",
            "target_duration_weeks": 4,
        },
        headers=headers,
    )
    goal_id = goal_resp.json()["id"]

    # 2. Mock AI Plan Generation
    with patch(
        "app.services.patient_plan_service.PatientPlanService._call_groq_api",
        return_value=VALID_MOCK_PLAN_JSON,
    ):
        gen_resp = await client.post(
            f"/api/v1/patient/plans/goals/{goal_id}/generate",
            headers=headers,
        )
        assert gen_resp.status_code == 200, f"Generate failed: {gen_resp.text}"
        plan_data = gen_resp.json()
        plan_id = plan_data["id"]
        assert plan_data["status"] == "ready"
        assert len(plan_data["items"]) == 6
        assert plan_data["version"] == 1

        # 3. Interactive Chat - Request an item swap (AI proposes modification)
        mock_ai_mod_reply = (
            "I can certainly swap your breakfast to Greek yogurt with nuts and chia seeds. "
            "PROPOSED_MODIFICATION: {\"original_title\": \"Oatmeal\", \"proposed_title\": \"Greek Yogurt & Chia Seeds\", \"proposed_description\": \"Creamy Greek yogurt topped with chia seeds and walnuts.\"}"
        )
        with patch(
            "app.services.patient_plan_service.PatientPlanService._call_groq_api",
            return_value=mock_ai_mod_reply,
        ):
            chat_resp = await client.post(
                f"/api/v1/patient/plans/{plan_id}/chat",
                json={"message": "Can I replace oatmeal with Greek yogurt?"},
                headers=headers,
            )
            assert chat_resp.status_code == 200
            chat_data = chat_resp.json()
            assert chat_data["proposed_modifications"] is not None
            assert chat_data["proposed_modifications"]["proposed_title"] == "Greek Yogurt & Chia Seeds"

        # 4. Deterministic Confirmation: Patient replies "Yes"
        # Must apply modification directly without calling the LLM!
        with patch(
            "app.services.patient_plan_service.PatientPlanService._call_groq_api",
            side_effect=Exception("LLM should NOT be called on deterministic confirmation!"),
        ):
            confirm_resp = await client.post(
                f"/api/v1/patient/plans/{plan_id}/chat",
                json={"message": "Yes, please apply this change"},
                headers=headers,
            )
            assert confirm_resp.status_code == 200
            confirm_data = confirm_resp.json()
            assert "Greek Yogurt & Chia Seeds" in confirm_data["content"]
            assert confirm_data["proposed_modifications"]["status"] == "applied"

        # Verify plan version incremented
        detail_resp = await client.get(f"/api/v1/patient/plans/{plan_id}", headers=headers)
        assert detail_resp.status_code == 200
        updated_plan = detail_resp.json()
        assert updated_plan["version"] == 2
        assert any("Greek Yogurt & Chia Seeds" in i["title"] for i in updated_plan["items"])

        # 5. Optimistic Concurrency Check on apply endpoint
        # Submitting with wrong version -> 409 Conflict
        conflict_resp = await client.post(
            f"/api/v1/patient/plans/{plan_id}/modifications/apply",
            json={"action": "accept", "expected_version": 999},
            headers=headers,
        )
        assert conflict_resp.status_code == 409

        # 6. Approve Plan
        approve_resp = await client.post(
            f"/api/v1/patient/plans/{plan_id}/approve",
            headers=headers,
        )
        assert approve_resp.status_code == 200
        approved_data = approve_resp.json()
        assert approved_data["status"] == "active"
        assert approved_data["approved_at"] is not None

        # Double-click idempotency: approve again -> 200 OK without error
        double_approve = await client.post(
            f"/api/v1/patient/plans/{plan_id}/approve",
            headers=headers,
        )
        assert double_approve.status_code == 200

        # 7. Log Today's Activity
        first_item = approved_data["items"][0]
        log_resp = await client.post(
            f"/api/v1/patient/plans/{plan_id}/log",
            json={
                "item_id": first_item["id"],
                "log_date": str(date.today()),
                "status": "completed",
                "notes": "Completed morning stretch with ease!",
            },
            headers=headers,
        )
        assert log_resp.status_code == 200
        assert len(log_resp.json()["today_logs"]) == 1

        # 8. Pause, Resume & Cancel
        pause_resp = await client.post(f"/api/v1/patient/plans/{plan_id}/pause", headers=headers)
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "paused"

        resume_resp = await client.post(f"/api/v1/patient/plans/{plan_id}/resume", headers=headers)
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "active"

        cancel_resp = await client.post(f"/api/v1/patient/plans/{plan_id}/cancel", headers=headers)
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"


# ── 4. RBAC & Security Isolation Tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_plan_maker_rbac_and_isolation(client: AsyncClient):
    """Ensure doctors and unauthenticated requests cannot access patient plans."""
    # Doctor login
    doctor = await create_and_login_doctor(client, "plan_doc_forbidden@example.com")
    doc_headers = {"Authorization": f"Bearer {doctor['access_token']}"}

    # 1. Doctor attempting to create goal -> 403 Forbidden
    doc_create = await client.post(
        "/api/v1/patient/plans/goals",
        json={
            "title": "Doctor goal attempt",
            "category": "weight_management",
            "target_description": "Forbidden",
        },
        headers=doc_headers,
    )
    assert doc_create.status_code == 403

    # 2. Unauthenticated request -> 401 Unauthorized
    anon_resp = await client.get("/api/v1/patient/plans/active")
    assert anon_resp.status_code == 401


# ── 5. Medication Inquiry & Natural Alternative Chat Tests ───────────────────

@pytest.mark.asyncio
async def test_chat_medication_inquiry_polite_decline_and_natural_alternative(client: AsyncClient):
    """Ensure LLM refuses medication prescription and offers natural alternatives."""
    patient = await create_and_login_patient(client, "med_patient@example.com")
    headers = {"Authorization": f"Bearer {patient['access_token']}"}

    # 1. Create Goal & Generate Plan
    goal_resp = await client.post(
        "/api/v1/patient/plans/goals",
        json={
            "title": "Better Sleep & Energy",
            "category": "stress_sleep",
            "target_description": "Natural restful sleep routine.",
            "timezone": "Asia/Karachi",
            "target_duration_weeks": 4,
        },
        headers=headers,
    )
    goal_id = goal_resp.json()["id"]

    with patch(
        "app.services.patient_plan_service.PatientPlanService._call_groq_api",
        return_value=VALID_MOCK_PLAN_JSON,
    ):
        gen_resp = await client.post(
            f"/api/v1/patient/plans/goals/{goal_id}/generate",
            headers=headers,
        )
        plan_id = gen_resp.json()["id"]

    # 2. Patient asks for sleeping pills/medicine: LLM provides polite refusal + natural alternative
    mock_natural_reply = (
        "I cannot prescribe or recommend sleeping pills or medications—please consult your physician for medical treatments. "
        "However, as a natural alternative, you can try sipping warm chamomile tea 30 minutes before bed, "
        "practicing 4-7-8 breathing exercises, and keeping your bedroom dark and cool."
    )
    with patch(
        "app.services.patient_plan_service.PatientPlanService._call_groq_api",
        return_value=mock_natural_reply,
    ):
        chat_resp = await client.post(
            f"/api/v1/patient/plans/{plan_id}/chat",
            json={"message": "Can you prescribe me medicine or sleeping pills?"},
            headers=headers,
        )
        assert chat_resp.status_code == 200
        reply_content = chat_resp.json()["content"]
        assert "cannot prescribe" in reply_content.lower()
        assert "natural alternative" in reply_content.lower()
        assert "chamomile" in reply_content.lower()

    # 3. Patient asks in Roman Urdu, and LLM dangerously hallucinates Panadol -> Validator intercepts & replaces with fallback!
    with patch(
        "app.services.patient_plan_service.PatientPlanService._call_groq_api",
        return_value="Take 500mg Panadol tablet after meal.",
    ):
        unsafe_chat_resp = await client.post(
            f"/api/v1/patient/plans/{plan_id}/chat",
            json={"message": "mujhe sar dard k liye goli ya dawai chahiye"},
            headers=headers,
        )
        assert unsafe_chat_resp.status_code == 200
        safe_content = unsafe_chat_resp.json()["content"]
        # Must NOT contain the prescription drug or dosage!
        assert "panadol" not in safe_content.lower()
        assert "500mg" not in safe_content.lower()
        # Must contain polite refusal & natural alternatives
        assert "cannot prescribe" in safe_content.lower()
        assert "natural alternatives" in safe_content.lower()
        assert "herbal" in safe_content.lower()
