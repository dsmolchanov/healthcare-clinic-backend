"""
FSM Constants Module

This module defines enums and constants for the Finite State Machine (FSM) system.
Includes conversation states, slot sources, and TTL values for Redis keys.
"""

from enum import Enum


class ConversationState(str, Enum):
    """
    FSM states for conversation flow.

    State transitions typically follow this flow:
    GREETING -> COLLECTING_SLOTS -> AWAITING_CONFIRMATION -> BOOKING -> COMPLETED

    Alternative paths:
    - AWAITING_CLARIFICATION: When LLM needs more information
    - DISAMBIGUATING: When multiple options match user input
    - FAILED: When booking or validation fails
    """
    GREETING = "greeting"
    COLLECTING_SLOTS = "collecting_slots"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DISAMBIGUATING = "disambiguating"
    BOOKING = "booking"
    COMPLETED = "completed"
    FAILED = "failed"


class SlotSource(str, Enum):
    """
    Provenance tracking for slot values.

    Indicates how a slot value was obtained:
    - LLM_EXTRACT: Extracted from user message by LLM
    - USER_CONFIRM: Explicitly confirmed by user
    - DB_LOOKUP: Retrieved from database query
    """
    LLM_EXTRACT = "llm_extract"
    USER_CONFIRM = "user_confirm"
    DB_LOOKUP = "db_lookup"


# State Transition Rules
# Defines valid FSM state transitions
# Terminal states (COMPLETED, FAILED) have empty transition lists
VALID_TRANSITIONS = {
    ConversationState.GREETING: [
        ConversationState.COLLECTING_SLOTS,
        ConversationState.FAILED
    ],
    ConversationState.COLLECTING_SLOTS: [
        ConversationState.AWAITING_CLARIFICATION,
        ConversationState.AWAITING_CONFIRMATION,
        ConversationState.FAILED
    ],
    ConversationState.AWAITING_CLARIFICATION: [
        ConversationState.COLLECTING_SLOTS,
        ConversationState.AWAITING_CONFIRMATION,
        ConversationState.FAILED
    ],
    ConversationState.AWAITING_CONFIRMATION: [
        ConversationState.BOOKING,
        ConversationState.DISAMBIGUATING,
        ConversationState.COLLECTING_SLOTS,
        ConversationState.FAILED
    ],
    ConversationState.DISAMBIGUATING: [
        ConversationState.AWAITING_CONFIRMATION,
        ConversationState.COLLECTING_SLOTS,
        ConversationState.FAILED
    ],
    ConversationState.BOOKING: [
        ConversationState.COMPLETED,
        ConversationState.FAILED
    ],
    ConversationState.COMPLETED: [],  # Terminal state
    ConversationState.FAILED: []  # Terminal state
}

# Redis TTL Constants (in seconds)
FSM_STATE_TTL = 86400  # 24 hours - Main FSM state persistence
IDEMPOTENCY_TTL = 3600  # 1 hour - Webhook deduplication window
SLOT_STALENESS_THRESHOLD = 300  # 5 minutes - When slot evidence becomes stale

# FSM Behavior Constants
MAX_FAILURES = 3  # Maximum consecutive failures before escalating to FAILED state

# Redis Key Patterns
# fsm:state:{conversation_id} - Stores FSMState JSON
# fsm:idempotency:{message_sid} - Stores IdempotencyRecord JSON
