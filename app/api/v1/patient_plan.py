"""
API Router for Patient Health & Wellness Plan Maker.

Strictly restricted to authenticated users with role PATIENT.
Provides endpoints for:
- Goal creation & questionnaire answering with friendly validation feedback
- AI plan generation with clinical safety validation
- Active plan inspection and historical list retrieval
- Multi-turn refinement chat with deterministic confirmation of proposed modifications
- Atomic plan approval & start
- Plan pause, resume, and cancellation
- Daily activity completion logging
- Standalone nutrition/food/exercise information queries
"""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.patient_plan import (
    ApplyPlanModificationRequest,
    CreateGoalRequest,
    LogActivityRequest,
    PatientGoalDetailResponse,
    PatientPlanDetailResponse,
    PatientPlanSummaryResponse,
    PlanDiscussionMessageRequest,
    PlanDiscussionMessageResponse,
    QuestionAnswerResponse,
    QuestionnaireAnswerRequest,
)
from app.schemas.nutrition_info import NutritionInfoResponse, NutritionQueryRequest
from app.repositories.patient_plan_repository import PatientPlanRepository
from app.services.nutrition_info_service import NutritionInfoService
from app.services.patient_plan_service import PatientPlanService

router = APIRouter(
    prefix="/patient/plans",
    tags=["Patient Health Plan Maker"],
)


@router.post(
    "/goals",
    response_model=PatientGoalDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new health and wellness goal",
)
async def create_patient_goal(
    payload: CreateGoalRequest,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientGoalDetailResponse:
    return await PatientPlanService.create_goal(
        session=session,
        patient_user=user,
        payload=payload,
    )


@router.get(
    "/goals/current",
    response_model=Optional[PatientGoalDetailResponse],
    summary="Get patient's current in-progress goal and questionnaire state",
)
async def get_current_goal(
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> Optional[PatientGoalDetailResponse]:
    return await PatientPlanService.get_current_goal(
        session=session,
        patient_user=user,
    )


@router.post(
    "/goals/{goal_id}/answers",
    response_model=QuestionAnswerResponse,
    summary="Submit or refine an answer to a questionnaire question",
)
async def submit_question_answer(
    goal_id: uuid.UUID,
    payload: QuestionnaireAnswerRequest,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> QuestionAnswerResponse:
    return await PatientPlanService.answer_question(
        session=session,
        patient_user=user,
        goal_id=goal_id,
        payload=payload,
    )


@router.post(
    "/goals/{goal_id}/generate",
    response_model=PatientPlanDetailResponse,
    summary="Generate a personalized wellness plan from completed questionnaire",
)
async def generate_patient_plan(
    goal_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientPlanDetailResponse:
    return await PatientPlanService.generate_plan(
        session=session,
        patient_user=user,
        goal_id=goal_id,
    )


@router.get(
    "/active",
    response_model=Optional[PatientPlanDetailResponse],
    summary="Get patient's current active plan and today's schedule",
)
async def get_active_plan(
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> Optional[PatientPlanDetailResponse]:
    return await PatientPlanService.get_active_plan(
        session=session,
        patient_user=user,
    )


@router.get(
    "",
    response_model=List[PatientPlanSummaryResponse],
    summary="List all past and current plans for the patient",
)
async def list_patient_plans(
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> List[PatientPlanSummaryResponse]:
    return await PatientPlanService.list_patient_plans(
        session=session,
        patient_user=user,
    )


@router.post(
    "/nutrition-info",
    response_model=NutritionInfoResponse,
    summary="Ask nutrition, food, or exercise information questions",
)
async def ask_nutrition_info(
    payload: NutritionQueryRequest,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> NutritionInfoResponse:
    conv_history = None
    if payload.plan_id:
        plan = await PatientPlanRepository.get_plan_by_id(session, payload.plan_id, user.id)
        if plan and plan.discussions:
            recent_msgs = plan.discussions[-6:] if len(plan.discussions) > 6 else plan.discussions
            conv_history = [
                {"role": m.role, "content": m.content}
                for m in recent_msgs if m.role in ("user", "assistant")
            ]
    return await NutritionInfoService.ask_nutrition_question(
        session=session,
        patient_user=user,
        question=payload.message,
        conversation_history=conv_history,
    )


@router.get(
    "/{plan_id}",
    response_model=PatientPlanDetailResponse,
    summary="Get complete plan details, schedule items, and discussions",
)
async def get_plan_detail(
    plan_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientPlanDetailResponse:
    return await PatientPlanService.get_plan_detail(
        session=session,
        patient_user=user,
        plan_id=plan_id,
    )


@router.post(
    "/{plan_id}/chat",
    response_model=PlanDiscussionMessageResponse,
    summary="Interactive multi-turn discussion or modification request for a plan",
)
async def chat_with_plan(
    plan_id: uuid.UUID,
    payload: PlanDiscussionMessageRequest,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PlanDiscussionMessageResponse:
    return await PatientPlanService.chat_with_plan(
        session=session,
        patient_user=user,
        plan_id=plan_id,
        message_text=payload.message,
    )


@router.post(
    "/{plan_id}/modifications/apply",
    response_model=PatientPlanDetailResponse,
    summary="Accept or decline a pending modification with optimistic lock checking",
)
async def apply_plan_modification(
    plan_id: uuid.UUID,
    payload: ApplyPlanModificationRequest,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientPlanDetailResponse:
    return await PatientPlanService.apply_modification(
        session=session,
        patient_user=user,
        plan_id=plan_id,
        payload=payload,
    )


@router.post(
    "/{plan_id}/approve",
    response_model=PatientPlanDetailResponse,
    summary="Approve and activate plan (provisions reminders)",
)
async def approve_plan(
    plan_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientPlanDetailResponse:
    return await PatientPlanService.approve_plan(
        session=session,
        patient_user=user,
        plan_id=plan_id,
    )


@router.post(
    "/{plan_id}/pause",
    response_model=PatientPlanDetailResponse,
    summary="Pause an active plan (stops upcoming reminders)",
)
async def pause_plan(
    plan_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientPlanDetailResponse:
    return await PatientPlanService.pause_plan(
        session=session,
        patient_user=user,
        plan_id=plan_id,
    )


@router.post(
    "/{plan_id}/resume",
    response_model=PatientPlanDetailResponse,
    summary="Resume a paused plan",
)
async def resume_plan(
    plan_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientPlanDetailResponse:
    return await PatientPlanService.resume_plan(
        session=session,
        patient_user=user,
        plan_id=plan_id,
    )


@router.post(
    "/{plan_id}/cancel",
    response_model=Dict[str, Any],
    summary="Cancel and completely remove plan and goal from database",
)
async def cancel_plan(
    plan_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    return await PatientPlanService.cancel_plan(
        session=session,
        patient_user=user,
        plan_id=plan_id,
    )


@router.post(
    "/{plan_id}/log",
    response_model=PatientPlanDetailResponse,
    summary="Log daily completion status for an activity item",
)
async def log_activity(
    plan_id: uuid.UUID,
    payload: LogActivityRequest,
    user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> PatientPlanDetailResponse:
    return await PatientPlanService.log_activity(
        session=session,
        patient_user=user,
        plan_id=plan_id,
        payload=payload,
    )
