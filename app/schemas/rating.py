"""
Schemas for patient doctor rating and feedback.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RatingCreateRequest(BaseModel):
    """Payload for submitting feedback/rating for a completed meeting."""
    rating: int = Field(..., ge=1, le=5, description="1 to 5 star rating")
    feedback_text: Optional[str] = Field(None, max_length=2000, description="Optional written feedback")


class RatingResponse(BaseModel):
    """Response returned when rating is fetched or created."""
    id: uuid.UUID
    meeting_id: uuid.UUID
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    rating: int
    feedback_text: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DoctorRatingSummary(BaseModel):
    """Aggregated rating info for a doctor."""
    average_rating: Optional[float] = None
    total_ratings: int = 0

    model_config = ConfigDict(from_attributes=True)
