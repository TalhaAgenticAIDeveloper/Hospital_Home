"""add plan nutritional metadata and daily nutrition summary

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add daily_nutrition_summary JSONB to patient_plans
    op.add_column(
        "patient_plans",
        sa.Column("daily_nutrition_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # Add nutritional fields to patient_plan_items
    op.add_column(
        "patient_plan_items",
        sa.Column("calories", sa.Integer(), nullable=True),
    )
    op.add_column(
        "patient_plan_items",
        sa.Column("protein_g", sa.Float(), nullable=True),
    )
    op.add_column(
        "patient_plan_items",
        sa.Column("carbs_g", sa.Float(), nullable=True),
    )
    op.add_column(
        "patient_plan_items",
        sa.Column("fat_g", sa.Float(), nullable=True),
    )
    op.add_column(
        "patient_plan_items",
        sa.Column("fiber_g", sa.Float(), nullable=True),
    )
    op.add_column(
        "patient_plan_items",
        sa.Column("calories_burned", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("patient_plan_items", "calories_burned")
    op.drop_column("patient_plan_items", "fiber_g")
    op.drop_column("patient_plan_items", "fat_g")
    op.drop_column("patient_plan_items", "carbs_g")
    op.drop_column("patient_plan_items", "protein_g")
    op.drop_column("patient_plan_items", "calories")
    op.drop_column("patient_plans", "daily_nutrition_summary")
