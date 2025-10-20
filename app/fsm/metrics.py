"""
FSM Prometheus Metrics Module

Provides production-ready observability for FSM operations with Prometheus metrics:
- State transition tracking with labels
- Context contamination detection
- Bad booking attempts
- Race condition monitoring (CAS conflicts)
- Duplicate message detection
- Auto-escalation tracking
- Active conversation gauges
- Transition duration histograms

These metrics complement the existing observability metrics in app/observability/metrics.py
and provide FSM-specific insights for production monitoring.
"""

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Create custom registry for FSM metrics (separate from main app metrics)
fsm_registry = CollectorRegistry()

# ==============================================================================
# STATE TRANSITION METRICS
# ==============================================================================

fsm_state_transitions_total = Counter(
    'fsm_state_transitions_total',
    'Total FSM state transitions',
    ['from_state', 'to_state', 'clinic_id'],
    registry=fsm_registry
)

# Transition duration histogram (buckets optimized for sub-second transitions)
fsm_transition_duration_seconds = Histogram(
    'fsm_transition_duration_seconds',
    'FSM state transition latency',
    ['from_state', 'to_state'],
    registry=fsm_registry,
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0)
)

# ==============================================================================
# DATA QUALITY METRICS
# ==============================================================================

fsm_context_contamination_total = Counter(
    'fsm_context_contamination_total',
    'Context contamination events detected (stale slots)',
    ['slot_name', 'clinic_id'],
    registry=fsm_registry
)

fsm_bad_bookings_total = Counter(
    'fsm_bad_bookings_total',
    'Bad booking attempts',
    ['reason', 'clinic_id'],
    registry=fsm_registry
)

# ==============================================================================
# CONCURRENCY METRICS
# ==============================================================================

fsm_race_conditions_total = Counter(
    'fsm_race_conditions_total',
    'CAS version conflicts detected',
    ['clinic_id'],
    registry=fsm_registry
)

fsm_duplicate_messages_total = Counter(
    'fsm_duplicate_messages_total',
    'Duplicate messages blocked by idempotency',
    ['clinic_id'],
    registry=fsm_registry
)

# ==============================================================================
# ESCALATION METRICS
# ==============================================================================

fsm_escalations_total = Counter(
    'fsm_escalations_total',
    'Auto-escalations to human agents',
    ['reason', 'clinic_id'],
    registry=fsm_registry
)

# ==============================================================================
# ACTIVE CONVERSATIONS
# ==============================================================================

fsm_active_conversations = Gauge(
    'fsm_active_conversations',
    'Current active conversations by state',
    ['state', 'clinic_id'],
    registry=fsm_registry
)

# ==============================================================================
# INTENT ACCURACY METRICS
# ==============================================================================

fsm_intent_accuracy_total = Counter(
    'fsm_intent_accuracy_total',
    'Intent detection accuracy tracking',
    ['intent', 'correct', 'clinic_id'],
    registry=fsm_registry
)

# ==============================================================================
# SLOT VALIDATION METRICS
# ==============================================================================

fsm_slot_validation_total = Counter(
    'fsm_slot_validation_total',
    'Slot validation attempts',
    ['slot_name', 'is_valid', 'clinic_id'],
    registry=fsm_registry
)

fsm_slot_extraction_duration_seconds = Histogram(
    'fsm_slot_extraction_duration_seconds',
    'Slot extraction latency',
    ['slot_name'],
    registry=fsm_registry,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0)
)

# ==============================================================================
# FALLBACK TRACKING METRICS (Task #74)
# ==============================================================================

fsm_fallback_total = Counter(
    'fsm_fallback_total',
    'Total fallback responses (should be 0 for known intents)',
    ['state', 'intent', 'clinic_id'],
    registry=fsm_registry
)

fsm_known_intent_fallback = Counter(
    'fsm_known_intent_fallback',
    'Known intents that fell to fallback (CRITICAL - should be 0)',
    ['state', 'intent', 'clinic_id'],
    registry=fsm_registry
)

# Intent distribution
fsm_intent_detected = Counter(
    'fsm_intent_detected',
    'Total intents detected',
    ['state', 'intent', 'topic', 'clinic_id'],
    registry=fsm_registry
)

# Response type tracking
fsm_response_type = Counter(
    'fsm_response_type',
    'Response generation method',
    ['type', 'state', 'clinic_id'],  # type: template, fallback, llm, state_specific
    registry=fsm_registry
)

# Response quality
fsm_response_latency = Histogram(
    'fsm_response_latency_seconds',
    'Time to generate response',
    ['response_type', 'state'],
    registry=fsm_registry,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0)
)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def record_state_transition(
    from_state: str,
    to_state: str,
    clinic_id: str,
    duration_seconds: float
):
    """
    Record a state transition with metrics.

    Args:
        from_state: Source state name
        to_state: Target state name
        clinic_id: Clinic identifier
        duration_seconds: Transition duration in seconds

    Example:
        >>> record_state_transition("greeting", "collecting_slots", "clinic_123", 0.05)
    """
    fsm_state_transitions_total.labels(
        from_state=from_state,
        to_state=to_state,
        clinic_id=clinic_id
    ).inc()

    fsm_transition_duration_seconds.labels(
        from_state=from_state,
        to_state=to_state
    ).observe(duration_seconds)

    logger.debug(
        f"FSM transition recorded: {from_state} -> {to_state} "
        f"(clinic={clinic_id}, duration={duration_seconds:.3f}s)"
    )


def record_context_contamination(slot_name: str, clinic_id: str):
    """
    Record context contamination detection (stale slot usage).

    Args:
        slot_name: Name of the contaminated slot
        clinic_id: Clinic identifier

    Example:
        >>> record_context_contamination("appointment_date", "clinic_123")
    """
    fsm_context_contamination_total.labels(
        slot_name=slot_name,
        clinic_id=clinic_id
    ).inc()

    logger.warning(f"Context contamination detected: slot={slot_name}, clinic={clinic_id}")


def record_bad_booking(reason: str, clinic_id: str):
    """
    Record a bad booking attempt.

    Args:
        reason: Reason for bad booking (e.g., "invalid_date", "invalid_doctor")
        clinic_id: Clinic identifier

    Example:
        >>> record_bad_booking("invalid_doctor", "clinic_123")
    """
    fsm_bad_bookings_total.labels(
        reason=reason,
        clinic_id=clinic_id
    ).inc()

    logger.warning(f"Bad booking recorded: reason={reason}, clinic={clinic_id}")


def record_race_condition(clinic_id: str):
    """
    Record a CAS version conflict (race condition).

    Args:
        clinic_id: Clinic identifier

    Example:
        >>> record_race_condition("clinic_123")
    """
    fsm_race_conditions_total.labels(clinic_id=clinic_id).inc()
    logger.warning(f"Race condition detected: clinic={clinic_id}")


def record_duplicate_message(clinic_id: str):
    """
    Record a duplicate message blocked by idempotency.

    Args:
        clinic_id: Clinic identifier

    Example:
        >>> record_duplicate_message("clinic_123")
    """
    fsm_duplicate_messages_total.labels(clinic_id=clinic_id).inc()
    logger.debug(f"Duplicate message blocked: clinic={clinic_id}")


def record_escalation(reason: str, clinic_id: str):
    """
    Record an auto-escalation to human agent.

    Args:
        reason: Escalation reason (e.g., "max_failures", "timeout")
        clinic_id: Clinic identifier

    Example:
        >>> record_escalation("max_failures", "clinic_123")
    """
    fsm_escalations_total.labels(
        reason=reason,
        clinic_id=clinic_id
    ).inc()

    logger.error(f"Escalation recorded: reason={reason}, clinic={clinic_id}")


def update_active_conversations(state: str, clinic_id: str, delta: int):
    """
    Update active conversation count for a state.

    Args:
        state: Conversation state name
        clinic_id: Clinic identifier
        delta: Change in count (+1 for new, -1 for completed)

    Example:
        >>> update_active_conversations("collecting_slots", "clinic_123", 1)
        >>> update_active_conversations("collecting_slots", "clinic_123", -1)
    """
    if delta > 0:
        fsm_active_conversations.labels(state=state, clinic_id=clinic_id).inc(delta)
    elif delta < 0:
        fsm_active_conversations.labels(state=state, clinic_id=clinic_id).dec(abs(delta))


def record_slot_validation(
    slot_name: str,
    is_valid: bool,
    clinic_id: str,
    duration_seconds: Optional[float] = None
):
    """
    Record slot validation attempt.

    Args:
        slot_name: Name of the slot
        is_valid: Whether validation succeeded
        clinic_id: Clinic identifier
        duration_seconds: Validation duration (optional)

    Example:
        >>> record_slot_validation("doctor_name", True, "clinic_123", 0.05)
    """
    fsm_slot_validation_total.labels(
        slot_name=slot_name,
        is_valid=str(is_valid),
        clinic_id=clinic_id
    ).inc()

    if duration_seconds is not None:
        fsm_slot_extraction_duration_seconds.labels(
            slot_name=slot_name
        ).observe(duration_seconds)


def record_intent_accuracy(intent: str, correct: bool, clinic_id: str):
    """
    Record intent detection accuracy.

    Args:
        intent: Detected intent name
        correct: Whether the intent was correct (validated by user or outcome)
        clinic_id: Clinic identifier

    Example:
        >>> record_intent_accuracy("booking_intent", True, "clinic_123")
    """
    fsm_intent_accuracy_total.labels(
        intent=intent,
        correct=str(correct),
        clinic_id=clinic_id
    ).inc()


def record_fallback_hit(state: str, intent: str, clinic_id: str):
    """
    Record when fallback response is used (Task #74).

    Args:
        state: Current conversation state
        intent: Intent that triggered fallback
        clinic_id: Clinic identifier

    Example:
        >>> record_fallback_hit("greeting", "unknown", "clinic_123")
    """
    fsm_fallback_total.labels(
        state=state,
        intent=intent,
        clinic_id=clinic_id
    ).inc()


def record_known_intent_fallback(state: str, intent: str, clinic_id: str):
    """
    Record CRITICAL: Known intent fell to fallback (Task #74).

    This should NEVER happen after Task #71. If this metric increases,
    it indicates a regression.

    Args:
        state: Current conversation state
        intent: Known intent that fell to fallback
        clinic_id: Clinic identifier

    Example:
        >>> record_known_intent_fallback("greeting", "booking_intent", "clinic_123")
    """
    fsm_known_intent_fallback.labels(
        state=state,
        intent=intent,
        clinic_id=clinic_id
    ).inc()
    logger.error(
        f"🚨 CRITICAL: Known intent {intent} fell to fallback in state {state}! "
        f"(clinic={clinic_id})"
    )


def record_intent_detection(
    state: str,
    intent: str,
    topic: Optional[str],
    clinic_id: str
):
    """
    Record intent detection for distribution analysis (Task #74).

    Args:
        state: Current conversation state
        intent: Detected intent
        topic: Topic (for topic_change intents)
        clinic_id: Clinic identifier

    Example:
        >>> record_intent_detection("greeting", "topic_change", "pricing", "clinic_123")
    """
    fsm_intent_detected.labels(
        state=state,
        intent=intent,
        topic=topic or "none",
        clinic_id=clinic_id
    ).inc()


def record_response_type(
    response_type: str,
    state: str,
    clinic_id: str,
    duration_seconds: Optional[float] = None
):
    """
    Record how response was generated (Task #74).

    Args:
        response_type: One of: template, fallback, llm, state_specific
        state: Current conversation state
        clinic_id: Clinic identifier
        duration_seconds: Response generation duration (optional)

    Example:
        >>> record_response_type("template", "greeting", "clinic_123", 0.05)
    """
    fsm_response_type.labels(
        type=response_type,
        state=state,
        clinic_id=clinic_id
    ).inc()

    if duration_seconds is not None:
        fsm_response_latency.labels(
            response_type=response_type,
            state=state
        ).observe(duration_seconds)


# ==============================================================================
# METRICS EXPORT
# ==============================================================================

def get_metrics() -> bytes:
    """
    Generate Prometheus metrics output for FSM metrics.

    Returns:
        bytes: Prometheus text format metrics

    Example:
        >>> metrics_data = get_metrics()
        >>> print(metrics_data.decode('utf-8'))
    """
    return generate_latest(fsm_registry)


def get_metrics_summary() -> dict:
    """
    Get human-readable FSM metrics summary for debugging.

    Returns:
        dict: Summary of key FSM metrics

    Example:
        >>> summary = get_metrics_summary()
        >>> print(f"Total transitions: {summary['transitions']['total']}")
    """
    try:
        # Calculate totals from metric samples
        transitions_total = sum(
            sample.value for sample in fsm_state_transitions_total.collect()[0].samples
        )

        contamination_total = sum(
            sample.value for sample in fsm_context_contamination_total.collect()[0].samples
        )

        bad_bookings_total = sum(
            sample.value for sample in fsm_bad_bookings_total.collect()[0].samples
        )

        race_conditions_total = sum(
            sample.value for sample in fsm_race_conditions_total.collect()[0].samples
        )

        duplicates_total = sum(
            sample.value for sample in fsm_duplicate_messages_total.collect()[0].samples
        )

        escalations_total = sum(
            sample.value for sample in fsm_escalations_total.collect()[0].samples
        )

        # Task #74 metrics
        fallback_total = sum(
            sample.value for sample in fsm_fallback_total.collect()[0].samples
        )

        known_intent_fallback_total = sum(
            sample.value for sample in fsm_known_intent_fallback.collect()[0].samples
        )

        intent_detected_total = sum(
            sample.value for sample in fsm_intent_detected.collect()[0].samples
        )

        response_type_total = sum(
            sample.value for sample in fsm_response_type.collect()[0].samples
        )

        return {
            'transitions': {
                'total': int(transitions_total)
            },
            'data_quality': {
                'contamination_events': int(contamination_total),
                'bad_bookings': int(bad_bookings_total)
            },
            'concurrency': {
                'race_conditions': int(race_conditions_total),
                'duplicate_messages': int(duplicates_total)
            },
            'escalations': {
                'total': int(escalations_total)
            },
            'fallbacks': {
                'total': int(fallback_total),
                'known_intent_fallback': int(known_intent_fallback_total)  # Should be 0!
            },
            'intents': {
                'detected': int(intent_detected_total)
            },
            'responses': {
                'total': int(response_type_total)
            }
        }
    except Exception as e:
        logger.error(f"Error generating metrics summary: {e}")
        return {
            'error': str(e)
        }
