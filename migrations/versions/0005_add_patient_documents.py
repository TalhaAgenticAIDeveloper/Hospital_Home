"""add patient documents and meeting documents tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create patient_documents table ────────────────────────────────
    op.create_table(
        "patient_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("stored_filename", sa.String(length=500), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_filename"),
    )
    op.create_index(
        op.f("ix_patient_documents_patient_id"),
        "patient_documents",
        ["patient_id"],
        unique=False,
    )

    # ── 2. Create meeting_documents table ────────────────────────────────
    op.create_table(
        "meeting_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_document_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_document_id"],
            ["patient_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "meeting_id",
            "patient_document_id",
            name="uq_meeting_patient_document",
        ),
    )
    op.create_index(
        op.f("ix_meeting_documents_meeting_id"),
        "meeting_documents",
        ["meeting_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_meeting_documents_patient_document_id"),
        "meeting_documents",
        ["patient_document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_meeting_documents_patient_document_id"),
        table_name="meeting_documents",
    )
    op.drop_index(
        op.f("ix_meeting_documents_meeting_id"),
        table_name="meeting_documents",
    )
    op.drop_table("meeting_documents")

    op.drop_index(
        op.f("ix_patient_documents_patient_id"),
        table_name="patient_documents",
    )
    op.drop_table("patient_documents")
