"""
Two-Layer State Model for Conversation Management.

Phase 3A of Agentic Flow Architecture Refactor.

This module introduces a composite state model that separates:
1. FlowState - Domain/workflow states (where are we in the conversation?)
2. TurnStatus - Interaction-level meta-state (whose turn is it?)

Why Two Layers?
--------------
The previous system conflated three notions of "state":
- FSM states (GREETING, COLLECTING_SLOTS, etc.) - workflow states
- Session turn_status (user_turn, agent_action_pending) - interaction level
- AI path conversation_state (intent + constraint context)

Stuffing all into one enum caused semantic confusion. This composite model
provides clarity and extensibility.
"""

from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class FlowState(str, Enum):
    """
    Domain/workflow state - where are we in the conversation flow?

    Maps directly to existing FSM ConversationState values for compatibility,
    with extensions for AI path handling.
    """
    # General states (AI path)
    IDLE = "idle"
    INFO_SEEKING = "info_seeking"

    # Booking flow states (from FSM)
    GREETING = "greeting"
    COLLECTING_SLOTS = "collecting_slots"
    PRESENTING_SLOTS = "presenting_slots"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DISAMBIGUATING = "disambiguating"
    BOOKING = "booking"

    # Terminal states
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"

    @classmethod
    def from_fsm_state(cls, fsm_state_value: str) -> "FlowState":
        """Convert FSM ConversationState value to FlowState."""
        try:
            return cls(fsm_state_value)
        except ValueError:
            # Unknown FSM state, default to IDLE
            return cls.IDLE

    @classmethod
    def from_episode_type(cls, episode_type: str) -> "FlowState":
        """Infer FlowState from AI path episode_type."""
        mapping = {
            "BOOKING": cls.COLLECTING_SLOTS,
            "INFO_SEEKING": cls.INFO_SEEKING,
            "GENERAL": cls.IDLE,
            "GREETING": cls.GREETING,
            "ESCALATION": cls.ESCALATED,
        }
        return mapping.get(episode_type, cls.IDLE)


class TurnStatus(str, Enum):
    """
    Interaction-level status - whose turn is it?

    This is orthogonal to flow state. A conversation can be in
    COLLECTING_SLOTS flow state while having agent_action_pending turn status.

    Values match existing session.turn_status for backward compatibility.
    """
    USER_TURN = "user_turn"                    # Waiting for user input
    AGENT_ACTION_PENDING = "agent_action_pending"  # Agent promised followup
    AGENT_TURN = "agent_turn"                  # Agent should respond (for jobs)
    RESOLVED = "resolved"                      # Conversation completed
    ESCALATED = "escalated"                    # Handed to human

    @classmethod
    def from_session_value(cls, value: str) -> "TurnStatus":
        """Convert session turn_status string to TurnStatus enum."""
        try:
            return cls(value)
        except ValueError:
            return cls.USER_TURN


class ConversationState(BaseModel):
    """
    Composite state model with two layers.

    This replaces the single-enum approach with a more expressive model
    that separates concerns:
    - flow_state: What step of the workflow are we in?
    - turn_status: Who should act next?
    - pending_action: What did the agent promise to do?
    """
    flow_state: FlowState = Field(
        default=FlowState.IDLE,
        description="Current position in the conversation workflow"
    )
    turn_status: TurnStatus = Field(
        default=TurnStatus.USER_TURN,
        description="Whose turn it is to act"
    )

    # Additional context for pending actions
    pending_action: Optional[str] = Field(
        default=None,
        description="Description of action the agent promised (e.g., 'check availability')"
    )
    pending_since: Optional[datetime] = Field(
        default=None,
        description="When the pending action was promised"
    )

    # Optional episode tracking
    episode_type: Optional[str] = Field(
        default=None,
        description="AI path episode type (BOOKING, INFO_SEEKING, etc.)"
    )

    def is_terminal(self) -> bool:
        """Check if conversation is in a terminal state."""
        return self.flow_state in [
            FlowState.COMPLETED,
            FlowState.FAILED,
            FlowState.ESCALATED,
        ]

    def is_booking_flow(self) -> bool:
        """Check if currently in booking-related flow."""
        return self.flow_state in [
            FlowState.COLLECTING_SLOTS,
            FlowState.PRESENTING_SLOTS,
            FlowState.AWAITING_CLARIFICATION,
            FlowState.AWAITING_CONFIRMATION,
            FlowState.DISAMBIGUATING,
            FlowState.BOOKING,
        ]

    def allows_booking_tools(self) -> bool:
        """
        Check if booking tools are allowed in current flow state.

        NOTE: This is a convenience method. Actual enforcement is done
        by ToolStateGate reading from x_meta.allowed_states (Phase 1A).
        """
        return self.flow_state in [
            FlowState.IDLE,  # AI path can start booking from IDLE
            FlowState.COLLECTING_SLOTS,
            FlowState.PRESENTING_SLOTS,
            FlowState.AWAITING_CONFIRMATION,
            FlowState.BOOKING,
        ]

    def allows_info_tools(self) -> bool:
        """Check if information-seeking tools are allowed."""
        # Info tools are allowed in most non-terminal states
        return not self.is_terminal()

    def waiting_for_user(self) -> bool:
        """Check if we're waiting for user input."""
        return self.turn_status == TurnStatus.USER_TURN

    def agent_needs_to_act(self) -> bool:
        """Check if agent has a pending action to complete."""
        return self.turn_status in [
            TurnStatus.AGENT_ACTION_PENDING,
            TurnStatus.AGENT_TURN,
        ]

    def to_legacy_dict(self) -> dict:
        """Convert to legacy session format for backward compatibility."""
        return {
            "conversation_state": self.flow_state.value,
            "turn_status": self.turn_status.value,
            "last_agent_action": self.pending_action,
            "pending_since": self.pending_since.isoformat() if self.pending_since else None,
            "episode_type": self.episode_type,
        }

    @classmethod
    def from_session(cls, session: dict) -> "ConversationState":
        """
        Construct ConversationState from session data.

        Handles both FSM path and AI path session formats.
        """
        # Get turn status
        turn_status_str = session.get("turn_status", "user_turn")
        turn_status = TurnStatus.from_session_value(turn_status_str)

        # Get flow state - try multiple sources
        flow_state = FlowState.IDLE

        # 1. Check for explicit conversation_state (FSM path)
        if "conversation_state" in session:
            flow_state = FlowState.from_fsm_state(session["conversation_state"])

        # 2. Check for FSM state object
        elif "fsm_state" in session and isinstance(session["fsm_state"], dict):
            fsm_current = session["fsm_state"].get("current_state", "idle")
            flow_state = FlowState.from_fsm_state(fsm_current)

        # 3. Infer from episode_type (AI path)
        elif "episode_type" in session:
            flow_state = FlowState.from_episode_type(session["episode_type"])

        # Get pending action details
        pending_action = session.get("last_agent_action")
        pending_since_str = session.get("pending_since")
        pending_since = None
        if pending_since_str:
            try:
                pending_since = datetime.fromisoformat(pending_since_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return cls(
            flow_state=flow_state,
            turn_status=turn_status,
            pending_action=pending_action,
            pending_since=pending_since,
            episode_type=session.get("episode_type"),
        )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "flow_state": "collecting_slots",
                    "turn_status": "user_turn",
                    "pending_action": None,
                    "pending_since": None,
                    "episode_type": "BOOKING",
                },
                {
                    "flow_state": "idle",
                    "turn_status": "agent_action_pending",
                    "pending_action": "check availability for next week",
                    "pending_since": "2025-01-15T14:30:00Z",
                    "episode_type": "BOOKING",
                },
            ]
        }
    }
