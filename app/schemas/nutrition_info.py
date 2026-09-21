"""
Pydantic schemas for the Nutrition Information Agent.

Covers:
- Nutrition query requests (food, exercise, general nutrition questions)
- Structured nutrition info responses with optional detailed breakdown
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class NutritionQueryRequest(BaseModel):
    """Request schema for asking nutrition/food/exercise info questions."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The nutrition question (e.g., 'How many calories in a banana?' or 'apple mein kitni calories hain?')",
    )
    plan_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Optional plan ID to provide plan context when querying from within a plan chat",
    )


class NutritionBreakdown(BaseModel):
    """Structured nutritional breakdown for a single food or exercise item."""

    item_name: Optional[str] = None
    serving_size: Optional[str] = None
    calories: Optional[int] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sugar_g: Optional[float] = None
    calories_burned: Optional[int] = None  # for exercise items
    duration_minutes: Optional[int] = None  # for exercise items


class NutritionInfoResponse(BaseModel):
    """Response from the nutrition info agent."""

    answer: str = Field(..., description="Human-readable answer in the patient's language")
    nutrition_data: Optional[List[NutritionBreakdown]] = Field(
        default=None,
        description="Structured nutritional breakdown if applicable",
    )
    query_type: str = Field(
        default="general_nutrition",
        description="Detected query type: food_info, exercise_info, comparison, general_nutrition",
    )
