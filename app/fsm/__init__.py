"""
FSM Package

Finite State Machine (FSM) system for managing conversation state
across WhatsApp and other channels.

Exports:
- ConversationState: Enum of FSM states
- SlotSource: Enum of slot provenance sources
- SlotEvidence: Pydantic model for slot tracking
- FSMState: Pydantic model for complete FSM state
- IdempotencyRecord: Pydantic model for webhook deduplication
- FSM_STATE_TTL: Redis TTL for FSM state (24 hours)
- IDEMPOTENCY_TTL: Redis TTL for idempotency records (1 hour)
- SLOT_STALENESS_THRESHOLD: Age threshold for stale slots (5 minutes)
- VALID_TRANSITIONS: State transition rules dictionary
- RedisClient: Redis client class with CAS support
- redis_client: Singleton Redis client instance
- FSMManager: Core FSM orchestration class
- InvalidTransitionError: Exception for invalid state transitions
- SlotManager: Slot validation and evidence tracking
- Intent: Intent type constants
- IntentRouter: Regex-based intent detection
- StateHandler: State-specific handlers for FSM transitions
"""

from .constants import (
    ConversationState,
    SlotSource,
    FSM_STATE_TTL,
    IDEMPOTENCY_TTL,
    SLOT_STALENESS_THRESHOLD,
    VALID_TRANSITIONS,
)
from .models import (
    SlotEvidence,
    FSMState,
    IdempotencyRecord,
)
from .redis_client import (
    RedisClient,
    redis_client,
)
from .manager import (
    FSMManager,
    InvalidTransitionError,
)
from .slot_manager import (
    SlotManager,
)
from .intent_router import (
    Intent,
    IntentRouter,
)
from .state_handlers import (
    StateHandler,
)

__all__ = [
    # Enums
    "ConversationState",
    "SlotSource",
    # Models
    "SlotEvidence",
    "FSMState",
    "IdempotencyRecord",
    # Constants
    "FSM_STATE_TTL",
    "IDEMPOTENCY_TTL",
    "SLOT_STALENESS_THRESHOLD",
    "VALID_TRANSITIONS",
    # Redis
    "RedisClient",
    "redis_client",
    # Manager
    "FSMManager",
    "InvalidTransitionError",
    # Slot Management
    "SlotManager",
    # Intent & State Handling
    "Intent",
    "IntentRouter",
    "StateHandler",
]
