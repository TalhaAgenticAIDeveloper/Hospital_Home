"""
ConsultationTranscript model — stores audio files and structured transcriptions
for doctor-patient consultations.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class ConsultationTranscript(TimestampMixin, Base):
    """
    Stores audio recordings and their Whisper-generated transcriptions
    for a consultation meeting.

    - Each participant's audio is recorded separately in the browser via MediaRecorder.
    - Groq Whisper API transcribes each audio file independently.
    - Segments are interleaved by timestamp into a structured transcript with speaker labels.
    """

    __tablename__ = "consultation_transcripts"

    # ── Primary Key ──────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Foreign Key (1:1 with Meeting) ───────────────────────────────────
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # ── Audio File Paths ─────────────────────────────────────────────────
    doctor_audio_path: Mapped[Optional[str]] = mapped_column(
        String(1000), nullable=True, default=None,
    )
    patient_audio_path: Mapped[Optional[str]] = mapped_column(
        String(1000), nullable=True, default=None,
    )

    # ── Raw Whisper Transcriptions ───────────────────────────────────────
    doctor_raw_transcription: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )
    patient_raw_transcription: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )

    # ── Structured Transcript (speaker-labeled, time-ordered) ────────────
    # Format: [{speaker, text, start_time, end_time, language, confidence}]
    structured_transcript: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=None,
    )

    # ── Flattened Human-Readable Text ────────────────────────────────────
    full_text: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )

    # ── Processing Metadata ──────────────────────────────────────────────
    transcription_model: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, default=None,
    )
    transcription_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
    )
    processing_time_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None,
    )

    # ── Relationships ────────────────────────────────────────────────────
    meeting = relationship("Meeting", foreign_keys=[meeting_id], lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<ConsultationTranscript id={self.id} meeting_id={self.meeting_id} "
            f"status={self.transcription_status}>"
        )
