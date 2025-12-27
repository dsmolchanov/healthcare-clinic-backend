"""FSM-based orchestration for healthcare conversations.

This module implements a Functional Core FSM architecture where:
- LLMs are used ONLY for parsing (one-shot router)
- All business logic is pure, deterministic Python
- The FSM handles state transitions with zero LLM calls

Key principle: LLMs understand; Code decides.
"""

from .types import (
    ActionType,
    AskUser,
    CallTool,
    Respond,
    Escalate,
    Action,
    RouterOutput,
    UserEvent,
    ToolResultEvent,
    Event,
)
from .state import (
    BookingStage,
    BookingState,
    PricingStage,
    PricingState,
    CancelStage,
    CancelState,
)
from . import booking_fsm
from . import pricing_fsm
from .router import route_message, fallback_router

__all__ = [
    # Action types
    'ActionType',
    'AskUser',
    'CallTool',
    'Respond',
    'Escalate',
    'Action',
    # Events
    'RouterOutput',
    'UserEvent',
    'ToolResultEvent',
    'Event',
    # State types
    'BookingStage',
    'BookingState',
    'PricingStage',
    'PricingState',
    'CancelStage',
    'CancelState',
    # FSM modules
    'booking_fsm',
    'pricing_fsm',
    # Router
    'route_message',
    'fallback_router',
]
