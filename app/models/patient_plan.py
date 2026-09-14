"""
Patient Plan Maker Models.

Provides models for:
- Patient goals and explicit workflow state tracking
- Goal-specific questions, retry tracking, and normalized answers
- Structured AI wellness plans with optimistic locking (version)
- Daily schedule items and categories
- Multi-turn plan refinement discussions and pending modifications
- Immutable plan revisions for auditing
- Daily activity logs with deduplication
- Scheduled notification delivery records with idempotency constraints
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class PatientGoal(TimestampMixin, Base):
    """
    Represents a patient's health and wellness goal.
    Tracks explicit workflow states through the planning lifecycle.
    """

    __tablename__ = "patient_goals"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Foreign Key -> Patient
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Goal Details
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="custom",
    )  # weight_management, sleep_optimization, fitness_mobility, stress_reduction, nutrition, custom
    target_description: Mapped[str] = mapped_column(Text, nullable=False)

    # Explicit Workflow State:
    # GOAL_CREATED, QUESTIONNAIRE_ACTIVE, QUESTIONNAIRE_COMPLETED, PLAN_GENERATING,
    # PLAN_READY, PLAN_DISCUSSION, PLAN_APPROVAL_PENDING, ACTIVE, PAUSED, COMPLETED, CANCELLED, FAILED
    workflow_state: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="GOAL_CREATED",
        server_default="GOAL_CREATED",
        index=True,
    )

    timezone: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="UTC",
        server_default="UTC",
    )
    target_duration_weeks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=4,
        server_default="4",
    )

    # Relationships
    patient: Mapped["User"] = relationship("User", foreign_keys=[patient_id])
    questions: Mapped[List["PatientGoalQuestion"]] = relationship(
        "PatientGoalQuestion",
        back_populates="goal",
        cascade="all, delete-orphan",
        order_by="PatientGoalQuestion.order_index",
        lazy="selectin",
    )
    answers: Mapped[List["PatientGoalAnswer"]] = relationship(
        "PatientGoalAnswer",
        back_populates="goal",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    plans: Mapped[List["PatientPlan"]] = relationship(
        "PatientPlan",
        back_populates="goal",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<PatientGoal id={self.id} patient_id={self.patient_id} state={self.workflow_state}>"


class PatientGoalQuestion(Base):
    """
    A specific question configured or seeded for a patient goal questionnaire.
    Tracks retry attempts to prevent infinite re-ask loops.
    """

    __tablename__ = "patient_goal_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_goals.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    question_key: Mapped[str] = mapped_column(String(100), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="text",
    )  # number, select, text, time
    options: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=None)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default=None)
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    order_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    help_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    goal: Mapped["PatientGoal"] = relationship("PatientGoal", back_populates="questions")
    answers: Mapped[List["PatientGoalAnswer"]] = relationship(
        "PatientGoalAnswer",
        back_populates="question",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<PatientGoalQuestion id={self.id} key={self.question_key} retries={self.retry_count}>"


class PatientGoalAnswer(TimestampMixin, Base):
    """
    Stores raw and normalized answers to goal questions.
    Updated when a user refines or updates their response.
    """

    __tablename__ = "patient_goal_answers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_goals.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_goal_questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default=None)
    is_skipped: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    validation_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="valid",
        server_default="valid",
    )  # valid, clarification_needed, invalid, skipped
    clarification_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)

    # Relationships
    goal: Mapped["PatientGoal"] = relationship("PatientGoal", back_populates="answers")
    question: Mapped["PatientGoalQuestion"] = relationship("PatientGoalQuestion", back_populates="answers")

    __table_args__ = (
        UniqueConstraint("goal_id", "question_id", name="uq_goal_question_answer"),
    )

    def __repr__(self) -> str:
        return f"<PatientGoalAnswer id={self.id} question_id={self.question_id} status={self.validation_status}>"


class PatientPlan(TimestampMixin, Base):
    """
    Represents an AI-generated, server-validated health and wellness plan.
    Uses version numbers for optimistic concurrency locking.
    """

    __tablename__ = "patient_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_goals.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    target_duration_weeks: Mapped[int] = mapped_column(Integer, default=4, server_default="4")

    # Structured guidance
    diet_guidelines: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=None)
    lifestyle_guidelines: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=None)
    precautions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=None)

    # Optimistic locking version
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    # Status: draft, ready, active, paused, completed, cancelled
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="draft",
        server_default="draft",
        index=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Relationships
    goal: Mapped["PatientGoal"] = relationship("PatientGoal", back_populates="plans")
    patient: Mapped["User"] = relationship("User", foreign_keys=[patient_id])
    items: Mapped[List["PatientPlanItem"]] = relationship(
        "PatientPlanItem",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PatientPlanItem.order_index",
        lazy="selectin",
    )
    discussions: Mapped[List["PatientPlanDiscussion"]] = relationship(
        "PatientPlanDiscussion",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PatientPlanDiscussion.created_at",
        lazy="selectin",
    )
    revisions: Mapped[List["PatientPlanRevision"]] = relationship(
        "PatientPlanRevision",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PatientPlanRevision.revision_number",
        lazy="selectin",
    )
    logs: Mapped[List["PatientPlanLog"]] = relationship(
        "PatientPlanLog",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<PatientPlan id={self.id} title={self.title} status={self.status} version={self.version}>"


class PatientPlanItem(TimestampMixin, Base):
    """
    A scheduled activity item within a daily wellness plan.
    """

    __tablename__ = "patient_plan_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    time_of_day: Mapped[str] = mapped_column(String(10), nullable=False)  # HH:MM (24-hour)
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="general",
    )  # morning_routine, breakfast, workout, lunch, evening_activity, dinner, sleep_routine
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    # Relationships
    plan: Mapped["PatientPlan"] = relationship("PatientPlan", back_populates="items")
    logs: Mapped[List["PatientPlanLog"]] = relationship(
        "PatientPlanLog",
        back_populates="item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    notifications: Mapped[List["PatientPlanNotification"]] = relationship(
        "PatientPlanNotification",
        back_populates="item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<PatientPlanItem id={self.id} time={self.time_of_day} title={self.title}>"


class PatientPlanDiscussion(Base):
    """
    A single chat message in an interactive plan refinement conversation thread.
    Can attach structured proposed modifications awaiting confirmation.
    """

    __tablename__ = "patient_plan_discussions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_modifications: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=None
    )  # e.g., {"item_id": "...", "original_title": "...", "proposed_title": "...", "status": "pending"}
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationship
    plan: Mapped["PatientPlan"] = relationship("PatientPlan", back_populates="discussions")

    def __repr__(self) -> str:
        return f"<PatientPlanDiscussion id={self.id} plan_id={self.plan_id} role={self.role}>"


class PatientPlanRevision(Base):
    """
    Immutable historical audit snapshot of a plan whenever modified.
    """

    __tablename__ = "patient_plan_revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationship
    plan: Mapped["PatientPlan"] = relationship("PatientPlan", back_populates="revisions")

    __table_args__ = (
        UniqueConstraint("plan_id", "revision_number", name="uq_plan_revision_number"),
    )

    def __repr__(self) -> str:
        return f"<PatientPlanRevision id={self.id} plan_id={self.plan_id} rev={self.revision_number}>"


class PatientPlanLog(Base):
    """
    Daily completion log for an activity item in an active plan.
    Prevents duplicate completions on the same day via unique constraint.
    """

    __tablename__ = "patient_plan_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_plan_items.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="completed",
    )  # completed, skipped
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    plan: Mapped["PatientPlan"] = relationship("PatientPlan", back_populates="logs")
    item: Mapped["PatientPlanItem"] = relationship("PatientPlanItem", back_populates="logs")

    __table_args__ = (
        UniqueConstraint("plan_id", "item_id", "log_date", name="uq_plan_item_log_date"),
    )

    def __repr__(self) -> str:
        return f"<PatientPlanLog id={self.id} item_id={self.item_id} date={self.log_date} status={self.status}>"


class PatientPlanNotification(Base):
    """
    Tracks scheduled and dispatched daily reminder notifications.
    Unique constraint on (patient_id, item_id, scheduled_date) prevents duplicate sends.
    """

    __tablename__ = "patient_plan_notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_plan_items.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    scheduled_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )  # pending, sent, failed, missed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Relationships
    plan: Mapped["PatientPlan"] = relationship("PatientPlan")
    item: Mapped["PatientPlanItem"] = relationship("PatientPlanItem", back_populates="notifications")

    __table_args__ = (
        UniqueConstraint("patient_id", "item_id", "scheduled_date", name="uq_patient_item_notif_date"),
    )

    def __repr__(self) -> str:
        return f"<PatientPlanNotification id={self.id} item_id={self.item_id} date={self.scheduled_date} status={self.status}>"
