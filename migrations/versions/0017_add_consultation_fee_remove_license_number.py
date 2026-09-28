"""Add consultation_fee and drop license_number from doctor_profiles

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-27
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "doctor_profiles",
        sa.Column("consultation_fee", sa.Float(), nullable=True),
    )
    # Safely drop license_number column if present
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("doctor_profiles")]
    if "license_number" in columns:
        op.drop_column("doctor_profiles", "license_number")


def downgrade() -> None:
    op.drop_column("doctor_profiles", "consultation_fee")
    op.add_column(
        "doctor_profiles",
        sa.Column("license_number", sa.String(100), nullable=True),
    )
