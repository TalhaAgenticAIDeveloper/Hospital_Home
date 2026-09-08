"""
Business logic service for Doctor Availability, Booking, Meetings, and Transcript handling.
"""

import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.doctor_availability import DoctorAvailability
from app.models.doctor_profile import DoctorProfile
from app.models.enums import MeetingStatus, UserRole, UserStatus
from app.models.meeting import Meeting
from app.models.meeting_document import MeetingDocument
from app.models.user import User
from app.models.weekly_schedule import DoctorWeeklySchedule
from app.repositories.meeting_repository import MeetingRepository
from app.repositories.patient_document_repository import PatientDocumentRepository
from app.schemas.meeting import (
    AvailabilityBatchCreateRequest,
    AvailabilityResponse,
    AvailabilitySlotCreate,
    DoctorDirectoryItemResponse,
    GenerateWeekSlotsRequest,
    MeetingBookRequest,
    MeetingEndAndSaveTranscriptRequest,
    MeetingResponse,
    MeetingTranscriptResponse,
    WeeklyScheduleResponse,
    WeeklyScheduleSaveRequest,
)
from app.schemas.patient_document import MeetingDocumentResponse
from app.services.signaling_manager import signaling_manager

logger = get_logger(__name__)
settings = get_settings()

TRANSCRIPT_DIR = os.path.join("uploads", "meeting_transcripts")


class MeetingService:
    """Service layer for doctor schedules, booking consultations, and transcript delivery."""

    @staticmethod
    def _ensure_transcript_dir():
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

    # ── Doctor Availability Management ───────────────────────────────────

    @staticmethod
    async def create_availability_batch(
        session: AsyncSession,
        doctor_user: User,
        request: AvailabilityBatchCreateRequest,
    ) -> List[AvailabilityResponse]:
        """Generate and save availability slots for a date window."""
        if doctor_user.role != UserRole.DOCTOR:
            raise AuthorizationError("Only doctors can set availability")
        if doctor_user.status != UserStatus.ACTIVE:
            raise AuthorizationError("Only verified active doctors can set availability")

        if request.start_datetime and request.end_datetime:
            window_start = request.start_datetime.astimezone(timezone.utc)
            window_end = request.end_datetime.astimezone(timezone.utc)
        else:
            naive_start = datetime.combine(request.slot_date, request.start_time)
            naive_end = datetime.combine(request.slot_date, request.end_time)
            offset = timedelta(minutes=request.timezone_offset_minutes or 0)
            window_start = (naive_start + offset).replace(tzinfo=timezone.utc)
            window_end = (naive_end + offset).replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # Allow slots starting within the current minute (truncate seconds for comparison)
        now_truncated = now.replace(second=0, microsecond=0)
        if window_start < now_truncated:
            raise ValidationError("Availability window must be in the future")

        delta = timedelta(minutes=request.slot_duration_minutes)
        slots_to_create: List[DoctorAvailability] = []

        current_start = window_start
        while current_start + delta <= window_end:
            current_end = current_start + delta

            # Check if an overlapping slot already exists for this doctor
            has_conflict = await MeetingRepository.check_slot_conflict(
                session, doctor_user.id, current_start, current_end
            )
            if not has_conflict:
                slots_to_create.append(
                    DoctorAvailability(
                        id=uuid.uuid4(),
                        doctor_id=doctor_user.id,
                        start_time=current_start,
                        end_time=current_end,
                        is_booked=False,
                    )
                )
            current_start = current_end

        if not slots_to_create:
            raise ConflictError("All requested slots in this window already exist or conflict with existing slots")

        created = await MeetingRepository.create_availability_slots(session, slots_to_create)
        await session.commit()
        return [AvailabilityResponse.model_validate(slot) for slot in created]

    @staticmethod
    async def create_single_slot(
        session: AsyncSession,
        doctor_user: User,
        slot_data: AvailabilitySlotCreate,
    ) -> AvailabilityResponse:
        """Create a single availability slot."""
        if doctor_user.role != UserRole.DOCTOR or doctor_user.status != UserStatus.ACTIVE:
            raise AuthorizationError("Only active doctors can create availability")

        now = datetime.now(timezone.utc)
        start_utc = slot_data.start_time.astimezone(timezone.utc)
        end_utc = slot_data.end_time.astimezone(timezone.utc)

        if start_utc <= now:
            raise ValidationError("Availability slot must be in the future")

        has_conflict = await MeetingRepository.check_slot_conflict(
            session, doctor_user.id, start_utc, end_utc
        )
        if has_conflict:
            raise ConflictError("This slot overlaps with an existing availability slot")

        slot = DoctorAvailability(
            id=uuid.uuid4(),
            doctor_id=doctor_user.id,
            start_time=start_utc,
            end_time=end_utc,
            is_booked=False,
        )
        await MeetingRepository.create_availability_slots(session, [slot])
        await session.commit()
        return AvailabilityResponse.model_validate(slot)

    @staticmethod
    async def list_doctor_availabilities(
        session: AsyncSession,
        doctor_user: User,
        future_only: bool = True,
    ) -> List[AvailabilityResponse]:
        """List current doctor's availability slots."""
        slots = await MeetingRepository.list_doctor_availabilities(
            session, doctor_user.id, future_only=future_only
        )
        return [AvailabilityResponse.model_validate(s) for s in slots]

    @staticmethod
    async def delete_availability_slot(
        session: AsyncSession,
        doctor_user: User,
        slot_id: uuid.UUID,
    ) -> bool:
        """Delete an unbooked availability slot."""
        success = await MeetingRepository.delete_availability(session, slot_id, doctor_user.id)
        if not success:
            raise NotFoundError("Availability slot not found or cannot be deleted (already booked)")
        await session.commit()
        return True

    # ── Doctor Directory & Public Slots for Patients ─────────────────────

    @staticmethod
    async def list_active_doctors_directory(
        session: AsyncSession,
    ) -> List[DoctorDirectoryItemResponse]:
        """List all verified active doctors with profile details and free slot counts."""
        rows = await MeetingRepository.list_active_doctors(session)
        result = []
        for user, profile, count in rows:
            result.append(
                DoctorDirectoryItemResponse(
                    doctor_id=user.id,
                    email=user.email,
                    full_name=profile.full_name if profile else None,
                    specialization=profile.specialization if profile else None,
                    qualification=profile.qualification if profile else None,
                    years_of_experience=profile.years_of_experience if profile else None,
                    bio=profile.bio if profile else None,
                    available_slots_count=count,
                )
            )
        return result

    @staticmethod
    async def list_doctor_available_slots(
        session: AsyncSession,
        doctor_id: uuid.UUID,
    ) -> List[AvailabilityResponse]:
        """List open future slots for patient booking."""
        slots = await MeetingRepository.list_doctor_availabilities(
            session, doctor_id, future_only=True, unbooked_only=True
        )
        return [AvailabilityResponse.model_validate(s) for s in slots]

    # ── Meeting Booking ──────────────────────────────────────────────────

    @staticmethod
    async def book_meeting(
        session: AsyncSession,
        patient_user: User,
        request: MeetingBookRequest,
    ) -> MeetingResponse:
        """Book a meeting slot with a doctor."""
        if patient_user.role != UserRole.PATIENT:
            raise AuthorizationError("Only patients can book doctor meetings")

        if patient_user.id == request.doctor_id:
            raise ValidationError("You cannot book a meeting with yourself")

        # Validate mandatory reason
        if not request.patient_notes or not request.patient_notes.strip():
            raise ValidationError("Reason for consultation is required")

        start_time: datetime
        end_time: datetime
        slot: Optional[DoctorAvailability] = None

        if request.availability_id:
            slot = await MeetingRepository.get_availability_by_id(
                session, request.availability_id, for_update=True
            )
            if not slot or slot.doctor_id != request.doctor_id:
                raise NotFoundError("Selected availability slot not found for this doctor")
            if slot.is_booked:
                raise ConflictError("This availability slot has already been booked by another patient")

            slot.is_booked = True
            start_time = slot.start_time
            end_time = slot.end_time
        else:
            now = datetime.now(timezone.utc)
            start_time = request.start_time.astimezone(timezone.utc)
            end_time = request.end_time.astimezone(timezone.utc)
            if start_time <= now:
                raise ValidationError("Meeting start time must be in the future")

        room_id = f"telemed_{uuid.uuid4().hex[:16]}"

        meeting = Meeting(
            id=uuid.uuid4(),
            room_id=room_id,
            doctor_id=request.doctor_id,
            patient_id=patient_user.id,
            availability_id=slot.id if slot else None,
            start_time=start_time,
            end_time=end_time,
            status=MeetingStatus.SCHEDULED,
            patient_notes=request.patient_notes.strip(),
        )

        await MeetingRepository.create_meeting(session, meeting)

        # Attach patient documents if any were selected
        if request.document_ids:
            # Validate all document IDs belong to the patient
            docs = await PatientDocumentRepository.get_documents_by_ids(
                session, request.document_ids, patient_user.id
            )
            if len(docs) != len(request.document_ids):
                raise ValidationError(
                    "One or more selected documents were not found or don't belong to you"
                )

            meeting_docs = [
                MeetingDocument(
                    id=uuid.uuid4(),
                    meeting=meeting,
                    meeting_id=meeting.id,
                    patient_document_id=doc.id,
                    patient_document=doc,
                )
                for doc in docs
            ]
            await MeetingRepository.create_meeting_documents(session, meeting_docs)
            meeting.attached_documents = meeting_docs

        await session.commit()

        # Re-fetch with fresh relationships
        meeting_id = meeting.id
        loaded_meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if loaded_meeting:
            if not loaded_meeting.attached_documents and request.document_ids:
                loaded_meeting.attached_documents = meeting.attached_documents
            return MeetingService._format_meeting_response(loaded_meeting)

        return MeetingService._format_meeting_response(meeting)

    # ── Meetings Listing & Retrieval ─────────────────────────────────────

    @staticmethod
    async def list_user_meetings(
        session: AsyncSession,
        user: User,
        status_filter: Optional[MeetingStatus] = None,
    ) -> List[MeetingResponse]:
        """List meetings for current doctor or patient."""
        meetings = await MeetingRepository.list_meetings_for_user(
            session, user.id, user.role, status_filter
        )
        return [MeetingService._format_meeting_response(m) for m in meetings]

    @staticmethod
    async def get_meeting_by_id_or_room(
        session: AsyncSession,
        user: User,
        meeting_id_or_room: str,
    ) -> MeetingResponse:
        """Fetch meeting details with participant authorization check."""
        meeting: Optional[Meeting] = None
        try:
            m_uuid = uuid.UUID(meeting_id_or_room)
            meeting = await MeetingRepository.get_meeting_by_id(session, m_uuid)
        except ValueError:
            meeting = await MeetingRepository.get_meeting_by_room_id(session, meeting_id_or_room)

        if not meeting:
            raise NotFoundError("Meeting consultation not found")

        if user.id not in (meeting.doctor_id, meeting.patient_id) and user.role != UserRole.SAAS_ADMIN:
            raise AuthorizationError("You are not authorized to access this consultation")

        return MeetingService._format_meeting_response(meeting)

    # ── Meeting Completion & Transcript Saving (Bilingual English/Urdu) ──

    @staticmethod
    async def finalize_meeting_and_save_transcript(
        session: AsyncSession,
        user: User,
        meeting_id: uuid.UUID,
        request: MeetingEndAndSaveTranscriptRequest,
    ) -> MeetingTranscriptResponse:
        """
        Compile bilingual (English/Urdu) speech transcript, save formatted UTF-8
        file to disk for the doctor, and update meeting record.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id not in (meeting.doctor_id, meeting.patient_id) and user.role != UserRole.SAAS_ADMIN:
            raise AuthorizationError("Only meeting participants can finalize the meeting")

        # Aggregate transcript segments from request or in-memory WebSocket manager
        segments = request.segments or []
        if not segments:
            in_memory_segments = signaling_manager.get_transcript_segments(meeting.room_id)
            segments = [
                {
                    "speaker": s.get("speaker", "participant"),
                    "speaker_name": s.get("speaker_name", "Participant"),
                    "text": s.get("text", ""),
                    "timestamp": s.get("timestamp", ""),
                    "language": s.get("language", "en-US"),
                }
                for s in in_memory_segments
            ]

        doctor_profile = meeting.doctor.doctor_profile if meeting.doctor else None
        doctor_name = doctor_profile.full_name if doctor_profile and doctor_profile.full_name else meeting.doctor.email
        specialization = doctor_profile.specialization if doctor_profile and doctor_profile.specialization else "General Physician"
        patient_name = meeting.patient.email

        # Build clean formatted consultation transcript document
        MeetingService._ensure_transcript_dir()
        file_name = f"{meeting.id}_transcript.txt"
        file_path = os.path.join(TRANSCRIPT_DIR, file_name)

        now_utc = datetime.now(timezone.utc)
        header_lines = [
            "=" * 80,
            "MEDTRUST HEALTHCARE — TELEMEDICINE CONSULTATION TRANSCRIPT",
            "=" * 80,
            f"Meeting ID      : {meeting.id}",
            f"Room Code       : {meeting.room_id}",
            f"Date & Time     : {meeting.start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Doctor          : Dr. {doctor_name} ({meeting.doctor.email})",
            f"Specialization  : {specialization}",
            f"Patient         : {patient_name}",
            f"Languages       : English / Urdu (اردو)",
            f"Recorded At     : {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "=" * 80,
            "",
            "[PATIENT CHIEF COMPLAINT / REASON FOR VISIT]:",
            f"{meeting.patient_notes or 'None specified'}",
            "",
            "[DOCTOR CLINICAL NOTES & SUMMARY]:",
            f"{request.doctor_notes or meeting.doctor_notes or 'No clinical notes added'}",
            "",
            "=" * 80,
            "DIALOGUE TRANSCRIPT (ENGLISH & URDU):",
            "=" * 80,
        ]

        body_lines = []
        if segments:
            for seg in segments:
                if isinstance(seg, dict):
                    speaker = seg.get("speaker_name") or seg.get("speaker") or "Participant"
                    ts = seg.get("timestamp", "")
                    text = seg.get("text", "").strip()
                    lang = seg.get("language", "")
                else:
                    speaker = getattr(seg, "speaker_name", None) or getattr(seg, "speaker", None) or "Participant"
                    ts = getattr(seg, "timestamp", "")
                    text = (getattr(seg, "text", "") or "").strip()
                    lang = getattr(seg, "language", "")
                lang_tag = f" [{lang}]" if lang else ""
                body_lines.append(f"[{ts}] {speaker}{lang_tag}: {text}")
        else:
            body_lines.append("(No spoken conversation was detected or transcribed during this call)")

        footer_lines = [
            "",
            "=" * 80,
            "END OF CONSULTATION TRANSCRIPT — CONFIDENTIAL MEDICAL RECORD",
            "=" * 80,
        ]

        full_transcript_text = "\n".join(header_lines + body_lines + footer_lines)

        # Write UTF-8 file to disk
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_transcript_text)

        # Persist to DB
        updated = await MeetingRepository.update_meeting_status_and_transcript(
            session=session,
            meeting=meeting,
            status=MeetingStatus.COMPLETED,
            transcript_text=full_transcript_text,
            transcript_path=file_path,
            doctor_notes=request.doctor_notes,
        )
        await session.commit()

        # Clear in-memory buffer
        signaling_manager.clear_transcript_buffer(meeting.room_id)

        return MeetingTranscriptResponse(
            meeting_id=updated.id,
            room_id=updated.room_id,
            transcript_text=updated.transcript_text,
            doctor_notes=updated.doctor_notes,
            status=updated.status,
            completed_at=updated.updated_at,
        )

    # ── Session-Based Transcript Saving (Per Join/Leave Cycle) ───────────

    @staticmethod
    async def save_session_transcript(
        session: AsyncSession,
        user: User,
        meeting_id: uuid.UUID,
        request,
    ):
        """
        Save a transcript for a single session (one join/leave cycle).
        Does NOT change meeting status — meeting remains active for rejoin.
        Each session generates a separate file: {meeting_id}_session_{n}_transcript.txt
        """
        from app.schemas.meeting import SessionTranscriptResponse

        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if user.id not in (meeting.doctor_id, meeting.patient_id) and user.role != UserRole.SAAS_ADMIN:
            raise AuthorizationError("Only meeting participants can save session transcripts")

        segments = request.segments or []
        if not segments:
            in_memory_segments = signaling_manager.get_transcript_segments(meeting.room_id)
            segments = [
                {
                    "speaker": s.get("speaker", "participant"),
                    "speaker_name": s.get("speaker_name", "Participant"),
                    "text": s.get("text", ""),
                    "timestamp": s.get("timestamp", ""),
                    "language": s.get("language", "en-US"),
                }
                for s in in_memory_segments
            ]

        doctor_profile = meeting.doctor.doctor_profile if meeting.doctor else None
        doctor_name = doctor_profile.full_name if doctor_profile and doctor_profile.full_name else meeting.doctor.email
        specialization = doctor_profile.specialization if doctor_profile and doctor_profile.specialization else "General Physician"
        patient_name = meeting.patient.email

        # Build formatted session transcript
        MeetingService._ensure_transcript_dir()
        session_num = request.session_number
        file_name = f"{meeting.id}_session_{session_num}_transcript.txt"
        file_path = os.path.join(TRANSCRIPT_DIR, file_name)

        now_utc = datetime.now(timezone.utc)
        header_lines = [
            "=" * 80,
            f"MEDTRUST HEALTHCARE — SESSION {session_num} TRANSCRIPT",
            "=" * 80,
            f"Meeting ID      : {meeting.id}",
            f"Room Code       : {meeting.room_id}",
            f"Session Number  : {session_num}",
            f"Meeting Window  : {meeting.start_time.strftime('%Y-%m-%d %H:%M:%S UTC')} - {meeting.end_time.strftime('%H:%M:%S UTC')}",
            f"Doctor          : Dr. {doctor_name} ({meeting.doctor.email})",
            f"Specialization  : {specialization}",
            f"Patient         : {patient_name}",
            f"Languages       : English / Urdu (اردو)",
            f"Recorded At     : {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "=" * 80,
            "",
            "[DOCTOR CLINICAL NOTES & SUMMARY]:",
            f"{request.doctor_notes or 'No clinical notes added'}",
            "",
            "=" * 80,
            f"SESSION {session_num} DIALOGUE TRANSCRIPT (ENGLISH & URDU):",
            "=" * 80,
        ]

        body_lines = []
        if segments:
            for seg in segments:
                if isinstance(seg, dict):
                    speaker = seg.get("speaker_name") or seg.get("speaker") or "Participant"
                    ts = seg.get("timestamp", "")
                    text = seg.get("text", "").strip()
                    lang = seg.get("language", "")
                else:
                    speaker = getattr(seg, "speaker_name", None) or getattr(seg, "speaker", None) or "Participant"
                    ts = getattr(seg, "timestamp", "")
                    text = (getattr(seg, "text", "") or "").strip()
                    lang = getattr(seg, "language", "")
                lang_tag = f" [{lang}]" if lang else ""
                body_lines.append(f"[{ts}] {speaker}{lang_tag}: {text}")
        else:
            body_lines.append("(No spoken conversation was detected or transcribed during this session)")

        footer_lines = [
            "",
            "=" * 80,
            f"END OF SESSION {session_num} TRANSCRIPT — CONFIDENTIAL MEDICAL RECORD",
            "=" * 80,
        ]

        full_text = "\n".join(header_lines + body_lines + footer_lines)

        # Write UTF-8 file to disk
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_text)

        logger.info(f"Session {session_num} transcript saved for meeting {meeting.id} at {file_path}")

        return SessionTranscriptResponse(
            meeting_id=meeting.id,
            session_number=session_num,
            transcript_path=file_path,
            message=f"Session {session_num} transcript saved successfully",
        )

    # ── Transcript Download for Doctor ───────────────────────────────────

    @staticmethod
    async def get_transcript_download(
        session: AsyncSession,
        user: User,
        meeting_id: uuid.UUID,
    ) -> FileResponse:
        """
        Securely stream transcript file download exclusively to the consulting doctor.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        # Restrict to doctor (or admin)
        if user.id != meeting.doctor_id and user.role != UserRole.SAAS_ADMIN:
            raise AuthorizationError("Only the consulting doctor can download this consultation transcript")

        if not meeting.transcript_path or not os.path.exists(meeting.transcript_path):
            raise NotFoundError("Transcript file is not yet generated for this meeting")

        file_name = f"Consultation_Transcript_{meeting.room_id}.txt"
        return FileResponse(
            path=meeting.transcript_path,
            media_type="text/plain; charset=utf-8",
            filename=file_name,
        )

    # ── Response Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _format_meeting_response(meeting: Meeting) -> MeetingResponse:
        doctor_profile = meeting.doctor.doctor_profile if meeting.doctor else None

        # Format attached patient documents
        attached_docs = []
        if hasattr(meeting, 'attached_documents') and meeting.attached_documents:
            for md in meeting.attached_documents:
                pd = md.patient_document
                if pd:
                    attached_docs.append(
                        MeetingDocumentResponse(
                            id=md.id,
                            meeting_id=md.meeting_id,
                            patient_document_id=md.patient_document_id,
                            label=pd.label,
                            original_filename=pd.original_filename,
                            file_size=pd.file_size,
                            mime_type=pd.mime_type,
                            uploaded_at=pd.created_at,
                        )
                    )

        return MeetingResponse(
            id=meeting.id,
            room_id=meeting.room_id,
            doctor_id=meeting.doctor_id,
            doctor_name=doctor_profile.full_name if doctor_profile else None,
            doctor_specialization=doctor_profile.specialization if doctor_profile else None,
            doctor_email=meeting.doctor.email if meeting.doctor else "",
            patient_id=meeting.patient_id,
            patient_name=meeting.patient.email if meeting.patient else "",
            patient_email=meeting.patient.email if meeting.patient else "",
            start_time=meeting.start_time,
            end_time=meeting.end_time,
            status=meeting.status,
            patient_notes=meeting.patient_notes,
            doctor_notes=meeting.doctor_notes,
            has_transcript=bool(meeting.transcript_text or meeting.transcript_path),
            attached_documents=attached_docs,
            created_at=meeting.created_at,
        )

    # ── Weekly Schedule Management ───────────────────────────────────────

    @staticmethod
    async def save_weekly_schedule(
        session: AsyncSession,
        doctor_user: User,
        request: WeeklyScheduleSaveRequest,
    ) -> List[WeeklyScheduleResponse]:
        """Save or replace the doctor's entire weekly availability template."""
        if doctor_user.role != UserRole.DOCTOR:
            raise AuthorizationError("Only doctors can set weekly schedule")
        if doctor_user.status != UserStatus.ACTIVE:
            raise AuthorizationError("Only verified active doctors can set weekly schedule")

        # Delete existing schedule for this doctor
        await MeetingRepository.delete_all_weekly_schedules(session, doctor_user.id)

        # Create new schedule entries (only active days)
        schedules_to_create = []
        seen_days = set()
        for slot in request.schedule:
            if slot.day_of_week in seen_days:
                raise ValidationError(f"Duplicate day_of_week: {slot.day_of_week}")
            seen_days.add(slot.day_of_week)

            if not slot.is_active:
                continue

            h1, m1 = map(int, slot.start_time.split(":"))
            h2, m2 = map(int, slot.end_time.split(":"))

            schedules_to_create.append(
                DoctorWeeklySchedule(
                    id=uuid.uuid4(),
                    doctor_id=doctor_user.id,
                    day_of_week=slot.day_of_week,
                    start_time=time(h1, m1),
                    end_time=time(h2, m2),
                    slot_duration_minutes=slot.slot_duration_minutes,
                    is_active=True,
                )
            )

        if schedules_to_create:
            await MeetingRepository.create_weekly_schedules(session, schedules_to_create)

        await session.commit()

        # Return all (including inactive markers)
        all_schedules = await MeetingRepository.get_weekly_schedule(session, doctor_user.id)
        return [
            WeeklyScheduleResponse(
                id=s.id,
                doctor_id=s.doctor_id,
                day_of_week=s.day_of_week,
                start_time=s.start_time.strftime("%H:%M"),
                end_time=s.end_time.strftime("%H:%M"),
                slot_duration_minutes=s.slot_duration_minutes,
                is_active=s.is_active,
            )
            for s in all_schedules
        ]

    @staticmethod
    async def get_weekly_schedule(
        session: AsyncSession,
        doctor_user: User,
    ) -> List[WeeklyScheduleResponse]:
        """Fetch doctor's current weekly schedule template."""
        schedules = await MeetingRepository.get_weekly_schedule(session, doctor_user.id)
        return [
            WeeklyScheduleResponse(
                id=s.id,
                doctor_id=s.doctor_id,
                day_of_week=s.day_of_week,
                start_time=s.start_time.strftime("%H:%M"),
                end_time=s.end_time.strftime("%H:%M"),
                slot_duration_minutes=s.slot_duration_minutes,
                is_active=s.is_active,
            )
            for s in schedules
        ]

    @staticmethod
    async def generate_slots_from_schedule(
        session: AsyncSession,
        doctor_user: User,
        request: GenerateWeekSlotsRequest,
    ) -> List[AvailabilityResponse]:
        """
        Generate actual bookable DoctorAvailability slots from the saved weekly
        schedule template for the specified number of weeks ahead.
        """
        if doctor_user.role != UserRole.DOCTOR:
            raise AuthorizationError("Only doctors can generate availability slots")
        if doctor_user.status != UserStatus.ACTIVE:
            raise AuthorizationError("Only verified active doctors can generate slots")

        schedules = await MeetingRepository.get_weekly_schedule(session, doctor_user.id)
        active_schedules = [s for s in schedules if s.is_active]

        if not active_schedules:
            raise ValidationError("No active weekly schedule found. Please save your weekly schedule first.")

        now = datetime.now(timezone.utc)
        today = now.date()
        total_days = request.weeks_ahead * 7
        slots_created: List[DoctorAvailability] = []

        # The schedule times are in doctor's local timezone.
        # Convert to UTC by applying the timezone offset.
        # JS getTimezoneOffset() returns negative for ahead-of-UTC (e.g. -300 for PKT/UTC+5)
        # So: UTC_time = local_time + offset_minutes (where offset is negative for +UTC zones)
        tz_offset = timedelta(minutes=request.timezone_offset_minutes)

        for day_offset in range(total_days):
            target_date = today + timedelta(days=day_offset)
            # Python weekday: Monday=0, Sunday=6 (matches our model)
            target_weekday = target_date.weekday()

            for sched in active_schedules:
                if sched.day_of_week != target_weekday:
                    continue

                # Build datetime in doctor's local time, then convert to UTC
                local_start = datetime.combine(
                    target_date, sched.start_time, tzinfo=timezone.utc
                )
                local_end = datetime.combine(
                    target_date, sched.end_time, tzinfo=timezone.utc
                )

                # Apply timezone offset to convert local → UTC
                window_start = local_start + tz_offset
                window_end = local_end + tz_offset

                delta = timedelta(minutes=sched.slot_duration_minutes)
                current = window_start

                while current + delta <= window_end:
                    slot_end = current + delta

                    # Skip slots that are already in the past
                    if current < now:
                        current = slot_end
                        continue

                    # Check for existing conflict
                    has_conflict = await MeetingRepository.check_slot_conflict(
                        session, doctor_user.id, current, slot_end
                    )
                    if not has_conflict:
                        slots_created.append(
                            DoctorAvailability(
                                id=uuid.uuid4(),
                                doctor_id=doctor_user.id,
                                start_time=current,
                                end_time=slot_end,
                                is_booked=False,
                            )
                        )

                    current = slot_end

        if not slots_created:
            raise ConflictError(
                "No new slots could be generated. All slots either already exist, "
                "conflict with existing ones, or are in the past."
            )

        created = await MeetingRepository.create_availability_slots(session, slots_created)
        await session.commit()

        logger.info(
            f"Generated {len(created)} slots from weekly schedule for doctor {doctor_user.id} "
            f"({request.weeks_ahead} week(s) ahead)"
        )

        return [AvailabilityResponse.model_validate(slot) for slot in created]
