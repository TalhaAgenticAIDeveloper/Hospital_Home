"""add patient_report_sessions and patient_report_messages tables

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── patient_report_sessions ──────────────────────────────────────────────
    op.create_table(
        "patient_report_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("stored_filename", sa.String(500), nullable=True),
        sa.Column("file_path", sa.String(1000), nullable=True),
        sa.Column("file_size", sa.Integer, nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column(
            "extraction_method",
            sa.String(50),
            nullable=False,
            server_default="pdf_text",
        ),
        sa.Column("report_text", sa.Text, nullable=False),
        sa.Column("report_explanation", sa.Text, nullable=False),
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
        "ix_patient_report_sessions_patient_id",
        "patient_report_sessions",
        ["patient_id"],
    )
    op.create_index(
        "ix_patient_report_sessions_updated_at",
        "patient_report_sessions",
        ["updated_at"],
    )

    # ── patient_report_messages ──────────────────────────────────────────────
    op.create_table(
        "patient_report_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_report_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "is_report_summary",
            sa.Boolean,
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_patient_report_messages_session_id",
        "patient_report_messages",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_table("patient_report_messages")
    op.drop_table("patient_report_sessions")
