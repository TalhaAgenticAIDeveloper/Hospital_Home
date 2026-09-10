"""
Schemas for prescriptions and medicine schedules.
"""

import uuid
from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrescriptionMedicineItem(BaseModel):
    """Details for a single prescribed medicine item."""
    medicine_name: str = Field(..., min_length=1, max_length=500, description="Medicine name and dosage instructions")
    
    # Morning slot
    morning: bool = False
    morning_time: Optional[time] = None
    morning_before_meal: bool = True

    # Afternoon slot
    afternoon: bool = False
    afternoon_time: Optional[time] = None
    afternoon_before_meal: bool = True

    # Evening slot
    evening: bool = False
    evening_time: Optional[time] = None
    evening_before_meal: bool = True

    # Night slot
    night: bool = False
    night_time: Optional[time] = None
    night_before_meal: bool = True

    # Dates
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_medicine(self) -> "PrescriptionMedicineItem":
        if not (self.morning or self.afternoon or self.evening or self.night):
            raise ValueError(f"At least one time slot (morning, afternoon, evening, or night) must be selected for '{self.medicine_name}'")
        if self.end_date < self.start_date:
            raise ValueError(f"End date ({self.end_date}) cannot be earlier than start date ({self.start_date}) for '{self.medicine_name}'")
        return self


class PrescriptionMedicineResponse(BaseModel):
    """Response model for a prescribed medicine."""
    id: uuid.UUID
    prescription_id: uuid.UUID
    medicine_name: str
    morning: bool
    morning_time: Optional[time] = None
    morning_before_meal: bool
    afternoon: bool
    afternoon_time: Optional[time] = None
    afternoon_before_meal: bool
    evening: bool
    evening_time: Optional[time] = None
    evening_before_meal: bool
    night: bool
    night_time: Optional[time] = None
    night_before_meal: bool
    start_date: date
    end_date: date
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PrescriptionCreateRequest(BaseModel):
    """Doctor request to issue a prescription for a completed meeting."""
    meeting_id: uuid.UUID
    notes: Optional[str] = Field(None, max_length=5000, description="General instructions or advice")
    medicines: List[PrescriptionMedicineItem] = Field(..., min_length=1, description="List of prescribed medicines")


class PrescriptionResponse(BaseModel):
    """Full prescription details with medicine schedules."""
    id: uuid.UUID
    meeting_id: uuid.UUID
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    notes: Optional[str] = None
    doctor_name: Optional[str] = None
    patient_name: Optional[str] = None
    created_at: datetime
    medicines: List[PrescriptionMedicineResponse] = []

    model_config = ConfigDict(from_attributes=True)
