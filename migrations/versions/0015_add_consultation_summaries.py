"""Add consultation_summaries table

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultation_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doctor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary_text", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("llm_model_used", sa.String(100), nullable=True),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index("ix_consultation_summaries_meeting_id", "consultation_summaries", ["meeting_id"])
    op.create_index("ix_consultation_summaries_patient_id", "consultation_summaries", ["patient_id"])
    op.create_index("ix_consultation_summaries_doctor_id", "consultation_summaries", ["doctor_id"])


def downgrade() -> None:
    op.drop_index("ix_consultation_summaries_doctor_id", table_name="consultation_summaries")
    op.drop_index("ix_consultation_summaries_patient_id", table_name="consultation_summaries")
    op.drop_index("ix_consultation_summaries_meeting_id", table_name="consultation_summaries")
    op.drop_table("consultation_summaries")
