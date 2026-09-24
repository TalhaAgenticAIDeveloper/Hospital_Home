"""
Live Real-Time Audio Transcription Manager using Groq Whisper.

Architecture:
- Manages real-time 3-5s audio chunk queues per participant (doctor queue, patient queue).
- Enforces strict participant identity from authenticated WebSocket session.
- Runs asynchronous Whisper API calls per participant to prevent concurrency storms.
- Supports auto-detection of English, Urdu, Roman Urdu, and mixed speech without translating.
- Broadcasts transcription_segment messages back to all meeting room participants via WebSocket.
- Continuously accumulates live segments in memory and commits to PostgreSQL DB
  (ConsultationTranscript and Meeting.transcript_text) so the final transcript is
  immediately available when the call ends.
"""

import asyncio
import base64
from datetime import datetime, timezone
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.logging import get_logger
from app.models.consultation_transcript import ConsultationTranscript
from app.repositories.consultation_ai_repository import ConsultationAIRepository
from app.repositories.meeting_repository import MeetingRepository

logger = get_logger(__name__)
settings = get_settings()

GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Common repetitive Whisper hallucinations on near-silent chunks to suppress
HALLUCINATED_PHRASES = {
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "subscribe to my channel",
    "see you next time",
    "bye bye",
    "subtitles by",
    "subtitles created by",
    "translated by",
    "all rights reserved",
    "copyright",
}


class ParticipantQueueItem:
    """Represents a single audio chunk from a verified participant."""
    def __init__(
        self,
        meeting_id: uuid.UUID,
        room_id: str,
        participant: str,
        speaker_name: str,
        sequence: int,
        timestamp: float,
        audio_bytes: bytes,
        mime_type: str = "audio/wav",
    ):
        self.meeting_id = meeting_id
        self.room_id = room_id
        self.participant = participant  # "doctor" or "patient"
        self.speaker_name = speaker_name
        self.sequence = sequence
        self.timestamp = timestamp
        self.audio_bytes = audio_bytes
        self.mime_type = mime_type
        self.enqueued_at = time.time()


class RoomTranscriptionState:
    """Holds active queues, workers, and accumulated segments for a room."""
    def __init__(self, room_id: str, meeting_id: uuid.UUID):
        self.room_id = room_id
        self.meeting_id = meeting_id
        self.doctor_queue: asyncio.Queue[ParticipantQueueItem] = asyncio.Queue(maxsize=30)
        self.patient_queue: asyncio.Queue[ParticipantQueueItem] = asyncio.Queue(maxsize=30)
        self.doctor_worker_task: Optional[asyncio.Task] = None
        self.patient_worker_task: Optional[asyncio.Task] = None
        self.segments: List[Dict[str, Any]] = []
        self.processed_sequences: Set[Tuple[str, int]] = set()  # (participant, sequence)
        self.is_active = True
        self.last_db_save_time = 0.0
        self.pending_db_save = False


class LiveTranscriptionManager:
    """
    Singleton manager coordinating live audio chunk queues and Groq Whisper transcription.
    """

    def __init__(self):
        # room_id -> RoomTranscriptionState
        self._rooms: Dict[str, RoomTranscriptionState] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_room_state(
        self, room_id: str, meeting_id: uuid.UUID
    ) -> RoomTranscriptionState:
        """Retrieve or initialize transcription queues and workers for a room."""
        async with self._lock:
            if room_id not in self._rooms:
                state = RoomTranscriptionState(room_id, meeting_id)
                self._rooms[room_id] = state

                # Start dedicated background worker per participant
                state.doctor_worker_task = asyncio.create_task(
                    self._participant_worker(state, "doctor")
                )
                state.patient_worker_task = asyncio.create_task(
                    self._participant_worker(state, "patient")
                )
                logger.info(
                    f"[LIVE_TRANSCRIPTION_INIT] Room {room_id} (meeting {meeting_id}) workers started"
                )
            return self._rooms[room_id]

    async def enqueue_chunk(
        self,
        room_id: str,
        connection: Any,  # MeetingConnection from signaling_manager
        data: Dict[str, Any],
    ) -> bool:
        """
        Validate and enqueue an incoming audio chunk.
        Enforces participant role from connection (doctor vs patient).
        """
        try:
            # 1. Server-verified participant role & speaker name
            role = connection.role
            speaker_name = connection.name or ("Doctor" if role == "doctor" else "Patient")

            meeting_id_raw = data.get("meeting_id")
            if not meeting_id_raw:
                # Fallback: look up meeting from room_id
                async with async_session_maker() as session:
                    meeting = await MeetingRepository.get_meeting_by_room_id(session, room_id)
                    if not meeting:
                        logger.warning(f"[AUDIO_CHUNK_DROP] Unknown room {room_id}")
                        return False
                    meeting_id = meeting.id
            else:
                meeting_id = uuid.UUID(str(meeting_id_raw))

            sequence = int(data.get("sequence", 0))
            timestamp = float(data.get("timestamp", 0.0))
            mime_type = data.get("mime_type", "audio/wav")
            audio_base64 = data.get("audio_base64", "")

            if not audio_base64:
                logger.warning(f"[AUDIO_CHUNK_DROP] Empty audio_base64 in room {room_id}")
                return False

            audio_bytes = base64.b64decode(audio_base64)
            if len(audio_bytes) < 100:
                logger.debug(f"[AUDIO_CHUNK_DROP] Audio chunk too small ({len(audio_bytes)}B)")
                return False

            # Max chunk size guard: 3MB
            if len(audio_bytes) > 3 * 1024 * 1024:
                logger.warning(f"[AUDIO_CHUNK_DROP] Audio chunk exceeds 3MB limit ({len(audio_bytes)}B)")
                return False

            state = await self.get_or_create_room_state(room_id, meeting_id)

            # Prevent duplicate enqueuing of the exact same participant + sequence
            seq_key = (role, sequence)
            if seq_key in state.processed_sequences:
                logger.debug(f"[AUDIO_CHUNK_DUP] Chunk already processed: {seq_key}")
                return False

            state.processed_sequences.add(seq_key)

            item = ParticipantQueueItem(
                meeting_id=meeting_id,
                room_id=room_id,
                participant=role,
                speaker_name=speaker_name,
                sequence=sequence,
                timestamp=timestamp,
                audio_bytes=audio_bytes,
                mime_type=mime_type,
            )

            target_queue = state.doctor_queue if role == "doctor" else state.patient_queue

            try:
                target_queue.put_nowait(item)
                logger.info(
                    f"[AUDIO_CHUNK_ENQUEUED] room={room_id} participant={role} "
                    f"seq={sequence} size={len(audio_bytes)} queue_size={target_queue.qsize()}"
                )
                return True
            except asyncio.QueueFull:
                logger.warning(
                    f"[AUDIO_CHUNK_QUEUE_FULL] {role} queue full in room {room_id}. Dropping chunk {sequence}"
                )
                return False

        except Exception as e:
            logger.error(f"[AUDIO_CHUNK_ERROR] Failed to enqueue audio chunk: {e}", exc_info=True)
            return False

    async def _participant_worker(self, state: RoomTranscriptionState, role: str):
        """
        Background worker processing audio chunks sequentially for a participant.
        Ensures in-order processing and prevents concurrent Whisper API storms.
        """
        queue = state.doctor_queue if role == "doctor" else state.patient_queue
        logger.info(f"[WHISPER_WORKER_START] Worker started for {role} in room {state.room_id}")

        while state.is_active:
            try:
                # Wait for next chunk with a timeout so worker can check is_active
                try:
                    item: ParticipantQueueItem = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue

                await self._process_chunk(state, item)
                queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[WHISPER_WORKER_ERROR] Worker exception for {role}: {e}", exc_info=True)
                await asyncio.sleep(0.5)

        logger.info(f"[WHISPER_WORKER_STOP] Worker stopped for {role} in room {state.room_id}")

    async def _process_chunk(self, state: RoomTranscriptionState, item: ParticipantQueueItem):
        """Execute Groq Whisper transcription on a single chunk and broadcast result."""
        room_id = state.room_id
        meeting_id = item.meeting_id
        role = item.participant
        seq = item.sequence
        size = len(item.audio_bytes)

        logger.info(
            f"[WHISPER] meeting_id={meeting_id} participant={role} chunk={seq} "
            f"size={size} status=processing"
        )

        start_time = time.time()
        text = await self._call_whisper_api_chunk(
            audio_bytes=item.audio_bytes,
            mime_type=item.mime_type,
        )
        elapsed = round(time.time() - start_time, 2)

        # Clean and validate transcribed text
        cleaned_text = self._clean_transcription(text)

        if not cleaned_text:
            logger.info(
                f"[WHISPER] meeting_id={meeting_id} participant={role} chunk={seq} "
                f"status=no_speech elapsed={elapsed}s"
            )
            return

        logger.info(
            f"[WHISPER] meeting_id={meeting_id} participant={role} chunk={seq} "
            f"text=\"{cleaned_text}\" status=success elapsed={elapsed}s"
        )

        # Build standardized transcript segment
        segment_id = f"{room_id}-{role}-{seq}-{int(item.timestamp * 10)}"
        segment = {
            "id": segment_id,
            "meeting_id": str(meeting_id),
            "participant": role,
            "speaker": role,
            "speakerName": item.speaker_name,
            "sequence": seq,
            "timestamp": item.timestamp,
            "start_time": item.timestamp,
            "end_time": round(item.timestamp + 3.5, 2),
            "text": cleaned_text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Store in state segments buffer
        state.segments.append(segment)
        state.segments.sort(key=lambda s: s.get("timestamp", 0.0))

        # Broadcast immediately to ALL participants in the room via WebSocket
        from app.services.signaling_manager import signaling_manager
        await signaling_manager.broadcast(
            room_id,
            {
                "type": "transcription_segment",
                "segment": segment,
                "meeting_id": str(meeting_id),
                "participant": role,
                "speaker": role,
                "speaker_name": item.speaker_name,
                "sequence": seq,
                "timestamp": item.timestamp,
                "text": cleaned_text,
            },
        )

        # Trigger asynchronous DB persistence (debounced to avoid overloading DB)
        await self._schedule_db_save(state)

    def _clean_transcription(self, text: Optional[str]) -> Optional[str]:
        """Strip punctuation hallucinations, empty spaces, or subtitle credits."""
        if not text:
            return None
        cleaned = text.strip()
        # Remove standalone dots, commas, or dashes
        if cleaned in (".", ",", "-", "--", "...", "?", "!"):
            return None

        # Check against known Whisper near-silent hallucinations
        lower = cleaned.lower().strip()
        for phrase in HALLUCINATED_PHRASES:
            if lower == phrase or lower.startswith(phrase + ".") or lower.startswith(phrase + "!"):
                return None

        # Repetitive hallucination check (e.g. "you you you you")
        words = lower.split()
        if len(words) >= 4 and len(set(words)) == 1:
            return None

        return cleaned

    async def _call_whisper_api_chunk(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/wav",
    ) -> Optional[str]:
        """
        Call Groq Whisper API for a small audio chunk.
        Uses in-memory audio bytes directly without disk I/O.
        Does NOT force language="en" so Urdu, Roman Urdu, and English are auto-detected.
        """
        api_key = settings.groq_api_key
        if not api_key:
            logger.error("[WHISPER_ERROR] Groq API key is missing. Check GROQ_API or GROQ_API_KEY.")
            return None

        ext = "wav" if "wav" in mime_type else "webm"
        filename = f"chunk.{ext}"

        files = {
            "file": (filename, audio_bytes, mime_type),
        }

        # Prompt hint primes Whisper for English, Urdu, and Roman Urdu medical conversation
        prompt_hint = (
            "Medical consultation between doctor and patient. Spoken in English, Urdu, "
            "and Roman Urdu. Transcribe medical symptoms, duration, questions, and responses accurately."
        )

        data = {
            "model": settings.GROQ_WHISPER_MODEL,
            "response_format": "json",
            "temperature": "0",
            "prompt": prompt_hint,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        timeout = float(settings.AI_WHISPER_TIMEOUT_SECONDS or 30.0)

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        GROQ_WHISPER_URL,
                        headers=headers,
                        files=files,
                        data=data,
                    )

                if response.status_code == 429:
                    wait_time = 1.5 * (attempt + 1)
                    logger.warning(f"[WHISPER_RATE_LIMIT] 429 hit, retrying in {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue

                if response.status_code != 200:
                    logger.error(
                        f"[WHISPER_API_ERROR] status={response.status_code} "
                        f"body={response.text[:300]}"
                    )
                    return None

                result = response.json()
                return result.get("text", "")

            except httpx.TimeoutException:
                logger.warning(f"[WHISPER_TIMEOUT] Attempt {attempt + 1} timed out after {timeout}s")
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                return None
            except Exception as e:
                logger.error(f"[WHISPER_EXCEPTION] Error calling Whisper: {e}")
                return None

        return None

    async def _schedule_db_save(self, state: RoomTranscriptionState):
        """Debounced asynchronous DB persistence."""
        now = time.time()
        # Save at most every 4 seconds during active call to prevent DB write contention
        if now - state.last_db_save_time > 4.0:
            state.last_db_save_time = now
            asyncio.create_task(self._persist_state_to_db(state))

    async def _persist_state_to_db(self, state: RoomTranscriptionState):
        """Write current accumulated transcript to ConsultationTranscript & Meeting."""
        try:
            segments_copy = list(state.segments)
            if not segments_copy:
                return

            full_text = self._build_full_text(segments_copy)

            async with async_session_maker() as session:
                transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
                    session, state.meeting_id
                )
                if not transcript:
                    transcript = ConsultationTranscript(
                        meeting_id=state.meeting_id,
                        transcription_status="in_progress",
                        transcription_model=settings.GROQ_WHISPER_MODEL,
                        structured_transcript=segments_copy,
                        full_text=full_text,
                    )
                    await ConsultationAIRepository.create_transcript(session, transcript)
                else:
                    transcript.structured_transcript = segments_copy
                    transcript.full_text = full_text
                    transcript.transcription_model = settings.GROQ_WHISPER_MODEL
                    transcript.transcription_status = "in_progress"

                # Update meeting transcript_text
                meeting = await MeetingRepository.get_meeting_by_id(session, state.meeting_id)
                if meeting:
                    meeting.transcript_text = full_text

                await session.commit()
                logger.debug(
                    f"[LIVE_TRANSCRIPT_PERSISTED] meeting_id={state.meeting_id} "
                    f"segments={len(segments_copy)}"
                )

        except Exception as e:
            logger.warning(f"[LIVE_TRANSCRIPT_PERSIST_WARN] meeting_id={state.meeting_id}: {e}")

    def _build_full_text(self, segments: List[Dict[str, Any]]) -> str:
        """Format segments into human-readable chronological text."""
        lines = []
        for s in segments:
            role = s.get("participant", s.get("speaker", "participant")).upper()
            name = s.get("speakerName") or f"[{role}]"
            ts = float(s.get("timestamp", 0.0))
            minutes = int(ts // 60)
            seconds = int(ts % 60)
            time_str = f"[{minutes:02d}:{seconds:02d}]"
            text = s.get("text", "").strip()
            if text:
                lines.append(f"{time_str} {name}: {text}")
        return "\n".join(lines)

    async def flush_and_close(self, room_id: str):
        """
        Flush all remaining audio chunks, stop worker tasks,
        mark transcript as 'completed', and remove room state.
        Ensures the final transcript is immediately ready when meeting ends.
        """
        async with self._lock:
            state = self._rooms.pop(room_id, None)

        if not state:
            return

        logger.info(f"[LIVE_TRANSCRIPTION_FLUSH] Flushing room {room_id} (meeting {state.meeting_id})")

        # Mark inactive so workers exit after processing current items
        state.is_active = False

        # Wait briefly for in-flight items
        for q in (state.doctor_queue, state.patient_queue):
            try:
                # Give workers up to 3 seconds to process any remaining item
                while not q.empty():
                    await asyncio.sleep(0.2)
            except Exception:
                pass

        # Cancel worker tasks
        for task in (state.doctor_worker_task, state.patient_worker_task):
            if task and not task.done():
                task.cancel()

        # Final DB commit marking transcript as completed
        try:
            segments_copy = list(state.segments)
            full_text = self._build_full_text(segments_copy)

            async with async_session_maker() as session:
                transcript = await ConsultationAIRepository.get_transcript_by_meeting_id(
                    session, state.meeting_id
                )
                if not transcript:
                    transcript = ConsultationTranscript(
                        meeting_id=state.meeting_id,
                        transcription_status="completed",
                        transcription_model=settings.GROQ_WHISPER_MODEL,
                        structured_transcript=segments_copy,
                        full_text=full_text,
                    )
                    await ConsultationAIRepository.create_transcript(session, transcript)
                else:
                    transcript.structured_transcript = segments_copy
                    transcript.full_text = full_text
                    transcript.transcription_status = "completed"
                    transcript.transcription_model = settings.GROQ_WHISPER_MODEL

                meeting = await MeetingRepository.get_meeting_by_id(session, state.meeting_id)
                if meeting:
                    meeting.transcript_text = full_text

                await session.commit()
                logger.info(
                    f"[LIVE_TRANSCRIPTION_FINALIZED] meeting_id={state.meeting_id} "
                    f"total_segments={len(segments_copy)} status=completed"
                )

        except Exception as e:
            logger.error(f"[LIVE_TRANSCRIPTION_FINAL_ERROR] meeting_id={state.meeting_id}: {e}", exc_info=True)


# Global singleton instance
live_transcription_manager = LiveTranscriptionManager()
