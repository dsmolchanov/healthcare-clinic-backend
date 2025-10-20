"""
FSM Intent-State Coverage Matrix

Defines required coverage for all (state, intent) combinations.
Used for validation and testing.
"""

from typing import Dict, List, Set
from .models import ConversationState
from .intent_router import Intent


# Required coverage: All states must handle these intents
REQUIRED_COVERAGE: Dict[ConversationState, List[str]] = {
    ConversationState.GREETING: [
        Intent.GREETING,          # ✅ Implemented
        Intent.BOOKING_INTENT,    # ✅ Implemented
        Intent.INFORMATION,       # ✅ Implemented
        Intent.TOPIC_CHANGE,      # ✅ Implemented
        Intent.CONFIRM,           # ✅ Implemented
        Intent.DENY,              # ✅ Implemented
        Intent.DISAMBIGUATE,      # ⚠️ Falls to fallback (acceptable)
        Intent.ACKNOWLEDGMENT,    # ⚠️ Falls to fallback (acceptable)
    ],

    ConversationState.COLLECTING_SLOTS: [
        Intent.INFORMATION,       # ✅ Slot extraction
        Intent.DENY,              # ⚠️ Needs explicit handling
        Intent.TOPIC_CHANGE,      # ⚠️ Should pause and answer
        Intent.CONFIRM,           # ⚠️ Premature confirmation
        Intent.BOOKING_INTENT,    # ⚠️ Redundant but should handle
    ],

    ConversationState.AWAITING_CONFIRMATION: [
        Intent.CONFIRM,           # ✅ Proceed to booking
        Intent.DENY,              # ✅ Back to collecting
        Intent.DISAMBIGUATE,      # ✅ Transition to disambiguating
        Intent.TOPIC_CHANGE,      # ⚠️ Needs handling (info during confirmation)
    ],

    ConversationState.DISAMBIGUATING: [
        Intent.CONFIRM,           # ✅ Re-handle confirmation
        Intent.DENY,              # ✅ Back to collecting
        Intent.DISAMBIGUATE,      # ✅ Still unclear (increment failure)
    ],

    ConversationState.AWAITING_CLARIFICATION: [
        Intent.INFORMATION,       # ✅ Corrected value
        Intent.DENY,              # ⚠️ Cancel clarification?
        Intent.TOPIC_CHANGE,      # ⚠️ Topic change during clarification
    ],

    # Terminal states don't need coverage (no state changes)
}

# Intent handlers that are acceptable to fall to fallback
FALLBACK_ACCEPTABLE: Dict[ConversationState, Set[str]] = {
    ConversationState.GREETING: {
        Intent.DISAMBIGUATE,
        Intent.ACKNOWLEDGMENT,
    },
}


def validate_coverage(state: ConversationState, intent: str) -> bool:
    """
    Check if (state, intent) combination has required coverage.

    Returns:
        True if coverage required, False if fallback acceptable
    """
    required = REQUIRED_COVERAGE.get(state, [])
    acceptable_fallback = FALLBACK_ACCEPTABLE.get(state, set())

    if intent in required and intent not in acceptable_fallback:
        return True  # Must have explicit handler

    return False  # Fallback acceptable


def get_missing_coverage() -> List[tuple]:
    """
    Analyze handlers and return missing coverage.

    Returns:
        List of (state, intent) tuples that need handlers
    """
    missing = []

    # TODO: Introspect StateHandler methods to check coverage
    # For now, return empty (manual verification)

    return missing
