"""
Models package — import all models here so Alembic can discover them.
"""

from app.models.doctor_availability import DoctorAvailability
from app.models.doctor_document import DoctorDocument
from app.models.doctor_profile import DoctorProfile
from app.models.enums import DocumentType, MeetingStatus, UserRole, UserStatus
from app.models.meeting import Meeting
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.weekly_schedule import DoctorWeeklySchedule

__all__ = [
    "User",
    "RefreshToken",
    "DoctorProfile",
    "DoctorDocument",
    "DoctorAvailability",
    "Meeting",
    "DoctorWeeklySchedule",
    "UserRole",
    "UserStatus",
    "DocumentType",
    "MeetingStatus",
]

