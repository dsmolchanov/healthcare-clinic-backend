"""
Conversation State Machine

Manages conversation flow explicitly to prevent context confusion.
Tracks the current task and ensures focus on one thing at a time.

States:
- idle: No active task
- service_inquiry: User asking about services/prices
- booking_new: Creating new appointment
- booking_confirmation: Confirming appointment details
- rescheduling: Modifying existing appointment
- canceling: Canceling appointment
- awaiting_info: Waiting for user to provide information
- escalated: Handed off to human agent
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict
import json

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    """Possible conversation states"""
    IDLE = "idle"
    SERVICE_INQUIRY = "service_inquiry"
    BOOKING_NEW = "booking_new"
    BOOKING_CONFIRMATION = "booking_confirmation"
    RESCHEDULING = "rescheduling"
    CANCELING = "canceling"
    AWAITING_INFO = "awaiting_info"
    ESCALATED = "escalated"


@dataclass
class StateContext:
    """Context associated with current state"""
    # What we're working on
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    doctor_id: Optional[str] = None
    doctor_name: Optional[str] = None
    appointment_id: Optional[str] = None  # For reschedule/cancel

    # Booking details
    requested_date: Optional[str] = None
    requested_time: Optional[str] = None
    duration_minutes: Optional[int] = None

    # Additional info
    patient_id: Optional[str] = None
    notes: Optional[str] = None

    # State tracking
    missing_fields: List[str] = None
    last_question: Optional[str] = None

    def __post_init__(self):
        if self.missing_fields is None:
            self.missing_fields = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization"""
        data = asdict(self)
        # Handle list fields
        if 'missing_fields' in data and data['missing_fields'] is None:
            data['missing_fields'] = []
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StateContext':
        """Create from dict"""
        return cls(**data)


class ConversationStateMachine:
    """Manages conversation state transitions"""

    def __init__(self, redis_client):
        self.redis = redis_client
        self.state_ttl = 3600  # 1 hour

    def _make_key(self, session_id: str) -> str:
        """Generate Redis key for state"""
        return f"conv_state:{session_id}"

    async def get_state(self, session_id: str) -> tuple[ConversationState, StateContext]:
        """
        Get current conversation state and context

        Returns:
            Tuple of (state, context)
        """
        key = self._make_key(session_id)

        try:
            data = self.redis.get(key)

            if not data:
                # No state, default to idle
                return ConversationState.IDLE, StateContext()

            # Decode if bytes
            if isinstance(data, bytes):
                data = data.decode('utf-8')

            state_data = json.loads(data)

            state = ConversationState(state_data.get('state', 'idle'))
            context = StateContext.from_dict(state_data.get('context', {}))

            logger.debug(f"📊 Current state for {session_id}: {state}")
            return state, context

        except Exception as e:
            logger.error(f"Failed to get state for {session_id}: {e}")
            return ConversationState.IDLE, StateContext()

    async def set_state(
        self,
        session_id: str,
        state: ConversationState,
        context: StateContext
    ):
        """
        Set conversation state and context

        Args:
            session_id: Session identifier
            state: New conversation state
            context: State context
        """
        key = self._make_key(session_id)

        state_data = {
            'state': state.value,
            'context': context.to_dict(),
            'updated_at': datetime.utcnow().isoformat()
        }

        try:
            self.redis.setex(key, self.state_ttl, json.dumps(state_data))
            logger.info(f"✅ Set state for {session_id}: {state}")

        except Exception as e:
            logger.error(f"Failed to set state for {session_id}: {e}")

    async def transition(
        self,
        session_id: str,
        new_state: ConversationState,
        context_updates: Dict[str, Any] = None
    ) -> tuple[ConversationState, StateContext]:
        """
        Transition to new state with optional context updates

        Args:
            session_id: Session identifier
            new_state: Target state
            context_updates: Optional context field updates

        Returns:
            Tuple of (new_state, updated_context)
        """
        current_state, current_context = await self.get_state(session_id)

        # Validate transition
        if not self._is_valid_transition(current_state, new_state):
            logger.warning(
                f"⚠️ Invalid state transition: {current_state} → {new_state}"
            )
            # Allow transition anyway but log warning
            # (some transitions might be valid in edge cases)

        # Update context
        context_dict = current_context.to_dict()
        if context_updates:
            context_dict.update(context_updates)

        new_context = StateContext.from_dict(context_dict)

        # Save new state
        await self.set_state(session_id, new_state, new_context)

        logger.info(f"🔄 State transition: {current_state} → {new_state}")
        return new_state, new_context

    def _is_valid_transition(
        self,
        from_state: ConversationState,
        to_state: ConversationState
    ) -> bool:
        """
        Check if state transition is valid

        Allowed transitions:
        - idle → any state (new conversation)
        - any state → idle (reset)
        - any state → escalated (escalation)
        - booking_new → booking_confirmation
        - booking_confirmation → booking_new (corrections)
        - service_inquiry → booking_new
        """
        # Always allow idle or escalated
        if to_state in [ConversationState.IDLE, ConversationState.ESCALATED]:
            return True

        # From idle to any state
        if from_state == ConversationState.IDLE:
            return True

        # Booking flow
        if from_state == ConversationState.SERVICE_INQUIRY and to_state == ConversationState.BOOKING_NEW:
            return True

        if from_state == ConversationState.BOOKING_NEW and to_state == ConversationState.BOOKING_CONFIRMATION:
            return True

        if from_state == ConversationState.BOOKING_CONFIRMATION and to_state == ConversationState.BOOKING_NEW:
            return True  # Allow corrections

        # Any state to awaiting_info
        if to_state == ConversationState.AWAITING_INFO:
            return True

        # Warn about potentially invalid transitions
        return False

    async def reset_state(self, session_id: str):
        """Reset state to idle"""
        await self.set_state(
            session_id,
            ConversationState.IDLE,
            StateContext()
        )
        logger.info(f"🔄 Reset state for {session_id}")

    async def get_state_summary(self, session_id: str) -> str:
        """
        Get human-readable state summary

        Returns:
            Summary string for system prompt context
        """
        state, context = await self.get_state(session_id)

        if state == ConversationState.IDLE:
            return "No active task. Ready to help with new request."

        summaries = {
            ConversationState.SERVICE_INQUIRY: f"Currently helping user learn about: {context.service_name or 'services'}",
            ConversationState.BOOKING_NEW: f"Creating new appointment for: {context.service_name or 'service'}",
            ConversationState.BOOKING_CONFIRMATION: f"Confirming appointment details for: {context.service_name or 'service'}",
            ConversationState.RESCHEDULING: f"Rescheduling appointment ID: {context.appointment_id}",
            ConversationState.CANCELING: f"Canceling appointment ID: {context.appointment_id}",
            ConversationState.AWAITING_INFO: f"Waiting for user to provide: {', '.join(context.missing_fields)}",
            ConversationState.ESCALATED: "Conversation escalated to human agent"
        }

        return summaries.get(state, f"Current state: {state}")

    async def should_inject_context(
        self,
        session_id: str,
        pending_action: str
    ) -> tuple[bool, str]:
        """
        Determine if pending action context should be injected

        Args:
            session_id: Session identifier
            pending_action: Pending action description

        Returns:
            Tuple of (should_inject, reason)
        """
        state, context = await self.get_state(session_id)

        # Never inject if user started a new task
        if state in [ConversationState.SERVICE_INQUIRY, ConversationState.BOOKING_NEW]:
            if context.service_name:
                # User is working on a specific service
                # Only inject if pending action is about the SAME service
                if context.service_name.lower() not in pending_action.lower():
                    return False, f"User focused on {context.service_name}, pending action unrelated"

        # If state is awaiting_info, inject context
        if state == ConversationState.AWAITING_INFO:
            return True, "User is expected to provide information"

        # If escalated, don't inject (human will handle)
        if state == ConversationState.ESCALATED:
            return False, "Conversation escalated to human"

        # Default: inject only if idle or confirmation states
        if state in [ConversationState.IDLE, ConversationState.BOOKING_CONFIRMATION]:
            return True, "State allows pending context"

        return False, f"Current state ({state}) doesn't warrant context injection"


# Singleton instance
_state_machine: Optional[ConversationStateMachine] = None


def get_conversation_state_machine(redis_client) -> ConversationStateMachine:
    """Get or create singleton ConversationStateMachine instance"""
    global _state_machine
    if _state_machine is None:
        _state_machine = ConversationStateMachine(redis_client)
    return _state_machine
