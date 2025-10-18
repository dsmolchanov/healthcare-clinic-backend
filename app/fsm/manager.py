"""
FSM Manager - Core orchestration for FSM state management.

This module provides the FSMManager class that orchestrates all FSM operations:
- State loading/saving with optimistic locking (CAS)
- State transition validation against VALID_TRANSITIONS
- Idempotency checking for webhook deduplication
- Exponential backoff retry logic for CAS conflicts
- Failure tracking for error handling

The FSMManager is the primary interface for FSM operations and coordinates
between Redis storage, Pydantic models, and business logic.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from .constants import VALID_TRANSITIONS, FSM_STATE_TTL, IDEMPOTENCY_TTL
from .models import FSMState, ConversationState, IdempotencyRecord
from .redis_client import redis_client
from .metrics import (
    record_state_transition,
    record_race_condition,
    record_duplicate_message
)
from .logger import (
    log_state_transition,
    log_race_condition,
    log_duplicate_message
)

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    """
    Raised when an invalid state transition is attempted.

    This exception indicates that a state transition violates the
    VALID_TRANSITIONS rules defined in constants.py.

    Example:
        >>> # COMPLETED is a terminal state
        >>> raise InvalidTransitionError("Invalid transition: completed -> greeting")
    """
    pass


class FSMManager:
    """
    Core FSM orchestration layer.

    Manages conversation state lifecycle including:
    - Loading state from Redis (with new state creation)
    - Saving state with CAS-based optimistic locking
    - Validating state transitions against allowed rules
    - Idempotency checking to prevent duplicate processing
    - Failure tracking for retry logic

    All state updates use deep copy to ensure immutability and prevent
    accidental mutations. CAS retry logic uses exponential backoff to
    handle concurrent updates gracefully.

    Usage:
        manager = FSMManager()

        # Load or create state
        state = await manager.load_state("conv_123", "clinic_001")

        # Transition state
        state = await manager.transition_state(state, ConversationState.COLLECTING_SLOTS)

        # Save with CAS retry
        success = await manager.save_state(state)

        # Check idempotency
        cached = await manager.check_idempotency("msg_123")
        if cached:
            return cached  # Already processed
    """

    def __init__(self):
        """
        Initialize FSM manager with Redis client dependency.

        The redis_client singleton instance is used for all Redis operations.
        Ensure redis_client.connect() has been called before using FSMManager.
        """
        self.redis = redis_client

    async def load_state(
        self,
        conversation_id: str,
        clinic_id: str
    ) -> FSMState:
        """
        Load FSM state from Redis or create new state.

        If the conversation_id does not exist in Redis, creates a new FSMState
        with initial state GREETING and version 0. This allows seamless handling
        of new conversations without explicit state creation.

        Args:
            conversation_id: Unique conversation identifier (e.g., WhatsApp session ID)
            clinic_id: Clinic/organization identifier

        Returns:
            FSMState object loaded from Redis or newly created

        Raises:
            RuntimeError: If Redis client not connected
            pydantic.ValidationError: If stored data fails validation

        Example:
            >>> state = await manager.load_state("whatsapp:+1555123456:sess1", "clinic_001")
            >>> print(state.current_state)
            ConversationState.GREETING
            >>> print(state.version)
            0
        """
        key = f"fsm:state:{conversation_id}"

        try:
            data = await self.redis.get(key)

            if data:
                # Parse existing state from Redis
                state = FSMState.model_validate_json(data)
                logger.debug(
                    f"Loaded FSM state for conversation {conversation_id}: "
                    f"state={state.current_state}, version={state.version}"
                )
                return state
            else:
                # Create new conversation state
                now = datetime.now(timezone.utc)
                state = FSMState(
                    conversation_id=conversation_id,
                    clinic_id=clinic_id,
                    current_state=ConversationState.GREETING,
                    version=0,
                    created_at=now,
                    updated_at=now
                )
                logger.info(
                    f"Created new FSM state for conversation {conversation_id} "
                    f"in clinic {clinic_id}"
                )
                return state

        except Exception as e:
            logger.error(
                f"Failed to load state for conversation {conversation_id}: {e}"
            )
            raise

    async def save_state(
        self,
        state: FSMState,
        max_retries: int = 3
    ) -> bool:
        """
        Save FSM state with CAS retry logic.

        Uses Compare-And-Set (CAS) with optimistic locking to ensure atomic
        state updates even under concurrent modifications. If a version conflict
        occurs, retries with exponential backoff up to max_retries times.

        Exponential backoff formula: 0.1 * (2 ^ attempt) seconds
        - Attempt 0: 0.1s
        - Attempt 1: 0.2s
        - Attempt 2: 0.4s

        Each attempt reloads the current state from Redis to get the latest
        version before retrying.

        Args:
            state: FSM state to save
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            True if state was saved successfully
            False if all retry attempts failed (version conflicts)

        Raises:
            RuntimeError: If Redis client not connected
            redis.RedisError: If Redis operation fails

        Example:
            >>> state = await manager.load_state("conv_123", "clinic_001")
            >>> state = await manager.transition_state(state, ConversationState.COLLECTING_SLOTS)
            >>> success = await manager.save_state(state, max_retries=3)
            >>> if success:
            ...     print("State saved successfully")
            ... else:
            ...     print("Failed after retries - concurrent update conflict")
        """
        key = f"fsm:state:{state.conversation_id}"

        for attempt in range(max_retries):
            try:
                # Create updated state with incremented version
                new_state = state.model_copy(deep=True)
                new_state.version += 1
                new_state.updated_at = datetime.now(timezone.utc)

                # Attempt CAS operation
                success = await self.redis.cas_set(
                    key=key,
                    expected_version=state.version,
                    new_value=new_state.model_dump_json(),
                    ttl=FSM_STATE_TTL
                )

                if success:
                    logger.debug(
                        f"Saved FSM state for conversation {state.conversation_id}: "
                        f"version {state.version} -> {new_state.version}, "
                        f"state={new_state.current_state}"
                    )
                    return True

                # Version conflict detected - record metrics
                record_race_condition(state.clinic_id)
                log_race_condition(
                    conversation_id=state.conversation_id,
                    clinic_id=state.clinic_id,
                    state=state.current_state.value,
                    expected_version=state.version,
                    retry_count=attempt + 1
                )

                logger.warning(
                    f"CAS conflict for conversation {state.conversation_id} "
                    f"(attempt {attempt + 1}/{max_retries}): "
                    f"expected version {state.version}"
                )

                # Exponential backoff before retry
                backoff_time = 0.1 * (2 ** attempt)
                await asyncio.sleep(backoff_time)

                # Reload current state for next attempt
                state = await self.load_state(
                    state.conversation_id,
                    state.clinic_id
                )

            except Exception as e:
                logger.error(
                    f"Error saving state for conversation {state.conversation_id} "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                # Continue to retry on errors (up to max_retries)
                if attempt == max_retries - 1:
                    raise

        # Failed after all retries
        logger.error(
            f"Failed to save state for conversation {state.conversation_id} "
            f"after {max_retries} attempts"
        )
        return False

    def validate_transition(
        self,
        from_state: ConversationState,
        to_state: ConversationState
    ) -> None:
        """
        Validate that a state transition is allowed.

        Checks the transition against VALID_TRANSITIONS rules. Terminal states
        (COMPLETED, FAILED) cannot transition to any other state.

        Args:
            from_state: Current conversation state
            to_state: Target conversation state

        Raises:
            InvalidTransitionError: If transition is not allowed

        Example:
            >>> # Valid transition
            >>> manager.validate_transition(
            ...     ConversationState.GREETING,
            ...     ConversationState.COLLECTING_SLOTS
            ... )  # No error

            >>> # Invalid transition (terminal state)
            >>> manager.validate_transition(
            ...     ConversationState.COMPLETED,
            ...     ConversationState.GREETING
            ... )
            InvalidTransitionError: Invalid transition: completed -> greeting
        """
        valid_targets = VALID_TRANSITIONS.get(from_state, [])

        if to_state not in valid_targets:
            raise InvalidTransitionError(
                f"Invalid transition: {from_state} -> {to_state}"
            )

    async def transition_state(
        self,
        state: FSMState,
        new_state: ConversationState,
        intent: str = ""
    ) -> FSMState:
        """
        Transition to new state with validation and metrics.

        Validates the transition against VALID_TRANSITIONS before creating
        the updated state. Returns a new FSMState object with the updated
        current_state - does NOT mutate the input state or save to Redis.

        Call save_state() after transition_state() to persist the change.

        Args:
            state: Current FSM state
            new_state: Target conversation state
            intent: Intent that triggered this transition (for logging)

        Returns:
            New FSMState object with updated current_state (not yet saved)

        Raises:
            InvalidTransitionError: If transition is not allowed

        Example:
            >>> state = await manager.load_state("conv_123", "clinic_001")
            >>> # Validate and create updated state
            >>> state = await manager.transition_state(
            ...     state,
            ...     ConversationState.COLLECTING_SLOTS,
            ...     intent="booking_intent"
            ... )
            >>> # Now save the updated state
            >>> await manager.save_state(state)
        """
        start_time = time.time()

        # Validate transition before making changes
        self.validate_transition(state.current_state, new_state)

        # Create updated state with deep copy (immutability)
        updated_state = state.model_copy(deep=True)
        updated_state.current_state = new_state

        # Calculate duration
        duration_seconds = time.time() - start_time

        # Record metrics
        record_state_transition(
            from_state=state.current_state.value,
            to_state=new_state.value,
            clinic_id=state.clinic_id,
            duration_seconds=duration_seconds
        )

        # Log transition
        log_state_transition(
            conversation_id=state.conversation_id,
            clinic_id=state.clinic_id,
            from_state=state.current_state.value,
            to_state=new_state.value,
            intent=intent,
            duration_ms=duration_seconds * 1000
        )

        logger.debug(
            f"Transitioned conversation {state.conversation_id}: "
            f"{state.current_state} -> {new_state}"
        )

        return updated_state

    async def check_idempotency(
        self,
        message_sid: str
    ) -> Optional[str]:
        """
        Check if message has already been processed.

        Used for webhook deduplication to prevent duplicate message processing.
        If the message_sid exists in Redis, returns the cached response.

        Args:
            message_sid: Unique message identifier (e.g., Twilio message SID)

        Returns:
            Cached response string if message was already processed
            None if message is new (not yet processed)

        Raises:
            RuntimeError: If Redis client not connected
            pydantic.ValidationError: If cached data fails validation

        Example:
            >>> # Check for duplicate
            >>> cached = await manager.check_idempotency("SM1234567890abcdef")
            >>> if cached:
            ...     return cached  # Return cached response immediately
            >>>
            >>> # Process message normally
            >>> response = await process_message(message)
            >>> await manager.cache_response("SM1234567890abcdef", response)
        """
        key = f"fsm:idempotency:{message_sid}"

        try:
            data = await self.redis.get(key)

            if data:
                record = IdempotencyRecord.model_validate_json(data)

                # Record duplicate message metrics
                record_duplicate_message(clinic_id="unknown")  # Clinic ID not available here
                log_duplicate_message(
                    conversation_id="unknown",  # Conversation ID not available from message_sid alone
                    clinic_id="unknown",
                    message_sid=message_sid,
                    cached_response=record.response
                )

                logger.debug(
                    f"Idempotency cache hit for message {message_sid}: "
                    f"processed at {record.processed_at}"
                )
                return record.response

            logger.debug(f"Idempotency cache miss for message {message_sid}")
            return None

        except Exception as e:
            logger.error(
                f"Failed to check idempotency for message {message_sid}: {e}"
            )
            # Don't raise - allow processing to continue even if cache check fails
            return None

    async def cache_response(
        self,
        message_sid: str,
        response: str
    ) -> None:
        """
        Cache response for idempotency checking.

        Stores the response in Redis with IDEMPOTENCY_TTL expiration to enable
        deduplication of webhook retries. If a webhook is redelivered within
        the TTL window, check_idempotency() will return this cached response.

        Args:
            message_sid: Unique message identifier
            response: Response text to cache

        Raises:
            RuntimeError: If Redis client not connected

        Example:
            >>> response = await process_webhook(message)
            >>> await manager.cache_response(message_sid, response)
            >>> # Future retries of same message_sid will return cached response
        """
        key = f"fsm:idempotency:{message_sid}"

        try:
            record = IdempotencyRecord(
                message_sid=message_sid,
                response=response,
                processed_at=datetime.now(timezone.utc)
            )

            await self.redis.set(
                key,
                record.model_dump_json(),
                ttl=IDEMPOTENCY_TTL
            )

            logger.debug(
                f"Cached response for message {message_sid} "
                f"(TTL: {IDEMPOTENCY_TTL}s)"
            )

        except Exception as e:
            logger.error(
                f"Failed to cache response for message {message_sid}: {e}"
            )
            # Don't raise - cache failure shouldn't block response

    async def increment_failure(
        self,
        state: FSMState
    ) -> FSMState:
        """
        Increment failure counter for error tracking.

        Creates a new FSMState with incremented failure_count. Does NOT
        save to Redis - call save_state() after this method.

        Used to track consecutive failures for retry logic and escalation.

        Args:
            state: Current FSM state

        Returns:
            New FSMState with incremented failure_count (not yet saved)

        Example:
            >>> try:
            ...     await book_appointment(state)
            ... except Exception as e:
            ...     state = await manager.increment_failure(state)
            ...     await manager.save_state(state)
            ...     if state.failure_count >= 3:
            ...         # Escalate to FAILED state
            ...         state = await manager.transition_state(
            ...             state,
            ...             ConversationState.FAILED
            ...         )
        """
        updated_state = state.model_copy(deep=True)
        updated_state.failure_count += 1

        logger.debug(
            f"Incremented failure count for conversation {state.conversation_id}: "
            f"{state.failure_count} -> {updated_state.failure_count}"
        )

        return updated_state

    async def reset_failure(
        self,
        state: FSMState
    ) -> FSMState:
        """
        Reset failure counter after successful operation.

        Creates a new FSMState with failure_count set to 0. Does NOT
        save to Redis - call save_state() after this method.

        Used to clear failure tracking after successful state progression.

        Args:
            state: Current FSM state

        Returns:
            New FSMState with failure_count reset to 0 (not yet saved)

        Example:
            >>> state = await manager.load_state("conv_123", "clinic_001")
            >>> # After successful booking
            >>> state = await manager.reset_failure(state)
            >>> state = await manager.transition_state(state, ConversationState.COMPLETED)
            >>> await manager.save_state(state)
        """
        updated_state = state.model_copy(deep=True)
        updated_state.failure_count = 0

        logger.debug(
            f"Reset failure count for conversation {state.conversation_id}"
        )

        return updated_state
