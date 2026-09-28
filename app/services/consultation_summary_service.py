"""
Consultation Summary Service — generates and manages LLM-powered consultation summaries.

After the AI extraction pipeline completes, this service takes the transcript
and generates a comprehensive, human-readable summary covering everything
discussed: illness, duration, medications, diet, sleep, tests, water intake, etc.

The summary is stored exactly as the LLM generates it — no field-level extraction.
"""

import time
import uuid
from typing import Any, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.consultation_summary import ConsultationSummary
from app.models.enums import UserRole
from app.repositories.consultation_ai_repository import ConsultationAIRepository
from app.repositories.consultation_summary_repository import ConsultationSummaryRepository
from app.repositories.meeting_repository import MeetingRepository
from app.schemas.consultation_summary import (
    ConsultationHistoryResponse,
    ConsultationSummaryResponse,
)

logger = get_logger(__name__)
settings = get_settings()


# ── Summary Generation Prompt ────────────────────────────────────────────────

SUMMARY_SYSTEM_PROMPT = """You are a medical consultation documentation assistant. Your job is to write a clear, comprehensive summary of a doctor-patient consultation based on the transcript provided.

Write the summary in a well-structured, easy-to-read format. Cover ALL of the following points that were discussed (skip any section that was NOT discussed):

1. **Patient's Complaint / Illness** — What the patient came for, what symptoms they described
2. **Duration** — How long they have been experiencing the issue
3. **Doctor's Assessment** — What the doctor said about the condition, any diagnosis made
4. **Medications Prescribed** — Name of medicine, dosage, frequency, duration, any special instructions
5. **Diet Plan** — Any dietary recommendations or restrictions
6. **Sleep Recommendations** — Any advice about sleep schedule or habits
7. **Exercise / Activity** — Physical activity recommendations
8. **Water Intake** — Any advice about hydration
9. **Tests Ordered** — Any lab tests, imaging, or investigations ordered and why
10. **Follow-up Instructions** — When to come back, warning signs to watch for
11. **Other Advice** — Any other recommendations or precautions the doctor mentioned

RULES:
- Write ONLY what was actually discussed in the consultation. Do NOT invent information.
- Use clear headings with markdown formatting (## for sections).
- Use bullet points for medications and instructions.
- If the consultation was in Urdu/Roman Urdu, write the summary in the same language.
- If it was in English, write in English.
- If it was mixed (code-switching), write in the language that was predominantly used but keep medical terms as-is.
- Keep it professional but easy for a patient to understand.
- Include the doctor's exact instructions as closely as possible.
- Do NOT add medical advice that the doctor did not give.

Write the summary directly. No preamble, no "Here is the summary" — just start with the content."""


class ConsultationSummaryService:
    """Generates and manages consultation summaries."""

    # ── Summary Generation (called after extraction pipeline) ─────────

    @classmethod
    async def generate_summary_from_transcript(
        cls,
        meeting_id: uuid.UUID,
    ) -> None:
        """
        Generate a consultation summary for a completed meeting.
        Runs in background — uses its own DB session.
        """
        from app.core.database import async_session_maker

        async with async_session_maker() as session:
            try:
                meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
                if not meeting:
                    logger.warning(f"[SUMMARY_ABORT] meeting_id={meeting_id} — Meeting not found")
                    return

                # Check if summary already exists
                existing = await ConsultationSummaryRepository.get_by_meeting_id(session, meeting_id)
                if existing and existing.status == "completed":
                    logger.info(f"[SUMMARY_SKIP] meeting_id={meeting_id} — Summary already exists")
                    return

                # Get the transcript
                transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
                    session, meeting_id
                )

                transcript_text = ""
                if transcript and transcript.full_text:
                    transcript_text = transcript.full_text
                elif transcript and transcript.structured_transcript:
                    # Build text from segments
                    lines = []
                    for seg in transcript.structured_transcript:
                        speaker = seg.get("speaker", "participant").upper()
                        name = seg.get("speakerName") or f"[{speaker}]"
                        text = seg.get("text", "").strip()
                        if text:
                            lines.append(f"{name}: {text}")
                    transcript_text = "\n".join(lines)

                # Fall back to doctor notes if no transcript
                if not transcript_text and meeting.doctor_notes:
                    transcript_text = f"[Doctor's Clinical Notes]\n{meeting.doctor_notes.strip()}"

                if not transcript_text:
                    logger.warning(f"[SUMMARY_SKIP] meeting_id={meeting_id} — No transcript or notes available")
                    return

                # Create or update the summary record
                if existing:
                    summary = existing
                    summary.status = "processing"
                    summary.error_message = None
                else:
                    summary = ConsultationSummary(
                        meeting_id=meeting_id,
                        patient_id=meeting.patient_id,
                        doctor_id=meeting.doctor_id,
                        status="processing",
                        llm_model_used=settings.GROQ_MODEL,
                    )
                    summary = await ConsultationSummaryRepository.create_summary(session, summary)

                await session.commit()

                logger.info(
                    f"[SUMMARY_START] meeting_id={meeting_id} "
                    f"transcript_length={len(transcript_text)}"
                )

                # Call LLM for summary
                start_time = time.time()
                summary_text = await cls._call_summary_llm(transcript_text)
                elapsed_ms = int((time.time() - start_time) * 1000)

                # Update summary record
                summary.summary_text = summary_text
                summary.status = "completed"
                summary.processing_time_ms = elapsed_ms
                summary.llm_model_used = settings.GROQ_MODEL

                await session.commit()

                logger.info(
                    f"[SUMMARY_COMPLETE] meeting_id={meeting_id} "
                    f"summary_length={len(summary_text)} elapsed_ms={elapsed_ms}"
                )

            except Exception as e:
                logger.error(
                    f"[SUMMARY_ERROR] meeting_id={meeting_id} — {e}",
                    exc_info=True,
                )
                try:
                    # Try to mark as failed
                    existing = await ConsultationSummaryRepository.get_by_meeting_id(
                        session, meeting_id
                    )
                    if existing:
                        existing.status = "failed"
                        existing.error_message = str(e)[:500]
                        await session.commit()
                except Exception as inner_err:
                    logger.error(f"[SUMMARY_DB_ERROR] Could not mark as failed: {inner_err}")

    @staticmethod
    async def _call_summary_llm(transcript_text: str) -> str:
        """Call the Groq LLM to generate a consultation summary."""
        import asyncio
        import re

        import httpx

        api_key = settings.groq_api_key
        if not api_key:
            raise ValidationError("Groq API key is not configured.")

        GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

        user_content = (
            "--- CONSULTATION TRANSCRIPT ---\n"
            f"{transcript_text}\n"
            "--- END OF TRANSCRIPT ---\n\n"
            "Write a comprehensive consultation summary covering all discussed topics."
        )

        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        from app.services.groq_queue_service import GroqPriority, groq_queue

        return await groq_queue.submit_chat_completion(
            messages=messages,
            model=settings.GROQ_MODEL,
            temperature=0.2,
            max_tokens=4096,
            priority=GroqPriority.NORMAL,
            caller="ConsultationSummaryService",
            timeout=float(settings.AI_LLM_TIMEOUT_SECONDS),
            enqueue_retries=3,
            max_retries=3,
        )

    # ── Read Endpoints ───────────────────────────────────────────────────

    @staticmethod
    def _build_summary_response(summary: ConsultationSummary) -> ConsultationSummaryResponse:
        """Build a response schema from a summary model instance."""
        doctor_name = None
        doctor_specialization = None
        patient_name = None
        meeting_date = None

        if summary.doctor:
            if hasattr(summary.doctor, "doctor_profile") and summary.doctor.doctor_profile:
                doctor_name = summary.doctor.doctor_profile.full_name
                doctor_specialization = summary.doctor.doctor_profile.specialization
            if not doctor_name:
                doctor_name = summary.doctor.email.split("@")[0] if summary.doctor.email else "Doctor"

        if summary.patient:
            if hasattr(summary.patient, "patient_profile") and summary.patient.patient_profile:
                patient_name = summary.patient.patient_profile.full_name
            if not patient_name:
                patient_name = summary.patient.email.split("@")[0] if summary.patient.email else "Patient"

        if summary.meeting:
            meeting_date = summary.meeting.start_time

        return ConsultationSummaryResponse(
            id=summary.id,
            meeting_id=summary.meeting_id,
            patient_id=summary.patient_id,
            doctor_id=summary.doctor_id,
            doctor_name=doctor_name,
            doctor_specialization=doctor_specialization,
            patient_name=patient_name,
            summary_text=summary.summary_text,
            status=summary.status,
            meeting_date=meeting_date,
            created_at=summary.created_at,
        )

    @classmethod
    async def get_summary_for_meeting(
        cls,
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
    ) -> ConsultationSummaryResponse:
        """Get the summary for a specific meeting. Both doctor and patient can view."""
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id not in (meeting.doctor_id, meeting.patient_id):
            if getattr(user, "role", None) != UserRole.SAAS_ADMIN:
                raise AuthorizationError("Not authorized to view this consultation summary")

        summary = await ConsultationSummaryRepository.get_by_meeting_id(session, meeting_id)
        if not summary:
            raise NotFoundError("Consultation summary not found for this meeting")

        return cls._build_summary_response(summary)

    @classmethod
    async def get_patient_history(
        cls,
        session: AsyncSession,
        patient_id: uuid.UUID,
        user: Any,
        limit: int = 50,
        offset: int = 0,
    ) -> ConsultationHistoryResponse:
        """
        Get paginated consultation history for a patient.
        - Patient can view their own history.
        - Doctors can view any patient's history (for informed care).
        - Admin can view any.
        """
        # Authorization
        is_own = user.id == patient_id
        is_doctor = getattr(user, "role", None) == UserRole.DOCTOR
        is_admin = getattr(user, "role", None) == UserRole.SAAS_ADMIN

        if not (is_own or is_doctor or is_admin):
            raise AuthorizationError("Not authorized to view this patient's history")

        summaries, total = await ConsultationSummaryRepository.get_patient_history(
            session, patient_id, limit=limit, offset=offset
        )

        return ConsultationHistoryResponse(
            summaries=[cls._build_summary_response(s) for s in summaries],
            total=total,
            limit=limit,
            offset=offset,
        )

    @classmethod
    async def get_patient_history_for_doctor(
        cls,
        session: AsyncSession,
        patient_id: uuid.UUID,
        doctor_id: uuid.UUID,
        user: Any,
    ) -> List[ConsultationSummaryResponse]:
        """
        Doctor views a patient's full consultation history.
        Used in the meeting room or before a consultation.
        """
        if user.id != doctor_id:
            if getattr(user, "role", None) != UserRole.SAAS_ADMIN:
                raise AuthorizationError("Not authorized")

        summaries = await ConsultationSummaryRepository.get_patient_history_for_doctor(
            session, patient_id, doctor_id
        )

        return [cls._build_summary_response(s) for s in summaries]
