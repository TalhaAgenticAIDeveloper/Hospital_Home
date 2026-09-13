"""add consultation_transcripts and consultation_ai_extractions tables

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── consultation_transcripts ─────────────────────────────────────────
    op.create_table(
        "consultation_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("doctor_audio_path", sa.String(1000), nullable=True),
        sa.Column("patient_audio_path", sa.String(1000), nullable=True),
        sa.Column("doctor_raw_transcription", sa.Text, nullable=True),
        sa.Column("patient_raw_transcription", sa.Text, nullable=True),
        sa.Column("structured_transcript", postgresql.JSONB, nullable=True),
        sa.Column("full_text", sa.Text, nullable=True),
        sa.Column("transcription_model", sa.String(100), nullable=True),
        sa.Column(
            "transcription_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("processing_time_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_consultation_transcripts_meeting_id",
        "consultation_transcripts",
        ["meeting_id"],
        unique=True,
    )

    # ── consultation_ai_extractions ──────────────────────────────────────
    op.create_table(
        "consultation_ai_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transcript_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consultation_transcripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("extraction_data", postgresql.JSONB, nullable=True),
        sa.Column("raw_llm_response", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("llm_model_used", sa.String(100), nullable=True),
        sa.Column("total_chunks", sa.Integer, nullable=False, server_default="1"),
        sa.Column("processing_time_ms", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("is_approved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_extraction_data", postgresql.JSONB, nullable=True),
        sa.Column("doctor_approval_notes", sa.Text, nullable=True),
        sa.Column(
            "prescription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prescriptions.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_consultation_ai_extractions_meeting_id",
        "consultation_ai_extractions",
        ["meeting_id"],
    )
    op.create_index(
        "ix_consultation_ai_extractions_meeting_version",
        "consultation_ai_extractions",
        ["meeting_id", "version"],
        unique=True,
    )
    op.create_index(
        "ix_consultation_ai_extractions_status",
        "consultation_ai_extractions",
        ["status"],
    )
    op.create_index(
        "ix_consultation_ai_extractions_doctor_id",
        "consultation_ai_extractions",
        ["doctor_id"],
    )


def downgrade() -> None:
    op.drop_table("consultation_ai_extractions")
    op.drop_table("consultation_transcripts")
