"""separate saas_admin into dedicated table with single-admin lock

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create saas_admins table ──────────────────────────────────────────
    op.create_table(
        "saas_admins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), unique=True, index=True, nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("full_name", sa.String(255), server_default="SaaS Administrator", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("single_admin_lock", sa.Boolean(), server_default="true", unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── 2. Create admin_refresh_tokens table ─────────────────────────────────
    op.create_table(
        "admin_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("saas_admins.id", ondelete="CASCADE"), index=True, nullable=False),
        sa.Column("token_hash", sa.String(512), index=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), index=True, nullable=False),
        sa.Column("is_revoked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("device_info", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── 3. Data Migration: Copy existing saas_admin user into saas_admins ────
    op.execute("""
        INSERT INTO saas_admins (id, email, password_hash, full_name, is_active, last_login_at, single_admin_lock, created_at, updated_at)
        SELECT id, email, password_hash, 'SaaS Administrator', is_active, last_login_at, true, created_at, updated_at
        FROM users
        WHERE role = 'saas_admin'
        LIMIT 1
        ON CONFLICT (id) DO NOTHING;
    """)

    # ── 4. Retarget doctor_profiles.reviewed_by FK to saas_admins ────────────
    op.drop_constraint("doctor_profiles_reviewed_by_fkey", "doctor_profiles", type_="foreignkey")
    op.create_foreign_key(
        "doctor_profiles_reviewed_by_fkey",
        "doctor_profiles",
        "saas_admins",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── 5. Delete saas_admin users from users table ──────────────────────────
    op.execute("DELETE FROM users WHERE role = 'saas_admin';")


def downgrade() -> None:
    # Retarget doctor_profiles.reviewed_by back to users
    op.drop_constraint("doctor_profiles_reviewed_by_fkey", "doctor_profiles", type_="foreignkey")
    op.create_foreign_key(
        "doctor_profiles_reviewed_by_fkey",
        "doctor_profiles",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # Re-insert saas_admins back into users
    op.execute("""
        INSERT INTO users (id, email, password_hash, role, status, is_active, last_login_at, created_at, updated_at)
        SELECT id, email, password_hash, 'saas_admin', 'active', is_active, last_login_at, created_at, updated_at
        FROM saas_admins
        ON CONFLICT (id) DO NOTHING;
    """)

    op.drop_table("admin_refresh_tokens")
    op.drop_table("saas_admins")
