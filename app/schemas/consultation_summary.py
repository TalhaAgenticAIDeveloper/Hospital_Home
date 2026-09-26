"""
Pydantic schemas for Consultation Summary — API request/response models.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConsultationSummaryResponse(BaseModel):
    """Single consultation summary returned to the client."""
    id: uuid.UUID
    meeting_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    doctor_name: Optional[str] = None
    doctor_specialization: Optional[str] = None
    patient_name: Optional[str] = None
    summary_text: Optional[str] = None
    status: str
    meeting_date: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ConsultationHistoryResponse(BaseModel):
    """Paginated list of consultation summaries."""
    summaries: List[ConsultationSummaryResponse] = []
    total: int = 0
    limit: int = 50
    offset: int = 0
