"""
Enumerations for user roles and account statuses.

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
