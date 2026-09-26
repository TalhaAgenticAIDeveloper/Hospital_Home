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

# ── Minimal Fallback Questions (used ONLY if AI question generation fails) ────
FALLBACK_ESSENTIAL_QUESTIONS: List[Dict[str, Any]] = [
    {
        "question_key": "food_allergies",
        "question_text": "Do you have any food allergies or severe intolerances?",
        "question_type": "text",
        "is_required": True,
        "help_text": "e.g., Peanuts, Dairy, Shellfish, Gluten, or None",
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
        "question_key": "activity_level",
        "question_text": "How would you describe your daily physical activity?",
        "question_type": "select",
        "options": ["Sedentary (mostly desk work)", "Lightly active (light walking)", "Moderately active (workout 3-4x/week)", "Very active (daily vigorous exercise)"],
        "is_required": True,
        "help_text": "Select your general activity level",
    },
]

# Valid question_type values the frontend supports
VALID_QUESTION_TYPES = {"number", "select", "time", "text"}


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

            result = cleaned or raw_content
            # Normalize unicode spaces and hyphens for Windows terminal and database safety
            result = result.replace("\u202f", " ").replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "--").replace("\u00a0", " ")
            return result

    # ── AI Question Generation ─────────────────────────────────────────────

    @classmethod
    async def _generate_questions_via_ai(
        cls,
        category: str,
        title: str,
        target_description: str,
    ) -> List[Dict[str, Any]]:
        """
        Calls Groq LLM to dynamically generate clinically relevant intake assessment
        questions tailored to the patient's specific goal, category, and description.
        Acts as an experienced clinical wellness doctor conducting an initial consultation.

        Returns a list of question dicts ready for PatientGoalQuestion creation.
        Falls back to FALLBACK_ESSENTIAL_QUESTIONS if AI generation fails.
        """
        system_prompt = (
            "You are an experienced clinical wellness doctor conducting an initial patient intake assessment. "
            "Based on the patient's health goal and description, generate a focused set of clinically relevant "
            "questions that a real doctor would ask before creating a personalized wellness plan.\n\n"
            "RULES:\n"
            "1. Generate exactly 6 to 10 questions — no more, no fewer.\n"
            "2. Questions must be medically and clinically relevant to the patient's specific goal.\n"
            "3. MANDATORY: You MUST always include these safety-critical questions:\n"
            "   - A food allergy question (question_key MUST be 'food_allergies', question_type: 'text', is_required: true)\n"
            "   - A wake-up time question (question_key MUST be 'wake_up_time', question_type: 'time', is_required: true)\n"
            "   - A bed/sleep time question (question_key MUST be 'bed_time', question_type: 'time', is_required: true)\n"
            "4. Each question must have:\n"
            "   - question_key: unique snake_case identifier (e.g., 'current_weight', 'dietary_pattern')\n"
            "   - question_text: clear, compassionate question text\n"
            "   - question_type: one of 'number', 'select', 'time', 'text'\n"
            "   - is_required: boolean (true for critical questions, false for optional)\n"
            "   - help_text: short helpful example or hint\n"
            "   - unit: string (ONLY for 'number' type, e.g. 'kg', 'cm', 'minutes', 'liters') — omit or set null for other types\n"
            "   - options: array of 3-5 option strings (ONLY for 'select' type) — omit or set null for other types\n"
            "5. Use appropriate question_type for each question:\n"
            "   - 'number' for measurable values (weight, height, age, duration in minutes)\n"
            "   - 'select' for multiple choice (activity level, diet pattern, frequency)\n"
            "   - 'time' for time-of-day questions (wake up, sleep, meal times)\n"
            "   - 'text' for open-ended responses (allergies, medical conditions, preferences)\n"
            "6. Be culturally sensitive — support South Asian context (Halal, roti/paratha, Fajr prayer timing, etc.)\n"
            "7. Do NOT ask about medications or prescriptions — only lifestyle, diet, and activity.\n"
            "8. Keep questions practical and actionable — avoid vague or overly clinical jargon.\n"
            "9. question_key values must be unique across all questions.\n\n"
            "OUTPUT: Return ONLY a valid raw JSON array of question objects. No markdown, no explanations, no preamble.\n"
            "Example format:\n"
            "[\n"
            '  {"question_key": "current_weight", "question_text": "What is your current weight?", '
            '"question_type": "number", "unit": "kg", "is_required": true, '
            '"help_text": "e.g., 70 kg or 154 lb"},\n'
            '  {"question_key": "activity_level", "question_text": "How active are you daily?", '
            '"question_type": "select", "options": ["Sedentary", "Lightly active", "Moderately active", "Very active"], '
            '"is_required": true, "help_text": "Choose your activity level"}\n'
            "]"
        )

        user_prompt = (
            f"PATIENT GOAL CATEGORY: {category}\n"
            f"GOAL TITLE: {title}\n"
            f"PATIENT'S DESCRIPTION: {target_description}\n\n"
            "Generate the clinical intake assessment questions for this patient now."
        )

        try:
            raw_response = await cls._call_groq_api(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1536,
            )

            # Extract JSON array from response using robust parser
            questions_raw = PlanValidator.extract_and_parse_json(raw_response)

            if not isinstance(questions_raw, list) or len(questions_raw) < 3:
                logger.warning(f"AI returned insufficient questions ({len(questions_raw) if isinstance(questions_raw, list) else 'non-list'}), falling back.")
                return FALLBACK_ESSENTIAL_QUESTIONS

            # Validate and sanitize each question
            validated_questions: List[Dict[str, Any]] = []
            seen_keys: Set[str] = set()

            for q in questions_raw:
                if not isinstance(q, dict):
                    continue

                q_key = str(q.get("question_key", "")).strip().lower().replace(" ", "_")
                q_text = str(q.get("question_text", "")).strip()
                q_type = str(q.get("question_type", "text")).strip().lower()
                q_required = bool(q.get("is_required", True))
                q_help = str(q.get("help_text", "")).strip() or None
                q_unit = q.get("unit")
                q_options = q.get("options")

                # Skip invalid entries
                if not q_key or not q_text or q_key in seen_keys:
                    continue
                if q_type not in VALID_QUESTION_TYPES:
                    q_type = "text"

                # Ensure unit only for number type
                if q_type != "number":
                    q_unit = None
                elif q_unit:
                    q_unit = str(q_unit).strip()[:20]

                # Ensure options only for select type and is a valid list
                if q_type == "select":
                    if not isinstance(q_options, list) or len(q_options) < 2:
                        q_type = "text"  # Demote to text if invalid options
                        q_options = None
                    else:
                        q_options = [str(o).strip() for o in q_options if str(o).strip()]
                else:
                    q_options = None

                seen_keys.add(q_key)
                validated_q: Dict[str, Any] = {
                    "question_key": q_key[:100],
                    "question_text": q_text[:500],
                    "question_type": q_type,
                    "is_required": q_required,
                    "help_text": q_help[:500] if q_help else None,
                }
                if q_unit:
                    validated_q["unit"] = q_unit
                if q_options:
                    validated_q["options"] = q_options

                validated_questions.append(validated_q)

            # Ensure mandatory safety questions are present
            mandatory_keys = {
                "food_allergies": {
                    "question_key": "food_allergies",
                    "question_text": "Do you have any food allergies or severe intolerances?",
                    "question_type": "text",
                    "is_required": True,
                    "help_text": "e.g., Peanuts, Dairy, Shellfish, Gluten, or None",
                },
                "wake_up_time": {
                    "question_key": "wake_up_time",
                    "question_text": "What time do you usually wake up?",
                    "question_type": "time",
                    "is_required": True,
                    "help_text": "e.g., 07:00 AM or after Fajr",
                },
                "bed_time": {
                    "question_key": "bed_time",
                    "question_text": "What time do you usually go to sleep?",
                    "question_type": "time",
                    "is_required": True,
                    "help_text": "e.g., 11:00 PM",
                },
            }
            existing_keys = {q["question_key"] for q in validated_questions}
            for key, fallback_q in mandatory_keys.items():
                if key not in existing_keys:
                    validated_questions.append(fallback_q)

            if len(validated_questions) < 3:
                logger.warning("AI question validation resulted in too few questions, falling back.")
                return FALLBACK_ESSENTIAL_QUESTIONS

            # Cap at 12 questions maximum
            return validated_questions[:12]

        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(f"AI question generation failed (parse/validation): {exc}")
            return FALLBACK_ESSENTIAL_QUESTIONS
        except Exception as exc:
            logger.error(f"AI question generation failed (unexpected): {exc}")
            return FALLBACK_ESSENTIAL_QUESTIONS

    # ── Goals & Questionnaire ────────────────────────────────────────────────

    @classmethod
    async def create_goal(
        cls,
        session: AsyncSession,
        patient_user: User,
        payload: CreateGoalRequest,
    ) -> PatientGoalDetailResponse:
        """
        Creates a new patient goal and seeds AI-generated clinical questions.
        The AI acts as a real doctor, generating relevant intake assessment
        questions tailored to the patient's specific goal and description.
        Falls back to essential questions if AI is unavailable.
        Transitions state to QUESTIONNAIRE_ACTIVE.
        """
        category = payload.category or "custom"

        target_desc = payload.target_description or ""

        # Generate questions via AI based on the patient's specific goal
        raw_questions = await cls._generate_questions_via_ai(
            category=category,
            title=payload.title,
            target_description=target_desc,
        )

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
            target_description=PlanValidator.sanitize_text(target_desc, 2000),
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
    async def _verify_answer_via_ai(
        cls,
        goal_title: str,
        goal_category: str,
        goal_description: Optional[str],
        question_text: str,
        question_key: str,
        question_type: str,
        expected_unit: Optional[str],
        options: Optional[List[str]],
        raw_input: str,
        retry_count: int = 0,
        allow_warning: bool = False,
    ) -> Any:
        """
        Intelligent Clinical Intake Verification Agent.
        - Evaluates patient input against goal, description, and question context.
        - Rejects impossible / absurd values (e.g. adult weight 5kg) with a polite, educational message.
        - Requests missing units for measurable parameters (e.g. 40 -> kindly enter kg or lb).
        - Issues advisory warning for minors under 18 with option to continue anyway.
        - Extracts normalized values for valid answers.
        - Falls back to PlanValidator deterministic safety rules if AI call fails.
        """
        clean_input = PlanValidator.sanitize_text(raw_input).strip()
        lowered = clean_input.lower()

        # 1. User confirmed an advisory warning (e.g. age < 18) and opted to proceed
        if allow_warning:
            return PlanValidator.validate_question_answer(
                question_key=question_key,
                question_type=question_type,
                raw_input=clean_input,
                is_skipped=False,
                retry_count=retry_count,
                expected_unit=expected_unit,
            )

        # 2. Empty string check
        if not clean_input:
            from app.services.plan_validator import AnswerValidationResult
            return AnswerValidationResult(
                status="invalid",
                message="Please enter your answer before continuing.",
                normalized_value=None,
                unit=None,
                can_proceed=False,
                extracted_fields={},
            )

        # 3. LLM Verification Agent invocation
        system_prompt = (
            "You are an intelligent, compassionate Clinical Intake Verification Agent for a digital health & wellness clinic.\n"
            "Your job is to verify a patient's answer to an intake questionnaire question before their personalized wellness plan is generated.\n"
            "The patient may write in English, Urdu, Roman Urdu, or informal conversational phrasing.\n\n"
            "EVALUATION DIRECTIVES:\n"
            "1. DETECT SKIPPED OR UNCERTAIN ANSWERS (status: 'skipped'):\n"
            "   - If the patient explicitly asks to skip, or indicates that they do not know, have no idea, cannot answer, or prefer not to answer "
            "(in English, Urdu, Roman Urdu, or slang, e.g., 'skip', 'idk', 'pass', 'mujhe nahi pata', 'pata nahi', 'maloom nahi', 'chhor do', 'not sure', 'dont know', 'no clue', 'prefer not to say').\n"
            "   - Output status 'skipped', message 'Question skipped. Continuing with standard recommendations.', and normalized_value 'Not provided (skipped)'.\n\n"
            "2. REJECT IMPOSSIBLE OR ABSURD ANSWERS (status: 'invalid'):\n"
            "   - If the answer contains physically impossible, absurd, or unsafe numbers (e.g., human weight of 5kg or 500kg, height of 10cm or 300cm, age of 200, sleeping for 25 hours).\n"
            "   - Or completely irrelevant nonsense / gibberish (e.g., answering 'cricket' or 'watching movies' when asked about weight or breakfast).\n"
            "   - Provide a POLITE, helpful explanatory message pointing out why this seems incorrect and what a realistic entry looks like.\n\n"
            "3. CLARIFY MISSING UNITS (status: 'clarification_needed'):\n"
            "   - If the question asks for a measurable physical metric (weight, height, liquid) and the user provides just a bare number without specifying the unit (e.g., '40' or '70' for weight):\n"
            "   - Politely ask: 'Kindly specify the unit as well (e.g. is that 40 kg or 40 lb?) so we can calculate your nutrition and plan accurately.'\n\n"
            "4. ADVISORY WARNING FOR MINORS (status: 'warning'):\n"
            "   - If the question asks about age and the user indicates an age under 18 years (e.g., 15, 16, 17):\n"
            "   - Output polite advisory message: 'We recommend you to be at least 18 years old before following an independent wellness regimen. If you still wish to continue with gentle lifestyle guidance, you may proceed.'\n\n"
            "5. VALID ANSWERS (status: 'valid'):\n"
            "   - If the answer is realistic, plausible, and addresses the question clearly:\n"
            "   - Output status 'valid', with clean normalized_value and unit.\n\n"
            "OUTPUT FORMAT:\n"
            "Return ONLY a valid raw JSON object matching:\n"
            "{\n"
            '  "status": "valid" | "clarification_needed" | "warning" | "invalid" | "skipped",\n'
            '  "message": "Polite explanatory message for user if invalid, clarification_needed, warning, or skipped (null if valid)",\n'
            '  "normalized_value": "Clean standardized string representation",\n'
            '  "unit": "Unit string if applicable or null"\n'
            "}"
        )

        user_content = (
            f"PATIENT GOAL: {goal_title} ({goal_category})\n"
            f"GOAL DESCRIPTION: {goal_description or 'None provided'}\n"
            f"QUESTION: {question_text} (key: {question_key}, type: {question_type})\n"
            f"EXPECTED UNIT: {expected_unit or 'None'}\n"
            f"SUGGESTED OPTIONS: {options or 'None'}\n"
            f"PATIENT ANSWER: \"{clean_input}\"\n\n"
            "Evaluate this answer now."
        )

        try:
            raw_eval = await cls._call_groq_api(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=600,
            )
            parsed = PlanValidator.extract_and_parse_json(raw_eval)
            if isinstance(parsed, dict) and "status" in parsed:
                eval_status = parsed.get("status", "valid").lower()
                eval_msg = parsed.get("message")
                norm_val = parsed.get("normalized_value") or clean_input
                eval_unit = parsed.get("unit") or expected_unit

                from app.services.plan_validator import AnswerValidationResult
                if eval_status == "skipped":
                    return AnswerValidationResult(
                        status="skipped",
                        message=eval_msg or "Question skipped. Continuing with standard recommendations.",
                        normalized_value="Not provided (skipped)",
                        unit=None,
                        can_proceed=True,
                        extracted_fields={},
                    )
                elif eval_status in ("invalid", "clarification_needed"):
                    return AnswerValidationResult(
                        status=eval_status,
                        message=eval_msg or "Please verify your input format.",
                        normalized_value=None,
                        unit=None,
                        can_proceed=False,
                        extracted_fields={},
                    )
                elif eval_status == "warning":
                    return AnswerValidationResult(
                        status="warning",
                        message=eval_msg or "Please review this advisory before continuing.",
                        normalized_value=norm_val,
                        unit=eval_unit,
                        can_proceed=False,
                        extracted_fields={question_key: norm_val},
                    )
                else:
                    return AnswerValidationResult(
                        status="valid",
                        message=eval_msg,
                        normalized_value=norm_val,
                        unit=eval_unit,
                        can_proceed=True,
                        extracted_fields={question_key: norm_val},
                    )
        except Exception as exc:
            logger.warning(f"AI answer verification failed ({exc}), falling back to deterministic rules.")

        # Deterministic fallback
        return PlanValidator.validate_question_answer(
            question_key=question_key,
            question_type=question_type,
            raw_input=clean_input,
            is_skipped=False,
            retry_count=retry_count,
            expected_unit=expected_unit,
        )

    @classmethod
    async def answer_question(
        cls,
        session: AsyncSession,
        patient_user: User,
        goal_id: uuid.UUID,
        payload: QuestionnaireAnswerRequest,
    ) -> QuestionAnswerResponse:
        """
        Processes and validates an answer to a goal questionnaire question using the
        intelligent Clinical Intake Verification Agent:
        - Detects impossible answers & missing units
        - Advises minors under 18 with option to continue
        - Auto-completes questionnaire when all required questions are valid
        """
        goal = await PatientPlanRepository.get_goal_by_id(session, goal_id, patient_user.id)
        if not goal:
            raise NotFoundError("Goal not found or does not belong to you.")

        question = await PatientPlanRepository.get_question_by_id(session, payload.question_id)
        if not question or question.goal_id != goal.id:
            raise NotFoundError("Question not found for this goal.")

        # Run AI Verification Agent
        val_res = await cls._verify_answer_via_ai(
            goal_title=goal.title,
            goal_category=goal.category,
            goal_description=goal.target_description,
            question_text=question.question_text,
            question_key=question.question_key,
            question_type=question.question_type,
            expected_unit=question.unit,
            options=question.options.get("items") if isinstance(question.options, dict) else question.options,
            raw_input=payload.raw_input,
            retry_count=question.retry_count,
            allow_warning=payload.allow_warning,
        )

        if val_res.status in ("invalid", "clarification_needed"):
            new_retries = await PatientPlanRepository.increment_question_retry(session, question.id)
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

        if val_res.status == "warning":
            await PatientPlanRepository.upsert_answer(
                session=session,
                goal_id=goal.id,
                question_id=question.id,
                raw_input=payload.raw_input,
                normalized_value=val_res.normalized_value,
                unit=val_res.unit,
                is_skipped=False,
                validation_status="warning",
                clarification_message=val_res.message,
            )
            await session.commit()
            return QuestionAnswerResponse(
                question_id=question.id,
                question_key=question.question_key,
                validation_status="warning",
                clarification_message=val_res.message,
                normalized_value=val_res.normalized_value,
                unit=val_res.unit,
                retry_count=question.retry_count,
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

        # Multi-field extraction auto-fill if present
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
                    max_tokens=6000,
                )

                parsed_dict = PlanValidator.extract_and_parse_json(raw_response)
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
                disliked_items={"items": []},
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
    def _add_disliked_item(cls, plan: PatientPlan, item_name: str) -> bool:
        """Helper to safely record a disliked/excluded item in the plan's persistent list."""
        if not item_name or not str(item_name).strip():
            return False
        clean_name = str(item_name).strip().lower()

        current_data = plan.disliked_items or {}
        if not isinstance(current_data, dict):
            current_data = {"items": []}
        items_list = current_data.get("items", [])
        if not isinstance(items_list, list):
            items_list = []

        if clean_name not in [str(i).lower() for i in items_list]:
            items_list.append(clean_name)
            current_data["items"] = items_list
            plan.disliked_items = current_data
            flag_modified(plan, "disliked_items")
            logger.info("Added '%s' to disliked_items for plan %s. All disliked: %s", clean_name, plan.id, items_list)
            return True
        return False

    @classmethod
    def _get_disliked_items(cls, plan: PatientPlan) -> List[str]:
        """Return list of disliked item strings for a plan."""
        if not plan.disliked_items:
            return []
        if isinstance(plan.disliked_items, dict):
            return plan.disliked_items.get("items", [])
        if isinstance(plan.disliked_items, list):
            return plan.disliked_items
        return []

    @classmethod
    def _parse_proposed_mod_from_text(cls, text: str) -> Optional[Dict[str, Any]]:
        """Extract and parse PROPOSED_MODIFICATION JSON block from LLM output."""
        if "PROPOSED_MODIFICATION:" not in text:
            return None
        parts = text.split("PROPOSED_MODIFICATION:", 1)
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
                    return mod_dict
                else:
                    logger.warning("Proposed modification contained medication terms. Dropping modification.")
        except Exception as e:
            logger.warning("Failed to parse PROPOSED_MODIFICATION JSON: %s", e)
        return None

    @classmethod
    async def _generate_next_alternative_reply(
        cls,
        session: AsyncSession,
        plan: PatientPlan,
        declined_title: str,
        original_title: str,
    ) -> PatientPlanDiscussion:
        """
        When patient declines a proposed alternative, automatically calls Groq LLM
        to suggest the next distinct alternative while respecting all disliked items.
        """
        disliked_list = cls._get_disliked_items(plan)
        disliked_str = ", ".join(disliked_list) if disliked_list else "None"

        schedule_summary = "\n".join(
            f"- [{item.id}] {item.time_of_day} ({item.category}): {item.title} — {item.description}"
            for item in plan.items if item.is_active
        )

        prompt = (
            f"The patient is customizing their wellness plan titled '{plan.title}'.\n"
            f"Current Schedule:\n{schedule_summary}\n\n"
            f"The patient needs an alternative to replace '{original_title}'.\n"
            f"The previous suggestion '{declined_title}' was DECLINED by the patient.\n"
            f"CURRENTLY DISLIKED / EXCLUDED ITEMS: {disliked_str}.\n\n"
            f"CRITICAL DIRECTIVES:\n"
            f"1. You MUST suggest a fresh, healthy, and appetizing alternative for '{original_title}'.\n"
            f"2. You are strictly forbidden from suggesting any item in the excluded list ({disliked_str}) or '{declined_title}'.\n"
            f"3. In your friendly message (in Roman Urdu if user writes in Urdu/Roman Urdu, or English with English letters only; NO Nastaliq/Arabic characters):\n"
            f"   - Acknowledge that they didn't like '{declined_title}'.\n"
            f"   - Suggest a new alternative and explain why it's a great fit.\n"
            f"   - Nutritional Comparison: What '{original_title}' provided vs what this new alternative provides (calories, protein, carbs, vitamins).\n"
            f"   - The overall effect on their daily plan and health goal.\n"
            f"4. End your message with exactly one PROPOSED_MODIFICATION JSON:\n"
            f"   PROPOSED_MODIFICATION: {{\"action_type\": \"swap\", \"original_title\": \"{original_title}\", \"proposed_title\": \"<new option>\", \"proposed_description\": \"<description with nutrients and goal impact>\", \"proposed_time\": \"HH:MM\", \"proposed_category\": \"<category>\", \"calories\": 160, \"protein_g\": 4.0, \"carbs_g\": 30.0, \"fat_g\": 2.0, \"fiber_g\": 3.5, \"calories_burned\": null, \"impact_summary\": \"<effect on daily plan>\", \"disliked_item_added\": \"{declined_title}\"}}\n"
            f"5. NO MEDICATIONS: strictly forbidden from mentioning medications."
        )

        messages = [
            {"role": "system", "content": "You are an empathetic, clinical wellness advisor specializing in tailored meal and fitness alternatives."},
            {"role": "user", "content": prompt},
        ]

        try:
            ai_text = await cls._call_groq_api(messages, temperature=0.3, max_tokens=1800)
        except Exception as e:
            logger.error("Error generating next alternative: %s", e)
            ai_text = f"Understood! I've noted that you don't like {declined_title}. Would you prefer another healthy option instead?"

        # Parse proposed modification if present
        proposed_mod = cls._parse_proposed_mod_from_text(ai_text)
        if proposed_mod and "PROPOSED_MODIFICATION:" in ai_text:
            ai_text = ai_text.split("PROPOSED_MODIFICATION:")[0].strip()

        msg = PatientPlanDiscussion(
            plan_id=plan.id,
            role="assistant",
            content=ai_text,
            proposed_modifications=proposed_mod,
        )
        return await PatientPlanRepository.add_discussion_message(session, msg)

    @classmethod
    async def _analyze_chat_intent_via_ai(
        cls,
        plan: PatientPlan,
        clean_text: str,
        pending_data: Optional[Dict[str, Any]] = None,
        recent_discussions: Optional[List[PatientPlanDiscussion]] = None,
    ) -> Dict[str, Any]:
        """
        Intelligent multi-lingual intent analyzer for plan chat interactions.
        Uses the LLM to understand what the patient wants in context of their active plan
        and any pending modification. Eliminates all hardcoded keyword lists.

        Returns a dict:
        {
            "intent": "CONFIRM_MODIFICATION" | "REJECT_ALTERNATIVE" | "CANCEL_KEEP_ORIGINAL" |
                      "CHANGE_GOAL" | "NUTRITION_INFO_QUERY" | "PLAN_DISCUSSION_OR_MODIFY",
            "rejected_item": Optional[str],
            "is_medication_inquiry": bool,
            "explanation": str
        }
        """
        active_items = [f"- {i.time_of_day} ({i.category}): {i.title}" for i in plan.items if i.is_active]
        schedule_ctx = "\n".join(active_items) if active_items else "No items currently scheduled."

        if pending_data:
            action_type = pending_data.get("action_type", "swap")
            orig_t = pending_data.get("original_title") or "None"
            prop_t = pending_data.get("proposed_title") or "None"
            pending_desc = (
                f"ACTIVE PENDING PROPOSAL WAITING FOR PATIENT CONFIRMATION:\n"
                f"- Action: {action_type}\n"
                f"- Original Item: {orig_t}\n"
                f"- Proposed Alternative: {prop_t}\n"
                f"- Impact Summary: {pending_data.get('impact_summary', 'N/A')}\n"
                f"Note: An alternative or adjustment is currently proposed. The patient may approve it, decline it for another option, or cancel."
            )
        else:
            pending_desc = "NO PENDING MODIFICATION IS CURRENTLY WAITING."

        recent_msgs_text = ""
        if recent_discussions:
            snippets = []
            for d in recent_discussions[-4:]:
                if d.role in ("user", "assistant"):
                    snippets.append(f"{d.role.capitalize()}: {d.content[:150]}")
            if snippets:
                recent_msgs_text = "Recent Chat History:\n" + "\n".join(snippets)

        system_prompt = (
            "You are an intelligent clinical intent classifier and dialogue router for a digital health wellness platform.\n"
            "Your task is to analyze the patient's incoming chat message in the context of their active wellness plan and any pending modification.\n"
            "The patient may write in English, Urdu, Roman Urdu, or informal slang/colloquial phrasing "
            "(e.g., 'theek hy done karo', 'ye nahi khana dusra do', 'mujhe maza nahi dega koi aur batao', "
            "'rehne do purana hi sahi tha', '1 katori daal me kitna protein ha', 'chalo laga do', 'ab weight gain karna hai').\n\n"
            f"PLAN TITLE: {plan.title}\n\n"
            f"ACTIVE SCHEDULE:\n{schedule_ctx}\n\n"
            f"{pending_desc}\n\n"
            f"{recent_msgs_text}\n\n"
            "ANALYZE CAREFULLY AND SELECT ONE OF THE FOLLOWING 6 INTENTS:\n\n"
            "1. 'CONFIRM_MODIFICATION':\n"
            "   - APPLIES ONLY IF there is an active pending modification waiting for confirmation.\n"
            "   - The user accepts, agrees with, confirms, or approves applying the proposed change.\n"
            "   - Examples (in English, Urdu, Roman Urdu, etc.): 'yes', 'confirm', 'apply', 'looks good', 'theek hai', 'haan kr do', 'done karo', 'yehi final karo', 'bilkul chalega', 'laga do bhai', 'sounds great do it', 'apply this', 'sure', 'approved'.\n\n"
            "2. 'REJECT_ALTERNATIVE':\n"
            "   - APPLIES ONLY IF there is an active pending modification.\n"
            "   - The user declines, dislikes, or rejects the suggested alternative and wants ANOTHER / DIFFERENT healthy option or suggestion.\n"
            "   - Examples: 'no', 'reject', 'don't want this', 'ye nahi khana', 'ye maza nahi dega koi aur batao', 'dusra option do', 'kuch aur dikhao', 'not this one', 'nahi pasand', 'give another choice', 'hate oats give something else'.\n\n"
            "3. 'CANCEL_KEEP_ORIGINAL':\n"
            "   - APPLIES IF there is a pending modification.\n"
            "   - The user wants to cancel or drop the proposed modification entirely and keep their original schedule intact without asking for any new alternative.\n"
            "   - Examples: 'rehne do purana hi theek tha', 'never mind keep original', 'cancel changes', 'chhor do kuch mat badlo', 'leave it as is', 'keep current plan'.\n\n"
            "4. 'CHANGE_GOAL':\n"
            "   - The user wants to change their high-level overall health goal (e.g. switch from weight loss to muscle gain), restart from scratch, or delete/wipe/cancel their entire wellness plan.\n"
            "   - Examples: 'change my goal', 'different goal', 'ab weight gain karna hai', 'cancel my whole plan', 'delete plan', 'start over with a new goal', 'reset my plan'.\n\n"
            "5. 'NUTRITION_INFO_QUERY':\n"
            "   - The user is asking a standalone informational question about food, calories, nutrients, vitamins, recipes, exercises, or metabolism (and is NOT asking to edit/modify their daily routine).\n"
            "   - Can mention ANY food or exercise in the world (e.g., haleem, biryani, matcha, chia seeds, paratha, banana, walking, cycling, etc.).\n"
            "   - Examples: '1 plate biryani mein kitni calories hoti hain?', 'how many calories in 2 boiled eggs?', 'running 30 mins burns how much?', 'is avocado good for cholesterol?', 'benefits of green tea', 'daal me kitna protein hota hai'.\n\n"
            "6. 'PLAN_DISCUSSION_OR_MODIFY':\n"
            "   - The user is asking to modify their plan schedule (swap meals, add a new snack/workout, remove an item, adjust timings/routine due to work or schedule constraints), OR asking questions specific to why something is in their daily schedule.\n"
            "   - Examples: 'I don't like banana in my breakfast', 'add green tea at 4pm', 'remove evening jog', 'I work 9 to 5 reschedule my meals', 'replace eggs with vegetarian option', 'why did you put oats in breakfast?'.\n\n"
            "OUTPUT FORMAT:\n"
            "Return ONLY a valid JSON object matching:\n"
            "{\n"
            '  "intent": "CONFIRM_MODIFICATION" | "REJECT_ALTERNATIVE" | "CANCEL_KEEP_ORIGINAL" | "CHANGE_GOAL" | "NUTRITION_INFO_QUERY" | "PLAN_DISCUSSION_OR_MODIFY",\n'
            '  "rejected_item": "Specific food or exercise the user disliked or rejected if applicable (or null)",\n'
            '  "is_medication_inquiry": true | false,\n'
            '  "explanation": "Brief 1-sentence reasoning"\n'
            "}"
        )

        try:
            raw_eval = await cls._call_groq_api(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f'Patient message: "{clean_text}"'},
                ],
                temperature=0.0,
                max_tokens=250,
            )
            parsed = PlanValidator.extract_and_parse_json(raw_eval)
            if isinstance(parsed, dict) and "intent" in parsed:
                return parsed
        except Exception as e:
            logger.warning("AI intent analysis failed (%s), using safe semantic fallback.", e)

        # Safe fallback only if AI call fails
        lowered = clean_text.lower().strip()
        if pending_data:
            if any(w in lowered for w in ("yes", "apply", "theek", "haan", "done", "confirm")):
                return {"intent": "CONFIRM_MODIFICATION", "rejected_item": None, "is_medication_inquiry": False}
            if any(w in lowered for w in ("keep", "rehne do", "original", "leave it")):
                return {"intent": "CANCEL_KEEP_ORIGINAL", "rejected_item": None, "is_medication_inquiry": False}
            if any(w in lowered for w in ("no", "nahi", "reject", "aur", "dusra", "different")):
                return {"intent": "REJECT_ALTERNATIVE", "rejected_item": pending_data.get("proposed_title"), "is_medication_inquiry": False}
        return {"intent": "PLAN_DISCUSSION_OR_MODIFY", "rejected_item": None, "is_medication_inquiry": False}

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
        - Intelligent LLM-based intent analysis for any phrasing/language (English, Roman Urdu, slang)
        - Applies pending modifications dynamically when confirmed
        - Chained alternative proposals with dislike tracking when an alternative is rejected
        - Safely redirects complete goal changes
        - Handles standalone nutrition & exercise inquiries seamlessly
        - Rejects medication prescription attempts and offers natural alternatives
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

        # Find any pending modification on the plan
        item_mod_pairs, pending_data, pending_disc = cls._find_pending_modification(plan)

        # Intelligent Intent & Decision Analysis via LLM
        analysis = await cls._analyze_chat_intent_via_ai(
            plan=plan,
            clean_text=clean_text,
            pending_data=pending_data,
            recent_discussions=plan.discussions[-6:] if len(plan.discussions) > 6 else plan.discussions,
        )
        intent = analysis.get("intent", "PLAN_DISCUSSION_OR_MODIFY")
        is_med_inquiry = bool(analysis.get("is_medication_inquiry")) or PlanValidator.is_medication_inquiry(clean_text)

        # 1. Nutrition Information Queries (standalone nutrition/exercise questions)
        if intent == "NUTRITION_INFO_QUERY":
            from app.services.nutrition_info_service import NutritionInfoService

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

        # 2. Positive Confirmation of Pending Modification
        if intent == "CONFIRM_MODIFICATION" and pending_data:
            if not item_mod_pairs:
                action = pending_data.get("action_type", "swap")
                if action == "add":
                    item_mod_pairs = [(None, pending_data)]
                else:
                    target = cls._find_item_for_mod(plan, pending_data)
                    if not target and plan.items:
                        target = plan.items[0]
                    if target:
                        item_mod_pairs = [(target, pending_data)]

            if item_mod_pairs:
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

        # 3. Rejection of Proposed Alternative (with alternative chaining)
        if intent == "REJECT_ALTERNATIVE" and pending_data:
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

            prop_title = pending_data.get("proposed_title")
            orig_title = pending_data.get("original_title")
            action_type = pending_data.get("action_type", "swap")
            rejected_item = analysis.get("rejected_item") or prop_title

            # Persist rejected alternative to disliked items so it won't be suggested again
            if rejected_item:
                cls._add_disliked_item(plan, rejected_item)
            if prop_title and prop_title != rejected_item:
                cls._add_disliked_item(plan, prop_title)
            if pending_data.get("disliked_item_added"):
                cls._add_disliked_item(plan, pending_data["disliked_item_added"])

            # If user declined a suggested swap alternative, automatically suggest the next alternative
            if action_type == "swap" and orig_title:
                alt_reply = await cls._generate_next_alternative_reply(
                    session=session,
                    plan=plan,
                    declined_title=rejected_item or prop_title or "this option",
                    original_title=orig_title,
                )
                plan.discussions.append(alt_reply)
                await session.commit()
                await session.refresh(alt_reply)
                return cls._format_discussion_response(alt_reply)

            # Explicit cancellation
            rejection_reply = PatientPlanDiscussion(
                plan_id=plan.id,
                role="assistant",
                content="No problem! I have cancelled this proposed change and kept your schedule unchanged.",
            )
            await PatientPlanRepository.add_discussion_message(session, rejection_reply)
            plan.discussions.append(rejection_reply)
            await session.commit()
            await session.refresh(rejection_reply)
            return cls._format_discussion_response(rejection_reply)

        # 4. Explicit Cancel & Keep Original Plan
        if intent == "CANCEL_KEEP_ORIGINAL" and pending_data:
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

            keep_reply = PatientPlanDiscussion(
                plan_id=plan.id,
                role="assistant",
                content="No problem! I have kept your current plan unchanged.",
            )
            await PatientPlanRepository.add_discussion_message(session, keep_reply)
            plan.discussions.append(keep_reply)
            await session.commit()
            await session.refresh(keep_reply)
            return cls._format_discussion_response(keep_reply)

        # 5. Goal Change / Cancel Entire Plan Request
        if intent == "CHANGE_GOAL":
            goal_reply = PatientPlanDiscussion(
                plan_id=plan.id,
                role="assistant",
                content=(
                    f"A patient can only have **one plan at a time**. Your current plan is configured for **{plan.title}**.\n\n"
                    "If you would like to start a brand new plan with a different goal, please click **Cancel Plan** at the top of your dashboard. "
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

        disliked_items_list = cls._get_disliked_items(plan)
        disliked_str = ", ".join(disliked_items_list) if disliked_items_list else "None"

        chat_system_prompt = (
            "You are an empathetic, expert wellness assistant discussing the patient's daily health plan.\n\n"
            f"PLAN TITLE: {plan.title}\n"
            f"SUMMARY: {plan.summary}\n\n"
            f"CURRENT SCHEDULE:\n{schedule_summary}\n\n"
            f"CURRENTLY DISLIKED / EXCLUDED ITEMS: {disliked_str}\n"
            "CRITICAL: You are strictly forbidden from suggesting any food, ingredient, or activity from this excluded list.\n\n"
            "CRITICAL RULES:\n"
            "1. NO MEDICATIONS: You are strictly forbidden from prescribing, recommending, or suggesting pharmaceutical drugs, pills, tablets, or clinical dosages.\n"
            "2. POLITELY DECLINE & OFFER NATURAL ALTERNATIVES: If the patient asks for any medicine or prescription, politely decline by explaining that you cannot prescribe medications and advise them to consult a licensed doctor, and provide safe natural, dietary, and lifestyle alternatives instead.\n"
            "3. Ground your explanations in their current plan.\n"
            "4. STRICT LANGUAGE & SCRIPT RULES:\n"
            "   - If the patient writes in English, reply strictly in English.\n"
            "   - If the patient writes in Urdu, Roman Urdu, or Hindi, reply STRICTLY in Roman Urdu (using Latin/English alphabet, e.g. 'Aap ke plan mein breakfast ko update kar diya gaya hai...').\n"
            "   - NEVER write in traditional Urdu script (اردو رسم الخط / Arabic script). Absolutely NO Nastaliq/Arabic characters. Even if the patient writes in Urdu script, your response MUST be in Roman Urdu with English letters.\n\n"
            "HOW TO HANDLE DIFFERENT REQUEST TYPES:\n\n"
            "A) DISLIKING AN ITEM OR ASKING FOR AN ALTERNATIVE (e.g. 'I don't like banana', 'mujhe kela pasand nahi', 'replace eggs with vegetarian', 'change workout'):\n"
            "   1. Identify the disliked item and the corresponding schedule item.\n"
            f"   2. Suggest a healthy, delicious alternative that is NOT in the excluded list ({disliked_str}).\n"
            "   3. In your response, clearly provide:\n"
            "      - Nutritional Comparison: What the original item provided (calories, protein, carbs, vitamins) vs. what the new alternative provides.\n"
            "      - Plan Impact: How this swap affects their daily caloric intake, macro balance, and goal.\n"
            "   4. End your response with exactly ONE proposed modification in this format:\n"
            "      PROPOSED_MODIFICATION: {\"action_type\": \"swap\", \"item_id\": \"<matching-item-uuid>\", \"original_title\": \"<old>\", \"proposed_title\": \"<new title>\", \"proposed_description\": \"<new description including caloric/macro details>\", \"proposed_time\": \"HH:MM\", \"proposed_category\": \"<morning_routine|breakfast|lunch|evening_activity|dinner|night_routine>\", \"calories\": 350, \"protein_g\": 15.0, \"carbs_g\": 45.0, \"fat_g\": 8.0, \"fiber_g\": 5.0, \"calories_burned\": null, \"impact_summary\": \"<summary of impact on daily plan>\", \"disliked_item_added\": \"<name of disliked item>\"}\n\n"
            "B) USER ASKING TO ADD AN ITEM (e.g. 'Add green tea at 4pm', 'Add 20 min walk', 'Can I add 15 almonds at 5pm?'):\n"
            "   1. Explain what nutrients this added item provides (calories, protein, carbs, fat, fiber) OR how many calories it burns (for workouts).\n"
            "   2. Explain the overall effect on their daily plan and goals (e.g. caloric impact, hydration, energy).\n"
            "   3. Ask if they want to confirm adding it, and end your response with:\n"
            "      PROPOSED_MODIFICATION: {\"action_type\": \"add\", \"proposed_title\": \"<new title>\", \"proposed_description\": \"<description>\", \"proposed_time\": \"HH:MM\", \"proposed_category\": \"<category>\", \"calories\": 105, \"protein_g\": 4.0, \"carbs_g\": 3.0, \"fat_g\": 9.0, \"fiber_g\": 2.0, \"calories_burned\": null, \"impact_summary\": \"+105 kcal, 4g protein added to daily intake\"}\n\n"
            "C) USER ASKING TO COMPLETELY REMOVE AN ITEM WITHOUT ALTERNATIVE (e.g. 'Remove evening snack completely', 'delete morning jog'):\n"
            "   1. Explain clearly the consequences and overall impact on their plan (e.g. caloric deficit increase, potential fatigue, missing protein target).\n"
            "   2. Ask if they are sure they want to remove it, and end your response with:\n"
            "      PROPOSED_MODIFICATION: {\"action_type\": \"remove\", \"item_id\": \"<matching-item-uuid>\", \"original_title\": \"<old title>\", \"impact_summary\": \"Reduces daily intake by 220 kcal and 8g protein\"}\n\n"
            "D) SCHEDULE / LIFESTYLE CONSTRAINTS & UNAVAILABILITY (e.g. 'I work 9-5', 'I have no time between 10 am and 5 pm', 'I am busy from 10:00 to 17:00'):\n"
            "   1. Identify EVERY SINGLE schedule item currently scheduled within or overlapping that unavailable window.\n"
            "   2. Reschedule ALL of those items to suitable times outside that window.\n"
            "   3. In your chat message, clearly list each moved item: old time -> new time.\n"
            "   4. YOU MUST output ALL of the adjusted items together in PROPOSED_MODIFICATION as a JSON array:\n"
            "   PROPOSED_MODIFICATION: [\n"
            "     {\"item_id\": \"<uuid-1>\", \"original_title\": \"<title 1>\", \"proposed_title\": \"<new title 1>\", \"proposed_description\": \"<desc 1>\", \"proposed_time\": \"09:30\", \"proposed_category\": \"lunch\", \"action_type\": \"reschedule\", \"calories\": 450, \"protein_g\": 20.0, \"carbs_g\": 60.0, \"fat_g\": 12.0, \"fiber_g\": 6.0, \"calories_burned\": null},\n"
            "     {\"item_id\": \"<uuid-2>\", \"original_title\": \"<title 2>\", \"proposed_title\": \"<new title 2>\", \"proposed_description\": \"<desc 2>\", \"proposed_time\": \"18:00\", \"proposed_category\": \"evening_activity\", \"action_type\": \"reschedule\", \"calories\": null, \"protein_g\": null, \"carbs_g\": null, \"fat_g\": null, \"fiber_g\": null, \"calories_burned\": 200}\n"
            "   ]\n\n"
            "E) GENERAL QUESTIONS (e.g. 'why this food?', 'is brown rice good?', 'how much water should I drink?'):\n"
            "   Answer helpfully grounded in their plan. No modification needed.\n\n"
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
        proposed_mod = cls._parse_proposed_mod_from_text(ai_response_text)
        if proposed_mod and "PROPOSED_MODIFICATION:" in ai_response_text:
            ai_response_text = ai_response_text.split("PROPOSED_MODIFICATION:")[0].strip()

        # Record any disliked item identified by LLM
        if proposed_mod:
            disliked_add = proposed_mod.get("disliked_item_added")
            if disliked_add:
                cls._add_disliked_item(plan, disliked_add)

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
        # For addition requests, there is no existing item to match
        if mod_item.get("action_type") == "add":
            return None

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

        # Fallback: match by category if available
        prop_cat = mod_item.get("proposed_category") or mod_item.get("category")
        if prop_cat:
            matched = next((i for i in candidates if i.category.lower() == str(prop_cat).lower()), None)
            if matched:
                return matched

        return None

    @classmethod
    def _find_pending_modification(
        cls, plan: PatientPlan
    ) -> Tuple[List[Tuple[Optional[PatientPlanItem], Dict[str, Any]]], Optional[Dict[str, Any]], Optional[PatientPlanDiscussion]]:
        """
        Finds any pending modification in the discussion history.
        Returns (item_mod_pairs, mod_data, disc).
        """
        for disc in reversed(plan.discussions):
            if disc.proposed_modifications:
                mod_status = disc.proposed_modifications.get("status", "pending")
                if mod_status == "pending":
                    mod = disc.proposed_modifications
                    # Multi-item modification
                    if "items" in mod and isinstance(mod["items"], list):
                        item_mod_pairs = []
                        used_ids: Set[uuid.UUID] = set()
                        for m in mod["items"]:
                            action = m.get("action_type", "swap")
                            if action == "add":
                                item_mod_pairs.append((None, m))
                            else:
                                target = cls._find_item_for_mod(plan, m, exclude_ids=used_ids)
                                if not target and plan.items:
                                    target = next((i for i in plan.items if i.id not in used_ids), plan.items[0])
                                if target:
                                    used_ids.add(target.id)
                                    item_mod_pairs.append((target, m))
                        if item_mod_pairs:
                            return item_mod_pairs, mod, disc
                    else:
                        # Single item modification
                        action = mod.get("action_type", "swap")
                        if action == "add":
                            return [(None, mod)], mod, disc
                        target = cls._find_item_for_mod(plan, mod)
                        if not target and plan.items and action != "add":
                            target = plan.items[0]
                        if target or action == "add":
                            return [(target, mod)], mod, disc
        return [], None, None

    @classmethod
    async def _apply_modification_internal(
        cls,
        session: AsyncSession,
        plan: PatientPlan,
        item_mod_pairs: List[Tuple[Optional[PatientPlanItem], Dict[str, Any]]],
        mod_data: Dict[str, Any],
        user_author: User,
        pending_disc: Optional[PatientPlanDiscussion] = None,
    ) -> PatientPlanDiscussion:
        """Internal helper to apply approved modifications (swap, add, remove, reschedule) with atomic versioning."""
        valid_categories = {"morning_routine", "breakfast", "lunch", "evening_activity", "dinner", "night_routine", "snack", "exercise", "hydration"}
        changes_summaries = []
        revision_items = []

        for item, m in item_mod_pairs:
            action_type = m.get("action_type", "swap")

            # Persist any disliked item associated with this modification
            disliked_item = m.get("disliked_item_added")
            if disliked_item:
                cls._add_disliked_item(plan, disliked_item)

            if action_type == "add":
                # Create a new scheduled plan item
                proposed_cat = m.get("proposed_category", "general")
                if proposed_cat not in valid_categories:
                    proposed_cat = "snack"

                new_item = PatientPlanItem(
                    plan_id=plan.id,
                    time_of_day=m.get("proposed_time") or "12:00",
                    category=proposed_cat,
                    title=PlanValidator.sanitize_text(m.get("proposed_title") or "New Activity", 255),
                    description=PlanValidator.sanitize_text(m.get("proposed_description") or "", 1000),
                    order_index=len(plan.items),
                    is_active=True,
                    calories=int(m["calories"]) if m.get("calories") is not None else None,
                    protein_g=float(m["protein_g"]) if m.get("protein_g") is not None else None,
                    carbs_g=float(m["carbs_g"]) if m.get("carbs_g") is not None else None,
                    fat_g=float(m["fat_g"]) if m.get("fat_g") is not None else None,
                    fiber_g=float(m["fiber_g"]) if m.get("fiber_g") is not None else None,
                    calories_burned=int(m["calories_burned"]) if m.get("calories_burned") is not None else None,
                )
                session.add(new_item)
                plan.items.append(new_item)

                changes_summaries.append(f"Added **{new_item.title}** at **{new_item.time_of_day}**")
                revision_items.append({
                    "action": "add",
                    "new_title": new_item.title,
                    "new_time": new_item.time_of_day,
                })

            elif action_type == "remove":
                if item:
                    item.is_active = False
                    changes_summaries.append(f"Removed **{item.title}** from your daily schedule")
                    revision_items.append({
                        "action": "remove",
                        "item_id": str(item.id),
                        "removed_title": item.title,
                    })

            else:
                # Default: swap, reschedule, or update existing item
                if item:
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
                    if "calories" in m:
                        item.calories = int(m["calories"]) if m["calories"] is not None else None
                    if "protein_g" in m:
                        item.protein_g = float(m["protein_g"]) if m["protein_g"] is not None else None
                    if "carbs_g" in m:
                        item.carbs_g = float(m["carbs_g"]) if m["carbs_g"] is not None else None
                    if "fat_g" in m:
                        item.fat_g = float(m["fat_g"]) if m["fat_g"] is not None else None
                    if "fiber_g" in m:
                        item.fiber_g = float(m["fiber_g"]) if m["fiber_g"] is not None else None
                    if "calories_burned" in m:
                        item.calories_burned = int(m["calories_burned"]) if m["calories_burned"] is not None else None

                    time_change = f" moved from **{old_time}** to **{item.time_of_day}**" if old_time != item.time_of_day else ""
                    changes_summaries.append(f"**{item.title}**{time_change}")
                    revision_items.append({
                        "action": "modify",
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
            reply_content = f"✅ Done! I've updated your daily plan with all {len(item_mod_pairs)} adjustments:\n{items_list}\nhave been successfully applied."

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

        if payload.expected_version is not None and plan.version != payload.expected_version:
            logger.warning(
                "Version mismatch: plan.version=%s != expected=%s",
                plan.version, payload.expected_version,
            )
            raise ConflictError("This plan was modified in another session. Please refresh to view the latest version.")

        # 1. Search for pending modification in discussions
        item_mod_pairs, pending_data, pending_disc = cls._find_pending_modification(plan)

        # 2. Fallback to payload.modification if not found in DB discussions
        if not pending_data and payload.modification:
            raw_mod = payload.modification
            if hasattr(raw_mod, "model_dump"):
                pending_data = raw_mod.model_dump(exclude_unset=True)
            elif isinstance(raw_mod, dict):
                pending_data = dict(raw_mod)

            if pending_data:
                logger.info("apply_modification: using fallback payload.modification: %s", pending_data.get("proposed_title"))
                prop_t = pending_data.get("proposed_title")
                for d in reversed(plan.discussions):
                    if d.proposed_modifications:
                        d_prop = d.proposed_modifications.get("proposed_title")
                        if prop_t and d_prop == prop_t:
                            pending_disc = d
                            break
                if not pending_disc:
                    for d in reversed(plan.discussions):
                        if d.proposed_modifications:
                            pending_disc = d
                            break

        # 3. Resolve item_mod_pairs if pending_data is present but item_mod_pairs was empty
        if pending_data and not item_mod_pairs:
            if "items" in pending_data and isinstance(pending_data["items"], list):
                used_ids: Set[uuid.UUID] = set()
                for m in pending_data["items"]:
                    action = m.get("action_type", "swap")
                    if action == "add":
                        item_mod_pairs.append((None, m))
                    else:
                        target = cls._find_item_for_mod(plan, m, exclude_ids=used_ids)
                        if not target and plan.items:
                            target = next((i for i in plan.items if i.id not in used_ids), plan.items[0])
                        if target:
                            used_ids.add(target.id)
                            item_mod_pairs.append((target, m))
            else:
                action = pending_data.get("action_type", "swap")
                if action == "add":
                    item_mod_pairs = [(None, pending_data)]
                else:
                    target = cls._find_item_for_mod(plan, pending_data)
                    if not target and plan.items:
                        target = plan.items[0]
                    if target:
                        item_mod_pairs = [(target, pending_data)]

        # 4. Handle REJECT action (Declining should never fail or raise 404)
        if payload.action == "reject":
            if pending_data:
                pending_data["status"] = "rejected"
            if pending_disc and pending_disc.proposed_modifications:
                p_mod = dict(pending_disc.proposed_modifications)
                p_mod["status"] = "rejected"
                pending_disc.proposed_modifications = p_mod
                flag_modified(pending_disc, "proposed_modifications")

            prop_title = pending_data.get("proposed_title") if pending_data else None
            orig_title = pending_data.get("original_title") if pending_data else None
            action_type = pending_data.get("action_type", "swap") if pending_data else "swap"

            # Persist rejected alternative to disliked items so it won't be suggested again
            if prop_title:
                cls._add_disliked_item(plan, prop_title)
            if pending_data and pending_data.get("disliked_item_added"):
                cls._add_disliked_item(plan, pending_data["disliked_item_added"])

            # If user declined a suggested alternative swap, automatically suggest the NEXT alternative!
            if action_type == "swap" and orig_title:
                alt_reply = await cls._generate_next_alternative_reply(
                    session=session,
                    plan=plan,
                    declined_title=prop_title or "this option",
                    original_title=orig_title,
                )
                plan.discussions.append(alt_reply)
            else:
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
                    d_mod["status"] = "rejected"
                    d.proposed_modifications = d_mod
                    flag_modified(d, "proposed_modifications")

            target_plan_id = plan.id
            target_user_id = patient_user.id
            await session.commit()
            session.expire_all()
            full_plan = await PatientPlanRepository.get_plan_by_id(session, target_plan_id, target_user_id)
            return cls._format_plan_response(full_plan)

        # 5. Handle ACCEPT action
        if not pending_data or not item_mod_pairs:
            # Check if this modification was already applied (idempotent success)
            for d in reversed(plan.discussions):
                if d.proposed_modifications and d.proposed_modifications.get("status") == "applied":
                    logger.info("apply_modification: modification was already applied.")
                    return cls._format_plan_response(plan)

            logger.warning("No pending modification found to apply. Discussions count: %s", len(plan.discussions))
            raise NotFoundError("No pending modification found to apply.")

        await cls._apply_modification_internal(
            session=session,
            plan=plan,
            item_mod_pairs=item_mod_pairs,
            mod_data=pending_data,
            user_author=patient_user,
            pending_disc=pending_disc,
        )

        # Mark all pending modifications in discussions as resolved
        for d in plan.discussions:
            if d.proposed_modifications:
                status = d.proposed_modifications.get("status")
                if status == "pending" or (pending_disc and d.id == pending_disc.id):
                    d_mod = dict(d.proposed_modifications)
                    d_mod["status"] = "applied"
                    d.proposed_modifications = d_mod
                    flag_modified(d, "proposed_modifications")

        target_plan_id = plan.id
        target_user_id = patient_user.id
        await session.commit()
        session.expire_all()
        full_plan = await PatientPlanRepository.get_plan_by_id(session, target_plan_id, target_user_id)
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
            disliked_items=cls._get_disliked_items(plan),
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

    # ── Legacy Intent Classifier Stub (backward compatibility) ────────────────
    @classmethod
    def _classify_chat_intent(cls, text: str) -> str:
        """
        Legacy fallback stub. All plan chat intent classification is now handled
        dynamically and intelligently via cls._analyze_chat_intent_via_ai.
        """
        return "plan_modification"

