"""add doctor onboarding fields and documents table

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

document_type_enum = postgresql.ENUM(
    "medical_license",
    "degree_certificate",
    "id_proof",
    "profile_photo",
    "other",
    name="document_type",
    create_type=False,
)


def upgrade() -> None:
    # ── Create document_type enum ────────────────────────────────────
    op.execute(
        "CREATE TYPE document_type AS ENUM ("
        "'medical_license', 'degree_certificate', 'id_proof', 'profile_photo', 'other')"
    )

    # ── Add professional & review columns to doctor_profiles ─────────
    op.add_column("doctor_profiles", sa.Column("full_name", sa.String(255), nullable=True))
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

    # ── Create doctor_documents table ────────────────────────────────
    op.create_table(
        "doctor_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "doctor_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("doctor_profiles.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        ),
        sa.Column("document_type", document_type_enum, nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("stored_filename", sa.String(500), unique=True, nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
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


def downgrade() -> None:
    op.drop_table("doctor_documents")

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
    op.drop_column("doctor_profiles", "full_name")

    op.execute("DROP TYPE IF EXISTS document_type")
