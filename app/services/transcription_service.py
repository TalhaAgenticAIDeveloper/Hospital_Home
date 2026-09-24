"""
Transcription Service — handles audio file uploads and Groq Whisper API transcription.

Provides:
- Audio file upload and validation for doctor/patient recordings.
- Groq Whisper API integration for speech-to-text with timestamps.
- Interleaving of separate doctor and patient transcripts by timestamp.
- Speaker-labeled structured transcript generation.
"""

import asyncio
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.consultation_transcript import ConsultationTranscript
from app.models.enums import MeetingStatus
from app.models.meeting import Meeting
from app.repositories.consultation_ai_repository import ConsultationAIRepository
from app.repositories.meeting_repository import MeetingRepository
from app.schemas.consultation_ai import (
    AudioUploadResponse,
    TranscriptResponse,
    TranscriptSegment,
    TranscriptionStatusResponse,
)

logger = get_logger(__name__)
settings = get_settings()

GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Supported audio formats (Groq Whisper accepts these)
ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/mp4", "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/ogg", "audio/m4a",
    "audio/x-m4a", "video/webm",
}
ALLOWED_AUDIO_EXTENSIONS = {".webm", ".mp4", ".mp3", ".wav", ".ogg", ".m4a"}


class TranscriptionService:
    """Handles audio uploads, Whisper transcription, and transcript interleaving."""

    # ── Audio Upload ─────────────────────────────────────────────────────

    @staticmethod
    async def upload_audio(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
        audio_file: Any,
    ) -> AudioUploadResponse:
        """
        Upload an audio recording from a meeting participant.
        Each participant uploads their own localStream audio.
        """
        # 1. Validate meeting
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        # 2. Determine participant role
        if user.id == meeting.doctor_id:
            role = "doctor"
        elif user.id == meeting.patient_id:
            role = "patient"
        else:
            raise AuthorizationError("You are not a participant in this meeting")

        # 3. Validate file format
        filename = audio_file.filename or "audio.webm"
        extension = os.path.splitext(filename)[1].lower()
        content_type = audio_file.content_type or ""

        if extension not in ALLOWED_AUDIO_EXTENSIONS and content_type not in ALLOWED_AUDIO_TYPES:
            raise ValidationError(
                f"Unsupported audio format. Supported: {', '.join(ALLOWED_AUDIO_EXTENSIONS)}"
            )

        # 4. Read file and validate size
        file_bytes = await audio_file.read()
        max_size = settings.CONSULTATION_MAX_AUDIO_SIZE_MB * 1024 * 1024
        if len(file_bytes) > max_size:
            raise ValidationError(
                f"Audio file exceeds {settings.CONSULTATION_MAX_AUDIO_SIZE_MB}MB limit"
            )

        if len(file_bytes) == 0:
            raise ValidationError("Audio file is empty")

        # 5. Save to disk
        audio_dir = os.path.join(settings.CONSULTATION_AUDIO_DIR, str(meeting_id))
        os.makedirs(audio_dir, exist_ok=True)

        safe_ext = extension if extension else ".webm"
        audio_filename = f"{role}{safe_ext}"
        audio_path = os.path.join(audio_dir, audio_filename)

        with open(audio_path, "wb") as f:
            f.write(file_bytes)

        # 6. Create or update transcript record
        transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
            session, meeting_id
        )
        if not transcript:
            transcript = ConsultationTranscript(
                meeting_id=meeting_id,
                transcription_status="pending",
            )
            transcript = await ConsultationAIRepository.create_transcript(
                session, transcript
            )

        await ConsultationAIRepository.update_transcript_audio_path(
            session, transcript, role, audio_path
        )
        await session.commit()

        logger.info(
            f"audio_uploaded: meeting_id={meeting_id} role={role} "
            f"size_bytes={len(file_bytes)} path={audio_path}"
        )

        return AudioUploadResponse(
            meeting_id=meeting_id,
            role=role,
            audio_path=audio_path,
            message=f"{role.capitalize()} audio uploaded successfully",
        )

    # ── Transcription ────────────────────────────────────────────────────

    @classmethod
    async def transcribe_meeting(
        cls,
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
    ) -> TranscriptionStatusResponse:
        """
        Initiate transcription for a meeting. Should be called as a background task.
        Returns status immediately; transcription runs in background.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id != meeting.doctor_id:
            raise AuthorizationError("Only the assigned doctor can start transcription")

        transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
            session, meeting_id
        )
        if not transcript:
            raise ValidationError(
                "No audio files uploaded yet. Upload audio before transcribing."
            )

        has_doctor = bool(transcript.doctor_audio_path and os.path.exists(transcript.doctor_audio_path))
        has_patient = bool(transcript.patient_audio_path and os.path.exists(transcript.patient_audio_path))

        if not has_doctor and not has_patient:
            raise ValidationError("No audio files found. Upload audio before transcribing.")

        # Mark as processing
        transcript.transcription_status = "processing"
        transcript.error_message = None
        await session.commit()

        return TranscriptionStatusResponse(
            meeting_id=meeting_id,
            transcription_status="processing",
            has_doctor_audio=has_doctor,
            has_patient_audio=has_patient,
        )

    @classmethod
    async def run_transcription_pipeline(
        cls,
        meeting_id: uuid.UUID,
    ) -> None:
        """
        Execute the full transcription pipeline in a background task.
        Uses its own database session.
        """
        from app.core.database import async_session_maker

        logger.info(f"[PIPELINE_START] meeting_id={meeting_id} — Transcription pipeline starting")

        async with async_session_maker() as session:
            try:
                transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
                    session, meeting_id
                )
                if not transcript:
                    logger.warning(f"[PIPELINE_ABORT] meeting_id={meeting_id} — No transcript record found in DB, aborting")
                    return

                logger.info(
                    f"[PIPELINE_STEP_1] meeting_id={meeting_id} — Transcript record loaded. "
                    f"doctor_audio={transcript.doctor_audio_path or 'NONE'} "
                    f"patient_audio={transcript.patient_audio_path or 'NONE'} "
                    f"current_status={transcript.transcription_status}"
                )

                start_time = time.time()

                doctor_segments: List[Dict] = []
                patient_segments: List[Dict] = []

                # Transcribe doctor audio
                if transcript.doctor_audio_path and os.path.exists(transcript.doctor_audio_path):
                    file_size = os.path.getsize(transcript.doctor_audio_path)
                    logger.info(
                        f"[PIPELINE_STEP_2a] meeting_id={meeting_id} — Calling Whisper API for DOCTOR audio. "
                        f"path={transcript.doctor_audio_path} size_bytes={file_size}"
                    )
                    try:
                        doctor_result = await cls._call_whisper_api(transcript.doctor_audio_path)
                        transcript.doctor_raw_transcription = json.dumps(doctor_result, ensure_ascii=False)
                        doctor_segments = cls._parse_whisper_segments(doctor_result, "doctor")
                        logger.info(
                            f"[PIPELINE_STEP_2a_DONE] meeting_id={meeting_id} — Doctor Whisper done. "
                            f"segments={len(doctor_segments)} language={doctor_result.get('language', 'unknown')}"
                        )
                    except Exception as e:
                        logger.error(f"[PIPELINE_FAIL] meeting_id={meeting_id} — Doctor transcription FAILED: {e}", exc_info=True)
                        transcript.transcription_status = "failed"
                        transcript.error_message = f"Doctor audio transcription failed: {str(e)[:500]}"
                        await session.commit()
                        return
                else:
                    logger.info(f"[PIPELINE_STEP_2a_SKIP] meeting_id={meeting_id} — No doctor audio file found, skipping")

                # Transcribe patient audio
                if transcript.patient_audio_path and os.path.exists(transcript.patient_audio_path):
                    file_size = os.path.getsize(transcript.patient_audio_path)
                    logger.info(
                        f"[PIPELINE_STEP_2b] meeting_id={meeting_id} — Calling Whisper API for PATIENT audio. "
                        f"path={transcript.patient_audio_path} size_bytes={file_size}"
                    )
                    try:
                        patient_result = await cls._call_whisper_api(transcript.patient_audio_path)
                        transcript.patient_raw_transcription = json.dumps(patient_result, ensure_ascii=False)
                        patient_segments = cls._parse_whisper_segments(patient_result, "patient")
                        logger.info(
                            f"[PIPELINE_STEP_2b_DONE] meeting_id={meeting_id} — Patient Whisper done. "
                            f"segments={len(patient_segments)} language={patient_result.get('language', 'unknown')}"
                        )
                    except Exception as e:
                        logger.error(f"[PIPELINE_FAIL] meeting_id={meeting_id} — Patient transcription FAILED: {e}", exc_info=True)
                        transcript.transcription_status = "failed"
                        transcript.error_message = f"Patient audio transcription failed: {str(e)[:500]}"
                        await session.commit()
                        return
                else:
                    logger.info(f"[PIPELINE_STEP_2b_SKIP] meeting_id={meeting_id} — No patient audio file found, skipping")

                # Interleave by timestamp
                interleaved = cls._interleave_transcripts(doctor_segments, patient_segments)
                transcript.structured_transcript = interleaved

                logger.info(
                    f"[PIPELINE_STEP_3] meeting_id={meeting_id} — Interleaved transcript built. "
                    f"total_segments={len(interleaved)}"
                )

                # Generate human-readable full text
                full_text = cls._generate_full_text(interleaved)
                transcript.full_text = full_text

                # Update meeting.transcript_text for backwards compatibility
                meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
                if meeting:
                    meeting.transcript_text = full_text

                # Finalize
                elapsed_ms = int((time.time() - start_time) * 1000)
                transcript.transcription_model = settings.GROQ_WHISPER_MODEL
                transcript.transcription_status = "completed"
                transcript.processing_time_ms = elapsed_ms
                transcript.error_message = None

                await session.commit()

                logger.info(
                    f"[PIPELINE_STEP_4_COMPLETE] meeting_id={meeting_id} — Transcription pipeline COMPLETED. "
                    f"doctor_segments={len(doctor_segments)} "
                    f"patient_segments={len(patient_segments)} "
                    f"total_segments={len(interleaved)} "
                    f"full_text_length={len(full_text)} "
                    f"elapsed_ms={elapsed_ms}"
                )

                # ── Auto-chain AI Clinical Extraction ────────────────────────
                # If audio was transcribed with speech segments, automatically trigger
                # the clinical extraction pipeline in the background so the draft prescription
                # is ready for the doctor without requiring manual clicks.
                if meeting and interleaved and len(interleaved) > 0:
                    try:
                        from app.models.consultation_ai_extraction import ConsultationAIExtraction
                        from app.services.consultation_ai_service import ConsultationAIService

                        # Check if extraction was already generated or is active
                        existing_extraction = await ConsultationAIRepository.get_latest_extraction(
                            session, meeting_id
                        )
                        if not existing_extraction or existing_extraction.status in ("failed", "cancelled"):
                            logger.info(
                                f"[PIPELINE_STEP_5a] meeting_id={meeting_id} — Creating extraction record for auto-chain. "
                                f"existing_extraction={'NONE' if not existing_extraction else existing_extraction.status}"
                            )
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

                            logger.info(
                                f"[PIPELINE_STEP_5b] meeting_id={meeting_id} — Auto-chain extraction INITIATED. "
                                f"extraction_id={extraction.id} version={next_version} — "
                                f"Launching background LLM task now"
                            )

                            # Run extraction LLM pipeline in the background
                            # IMPORTANT: Do NOT await — the LLM call can take 90s+
                            # with retries, which would block the transcription
                            # pipeline and cause the frontend to appear stuck.
                            asyncio.create_task(
                                ConsultationAIService.run_extraction_pipeline(meeting_id, extraction.id)
                            )
                        else:
                            logger.info(
                                f"[PIPELINE_STEP_5_SKIP] meeting_id={meeting_id} — Extraction already exists "
                                f"(status={existing_extraction.status}), skipping auto-chain"
                            )
                    except Exception as auto_ex_err:
                        logger.error(
                            f"[PIPELINE_STEP_5_ERROR] meeting_id={meeting_id} — Auto-chain extraction setup FAILED: {auto_ex_err}",
                            exc_info=True,
                        )
                else:
                    logger.info(
                        f"[PIPELINE_STEP_5_NO_SEGMENTS] meeting_id={meeting_id} — "
                        f"No interleaved segments ({len(interleaved) if interleaved else 0}), skipping auto-chain extraction"
                    )

            except Exception as e:
                logger.error(f"[PIPELINE_FATAL_ERROR] meeting_id={meeting_id} — Pipeline CRASHED: {e}", exc_info=True)
                try:
                    transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
                        session, meeting_id
                    )
                    if transcript:
                        transcript.transcription_status = "failed"
                        transcript.error_message = f"Unexpected error: {str(e)[:500]}"
                        await session.commit()
                except Exception:
                    pass

    # ── Whisper API ──────────────────────────────────────────────────────

    @staticmethod
    async def _call_whisper_api(
        audio_path: str,
        language: Optional[str] = None,
    ) -> Dict:
        """
        Call Groq Whisper API for transcription.
        Returns verbose_json with segment-level timestamps.
        """
        api_key = settings.groq_api_key
        if not api_key:
            logger.error(f"[WHISPER_ERROR] No Groq API key configured! Check GROQ_API or GROQ_API_KEY env var.")
            raise ValidationError("Groq API key is not configured.")

        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        filename = os.path.basename(audio_path)
        data = {
            "model": settings.GROQ_WHISPER_MODEL,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if language:
            data["language"] = language

        timeout = settings.AI_WHISPER_TIMEOUT_SECONDS

        # Retry once on timeout or rate limit
        last_error = None
        for attempt in range(2):
            try:
                logger.info(
                    f"[WHISPER_CALL] file={filename} size_bytes={len(audio_bytes)} "
                    f"model={settings.GROQ_WHISPER_MODEL} timeout={timeout}s attempt={attempt + 1}/2"
                )
                files = {
                    "file": (filename, audio_bytes),
                }
                async with httpx.AsyncClient(timeout=float(timeout)) as client:
                    response = await client.post(
                        GROQ_WHISPER_URL,
                        headers=headers,
                        files=files,
                        data=data,
                    )

                logger.info(f"[WHISPER_RESPONSE] file={filename} status_code={response.status_code}")

                if response.status_code == 429:
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"[WHISPER_RATE_LIMIT] file={filename} — Rate limit hit, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue

                if response.status_code != 200:
                    err_text = response.text[:500]
                    logger.error(f"[WHISPER_API_ERROR] file={filename} status={response.status_code} error={err_text}")
                    raise ValidationError(
                        f"Groq Whisper API error ({response.status_code}): {err_text}"
                    )

                result = response.json()
                segment_count = len(result.get('segments', []))
                logger.info(
                    f"[WHISPER_SUCCESS] file={filename} language={result.get('language', '?')} "
                    f"segments={segment_count} text_length={len(result.get('text', ''))}"
                )
                return result

            except httpx.TimeoutException as e:
                last_error = e
                if attempt < 1:
                    logger.warning(f"[WHISPER_TIMEOUT] file={filename} — Timed out after {timeout}s, retrying...")
                    continue
                logger.error(f"[WHISPER_TIMEOUT_FINAL] file={filename} — Timed out after {timeout}s on final attempt")
                raise ValidationError(
                    f"Groq Whisper API timed out after {timeout}s"
                ) from last_error

        raise ValidationError("Whisper API failed after retries")

    # ── Parsing & Interleaving ───────────────────────────────────────────

    @staticmethod
    def _parse_whisper_segments(
        whisper_response: Dict,
        speaker: str,
    ) -> List[Dict]:
        """
        Parse Whisper verbose_json response into standardized segments.
        """
        segments = []
        raw_segments = whisper_response.get("segments", [])

        for seg in raw_segments:
            text = seg.get("text", "").strip()
            if not text:
                continue

            segments.append({
                "speaker": speaker,
                "text": text,
                "start_time": float(seg.get("start", 0)),
                "end_time": float(seg.get("end", 0)),
                "language": whisper_response.get("language"),
                "confidence": 1.0 - float(seg.get("no_speech_prob", 0)),
            })

        return segments

    @staticmethod
    def _interleave_transcripts(
        doctor_segments: List[Dict],
        patient_segments: List[Dict],
    ) -> List[Dict]:
        """
        Merge doctor and patient transcript segments by start_time.
        """
        combined = doctor_segments + patient_segments
        combined.sort(key=lambda s: s.get("start_time", 0))
        return combined

    @staticmethod
    def _generate_full_text(segments: List[Dict]) -> str:
        """
        Generate a human-readable text version of the interleaved transcript.
        """
        lines = []
        for seg in segments:
            speaker_label = "[DOCTOR]" if seg.get("speaker") == "doctor" else "[PATIENT]"
            text = seg.get("text", "")
            start = seg.get("start_time", 0)
            minutes = int(start // 60)
            seconds = int(start % 60)
            timestamp = f"[{minutes:02d}:{seconds:02d}]"
            lines.append(f"{timestamp} {speaker_label} {text}")
        return "\n".join(lines)

    # ── Read Transcript ──────────────────────────────────────────────────

    @staticmethod
    async def get_transcript(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        user: Any,
    ) -> TranscriptResponse:
        """Fetch transcript for a meeting with authorization check."""
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id not in (meeting.doctor_id, meeting.patient_id):
            from app.models.enums import UserRole
            if user.role != UserRole.SAAS_ADMIN:
                raise AuthorizationError("You are not authorized to view this transcript")

        transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
            session, meeting_id
        )
        if not transcript:
            raise NotFoundError("No transcript found for this meeting")

        # Parse structured_transcript JSONB into TranscriptSegment list
        segments = []
        if transcript.structured_transcript:
            raw_segments = transcript.structured_transcript
            if isinstance(raw_segments, list):
                for seg in raw_segments:
                    try:
                        segments.append(TranscriptSegment(**seg))
                    except Exception:
                        pass

        return TranscriptResponse(
            meeting_id=meeting_id,
            transcription_status=transcript.transcription_status,
            transcription_model=transcript.transcription_model,
            segments=segments,
            full_text=transcript.full_text,
            processing_time_ms=transcript.processing_time_ms,
            created_at=transcript.created_at,
        )

    @staticmethod
    async def get_transcription_status(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> TranscriptionStatusResponse:
        """Get current transcription status without authorization (used internally)."""
        transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
            session, meeting_id
        )
        if not transcript:
            return TranscriptionStatusResponse(
                meeting_id=meeting_id,
                transcription_status="no_audio",
                has_doctor_audio=False,
                has_patient_audio=False,
            )

        return TranscriptionStatusResponse(
            meeting_id=meeting_id,
            transcription_status=transcript.transcription_status,
            has_doctor_audio=bool(transcript.doctor_audio_path),
            has_patient_audio=bool(transcript.patient_audio_path),
            error_message=transcript.error_message,
        )
