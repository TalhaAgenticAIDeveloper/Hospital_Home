"""add email_verifications table for OTP-based email verification

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False, index=True),
        sa.Column("otp_hash", sa.String(256), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False, comment="signup | reset_password"),
        sa.Column("is_used", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Composite index for fast lookups
    op.create_index(
        "ix_email_verifications_email_purpose",
        "email_verifications",
        ["email", "purpose"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_verifications_email_purpose", table_name="email_verifications")
    op.drop_table("email_verifications")
