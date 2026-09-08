"""add ai_summary fields to patient_documents table

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patient_documents",
        sa.Column("ai_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "patient_documents",
        sa.Column("ai_summary_status", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "patient_documents",
        sa.Column("ai_summary_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("patient_documents", "ai_summary_generated_at")
    op.drop_column("patient_documents", "ai_summary_status")
    op.drop_column("patient_documents", "ai_summary")
