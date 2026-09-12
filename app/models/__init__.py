"""
Models package — import all models here so Alembic can discover them.
"""

from app.models.consultation_ai_extraction import ConsultationAIExtraction
from app.models.consultation_transcript import ConsultationTranscript
from app.models.doctor_availability import DoctorAvailability
from app.models.doctor_rating import DoctorRating
from app.models.email_verification import EmailVerification
from app.models.doctor_profile import DoctorProfile
from app.models.enums import MeetingStatus, UserRole, UserStatus
from app.models.meeting import Meeting
from app.models.meeting_document import MeetingDocument
from app.models.patient_document import PatientDocument
from app.models.patient_profile import PatientProfile
from app.models.prescription import Prescription, PrescriptionMedicine
from app.models.refresh_token import RefreshToken
from app.models.saas_admin import SaaSAdmin
from app.models.admin_refresh_token import AdminRefreshToken
from app.models.user import User
from app.models.weekly_schedule import DoctorWeeklySchedule

__all__ = [
    "User",
    "SaaSAdmin",
    "RefreshToken",
    "AdminRefreshToken",
    "ConsultationTranscript",
    "ConsultationAIExtraction",
    "DoctorProfile",
    "DoctorAvailability",
    "DoctorRating",
    "EmailVerification",
    "Meeting",
    "MeetingDocument",
    "PatientDocument",
    "PatientProfile",
    "Prescription",
    "PrescriptionMedicine",
    "DoctorWeeklySchedule",
    "UserRole",
    "UserStatus",
    "MeetingStatus",
]

