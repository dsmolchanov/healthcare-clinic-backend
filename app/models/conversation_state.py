"""
Unified Conversation State: Single source of truth for all channels.

SIMPLIFIED (Expert Opinion 3):
- FlowState + BookingTask + constraints + language
- Removed InfoTask (implied by flow_state == INFO)
- Removed TurnStatus fine-grained distinctions (cognitive overhead)

This replaces the implicit state scattered across:
- Session metadata
- Pipeline context
- LangGraph state
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class FlowState(str, Enum):
    """Domain/workflow state (persists across turns)."""
    IDLE = "idle"
    INFO = "info"           # Answering questions (no explicit InfoTask needed)
    SCHEDULING = "scheduling"  # Booking flow (uses BookingTask)
    ESCALATED = "escalated" # Human handoff
    COMPLETED = "completed"


class BookingTask(BaseModel):
    """
    First-class goal object for scheduling flow.

    ENHANCEMENT: Added "type" field for reliable branching (Opinion 3).
    """
    type: str = "booking"  # For reliable node branching
    goal: str = "book_appointment"
    service: Optional[str] = None
    service_id: Optional[str] = None
    doctor_id: Optional[str] = None
    date_window: Optional[Dict[str, str]] = None
    slots_offered: List[Dict[str, Any]] = []
    slot_selected: Optional[Dict[str, Any]] = None
    confirmed: bool = False


class ConversationState(BaseModel):
    """
    The canonical state object passed through the graph.

    SIMPLIFIED for v1:
    - FlowState for domain state
    - BookingTask for scheduling (only task type for v1)
    - constraints for tool gating
    - language for prompts

    Stored in conversation_sessions.metadata as JSON.

    Lifecycle:
    1. SessionManager loads from conversation_sessions.metadata (or initializes defaults)
    2. State passed to HealthcareLangGraph.process() as conversation_state
    3. Graph updates state during execution (flow_state, active_task, turn_count)
    4. SessionManager writes updated state back to conversation_sessions.metadata
    """
    # Core state
    flow_state: FlowState = FlowState.IDLE

    # Active task (only BookingTask for v1)
    active_task: Optional[Dict[str, Any]] = None  # BookingTask as dict with "type": "booking"

    # Constraints (from existing ToolStateGate)
    constraints: Dict[str, Any] = {}

    # Language (unified, no duplicate detection)
    language: str = "es"

    # Context
    clinic_id: Optional[str] = None
    session_id: Optional[str] = None

    # Metadata
    turn_count: int = 0

    def to_langgraph_state(self) -> Dict[str, Any]:
        """Convert to LangGraph state dict."""
        return {
            "flow_state": self.flow_state.value,
            "active_task": self.active_task,
            "constraints": self.constraints,
            "language": self.language,
            "metadata": {
                "clinic_id": self.clinic_id,
                "session_id": self.session_id,
                "turn_count": self.turn_count,
            }
        }

    @classmethod
    def from_langgraph_state(cls, state: Dict[str, Any]) -> "ConversationState":
        """Reconstruct from LangGraph state dict."""
        metadata = state.get("metadata", {})
        return cls(
            flow_state=FlowState(state.get("flow_state", "idle")),
            active_task=state.get("active_task"),
            constraints=state.get("constraints", {}),
            language=state.get("language", "es"),
            clinic_id=metadata.get("clinic_id"),
            session_id=metadata.get("session_id"),
            turn_count=metadata.get("turn_count", 0),
        )

    @classmethod
    def from_session_metadata(cls, metadata: Dict[str, Any]) -> "ConversationState":
        """
        Reconstruct from session metadata stored in database.

        Args:
            metadata: Dict from conversation_sessions.metadata column

        Returns:
            ConversationState instance
        """
        conversation_state_data = metadata.get("conversation_state", {})
        return cls(
            flow_state=FlowState(conversation_state_data.get("flow_state", "idle")),
            active_task=conversation_state_data.get("active_task"),
            constraints=conversation_state_data.get("constraints", {}),
            language=conversation_state_data.get("language", metadata.get("language", "es")),
            clinic_id=metadata.get("clinic_id"),
            session_id=metadata.get("session_id"),
            turn_count=conversation_state_data.get("turn_count", 0),
        )

    def to_session_metadata(self) -> Dict[str, Any]:
        """
        Convert to format suitable for storing in session metadata.

        Returns:
            Dict to merge into conversation_sessions.metadata
        """
        return {
            "conversation_state": {
                "flow_state": self.flow_state.value,
                "active_task": self.active_task,
                "constraints": self.constraints,
                "language": self.language,
                "turn_count": self.turn_count,
            }
        }

    def start_booking_task(
        self,
        service: Optional[str] = None,
        service_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
    ) -> "ConversationState":
        """
        Start a new booking task, updating flow state.

        Args:
            service: Service name
            service_id: Service ID
            doctor_id: Doctor ID if specified

        Returns:
            Updated ConversationState (new instance)
        """
        task = BookingTask(
            service=service,
            service_id=service_id,
            doctor_id=doctor_id,
        )
        return ConversationState(
            flow_state=FlowState.SCHEDULING,
            active_task=task.model_dump(),
            constraints=self.constraints,
            language=self.language,
            clinic_id=self.clinic_id,
            session_id=self.session_id,
            turn_count=self.turn_count,
        )

    def complete_booking(self, slot: Dict[str, Any]) -> "ConversationState":
        """
        Complete the booking task with selected slot.

        Args:
            slot: Selected appointment slot

        Returns:
            Updated ConversationState (new instance)
        """
        if self.active_task and self.active_task.get("type") == "booking":
            updated_task = {**self.active_task, "slot_selected": slot, "confirmed": True}
        else:
            updated_task = None

        return ConversationState(
            flow_state=FlowState.COMPLETED,
            active_task=updated_task,
            constraints=self.constraints,
            language=self.language,
            clinic_id=self.clinic_id,
            session_id=self.session_id,
            turn_count=self.turn_count,
        )

    def escalate(self, reason: str = "user_request") -> "ConversationState":
        """
        Escalate to human operator.

        Args:
            reason: Escalation reason

        Returns:
            Updated ConversationState (new instance)
        """
        return ConversationState(
            flow_state=FlowState.ESCALATED,
            active_task=self.active_task,
            constraints={**self.constraints, "escalation_reason": reason},
            language=self.language,
            clinic_id=self.clinic_id,
            session_id=self.session_id,
            turn_count=self.turn_count,
        )

    def increment_turn(self) -> "ConversationState":
        """
        Increment turn count.

        Returns:
            Updated ConversationState (new instance)
        """
        return ConversationState(
            flow_state=self.flow_state,
            active_task=self.active_task,
            constraints=self.constraints,
            language=self.language,
            clinic_id=self.clinic_id,
            session_id=self.session_id,
            turn_count=self.turn_count + 1,
        )
