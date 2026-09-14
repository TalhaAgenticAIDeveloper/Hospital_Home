"""add plan_maker tables: patient_goals, patient_goal_questions, patient_goal_answers, patient_plans, patient_plan_items, patient_plan_discussions, patient_plan_revisions, patient_plan_logs, patient_plan_notifications

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. patient_goals ──────────────────────────────────────────────────────
    op.create_table(
        "patient_goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False, server_default="custom"),
        sa.Column("target_description", sa.Text, nullable=False),
        sa.Column(
            "workflow_state",
            sa.String(50),
            nullable=False,
            server_default="GOAL_CREATED",
        ),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("target_duration_weeks", sa.Integer, nullable=False, server_default="4"),
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
    op.create_index("ix_patient_goals_patient_id", "patient_goals", ["patient_id"])
    op.create_index("ix_patient_goals_workflow_state", "patient_goals", ["workflow_state"])

    # ── 2. patient_goal_questions ─────────────────────────────────────────────
    op.create_table(
        "patient_goal_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_key", sa.String(100), nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("question_type", sa.String(50), nullable=False, server_default="text"),
        sa.Column("options", postgresql.JSONB, nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("help_text", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_patient_goal_questions_goal_id", "patient_goal_questions", ["goal_id"])

    # ── 3. patient_goal_answers ───────────────────────────────────────────────
    op.create_table(
        "patient_goal_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_goal_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_input", sa.Text, nullable=False),
        sa.Column("normalized_value", sa.Text, nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("is_skipped", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("validation_status", sa.String(50), nullable=False, server_default="valid"),
        sa.Column("clarification_message", sa.Text, nullable=True),
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
        sa.UniqueConstraint("goal_id", "question_id", name="uq_goal_question_answer"),
    )
    op.create_index("ix_patient_goal_answers_goal_id", "patient_goal_answers", ["goal_id"])
    op.create_index("ix_patient_goal_answers_question_id", "patient_goal_answers", ["question_id"])

    # ── 4. patient_plans ──────────────────────────────────────────────────────
    op.create_table(
        "patient_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("target_duration_weeks", sa.Integer, nullable=False, server_default="4"),
        sa.Column("diet_guidelines", postgresql.JSONB, nullable=True),
        sa.Column("lifestyle_guidelines", postgresql.JSONB, nullable=True),
        sa.Column("precautions", postgresql.JSONB, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_patient_plans_goal_id", "patient_plans", ["goal_id"])
    op.create_index("ix_patient_plans_patient_id", "patient_plans", ["patient_id"])
    op.create_index("ix_patient_plans_status", "patient_plans", ["status"])

    # ── 5. patient_plan_items ─────────────────────────────────────────────────
    op.create_table(
        "patient_plan_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("time_of_day", sa.String(10), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="general"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
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
    op.create_index("ix_patient_plan_items_plan_id", "patient_plan_items", ["plan_id"])

    # ── 6. patient_plan_discussions ───────────────────────────────────────────
    op.create_table(
        "patient_plan_discussions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("proposed_modifications", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_patient_plan_discussions_plan_id", "patient_plan_discussions", ["plan_id"])

    # ── 7. patient_plan_revisions ─────────────────────────────────────────────
    op.create_table(
        "patient_plan_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer, nullable=False),
        sa.Column("change_summary", sa.Text, nullable=False),
        sa.Column("snapshot", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("plan_id", "revision_number", name="uq_plan_revision_number"),
    )
    op.create_index("ix_patient_plan_revisions_plan_id", "patient_plan_revisions", ["plan_id"])

    # ── 8. patient_plan_logs ──────────────────────────────────────────────────
    op.create_table(
        "patient_plan_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("log_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "logged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("plan_id", "item_id", "log_date", name="uq_plan_item_log_date"),
    )
    op.create_index("ix_patient_plan_logs_plan_id", "patient_plan_logs", ["plan_id"])
    op.create_index("ix_patient_plan_logs_patient_id", "patient_plan_logs", ["patient_id"])

    # ── 9. patient_plan_notifications ─────────────────────────────────────────
    op.create_table(
        "patient_plan_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("patient_id", "item_id", "scheduled_date", name="uq_patient_item_notif_date"),
    )
    op.create_index("ix_patient_plan_notifications_plan_id", "patient_plan_notifications", ["plan_id"])
    op.create_index("ix_patient_plan_notifications_patient_id", "patient_plan_notifications", ["patient_id"])


def downgrade() -> None:
    op.drop_table("patient_plan_notifications")
    op.drop_table("patient_plan_logs")
    op.drop_table("patient_plan_revisions")
    op.drop_table("patient_plan_discussions")
    op.drop_table("patient_plan_items")
    op.drop_table("patient_plans")
    op.drop_table("patient_goal_answers")
    op.drop_table("patient_goal_questions")
    op.drop_table("patient_goals")
