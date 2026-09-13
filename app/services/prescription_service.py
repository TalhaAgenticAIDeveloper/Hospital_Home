"""
Prescription service — manages medical prescriptions, dosage schedules, and reminders.
"""

import uuid
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.enums import MeetingStatus, UserRole
from app.models.prescription import Prescription, PrescriptionMedicine
from app.models.user import User
from app.repositories.meeting_repository import MeetingRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.user_repository import UserRepository
from app.schemas.prescription import (
    PrescriptionCreateRequest,
    PrescriptionMedicineResponse,
    PrescriptionResponse,
)
from app.services.reminder_scheduler import schedule_prescription_reminders

logger = get_logger(__name__)


class PrescriptionService:
    """Handles consultation prescriptions and automated medicine reminders."""

    @staticmethod
    async def create_prescription(
        session: AsyncSession,
        current_user: User,
        payload: PrescriptionCreateRequest,
    ) -> PrescriptionResponse:
        """
        Create a prescription with medicine schedules and schedule reminder emails.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, payload.meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        # Must be meeting's assigned doctor
        if meeting.doctor_id != current_user.id:
            raise AuthorizationError("Only the assigned doctor can issue a prescription for this consultation")

        # Meeting should be completed or in-progress
        if meeting.status not in (MeetingStatus.COMPLETED, MeetingStatus.IN_PROGRESS):
            raise ValidationError("Prescriptions can only be created for completed or active consultations")

        # Check existing prescription for this meeting
        existing = await PrescriptionRepository.get_by_meeting_id(session, payload.meeting_id)
        if existing:
            raise ConflictError("A prescription has already been issued for this meeting")

        # Build Prescription
        prescription = Prescription(
            meeting_id=meeting.id,
            doctor_id=meeting.doctor_id,
            patient_id=meeting.patient_id,
            notes=payload.notes.strip() if payload.notes else None,
        )

        # Build Medicines
        for item in payload.medicines:
            med = PrescriptionMedicine(
                medicine_name=item.medicine_name.strip(),
                morning=item.morning,
                morning_time=item.morning_time,
                morning_before_meal=item.morning_before_meal,
                afternoon=item.afternoon,
                afternoon_time=item.afternoon_time,
                afternoon_before_meal=item.afternoon_before_meal,
                evening=item.evening,
                evening_time=item.evening_time,
                evening_before_meal=item.evening_before_meal,
                night=item.night,
                night_time=item.night_time,
                night_before_meal=item.night_before_meal,
                start_date=item.start_date,
                end_date=item.end_date,
            )
            prescription.medicines.append(med)

        saved = await PrescriptionRepository.create_prescription(session, prescription)
        await session.commit()

        # Retrieve patient and doctor details for reminders & response
        patient_user = await UserRepository.get_by_id(session, meeting.patient_id)
        patient_email = patient_user.email if patient_user else ""
        patient_name = (
            patient_user.patient_profile.full_name
            if (patient_user and patient_user.patient_profile and patient_user.patient_profile.full_name)
            else (patient_user.email.split("@")[0] if patient_user else "Patient")
        )

        doctor_user = await UserRepository.get_by_id(session, meeting.doctor_id)
        doctor_name = (
            doctor_user.doctor_profile.full_name
            if (doctor_user and doctor_user.doctor_profile and doctor_user.doctor_profile.full_name)
            else (doctor_user.email.split("@")[0] if doctor_user else "Doctor")
        )

        # Schedule reminder emails
        if patient_email:
            try:
                schedule_prescription_reminders(
                    prescription=saved,
                    patient_email=patient_email,
                    patient_name=patient_name,
                    doctor_name=doctor_name,
                )
            except Exception as e:
                logger.error(f"Error scheduling prescription reminders: {e}")

        logger.info(
            f"prescription_created: prescription_id={saved.id} meeting_id={meeting.id} "
            f"medicines_count={len(saved.medicines)} patient={patient_email}"
        )

        return PrescriptionResponse(
            id=saved.id,
            meeting_id=saved.meeting_id,
            doctor_id=saved.doctor_id,
            patient_id=saved.patient_id,
            notes=saved.notes,
            doctor_name=doctor_name,
            patient_name=patient_name,
            created_at=saved.created_at,
            medicines=[PrescriptionMedicineResponse.model_validate(m) for m in saved.medicines],
        )

    @staticmethod
    async def get_by_meeting_id(
        session: AsyncSession,
        meeting_id: uuid.UUID,
        current_user: User,
    ) -> PrescriptionResponse:
        """
        Fetch prescription for a specific meeting.
        """
        meeting = await MeetingRepository.get_meeting_by_id(session, meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        if (
            current_user.role != UserRole.SAAS_ADMIN
            and meeting.patient_id != current_user.id
            and meeting.doctor_id != current_user.id
        ):
            raise AuthorizationError("You are not authorized to view this prescription")

        prescription = await PrescriptionRepository.get_by_meeting_id(session, meeting_id)
        if not prescription:
            raise NotFoundError("No prescription found for this consultation")

        doctor_name = (
            prescription.doctor.doctor_profile.full_name
            if (prescription.doctor and prescription.doctor.doctor_profile and prescription.doctor.doctor_profile.full_name)
            else (prescription.doctor.email.split("@")[0] if prescription.doctor else "Doctor")
        )
        patient_name = (
            prescription.patient.patient_profile.full_name
            if (prescription.patient and prescription.patient.patient_profile and prescription.patient.patient_profile.full_name)
            else (prescription.patient.email.split("@")[0] if prescription.patient else "Patient")
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
    async def list_patient_prescriptions(
        session: AsyncSession,
        current_user: User,
    ) -> List[PrescriptionResponse]:
        """
        List all prescriptions issued to the logged-in patient.
        """
        prescriptions = await PrescriptionRepository.list_by_patient_id(session, current_user.id)
        results = []
        for p in prescriptions:
            doc_name = (
                p.doctor.doctor_profile.full_name
                if (p.doctor and p.doctor.doctor_profile and p.doctor.doctor_profile.full_name)
                else (p.doctor.email.split("@")[0] if p.doctor else "Doctor")
            )
            pat_name = (
                current_user.patient_profile.full_name
                if (current_user.patient_profile and current_user.patient_profile.full_name)
                else current_user.email.split("@")[0]
            )
            results.append(
                PrescriptionResponse(
                    id=p.id,
                    meeting_id=p.meeting_id,
                    doctor_id=p.doctor_id,
                    patient_id=p.patient_id,
                    notes=p.notes,
                    doctor_name=doc_name,
                    patient_name=pat_name,
                    created_at=p.created_at,
                    medicines=[PrescriptionMedicineResponse.model_validate(m) for m in p.medicines],
                )
            )
        return results
