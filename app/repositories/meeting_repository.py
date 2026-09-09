"""
Repository for Doctor Availability and Meetings database operations.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.doctor_availability import DoctorAvailability
from app.models.doctor_profile import DoctorProfile
from app.models.enums import MeetingStatus, UserRole, UserStatus
from app.models.meeting import Meeting
from app.models.meeting_document import MeetingDocument
from app.models.patient_document import PatientDocument
from app.models.user import User
from app.models.weekly_schedule import DoctorWeeklySchedule


class MeetingRepository:
    """Database operations for doctor availability and patient-doctor meetings."""

    # ── Availability Operations ──────────────────────────────────────────

    @staticmethod
    async def create_availability_slots(
        session: AsyncSession,
        slots: List[DoctorAvailability],
    ) -> List[DoctorAvailability]:
        """Bulk insert availability slots."""
        session.add_all(slots)
        await session.flush()
        for slot in slots:
            await session.refresh(slot)
        return slots

    @staticmethod
    async def get_availability_by_id(
        session: AsyncSession,
        availability_id: uuid.UUID,
        for_update: bool = False,
    ) -> Optional[DoctorAvailability]:
        """Fetch a specific availability slot by ID, optionally locking row."""
        query = select(DoctorAvailability).where(DoctorAvailability.id == availability_id)
        if for_update:
            query = query.with_for_update()
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_doctor_availabilities(
        session: AsyncSession,
        doctor_id: uuid.UUID,
        future_only: bool = True,
        unbooked_only: bool = False,
    ) -> List[DoctorAvailability]:
        """List availability slots for a doctor."""
        conditions = [DoctorAvailability.doctor_id == doctor_id]
        if future_only:
            now = datetime.now(timezone.utc)
            conditions.append(DoctorAvailability.start_time >= now)
        if unbooked_only:
            conditions.append(DoctorAvailability.is_booked.is_(False))

        query = (
            select(DoctorAvailability)
            .where(and_(*conditions))
            .order_by(DoctorAvailability.start_time.asc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def check_slot_conflict(
        session: AsyncSession,
        doctor_id: uuid.UUID,
        start_time: datetime,
        end_time: datetime,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """Check if a doctor already has an overlapping availability slot."""
        conditions = [
            DoctorAvailability.doctor_id == doctor_id,
            DoctorAvailability.start_time < end_time,
            DoctorAvailability.end_time > start_time,
        ]
        if exclude_id:
            conditions.append(DoctorAvailability.id != exclude_id)

        query = select(DoctorAvailability.id).where(and_(*conditions)).limit(1)
        result = await session.execute(query)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def delete_availability(
        session: AsyncSession,
        availability_id: uuid.UUID,
        doctor_id: uuid.UUID,
    ) -> bool:
        """Delete an unbooked availability slot belonging to doctor."""
        slot = await MeetingRepository.get_availability_by_id(session, availability_id)
        if not slot or slot.doctor_id != doctor_id or slot.is_booked:
            return False
        await session.delete(slot)
        await session.flush()
        return True

    # ── Doctor Directory Operations ──────────────────────────────────────

    @staticmethod
    async def list_active_doctors(
        session: AsyncSession,
    ) -> List[Tuple[User, DoctorProfile, int]]:
        """
        List all approved, active doctors with their profile and count of available slots.
        """
        now = datetime.now(timezone.utc)

        # Count future unbooked slots subquery
        slots_subquery = (
            select(
                DoctorAvailability.doctor_id,
                func.count(DoctorAvailability.id).label("slots_count"),
            )
            .where(
                and_(
                    DoctorAvailability.is_booked.is_(False),
                    DoctorAvailability.start_time >= now,
                )
            )
            .group_by(DoctorAvailability.doctor_id)
            .subquery()
        )

        query = (
            select(
                User,
                DoctorProfile,
                func.coalesce(slots_subquery.c.slots_count, 0).label("available_slots_count"),
            )
            .join(DoctorProfile, User.id == DoctorProfile.user_id)
            .outerjoin(slots_subquery, User.id == slots_subquery.c.doctor_id)
            .where(
                and_(
                    User.role == UserRole.DOCTOR,
                    User.status == UserStatus.ACTIVE,
                    User.is_active.is_(True),
                )
            )
            .order_by(DoctorProfile.full_name.asc())
        )

        result = await session.execute(query)
        return list(result.all())

    # ── Meeting Operations ───────────────────────────────────────────────

    @staticmethod
    async def create_meeting(
        session: AsyncSession,
        meeting: Meeting,
    ) -> Meeting:
        """Persist a new meeting record."""
        session.add(meeting)
        await session.flush()
        await session.refresh(meeting)
        return meeting

    @staticmethod
    async def get_meeting_by_id(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> Optional[Meeting]:
        """Fetch meeting by primary key with relationships loaded."""
        query = (
            select(Meeting)
            .options(
                selectinload(Meeting.doctor).selectinload(User.doctor_profile),
                selectinload(Meeting.patient).selectinload(User.patient_profile),
                selectinload(Meeting.availability),
                selectinload(Meeting.attached_documents)
                    .selectinload(MeetingDocument.patient_document),
            )
            .where(Meeting.id == meeting_id)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_meeting_by_room_id(
        session: AsyncSession,
        room_id: str,
    ) -> Optional[Meeting]:
        """Fetch meeting by unique room code."""
        query = (
            select(Meeting)
            .options(
                selectinload(Meeting.doctor).selectinload(User.doctor_profile),
                selectinload(Meeting.patient).selectinload(User.patient_profile),
                selectinload(Meeting.attached_documents)
                    .selectinload(MeetingDocument.patient_document),
            )
            .where(Meeting.room_id == room_id)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def auto_complete_expired_meetings(
        session: AsyncSession,
        user_id: uuid.UUID,
        role: UserRole,
        now_utc: datetime,
    ) -> None:
        """Mark scheduled or in_progress meetings whose end_time has passed as COMPLETED."""
        conditions = [
            Meeting.end_time <= now_utc,
            Meeting.status.in_([MeetingStatus.SCHEDULED, MeetingStatus.IN_PROGRESS]),
        ]
        if role == UserRole.DOCTOR:
            conditions.append(Meeting.doctor_id == user_id)
        elif role == UserRole.PATIENT:
            conditions.append(Meeting.patient_id == user_id)
        else:
            conditions.append(or_(Meeting.doctor_id == user_id, Meeting.patient_id == user_id))

        stmt = (
            update(Meeting)
            .where(and_(*conditions))
            .values(status=MeetingStatus.COMPLETED)
            .execution_options(synchronize_session=False)
        )
        await session.execute(stmt)
        await session.commit()

    @staticmethod
    async def list_meetings_for_user(
        session: AsyncSession,
        user_id: uuid.UUID,
        role: UserRole,
        status_filter: Optional[MeetingStatus] = None,
    ) -> List[Meeting]:
        """List meetings for a specific doctor or patient."""
        conditions = []
        if role == UserRole.DOCTOR:
            conditions.append(Meeting.doctor_id == user_id)
        elif role == UserRole.PATIENT:
            conditions.append(Meeting.patient_id == user_id)
        else:
            conditions.append(or_(Meeting.doctor_id == user_id, Meeting.patient_id == user_id))

        if status_filter:
            conditions.append(Meeting.status == status_filter)

        query = (
            select(Meeting)
            .options(
                selectinload(Meeting.doctor).selectinload(User.doctor_profile),
                selectinload(Meeting.patient).selectinload(User.patient_profile),
                selectinload(Meeting.availability),
                selectinload(Meeting.attached_documents)
                    .selectinload(MeetingDocument.patient_document),
            )
            .where(and_(*conditions))
            .order_by(Meeting.start_time.asc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update_meeting_status(
        session: AsyncSession,
        meeting: Meeting,
        status: MeetingStatus,
        doctor_notes: Optional[str] = None,
    ) -> Meeting:
        """Update meeting status and optional doctor notes."""
        meeting.status = status
        if doctor_notes is not None:
            meeting.doctor_notes = doctor_notes
        await session.flush()
        await session.refresh(meeting)
        return meeting

    @staticmethod
    async def update_meeting_status_and_transcript(
        session: AsyncSession,
        meeting: Meeting,
        status: MeetingStatus,
        transcript_text: Optional[str] = None,
        transcript_path: Optional[str] = None,
        doctor_notes: Optional[str] = None,
    ) -> Meeting:
        """Backwards-compatible wrapper for updating meeting status."""
        return await MeetingRepository.update_meeting_status(
            session=session,
            meeting=meeting,
            status=status,
            doctor_notes=doctor_notes,
        )

    # ── Weekly Schedule Operations ───────────────────────────────────────

    @staticmethod
    async def get_weekly_schedule(
        session: AsyncSession,
        doctor_id: uuid.UUID,
    ) -> List[DoctorWeeklySchedule]:
        """Fetch all weekly schedule entries for a doctor."""
        query = (
            select(DoctorWeeklySchedule)
            .where(DoctorWeeklySchedule.doctor_id == doctor_id)
            .order_by(DoctorWeeklySchedule.day_of_week.asc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def delete_all_weekly_schedules(
        session: AsyncSession,
        doctor_id: uuid.UUID,
    ) -> None:
        """Delete all weekly schedule entries for a doctor (before re-saving)."""
        from sqlalchemy import delete
        stmt = delete(DoctorWeeklySchedule).where(
            DoctorWeeklySchedule.doctor_id == doctor_id
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def create_weekly_schedules(
        session: AsyncSession,
        schedules: List[DoctorWeeklySchedule],
    ) -> List[DoctorWeeklySchedule]:
        """Bulk insert weekly schedule entries."""
        session.add_all(schedules)
        await session.flush()
        for s in schedules:
            await session.refresh(s)
        return schedules

    # ── Meeting Document Operations ──────────────────────────────────────

    @staticmethod
    async def create_meeting_documents(
        session: AsyncSession,
        meeting_documents: List[MeetingDocument],
    ) -> List[MeetingDocument]:
        """Bulk insert meeting-document associations."""
        if not meeting_documents:
            return []
        session.add_all(meeting_documents)
        await session.flush()
        for md in meeting_documents:
            await session.refresh(md)
        return meeting_documents

    @staticmethod
    async def get_meeting_documents(
        session: AsyncSession,
        meeting_id: uuid.UUID,
    ) -> List[MeetingDocument]:
        """List all patient documents attached to a specific meeting."""
        query = (
            select(MeetingDocument)
            .options(
                selectinload(MeetingDocument.patient_document),
            )
            .where(MeetingDocument.meeting_id == meeting_id)
            .order_by(MeetingDocument.created_at.asc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())
