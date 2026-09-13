"""
Consultation AI Service — LLM-powered extraction of structured medical data
from consultation transcripts, with chunking and approval-to-prescription workflow.

Provides:
- Single-pass and map-reduce extraction from transcripts.
- Medical safety enforcement (AI is documentation-only, never prescribes).
- Chunk-at-utterance-boundary splitting with configurable overlap.
- Conflict resolution and deduplication across chunks.
- Approval → Prescription creation in a single transaction.
"""

import asyncio
import json
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.consultation_ai_extraction import ConsultationAIExtraction
from app.models.enums import MeetingStatus, UserRole
from app.models.prescription import Prescription, PrescriptionMedicine
from app.repositories.consultation_ai_repository import ConsultationAIRepository
from app.repositories.meeting_repository import MeetingRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.schemas.consultation_ai import (
    ApproveExtractionRequest,
    ConsultationExtractionResult,
    ExtractionResponse,
    ExtractionVersionSummary,
    ExtractionVersionsResponse,
    ConsultationAIStatusResponse,
    UncertainItem,
)
from app.schemas.prescription import PrescriptionMedicineResponse, PrescriptionResponse
from app.services.reminder_scheduler import schedule_prescription_reminders

logger = get_logger(__name__)
settings = get_settings()

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── Extraction System Prompt ─────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are a medical consultation documentation assistant. You extract ONLY information explicitly stated or instructed by the DOCTOR during the consultation.

CRITICAL SAFETY RULES:
1. NEVER diagnose. NEVER recommend medicines. NEVER invent information.
2. ONLY extract what the DOCTOR explicitly stated as instructions, prescriptions, diagnoses, or observations.
3. If the PATIENT mentions taking a medicine, this is NOT a doctor prescription unless the doctor explicitly confirms or prescribes it.
4. If the doctor says "maybe" or "could be" or "possibly", mark the diagnosis as "suspected", NOT "confirmed".
5. If ANY medical field (dose, frequency, timing, duration) was NOT explicitly stated by the doctor, leave it as null. NEVER fill it in.
6. If information is ambiguous, add it to uncertain_items with an explanation.

SPEAKER LABELS:
- Lines marked [DOCTOR] are the doctor's statements.
- Lines marked [PATIENT] are the patient's statements.
- Extract prescriptions/instructions ONLY from [DOCTOR] lines.
- Extract symptoms/complaints from [PATIENT] lines.

LANGUAGE:
- The consultation may be in Urdu, English, or code-mixed (Urdu-English / Roman Urdu).
- Translate medication names to their standard English/generic form if recognizable.
- Keep doctor instructions in the language they were given.

OUTPUT FORMAT:
Return valid JSON matching this exact schema. No markdown, no commentary, no explanation outside JSON:
{
  "medications": [{"medication_name": "...", "dose_value": "...", "dose_unit": "...", "route": "...", "frequency": "...", "times_per_day": null, "timings": ["morning", "evening"], "meal_relation": "...", "start_date": null, "end_date": null, "duration_days": null, "special_instructions": "...", "status": "new", "confidence": 0.95, "evidence": [{"speaker": "doctor", "transcript_excerpt": "..."}]}],
  "diagnoses": [{"diagnosis_name": "...", "certainty": "confirmed|suspected|differential|ruled_out", "confidence": 0.9, "evidence": []}],
  "symptoms": [{"symptom": "...", "reported_by": "patient|doctor_observed", "duration": "...", "severity": "..."}],
  "tests": [{"test_name": "...", "urgency": "routine|urgent|stat", "reason": "...", "evidence": []}],
  "follow_ups": [{"follow_up_date": null, "follow_up_period": "1 week", "instructions": "..."}],
  "doctor_instructions": [{"category": "diet|activity|hydration|rest|monitoring|precaution|lifestyle|other", "instruction_text": "..."}],
  "uncertain_items": [{"field": "medication|diagnosis|symptom|test", "extracted_value": "...", "confidence": 0.3, "reason": "...", "evidence": null}],
  "consultation_summary": "Brief 2-3 sentence summary of the consultation."
}"""


class ConsultationAIService:
    """Handles AI extraction from transcripts and approval-to-prescription workflow."""

    # ── Token Counting ───────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count. Uses tiktoken if available, otherwise heuristic."""
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except (ImportError, Exception):
            # Conservative estimate for mixed Urdu/English text
            return int(len(text) / 3.5)

    # ── Chunking ─────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_transcript(
        segments: List[Dict],
        max_tokens: int,
        overlap_tokens: int,
    ) -> List[str]:
        """
        Split structured transcript into chunks at utterance boundaries.
        Each segment is atomic — never split mid-utterance.
        """
        chunks: List[str] = []
        current_lines: List[str] = []
        current_tokens = 0
        overlap_buffer: List[str] = []

        for seg in segments:
            speaker_label = "[DOCTOR]" if seg.get("speaker") == "doctor" else "[PATIENT]"
            start = seg.get("start_time", 0)
            minutes = int(start // 60)
            seconds = int(start % 60)
            line = f"[{minutes:02d}:{seconds:02d}] {speaker_label} {seg.get('text', '')}"

            line_tokens = ConsultationAIService._estimate_tokens(line)

            if current_tokens + line_tokens > max_tokens and current_lines:
                # Save current chunk
                chunks.append("\n".join(current_lines))

                # Build overlap from the tail of current_lines
                overlap_lines = []
                overlap_count = 0
                for prev_line in reversed(current_lines):
                    prev_tokens = ConsultationAIService._estimate_tokens(prev_line)
                    if overlap_count + prev_tokens > overlap_tokens:
                        break
                    overlap_lines.insert(0, prev_line)
                    overlap_count += prev_tokens

                current_lines = overlap_lines + [line]
                current_tokens = overlap_count + line_tokens
            else:
                current_lines.append(line)
                current_tokens += line_tokens

        # Final chunk
        if current_lines:
            chunks.append("\n".join(current_lines))

        return chunks

    # ── LLM Call ─────────────────────────────────────────────────────────

    @staticmethod
    async def _call_extraction_llm(
        transcript_text: str,
        chunk_num: Optional[int] = None,
        total_chunks: Optional[int] = None,
    ) -> str:
        """
        Call Groq Chat API for medical extraction.
        Returns raw JSON string from LLM.
        """
        api_key = settings.groq_api_key
        if not api_key:
            raise ValidationError("Groq API key is not configured.")

        user_content = ""
        if chunk_num and total_chunks and total_chunks > 1:
            user_content += (
                f"[Chunk {chunk_num} of {total_chunks}] — "
                "This is a segment of a longer consultation. "
                "Extract all relevant information from this segment.\n\n"
            )

        user_content += (
            "--- CONSULTATION TRANSCRIPT ---\n"
            f"{transcript_text}\n"
            "--- END OF TRANSCRIPT ---\n\n"
            "Extract all medical information according to your instructions. "
            "Return ONLY valid JSON."
        )

        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        payload = {
            "model": settings.GROQ_MODEL,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4096,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        timeout = float(settings.AI_LLM_TIMEOUT_SECONDS)
        max_retries = settings.AI_LLM_MAX_RETRIES
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        GROQ_CHAT_URL, json=payload, headers=headers
                    )

                if resp.status_code == 429:
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"LLM rate limit, waiting {wait_time}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait_time)
                    continue

                if resp.status_code != 200:
                    err_text = resp.text[:500]
                    raise ValidationError(
                        f"Groq API error ({resp.status_code}): {err_text}"
                    )

                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValidationError("Groq returned empty response")

                raw_content = choices[0].get("message", {}).get("content", "").strip()

                # Clean <think> tags from reasoning models
                cleaned = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
                if "<think>" in cleaned and "</think>" not in cleaned:
                    cleaned = cleaned.split("<think>", 1)[0].strip()

                return cleaned or raw_content

            except httpx.TimeoutException as e:
                last_error = e
                if attempt < max_retries:
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"LLM timeout, retrying in {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue

            except ValidationError:
                raise

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue

        raise ValidationError(f"LLM call failed after {max_retries + 1} attempts: {last_error}")

    # ── JSON Parsing ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_llm_json(raw_response: str) -> Dict:
        """
        Parse JSON from LLM response. Handles markdown code blocks.
        """
        text = raw_response.strip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code block
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group())
            except json.JSONDecodeError:
                pass

        raise ValidationError("Could not parse valid JSON from LLM response")

    # ── Merge Chunk Results ──────────────────────────────────────────────

    @staticmethod
    def _merge_chunk_results(
        results: List[ConsultationExtractionResult],
    ) -> ConsultationExtractionResult:
        """
        Merge extractions from multiple chunks with deduplication
        and conflict resolution.
        """
        merged = ConsultationExtractionResult()

        seen_meds = {}  # medication_name_lower -> ExtractedMedication
        seen_diagnoses = {}  # diagnosis_name_lower -> ExtractedDiagnosis
        seen_tests = {}  # test_name_lower -> ExtractedTest
        seen_symptoms = set()  # symptom_lower

        for chunk_result in results:
            # Medications — dedup by name, handle conflicts
            for med in chunk_result.medications:
                key = med.medication_name.strip().lower()
                if key in seen_meds:
                    existing = seen_meds[key]
                    if med.status == "modified" or med.status == "stopped":
                        # Explicit change instruction → later chunk wins
                        seen_meds[key] = med
                    elif (
                        med.dose_value and existing.dose_value
                        and med.dose_value != existing.dose_value
                    ):
                        # Conflict without explicit change → uncertain
                        merged.uncertain_items.append(
                            UncertainItem(
                                field="medication",
                                extracted_value=f"{med.medication_name}: {existing.dose_value} vs {med.dose_value}",
                                confidence=min(existing.confidence, med.confidence),
                                reason="Conflicting dosage instructions found in different parts of the consultation",
                            )
                        )
                        # Keep higher confidence version
                        if med.confidence > existing.confidence:
                            seen_meds[key] = med
                    elif med.confidence > existing.confidence:
                        seen_meds[key] = med
                else:
                    seen_meds[key] = med

            # Diagnoses — dedup by name, keep highest certainty
            for dx in chunk_result.diagnoses:
                key = dx.diagnosis_name.strip().lower()
                if key in seen_diagnoses:
                    existing = seen_diagnoses[key]
                    certainty_rank = {"confirmed": 4, "suspected": 3, "differential": 2, "ruled_out": 1}
                    if certainty_rank.get(dx.certainty, 0) > certainty_rank.get(existing.certainty, 0):
                        seen_diagnoses[key] = dx
                else:
                    seen_diagnoses[key] = dx

            # Symptoms — dedup by name
            for sym in chunk_result.symptoms:
                key = sym.symptom.strip().lower()
                if key not in seen_symptoms:
                    seen_symptoms.add(key)
                    merged.symptoms.append(sym)

            # Tests — dedup by name
            for test in chunk_result.tests:
                key = test.test_name.strip().lower()
                if key not in seen_tests:
                    seen_tests[key] = test

            # Follow-ups, instructions — union (no dedup needed)
            merged.follow_ups.extend(chunk_result.follow_ups)
            merged.doctor_instructions.extend(chunk_result.doctor_instructions)

            # Uncertain items — union
            merged.uncertain_items.extend(chunk_result.uncertain_items)

            # Consultation summary — use last non-empty one
            if chunk_result.consultation_summary:
                merged.consultation_summary = chunk_result.consultation_summary

        # Flatten deduped collections
        merged.medications = list(seen_meds.values())
        merged.diagnoses = list(seen_diagnoses.values())
        merged.tests = list(seen_tests.values())

        # Filter out None uncertain items
        merged.uncertain_items = [u for u in merged.uncertain_items if u is not None]

        return merged

    # ── Main Extraction Flow ─────────────────────────────────────────────

    @classmethod
    async def generate_extraction(
        cls,
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
    ) -> ExtractionResponse:
        """
        Start AI extraction. Creates a new extraction version.
        Returns immediately with status=processing.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id != meeting.doctor_id:
            raise AuthorizationError("Only the assigned doctor can generate extractions")

        transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
            session, meeting_id
        )
        if not transcript or transcript.transcription_status != "completed":
            raise ValidationError(
                "Transcription must be completed before generating extraction"
            )

        # Create new version
        next_version = await ConsultationAIRepository.get_next_extraction_version(
            session, meeting_id
        )

        extraction = ConsultationAIExtraction(
            meeting_id=meeting_id,
            transcript_id=transcript.id,
            doctor_id=meeting.doctor_id,
            patient_id=meeting.patient_id,
            version=next_version,
            status="processing",
            llm_model_used=settings.GROQ_MODEL,
        )
        extraction = await ConsultationAIRepository.create_extraction(session, extraction)
        await session.commit()

        return ExtractionResponse(
            id=extraction.id,
            meeting_id=meeting_id,
            version=next_version,
            status="processing",
            llm_model_used=settings.GROQ_MODEL,
            created_at=extraction.created_at,
        )

    @classmethod
    async def run_extraction_pipeline(
        cls,
        meeting_id: uuid.UUID,
        extraction_id: uuid.UUID,
    ) -> None:
        """
        Execute the full extraction pipeline in a background task.
        Uses its own database session.
        """
        from app.core.database import async_session_maker

        async with async_session_maker() as session:
            try:
                extraction = await ConsultationAIRepository.get_extraction_by_id(
                    session, extraction_id
                )
                if not extraction:
                    return

                transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
                    session, meeting_id
                )
                if not transcript or not transcript.structured_transcript:
                    extraction.status = "failed"
                    extraction.error_message = "No transcript data available"
                    await session.commit()
                    return

                start_time = time.time()
                segments = transcript.structured_transcript

                if not isinstance(segments, list) or len(segments) == 0:
                    extraction.status = "failed"
                    extraction.error_message = "Transcript has no segments"
                    await session.commit()
                    return

                # Generate full text for token estimation
                full_text = transcript.full_text or ""
                if not full_text:
                    from app.services.transcription_service import TranscriptionService
                    full_text = TranscriptionService._generate_full_text(segments)

                total_tokens = cls._estimate_tokens(full_text)
                max_tokens = settings.AI_CHUNK_MAX_TOKENS
                overlap_tokens = settings.AI_CHUNK_OVERLAP_TOKENS

                chunk_results: List[ConsultationExtractionResult] = []

                if total_tokens <= max_tokens:
                    # ── Single-pass extraction ───────────────────────
                    raw_response = await cls._call_extraction_llm(full_text)
                    extraction.raw_llm_response = raw_response
                    extraction.total_chunks = 1

                    parsed = cls._parse_llm_json(raw_response)
                    result = ConsultationExtractionResult(**parsed)
                    chunk_results.append(result)

                else:
                    # ── Multi-chunk map-reduce extraction ────────────
                    chunks = cls._chunk_transcript(segments, max_tokens, overlap_tokens)
                    extraction.total_chunks = len(chunks)
                    all_raw_responses = []

                    semaphore = asyncio.Semaphore(settings.AI_MAX_CONCURRENT_CHUNKS)

                    async def process_chunk(chunk_text: str, idx: int) -> ConsultationExtractionResult:
                        async with semaphore:
                            raw = await cls._call_extraction_llm(
                                chunk_text,
                                chunk_num=idx + 1,
                                total_chunks=len(chunks),
                            )
                            all_raw_responses.append(f"--- Chunk {idx + 1} ---\n{raw}")
                            parsed_chunk = cls._parse_llm_json(raw)
                            return ConsultationExtractionResult(**parsed_chunk)

                    tasks = [
                        process_chunk(chunk, i) for i, chunk in enumerate(chunks)
                    ]
                    chunk_results = await asyncio.gather(*tasks)
                    extraction.raw_llm_response = "\n\n".join(all_raw_responses)

                # Merge results
                if len(chunk_results) == 1:
                    final_result = chunk_results[0]
                else:
                    final_result = cls._merge_chunk_results(list(chunk_results))

                # Auto-flag low-confidence items
                for med in final_result.medications:
                    if med.confidence < 0.5:
                        final_result.uncertain_items.append(
                            type(final_result.uncertain_items[0])(
                                field="medication",
                                extracted_value=med.medication_name,
                                confidence=med.confidence,
                                reason="Low confidence extraction",
                            ) if final_result.uncertain_items else None
                        )

                # Filter None uncertain items
                final_result.uncertain_items = [
                    u for u in final_result.uncertain_items if u is not None
                ]

                # Calculate average confidence
                all_confidences = (
                    [m.confidence for m in final_result.medications]
                    + [d.confidence for d in final_result.diagnoses]
                )
                avg_confidence = (
                    sum(all_confidences) / len(all_confidences)
                    if all_confidences
                    else 0.0
                )

                elapsed_ms = int((time.time() - start_time) * 1000)

                extraction.extraction_data = final_result.model_dump()
                extraction.status = "completed"
                extraction.confidence_score = round(avg_confidence, 3)
                extraction.processing_time_ms = elapsed_ms
                extraction.error_message = None

                await session.commit()

                logger.info(
                    f"extraction_completed: meeting_id={meeting_id} "
                    f"version={extraction.version} chunks={extraction.total_chunks} "
                    f"medications={len(final_result.medications)} "
                    f"diagnoses={len(final_result.diagnoses)} "
                    f"confidence={avg_confidence:.2f} elapsed_ms={elapsed_ms}"
                )

            except Exception as e:
                logger.error(f"Extraction pipeline error: {e}")
                try:
                    extraction = await ConsultationAIRepository.get_extraction_by_id(
                        session, extraction_id
                    )
                    if extraction:
                        extraction.status = "failed"
                        extraction.error_message = f"{str(e)[:500]}"
                        await session.commit()
                except Exception:
                    pass

    # ── Read Extraction ──────────────────────────────────────────────────

    @staticmethod
    async def get_extraction(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
        version: Optional[int] = None,
    ) -> ExtractionResponse:
        """Fetch extraction for a meeting (latest or specific version)."""
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if version:
            extraction = await ConsultationAIRepository.get_extraction_by_version(
                session, meeting_id, version
            )
        else:
            extraction = await ConsultationAIRepository.get_latest_extraction(
                session, meeting_id
            )

        if not extraction:
            raise NotFoundError("No extraction found for this meeting")

        # Authorization: unapproved → doctor only; approved → doctor, patient, admin
        if not extraction.is_approved:
            if user.id != meeting.doctor_id:
                raise AuthorizationError(
                    "Only the assigned doctor can view unapproved extractions"
                )
        else:
            if user.id not in (meeting.doctor_id, meeting.patient_id):
                if user.role != UserRole.SAAS_ADMIN:
                    raise AuthorizationError("Not authorized to view this extraction")

        extraction_data = None
        if extraction.extraction_data:
            try:
                extraction_data = ConsultationExtractionResult(**extraction.extraction_data)
            except Exception:
                extraction_data = None

        approved_data = None
        if extraction.approved_extraction_data:
            try:
                approved_data = ConsultationExtractionResult(**extraction.approved_extraction_data)
            except Exception:
                approved_data = None

        return ExtractionResponse(
            id=extraction.id,
            meeting_id=extraction.meeting_id,
            version=extraction.version,
            status=extraction.status,
            extraction_data=extraction_data,
            confidence_score=extraction.confidence_score,
            llm_model_used=extraction.llm_model_used,
            total_chunks=extraction.total_chunks,
            processing_time_ms=extraction.processing_time_ms,
            error_message=extraction.error_message,
            is_approved=extraction.is_approved,
            approved_at=extraction.approved_at,
            approved_extraction_data=approved_data,
            doctor_approval_notes=extraction.doctor_approval_notes,
            prescription_id=extraction.prescription_id,
            created_at=extraction.created_at,
        )

    @staticmethod
    async def list_extraction_versions(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
    ) -> ExtractionVersionsResponse:
        """List all extraction versions for a meeting."""
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id != meeting.doctor_id:
            raise AuthorizationError("Only the assigned doctor can view extraction versions")

        extractions = await ConsultationAIRepository.list_extraction_versions(
            session, meeting_id
        )

        versions = [
            ExtractionVersionSummary(
                id=e.id,
                version=e.version,
                status=e.status,
                is_approved=e.is_approved,
                confidence_score=e.confidence_score,
                total_chunks=e.total_chunks,
                created_at=e.created_at,
            )
            for e in extractions
        ]

        return ExtractionVersionsResponse(
            meeting_id=meeting_id,
            versions=versions,
        )

    # ── Approval → Prescription ──────────────────────────────────────────

    @classmethod
    async def approve_extraction(
        cls,
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
        request: ApproveExtractionRequest,
    ) -> PrescriptionResponse:
        """
        Approve an extraction and create a Prescription in a single transaction.
        Idempotent: if prescription already exists, returns existing.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id != meeting.doctor_id:
            raise AuthorizationError("Only the assigned doctor can approve extractions")

        # Check for existing prescription (idempotency)
        existing_rx = await PrescriptionRepository.get_by_meeting_id(session, meeting_id)
        if existing_rx:
            from app.repositories.user_repository import UserRepository
            doctor_user = await UserRepository.get_by_id(session, meeting.doctor_id)
            patient_user = await UserRepository.get_by_id(session, meeting.patient_id)
            doctor_name = (
                doctor_user.doctor_profile.full_name
                if (doctor_user and doctor_user.doctor_profile and doctor_user.doctor_profile.full_name)
                else (doctor_user.email.split("@")[0] if doctor_user else "Doctor")
            )
            patient_name = (
                patient_user.patient_profile.full_name
                if (patient_user and patient_user.patient_profile and patient_user.patient_profile.full_name)
                else (patient_user.email.split("@")[0] if patient_user else "Patient")
            )
            return PrescriptionResponse(
                id=existing_rx.id,
                meeting_id=existing_rx.meeting_id,
                doctor_id=existing_rx.doctor_id,
                patient_id=existing_rx.patient_id,
                notes=existing_rx.notes,
                doctor_name=doctor_name,
                patient_name=patient_name,
                created_at=existing_rx.created_at,
                medicines=[PrescriptionMedicineResponse.model_validate(m) for m in existing_rx.medicines],
            )

        # Get latest extraction
        extraction = await ConsultationAIRepository.get_latest_extraction(
            session, meeting_id
        )
        if not extraction:
            raise NotFoundError("No extraction found to approve")

        if extraction.status != "completed":
            raise ValidationError("Cannot approve extraction that is not completed")

        if extraction.is_approved:
            raise ConflictError("This extraction has already been approved")

        # Re-validate edited data
        edited = request.edited_extraction

        # Build Prescription
        prescription = Prescription(
            meeting_id=meeting.id,
            doctor_id=meeting.doctor_id,
            patient_id=meeting.patient_id,
            notes=request.notes.strip() if request.notes else None,
        )

        # Map medications to PrescriptionMedicine
        for med in edited.medications:
            pm = cls._map_medication_to_prescription_medicine(med)
            prescription.medicines.append(pm)

        session.add(prescription)
        await session.flush()

        # Update extraction
        extraction.is_approved = True
        extraction.approved_at = datetime.now(timezone.utc)
        extraction.approved_extraction_data = edited.model_dump()
        extraction.doctor_approval_notes = request.notes
        extraction.prescription_id = prescription.id

        await session.commit()
        await session.refresh(prescription)

        # Schedule reminders
        from app.repositories.user_repository import UserRepository
        patient_user = await UserRepository.get_by_id(session, meeting.patient_id)
        doctor_user = await UserRepository.get_by_id(session, meeting.doctor_id)

        patient_email = patient_user.email if patient_user else ""
        patient_name = (
            patient_user.patient_profile.full_name
            if (patient_user and patient_user.patient_profile and patient_user.patient_profile.full_name)
            else (patient_user.email.split("@")[0] if patient_user else "Patient")
        )
        doctor_name = (
            doctor_user.doctor_profile.full_name
            if (doctor_user and doctor_user.doctor_profile and doctor_user.doctor_profile.full_name)
            else (doctor_user.email.split("@")[0] if doctor_user else "Doctor")
        )

        if patient_email and prescription.medicines:
            try:
                schedule_prescription_reminders(
                    prescription=prescription,
                    patient_email=patient_email,
                    patient_name=patient_name,
                    doctor_name=doctor_name,
                )
            except Exception as e:
                logger.error(f"Error scheduling reminders from AI approval: {e}")

        logger.info(
            f"extraction_approved: meeting_id={meeting_id} "
            f"extraction_id={extraction.id} version={extraction.version} "
            f"prescription_id={prescription.id} "
            f"medicines_count={len(prescription.medicines)}"
        )

        return PrescriptionResponse(
            id=prescription.id,
            meeting_id=prescription.meeting_id,
            doctor_id=prescription.doctor_id,
            patient_id=prescription.patient_id,
            notes=prescription.notes,
            doctor_name=doctor_name,
            patient_name=patient_name,
            created_at=prescription.created_at,
            medicines=[PrescriptionMedicineResponse.model_validate(m) for m in prescription.medicines],
        )

    @staticmethod
    def _map_medication_to_prescription_medicine(
        med: Any,
    ) -> PrescriptionMedicine:
        """
        Map an ExtractedMedication to a PrescriptionMedicine record.
        Formats medicine_name to include dose/route info.
        Maps frequency/timings to morning/afternoon/evening/night slots.
        """
        # Build formatted medicine name
        name_parts = [med.medication_name]
        if med.dose_value:
            name_parts.append(f"{med.dose_value}{med.dose_unit or ''}")
        if med.route:
            name_parts.append(f"({med.route})")
        medicine_name = " ".join(name_parts)

        if med.special_instructions:
            medicine_name += f" — {med.special_instructions}"

        # Determine time slots from timings or frequency
        morning = False
        afternoon = False
        evening = False
        night = False

        timings = med.timings or []
        for t in timings:
            t_lower = t.lower().strip()
            if t_lower in ("morning", "subah", "صبح"):
                morning = True
            elif t_lower in ("afternoon", "dopahar", "دوپہر"):
                afternoon = True
            elif t_lower in ("evening", "sham", "شام"):
                evening = True
            elif t_lower in ("night", "raat", "رات"):
                night = True

        # If no explicit timings, infer from frequency
        if not any([morning, afternoon, evening, night]) and med.frequency:
            freq = med.frequency.lower()
            if "once" in freq or freq == "once_daily":
                morning = True
            elif "twice" in freq or freq == "twice_daily":
                morning = True
                night = True
            elif "thrice" in freq or freq == "thrice_daily" or "three" in freq:
                morning = True
                afternoon = True
                night = True
            elif "four" in freq or freq == "four_times_daily":
                morning = True
                afternoon = True
                evening = True
                night = True
            else:
                # Default to morning if we can't determine
                morning = True

        # If still nothing, default morning
        if not any([morning, afternoon, evening, night]):
            morning = True

        # Meal relation
        before_meal = True  # default
        if med.meal_relation:
            meal = med.meal_relation.lower()
            if "after" in meal:
                before_meal = False
            elif "with" in meal:
                before_meal = False

        # Date range
        today = date.today()
        start_date = today
        end_date = today

        if med.duration_days:
            from datetime import timedelta
            end_date = today + timedelta(days=med.duration_days)
        elif med.end_date:
            try:
                end_date = date.fromisoformat(med.end_date)
            except (ValueError, TypeError):
                end_date = today + __import__("datetime").timedelta(days=7)
        else:
            end_date = today + __import__("datetime").timedelta(days=7)

        if med.start_date:
            try:
                start_date = date.fromisoformat(med.start_date)
            except (ValueError, TypeError):
                pass

        from datetime import time as dt_time

        return PrescriptionMedicine(
            medicine_name=medicine_name[:500],  # Respect column max length
            morning=morning,
            morning_time=dt_time(8, 0) if morning else None,
            morning_before_meal=before_meal if morning else True,
            afternoon=afternoon,
            afternoon_time=dt_time(13, 0) if afternoon else None,
            afternoon_before_meal=before_meal if afternoon else True,
            evening=evening,
            evening_time=dt_time(18, 0) if evening else None,
            evening_before_meal=before_meal if evening else True,
            night=night,
            night_time=dt_time(21, 0) if night else None,
            night_before_meal=before_meal if night else True,
            start_date=start_date,
            end_date=end_date,
        )

    # ── Combined Status ──────────────────────────────────────────────────

    @staticmethod
    async def get_consultation_ai_status(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
    ) -> ConsultationAIStatusResponse:
        """Get combined status of transcription and extraction."""
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id not in (meeting.doctor_id, meeting.patient_id):
            if user.role != UserRole.SAAS_ADMIN:
                raise AuthorizationError("Not authorized")

        transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
            session, meeting_id
        )
        extraction = await ConsultationAIRepository.get_latest_extraction(
            session, meeting_id
        )
        approved = await ConsultationAIRepository.get_approved_extraction(
            session, meeting_id
        )

        return ConsultationAIStatusResponse(
            meeting_id=meeting_id,
            transcription_status=transcript.transcription_status if transcript else None,
            has_doctor_audio=bool(transcript and transcript.doctor_audio_path),
            has_patient_audio=bool(transcript and transcript.patient_audio_path),
            extraction_status=extraction.status if extraction else None,
            has_approved_extraction=bool(approved),
            latest_extraction_version=extraction.version if extraction else None,
            prescription_id=approved.prescription_id if approved else None,
        )
