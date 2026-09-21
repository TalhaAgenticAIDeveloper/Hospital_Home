"""
Plan Validator Service.

Implements multi-layer clinical and business rule validation:
- Input sanitization and prompt injection defenses
- Questionnaire answer validation, range checking, unit clarification, and retry thresholding
- Natural language extraction and time/unit normalization
- Plan safety checks (strict prescription medication blocker, allergy conflict scanner, schedule sanity)
"""

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.patient_plan import GeneratedPlanPayload


# ── Known Prescription Drugs, Medications & Clinical Terms (Strictly Blocked) ─────────────
BLOCKED_PRESCRIPTION_KEYWORDS = {
    "insulin",
    "metformin",
    "atorvastatin",
    "amlodipine",
    "lisinopril",
    "levothyroxine",
    "losartan",
    "omeprazole",
    "gabapentin",
    "hydrochlorothiazide",
    "sertraline",
    "simvastatin",
    "montelukast",
    "escitalopram",
    "rosuvastatin",
    "bupropion",
    "furosemide",
    "pantoprazole",
    "duloxetine",
    "prednisone",
    "tamsulosin",
    "fluoxetine",
    "clopidogrel",
    "carvedilol",
    "metoprolol",
    "warfarin",
    "apixaban",
    "xarelto",
    "eliquis",
    "ozempic",
    "wegovy",
    "mounjaro",
    "semaglutide",
    "tirzepatide",
    "amoxicillin",
    "azithromycin",
    "ciprofloxacin",
    "doxycycline",
    "augmentin",
    "tramadol",
    "codeine",
    "morphine",
    "xanax",
    "alprazolam",
    "valium",
    "diazepam",
    "adderall",
    "ritalin",
    "paracetamol",
    "panadol",
    "aspirin",
    "ibuprofen",
    "acetaminophen",
    "brufen",
    "disprin",
    "calpol",
    "tylenol",
    "advil",
    "motrin",
    "naproxen",
    "diclofenac",
    "flagyl",
    "metronidazole",
    "antibiotic",
    "antibiotics",
    "painkiller",
    "painkillers",
    "sleeping pill",
    "sleeping pills",
    "cough syrup",
    "antacid",
}

# Regex for clinical dosages e.g., "500mg", "10 units", "20 mcg", "2 tablets"
DOSAGE_PATTERN = re.compile(
    r"\b\d+(\.\d+)?\s*(mg|mcg|iu|units|tablets?|capsules?|pills?)\b",
    re.IGNORECASE,
)
MED_LIQUID_PATTERN = re.compile(
    r"\b\d+(\.\d+)?\s*ml\s+(?:of\s+)?(?:syrup|liquid\s+medicine|suspension|oral\s+solution|injection)\b",
    re.IGNORECASE,
)

# Regex for patient questions asking for medications, drugs, pills, or clinical treatments
MEDICATION_INQUIRY_PATTERN = re.compile(
    r"\b("
    r"medicines?|medications?|pills?|tablets?|capsules?|syrups?|dosages?|"
    r"prescribe|prescriptions?|drugs?|painkillers?|antibiotics?|sleeping\s*pills?|"
    r"panadol|paracetamol|aspirin|ibuprofen|brufen|disprin|calpol|tylenol|advil|motrin|insulin|metformin|"
    r"dawa|dawai|dawaii|dawayi|goli|goliyan|nuskha|ilaaj|ilaj"
    r")\b",
    re.IGNORECASE,
)

# Irrelevant answer patterns for health questionnaire
IRRELEVANT_PATTERNS = [
    r"\b(cricket|football|soccer|baseball|basketball|movie|film|song|actor|actress|politics|election|car|bike|weather)\b",
    r"\b(i like watching|i love watching|my favorite team|it is raining|who won)\b",
]

# Skip phrases
SKIP_PHRASES = {
    "skip",
    "i don't know",
    "idk",
    "not sure",
    "don't know",
    "no idea",
    "pass",
    "prefer not to answer",
    "i do not know",
}


@dataclass
class AnswerValidationResult:
    status: str  # valid, clarification_needed, invalid, skipped
    message: Optional[str]
    normalized_value: Optional[str]
    unit: Optional[str]
    can_proceed: bool
    extracted_fields: Dict[str, Any]


class PlanValidator:
    """Validator ensuring zero untrusted data causes crashes, corruption, or medical safety violations."""

    @staticmethod
    def sanitize_text(text: Optional[str], max_length: int = 2000) -> str:
        """Sanitize text input, remove null bytes and excessive whitespace."""
        if not text:
            return ""
        clean = text.replace("\x00", "").strip()
        # Truncate to maximum allowed length defensively
        return clean[:max_length]

    @classmethod
    def check_for_prompt_injection(cls, text: str) -> bool:
        """Check for blatant prompt injection attempts."""
        lowered = text.lower()
        injection_phrases = [
            "ignore all previous instructions",
            "ignore previous instructions",
            "disregard all previous",
            "you are now a",
            "system prompt",
            "database credentials",
            "drop table",
            "select * from",
            "reveal your instructions",
        ]
        return any(phrase in lowered for phrase in injection_phrases)

    @classmethod
    def parse_number_with_unit(cls, text: str) -> Tuple[Optional[float], Optional[str]]:
        """
        Extract numeric value and optional unit from text.
        Handles '55kg', '55 kg', '55 kilos', '120 lbs', '70.5'.
        """
        clean = text.strip().lower()
        match = re.search(r"(-?\d+(\.\d+)?)\s*([a-zA-Z%]+)?", clean)
        if not match:
            return None, None
        try:
            val = float(match.group(1))
            raw_unit = match.group(3) or ""
            # Normalize unit
            unit = None
            if raw_unit in ("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"):
                unit = "kg"
            elif raw_unit in ("lb", "lbs", "pound", "pounds"):
                unit = "lb"
            elif raw_unit in ("cm", "cms", "centimeter", "centimeters"):
                unit = "cm"
            elif raw_unit in ("m", "meter", "meters"):
                unit = "m"
            elif raw_unit in ("in", "inch", "inches"):
                unit = "inch"
            elif raw_unit in ("ft", "feet", "foot"):
                unit = "ft"
            elif raw_unit in ("hours", "hour", "hr", "hrs"):
                unit = "hours"
            elif raw_unit:
                unit = raw_unit
            return val, unit
        except (ValueError, OverflowError):
            return None, None

    @classmethod
    def normalize_time(cls, text: str) -> Optional[str]:
        """
        Normalize natural language time into 24-hour HH:MM format.
        Examples:
        '08:00' -> '08:00'
        '8:00 AM' -> '08:00'
        'around 8 in the morning' -> '08:00'
        '10 PM' -> '22:00'
        'after fajr' -> '05:30'
        """
        clean = text.lower().strip()

        # Check special cultural / natural language time keywords
        if "fajr" in clean:
            return "05:30"
        if "maghrib" in clean:
            return "18:45"
        if "noon" in clean or "midday" in clean:
            return "12:00"
        if "midnight" in clean:
            return "00:00"

        # Regex for HH:MM (AM/PM)
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", clean)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2)) if match.group(2) else 0
            meridiem = match.group(3)

            # Detect morning / evening from words if AM/PM absent
            if not meridiem:
                if "morning" in clean or "dawn" in clean:
                    meridiem = "am"
                elif "night" in clean or "evening" in clean or "afternoon" in clean:
                    meridiem = "pm"

            if meridiem == "pm" and hours < 12:
                hours += 12
            elif meridiem == "am" and hours == 12:
                hours = 0

            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                return f"{hours:02d}:{minutes:02d}"

        return None

    @classmethod
    def validate_question_answer(
        cls,
        question_key: str,
        question_type: str,
        raw_input: str,
        is_skipped: bool = False,
        retry_count: int = 0,
        expected_unit: Optional[str] = None,
    ) -> AnswerValidationResult:
        """
        Validates user response against question requirements and edge cases.
        Handles:
        - Skipped / uncertain answers ("I don't know", "skip")
        - Irrelevant answers ("I like watching cricket")
        - Missing units ("55" for weight)
        - Invalid numbers (-50, 999, NaN)
        - Infinite loop prevention (MAX_QUESTION_RETRIES)
        - Natural language parsing
        """
        clean_input = cls.sanitize_text(raw_input).strip()
        lowered = clean_input.lower()
        extracted: Dict[str, Any] = {}

        # 1. Handle Explicit or Natural Language Skip
        if is_skipped or lowered in SKIP_PHRASES or any(lowered.startswith(p) for p in ("i don't know", "skip", "not sure")):
            # If question is critical (e.g. weight, sleep time), check retry count
            if question_key in ("current_weight", "wake_up_time") and retry_count < 2 and not is_skipped:
                return AnswerValidationResult(
                    status="clarification_needed",
                    message=(
                        f"I need your {question_key.replace('_', ' ')} to personalize your plan accurately. "
                        "If you don't know the exact number, you can skip this question and I will create a general plan."
                    ),
                    normalized_value=None,
                    unit=None,
                    can_proceed=False,
                    extracted_fields={},
                )
            # Safe to skip
            return AnswerValidationResult(
                status="skipped",
                message="Question skipped. Continuing with standard recommendations.",
                normalized_value="Not provided (skipped)",
                unit=None,
                can_proceed=True,
                extracted_fields={},
            )

        # 2. Irrelevant Answer Detection
        for pattern in IRRELEVANT_PATTERNS:
            if re.search(pattern, lowered):
                label = question_key.replace("_", " ")
                unit_hint = f" in {expected_unit}" if expected_unit else ""
                return AnswerValidationResult(
                    status="clarification_needed",
                    message=f"Thanks! I still need your {label} to personalize this plan. Please enter your {label}{unit_hint}.",
                    normalized_value=None,
                    unit=None,
                    can_proceed=False,
                    extracted_fields={},
                )

        # 3. Numeric Question Handling (Weight, Height, Age, Target Weight)
        if question_type == "number" or question_key in ("current_weight", "target_weight", "height", "age"):
            val, unit = cls.parse_number_with_unit(clean_input)

            if val is None or math.isnan(val) or math.isinf(val):
                if retry_count >= 2:
                    return AnswerValidationResult(
                        status="skipped",
                        message="We couldn't recognize a valid number. Skipping this question to continue with a general plan.",
                        normalized_value="Skipped after multiple attempts",
                        unit=None,
                        can_proceed=True,
                        extracted_fields={},
                    )
                example = "65 kg" if "weight" in question_key else ("170 cm" if "height" in question_key else "28")
                return AnswerValidationResult(
                    status="invalid",
                    message=f"That doesn't look like a valid number. Please enter a realistic value (e.g. {example}).",
                    normalized_value=None,
                    unit=None,
                    can_proceed=False,
                    extracted_fields={},
                )

            # Weight validation
            if "weight" in question_key:
                # Check missing unit ambiguity
                if not unit:
                    if 20 <= val <= 350:
                        return AnswerValidationResult(
                            status="clarification_needed",
                            message=f"Is that {int(val) if val.is_integer() else val} kg or {int(val) if val.is_integer() else val} lb?",
                            normalized_value=None,
                            unit=None,
                            can_proceed=False,
                            extracted_fields={},
                        )
                    else:
                        return AnswerValidationResult(
                            status="invalid",
                            message="Please enter a realistic body weight between 20 kg and 350 kg.",
                            normalized_value=None,
                            unit=None,
                            can_proceed=False,
                            extracted_fields={},
                        )

                # Unit normalization
                if unit == "lb":
                    # Convert to kg for internal standard
                    val_kg = round(val * 0.45359237, 1)
                else:
                    val_kg = round(val, 1)

                if val_kg < 20 or val_kg > 350:
                    return AnswerValidationResult(
                        status="invalid",
                        message=f"That weight ({val} {unit}) is outside realistic boundaries. Please enter a value between 20 kg and 350 kg.",
                        normalized_value=None,
                        unit=None,
                        can_proceed=False,
                        extracted_fields={},
                    )

                return AnswerValidationResult(
                    status="valid",
                    message=None,
                    normalized_value=f"{val_kg} kg",
                    unit="kg",
                    can_proceed=True,
                    extracted_fields={question_key: f"{val_kg} kg"},
                )

            # Height validation
            if "height" in question_key:
                # Support feet/inches e.g., 5'6 or 5 feet 6 inches
                ft_match = re.search(r"(\d+)\s*(?:'|ft|feet)\s*(\d+)?(?:\s*\"|\s*in|\s*inches)?", clean_input)
                if ft_match:
                    feet = int(ft_match.group(1))
                    inches = int(ft_match.group(2)) if ft_match.group(2) else 0
                    total_cm = round((feet * 12 + inches) * 2.54, 1)
                    if 50 <= total_cm <= 260:
                        return AnswerValidationResult(
                            status="valid",
                            message=None,
                            normalized_value=f"{total_cm} cm ({feet}'{inches}\")",
                            unit="cm",
                            can_proceed=True,
                            extracted_fields={question_key: f"{total_cm} cm"},
                        )

                if unit == "m":
                    val_cm = round(val * 100, 1)
                else:
                    val_cm = round(val, 1)

                if val_cm < 50 or val_cm > 260:
                    return AnswerValidationResult(
                        status="invalid",
                        message="Please enter a realistic height between 50 cm and 260 cm (or e.g. 5'8\").",
                        normalized_value=None,
                        unit=None,
                        can_proceed=False,
                        extracted_fields={},
                    )

                return AnswerValidationResult(
                    status="valid",
                    message=None,
                    normalized_value=f"{val_cm} cm",
                    unit="cm",
                    can_proceed=True,
                    extracted_fields={question_key: f"{val_cm} cm"},
                )

            # Age validation
            if question_key == "age":
                age_val = int(val)
                if age_val < 1 or age_val > 120:
                    return AnswerValidationResult(
                        status="invalid",
                        message="Please enter a realistic age between 1 and 120 years.",
                        normalized_value=None,
                        unit=None,
                        can_proceed=False,
                        extracted_fields={},
                    )
                return AnswerValidationResult(
                    status="valid",
                    message=None,
                    normalized_value=str(age_val),
                    unit="years",
                    can_proceed=True,
                    extracted_fields={"age": age_val},
                )

        # 4. Time Question Handling
        if question_type == "time" or "time" in question_key:
            norm_time = cls.normalize_time(clean_input)
            if not norm_time:
                if retry_count >= 2:
                    return AnswerValidationResult(
                        status="skipped",
                        message="Could not parse time. Continuing with standard routine (07:00).",
                        normalized_value="07:00",
                        unit=None,
                        can_proceed=True,
                        extracted_fields={},
                    )
                return AnswerValidationResult(
                    status="clarification_needed",
                    message="Please enter a time like '07:30 AM', '8:00', or 'after Fajr'.",
                    normalized_value=None,
                    unit=None,
                    can_proceed=False,
                    extracted_fields={},
                )

            return AnswerValidationResult(
                status="valid",
                message=None,
                normalized_value=norm_time,
                unit=None,
                can_proceed=True,
                extracted_fields={question_key: norm_time},
            )

        # 5. Multi-field extraction check for general text input
        # Example: "I'm 55 kg, 5'6, 23 years old and walk 30 minutes"
        multi_extracted: Dict[str, Any] = {}
        # check for weight
        w_match = re.search(r"(\d+(\.\d+)?)\s*(kg|kilos|lbs|pounds)", clean_input, re.I)
        if w_match:
            multi_extracted["current_weight"] = f"{w_match.group(1)} {w_match.group(3).lower()}"
        # check for height
        h_match = re.search(r"(\d+)'(\d+)\"?", clean_input)
        if h_match:
            multi_extracted["height"] = f"{h_match.group(1)}'{h_match.group(2)}\""
        # check for age
        a_match = re.search(r"\b(\d{1,2})\s*(?:years?\s*old|yo)\b", clean_input, re.I)
        if a_match:
            multi_extracted["age"] = int(a_match.group(1))

        # Default text question
        if len(clean_input) < 2:
            return AnswerValidationResult(
                status="invalid",
                message="Answer is too short. Please provide a little more detail.",
                normalized_value=None,
                unit=None,
                can_proceed=False,
                extracted_fields={},
            )

        return AnswerValidationResult(
            status="valid",
            message=None,
            normalized_value=clean_input,
            unit=None,
            can_proceed=True,
            extracted_fields=multi_extracted,
        )

    @classmethod
    def validate_generated_plan(
        cls,
        payload: GeneratedPlanPayload,
        declared_allergies: Optional[List[str]] = None,
        declared_restrictions: Optional[List[str]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Performs server-side validation on AI-generated plan JSON.
        Checks:
        1. Strict prescription medication blocker (insulin, metformin, dosages).
        2. Known allergy and restriction conflicts.
        3. Schedule format and sanity (no duplicate times, realistic window).
        4. Target sanity.
        Returns:
            (is_valid, list_of_error_strings)
        """
        errors: List[str] = []

        # 1. Block prescription medications
        text_corpus = (
            f"{payload.title} {payload.summary} "
            f"{' '.join(payload.diet_guidelines)} "
            f"{' '.join(payload.lifestyle_guidelines)} "
            f"{' '.join(item.title + ' ' + item.description for item in payload.schedule_items)}"
        ).lower()

        for drug in BLOCKED_PRESCRIPTION_KEYWORDS:
            # Word boundary search
            if re.search(rf"\b{re.escape(drug)}\b", text_corpus):
                errors.append(
                    f"Generated plan mentions prescription medication '{drug}'. "
                    "Wellness plans cannot prescribe or manage prescription drugs."
                )

        if DOSAGE_PATTERN.search(text_corpus) or MED_LIQUID_PATTERN.search(text_corpus):
            errors.append(
                "Generated plan appears to include clinical medication dosages. "
                "Dosages and medical treatments are forbidden in wellness plans."
            )

        # 2. Check declared allergies
        if declared_allergies:
            for allergy in declared_allergies:
                clean_allergy = allergy.strip().lower()
                if clean_allergy and len(clean_allergy) > 2:
                    stemmed = clean_allergy.rstrip("s")
                    if re.search(rf"\b{re.escape(stemmed)}", text_corpus):
                        errors.append(
                            f"Plan contains '{clean_allergy}', which conflicts with patient's declared allergy."
                        )

        # 3. Schedule items sanity
        if len(payload.schedule_items) < 2:
            errors.append("Plan must contain at least 2 scheduled daily activities.")

        seen_times = set()
        for item in payload.schedule_items:
            # Ensure valid HH:MM
            if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", item.time_of_day):
                errors.append(f"Invalid time format '{item.time_of_day}'. Expected HH:MM.")
            # Check duplicate schedule times
            if item.time_of_day in seen_times:
                errors.append(f"Duplicate schedule activity detected at {item.time_of_day}.")
            seen_times.add(item.time_of_day)

        # 4. Dangerous / Unrealistic claims check
        unrealistic_phrases = [
            "gain 20 kg",
            "lose 20 kg in a week",
            "cure cancer",
            "cure diabetes",
            "stop taking your medication",
            "guaranteed results",
        ]
        for phrase in unrealistic_phrases:
            if phrase in text_corpus:
                errors.append(f"Plan contains unrealistic or unsafe claim: '{phrase}'.")

        # 5. Nutritional data sanity checks
        food_categories = {"breakfast", "lunch", "dinner", "snack", "evening_activity"}
        exercise_categories = {"workout", "exercise"}

        for item in payload.schedule_items:
            cat = item.category.lower() if item.category else ""

            # Food items with absurdly high/low calories
            if cat in food_categories and item.calories is not None:
                if item.calories < 0:
                    errors.append(
                        f"Schedule item '{item.title}' has negative calories ({item.calories}). "
                        "Food items cannot have negative calorie values."
                    )
                if item.calories > 3000:
                    errors.append(
                        f"Schedule item '{item.title}' has unrealistically high calories ({item.calories} kcal) "
                        "for a single meal. Maximum expected is ~3000 kcal per meal."
                    )

            # Exercise items should not have food macros set
            if cat in exercise_categories:
                if item.calories is not None and item.calories > 0:
                    errors.append(
                        f"Exercise item '{item.title}' should not have food calories. "
                        "Use calories_burned instead."
                    )
                if item.calories_burned is not None and item.calories_burned > 2000:
                    errors.append(
                        f"Exercise item '{item.title}' has unrealistically high calories_burned ({item.calories_burned}). "
                        "Maximum expected per session is ~2000 kcal."
                    )

        # Daily summary sanity
        summary = payload.daily_nutrition_summary
        if summary and isinstance(summary, dict):
            total_cal = summary.get("total_calories")
            if total_cal is not None and (total_cal < 200 or total_cal > 10000):
                errors.append(
                    f"Daily nutrition summary has unrealistic total_calories ({total_cal}). "
                    "Expected range: 200-10000 kcal."
                )

        return len(errors) == 0, errors

    @classmethod
    def is_medication_inquiry(cls, text: str) -> bool:
        """Check if patient input is asking about or mentioning medications, drugs, tablets, or prescriptions."""
        if not text:
            return False
        return bool(MEDICATION_INQUIRY_PATTERN.search(text))

    @classmethod
    def contains_blocked_medication(cls, text: str) -> Tuple[bool, List[str]]:
        """
        Check if text mentions blocked prescription keywords or clinical dosages.
        Returns (is_unsafe, list_of_flagged_terms).
        """
        if not text:
            return False, []
        lowered = text.lower()
        matches = []
        for drug in BLOCKED_PRESCRIPTION_KEYWORDS:
            if re.search(rf"\b{re.escape(drug)}\b", lowered):
                matches.append(drug)
        if DOSAGE_PATTERN.search(lowered) or MED_LIQUID_PATTERN.search(lowered):
            matches.append("clinical_dosage")
        return len(matches) > 0, matches

    @staticmethod
    def get_safe_natural_alternative_fallback() -> str:
        """
        Safe, compassionate response that politely declines medication prescription
        and offers evidence-based natural, dietary, and lifestyle alternatives.
        """
        return (
            "I cannot prescribe or recommend any medications, pharmaceuticals, or clinical treatments—for any prescription drugs, "
            "please consult your licensed physician or attending doctor.\n\n"
            "However, if you are looking for safe and **natural alternatives**, here are evidence-based lifestyle approaches that can help:\n\n"
            "🌿 **Hydration & Herbal Infusions**: Drinking warm water or herbal teas like ginger (for digestion and anti-inflammatory support), "
            "chamomile (for relaxation and restful sleep), or peppermint (for soothing physical tension).\n\n"
            "🥗 **Nutrient-Rich Whole Foods**: Focus on wholesome, anti-inflammatory foods like berries, leafy greens, nuts, and healthy fats while minimizing processed sugars.\n\n"
            "🧘 **Rest, Movement & Breathing**: Gentle daily stretching, deep diaphragmatic breathing, and maintaining consistent sleep routines can naturally ease stress and restore vitality.\n\n"
            "Would you like me to adjust any item in your daily routine to incorporate more of these natural wellness habits?"
        )
