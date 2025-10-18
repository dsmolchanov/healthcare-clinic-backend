"""
FSM Structured Logging Module

Provides HIPAA-compliant structured logging for FSM operations:
- JSON-formatted logs for easy parsing by log aggregators
- Privacy-preserving conversation ID hashing
- Event-based logging with consistent structure
- Type-safe logging functions for common FSM events

All logs include:
- Timestamp (ISO 8601 UTC)
- Event type
- Hashed conversation ID (for privacy)
- Clinic ID
- State information
- Relevant context

Privacy:
- Conversation IDs are hashed (SHA-256, truncated to 16 chars)
- Slot values are sanitized (no PII)
- Only essential metadata is logged
"""

import logging
import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger("fsm")

# ==============================================================================
# PRIVACY UTILITIES
# ==============================================================================

def hash_conversation_id(conversation_id: str) -> str:
    """
    Hash conversation ID for privacy.

    Uses SHA-256 and truncates to 16 characters for log correlation
    without exposing actual conversation IDs.

    Args:
        conversation_id: Original conversation ID

    Returns:
        str: Hashed conversation ID (16 chars)

    Example:
        >>> hash_conversation_id("whatsapp:+15551234567:session123")
        'a1b2c3d4e5f6g7h8'
    """
    return hashlib.sha256(conversation_id.encode()).hexdigest()[:16]


def sanitize_slot_value(value: Any) -> str:
    """
    Sanitize slot value for logging.

    Converts slot values to strings while avoiding PII exposure.
    Masks phone numbers and sensitive data.

    Args:
        value: Slot value (any type)

    Returns:
        str: Sanitized value safe for logging

    Example:
        >>> sanitize_slot_value("+15551234567")
        '+1555***4567'
        >>> sanitize_slot_value("2025-10-20")
        '2025-10-20'
    """
    value_str = str(value)

    # Mask phone numbers (keep country code and last 4 digits)
    if value_str.startswith('+') and len(value_str) > 8:
        return f"{value_str[:5]}***{value_str[-4:]}"

    # Mask emails (keep domain)
    if '@' in value_str:
        parts = value_str.split('@')
        if len(parts) == 2:
            return f"{parts[0][:2]}***@{parts[1]}"

    # Return as-is for dates, times, doctor names, etc.
    return value_str


# ==============================================================================
# CORE LOGGING FUNCTIONS
# ==============================================================================

def log_fsm_event(
    event_type: str,
    conversation_id: str,
    clinic_id: str,
    state: str,
    intent: Optional[str] = None,
    slots: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None
):
    """
    Log FSM event with structured JSON.

    This is the base logging function used by all other FSM logging functions.

    Args:
        event_type: Type of event (e.g., "state_transition", "slot_validation")
        conversation_id: Conversation identifier (will be hashed)
        clinic_id: Clinic identifier
        state: Current conversation state
        intent: Detected intent (optional)
        slots: Slot data (optional, will be sanitized)
        error: Error message if event failed (optional)
        extra: Additional metadata (optional)

    Example:
        >>> log_fsm_event(
        ...     event_type="state_transition",
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     state="collecting_slots",
        ...     intent="booking_intent"
        ... )
    """
    # Sanitize slots (extract values, avoid PII)
    sanitized_slots = None
    if slots:
        sanitized_slots = {
            k: sanitize_slot_value(v.value if hasattr(v, 'value') else v)
            for k, v in slots.items()
        }

    log_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "conversation_id_hash": hash_conversation_id(conversation_id),
        "clinic_id": clinic_id,
        "state": state,
        "intent": intent,
        "slots": sanitized_slots,
        "error": error,
        **(extra or {})
    }

    # Log at appropriate level
    if error:
        logger.error(json.dumps(log_data))
    else:
        logger.info(json.dumps(log_data))


# ==============================================================================
# EVENT-SPECIFIC LOGGING FUNCTIONS
# ==============================================================================

def log_state_transition(
    conversation_id: str,
    clinic_id: str,
    from_state: str,
    to_state: str,
    intent: str,
    duration_ms: float
):
    """
    Log state transition event.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        from_state: Source state
        to_state: Target state
        intent: Intent that triggered transition
        duration_ms: Transition duration in milliseconds

    Example:
        >>> log_state_transition(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     from_state="greeting",
        ...     to_state="collecting_slots",
        ...     intent="booking_intent",
        ...     duration_ms=45.2
        ... )
    """
    log_fsm_event(
        event_type="state_transition",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=to_state,
        intent=intent,
        extra={
            "from_state": from_state,
            "to_state": to_state,
            "duration_ms": round(duration_ms, 2)
        }
    )


def log_slot_validation(
    conversation_id: str,
    clinic_id: str,
    state: str,
    slot_name: str,
    slot_value: Any,
    is_valid: bool,
    error_message: Optional[str] = None,
    duration_ms: Optional[float] = None
):
    """
    Log slot validation event.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        state: Current state
        slot_name: Name of slot being validated
        slot_value: Value being validated (will be sanitized)
        is_valid: Whether validation passed
        error_message: Validation error message (optional)
        duration_ms: Validation duration in milliseconds (optional)

    Example:
        >>> log_slot_validation(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     state="collecting_slots",
        ...     slot_name="doctor_name",
        ...     slot_value="Иванов",
        ...     is_valid=True,
        ...     duration_ms=52.3
        ... )
    """
    extra = {
        "slot_name": slot_name,
        "slot_value": sanitize_slot_value(slot_value),
        "is_valid": is_valid,
        "error_message": error_message
    }

    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms, 2)

    log_fsm_event(
        event_type="slot_validation",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=state,
        error=error_message if not is_valid else None,
        extra=extra
    )


def log_auto_escalation(
    conversation_id: str,
    clinic_id: str,
    state: str,
    failure_count: int,
    reason: str
):
    """
    Log auto-escalation event.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        state: State at time of escalation
        failure_count: Number of consecutive failures
        reason: Reason for escalation

    Example:
        >>> log_auto_escalation(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     state="disambiguating",
        ...     failure_count=3,
        ...     reason="max_failures_reached"
        ... )
    """
    log_fsm_event(
        event_type="auto_escalation",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=state,
        error=f"Escalated after {failure_count} failures: {reason}",
        extra={
            "failure_count": failure_count,
            "reason": reason
        }
    )


def log_context_contamination(
    conversation_id: str,
    clinic_id: str,
    state: str,
    slot_name: str,
    age_seconds: float
):
    """
    Log context contamination event (stale slot detection).

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        state: Current state
        slot_name: Name of stale slot
        age_seconds: Age of slot in seconds

    Example:
        >>> log_context_contamination(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     state="booking",
        ...     slot_name="appointment_date",
        ...     age_seconds=350.5
        ... )
    """
    log_fsm_event(
        event_type="context_contamination",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=state,
        error=f"Stale slot detected: {slot_name} (age={age_seconds:.1f}s)",
        extra={
            "slot_name": slot_name,
            "age_seconds": round(age_seconds, 2)
        }
    )


def log_bad_booking(
    conversation_id: str,
    clinic_id: str,
    state: str,
    reason: str,
    slots: Optional[Dict[str, Any]] = None
):
    """
    Log bad booking attempt.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        state: Current state
        reason: Reason for bad booking
        slots: Slots at time of bad booking (optional)

    Example:
        >>> log_bad_booking(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     state="booking",
        ...     reason="invalid_date",
        ...     slots={"date": "2025-01-01", "doctor": "Иванов"}
        ... )
    """
    log_fsm_event(
        event_type="bad_booking",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=state,
        slots=slots,
        error=f"Bad booking: {reason}",
        extra={
            "reason": reason
        }
    )


def log_race_condition(
    conversation_id: str,
    clinic_id: str,
    state: str,
    expected_version: int,
    actual_version: Optional[int] = None,
    retry_count: int = 0
):
    """
    Log CAS race condition (version conflict).

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        state: Current state
        expected_version: Version we expected
        actual_version: Actual version in database (optional)
        retry_count: Number of retries so far

    Example:
        >>> log_race_condition(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     state="collecting_slots",
        ...     expected_version=5,
        ...     actual_version=7,
        ...     retry_count=1
        ... )
    """
    log_fsm_event(
        event_type="race_condition",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=state,
        error=f"CAS conflict: expected version {expected_version}",
        extra={
            "expected_version": expected_version,
            "actual_version": actual_version,
            "retry_count": retry_count
        }
    )


def log_duplicate_message(
    conversation_id: str,
    clinic_id: str,
    message_sid: str,
    cached_response: str
):
    """
    Log duplicate message detection.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        message_sid: Message SID that was duplicate
        cached_response: Response from cache

    Example:
        >>> log_duplicate_message(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     message_sid="SM1234567890",
        ...     cached_response="Хорошо! К какому доктору?"
        ... )
    """
    log_fsm_event(
        event_type="duplicate_message",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state="unknown",  # State not known for duplicates
        extra={
            "message_sid": message_sid,
            "response_length": len(cached_response)
        }
    )


def log_intent_detection(
    conversation_id: str,
    clinic_id: str,
    state: str,
    message: str,
    detected_intent: str,
    confidence: Optional[float] = None,
    duration_ms: Optional[float] = None
):
    """
    Log intent detection result.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        state: Current state
        message: User message (truncated)
        detected_intent: Intent that was detected
        confidence: Confidence score (optional)
        duration_ms: Detection duration in milliseconds (optional)

    Example:
        >>> log_intent_detection(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     state="greeting",
        ...     message="записаться к доктору",
        ...     detected_intent="booking_intent",
        ...     confidence=0.95,
        ...     duration_ms=120.5
        ... )
    """
    extra = {
        "message_preview": message[:50] if len(message) > 50 else message,
        "detected_intent": detected_intent
    }

    if confidence is not None:
        extra["confidence"] = round(confidence, 3)

    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms, 2)

    log_fsm_event(
        event_type="intent_detection",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=state,
        intent=detected_intent,
        extra=extra
    )


def log_booking_success(
    conversation_id: str,
    clinic_id: str,
    slots: Dict[str, Any],
    appointment_id: Optional[str] = None
):
    """
    Log successful booking completion.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        slots: Final confirmed slots
        appointment_id: Created appointment ID (optional)

    Example:
        >>> log_booking_success(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     slots={"doctor": "Иванов", "date": "2025-10-20", "time": "14:00"},
        ...     appointment_id="appt_456"
        ... )
    """
    extra = {"appointment_id": appointment_id} if appointment_id else {}

    log_fsm_event(
        event_type="booking_success",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state="completed",
        slots=slots,
        extra=extra
    )


# ==============================================================================
# SUMMARY LOGGING
# ==============================================================================

def log_conversation_summary(
    conversation_id: str,
    clinic_id: str,
    final_state: str,
    total_messages: int,
    total_duration_seconds: float,
    transitions_count: int,
    success: bool
):
    """
    Log conversation summary at end of session.

    Args:
        conversation_id: Conversation identifier
        clinic_id: Clinic identifier
        final_state: Final state (completed/failed)
        total_messages: Total message count
        total_duration_seconds: Total conversation duration
        transitions_count: Number of state transitions
        success: Whether conversation succeeded

    Example:
        >>> log_conversation_summary(
        ...     conversation_id="conv_123",
        ...     clinic_id="clinic_001",
        ...     final_state="completed",
        ...     total_messages=12,
        ...     total_duration_seconds=180.5,
        ...     transitions_count=5,
        ...     success=True
        ... )
    """
    log_fsm_event(
        event_type="conversation_summary",
        conversation_id=conversation_id,
        clinic_id=clinic_id,
        state=final_state,
        extra={
            "total_messages": total_messages,
            "total_duration_seconds": round(total_duration_seconds, 2),
            "transitions_count": transitions_count,
            "success": success
        }
    )
