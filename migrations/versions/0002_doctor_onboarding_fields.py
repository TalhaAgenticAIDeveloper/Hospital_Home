"""add doctor onboarding fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add professional, verification & review columns to doctor_profiles ─────────
    op.add_column("doctor_profiles", sa.Column("full_name", sa.String(255), nullable=True))
    op.add_column("doctor_profiles", sa.Column("father_name", sa.String(255), nullable=True))
    op.add_column("doctor_profiles", sa.Column("pmdc_registration_number", sa.String(100), nullable=True))
    op.add_column("doctor_profiles", sa.Column("phone_number", sa.String(20), nullable=True))
    op.add_column("doctor_profiles", sa.Column("specialization", sa.String(255), nullable=True))
    op.add_column("doctor_profiles", sa.Column("license_number", sa.String(100), nullable=True))
    op.add_column("doctor_profiles", sa.Column("years_of_experience", sa.Integer(), nullable=True))
    op.add_column("doctor_profiles", sa.Column("qualification", sa.String(500), nullable=True))
    op.add_column("doctor_profiles", sa.Column("bio", sa.Text(), nullable=True))
    op.add_column("doctor_profiles", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("doctor_profiles", sa.Column("admin_feedback", sa.Text(), nullable=True))
    op.add_column("doctor_profiles", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "doctor_profiles",
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("doctor_profiles", "reviewed_by")
    op.drop_column("doctor_profiles", "reviewed_at")
    op.drop_column("doctor_profiles", "admin_feedback")
    op.drop_column("doctor_profiles", "submitted_at")
    op.drop_column("doctor_profiles", "bio")
    op.drop_column("doctor_profiles", "qualification")
    op.drop_column("doctor_profiles", "years_of_experience")
    op.drop_column("doctor_profiles", "license_number")
    op.drop_column("doctor_profiles", "specialization")
    op.drop_column("doctor_profiles", "phone_number")
    op.drop_column("doctor_profiles", "pmdc_registration_number")
    op.drop_column("doctor_profiles", "father_name")
    op.drop_column("doctor_profiles", "full_name")
