"""
Preference Narrowing Models

Defines the canonical cases and instructions for narrowing conversation flow.
"""

from enum import StrEnum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


class NarrowingAction(StrEnum):
    """What the agent should do next"""
    ASK_QUESTION = "ask_question"
    CALL_TOOL = "call_tool"


class QuestionType(StrEnum):
    """
    Language-neutral question types.
    LLM converts these to localized questions based on user's language.
    """
    ASK_FOR_SERVICE = "ask_for_service"           # "What service do you need?"
    ASK_FOR_TIME = "ask_for_time"                 # "What day/time works?"
    ASK_FOR_DOCTOR_PREFERENCE = "ask_for_doctor"  # "Dr. X or Dr. Y?"
    ASK_TIME_WITH_DOCTOR = "ask_time_with_doctor" # "When would you like to see Dr. X?"
    ASK_TIME_WITH_SERVICE = "ask_time_with_service"  # "When for your cleaning?"
    ASK_TODAY_OR_TOMORROW = "ask_today_or_tomorrow"  # Urgent: "Today or tomorrow?"
    SUGGEST_CONSULTATION = "suggest_consultation"    # "No specialist, try consultation?"
    ASK_DOCTOR_OR_FIRST_AVAILABLE = "ask_first_available"  # "Preference or first available?"


class NarrowingCase(StrEnum):
    """Canonical cases based on known constraints"""
    FULLY_SPECIFIED = "doctor+service+time"  # Ready to search
    SERVICE_ONLY = "service_only"            # Know service, need doctor/time
    SERVICE_AND_TIME = "service+time"        # Know both, can search
    SERVICE_AND_DOCTOR = "service+doctor"    # Know both, need time
    DOCTOR_ONLY = "doctor_only"              # Know doctor, need service
    TIME_ONLY = "time_only"                  # Know time, need service
    NOTHING_KNOWN = "nothing_known"          # Start from scratch
    URGENT_NO_TIME = "urgent_no_time"        # Emergency without specific time


class UrgencyLevel(StrEnum):
    """Urgency classification from user message"""
    ROUTINE = "routine"   # "whenever", "flexible" -> 7-14 days
    SOON = "soon"         # "this week", "soon" -> 3-7 days
    URGENT = "urgent"     # "ASAP", "emergency", "hurts" -> 0-1 days


@dataclass
class ToolCallPlan:
    """Parameters for calling check_availability"""
    tool_name: str = "check_availability"
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NarrowingInstruction:
    """
    The output of PreferenceNarrowingService.decide()

    Either tells LLM to ask a specific question OR call a tool with specific params.
    """
    action: NarrowingAction
    case: NarrowingCase

    # For ASK_QUESTION (language-neutral)
    question_type: Optional[QuestionType] = None
    question_args: Dict[str, Any] = field(default_factory=dict)  # e.g., {"doctor_names": ["Dr. X", "Dr. Y"]}
    question_context: Optional[str] = None  # Extra context for LLM

    # For CALL_TOOL
    tool_call: Optional[ToolCallPlan] = None

    # Metadata
    eligible_doctor_count: Optional[int] = None  # None = RPC error, 0 = actually zero
    urgency: UrgencyLevel = UrgencyLevel.ROUTINE
