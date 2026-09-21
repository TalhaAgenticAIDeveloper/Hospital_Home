"""
Repository for Patient Plan Maker database operations.

Provides isolated, transactional data access for:
- Patient goals and explicit workflow states
- Questionnaire questions, retry counts, and normalized answers
- Plans, optimistic locking versioning, and revisions
- Daily schedule items and modifications
- Discussions and proposed adjustments
- Daily completion logs and notification tracking
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.patient_plan import (
    PatientGoal,
    PatientGoalAnswer,
    PatientGoalQuestion,
    PatientPlan,
    PatientPlanDiscussion,
    PatientPlanItem,
    PatientPlanLog,
    PatientPlanNotification,
    PatientPlanRevision,
)


class PatientPlanRepository:
    """Centralized database access layer for Patient Health & Wellness Plans."""

    # ── Goals ────────────────────────────────────────────────────────────────

    @staticmethod
    async def create_goal(session: AsyncSession, goal: PatientGoal) -> PatientGoal:
        """Persist a new patient goal."""
        session.add(goal)
        await session.flush()
        await session.refresh(goal)
        return goal

    @staticmethod
    async def get_goal_by_id(
        session: AsyncSession,
        goal_id: uuid.UUID,
        patient_id: Optional[uuid.UUID] = None,
    ) -> Optional[PatientGoal]:
        """Fetch goal by ID with preloaded questions and answers."""
        query = (
            select(PatientGoal)
            .options(
                selectinload(PatientGoal.questions),
                selectinload(PatientGoal.answers),
                selectinload(PatientGoal.plans),
            )
            .where(PatientGoal.id == goal_id)
        )
        if patient_id is not None:
            query = query.where(PatientGoal.patient_id == patient_id)

        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_latest_in_progress_goal(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> Optional[PatientGoal]:
        """Retrieve most recent goal that is in creation or questionnaire state."""
        in_progress_states = [
            "GOAL_CREATED",
            "QUESTIONNAIRE_ACTIVE",
            "QUESTIONNAIRE_COMPLETED",
            "PLAN_GENERATING",
            "PLAN_READY",
            "PLAN_DISCUSSION",
            "PLAN_APPROVAL_PENDING",
        ]
        query = (
            select(PatientGoal)
            .options(
                selectinload(PatientGoal.questions),
                selectinload(PatientGoal.answers),
                selectinload(PatientGoal.plans),
            )
            .where(
                and_(
                    PatientGoal.patient_id == patient_id,
                    PatientGoal.workflow_state.in_(in_progress_states),
                )
            )
            .order_by(PatientGoal.updated_at.desc())
        )
        result = await session.execute(query)
        return result.scalars().first()

    @staticmethod
    async def update_goal_state(
        session: AsyncSession,
        goal_id: uuid.UUID,
        new_state: str,
    ) -> None:
        """Update workflow state of a goal."""
        stmt = (
            update(PatientGoal)
            .where(PatientGoal.id == goal_id)
            .values(workflow_state=new_state, updated_at=func.now())
        )
        await session.execute(stmt)
        await session.flush()

    # ── Questions & Answers ──────────────────────────────────────────────────

    @staticmethod
    async def create_questions(
        session: AsyncSession,
        questions: List[PatientGoalQuestion],
    ) -> List[PatientGoalQuestion]:
        """Bulk persist seeded questionnaire questions."""
        session.add_all(questions)
        await session.flush()
        return questions

    @staticmethod
    async def get_question_by_id(
        session: AsyncSession,
        question_id: uuid.UUID,
    ) -> Optional[PatientGoalQuestion]:
        """Fetch a specific question by ID."""
        query = (
            select(PatientGoalQuestion)
            .options(selectinload(PatientGoalQuestion.answers))
            .where(PatientGoalQuestion.id == question_id)
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def increment_question_retry(
        session: AsyncSession,
        question_id: uuid.UUID,
    ) -> int:
        """Increment retry count on a question to prevent infinite loops."""
        q = await PatientPlanRepository.get_question_by_id(session, question_id)
        if q:
            q.retry_count += 1
            await session.flush()
            return q.retry_count
        return 0

    @staticmethod
    async def upsert_answer(
        session: AsyncSession,
        goal_id: uuid.UUID,
        question_id: uuid.UUID,
        raw_input: str,
        normalized_value: Optional[str] = None,
        unit: Optional[str] = None,
        is_skipped: bool = False,
        validation_status: str = "valid",
        clarification_message: Optional[str] = None,
    ) -> PatientGoalAnswer:
        """Insert or update patient's answer for a question in a goal."""
        query = select(PatientGoalAnswer).where(
            and_(
                PatientGoalAnswer.goal_id == goal_id,
                PatientGoalAnswer.question_id == question_id,
            )
        )
        result = await session.execute(query)
        answer = result.scalar_one_or_none()

        if answer:
            answer.raw_input = raw_input
            answer.normalized_value = normalized_value
            answer.unit = unit
            answer.is_skipped = is_skipped
            answer.validation_status = validation_status
            answer.clarification_message = clarification_message
        else:
            answer = PatientGoalAnswer(
                goal_id=goal_id,
                question_id=question_id,
                raw_input=raw_input,
                normalized_value=normalized_value,
                unit=unit,
                is_skipped=is_skipped,
                validation_status=validation_status,
                clarification_message=clarification_message,
            )
            session.add(answer)

        await session.flush()
        await session.refresh(answer)
        return answer

    # ── Plans & Schedule Items ───────────────────────────────────────────────

    @staticmethod
    async def create_plan(
        session: AsyncSession,
        plan: PatientPlan,
        items: List[PatientPlanItem],
    ) -> PatientPlan:
        """Persist a plan and its initial schedule items in one atomic operation."""
        session.add(plan)
        await session.flush()

        for idx, item in enumerate(items):
            item.plan_id = plan.id
            item.order_index = idx
            session.add(item)

        await session.flush()
        await session.refresh(plan)
        return plan

    @staticmethod
    async def get_plan_by_id(
        session: AsyncSession,
        plan_id: uuid.UUID,
        patient_id: Optional[uuid.UUID] = None,
    ) -> Optional[PatientPlan]:
        """Fetch plan with items, discussions, revisions, and logs."""
        query = (
            select(PatientPlan)
            .options(
                selectinload(PatientPlan.items),
                selectinload(PatientPlan.discussions),
                selectinload(PatientPlan.revisions),
                selectinload(PatientPlan.logs),
                selectinload(PatientPlan.goal),
            )
            .where(PatientPlan.id == plan_id)
        )
        if patient_id is not None:
            query = query.where(PatientPlan.patient_id == patient_id)

        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_active_plan(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> Optional[PatientPlan]:
        """Fetch the single active plan for a patient (if any)."""
        query = (
            select(PatientPlan)
            .options(
                selectinload(PatientPlan.items),
                selectinload(PatientPlan.discussions),
                selectinload(PatientPlan.logs),
                selectinload(PatientPlan.goal),
            )
            .where(
                and_(
                    PatientPlan.patient_id == patient_id,
                    PatientPlan.status == "active",
                )
            )
        )
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_plans_by_patient(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> List[PatientPlan]:
        """List all plans for a patient, ordered by newest first."""
        query = (
            select(PatientPlan)
            .options(selectinload(PatientPlan.items))
            .where(PatientPlan.patient_id == patient_id)
            .order_by(PatientPlan.created_at.desc())
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def add_discussion_message(
        session: AsyncSession,
        message: PatientPlanDiscussion,
    ) -> PatientPlanDiscussion:
        """Record a chat message in the plan discussion thread."""
        session.add(message)
        await session.flush()
        await session.refresh(message)
        return message

    @staticmethod
    async def create_revision(
        session: AsyncSession,
        plan_id: uuid.UUID,
        revision_number: int,
        change_summary: str,
        snapshot: Dict[str, Any],
    ) -> PatientPlanRevision:
        """Create an immutable revision record for auditing."""
        rev = PatientPlanRevision(
            plan_id=plan_id,
            revision_number=revision_number,
            change_summary=change_summary,
            snapshot=snapshot,
        )
        session.add(rev)
        await session.flush()
        return rev

    # ── Daily Activity Logs ──────────────────────────────────────────────────

    @staticmethod
    async def log_activity(
        session: AsyncSession,
        plan_id: uuid.UUID,
        item_id: uuid.UUID,
        patient_id: uuid.UUID,
        log_date: date,
        status: str = "completed",
        notes: Optional[str] = None,
    ) -> PatientPlanLog:
        """Upsert daily activity log."""
        query = select(PatientPlanLog).where(
            and_(
                PatientPlanLog.plan_id == plan_id,
                PatientPlanLog.item_id == item_id,
                PatientPlanLog.log_date == log_date,
            )
        )
        result = await session.execute(query)
        log = result.scalar_one_or_none()

        if log:
            log.status = status
            log.notes = notes
            log.logged_at = datetime.now(timezone.utc)
        else:
            log = PatientPlanLog(
                plan_id=plan_id,
                item_id=item_id,
                patient_id=patient_id,
                log_date=log_date,
                status=status,
                notes=notes,
            )
            session.add(log)

        await session.flush()
        await session.refresh(log)
        return log

    @staticmethod
    async def get_logs_for_date(
        session: AsyncSession,
        plan_id: uuid.UUID,
        log_date: date,
    ) -> List[PatientPlanLog]:
        """Fetch all activity completion logs for a given plan and date."""
        query = select(PatientPlanLog).where(
            and_(
                PatientPlanLog.plan_id == plan_id,
                PatientPlanLog.log_date == log_date,
            )
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    # ── Notification Tracking ────────────────────────────────────────────────

    @staticmethod
    async def record_notification_dispatch(
        session: AsyncSession,
        plan_id: uuid.UUID,
        item_id: uuid.UUID,
        patient_id: uuid.UUID,
        scheduled_time: datetime,
        scheduled_date: date,
        status: str,
        error_message: Optional[str] = None,
    ) -> Optional[PatientPlanNotification]:
        """Record notification send attempt with unique date deduplication."""
        query = select(PatientPlanNotification).where(
            and_(
                PatientPlanNotification.patient_id == patient_id,
                PatientPlanNotification.item_id == item_id,
                PatientPlanNotification.scheduled_date == scheduled_date,
            )
        )
        result = await session.execute(query)
        notif = result.scalar_one_or_none()

        if notif:
            # Already exists for today
            return notif

        notif = PatientPlanNotification(
            plan_id=plan_id,
            item_id=item_id,
            patient_id=patient_id,
            scheduled_time=scheduled_time,
            scheduled_date=scheduled_date,
            status=status,
            error_message=error_message,
            sent_at=datetime.now(timezone.utc) if status == "sent" else None,
        )
        session.add(notif)
        await session.flush()
        return notif

    # ── Plan & Goal Complete Deletion ────────────────────────────────────────

    @staticmethod
    async def delete_all_patient_plans_and_goals(
        session: AsyncSession,
        patient_id: uuid.UUID,
    ) -> int:
        """
        Permanently wipes all plans, goals, schedule items, discussions,
        logs, questions, and answers for a patient from the database.
        """
        # 1. Fetch and delete all goals (cascades to questions, answers, plans, items, etc.)
        goal_query = select(PatientGoal).where(PatientGoal.patient_id == patient_id)
        goal_result = await session.execute(goal_query)
        goals = list(goal_result.scalars().all())

        for goal in goals:
            await session.delete(goal)

        # 2. In case any orphaned plans exist directly tied to patient
        plan_query = select(PatientPlan).where(PatientPlan.patient_id == patient_id)
        plan_result = await session.execute(plan_query)
        plans = list(plan_result.scalars().all())

        for plan in plans:
            await session.delete(plan)

        await session.flush()
        return len(goals) + len(plans)
