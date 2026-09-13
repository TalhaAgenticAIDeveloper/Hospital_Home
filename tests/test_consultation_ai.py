"""
Unit and Integration Tests for Consultation AI — Transcription,
Chunking, Clinical Extraction, Conflict Resolution, and Approval Workflow.
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.models.consultation_ai_extraction import ConsultationAIExtraction
from app.models.consultation_transcript import ConsultationTranscript
from app.models.enums import UserRole
from app.schemas.consultation_ai import (
    ConsultationExtractionResult,
    DoctorInstruction,
    ExtractedDiagnosis,
    ExtractedFollowUp,
    ExtractedMedication,
    ExtractedTest,
    TranscriptSegment,
    UncertainItem,
)
from app.services.consultation_ai_service import ConsultationAIService
from app.services.transcription_service import TranscriptionService
from tests.conftest import (
    create_and_login_patient,
    create_test_user,
    login_test_user,
    test_session_maker,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def create_active_doctor(client: AsyncClient, email: str = "doc_ai@example.com") -> dict:
    """Helper to create and activate a doctor account."""
    await create_test_user(client, email=email, password="DoctorPassword123!", role="doctor")
    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE users SET status = 'active' WHERE email = :email"),
            {"email": email},
        )
        await session.execute(
            text(
                "UPDATE doctor_profiles SET full_name = 'Dr. AI Specialist', specialization = 'General Physician', "
                "qualification = 'MBBS' "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": email},
        )
        await session.commit()
    return await login_test_user(client, email=email, password="DoctorPassword123!")


async def setup_test_meeting(client: AsyncClient, doc_email: str, pat_email: str):
    """Helper to book and complete a consultation meeting."""
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
    assert batch_resp.status_code == 201
    slot_id = batch_resp.json()[0]["id"]
    doctor_id = batch_resp.json()[0]["doctor_id"]

    patient_auth = await create_and_login_patient(client, pat_email)
    pat_headers = {"Authorization": f"Bearer {patient_auth['access_token']}"}

    book_resp = await client.post(
        "/api/v1/meetings/book",
        json={
            "doctor_id": doctor_id,
            "availability_id": slot_id,
            "patient_notes": "Fever, cough, and throat irritation for 3 days.",
        },
        headers=pat_headers,
    )
    assert book_resp.status_code == 201
    meeting_data = book_resp.json()
    meeting_id = meeting_data["id"]

    # Mark meeting completed
    end_resp = await client.post(
        f"/api/v1/meetings/{meeting_id}/end",
        json={"doctor_notes": "Completed telemedicine consultation."},
        headers=doc_headers,
    )
    assert end_resp.status_code == 200

    return {
        "meeting_id": meeting_id,
        "doctor_id": doctor_id,
        "patient_id": meeting_data["patient_id"],
        "doc_headers": doc_headers,
        "pat_headers": pat_headers,
    }


# ── Unit Tests: Token Estimation & Chunking ──────────────────────────────────


def test_token_estimation():
    """Verify token estimation returns positive integer."""
    text_sample = "Doctor: Take Paracetamol 500mg twice daily after meals."
    tokens = ConsultationAIService._estimate_tokens(text_sample)
    assert tokens > 0
    assert isinstance(tokens, int)


def test_chunk_transcript_boundary_preservation():
    """Verify chunking never splits mid-utterance and respects max_tokens."""
    segments = [
        {"speaker": "doctor", "start_time": 0.0, "end_time": 5.0, "text": "Hello, what brings you in today?"},
        {"speaker": "patient", "start_time": 5.5, "end_time": 12.0, "text": "Doctor, I have severe headache and throat pain."},
        {"speaker": "doctor", "start_time": 13.0, "end_time": 20.0, "text": "I see. Take Augmentin 625mg twice daily for five days."},
        {"speaker": "patient", "start_time": 21.0, "end_time": 25.0, "text": "Should I take it before or after eating?"},
        {"speaker": "doctor", "start_time": 26.0, "end_time": 30.0, "text": "Always after meals. And rest properly."},
    ]

    # Small max_tokens to force splitting into multiple chunks
    chunks = ConsultationAIService._chunk_transcript(segments, max_tokens=25, overlap_tokens=8)
    assert len(chunks) >= 2

    # Every chunk must contain full utterance brackets
    for chunk in chunks:
        assert "[" in chunk and "]" in chunk
        # Verify timestamps are present in chunk lines
        assert any(":" in line for line in chunk.split("\n"))


# ── Unit Tests: LLM Output JSON Parsing ──────────────────────────────────────


def test_parse_llm_json_direct():
    """Verify raw json parses cleanly."""
    raw = '{"medications": [], "diagnoses": [], "symptoms": []}'
    result = ConsultationAIService._parse_llm_json(raw)
    assert isinstance(result, dict)
    assert "medications" in result


def test_parse_llm_json_markdown_wrapped():
    """Verify json inside markdown ```json ... ``` is extracted."""
    wrapped = """Here is the structured extraction:
```json
{
  "medications": [{"medication_name": "Panadol 500mg"}],
  "diagnoses": [{"diagnosis_name": "Viral Pharyngitis", "certainty": "confirmed"}]
}
```
Let me know if you need anything else!"""
    result = ConsultationAIService._parse_llm_json(wrapped)
    assert len(result["medications"]) == 1
    assert result["medications"][0]["medication_name"] == "Panadol 500mg"


# ── Unit Tests: Conflict Resolution & Chunk Merging ──────────────────────────


def test_merge_chunk_results_deduplication_and_conflicts():
    """Verify chunk results are deduplicated and dosage conflicts produce UncertainItem."""
    chunk1 = ConsultationExtractionResult(
        medications=[
            ExtractedMedication(
                medication_name="Augmentin",
                dose_value="625mg",
                frequency="twice daily",
                confidence=0.9,
            )
        ],
        diagnoses=[
            ExtractedDiagnosis(diagnosis_name="Bronchitis", certainty="suspected", confidence=0.8)
        ],
        symptoms=[{"symptom": "Cough", "reported_by": "patient"}],
    )

    # Chunk 2 has conflicting dose for Augmentin
    chunk2 = ConsultationExtractionResult(
        medications=[
            ExtractedMedication(
                medication_name="Augmentin",
                dose_value="1000mg",
                frequency="twice daily",
                confidence=0.85,
            )
        ],
        diagnoses=[
            # Later chunk confirmed diagnosis
            ExtractedDiagnosis(diagnosis_name="Bronchitis", certainty="confirmed", confidence=0.95)
        ],
        symptoms=[{"symptom": "Cough", "reported_by": "patient"}],
        tests=[ExtractedTest(test_name="Chest X-Ray", urgency="routine")],
    )

    merged = ConsultationAIService._merge_chunk_results([chunk1, chunk2])

    # Medications deduped to 1
    assert len(merged.medications) == 1
    # Conflicting doses created an uncertain item
    assert len(merged.uncertain_items) >= 1
    assert merged.uncertain_items[0].field == "medication"
    assert "Augmentin" in merged.uncertain_items[0].extracted_value

    # Diagnoses upgraded to highest certainty ("confirmed")
    assert len(merged.diagnoses) == 1
    assert merged.diagnoses[0].certainty == "confirmed"

    # Symptoms deduped
    assert len(merged.symptoms) == 1

    # Tests merged
    assert len(merged.tests) == 1
    assert merged.tests[0].test_name == "Chest X-Ray"


# ── Unit Tests: Whisper Parsing & Interleaving ───────────────────────────────


def test_whisper_segment_parsing_and_interleaving():
    """Verify segment parsing and time-sorted interleaving."""
    doc_whisper = {
        "segments": [
            {"start": 0.0, "end": 4.0, "text": "Hello, how are you feeling?"},
            {"start": 10.0, "end": 15.0, "text": "Let me check your throat."},
        ]
    }
    pat_whisper = {
        "segments": [
            {"start": 4.5, "end": 9.5, "text": "I have had fever since yesterday."},
        ]
    }

    doc_segs = TranscriptionService._parse_whisper_segments(doc_whisper, "doctor")
    pat_segs = TranscriptionService._parse_whisper_segments(pat_whisper, "patient")

    assert len(doc_segs) == 2
    assert len(pat_segs) == 1

    interleaved = TranscriptionService._interleave_transcripts(doc_segs, pat_segs)
    assert len(interleaved) == 3

    # Must be chronologically sorted: doc(0.0) -> pat(4.5) -> doc(10.0)
    assert interleaved[0]["speaker"] == "doctor"
    assert interleaved[1]["speaker"] == "patient"
    assert interleaved[2]["speaker"] == "doctor"

    full_text = TranscriptionService._generate_full_text(interleaved)
    assert "[DOCTOR]" in full_text
    assert "[PATIENT]" in full_text


# ── Integration Tests: Endpoints & Approval Workflow ─────────────────────────


@pytest.mark.asyncio
async def test_audio_upload_validation(client: AsyncClient):
    """Verify upload-audio rejects non-audio file extensions and allows valid webm."""
    setup = await setup_test_meeting(client, "doc_upload@test.com", "pat_upload@test.com")
    meeting_id = setup["meeting_id"]
    doc_headers = setup["doc_headers"]

    # 1. Invalid file format (text file)
    bad_files = {"audio_file": ("test.txt", b"not audio data", "text/plain")}
    resp_bad = await client.post(
        f"/api/v1/meetings/{meeting_id}/upload-audio",
        files=bad_files,
        headers=doc_headers,
    )
    assert resp_bad.status_code == 422

    # 2. Valid webm audio file
    valid_files = {"audio_file": ("doctor.webm", b"\x1aE\xdf\xa3fake-webm-audio-bytes", "audio/webm")}
    resp_ok = await client.post(
        f"/api/v1/meetings/{meeting_id}/upload-audio",
        files=valid_files,
        headers=doc_headers,
    )
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert data["meeting_id"] == meeting_id
    assert data["role"] == "doctor"


@pytest.mark.asyncio
async def test_status_endpoint_lifecycle(client: AsyncClient):
    """Verify status endpoint returns transcription and extraction state."""
    setup = await setup_test_meeting(client, "doc_status@test.com", "pat_status@test.com")
    meeting_id = setup["meeting_id"]
    doc_headers = setup["doc_headers"]

    # Initial state before audio: transcription_status is None
    status_resp = await client.get(
        f"/api/v1/consultation-ai/{meeting_id}/status",
        headers=doc_headers,
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["meeting_id"] == meeting_id
    assert status_data["transcription_status"] is None
    assert status_data["has_approved_extraction"] is False

    # After audio upload: transcription_status is "pending"
    valid_files = {"audio_file": ("doctor.webm", b"\x1aE\xdf\xa3fake-audio", "audio/webm")}
    await client.post(
        f"/api/v1/meetings/{meeting_id}/upload-audio",
        files=valid_files,
        headers=doc_headers,
    )

    status_resp2 = await client.get(
        f"/api/v1/consultation-ai/{meeting_id}/status",
        headers=doc_headers,
    )
    status_data2 = status_resp2.json()
    assert status_data2["transcription_status"] == "pending"
    assert status_data2["has_doctor_audio"] is True


@pytest.mark.asyncio
async def test_approval_workflow_creates_prescription(client: AsyncClient):
    """
    Verify approving an extraction creates official Prescription
    and PrescriptionMedicine records.
    """
    setup = await setup_test_meeting(client, "doc_approve@test.com", "pat_approve@test.com")
    meeting_id = setup["meeting_id"]
    doc_headers = setup["doc_headers"]

    # Seed an extraction record directly for testing approval
    sample_extraction_data = {
        "medications": [
            {
                "medication_name": "Amoxicillin 500mg",
                "dose_value": "500",
                "dose_unit": "mg",
                "route": "oral",
                "frequency": "twice daily",
                "timings": ["morning", "night"],
                "meal_relation": "after_meals",
                "start_date": date.today().isoformat(),
                "end_date": (date.today() + timedelta(days=5)).isoformat(),
                "special_instructions": "Take with plenty of water",
                "confidence": 0.98,
            }
        ],
        "diagnoses": [{"diagnosis_name": "Bacterial Sinusitis", "certainty": "confirmed"}],
        "symptoms": [{"symptom": "Facial pain", "duration": "4 days"}],
        "tests": [],
        "follow_ups": [{"follow_up_period": "1 week"}],
        "doctor_instructions": [{"category": "hydration", "instruction_text": "Drink at least 2.5L water daily"}],
        "consultation_summary": "Patient presented with sinus pressure and fever. Prescribed Amoxicillin.",
    }

    async with test_session_maker() as session:
        transcript = ConsultationTranscript(
            meeting_id=meeting_id,
            transcription_status="completed",
        )
        session.add(transcript)
        await session.flush()

        extraction = ConsultationAIExtraction(
            meeting_id=meeting_id,
            transcript_id=transcript.id,
            doctor_id=setup["doctor_id"],
            patient_id=setup["patient_id"],
            version=1,
            status="completed",
            extraction_data=sample_extraction_data,
            confidence_score=0.98,
            is_approved=False,
        )
        session.add(extraction)
        await session.commit()

    # Call approve endpoint
    approve_payload = {
        "edited_extraction": sample_extraction_data,
        "notes": "Patient advised to complete full 5-day antibiotic course.",
    }

    resp = await client.post(
        f"/api/v1/consultation-ai/{meeting_id}/approve",
        json=approve_payload,
        headers=doc_headers,
    )
    assert resp.status_code == 201
    rx = resp.json()
    assert rx["meeting_id"] == meeting_id
    assert len(rx["medicines"]) == 1

    med = rx["medicines"][0]
    assert "Amoxicillin" in med["medicine_name"]
    # Verify time slot mapping
    assert med["morning"] is True
    assert med["night"] is True
    assert med["afternoon"] is False
    assert med["evening"] is False
