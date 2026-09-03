"""
Doctor document model — metadata for uploaded documents.

Actual files are stored on the filesystem. This model tracks metadata
(filename, type, path, size) and links to the doctor profile.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.enums import DocumentType

if TYPE_CHECKING:
    from app.models.doctor_profile import DoctorProfile


class DoctorDocument(TimestampMixin, Base):
    """
    Metadata for a document uploaded by a doctor.

    The actual file is stored on disk at `file_path`.
    Only metadata is stored in the database.
    """

    __tablename__ = "doctor_documents"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Key ──────────────────────────────────────────────────────
    doctor_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("doctor_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # ── Document Metadata ────────────────────────────────────────────────
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type", values_callable=lambda x: [e.value for e in x], create_constraint=True),
        nullable=False,
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
    doctor_profile: Mapped["DoctorProfile"] = relationship(
        "DoctorProfile",
        back_populates="documents",
    )

    def __repr__(self) -> str:
        return (
            f"<DoctorDocument id={self.id} type={self.document_type} "
            f"file={self.original_filename}>"
        )
