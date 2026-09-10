"""add ratings, prescriptions, and cached doctor ratings

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add rating summary columns to doctor_profiles ────────────────────
    op.add_column("doctor_profiles", sa.Column("average_rating", sa.Float(), nullable=True))
    op.add_column("doctor_profiles", sa.Column("total_ratings", sa.Integer(), server_default="0", nullable=False))

    # ── Create doctor_ratings table ──────────────────────────────────────
    op.create_table(
        "doctor_ratings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_doctor_ratings_rating_range"), nullable=False),
        sa.Column("feedback_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_doctor_ratings_meeting_id", "doctor_ratings", ["meeting_id"])
    op.create_index("ix_doctor_ratings_doctor_id", "doctor_ratings", ["doctor_id"])
    op.create_index("ix_doctor_ratings_patient_id", "doctor_ratings", ["patient_id"])

    # ── Create prescriptions table ───────────────────────────────────────
    op.create_table(
        "prescriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_prescriptions_meeting_id", "prescriptions", ["meeting_id"])
    op.create_index("ix_prescriptions_doctor_id", "prescriptions", ["doctor_id"])
    op.create_index("ix_prescriptions_patient_id", "prescriptions", ["patient_id"])

    # ── Create prescription_medicines table ──────────────────────────────
    op.create_table(
        "prescription_medicines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("prescription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("medicine_name", sa.String(500), nullable=False),
        sa.Column("morning", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("morning_time", sa.Time(), nullable=True),
        sa.Column("morning_before_meal", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("afternoon", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("afternoon_time", sa.Time(), nullable=True),
        sa.Column("afternoon_before_meal", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("evening", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("evening_time", sa.Time(), nullable=True),
        sa.Column("evening_before_meal", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("night", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("night_time", sa.Time(), nullable=True),
        sa.Column("night_before_meal", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_prescription_medicines_prescription_id", "prescription_medicines", ["prescription_id"])


def downgrade() -> None:
    op.drop_index("ix_prescription_medicines_prescription_id", table_name="prescription_medicines")
    op.drop_table("prescription_medicines")

    op.drop_index("ix_prescriptions_patient_id", table_name="prescriptions")
    op.drop_index("ix_prescriptions_doctor_id", table_name="prescriptions")
    op.drop_index("ix_prescriptions_meeting_id", table_name="prescriptions")
    op.drop_table("prescriptions")

    op.drop_index("ix_doctor_ratings_patient_id", table_name="doctor_ratings")
    op.drop_index("ix_doctor_ratings_doctor_id", table_name="doctor_ratings")
    op.drop_index("ix_doctor_ratings_meeting_id", table_name="doctor_ratings")
    op.drop_table("doctor_ratings")

    op.drop_column("doctor_profiles", "total_ratings")
    op.drop_column("doctor_profiles", "average_rating")
