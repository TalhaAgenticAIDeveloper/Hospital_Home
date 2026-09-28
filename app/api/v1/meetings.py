"""
API endpoints for Doctor Availability, Patient Booking, 1-to-1 Meetings,
WebRTC Signaling, Bilingual Transcription delivery, and Gemini Live Transcription.
"""

import base64
import uuid
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_active_doctor,
    get_current_user,
    get_db,
)
from app.core.database import async_session_maker
from app.core.logging import get_logger
from app.core.security import decode_token
from app.models.enums import MeetingStatus, UserRole
from app.models.user import User
from app.repositories.meeting_repository import MeetingRepository
from app.schemas.auth import MessageResponse
from app.schemas.meeting import (
    AvailabilityBatchCreateRequest,
    AvailabilityResponse,
    AvailabilitySlotCreate,
    DoctorDirectoryItemResponse,
    GenerateWeekSlotsRequest,
    MeetingBookRequest,
    MeetingEndRequest,
    MeetingEndResponse,
    MeetingEndAndSaveTranscriptRequest,
    MeetingResponse,
    MeetingTranscriptResponse,
    SessionTranscriptSaveRequest,
    SessionTranscriptResponse,
    WeeklyScheduleResponse,
    WeeklyScheduleSaveRequest,
)
from app.services.meeting_service import MeetingService
from app.services.signaling_manager import signaling_manager

logger = get_logger(__name__)

router = APIRouter(prefix="/meetings", tags=["Consultations & Telemedicine Meetings"])

# ── Weekly Schedule Management Endpoints ─────────────────────────────────────

@router.put(
    "/weekly-schedule",
    response_model=List[WeeklyScheduleResponse],
    summary="Save or update weekly availability schedule",
    description="Doctor sets their recurring weekly availability template. Replaces any existing schedule.",
)
async def save_weekly_schedule(
    request: WeeklyScheduleSaveRequest,
    doctor_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> List[WeeklyScheduleResponse]:
    return await MeetingService.save_weekly_schedule(session, doctor_user, request)


@router.get(
    "/weekly-schedule",
    response_model=List[WeeklyScheduleResponse],
    summary="Get doctor's weekly schedule template",
)
async def get_weekly_schedule(
    doctor_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> List[WeeklyScheduleResponse]:
    return await MeetingService.get_weekly_schedule(session, doctor_user)


@router.post(
    "/weekly-schedule/generate",
    response_model=List[AvailabilityResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate bookable slots from weekly schedule",
    description="Bulk-generate actual bookable availability slots from the saved weekly template for the specified number of weeks ahead.",
)
async def generate_slots_from_schedule(
    request: GenerateWeekSlotsRequest,
    doctor_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> List[AvailabilityResponse]:
    return await MeetingService.generate_slots_from_schedule(session, doctor_user, request)


# ── Doctor Availability Management Endpoints ─────────────────────────────────

@router.post(
    "/availability/batch",
    response_model=List[AvailabilityResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate batch availability slots",
    description="Doctor specifies a date, start time, end time, and slot duration (e.g. 30 min) to generate free slots.",
)
async def create_availability_batch(
    request: AvailabilityBatchCreateRequest,
    doctor_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> List[AvailabilityResponse]:
    return await MeetingService.create_availability_batch(session, doctor_user, request)


@router.post(
    "/availability",
    response_model=AvailabilityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a single availability slot",
)
async def create_single_availability_slot(
    request: AvailabilitySlotCreate,
    doctor_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> AvailabilityResponse:
    return await MeetingService.create_single_slot(session, doctor_user, request)


@router.get(
    "/availability",
    response_model=List[AvailabilityResponse],
    summary="Get doctor's availability slots",
)
async def get_my_availability(
    future_only: bool = Query(True, description="Only show upcoming slots"),
    doctor_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> List[AvailabilityResponse]:
    return await MeetingService.list_doctor_availabilities(session, doctor_user, future_only=future_only)


@router.delete(
    "/availability/{slot_id}",
    response_model=MessageResponse,
    summary="Delete an unbooked availability slot",
)
async def delete_availability_slot(
    slot_id: uuid.UUID,
    doctor_user: User = Depends(get_current_active_doctor),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await MeetingService.delete_availability_slot(session, doctor_user, slot_id)
    return MessageResponse(message="Availability slot deleted successfully")


# ── Doctor Directory & Public Slots (Patient Access) ─────────────────────────

@router.get(
    "/doctors",
    response_model=List[DoctorDirectoryItemResponse],
    summary="List verified doctors for consultation booking",
    description="Returns verified active doctors with profile details and counts of open consultation slots.",
)
async def list_consultation_doctors(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> List[DoctorDirectoryItemResponse]:
    return await MeetingService.list_active_doctors_directory(session)


@router.get(
    "/doctors/{doctor_id}/slots",
    response_model=List[AvailabilityResponse],
    summary="Get open slots for a specific doctor",
)
async def get_doctor_open_slots(
    doctor_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> List[AvailabilityResponse]:
    return await MeetingService.list_doctor_available_slots(session, doctor_id)


# ── Meeting Booking & Management ─────────────────────────────────────────────

@router.post(
    "/book",
    response_model=MeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a consultation meeting with a doctor",
)
async def book_consultation(
    request: MeetingBookRequest,
    patient_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    return await MeetingService.book_meeting(session, patient_user, request)


@router.get(
    "/my-meetings",
    response_model=List[MeetingResponse],
    summary="List all consultations for the authenticated user",
)
async def get_my_meetings(
    status_filter: Optional[MeetingStatus] = Query(None, description="Filter by status (scheduled, in_progress, completed)"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> List[MeetingResponse]:
    return await MeetingService.list_user_meetings(session, user, status_filter=status_filter)


@router.get(
    "/{meeting_id_or_room}",
    response_model=MeetingResponse,
    summary="Get consultation meeting details by ID or room code",
)
async def get_meeting_details(
    meeting_id_or_room: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeetingResponse:
    return await MeetingService.get_meeting_by_id_or_room(session, user, meeting_id_or_room)


# ── Meeting Completion Endpoints ─────────────────────────────────────────────

@router.post(
    "/{meeting_id}/end",
    response_model=MeetingEndResponse,
    summary="End consultation meeting",
    description="Ends the 1-to-1 consultation, saves optional clinical doctor notes, and transitions meeting status to completed.",
)
async def end_meeting(
    meeting_id: uuid.UUID,
    request: MeetingEndRequest = MeetingEndRequest(),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeetingEndResponse:
    return await MeetingService.end_meeting(
        session=session,
        user=user,
        meeting_id=meeting_id,
        doctor_notes=request.doctor_notes,
    )


@router.post(
    "/{meeting_id}/end-and-save-transcript",
    response_model=MeetingTranscriptResponse,
    summary="End meeting (legacy alias)",
    description="Backwards-compatible endpoint for finalizing consultation.",
)
async def end_meeting_and_save_transcript(
    meeting_id: uuid.UUID,
    request: MeetingEndAndSaveTranscriptRequest = MeetingEndAndSaveTranscriptRequest(),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeetingTranscriptResponse:
    return await MeetingService.finalize_meeting_and_save_transcript(session, user, meeting_id, request)


@router.post(
    "/{meeting_id}/save-session-transcript",
    response_model=SessionTranscriptResponse,
    summary="Save a per-session transcript (one join/leave cycle)",
    description="Saves transcript for a single session without ending the meeting. Used when doctor leaves but meeting remains active for rejoin.",
)
async def save_session_transcript(
    meeting_id: uuid.UUID,
    request: SessionTranscriptSaveRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> SessionTranscriptResponse:
    return await MeetingService.save_session_transcript(session, user, meeting_id, request)


@router.get(
    "/{meeting_id}/transcript",
    response_model=MeetingTranscriptResponse,
    summary="View meeting transcript text",
)
async def get_meeting_transcript_text(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeetingTranscriptResponse:
    meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if user.id not in (meeting.doctor_id, meeting.patient_id) and user.role != UserRole.SAAS_ADMIN:
        raise HTTPException(status_code=403, detail="Unauthorized access to transcript")

    return MeetingTranscriptResponse(
        meeting_id=meeting.id,
        room_id=meeting.room_id,
        transcript_text=meeting.transcript_text,
        doctor_notes=meeting.doctor_notes,
        status=meeting.status,
        completed_at=meeting.updated_at,
    )


@router.get(
    "/{meeting_id}/transcript/download",
    summary="Download meeting transcript file (Doctor only)",
    description="Exclusively downloads the formatted consultation transcript text file for the consulting doctor.",
)
async def download_transcript_file(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    return await MeetingService.get_transcript_download(session, user, meeting_id)


# ── WebSocket Route: WebRTC Signaling & Live Transcript ──────────────────────

@router.websocket("/ws/{room_id}")
async def meeting_signaling_websocket(
    websocket: WebSocket,
    room_id: str,
    token: Optional[str] = Query(None),
):
    """
    WebSocket endpoint for 1-to-1 WebRTC signaling (Offer, Answer, ICE Candidates)
    and live real-time bilingual speech transcript relay.
    """
    # 1. Authenticate via JWT Query Param
    if not token:
        await websocket.close(code=4001, reason="Authentication token missing")
        return

    try:
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        token_type = payload.get("type")
        if not user_id_str or token_type != "access":
            await websocket.close(code=4001, reason="Invalid token")
            return
        user_id = uuid.UUID(user_id_str)
    except Exception:
        await websocket.close(code=4001, reason="Authentication token validation failed")
        return

    # 2. Authorize Meeting Room & Retrieve Participant Role
    async with async_session_maker() as session:
        meeting = await MeetingRepository.get_meeting_by_room_id(session, room_id)
        if not meeting:
            await websocket.close(code=4004, reason="Meeting room does not exist")
            return

        if user_id != meeting.doctor_id and user_id != meeting.patient_id:
            logger.warning(f"Unauthorized WebSocket access attempt by {user_id} on room {room_id}")
            await websocket.close(code=4003, reason="Unauthorized access to meeting room")
            return

        role = "doctor" if user_id == meeting.doctor_id else "patient"
        doctor_profile = meeting.doctor.doctor_profile if meeting.doctor else None
        if role == "doctor":
            name = f"Dr. {doctor_profile.full_name}" if doctor_profile and doctor_profile.full_name else "Doctor"
        else:
            name = meeting.patient.email

    # 3. Connect to Signaling Manager
    connected = await signaling_manager.connect(websocket, room_id, str(user_id), role, name)
    if not connected:
        return

    # 4. Message Loop
    try:
        while True:
            raw_text = await websocket.receive_text()
            await signaling_manager.handle_message(room_id, websocket, raw_text)
    except WebSocketDisconnect:
        signaling_manager.disconnect(websocket, room_id)
        await signaling_manager.broadcast(
            room_id,
            {"type": "peer-left", "user_id": str(user_id), "role": role},
        )
    except Exception as e:
        logger.warning(f"WebSocket error in room {room_id}: {e}")
        signaling_manager.disconnect(websocket, room_id)


# ── WebSocket Route: Gemini Live Transcription (Audio → Text) ────────────────

@router.websocket("/ws/transcribe/{room_id}")
async def gemini_live_transcription_websocket(
    websocket: WebSocket,
    room_id: str,
    token: Optional[str] = Query(None),
):
    """
    WebSocket endpoint for Gemini-powered live speech-to-text transcription.

    Each participant (doctor/patient) connects here alongside the signaling WS.
    The frontend streams raw PCM audio chunks (16-bit, 16kHz, mono) as base64-encoded
    messages. The backend forwards them to Google's Gemini 3.5 Transcribe Live model
    and broadcasts the transcribed text to both participants via the signaling WebSocket.

    Protocol:
        Client → Server (JSON):
            {"type": "audio", "data": "<base64-encoded-PCM-chunk>"}
            {"type": "stop"}   (optional graceful shutdown)

        Server → Client (JSON):
            {"type": "transcription-ready"}   (session connected to Gemini)
            {"type": "transcription-error", "message": "..."}
            {"type": "transcription-closed"}

    Transcribed text is broadcast via the signaling WS as `transcript-segment` messages
    (same format as Web Speech API used before), so the existing frontend rendering
    and backend persistence work unchanged.
    """
    import json
    from app.services.gemini_live_transcription_service import gemini_transcription_manager

    # 1. Authenticate via JWT Query Param
    if not token:
        await websocket.close(code=4001, reason="Authentication token missing")
        return

    try:
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        token_type = payload.get("type")
        if not user_id_str or token_type != "access":
            await websocket.close(code=4001, reason="Invalid token")
            return
        user_id = uuid.UUID(user_id_str)
    except Exception:
        await websocket.close(code=4001, reason="Authentication token validation failed")
        return

    # 2. Authorize Meeting Room & Retrieve Participant Role
    async with async_session_maker() as session:
        meeting = await MeetingRepository.get_meeting_by_room_id(session, room_id)
        if not meeting:
            await websocket.close(code=4004, reason="Meeting room does not exist")
            return

        if user_id != meeting.doctor_id and user_id != meeting.patient_id:
            logger.warning(f"Unauthorized transcription WS access by {user_id} on room {room_id}")
            await websocket.close(code=4003, reason="Unauthorized access to meeting room")
            return

        role = "doctor" if user_id == meeting.doctor_id else "patient"
        doctor_profile = meeting.doctor.doctor_profile if meeting.doctor else None
        if role == "doctor":
            speaker_name = f"Dr. {doctor_profile.full_name}" if doctor_profile and doctor_profile.full_name else "Doctor"
        else:
            patient_profile = meeting.patient.patient_profile if meeting.patient else None
            speaker_name = patient_profile.full_name if patient_profile and patient_profile.full_name else meeting.patient.email

    # 3. Accept WebSocket connection
    await websocket.accept()
    logger.info(
        f"[GEMINI_TRANSCRIPTION_WS] Connected: room={room_id} role={role} "
        f"user={user_id} speaker={speaker_name}"
    )

    # 4. Define callback: when Gemini transcribes text, broadcast it via signaling WS
    async def on_gemini_transcript(segment: dict):
        """Broadcast transcribed segment to both participants via signaling manager."""
        # Broadcast as transcript-segment (same format as Web Speech API)
        broadcast_data = {
            "type": "transcript-segment",
            **segment,
        }
        await signaling_manager.broadcast(room_id, broadcast_data)

        # Also persist to in-memory transcript buffer (same as before)
        signaling_manager._append_transcript_segment(room_id, segment)

    # 5. Create Gemini Live session
    gemini_session = await gemini_transcription_manager.create_session(
        room_id=room_id,
        user_id=str(user_id),
        role=role,
        speaker_name=speaker_name,
        on_transcript=on_gemini_transcript,
    )

    if not gemini_session:
        await websocket.send_text(json.dumps({
            "type": "transcription-error",
            "message": "Failed to connect to Gemini Live transcription. Check server logs.",
        }))
        await websocket.close(code=4500, reason="Gemini session initialization failed")
        return

    # Notify client that transcription is ready
    await websocket.send_text(json.dumps({"type": "transcription-ready"}))

    # 6. Audio Streaming Loop
    try:
        while True:
            raw_text = await websocket.receive_text()

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "audio":
                # Decode base64 PCM audio data and stream to Gemini
                audio_b64 = data.get("data")
                if audio_b64:
                    try:
                        audio_bytes = base64.b64decode(audio_b64)
                        await gemini_session.send_audio(audio_bytes)
                    except Exception as decode_err:
                        logger.debug(
                            f"[GEMINI_TRANSCRIPTION_WS] Audio decode error: {decode_err}"
                        )

            elif msg_type == "stop":
                logger.info(
                    f"[GEMINI_TRANSCRIPTION_WS] Client requested stop: "
                    f"room={room_id} role={role}"
                )
                break

    except WebSocketDisconnect:
        logger.info(
            f"[GEMINI_TRANSCRIPTION_WS] Client disconnected: "
            f"room={room_id} role={role}"
        )
    except Exception as e:
        logger.warning(
            f"[GEMINI_TRANSCRIPTION_WS] Error in audio loop: "
            f"room={room_id} role={role} error={e}"
        )
    finally:
        # Cleanup Gemini session
        await gemini_transcription_manager.close_session(room_id, str(user_id))
        logger.info(
            f"[GEMINI_TRANSCRIPTION_WS] Session cleaned up: "
            f"room={room_id} role={role}"
        )

