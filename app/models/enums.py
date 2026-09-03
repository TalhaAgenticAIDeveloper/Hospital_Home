"""
Enumerations for user roles, account statuses, and document types.

These are used as both Python enums and PostgreSQL enum types via SQLAlchemy.
"""

import enum


class UserRole(str, enum.Enum):
    """Roles available in the system."""

    PATIENT = "patient"
    DOCTOR = "doctor"
    SAAS_ADMIN = "saas_admin"


class UserStatus(str, enum.Enum):
    """Account status lifecycle states."""

    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class DocumentType(str, enum.Enum):
    """Types of documents a doctor can upload."""

    MEDICAL_LICENSE = "medical_license"
    DEGREE_CERTIFICATE = "degree_certificate"
    ID_PROOF = "id_proof"
    PROFILE_PHOTO = "profile_photo"
    OTHER = "other"
