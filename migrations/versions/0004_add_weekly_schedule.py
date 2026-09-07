"""add doctor weekly schedule table

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doctor_weekly_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("slot_duration_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )

    # Index for fast lookup by doctor
    op.create_index(
        "ix_doctor_weekly_schedules_doctor_id",
        "doctor_weekly_schedules",
        ["doctor_id"],
    )

    # Unique constraint: one entry per doctor per day of week
    op.create_index(
        "ix_weekly_schedule_doctor_day",
        "doctor_weekly_schedules",
        ["doctor_id", "day_of_week"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_weekly_schedule_doctor_day", table_name="doctor_weekly_schedules")
    op.drop_index("ix_doctor_weekly_schedules_doctor_id", table_name="doctor_weekly_schedules")
    op.drop_table("doctor_weekly_schedules")
