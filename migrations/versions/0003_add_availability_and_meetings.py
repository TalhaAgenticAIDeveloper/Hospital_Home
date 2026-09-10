"""add doctor availability and meetings tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

meeting_status_enum = postgresql.ENUM(
    "scheduled",
    "in_progress",
    "completed",
    "cancelled",
    name="meeting_status",
    create_type=False,
)


def upgrade() -> None:
    # ── Create meeting_status enum ───────────────────────────────────
    op.execute(
        "CREATE TYPE meeting_status AS ENUM ("
        "'scheduled', 'in_progress', 'completed', 'cancelled')"
    )

    # ── Create doctor_availabilities table ────────────────────────────
    op.create_table(
        "doctor_availabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_booked", sa.Boolean(), server_default="false", nullable=False, index=True),
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
        "ix_doctor_availability_slot",
        "doctor_availabilities",
        ["doctor_id", "start_time", "end_time"],
    )

    # ── Create meetings table ────────────────────────────────────────
    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("room_id", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "availability_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("doctor_availabilities.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", meeting_status_enum, server_default="scheduled", nullable=False, index=True),
        sa.Column("patient_notes", sa.Text(), nullable=True),
        sa.Column("doctor_notes", sa.Text(), nullable=True),
        sa.Column("transcript_path", sa.String(1000), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
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
        "ix_meetings_doctor_status",
        "meetings",
        ["doctor_id", "status"],
    )
    op.create_index(
        "ix_meetings_patient_status",
        "meetings",
        ["patient_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_meetings_patient_status", table_name="meetings")
    op.drop_index("ix_meetings_doctor_status", table_name="meetings")
    op.drop_table("meetings")

    op.drop_index("ix_doctor_availability_slot", table_name="doctor_availabilities")
    op.drop_table("doctor_availabilities")

    op.execute("DROP TYPE IF EXISTS meeting_status")
