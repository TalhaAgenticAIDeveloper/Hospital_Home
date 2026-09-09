"""add patient_profiles table

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("date_of_birth", sa.String(length=20), nullable=True),
        sa.Column("gender", sa.String(length=20), nullable=True),
        sa.Column("blood_group", sa.String(length=10), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column(
            "is_completed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_patient_profiles_user_id"),
        "patient_profiles",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_patient_profiles_user_id"), table_name="patient_profiles")
    op.drop_table("patient_profiles")
