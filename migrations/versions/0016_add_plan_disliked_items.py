"""Add disliked_items column to patient_plans

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patient_plans",
        sa.Column(
            "disliked_items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{\"items\": []}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("patient_plans", "disliked_items")
