"""Action and Event types for FSM-based orchestration.

These types define the interface between the FSM and the outside world:
- Actions: What the FSM wants to do (ask user, call tool, respond)
- Events: What happened (user message, tool result)
"""

from dataclasses import dataclass
from typing import Union, Dict, Any, Optional, Literal
from enum import Enum, auto


class ActionType(Enum):
    """Types of actions the FSM can emit."""
    ASK_USER = auto()
    CALL_TOOL = auto()
    RESPOND = auto()
    ERROR = auto()      # For unhandled states - enables exhaustive coverage
    ESCALATE = auto()   # For human handoff scenarios


@dataclass(frozen=True)
class AskUser:
    """Ask user for clarification - expects response.

    Attributes:
        text: The question/prompt to show the user
        field_awaiting: Which field we're collecting (e.g., "patient_phone")
    """
    text: str
    field_awaiting: Optional[str] = None


@dataclass(frozen=True)
class CallTool:
    """Execute a tool - result feeds back as ToolResultEvent.

    Attributes:
        name: Tool name (e.g., "check_availability", "book_appointment")
        args: Arguments to pass to the tool
    """
    name: str
    args: Dict[str, Any]


@dataclass(frozen=True)
class Respond:
    """Final response to user - ends the turn.

    Attributes:
        text: The response text to show the user
    """
    text: str


@dataclass(frozen=True)
class Escalate:
    """Escalate to human - ends the turn with handoff.

    Attributes:
        reason: Why escalation is needed (e.g., "max_clarifications_exceeded")
        context: Additional context for the handoff
    """
    reason: str
    context: Optional[Dict[str, Any]] = None


# Union of all action types
Action = Union[AskUser, CallTool, Respond, Escalate]


@dataclass(frozen=True)
class RouterOutput:
    """Output from one-shot router LLM.

    IMPORTANT: Date/time fields contain RAW strings from user input.
    Do NOT let LLM calculate ISO dates - LLMs are bad at calendar math.
    Let Python tools (dateparser) resolve to actual datetimes.

    Attributes:
        route: The intent route ("scheduling", "pricing", "info", "doctor_info", "cancel", "exit")
        doctor_info_kind: For doctor_info route: "exists" | "list" | "recommend"
        service_type: Type of service requested (e.g., "cleaning", "checkup")
        target_date: Raw date string (e.g., "tomorrow", "next tuesday")
        time_of_day: Raw time string (e.g., "morning", "2pm")
        doctor_name: Requested doctor name
        patient_name: Patient's name if provided
        patient_phone: Patient's phone if provided
        has_pain: Whether the user mentioned pain/discomfort
        cancel_intent: Whether user wants to cancel
        confidence: Router's confidence in the classification
        language: Detected language code (e.g., "en", "ru", "es")
    """
    route: str  # "scheduling", "pricing", "info", "doctor_info", "cancel", "exit"
    doctor_info_kind: Optional[Literal["exists", "list", "recommend"]] = None  # For doctor_info route
    service_type: Optional[str] = None
    target_date: Optional[str] = None  # Raw string: "tomorrow", "next tuesday" - NOT ISO
    time_of_day: Optional[str] = None  # Raw string: "morning", "2pm" - NOT calculated
    doctor_name: Optional[str] = None
    patient_name: Optional[str] = None
    patient_phone: Optional[str] = None
    has_pain: bool = False
    cancel_intent: bool = False
    confidence: float = 1.0
    language: str = "en"  # Detected language for downstream use


@dataclass
class UserEvent:
    """User message + router output.

    Attributes:
        text: The raw user message text
        router: Parsed router output with extracted entities
        language: Detected language code
    """
    text: str
    router: RouterOutput
    language: str = "en"


@dataclass
class ToolResultEvent:
    """Result from a tool execution.

    Attributes:
        tool_name: Name of the tool that was called
        result: The result dictionary from the tool
        success: Whether the tool call succeeded
    """
    tool_name: str
    result: Dict[str, Any]
    success: bool = True


# Union of all event types
Event = Union[UserEvent, ToolResultEvent]
