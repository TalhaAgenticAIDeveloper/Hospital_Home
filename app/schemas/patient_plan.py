"""
Pydantic schemas for Patient Plan Maker.

Covers:
- Goal creation & questionnaire responses
- Multi-tier validation feedback
- LLM structured output parsing & Pydantic validation
- Multi-turn plan refinement chat & deterministic modification payloads
- Daily activity completion logging
- Plan summaries & detail responses
"""

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ── Goal Schemas ─────────────────────────────────────────────────────────────

class CreateGoalRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=255, description="Brief title for the goal")
    category: str = Field(
        ...,
        description="Goal category (weight_management, sleep_optimization, fitness_mobility, stress_reduction, nutrition, custom)",
    )
    target_description: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="What the patient wishes to accomplish in their own words",
    )
    timezone: str = Field(default="UTC", max_length=100, description="Patient's local timezone name (e.g. UTC, Asia/Karachi, America/New_York)")
    target_duration_weeks: int = Field(default=4, ge=1, le=52, description="Target timeline in weeks")


class QuestionnaireAnswerRequest(BaseModel):
    question_id: uuid.UUID
    raw_input: str = Field(..., max_length=2000, description="Raw user input response")
    is_skipped: bool = Field(default=False, description="Flag indicating if the user explicitly chose to skip")


class QuestionAnswerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_id: uuid.UUID
    question_key: str
    validation_status: str  # valid, clarification_needed, invalid, skipped
    clarification_message: Optional[str] = None
    normalized_value: Optional[str] = None
    unit: Optional[str] = None
    retry_count: int
    is_skipped: bool
    can_proceed: bool


class GoalQuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question_key: str
    question_text: str
    question_type: str
    options: Optional[Any] = None
    unit: Optional[str] = None
    is_required: bool
    order_index: int
    retry_count: int
    help_text: Optional[str] = None
    current_answer: Optional[Dict[str, Any]] = None


class PatientGoalDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    target_description: str
    workflow_state: str
    timezone: str
    target_duration_weeks: int
    questions: List[GoalQuestionResponse]
    is_questionnaire_completed: bool
    active_plan_id: Optional[uuid.UUID] = None
    created_at: datetime


# ── Plan & Schedule Schemas ──────────────────────────────────────────────────

class PlanItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[uuid.UUID] = None
    time_of_day: str = Field(
        ...,
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="24-hour time string in HH:MM format",
    )
    category: str = Field(
        ...,
        description="Item category (morning_routine, breakfast, workout, lunch, evening_activity, dinner, sleep_routine, general)",
    )
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=1000)
    order_index: int = 0
    is_active: bool = True

    # Nutritional metadata
    calories: Optional[int] = Field(default=None, description="Kilocalories for meals")
    protein_g: Optional[float] = Field(default=None, description="Grams of protein")
    carbs_g: Optional[float] = Field(default=None, description="Grams of carbohydrates")
    fat_g: Optional[float] = Field(default=None, description="Grams of fat")
    fiber_g: Optional[float] = Field(default=None, description="Grams of dietary fiber")
    calories_burned: Optional[int] = Field(default=None, description="Calories burned for exercise items")


class GeneratedPlanPayload(BaseModel):
    """Schema for validating AI-generated plan JSON before DB persistence."""
    title: str = Field(..., min_length=3, max_length=255)
    summary: str = Field(..., min_length=10, max_length=3000)
    target_duration_weeks: int = Field(default=4, ge=1, le=52)
    diet_guidelines: List[str] = Field(default_factory=list)
    lifestyle_guidelines: List[str] = Field(default_factory=list)
    precautions: List[str] = Field(default_factory=list)
    schedule_items: List[PlanItemSchema] = Field(..., min_length=1)
    daily_nutrition_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Aggregate daily nutrition totals (total_calories, total_protein_g, total_carbs_g, total_fat_g, total_fiber_g, total_calories_burned, net_calories)",
    )


class ProposedModificationSchema(BaseModel):
    item_id: Optional[str] = None
    original_title: Optional[str] = None
    proposed_title: str
    proposed_description: str
    proposed_time: Optional[str] = None
    proposed_category: Optional[str] = None
    status: str = "pending"  # pending, applied, rejected
    calories: Optional[int] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    calories_burned: Optional[int] = None


class PlanDiscussionMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="Message to the AI plan assistant")


class PlanDiscussionMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    proposed_modifications: Optional[Any] = None
    created_at: datetime


class ApplyPlanModificationRequest(BaseModel):
    action: str = Field(default="accept", pattern=r"^(accept|reject)$")
    expected_version: int = Field(..., description="Optimistic locking version check")
    modification: Optional[ProposedModificationSchema] = None


class LogActivityRequest(BaseModel):
    item_id: uuid.UUID
    log_date: date
    status: str = Field(default="completed", pattern=r"^(completed|skipped)$")
    notes: Optional[str] = Field(default=None, max_length=500)


class PlanLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    log_date: date
    status: str
    notes: Optional[str] = None
    logged_at: datetime


class PatientPlanDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal_id: uuid.UUID
    title: str
    summary: str
    target_duration_weeks: int
    diet_guidelines: Optional[Any] = None
    lifestyle_guidelines: Optional[Any] = None
    precautions: Optional[Any] = None
    version: int
    status: str
    approved_at: Optional[datetime] = None
    items: List[PlanItemSchema]
    discussions: List[PlanDiscussionMessageResponse]
    today_logs: List[PlanLogResponse]
    daily_nutrition_summary: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class PatientPlanSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal_id: uuid.UUID
    title: str
    status: str
    target_duration_weeks: int
    approved_at: Optional[datetime] = None
    created_at: datetime
