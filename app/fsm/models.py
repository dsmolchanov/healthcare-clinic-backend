"""
FSM Pydantic Models

This module defines type-safe data models for the Finite State Machine (FSM) system.
All models use Pydantic V2 for validation and serialization.

Models:
- SlotEvidence: Tracks slot values with provenance and staleness detection
- FSMState: Complete FSM state with version tracking for optimistic locking
- IdempotencyRecord: Cached webhook responses for deduplication
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, ValidationInfo

from .constants import ConversationState, SlotSource, SLOT_STALENESS_THRESHOLD


class SlotEvidence(BaseModel):
    """
    Tracks slot value with provenance metadata.

    Attributes:
        value: The extracted slot value (can be any JSON-serializable type)
        source: How the value was obtained (LLM, user confirmation, DB)
        confidence: LLM confidence score (0.0 to 1.0)
        extracted_at: UTC timestamp when value was extracted
        confirmed: Whether user explicitly confirmed this value
    """
    value: Any
    source: SlotSource
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_at: datetime
    confirmed: bool = False

    @field_validator('extracted_at')
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure extracted_at is timezone-aware (UTC)."""
        if v.tzinfo is None:
            # Convert naive datetime to UTC
            return v.replace(tzinfo=timezone.utc)
        return v

    def is_stale(self, max_age_seconds: int = SLOT_STALENESS_THRESHOLD) -> bool:
        """
        Check if slot evidence is stale based on age.

        Args:
            max_age_seconds: Maximum age in seconds before slot is considered stale.
                           Defaults to SLOT_STALENESS_THRESHOLD (300 seconds / 5 minutes).

        Returns:
            True if slot age exceeds max_age_seconds, False otherwise.

        Example:
            >>> slot = SlotEvidence(
            ...     value="2025-10-20",
            ...     source=SlotSource.LLM_EXTRACT,
            ...     confidence=0.95,
            ...     extracted_at=datetime.now(timezone.utc)
            ... )
            >>> slot.is_stale(max_age_seconds=300)  # Check if older than 5 minutes
            False
        """
        # Ensure current time is timezone-aware
        now = datetime.now(timezone.utc)
        age = (now - self.extracted_at).total_seconds()
        return age > max_age_seconds

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "value": "2025-10-20",
                    "source": "llm_extract",
                    "confidence": 0.95,
                    "extracted_at": "2025-10-18T10:30:00Z",
                    "confirmed": False
                }
            ]
        }
    }


class FSMState(BaseModel):
    """
    Complete FSM state stored in Redis.

    This model represents the full conversation state with optimistic locking
    via version tracking. Used with CAS (Compare-And-Set) for race-free updates.

    Attributes:
        conversation_id: Unique conversation identifier (e.g., WhatsApp session ID)
        clinic_id: Clinic/organization identifier
        current_state: Current FSM state in the conversation flow
        version: Optimistic lock version (incremented on each update)
        slots: Dictionary mapping slot names to their evidence
        failure_count: Number of consecutive failures (for retry logic)
        created_at: UTC timestamp when state was first created
        updated_at: UTC timestamp of last update
    """
    conversation_id: str = Field(..., min_length=1)
    clinic_id: str = Field(..., min_length=1)
    current_state: ConversationState
    version: int = Field(default=0, ge=0)
    slots: Dict[str, SlotEvidence] = Field(default_factory=dict)
    failure_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator('created_at', 'updated_at')
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure timestamps are timezone-aware (UTC)."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @field_validator('updated_at')
    @classmethod
    def validate_updated_after_created(cls, v: datetime, info: ValidationInfo) -> datetime:
        """Ensure updated_at is not before created_at."""
        if 'created_at' in info.data:
            created_at = info.data['created_at']
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if v < created_at:
                raise ValueError("updated_at cannot be before created_at")
        return v

    def increment_version(self) -> None:
        """Increment version counter for optimistic locking."""
        self.version += 1
        self.updated_at = datetime.now(timezone.utc)

    def get_slot_value(self, slot_name: str) -> Optional[Any]:
        """
        Get slot value by name.

        Args:
            slot_name: Name of the slot to retrieve

        Returns:
            Slot value if exists, None otherwise
        """
        evidence = self.slots.get(slot_name)
        return evidence.value if evidence else None

    def set_slot(
        self,
        slot_name: str,
        value: Any,
        source: SlotSource,
        confidence: float,
        confirmed: bool = False
    ) -> None:
        """
        Set or update a slot value.

        Args:
            slot_name: Name of the slot
            value: Value to store
            source: How the value was obtained
            confidence: LLM confidence score (0.0 to 1.0)
            confirmed: Whether user confirmed this value
        """
        self.slots[slot_name] = SlotEvidence(
            value=value,
            source=source,
            confidence=confidence,
            extracted_at=datetime.now(timezone.utc),
            confirmed=confirmed
        )
        self.updated_at = datetime.now(timezone.utc)

    model_config = {
        "use_enum_values": True,
        "json_schema_extra": {
            "examples": [
                {
                    "conversation_id": "whatsapp:+15551234567:session123",
                    "clinic_id": "clinic_001",
                    "current_state": "collecting_slots",
                    "version": 3,
                    "slots": {
                        "appointment_date": {
                            "value": "2025-10-20",
                            "source": "llm_extract",
                            "confidence": 0.95,
                            "extracted_at": "2025-10-18T10:30:00Z",
                            "confirmed": False
                        }
                    },
                    "failure_count": 0,
                    "created_at": "2025-10-18T10:00:00Z",
                    "updated_at": "2025-10-18T10:30:00Z"
                }
            ]
        }
    }


class IdempotencyRecord(BaseModel):
    """
    Cached webhook response for deduplication.

    Used to prevent duplicate processing of webhooks by caching responses
    based on message_sid (unique message identifier).

    Attributes:
        message_sid: Unique message identifier (e.g., Twilio message SID)
        response: Cached response text to return for duplicate requests
        processed_at: UTC timestamp when message was first processed
    """
    message_sid: str = Field(..., min_length=1)
    response: str
    processed_at: datetime

    @field_validator('processed_at')
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure processed_at is timezone-aware (UTC)."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message_sid": "SM1234567890abcdef1234567890abcdef",
                    "response": "Your appointment has been booked for October 20th at 2:00 PM.",
                    "processed_at": "2025-10-18T10:30:00Z"
                }
            ]
        }
    }
