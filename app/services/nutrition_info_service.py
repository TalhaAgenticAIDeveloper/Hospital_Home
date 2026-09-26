"""
Nutrition Information Agent Service.

Standalone AI agent for answering food, exercise, and general nutrition
queries.  Not tied to any specific plan — provides pure nutritional
information.

Features:
- Per-food calorie / macro breakdown (including Pakistani / South Asian foods)
- Exercise calorie-burn estimates
- Multi-language support (English, Urdu, Roman Urdu)
- Strict medication blocker (never prescribes)
- Structured JSON extraction alongside friendly human-readable response
"""

import json
import re
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.nutrition_info import (
    NutritionBreakdown,
    NutritionInfoResponse,
)
from app.services.plan_validator import PlanValidator

logger = get_logger(__name__)
settings = get_settings()

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── System Prompt ────────────────────────────────────────────────────────────

NUTRITION_AGENT_SYSTEM_PROMPT = """\
You are **NutriGuide**, a knowledgeable, friendly nutrition and fitness information assistant embedded in a healthcare platform.
Your ONLY purpose is to provide accurate nutritional facts, calorie estimates, macro breakdowns, and exercise calorie-burn estimates.

CRITICAL RULES:
1. NEVER prescribe, recommend, or mention any medication, drug, tablet, pill, syrup, supplement pill, or clinical dosage. You are NOT a doctor.
2. If the patient asks for medical advice, medications, or diagnosis, politely decline and advise them to consult their doctor.
3. Base your answers on standard nutritional databases (USDA, Pakistani food composition tables, etc.).
4. Always mention the approximate serving/portion size for your calorie estimates.
5. STRICT LANGUAGE & SCRIPT RULES:
   - If the patient asks in English, reply strictly in English.
   - If the patient asks in Urdu, Roman Urdu, or Hindi, reply STRICTLY in Roman Urdu (using English/Latin alphabet, e.g., "1 kele (banana) mein taqreeban 105 calories aur 1.3g protein hoti hai...").
   - NEVER write in traditional Urdu script (اردو رسم الخط / Arabic script). Absolutely NO Nastaliq or Arabic letters. Even if the patient writes their question in Urdu script, you MUST reply in Roman Urdu with English letters.
6. Be warm, concise, and specific — give numbers, not vague statements.

FOOD QUERIES:
- When asked about a food item, provide: calories (kcal), protein (g), carbs (g), fat (g), fiber (g) per standard serving.
- For Pakistani/South Asian foods (roti, paratha, biryani, daal, nihari, halwa puri, etc.), use culturally accurate portion sizes and recipes.
- If the user mentions a quantity, adjust the values accordingly.

EXERCISE QUERIES:
- When asked about exercise calorie burn, estimate for a ~70 kg adult (adjust if weight is given).
- Provide calories burned per duration (e.g., "30 minutes brisk walking ~ 150 kcal").
- Mention that actual burn varies by body weight, intensity, and fitness level.

COMPARISON QUERIES:
- When asked to compare foods or exercises, present a clear side-by-side comparison.

OUTPUT FORMAT:
Your response must have TWO parts separated by the marker `NUTRITION_DATA:`.

Part 1: A friendly, natural language answer to the patient's question.
Part 2: After `NUTRITION_DATA:` — a JSON array of nutrition breakdowns. Each item:
{
  "item_name": "Food or exercise name",
  "serving_size": "e.g. 1 medium (118g) or 30 minutes",
  "calories": 105,
  "protein_g": 1.3,
  "carbs_g": 27,
  "fat_g": 0.4,
  "fiber_g": 3.1,
  "sugar_g": 14,
  "calories_burned": null,
  "duration_minutes": null
}

For exercise items, set calories/protein/carbs/fat/fiber to null and fill calories_burned and duration_minutes instead.
If the query is general and no specific item breakdown applies, output: NUTRITION_DATA: []
"""


class NutritionInfoService:
    """Standalone AI agent for nutrition and exercise information queries."""

    # ── Core Query Handler ───────────────────────────────────────────────────

    @classmethod
    async def ask_nutrition_question(
        cls,
        session: AsyncSession,
        patient_user: User,
        question: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> NutritionInfoResponse:
        """
        Answers a food / exercise / nutrition query using Groq LLM.

        Returns a structured response with:
        - Friendly human-readable answer
        - Optional structured nutritional breakdown
        - Detected query type
        """
        clean_text = PlanValidator.sanitize_text(question, 2000).strip()
        if not clean_text:
            raise ValidationError("Question cannot be empty.")

        # Detect query type for metadata
        query_type = cls._detect_query_type(clean_text)

        # Check for medication inquiry — block it upfront
        if PlanValidator.is_medication_inquiry(clean_text):
            return NutritionInfoResponse(
                answer=(
                    "I'm a nutrition information assistant — I cannot prescribe or recommend any medications, "
                    "drugs, supplements, or clinical treatments. For any medical prescriptions, please consult "
                    "your licensed physician.\n\n"
                    "However, I'm happy to help with any food, nutrition, or exercise-related questions! "
                    "For example, you can ask me about calories in a specific food, protein in daal, "
                    "or how many calories a 30-minute walk burns. 😊"
                ),
                nutrition_data=None,
                query_type="general_nutrition",
            )

        # Build conversation context
        messages = [{"role": "system", "content": NUTRITION_AGENT_SYSTEM_PROMPT}]

        if conversation_history:
            # Include last 4 exchanges for context
            recent = conversation_history[-8:] if len(conversation_history) > 8 else conversation_history
            for msg in recent:
                if msg.get("role") in ("user", "assistant"):
                    messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": clean_text})

        # Call Groq API
        try:
            raw_response = await cls._call_groq_api(
                messages=messages,
                temperature=0.2,
                max_tokens=1500,
            )
        except Exception as e:
            logger.error("NutritionInfoService Groq API error: %s", e)
            return NutritionInfoResponse(
                answer="I'm having trouble connecting right now. Please try asking again in a moment.",
                nutrition_data=None,
                query_type=query_type,
            )

        # Safety check — verify no medication slipped through
        is_unsafe, flagged = PlanValidator.contains_blocked_medication(raw_response)
        if is_unsafe:
            logger.warning("Nutrition agent response contained blocked terms: %s", flagged)
            return NutritionInfoResponse(
                answer=(
                    "I can only provide nutritional and exercise information. "
                    "For medical advice or prescriptions, please consult your doctor.\n\n"
                    "Ask me about any food's calories, protein, or how much a walk burns! 😊"
                ),
                nutrition_data=None,
                query_type="general_nutrition",
            )

        # Parse response into answer + structured data
        answer_text, nutrition_data = cls._parse_response(raw_response)

        return NutritionInfoResponse(
            answer=answer_text,
            nutrition_data=nutrition_data,
            query_type=query_type,
        )

    # ── Query Type Detection ─────────────────────────────────────────────────

    @classmethod
    def _detect_query_type(cls, text: str) -> str:
        """Classify the nutrition query into a type for metadata."""
        lowered = text.lower()

        exercise_keywords = [
            "walk", "run", "jog", "cycling", "swim", "pushup", "push-up", "squat",
            "exercise", "workout", "burn", "yoga", "plank", "jumping", "stairs",
            "calories burn", "calorie burn", "kitna jalega", "kitna jalein",
            "kitni calories jalein", "kitna burn",
        ]
        for kw in exercise_keywords:
            if kw in lowered:
                return "exercise_info"

        comparison_keywords = [
            "compare", "vs", "versus", "better", "acha", "behtar", "difference",
            "ya phir", "ya fir", "konsa", "which is",
        ]
        for kw in comparison_keywords:
            if kw in lowered:
                return "comparison"

        # Default to food_info for specific food queries, general for everything else
        food_patterns = [
            r"\b(calories|calorie|kcal|protein|carbs?|fat|fiber|nutrition)\b",
            r"\b(kitni|kitna|kitne)\b",
            r"\b(khaon|khaana|eat|eating)\b",
        ]
        for pat in food_patterns:
            if re.search(pat, lowered):
                return "food_info"

        return "general_nutrition"

    # ── Response Parser ──────────────────────────────────────────────────────

    @classmethod
    def _parse_response(cls, raw_response: str) -> tuple:
        """
        Split the LLM response into human answer and structured nutrition data.
        Returns (answer_text, List[NutritionBreakdown] or None).
        """
        answer_text = raw_response.strip()
        nutrition_data = None

        if "NUTRITION_DATA:" in raw_response:
            parts = raw_response.split("NUTRITION_DATA:", 1)
            answer_text = parts[0].strip()
            json_str = parts[1].strip()

            # Clean markdown fences
            json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
            json_str = re.sub(r"\s*```\s*$", "", json_str.strip())

            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, list) and parsed:
                    nutrition_data = []
                    for item in parsed:
                        if isinstance(item, dict):
                            nutrition_data.append(NutritionBreakdown(**item))
                elif isinstance(parsed, dict):
                    nutrition_data = [NutritionBreakdown(**parsed)]
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Failed to parse NUTRITION_DATA JSON: %s", e)
                nutrition_data = None

        # If answer is empty but we got data, provide a generic prefix
        if not answer_text and nutrition_data:
            answer_text = "Here is the nutritional information you requested:"

        return answer_text, nutrition_data if nutrition_data else None

    # ── Groq API Caller ──────────────────────────────────────────────────────

    @classmethod
    async def _call_groq_api(
        cls,
        messages: list,
        temperature: float = 0.2,
        max_tokens: int = 1500,
    ) -> str:
        """Invokes Groq LLM API via centralized Groq queue with 3x retries."""
        from app.services.groq_queue_service import GroqPriority, groq_queue

        return await groq_queue.submit_chat_completion(
            messages=messages,
            model=settings.GROQ_MODEL or "llama-3.3-70b-versatile",
            temperature=temperature,
            max_tokens=max_tokens,
            priority=GroqPriority.HIGH,
            caller="NutritionInfoService",
            timeout=45.0,
            enqueue_retries=3,
            max_retries=3,
        )
