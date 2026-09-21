"""
Patient Plan Service.

Orchestrates the entire Health & Wellness Plan Maker lifecycle:
- Goal creation & dynamic / deterministic question seeding
- Step-by-step questionnaire processing, bounds checking, and retry counter limits (MAX_QUESTION_RETRIES = 3)
- Safe Groq LLM invocation with multi-tier validation (LLM -> JSON parser -> Pydantic -> Clinical safety rules -> DB)
- Multi-turn plan refinement chat with deterministic confirmation and optimistic concurrency locking
- Single active plan enforcement and atomic approvals
- Timezone-aware daily schedule reminder dispatch & activity completion tracking
"""

import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.patient_plan import (
    PatientGoal,
    PatientGoalAnswer,
    PatientGoalQuestion,
    PatientPlan,
    PatientPlanDiscussion,
    PatientPlanItem,
    PatientPlanLog,
    PatientPlanRevision,
)
from app.models.user import User
from app.repositories.patient_plan_repository import PatientPlanRepository
from app.schemas.patient_plan import (
    ApplyPlanModificationRequest,
    CreateGoalRequest,
    GeneratedPlanPayload,
    GoalQuestionResponse,
    LogActivityRequest,
    PatientGoalDetailResponse,
    PatientPlanDetailResponse,
    PatientPlanSummaryResponse,
    PlanDiscussionMessageResponse,
    PlanItemSchema,
    QuestionAnswerResponse,
    QuestionnaireAnswerRequest,
)
from app.services.plan_validator import PlanValidator
from app.services.reminder_scheduler import (
    cancel_plan_reminders,
    schedule_plan_reminders,
)

logger = get_logger(__name__)
settings = get_settings()

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_QUESTION_RETRIES = 3

# ── Goal-Tailored Question Banks (Deterministic & Safe Fallback) ─────────────
QUESTION_BANKS: Dict[str, List[Dict[str, Any]]] = {
    "weight_management": [
        {
            "question_key": "current_weight",
            "question_text": "What is your current weight?",
            "question_type": "number",
            "unit": "kg",
            "is_required": True,
            "help_text": "e.g., 70 kg or 154 lb",
        },
        {
            "question_key": "target_weight",
            "question_text": "What is your target weight goal?",
            "question_type": "number",
            "unit": "kg",
            "is_required": False,
            "help_text": "e.g., 65 kg (optional)",
        },
        {
            "question_key": "height",
            "question_text": "What is your height?",
            "question_type": "number",
            "unit": "cm",
            "is_required": False,
            "help_text": "e.g., 170 cm or 5'8\"",
        },
        {
            "question_key": "activity_level",
            "question_text": "How active are you in an average day?",
            "question_type": "select",
            "options": ["Sedentary (mostly desk work)", "Lightly active (light walking)", "Moderately active (workout 3-4x/week)", "Very active (daily vigorous exercise)"],
            "is_required": True,
            "help_text": "Choose your general activity level",
        },
        {
            "question_key": "wake_up_time",
            "question_text": "What time do you usually wake up?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 07:00 AM or after Fajr",
        },
        {
            "question_key": "bed_time",
            "question_text": "What time do you usually go to sleep?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 11:00 PM",
        },
        {
            "question_key": "dietary_restrictions",
            "question_text": "Do you follow any specific dietary patterns or restrictions?",
            "question_type": "text",
            "is_required": False,
            "help_text": "e.g., Vegetarian, Vegan, Halal, Low sodium, or None",
        },
        {
            "question_key": "food_allergies",
            "question_text": "Do you have any food allergies or severe intolerances?",
            "question_type": "text",
            "is_required": True,
            "help_text": "e.g., Peanuts, Dairy, Shellfish, Gluten, or None",
        },
    ],
    "sleep_optimization": [
        {
            "question_key": "bed_time",
            "question_text": "What time do you currently go to bed?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 11:30 PM",
        },
        {
            "question_key": "wake_up_time",
            "question_text": "What time do you usually wake up in the morning?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 07:00 AM",
        },
        {
            "question_key": "sleep_challenge",
            "question_text": "What is your primary sleep difficulty?",
            "question_type": "select",
            "options": ["Difficulty falling asleep", "Waking up repeatedly during night", "Waking up too early and feeling tired", "Irregular sleep schedule"],
            "is_required": True,
            "help_text": "Select the main issue you face",
        },
        {
            "question_key": "caffeine_intake",
            "question_text": "How much caffeine (tea, coffee, energy drinks) do you consume daily?",
            "question_type": "select",
            "options": ["None", "1 cup in morning only", "2-3 cups through the day", "Caffeine in the evening or night"],
            "is_required": True,
            "help_text": "Select your caffeine habit",
        },
        {
            "question_key": "evening_screen_time",
            "question_text": "Do you use your phone, laptop, or watch TV within 1 hour of sleep?",
            "question_type": "select",
            "options": ["Rarely or never", "Sometimes (15-30 mins)", "Consistently right up to sleep"],
            "is_required": False,
            "help_text": "Evening screen habits",
        },
        {
            "question_key": "food_allergies",
            "question_text": "Do you have any food allergies or food intolerances?",
            "question_type": "text",
            "is_required": True,
            "help_text": "e.g., None, Dairy, Nuts",
        },
    ],
    "fitness_mobility": [
        {
            "question_key": "current_fitness_level",
            "question_text": "What is your current fitness experience level?",
            "question_type": "select",
            "options": ["Beginner (little to no exercise)", "Intermediate (regular walks or workouts)", "Advanced (consistent athletic training)"],
            "is_required": True,
            "help_text": "Select your fitness level",
        },
        {
            "question_key": "exercise_preference",
            "question_text": "What types of physical activity do you enjoy most?",
            "question_type": "select",
            "options": ["Brisk walking / light jogging", "Bodyweight & home mobility exercises", "Gym weight training", "Yoga, stretching & pilates"],
            "is_required": True,
            "help_text": "Choose your preferred activity",
        },
        {
            "question_key": "daily_available_time",
            "question_text": "How many minutes can you dedicate to physical activity each day?",
            "question_type": "number",
            "unit": "minutes",
            "is_required": True,
            "help_text": "e.g., 30 minutes",
        },
        {
            "question_key": "physical_limitations",
            "question_text": "Do you have any joint pain, back pain, or physical limitations?",
            "question_type": "text",
            "is_required": False,
            "help_text": "e.g., Lower back stiffness, knee pain, or None",
        },
        {
            "question_key": "wake_up_time",
            "question_text": "What time do you wake up?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 06:30 AM",
        },
        {
            "question_key": "food_allergies",
            "question_text": "Any food allergies or dietary restrictions?",
            "question_type": "text",
            "is_required": True,
            "help_text": "e.g., None, Peanuts, Gluten",
        },
    ],
    "stress_reduction": [
        {
            "question_key": "primary_stressor",
            "question_text": "What are your primary daily stress triggers?",
            "question_type": "select",
            "options": ["Work & career demands", "Family & personal responsibilities", "Health worries", "General feeling of overwhelm"],
            "is_required": True,
            "help_text": "Select the main stress source",
        },
        {
            "question_key": "daily_relaxation_time",
            "question_text": "How much downtime or relaxation time do you get daily?",
            "question_type": "select",
            "options": ["Almost none (< 15 mins)", "Around 30 minutes", "1 hour or more"],
            "is_required": True,
            "help_text": "Choose your average downtime",
        },
        {
            "question_key": "wake_up_time",
            "question_text": "What time do you usually wake up?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 07:30 AM",
        },
        {
            "question_key": "bed_time",
            "question_text": "What time do you usually go to bed?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 11:00 PM",
        },
        {
            "question_key": "food_allergies",
            "question_text": "Do you have any food allergies?",
            "question_type": "text",
            "is_required": True,
            "help_text": "e.g., None, Shellfish, Nuts",
        },
    ],
    "nutrition": [
        {
            "question_key": "dietary_pattern",
            "question_text": "Which dietary pattern best describes your eating habits?",
            "question_type": "select",
            "options": ["Omnivore (eats all meats and plants)", "Vegetarian", "Vegan (plant-based only)", "Pescatarian (fish & plants)", "Halal only"],
            "is_required": True,
            "help_text": "Select your diet style",
        },
        {
            "question_key": "meals_per_day",
            "question_text": "How many meals do you typically eat in a day?",
            "question_type": "select",
            "options": ["2 meals (often skip breakfast)", "3 regular meals", "3 meals plus snacks"],
            "is_required": True,
            "help_text": "Select meal frequency",
        },
        {
            "question_key": "water_intake",
            "question_text": "How much water do you drink per day?",
            "question_type": "select",
            "options": ["Less than 1 liter", "1 to 2 liters", "More than 2 liters"],
            "is_required": False,
            "help_text": "Daily hydration level",
        },
        {
            "question_key": "food_allergies",
            "question_text": "Do you have any food allergies or severe intolerances?",
            "question_type": "text",
            "is_required": True,
            "help_text": "e.g., Peanuts, Dairy, Gluten, Soy, or None",
        },
        {
            "question_key": "food_dislikes",
            "question_text": "Are there any healthy foods you strongly dislike or avoid?",
            "question_type": "text",
            "is_required": False,
            "help_text": "e.g., Mushrooms, fish, eggs (optional)",
        },
        {
            "question_key": "wake_up_time",
            "question_text": "What time do you usually wake up?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 07:00 AM",
        },
    ],
    "custom": [
        {
            "question_key": "activity_level",
            "question_text": "How would you describe your daily physical activity?",
            "question_type": "select",
            "options": ["Sedentary", "Lightly active", "Moderately active", "Very active"],
            "is_required": True,
            "help_text": "Activity level",
        },
        {
            "question_key": "wake_up_time",
            "question_text": "What time do you typically wake up?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 07:00 AM",
        },
        {
            "question_key": "bed_time",
            "question_text": "What time do you typically go to sleep?",
            "question_type": "time",
            "is_required": True,
            "help_text": "e.g., 11:00 PM",
        },
        {
            "question_key": "food_allergies",
            "question_text": "Do you have any food allergies or dietary restrictions?",
            "question_type": "text",
            "is_required": True,
            "help_text": "e.g., Peanuts, Dairy, Gluten, or None",
        },
    ],
}


class PatientPlanService:
    """Core domain service for Patient Health & Wellness Plans."""

    # ── AI Helper ────────────────────────────────────────────────────────────

    @classmethod
    async def _call_groq_api(
        cls,
        messages: list,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        """Invokes Groq LLM API with strict error handling, timeout, and token cleanup."""
        api_key = settings.groq_api_key
        if not api_key:
            raise ValidationError(
                "Groq API key is not configured. Please set GROQ_API or GROQ_API_KEY in backend .env."
            )

        payload = {
            "model": settings.GROQ_MODEL or "llama-3.3-70b-versatile",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Explicit 45-second timeout
        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                resp = await client.post(GROQ_CHAT_COMPLETIONS_URL, json=payload, headers=headers)
            except httpx.TimeoutException:
                logger.error("Groq API timeout during plan operation")
                raise ValidationError("AI service timed out. Your answers are safely preserved. Please try again.")
            except httpx.RequestError as exc:
                logger.error(f"Groq API request error: {exc}")
                raise ValidationError("Could not connect to AI service. Please check your network and try again.")

            if resp.status_code != 200:
                logger.error(f"Groq API error ({resp.status_code}): {resp.text[:300]}")
                raise ValidationError("The AI service is temporarily busy. Please try again in a few moments.")

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise ValidationError("AI model returned an empty response.")

            raw_content = choices[0].get("message", {}).get("content", "").strip()
            # Clean reasoning <think> tags if Qwen/DeepSeek reasoning model
            cleaned = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
            if "<think>" in cleaned and "</think>" not in cleaned:
                cleaned = cleaned.split("<think>", 1)[0].strip()

            return cleaned or raw_content

    # ── Goals & Questionnaire ────────────────────────────────────────────────

    @classmethod
    async def create_goal(
        cls,
        session: AsyncSession,
        patient_user: User,
        payload: CreateGoalRequest,
    ) -> PatientGoalDetailResponse:
        """
        Creates a new patient goal and seeds the goal-tailored questions.
        Transitions state to QUESTIONNAIRE_ACTIVE.
        """
        category = payload.category if payload.category in QUESTION_BANKS else "custom"
        raw_questions = QUESTION_BANKS.get(category, QUESTION_BANKS["custom"])
        question_objects = []
        for idx, q in enumerate(raw_questions):
            question_objects.append(
                PatientGoalQuestion(
                    question_key=q["question_key"],
                    question_text=q["question_text"],
                    question_type=q["question_type"],
                    options={"items": q["options"]} if "options" in q else None,
                    unit=q.get("unit"),
                    is_required=q.get("is_required", True),
                    order_index=idx,
                    retry_count=0,
                    help_text=q.get("help_text"),
                )
            )

        # Enforce single plan per patient rule
        existing_plans = await PatientPlanRepository.list_plans_by_patient(session, patient_user.id)
        if existing_plans:
            existing = existing_plans[0]
            raise ValidationError(
                f"You already have an existing plan ('{existing.title}'). "
                "A patient can only have one plan at a time. "
                "Please cancel your current plan first to wipe it from the database before starting a new one."
            )

        # Clear any prior incomplete goal without a plan to keep database clean
        existing_goal = await PatientPlanRepository.get_latest_in_progress_goal(session, patient_user.id)
        if existing_goal and not existing_goal.plans:
            await session.delete(existing_goal)
            await session.flush()

        goal = PatientGoal(
            patient_id=patient_user.id,
            title=PlanValidator.sanitize_text(payload.title, 255),
            category=category,
            target_description=PlanValidator.sanitize_text(payload.target_description, 2000),
            timezone=PlanValidator.sanitize_text(payload.timezone, 100) or "UTC",
            target_duration_weeks=payload.target_duration_weeks,
            workflow_state="QUESTIONNAIRE_ACTIVE",
            questions=question_objects,
        )
        created_goal = await PatientPlanRepository.create_goal(session, goal)
        await session.commit()

        # Reload with questions and answers
        full_goal = await PatientPlanRepository.get_goal_by_id(session, created_goal.id, patient_user.id)
        return cls._format_goal_response(full_goal or created_goal)

    @classmethod
    async def get_current_goal(
        cls,
        session: AsyncSession,
        patient_user: User,
    ) -> Optional[PatientGoalDetailResponse]:
        """Fetch the patient's currently in-progress goal (if any)."""
        goal = await PatientPlanRepository.get_latest_in_progress_goal(session, patient_user.id)
        if not goal:
            return None
        return cls._format_goal_response(goal)

    @classmethod
    async def answer_question(
        cls,
        session: AsyncSession,
        patient_user: User,
        goal_id: uuid.UUID,
        payload: QuestionnaireAnswerRequest,
    ) -> QuestionAnswerResponse:
        """
        Processes and validates an answer to a goal questionnaire question.
        Handles:
        - Missing unit detection ("55" -> "Is that 55 kg or 55 lb?")
        - Bounds checking, irrelevant answers, and natural language
        - Retry counting with MAX_QUESTION_RETRIES
        - Multi-field extraction
        - Automatic questionnaire completion state transition
        """
        goal = await PatientPlanRepository.get_goal_by_id(session, goal_id, patient_user.id)
        if not goal:
            raise NotFoundError("Goal not found or does not belong to you.")

        question = await PatientPlanRepository.get_question_by_id(session, payload.question_id)
        if not question or question.goal_id != goal.id:
            raise NotFoundError("Question not found for this goal.")

        # Run multi-layer validator
        val_res = PlanValidator.validate_question_answer(
            question_key=question.question_key,
            question_type=question.question_type,
            raw_input=payload.raw_input,
            is_skipped=payload.is_skipped,
            retry_count=question.retry_count,
            expected_unit=question.unit,
        )

        if val_res.status in ("invalid", "clarification_needed"):
            # Increment retry counter on question
            new_retries = await PatientPlanRepository.increment_question_retry(session, question.id)
            # Save clarification answer record
            await PatientPlanRepository.upsert_answer(
                session=session,
                goal_id=goal.id,
                question_id=question.id,
                raw_input=payload.raw_input,
                normalized_value=None,
                unit=None,
                is_skipped=False,
                validation_status=val_res.status,
                clarification_message=val_res.message,
            )
            await session.commit()
            return QuestionAnswerResponse(
                question_id=question.id,
                question_key=question.question_key,
                validation_status=val_res.status,
                clarification_message=val_res.message,
                normalized_value=None,
                unit=None,
                retry_count=new_retries,
                is_skipped=False,
                can_proceed=False,
            )

        # Valid or safely skipped answer
        saved_answer = await PatientPlanRepository.upsert_answer(
            session=session,
            goal_id=goal.id,
            question_id=question.id,
            raw_input=payload.raw_input,
            normalized_value=val_res.normalized_value,
            unit=val_res.unit,
            is_skipped=(val_res.status == "skipped"),
            validation_status=val_res.status,
            clarification_message=val_res.message,
        )

        # If multi-field answer extracted extra values (e.g. weight and height in one sentence),
        # fill out other questions automatically if present!
        if val_res.extracted_fields:
            for extra_key, extra_val in val_res.extracted_fields.items():
                matching_q = next((q for q in goal.questions if q.question_key == extra_key and q.id != question.id), None)
                if matching_q:
                    await PatientPlanRepository.upsert_answer(
                        session=session,
                        goal_id=goal.id,
                        question_id=matching_q.id,
                        raw_input=payload.raw_input,
                        normalized_value=str(extra_val),
                        unit=matching_q.unit,
                        is_skipped=False,
                        validation_status="valid",
                        clarification_message=None,
                    )

        # Check questionnaire completion
        await session.flush()
        # Reload goal with updated answers
        refreshed_goal = await PatientPlanRepository.get_goal_by_id(session, goal.id, patient_user.id)
        answered_keys = {
            a.question_id for a in refreshed_goal.answers
            if a.validation_status in ("valid", "skipped")
        }
        all_required_done = all(
            (not q.is_required or q.id in answered_keys)
            for q in refreshed_goal.questions
        )

        if all_required_done and refreshed_goal.workflow_state == "QUESTIONNAIRE_ACTIVE":
            await PatientPlanRepository.update_goal_state(session, goal.id, "QUESTIONNAIRE_COMPLETED")

        await session.commit()

        return QuestionAnswerResponse(
            question_id=question.id,
            question_key=question.question_key,
            validation_status=saved_answer.validation_status,
            clarification_message=saved_answer.clarification_message,
            normalized_value=saved_answer.normalized_value,
            unit=saved_answer.unit,
            retry_count=question.retry_count,
            is_skipped=saved_answer.is_skipped,
            can_proceed=True,
        )

    # ── AI Plan Generation ───────────────────────────────────────────────────

    @classmethod
    async def generate_plan(
        cls,
        session: AsyncSession,
        patient_user: User,
        goal_id: uuid.UUID,
    ) -> PatientPlanDetailResponse:
        """
        Generates a personalized daily wellness routine and dietary guidelines.
        Guarantees:
        - Idempotent execution (prevents duplicate generation on double-click)
        - Never trust AI output directly: parses JSON -> validates Pydantic schema -> runs clinical safety checks
        - Preserves questionnaire answers if generation fails
        - Never prescribes medications
        - Never violates declared allergies
        """
        goal = await PatientPlanRepository.get_goal_by_id(session, goal_id, patient_user.id)
        if not goal:
            raise NotFoundError("Goal not found or does not belong to you.")

        # Enforce single plan per patient rule (ensure no other plans exist outside this goal)
        existing_plans = await PatientPlanRepository.list_plans_by_patient(session, patient_user.id)
        conflicting_plans = [p for p in existing_plans if p.goal_id != goal.id]
        if conflicting_plans:
            raise ValidationError(
                f"You already have an existing plan ('{conflicting_plans[0].title}'). "
                "A patient can only have one plan at a time. Please cancel your existing plan before creating a new one."
            )

        # Idempotency check: if plan already exists in 'ready' or 'active', return existing plan
        for existing in goal.plans:
            if existing.status in ("ready", "active"):
                full_plan = await PatientPlanRepository.get_plan_by_id(session, existing.id, patient_user.id)
                return cls._format_plan_response(full_plan)

        # Transition state to PLAN_GENERATING
        await PatientPlanRepository.update_goal_state(session, goal.id, "PLAN_GENERATING")
        await session.commit()

        # Build answer context and extract declared allergies
        qa_lines = []
        declared_allergies = []
        declared_restrictions = []

        for q in goal.questions:
            ans = next((a for a in goal.answers if a.question_id == q.id), None)
            ans_val = ans.normalized_value or ans.raw_input if ans else "Not provided"
            qa_lines.append(f"- {q.question_text}: {ans_val}")

            if ans and ans.validation_status == "valid":
                if q.question_key == "food_allergies" and ans_val.lower() not in ("none", "no", "n/a"):
                    declared_allergies.extend([x.strip() for x in re.split(r"[,;]\s*", ans_val)])
                if q.question_key == "dietary_restrictions" and ans_val.lower() not in ("none", "no", "n/a"):
                    declared_restrictions.extend([x.strip() for x in re.split(r"[,;]\s*", ans_val)])

        qa_context = "\n".join(qa_lines)
        allergy_context = ", ".join(declared_allergies) if declared_allergies else "None"

        system_prompt = (
            "You are an expert, compassionate clinical wellness and preventive lifestyle advisor. "
            "Your role is to create a structured, highly personalized daily health routine and dietary guidance "
            "based strictly on the patient's verified goal and questionnaire responses.\n\n"
            "CRITICAL MEDICAL & SAFETY RULES:\n"
            "1. NO MEDICINES OR PRESCRIPTION DRUGS: You are strictly forbidden from prescribing, recommending, or adjusting any "
            "prescription medications, over-the-counter drugs, pills, tablets, syrups, or clinical dosages (e.g., insulin, metformin, statins, painkillers). "
            "Even if the patient's goal or questionnaire mentions medical symptoms, diseases, or requested medications, NEVER prescribe or recommend medicines. "
            "Instead, provide 100% natural, dietary, nutritional, and lifestyle alternatives (e.g. whole foods, hydration, herbal teas, physical activity, sleep hygiene).\n"
            "2. STRICT ALLERGY RESPECT: Never include any food, ingredient, or snack that conflicts with the patient's declared allergies.\n"
            "3. REALISTIC & GROUNDED: Every activity must have realistic timing, sensible nutrition, and manageable habits.\n"
            "4. OUTPUT FORMAT: You must return ONLY valid, raw JSON matching the required schema. Do NOT include markdown fences, preambles, or explanations.\n\n"
            "NUTRITIONAL DATA & WEIGHT IMPACT REQUIREMENT:\n"
            "For EVERY meal/food schedule item, you MUST include accurate nutritional estimates:\n"
            "- calories (integer, kcal for the described meal/snack)\n"
            "- protein_g (float, grams of protein)\n"
            "- carbs_g (float, grams of carbohydrates)\n"
            "- fat_g (float, grams of fat)\n"
            "- fiber_g (float, grams of dietary fiber)\n"
            "- calories_burned: null (set to null for food items)\n\n"
            "For EVERY exercise/workout schedule item:\n"
            "- calories: null (set to null for exercise items)\n"
            "- protein_g: null, carbs_g: null, fat_g: null, fiber_g: null\n"
            "- calories_burned (integer, estimated kcal burned for a ~70kg person)\n\n"
            "For non-food/non-exercise items (morning_routine, sleep_routine), set ALL nutrition fields to null.\n\n"
            "CRITICAL: WEIGHT GAIN / LOSS & CALORIC IMPACT IN DESCRIPTIONS:\n"
            "If the plan is for weight management (weight gain or weight loss), fitness, or nutrition, you MUST explicitly state the caloric and weight impact in each item's description:\n"
            "- For meals: mention calories, protein, and how it contributes to a caloric surplus (for weight gain) or deficit (for weight loss), e.g., 'Provides 450 kcal and 25g protein, creating a healthy +300 kcal surplus to support lean muscle gain' or 'Provides 280 kcal and 20g protein, creating a 350 kcal deficit to promote gradual fat loss while keeping you energized'.\n"
            "- For exercises: mention estimated calories burned and expected loss/burn impact, e.g., 'Burns approx 220 kcal, directly contributing to your daily fat loss deficit'.\n\n"
            "You MUST also provide a 'daily_nutrition_summary' object with aggregate totals.\n\n"
            "JSON SCHEMA REQUIREMENT:\n"
            "{\n"
            '  "title": "Title of the Personalized Plan",\n'
            '  "summary": "2-3 supportive sentences explaining how this plan achieves their goal",\n'
            '  "target_duration_weeks": 4,\n'
            '  "diet_guidelines": ["guideline 1", "guideline 2", "guideline 3"],\n'
            '  "lifestyle_guidelines": ["habit 1", "habit 2"],\n'
            '  "precautions": ["precaution 1 (e.g. consult doctor before starting high-intensity exercise)"],\n'
            '  "schedule_items": [\n'
            '    {\n'
            '      "time_of_day": "HH:MM",\n'
            '      "category": "morning_routine | breakfast | workout | lunch | evening_activity | dinner | sleep_routine",\n'
            '      "title": "Actionable title",\n'
            '      "description": "Clear guidance and dietary instructions",\n'
            '      "calories": 350,\n'
            '      "protein_g": 12.0,\n'
            '      "carbs_g": 55.0,\n'
            '      "fat_g": 8.0,\n'
            '      "fiber_g": 6.0,\n'
            '      "calories_burned": null\n'
            '    }\n'
            '  ],\n'
            '  "daily_nutrition_summary": {\n'
            '    "total_calories": 2100,\n'
            '    "total_protein_g": 120.0,\n'
            '    "total_carbs_g": 250.0,\n'
            '    "total_fat_g": 65.0,\n'
            '    "total_fiber_g": 30.0,\n'
            '    "total_calories_burned": 350,\n'
            '    "net_calories": 1750\n'
            '  }\n'
            "}"
        )

        user_prompt = (
            f"GOAL: {goal.title} ({goal.category})\n"
            f"TARGET DESCRIPTION: {goal.target_description}\n"
            f"TIMEZONE: {goal.timezone}\n"
            f"DECLARED ALLERGIES: {allergy_context}\n\n"
            f"<<< PATIENT QUESTIONNAIRE DATA >>>\n"
            f"{qa_context}\n"
            f"<<< END PATIENT QUESTIONNAIRE DATA >>>\n\n"
            "Generate their personalized daily wellness plan JSON now."
        )

        # Call LLM with retry
        plan_payload: Optional[GeneratedPlanPayload] = None
        last_error = ""

        for attempt in range(2):
            try:
                raw_response = await cls._call_groq_api(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    max_tokens=2048,
                )

                # Extract JSON block
                json_str = raw_response.strip()
                if "```json" in json_str:
                    json_str = json_str.split("```json", 1)[1].split("```", 1)[0].strip()
                elif "```" in json_str:
                    json_str = json_str.split("```", 1)[1].split("```", 1)[0].strip()

                parsed_dict = json.loads(json_str)
                plan_payload = GeneratedPlanPayload(**parsed_dict)

                # Clinical safety validation
                is_valid, validation_errors = PlanValidator.validate_generated_plan(
                    plan_payload,
                    declared_allergies=declared_allergies,
                )
                if not is_valid:
                    logger.warning(f"Plan validation failed on attempt {attempt + 1}: {validation_errors}")
                    last_error = "; ".join(validation_errors)
                    # Add error to prompt for second attempt
                    user_prompt += f"\n\nPREVIOUS GENERATION HAD ERRORS: {last_error}. Please fix these issues strictly."
                    plan_payload = None
                    continue

                break
            except (json.JSONDecodeError, Exception) as exc:
                logger.error(f"Plan generation error on attempt {attempt + 1}: {exc}")
                last_error = str(exc)
                plan_payload = None

        if not plan_payload:
            # Revert goal state to QUESTIONNAIRE_COMPLETED so user answers are preserved and they can try again
            await PatientPlanRepository.update_goal_state(session, goal.id, "QUESTIONNAIRE_COMPLETED")
            await session.commit()
            raise ValidationError(
                f"We could not generate a safe plan right now ({last_error[:150]}). "
                "Your answers are safely preserved. Please click 'Try Again'."
            )

        # Persist Plan and Schedule Items in one transaction
        try:
            plan = PatientPlan(
                goal_id=goal.id,
                patient_id=patient_user.id,
                title=PlanValidator.sanitize_text(plan_payload.title, 255),
                summary=PlanValidator.sanitize_text(plan_payload.summary, 3000),
                target_duration_weeks=plan_payload.target_duration_weeks,
                diet_guidelines={"items": plan_payload.diet_guidelines},
                lifestyle_guidelines={"items": plan_payload.lifestyle_guidelines},
                precautions={"items": plan_payload.precautions},
                daily_nutrition_summary=plan_payload.daily_nutrition_summary,
                version=1,
                status="ready",
            )
            items = [
                PatientPlanItem(
                    time_of_day=item.time_of_day,
                    category=item.category,
                    title=PlanValidator.sanitize_text(item.title, 255),
                    description=PlanValidator.sanitize_text(item.description, 1000),
                    order_index=idx,
                    is_active=True,
                    calories=item.calories,
                    protein_g=item.protein_g,
                    carbs_g=item.carbs_g,
                    fat_g=item.fat_g,
                    fiber_g=item.fiber_g,
                    calories_burned=item.calories_burned,
                )
                for idx, item in enumerate(plan_payload.schedule_items)
            ]

            created_plan = await PatientPlanRepository.create_plan(session, plan, items)

            # Create initial revision record
            await PatientPlanRepository.create_revision(
                session=session,
                plan_id=created_plan.id,
                revision_number=1,
                change_summary="Initial personalized plan generated by AI based on questionnaire.",
                snapshot=plan_payload.model_dump(),
            )

            # Add welcome discussion message
            welcome_msg = PatientPlanDiscussion(
                plan_id=created_plan.id,
                role="assistant",
                content=(
                    f"Welcome to your personalized **{created_plan.title}**!\n\n"
                    "Review your daily schedule and guidelines above. If you'd like to adjust any meal, "
                    "swap an ingredient, or shift your workout time, just ask here in the chat. "
                    "When you're happy with the routine, click **Approve & Start Plan** to begin your daily journey!"
                ),
            )
            await PatientPlanRepository.add_discussion_message(session, welcome_msg)

            # Update goal state to PLAN_READY
            await PatientPlanRepository.update_goal_state(session, goal.id, "PLAN_READY")
            await session.commit()

            full_plan = await PatientPlanRepository.get_plan_by_id(session, created_plan.id, patient_user.id)
            return cls._format_plan_response(full_plan)
        except Exception as exc:
            await session.rollback()
            logger.error(f"Failed to persist generated plan: {exc}")
            await PatientPlanRepository.update_goal_state(session, goal.id, "QUESTIONNAIRE_COMPLETED")
            await session.commit()
            raise ValidationError("A database error occurred while saving your plan. Please try again.")

    # ── Plan Discussions & Deterministic Refinement ──────────────────────────

    @classmethod
    async def chat_with_plan(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
        message_text: str,
    ) -> PlanDiscussionMessageResponse:
        """
        Interactive discussion about the plan.
        Features:
        - Deterministic confirmation ('yes', 'accept', 'sure') directly applies pending modifications without LLM cost
        - Rejection ('no', 'cancel') clears pending modification
        - Complete goal change requests redirected safely
        - Rejects medication prescription attempts
        - Grounds answers in current database plan state
        """
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if not plan:
            raise NotFoundError("Plan not found or does not belong to you.")

        clean_text = PlanValidator.sanitize_text(message_text, 2000).strip()
        if not clean_text:
            raise ValidationError("Message cannot be empty.")

        # Save user message
        user_msg = PatientPlanDiscussion(
            plan_id=plan.id,
            role="user",
            content=clean_text,
        )
        await PatientPlanRepository.add_discussion_message(session, user_msg)
        plan.discussions.append(user_msg)

        lowered = clean_text.lower()

        # 0. Smart Intent Detection — Route nutrition info queries to NutritionInfoService
        intent = cls._classify_chat_intent(clean_text)
        if intent == "nutrition_info":
            # Lazy import to avoid circular dependency
            from app.services.nutrition_info_service import NutritionInfoService

            # Build conversation context from recent plan discussions
            recent_msgs = plan.discussions[-6:] if len(plan.discussions) > 6 else plan.discussions
            conv_history = [
                {"role": m.role, "content": m.content}
                for m in recent_msgs if m.role in ("user", "assistant")
            ]

            info_response = await NutritionInfoService.ask_nutrition_question(
                session=session,
                patient_user=patient_user,
                question=clean_text,
                conversation_history=conv_history,
            )

            # Save nutrition info response as a discussion message in the plan chat (seamless UX)
            assistant_msg = PatientPlanDiscussion(
                plan_id=plan.id,
                role="assistant",
                content=info_response.answer,
            )
            await PatientPlanRepository.add_discussion_message(session, assistant_msg)
            plan.discussions.append(assistant_msg)
            await session.commit()
            await session.refresh(assistant_msg)
            return cls._format_discussion_response(assistant_msg)

        # 1. Deterministic Positive Confirmation
        is_positive_confirmation = (
            lowered in (
                "yes", "accept", "sure", "okay", "ok", "confirm", "apply", "change it",
                "yes please", "do it", "go ahead", "sounds good", "perfect",
                "theek hai", "theek hy", "haan", "haan theek hy", "haan theek hai",
                "kr do", "kar do", "haan kr do", "haan kar do", "apply it", "apply changes",
                "apply all", "adjust it", "adjust all", "adjust them",
            )
            or (lowered.startswith("yes") and any(w in lowered for w in ("apply", "change", "please", "do it", "sure")))
            or ("apply" in lowered and any(w in lowered for w in ("change", "this", "modification", "it", "plan", "all", "these")))
            or ("adjust" in lowered and any(w in lowered for w in ("all", "it", "them", "these", "plan")))
            or any(u in lowered for u in ("theek hai", "theek hy", "kr do", "kar do", "haan kr"))
            or lowered.startswith("confirm")
            or lowered.startswith("accept")
        )
        if is_positive_confirmation:
            item_mod_pairs, pending_data, pending_disc = cls._find_pending_modification(plan)
            if item_mod_pairs and pending_data:
                applied_msg = await cls._apply_modification_internal(
                    session=session,
                    plan=plan,
                    item_mod_pairs=item_mod_pairs,
                    mod_data=pending_data,
                    user_author=patient_user,
                    pending_disc=pending_disc,
                )
                for d in plan.discussions:
                    if d.proposed_modifications and d.proposed_modifications.get("status") == "pending":
                        d_mod = dict(d.proposed_modifications)
                        d_mod["status"] = "applied"
                        d.proposed_modifications = d_mod
                        flag_modified(d, "proposed_modifications")
                plan.discussions.append(applied_msg)
                await session.commit()
                await session.refresh(applied_msg)
                return cls._format_discussion_response(applied_msg)

        # 2. Deterministic Rejection
        is_rejection = (
            lowered in ("no", "cancel", "never mind", "reject", "keep it", "don't change", "leave it", "no thanks", "no please")
            or (lowered.startswith("no") and any(w in lowered for w in ("change", "thanks", "keep", "cancel", "don't")))
            or lowered.startswith("cancel")
            or lowered.startswith("reject")
        )
        if is_rejection:
            item_mod_pairs, pending_data, pending_disc = cls._find_pending_modification(plan)
            if pending_data:
                pending_data["status"] = "rejected"
                if pending_disc and pending_disc.proposed_modifications:
                    p_mod = dict(pending_disc.proposed_modifications)
                    p_mod["status"] = "rejected"
                    pending_disc.proposed_modifications = p_mod
                    flag_modified(pending_disc, "proposed_modifications")
                for d in plan.discussions:
                    if d.proposed_modifications and d.proposed_modifications.get("status") == "pending":
                        d_mod = dict(d.proposed_modifications)
                        d_mod["status"] = "rejected"
                        d.proposed_modifications = d_mod
                        flag_modified(d, "proposed_modifications")
                rejection_reply = PatientPlanDiscussion(
                    plan_id=plan.id,
                    role="assistant",
                    content="No problem! I have kept your current plan unchanged.",
                )
                await PatientPlanRepository.add_discussion_message(session, rejection_reply)
                plan.discussions.append(rejection_reply)
                await session.commit()
                await session.refresh(rejection_reply)
                return cls._format_discussion_response(rejection_reply)

        # 3. Detect Goal Change / Cancel Request ("I want weight loss instead", "cancel plan")
        if any(phrase in lowered for phrase in ("change my goal", "different goal", "weight loss instead", "weight gain instead", "forget this plan", "new goal", "cancel my plan", "delete my plan", "cancel plan", "delete plan")):
            goal_reply = PatientPlanDiscussion(
                plan_id=plan.id,
                role="assistant",
                content=(
                    f"A patient can only have **one plan at a time**. Your current plan is configured for **{plan.title}**.\n\n"
                    "If you would like to start a brand new plan, please click **Cancel Plan** at the top of your dashboard. "
                    "This will completely clear your current plan from the database so you can start fresh with a new goal and questionnaire."
                ),
            )
            await PatientPlanRepository.add_discussion_message(session, goal_reply)
            plan.discussions.append(goal_reply)
            await session.commit()
            await session.refresh(goal_reply)
            return cls._format_discussion_response(goal_reply)

        # 4. Detect Medication Requests & Configure Safety Guidance
        is_med_inquiry = PlanValidator.is_medication_inquiry(clean_text)

        # 5. LLM Follow-up Reasoning & Proposed Modification
        schedule_summary = "\n".join(
            f"- [{item.id}] {item.time_of_day} ({item.category}): {item.title} — {item.description}"
            for item in plan.items if item.is_active
        )
        recent_msgs = plan.discussions[-6:] if len(plan.discussions) > 6 else plan.discussions

        conv_context = []
        for m in recent_msgs:
            if m.role in ("user", "assistant"):
                conv_context.append({"role": m.role, "content": m.content})

        # Guarantee the user's latest query is present at the end of the context
        if not conv_context or conv_context[-1]["role"] != "user" or conv_context[-1]["content"] != clean_text:
            conv_context.append({"role": "user", "content": clean_text})

        med_guidance = ""
        if is_med_inquiry:
            med_guidance = (
                "\n\nCRITICAL PATIENT SAFETY DIRECTIVE:\n"
                "The patient is asking about or mentioning medications, drugs, tablets, pills, or medical prescriptions.\n"
                "1. STRICTLY FORBIDDEN: You must NEVER prescribe, recommend, or suggest any pharmaceutical drugs, medicines, tablets, pills, syrups, or clinical dosages.\n"
                "2. POLITELY DECLINE: Clearly and respectfully inform the patient that as an AI wellness guide, you cannot prescribe or advise on medications or clinical treatments, and advise them to consult a qualified physician for prescription needs.\n"
                "3. PROVIDE NATURAL ALTERNATIVES: Actively provide helpful, evidence-based NATURAL, DIETARY, and LIFESTYLE alternatives (such as herbal infusions like chamomile/ginger tea, proper hydration, wholesome nutrient-rich foods, gentle physical movement, and sleep routines) to support them naturally.\n"
                "4. Respond in English for English inquiries, or in Roman Urdu (using English letters) if the patient writes in Urdu/Roman Urdu. Never use Arabic/Urdu script.\n"
            )

        chat_system_prompt = (
            "You are an empathetic, expert wellness assistant discussing the patient's daily health plan.\n\n"
            f"PLAN TITLE: {plan.title}\n"
            f"SUMMARY: {plan.summary}\n\n"
            f"CURRENT SCHEDULE:\n{schedule_summary}\n\n"
            "CRITICAL RULES:\n"
            "1. NO MEDICATIONS: You are strictly forbidden from prescribing, recommending, or suggesting pharmaceutical drugs, pills, tablets, or clinical dosages.\n"
            "2. POLITELY DECLINE & OFFER NATURAL ALTERNATIVES: If the patient asks for any medicine or prescription, politely decline by explaining that you cannot prescribe medications and advise them to consult a licensed doctor, and provide safe natural, dietary, and lifestyle alternatives instead.\n"
            "3. Ground your explanations in their current plan.\n"
            "4. STRICT LANGUAGE & SCRIPT RULES:\n"
            "   - If the patient writes in English, reply strictly in English.\n"
            "   - If the patient writes in Urdu, Roman Urdu, or Hindi, reply STRICTLY in Roman Urdu (using Latin/English alphabet, e.g. 'Aap ke plan mein breakfast ko update kar diya gaya hai...').\n"
            "   - NEVER write in traditional Urdu script (اردو رسم الخط / Arabic script). Absolutely NO Nastaliq/Arabic characters. Even if the patient writes in Urdu script, your response MUST be in Roman Urdu with English letters.\n\n"
            "HOW TO HANDLE DIFFERENT REQUEST TYPES:\n\n"
            "A) SINGLE ITEM SWAP (e.g. 'swap my breakfast', 'change workout time'):\n"
            "   Explain the swap briefly and end your response with exactly ONE proposed modification in this format:\n"
            "   PROPOSED_MODIFICATION: {\"item_id\": \"<matching-item-uuid>\", \"original_title\": \"<old>\", \"proposed_title\": \"<new title>\", \"proposed_description\": \"<new description including weight gain/loss caloric impact>\", \"proposed_time\": \"HH:MM\", \"proposed_category\": \"<morning_routine|breakfast|lunch|evening_activity|dinner|night_routine>\", \"calories\": 350, \"protein_g\": 15.0, \"carbs_g\": 45.0, \"fat_g\": 8.0, \"fiber_g\": 5.0, \"calories_burned\": null}\n"
            "   IMPORTANT: Always include proposed_time (24-hour HH:MM format) if the time is changing. Always include proposed_category if the time-of-day category changes.\n"
            "   IMPORTANT: For meals, include accurate nutritional estimates (calories, protein_g, carbs_g, fat_g, fiber_g) and in proposed_description explicitly state the caloric surplus/deficit impact (weight gain or loss). For exercise items, set calories to null and provide calories_burned.\n\n"
            "B) SCHEDULE / LIFESTYLE CONSTRAINTS & UNAVAILABILITY (e.g. 'I work 9-5', 'I have no time between 10 am and 5 pm', 'I am busy from 10:00 to 17:00'):\n"
            "   This is CRITICAL. When the patient specifies an unavailable window or work hours:\n"
            "   1. Identify EVERY SINGLE schedule item currently scheduled within or overlapping that unavailable window.\n"
            "   2. Reschedule ALL of those items to suitable times outside that window (e.g. move lunch to before work or appropriate break, move afternoon activities/snacks to evening after work).\n"
            "   3. In your chat message, clearly list each moved item: old time -> new time.\n"
            "   4. YOU MUST output ALL of the adjusted items together in PROPOSED_MODIFICATION as a JSON array:\n"
            "   PROPOSED_MODIFICATION: [\n"
            "     {\"item_id\": \"<uuid-1>\", \"original_title\": \"<title 1>\", \"proposed_title\": \"<new title 1>\", \"proposed_description\": \"<desc 1>\", \"proposed_time\": \"09:30\", \"proposed_category\": \"lunch\", \"calories\": 450, \"protein_g\": 20.0, \"carbs_g\": 60.0, \"fat_g\": 12.0, \"fiber_g\": 6.0, \"calories_burned\": null},\n"
            "     {\"item_id\": \"<uuid-2>\", \"original_title\": \"<title 2>\", \"proposed_title\": \"<new title 2>\", \"proposed_description\": \"<desc 2>\", \"proposed_time\": \"18:00\", \"proposed_category\": \"evening_activity\", \"calories\": null, \"protein_g\": null, \"carbs_g\": null, \"fat_g\": null, \"fiber_g\": null, \"calories_burned\": 200}\n"
            "   ]\n"
            "   CRITICAL DIRECTIVE: NEVER adjust only one item and leave other items conflicting in the user's unavailable hours! You MUST include ALL conflicting items in the JSON array so the user's entire schedule becomes conflict-free in one click!\n\n"
            "C) GENERAL QUESTIONS (e.g. 'why this food?', 'is brown rice good?', 'how much water should I drink?'):\n"
            "   Answer helpfully grounded in their plan. No modification needed.\n\n"
            "D) VAGUE FEEDBACK (e.g. 'this is too much', 'I can't do all this', 'make it easier'):\n"
            "   Ask 1-2 specific clarifying questions about which parts feel difficult, then suggest practical lighter alternatives.\n\n"
            "Keep your replies concise and friendly (3-6 sentences). Always be practical and specific, never generic."
            f"{med_guidance}"
        )

        messages = [{"role": "system", "content": chat_system_prompt}] + conv_context

        try:
            ai_response_text = await cls._call_groq_api(messages, temperature=0.3, max_tokens=1800)
        except Exception as e:
            logger.error("Groq API error in discuss_plan: %s", e)
            if is_med_inquiry:
                ai_response_text = PlanValidator.get_safe_natural_alternative_fallback()
            else:
                ai_response_text = "I'm having trouble connecting right now. Please try asking again in a moment."

        # Safety Check: Verify no prescription drugs or dosages slipped through the LLM response
        is_unsafe, flagged_drugs = PlanValidator.contains_blocked_medication(ai_response_text)
        if is_unsafe:
            logger.warning(
                "LLM response contained blocked medication terms (%s). Overriding with safe natural alternative.",
                flagged_drugs,
            )
            ai_response_text = PlanValidator.get_safe_natural_alternative_fallback()

        # Parse proposed modification if present
        proposed_mod = None
        if "PROPOSED_MODIFICATION:" in ai_response_text:
            parts = ai_response_text.split("PROPOSED_MODIFICATION:", 1)
            ai_response_text = parts[0].strip()
            mod_json_str = parts[1].strip()

            # Strip markdown code fences if LLM wrapped JSON in ```json ... ```
            mod_json_str = re.sub(r"^```(?:json)?\s*", "", mod_json_str)
            mod_json_str = re.sub(r"\s*```\s*$", "", mod_json_str.strip())

            # Try to extract JSON array or object
            json_match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", mod_json_str)
            if json_match:
                mod_json_str = json_match.group(0)

            try:
                parsed_mod = json.loads(mod_json_str)
                if isinstance(parsed_mod, list):
                    mod_dict = {"status": "pending", "items": parsed_mod}
                elif isinstance(parsed_mod, dict):
                    mod_dict = dict(parsed_mod)
                    mod_dict["status"] = "pending"
                    for alt_key in ("modifications", "schedule_items", "adjustments", "changes"):
                        if alt_key in mod_dict and isinstance(mod_dict[alt_key], list):
                            mod_dict["items"] = mod_dict.pop(alt_key)
                            break
                else:
                    mod_dict = None

                if mod_dict:
                    # Verify proposed modification does not contain medications
                    mod_corpus = json.dumps(mod_dict)
                    is_mod_unsafe, _ = PlanValidator.contains_blocked_medication(mod_corpus)
                    if not is_mod_unsafe:
                        proposed_mod = mod_dict
                        item_count = len(mod_dict.get("items", [])) if "items" in mod_dict else 1
                        logger.info("Successfully parsed PROPOSED_MODIFICATION with %s item(s)", item_count)
                    else:
                        logger.warning("Proposed modification contained medication terms. Dropping modification.")
            except json.JSONDecodeError as je:
                logger.warning("Failed to parse PROPOSED_MODIFICATION JSON: %s | Raw: %s", je, mod_json_str[:300])
            except Exception as e:
                logger.warning("Unexpected error parsing PROPOSED_MODIFICATION: %s", e)

        assistant_msg = PatientPlanDiscussion(
            plan_id=plan.id,
            role="assistant",
            content=ai_response_text,
            proposed_modifications=proposed_mod,
        )
        await PatientPlanRepository.add_discussion_message(session, assistant_msg)
        plan.discussions.append(assistant_msg)
        await session.commit()
        await session.refresh(assistant_msg)

        return cls._format_discussion_response(assistant_msg)

    @classmethod
    def _find_item_for_mod(
        cls, plan: PatientPlan, mod_item: Dict[str, Any], exclude_ids: Optional[Set[uuid.UUID]] = None
    ) -> Optional[PatientPlanItem]:
        """Find matching PatientPlanItem by item_id, original_time, or title with distinct item resolution."""
        candidates = [i for i in plan.items if exclude_ids is None or i.id not in exclude_ids]
        if not candidates:
            return None

        item_id_str = mod_item.get("item_id")
        if item_id_str:
            try:
                target_uuid = uuid.UUID(str(item_id_str))
                matched = next((i for i in candidates if i.id == target_uuid), None)
                if matched:
                    return matched
            except (ValueError, TypeError):
                pass

        orig_time = mod_item.get("original_time")
        if orig_time:
            matched = next((i for i in candidates if i.time_of_day == orig_time), None)
            if matched:
                return matched

        orig_title = mod_item.get("original_title")
        if orig_title:
            orig_lower = orig_title.lower()
            matched = next(
                (i for i in candidates if orig_lower in i.title.lower() or i.title.lower() in orig_lower), None
            )
            if matched:
                return matched
            orig_words = set(re.findall(r"\w{3,}", orig_lower))
            if orig_words:
                matched = next(
                    (i for i in candidates if orig_words.intersection(re.findall(r"\w{3,}", i.title.lower()))),
                    None
                )
                if matched:
                    return matched

        prop_title = mod_item.get("proposed_title")
        if prop_title:
            prop_lower = prop_title.lower()
            matched = next(
                (i for i in candidates if prop_lower in i.title.lower() or i.title.lower() in prop_lower), None
            )
            if matched:
                return matched

        return None

    @classmethod
    def _find_pending_modification(
        cls, plan: PatientPlan
    ) -> Tuple[List[Tuple[PatientPlanItem, Dict[str, Any]]], Optional[Dict[str, Any]], Optional[PatientPlanDiscussion]]:
        """
        Finds any pending modification in the discussion history.
        Returns (item_mod_pairs, mod_data, disc).
        """
        for disc in reversed(plan.discussions):
            if disc.proposed_modifications and disc.proposed_modifications.get("status") == "pending":
                mod = disc.proposed_modifications
                # Multi-item modification
                if "items" in mod and isinstance(mod["items"], list):
                    item_mod_pairs = []
                    used_ids: Set[uuid.UUID] = set()
                    for m in mod["items"]:
                        target = cls._find_item_for_mod(plan, m, exclude_ids=used_ids)
                        if target:
                            used_ids.add(target.id)
                            item_mod_pairs.append((target, m))
                    if item_mod_pairs:
                        return item_mod_pairs, mod, disc
                else:
                    # Single item modification
                    target = cls._find_item_for_mod(plan, mod)
                    if not target and plan.items:
                        target = plan.items[0]
                    if target:
                        return [(target, mod)], mod, disc
        return [], None, None

    @classmethod
    async def _apply_modification_internal(
        cls,
        session: AsyncSession,
        plan: PatientPlan,
        item_mod_pairs: List[Tuple[PatientPlanItem, Dict[str, Any]]],
        mod_data: Dict[str, Any],
        user_author: User,
        pending_disc: Optional[PatientPlanDiscussion] = None,
    ) -> PatientPlanDiscussion:
        """Internal helper to apply approved modifications with atomic versioning."""
        valid_categories = {"morning_routine", "breakfast", "lunch", "evening_activity", "dinner", "night_routine", "snack", "exercise", "hydration"}
        changes_summaries = []
        revision_items = []

        for item, m in item_mod_pairs:
            old_title = item.title
            old_time = item.time_of_day
            old_category = item.category

            if m.get("proposed_title"):
                item.title = PlanValidator.sanitize_text(m["proposed_title"], 255)
            if m.get("proposed_description"):
                item.description = PlanValidator.sanitize_text(m["proposed_description"], 1000)

            proposed_time = m.get("proposed_time")
            if proposed_time and re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", proposed_time):
                item.time_of_day = proposed_time
                logger.info("Updating item time: %s -> %s", old_time, proposed_time)

            proposed_category = m.get("proposed_category")
            if proposed_category and proposed_category.lower() in valid_categories:
                item.category = proposed_category.lower()
                logger.info("Updating item category: %s -> %s", old_category, proposed_category)

            # Update nutritional metadata if present in modification
            if "calories" in m and m["calories"] is not None:
                item.calories = int(m["calories"])
            if "protein_g" in m and m["protein_g"] is not None:
                item.protein_g = float(m["protein_g"])
            if "carbs_g" in m and m["carbs_g"] is not None:
                item.carbs_g = float(m["carbs_g"])
            if "fat_g" in m and m["fat_g"] is not None:
                item.fat_g = float(m["fat_g"])
            if "fiber_g" in m and m["fiber_g"] is not None:
                item.fiber_g = float(m["fiber_g"])
            if "calories_burned" in m and m["calories_burned"] is not None:
                item.calories_burned = int(m["calories_burned"])

            time_change = f" moved from **{old_time}** to **{item.time_of_day}**" if old_time != item.time_of_day else ""
            changes_summaries.append(f"**{item.title}**{time_change}")
            revision_items.append({
                "item_id": str(item.id),
                "previous_title": old_title,
                "new_title": item.title,
                "previous_time": old_time,
                "new_time": item.time_of_day,
            })

        # Recalculate daily nutrition summary with updated items
        updated_summary = cls._calculate_daily_nutrition_summary(plan)
        if updated_summary:
            plan.daily_nutrition_summary = updated_summary
            flag_modified(plan, "daily_nutrition_summary")

        mod_data["status"] = "applied"
        if pending_disc and pending_disc.proposed_modifications:
            p_mod = dict(pending_disc.proposed_modifications)
            p_mod["status"] = "applied"
            pending_disc.proposed_modifications = p_mod
            flag_modified(pending_disc, "proposed_modifications")

        await session.flush()
        plan.version += 1

        snapshot = {
            "version": plan.version,
            "modified_items": revision_items,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        await PatientPlanRepository.create_revision(
            session=session,
            plan_id=plan.id,
            revision_number=plan.version,
            change_summary=f"Adjusted {len(item_mod_pairs)} schedule item(s).",
            snapshot=snapshot,
        )

        if len(item_mod_pairs) == 1:
            reply_content = f"✅ Done! I've updated your daily plan: {changes_summaries[0]} has been applied."
        else:
            items_list = "\n".join(f"- {s}" for s in changes_summaries)
            reply_content = f"✅ Done! I've updated your daily plan with all {len(item_mod_pairs)} schedule adjustments:\n{items_list}\nhave been successfully applied."

        reply = PatientPlanDiscussion(
            plan_id=plan.id,
            role="assistant",
            content=reply_content,
            proposed_modifications=mod_data,
        )
        return await PatientPlanRepository.add_discussion_message(session, reply)

    @classmethod
    async def apply_modification(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
        payload: ApplyPlanModificationRequest,
    ) -> PatientPlanDetailResponse:
        """
        Explicit endpoint for accepting/rejecting a pending plan modification.
        Protects against lost updates via expected_version optimistic locking.
        """
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if not plan:
            raise NotFoundError("Plan not found or does not belong to you.")

        logger.info(
            "apply_modification: plan_id=%s, plan_version=%s, expected_version=%s, action=%s",
            plan_id, plan.version, payload.expected_version, payload.action,
        )

        if plan.version != payload.expected_version:
            logger.warning(
                "Version mismatch: plan.version=%s != expected=%s",
                plan.version, payload.expected_version,
            )
            raise ConflictError("This plan was modified in another session. Please refresh to view the latest version.")

        item_mod_pairs, pending_data, pending_disc = cls._find_pending_modification(plan)
        logger.info(
            "apply_modification: found %s items to modify, pending_data=%s",
            len(item_mod_pairs),
            bool(pending_data),
        )
        if not pending_data or not item_mod_pairs:
            logger.warning("No pending modification found. Discussion count: %s", len(plan.discussions))
            for d in plan.discussions[-3:]:
                logger.warning(
                    "  Discussion id=%s role=%s has_mods=%s mod_status=%s",
                    d.id, d.role,
                    bool(d.proposed_modifications),
                    d.proposed_modifications.get("status") if d.proposed_modifications else "N/A",
                )
            raise NotFoundError("No pending modification found to apply.")

        if payload.action == "accept":
            await cls._apply_modification_internal(
                session=session,
                plan=plan,
                item_mod_pairs=item_mod_pairs,
                mod_data=pending_data,
                user_author=patient_user,
                pending_disc=pending_disc,
            )
        else:
            pending_data["status"] = "rejected"
            if pending_disc and pending_disc.proposed_modifications:
                p_mod = dict(pending_disc.proposed_modifications)
                p_mod["status"] = "rejected"
                pending_disc.proposed_modifications = p_mod
                flag_modified(pending_disc, "proposed_modifications")
            reject_msg = PatientPlanDiscussion(
                plan_id=plan.id,
                role="assistant",
                content="Modification declined. Your plan remains unchanged.",
            )
            await PatientPlanRepository.add_discussion_message(session, reject_msg)
            plan.discussions.append(reject_msg)

        # Mark all pending modifications in discussions as resolved
        for d in plan.discussions:
            if d.proposed_modifications and d.proposed_modifications.get("status") == "pending":
                d_mod = dict(d.proposed_modifications)
                d_mod["status"] = "applied" if payload.action == "accept" else "rejected"
                d.proposed_modifications = d_mod
                flag_modified(d, "proposed_modifications")

        await session.commit()
        session.expire_all()  # Force fresh load from DB to pick up item changes
        full_plan = await PatientPlanRepository.get_plan_by_id(session, plan.id, patient_user.id)
        return cls._format_plan_response(full_plan)

    # ── Approval, Lifecycle & Reminders ──────────────────────────────────────

    @classmethod
    async def approve_plan(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
    ) -> PatientPlanDetailResponse:
        """
        Atomic approval:
        - Enforces single active plan rule (raises ValidationError if another plan active)
        - Updates plan status to ACTIVE and records approved_at
        - Updates goal state to ACTIVE
        - Schedules background recurring daily reminders
        - Idempotent: returns existing active plan safely on double-click
        """
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if not plan:
            raise NotFoundError("Plan not found or does not belong to you.")

        if plan.status == "active":
            # Already active (idempotent protection)
            return cls._format_plan_response(plan)

        # Enforce single active plan rule
        active_plan = await PatientPlanRepository.get_active_plan(session, patient_user.id)
        if active_plan and active_plan.id != plan.id:
            raise ValidationError(
                f"You already have an active plan ('{active_plan.title}'). "
                "Please pause or cancel your current active plan before starting a new one."
            )

        try:
            plan.status = "active"
            plan.approved_at = datetime.now(timezone.utc)
            await PatientPlanRepository.update_goal_state(session, plan.goal_id, "ACTIVE")

            # Schedule reminders via APScheduler
            patient_name = patient_user.patient_profile.full_name if (patient_user.patient_profile and patient_user.patient_profile.full_name) else "Patient"
            schedule_plan_reminders(plan, patient_user.email, patient_name)

            await session.commit()
            full_plan = await PatientPlanRepository.get_plan_by_id(session, plan.id, patient_user.id)
            return cls._format_plan_response(full_plan)
        except Exception as exc:
            await session.rollback()
            logger.error(f"Plan approval error: {exc}")
            raise ValidationError("Could not activate plan. Please try again.")

    @classmethod
    async def pause_plan(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
    ) -> PatientPlanDetailResponse:
        """Pauses an active plan, stops future reminder jobs, and retains all history."""
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if not plan:
            raise NotFoundError("Plan not found.")

        plan.status = "paused"
        await PatientPlanRepository.update_goal_state(session, plan.goal_id, "PAUSED")
        cancel_plan_reminders(plan)
        await session.commit()

        full_plan = await PatientPlanRepository.get_plan_by_id(session, plan.id, patient_user.id)
        return cls._format_plan_response(full_plan)

    @classmethod
    async def resume_plan(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
    ) -> PatientPlanDetailResponse:
        """Resumes a paused plan, enforcing single active plan rule."""
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if not plan:
            raise NotFoundError("Plan not found.")

        # Check single active plan
        active_plan = await PatientPlanRepository.get_active_plan(session, patient_user.id)
        if active_plan and active_plan.id != plan.id:
            raise ValidationError("Another plan is currently active. Pause or complete it before resuming this plan.")

        plan.status = "active"
        await PatientPlanRepository.update_goal_state(session, plan.goal_id, "ACTIVE")

        patient_name = patient_user.patient_profile.full_name if (patient_user.patient_profile and patient_user.patient_profile.full_name) else "Patient"
        schedule_plan_reminders(plan, patient_user.email, patient_name)
        await session.commit()

        full_plan = await PatientPlanRepository.get_plan_by_id(session, plan.id, patient_user.id)
        return cls._format_plan_response(full_plan)

    @classmethod
    async def cancel_plan(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Cancels and completely wipes the plan, its schedule items, discussions,
        logs, and associated goal from the database so the patient can start fresh.
        """
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if plan:
            # 1. Stop background scheduled reminders
            try:
                cancel_plan_reminders(plan)
            except Exception as e:
                logger.warning(f"Error cancelling reminders during plan deletion: {e}")

        # 2. Completely wipe all plans, goals, schedule items, discussions, and logs for this patient
        await PatientPlanRepository.delete_all_patient_plans_and_goals(session, patient_user.id)
        await session.commit()

        return {
            "status": "deleted",
            "message": "Your plan and all associated data have been completely wiped from the database. You can now create a fresh new plan.",
        }

    # ── Daily Progress Logging ───────────────────────────────────────────────

    @classmethod
    async def log_activity(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
        payload: LogActivityRequest,
    ) -> PatientPlanDetailResponse:
        """Logs daily activity completion with idempotency."""
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if not plan:
            raise NotFoundError("Plan not found.")

        await PatientPlanRepository.log_activity(
            session=session,
            plan_id=plan.id,
            item_id=payload.item_id,
            patient_id=patient_user.id,
            log_date=payload.log_date,
            status=payload.status,
            notes=PlanValidator.sanitize_text(payload.notes, 500) if payload.notes else None,
        )
        await session.commit()

        full_plan = await PatientPlanRepository.get_plan_by_id(session, plan.id, patient_user.id)
        today_logs = await PatientPlanRepository.get_logs_for_date(session, plan.id, date.today())
        return cls._format_plan_response(full_plan, today_logs=today_logs)

    # ── Queries ──────────────────────────────────────────────────────────────

    @classmethod
    async def get_active_plan(
        cls,
        session: AsyncSession,
        patient_user: User,
    ) -> Optional[PatientPlanDetailResponse]:
        """Fetch current active plan for dashboard."""
        plan = await PatientPlanRepository.get_active_plan(session, patient_user.id)
        if not plan:
            return None
        today_logs = await PatientPlanRepository.get_logs_for_date(session, plan.id, date.today())
        return cls._format_plan_response(plan, today_logs=today_logs)

    @classmethod
    async def get_plan_detail(
        cls,
        session: AsyncSession,
        patient_user: User,
        plan_id: uuid.UUID,
    ) -> PatientPlanDetailResponse:
        """Fetch full details for a specific plan."""
        plan = await PatientPlanRepository.get_plan_by_id(session, plan_id, patient_user.id)
        if not plan:
            raise NotFoundError("Plan not found.")
        today_logs = await PatientPlanRepository.get_logs_for_date(session, plan.id, date.today())
        return cls._format_plan_response(plan, today_logs=today_logs)

    @classmethod
    async def list_patient_plans(
        cls,
        session: AsyncSession,
        patient_user: User,
    ) -> List[PatientPlanSummaryResponse]:
        """List all plans for a patient."""
        plans = await PatientPlanRepository.list_plans_by_patient(session, patient_user.id)
        return [
            PatientPlanSummaryResponse(
                id=p.id,
                goal_id=p.goal_id,
                title=p.title,
                status=p.status,
                target_duration_weeks=p.target_duration_weeks,
                approved_at=p.approved_at,
                created_at=p.created_at,
            )
            for p in plans
        ]

    # ── Response Formatters ──────────────────────────────────────────────────

    @classmethod
    def _format_goal_response(cls, goal: PatientGoal) -> PatientGoalDetailResponse:
        answers_by_q = {a.question_id: a for a in goal.answers}
        formatted_questions = []

        for q in goal.questions:
            ans = answers_by_q.get(q.id)
            current_ans_dict = None
            if ans:
                current_ans_dict = {
                    "raw_input": ans.raw_input,
                    "normalized_value": ans.normalized_value,
                    "unit": ans.unit,
                    "is_skipped": ans.is_skipped,
                    "validation_status": ans.validation_status,
                    "clarification_message": ans.clarification_message,
                }
            formatted_questions.append(
                GoalQuestionResponse(
                    id=q.id,
                    question_key=q.question_key,
                    question_text=q.question_text,
                    question_type=q.question_type,
                    options=q.options.get("items") if q.options and isinstance(q.options, dict) else q.options,
                    unit=q.unit,
                    is_required=q.is_required,
                    order_index=q.order_index,
                    retry_count=q.retry_count,
                    help_text=q.help_text,
                    current_answer=current_ans_dict,
                )
            )

        answered_count = sum(
            1 for q in goal.questions
            if answers_by_q.get(q.id) and answers_by_q[q.id].validation_status in ("valid", "skipped")
        )
        is_completed = (
            len(goal.questions) > 0 and
            all((not q.is_required or (answers_by_q.get(q.id) and answers_by_q[q.id].validation_status in ("valid", "skipped"))) for q in goal.questions)
        )

        active_plan_id = goal.plans[0].id if goal.plans else None

        return PatientGoalDetailResponse(
            id=goal.id,
            title=goal.title,
            category=goal.category,
            target_description=goal.target_description,
            workflow_state=goal.workflow_state,
            timezone=goal.timezone,
            target_duration_weeks=goal.target_duration_weeks,
            questions=formatted_questions,
            is_questionnaire_completed=is_completed,
            active_plan_id=active_plan_id,
            created_at=goal.created_at,
        )

    @classmethod
    def _format_plan_response(
        cls,
        plan: PatientPlan,
        today_logs: Optional[List[PatientPlanLog]] = None,
    ) -> PatientPlanDetailResponse:
        items = [
            PlanItemSchema(
                id=item.id,
                time_of_day=item.time_of_day,
                category=item.category,
                title=item.title,
                description=item.description,
                order_index=item.order_index,
                is_active=item.is_active,
                calories=item.calories,
                protein_g=item.protein_g,
                carbs_g=item.carbs_g,
                fat_g=item.fat_g,
                fiber_g=item.fiber_g,
                calories_burned=item.calories_burned,
            )
            for item in plan.items
        ]

        discussions = [
            cls._format_discussion_response(d)
            for d in plan.discussions
        ]

        today = date.today()
        if today_logs is None:
            resolved_today_logs = [
                log for log in plan.logs
                if log.log_date == today
            ]
        else:
            resolved_today_logs = today_logs

        return PatientPlanDetailResponse(
            id=plan.id,
            goal_id=plan.goal_id,
            title=plan.title,
            summary=plan.summary,
            target_duration_weeks=plan.target_duration_weeks,
            diet_guidelines=plan.diet_guidelines.get("items") if isinstance(plan.diet_guidelines, dict) else plan.diet_guidelines,
            lifestyle_guidelines=plan.lifestyle_guidelines.get("items") if isinstance(plan.lifestyle_guidelines, dict) else plan.lifestyle_guidelines,
            precautions=plan.precautions.get("items") if isinstance(plan.precautions, dict) else plan.precautions,
            version=plan.version,
            status=plan.status,
            approved_at=plan.approved_at,
            items=items,
            discussions=discussions,
            today_logs=resolved_today_logs,
            daily_nutrition_summary=plan.daily_nutrition_summary or cls._calculate_daily_nutrition_summary(plan),
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    @classmethod
    def _calculate_daily_nutrition_summary(cls, plan: PatientPlan) -> Optional[Dict[str, Any]]:
        """Calculates aggregate daily nutritional totals from all active items."""
        total_calories = 0
        total_protein = 0.0
        total_carbs = 0.0
        total_fat = 0.0
        total_fiber = 0.0
        total_burned = 0
        has_nutrition = False

        for item in plan.items:
            if not item.is_active:
                continue
            if item.calories is not None:
                total_calories += item.calories
                has_nutrition = True
            if item.protein_g is not None:
                total_protein += item.protein_g
                has_nutrition = True
            if item.carbs_g is not None:
                total_carbs += item.carbs_g
                has_nutrition = True
            if item.fat_g is not None:
                total_fat += item.fat_g
                has_nutrition = True
            if item.fiber_g is not None:
                total_fiber += item.fiber_g
                has_nutrition = True
            if item.calories_burned is not None:
                total_burned += item.calories_burned
                has_nutrition = True

        if not has_nutrition:
            return None

        return {
            "total_calories": total_calories,
            "total_protein_g": round(total_protein, 1),
            "total_carbs_g": round(total_carbs, 1),
            "total_fat_g": round(total_fat, 1),
            "total_fiber_g": round(total_fiber, 1),
            "total_calories_burned": total_burned,
            "net_calories": total_calories - total_burned,
        }

    @classmethod
    def _format_discussion_response(cls, d: PatientPlanDiscussion) -> PlanDiscussionMessageResponse:
        return PlanDiscussionMessageResponse(
            id=d.id,
            role=d.role,
            content=d.content,
            proposed_modifications=d.proposed_modifications,
            created_at=d.created_at,
        )

    # ── Smart Intent Classifier ──────────────────────────────────────────────

    @classmethod
    def _classify_chat_intent(cls, text: str) -> str:
        """
        Deterministic intent classifier for plan chat messages.

        Returns:
        - "nutrition_info"      → standalone food/exercise information query
        - "plan_modification"   → plan change request or general plan discussion (default)

        The classifier is conservative: if there's ANY hint the user wants to
        modify their plan, it falls through to the existing plan chat logic.
        Only clearly standalone info queries are routed to the nutrition agent.
        """
        lowered = text.lower().strip()

        # ── 1. Plan modification verbs → always plan_modification ─────────
        plan_mod_verbs = (
            "change", "swap", "shift", "replace", "move", "adjust", "switch",
            "remove", "add", "update", "modify", "edit", "reschedule",
            "badal", "badlo", "hatao", "hata do", "laga do", "daal do",
            "time change", "time badal", "time shift",
        )
        for verb in plan_mod_verbs:
            if verb in lowered:
                return "plan_modification"

        # ── 2. Plan item references → plan_modification ───────────────────
        plan_item_refs = (
            "my breakfast", "my lunch", "my dinner", "my workout",
            "my plan", "my schedule", "mera plan", "mera breakfast",
            "mera lunch", "mera dinner", "mera workout",
            "morning routine", "evening routine", "sleep routine",
        )
        for ref in plan_item_refs:
            if ref in lowered:
                return "plan_modification"

        # ── 3. Clear nutrition info queries → nutrition_info ──────────────

        # Pattern: "X mein/ma/me kitni/kitna/kitne calories/protein/..."
        urdu_info_patterns = [
            r"\b(?:mein|ma|me|mai)\s+(?:kitni|kitna|kitne)\b",
            r"\b(?:kitni|kitna|kitne)\s+(?:calories|calorie|protein|carbs?|fat|fiber)\b",
            r"\b(?:agar|agr)\s+(?:main|mein|ma)\b.*\b(?:khaon|khaun|khata|khati|peeta|peeti|piyon)\b",
            r"\b(?:se|sy)\s+(?:kitna|kitni|kitne)\s+(?:burn|jale|jalega|jalein|milega|milein)\b",
        ]
        for pat in urdu_info_patterns:
            if re.search(pat, lowered):
                return "nutrition_info"

        # English info query patterns
        english_info_patterns = [
            r"\bhow\s+(?:many|much)\s+(?:calories|calorie|protein|carbs?|fat|fiber)\b",
            r"\b(?:calories?|protein|carbs?|fat|fiber|nutrition(?:al)?)\s+(?:in|of|for)\b",
            r"\bif\s+i\s+(?:eat|drink|have|consume|walk|run|jog|cycle|swim)\b",
            r"\bhow\s+(?:many|much)\s+(?:calories?)\s+(?:does?|do|will|would|can)\b.*\bburn\b",
            r"\b(?:nutritional?|caloric)\s+(?:value|info|information|content|data|facts?)\b",
            r"\bwhat(?:'s| is| are)\s+(?:the\s+)?(?:calories?|protein|carbs?|fat|nutrition)\b",
        ]
        for pat in english_info_patterns:
            if re.search(pat, lowered):
                return "nutrition_info"

        # Direct food/exercise info questions (standalone item name + calories keyword)
        standalone_food_query = re.search(
            r"\b(?:banana|apple|roti|paratha|biryani|daal|dal|chawal|rice|chicken|egg|anda|"
            r"bread|naan|lassi|chai|milk|doodh|mango|orange|yogurt|dahi|sabzi|gosht|"
            r"fish|machli|paneer|chana|rajma|oats|oatmeal|almonds|badam|walnuts|akhrot)\b"
            r".*\b(?:calories?|protein|carbs?|fat|fiber|nutrition|kitni|kitna)\b",
            lowered,
        )
        if standalone_food_query:
            return "nutrition_info"

        # Reverse pattern: nutrition keyword first, then food name
        reverse_food_query = re.search(
            r"\b(?:calories?|protein|carbs?|fat|fiber|nutrition|kitni|kitna)\b"
            r".*\b(?:banana|apple|roti|paratha|biryani|daal|dal|chawal|rice|chicken|egg|anda|"
            r"bread|naan|lassi|chai|milk|doodh|mango|orange|yogurt|dahi|sabzi|gosht|"
            r"fish|machli|paneer|chana|rajma|oats|oatmeal|almonds|badam|walnuts|akhrot)\b",
            lowered,
        )
        if reverse_food_query:
            return "nutrition_info"

        # Exercise info queries
        exercise_info_query = re.search(
            r"\b(?:walk(?:ing)?|run(?:ning)?|jog(?:ging)?|cycl(?:ing|e)|swim(?:ming)?|"
            r"pushup|push[- ]?up|squat|plank|yoga|stretching|stairs|jumping)\b"
            r".*\b(?:calories?|burn|jale|jalega|kitna|kitni|how\s+(?:many|much))\b",
            lowered,
        )
        if exercise_info_query:
            return "nutrition_info"

        # ── 4. Default: route to plan discussion LLM ─────────────────────
        return "plan_modification"
