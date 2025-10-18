"""
End-to-End (E2E) Tests for FSM Conversation Flows

This package contains comprehensive end-to-end test scenarios that validate
complete conversation flows through the FSM system, from greeting to booking
completion or failure states.

Test Scenarios:
- test_happy_path.py: Full successful booking flow
- test_topic_change.py: Handling topic changes during booking
- test_disambiguation.py: Handling unclear/ambiguous responses
- test_auto_escalation.py: Automatic escalation to FAILED after 3 failures
- test_doctor_validation.py: Invalid doctor name handling and suggestions
- test_slot_freshness.py: Stale slot detection and re-confirmation
- test_concurrent_bookings.py: Race condition prevention for concurrent bookings
- test_idempotency.py: Duplicate webhook handling with idempotency
"""
