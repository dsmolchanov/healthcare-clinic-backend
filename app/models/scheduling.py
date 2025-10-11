"""
Pydantic models for scheduling API.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from enum import Enum


class TimeOfDay(str, Enum):
    """Time of day preference."""
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class DateRange(BaseModel):
    """Date range for slot search."""
    start_date: datetime = Field(..., description="Start of search range")
    end_date: datetime = Field(..., description="End of search range")


class HardConstraints(BaseModel):
    """Hard constraints for slot search."""
    doctor_id: Optional[UUID] = Field(None, description="Required doctor ID")
    room_id: Optional[UUID] = Field(None, description="Required room ID")
    time_of_day: Optional[TimeOfDay] = Field(None, description="Preferred time of day")


class SlotExplanation(BaseModel):
    """Explanation for slot score."""
    factor: str = Field(..., description="Scoring factor name")
    score: float = Field(..., description="Score for this factor")
    reason: str = Field(..., description="Human-readable explanation")


class Slot(BaseModel):
    """Appointment slot."""
    slot_id: str = Field(..., description="Unique slot identifier")
    doctor_id: UUID = Field(..., description="Doctor ID")
    doctor_name: str = Field(..., description="Doctor name")
    room_id: Optional[UUID] = Field(None, description="Room ID")
    service_id: UUID = Field(..., description="Service ID")
    start_time: datetime = Field(..., description="Slot start time")
    end_time: datetime = Field(..., description="Slot end time")
    score: float = Field(..., description="Slot score (0-100)")
    explanation: List[SlotExplanation] = Field(
        default_factory=list,
        description="Score explanation"
    )


class SuggestedSlots(BaseModel):
    """Response for slot suggestions."""
    slots: List[Slot] = Field(..., description="Top suggested slots")
    total_evaluated: int = Field(..., description="Total slots evaluated")
    search_criteria: Dict[str, Any] = Field(..., description="Search criteria used")


class HoldResponse(BaseModel):
    """Response for hold creation."""
    hold_id: UUID = Field(..., description="Hold identifier")
    slot: Slot = Field(..., description="Held slot")
    expires_at: datetime = Field(..., description="Hold expiration time")
    client_hold_id: str = Field(..., description="Client-provided idempotency key")


class AppointmentResponse(BaseModel):
    """Response for appointment creation."""
    appointment_id: UUID = Field(..., description="Appointment identifier")
    slot: Slot = Field(..., description="Appointment slot")
    patient_id: UUID = Field(..., description="Patient ID")
    status: str = Field(..., description="Appointment status")
    created_at: datetime = Field(..., description="Creation timestamp")
    calendar_synced: bool = Field(False, description="Whether calendar sync was successful")
    calendar_event_ids: Optional[Dict[str, str]] = Field(None, description="External calendar event IDs")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )


# Custom exceptions
class NoSlotsAvailableError(Exception):
    """Raised when no slots are available for the requested criteria."""
    def __init__(self, message: str = "No available slots found", escalation_id: Optional[UUID] = None):
        self.escalation_id = escalation_id
        super().__init__(message)


class HoldExpiredError(Exception):
    """Raised when trying to confirm an expired hold."""
    def __init__(self, message: str = "Hold has expired"):
        super().__init__(message)


class HoldNotFoundError(Exception):
    """Raised when hold ID doesn't exist."""
    def __init__(self, message: str = "Hold not found"):
        super().__init__(message)


class InvalidConstraintsError(Exception):
    """Raised when constraints are contradictory or invalid."""
    def __init__(self, message: str = "Invalid constraints"):
        super().__init__(message)
