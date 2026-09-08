"""
Patient document model — metadata for medical documents uploaded by patients.

Actual files are stored on the filesystem. This model tracks metadata
(filename, type, path, size, label) and links to the patient user.
Patients can upload up to 5 documents, each up to 50 MB.
"""

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class PatientDocument(TimestampMixin, Base):
    """
    Metadata for a medical document uploaded by a patient.

    The actual file is stored on disk at `file_path`.
    Only metadata is stored in the database.

    Constraints (enforced at service layer):
    - Max 5 documents per patient
    - Max 50 MB per document
    """

    __tablename__ = "patient_documents"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Key ──────────────────────────────────────────────────────
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # ── Document Metadata ────────────────────────────────────────────────
    label: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )
    original_filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    stored_filename: Mapped[str] = mapped_column(
        String(500),
        unique=True,
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )
    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────
    patient: Mapped["User"] = relationship(
        "User",
        back_populates="patient_documents",
        foreign_keys=[patient_id],
    )

    def __repr__(self) -> str:
        return (
            f"<PatientDocument id={self.id} patient={self.patient_id} "
            f"file={self.original_filename}>"
        )
