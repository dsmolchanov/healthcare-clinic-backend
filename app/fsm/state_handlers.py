"""
State Handlers Module

Provides state-specific handlers for each FSM conversation state.
Each handler processes user messages, extracts/validates slots, and determines
the next state transition based on intent and slot validation results.

Key Features:
- 7 state handlers for all FSM states
- Auto-escalation to FAILED state after 3 consecutive failures
- Slot extraction and validation integration
- Context-aware response generation
- Immutable state updates using deep copy

State Flow:
    GREETING → COLLECTING_SLOTS → AWAITING_CONFIRMATION → BOOKING → COMPLETED
    Alternative paths: AWAITING_CLARIFICATION, DISAMBIGUATING, FAILED
"""

from typing import Tuple
import logging

from .models import FSMState, ConversationState
from .intent_router import Intent, IntentRouter
from .slot_manager import SlotManager
from .manager import FSMManager
from .constants import SlotSource
from .metrics import record_escalation
from .logger import log_auto_escalation

logger = logging.getLogger(__name__)


class StateHandler:
    """
    Handles state-specific logic and transitions.

    Coordinates FSM transitions, intent detection, and slot management
    for each conversation state. All handlers return a tuple of
    (new_state, response_message).

    Usage:
        >>> handler = StateHandler(fsm_manager, intent_router, slot_manager)
        >>> state, response = await handler.handle_greeting(
        ...     state, "записаться к доктору", Intent.BOOKING_INTENT
        ... )
        >>> print(state.current_state)  # ConversationState.COLLECTING_SLOTS
        >>> print(response)  # "Хорошо! К какому доктору вы хотите записаться?"
    """

    def __init__(
        self,
        fsm_manager: FSMManager,
        intent_router: IntentRouter,
        slot_manager: SlotManager
    ):
        """
        Initialize StateHandler with dependencies.

        Args:
            fsm_manager: FSM orchestration manager
            intent_router: Intent detection router
            slot_manager: Slot validation and evidence tracking
        """
        self.fsm = fsm_manager
        self.intent = intent_router
        self.slots = slot_manager

    async def handle_greeting(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle GREETING state.

        Transitions to COLLECTING_SLOTS on booking intent or information.
        Returns greeting response if user just says hello.

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)

        Example:
            >>> state, response = await handler.handle_greeting(
            ...     state, "записаться", Intent.BOOKING_INTENT
            ... )
            >>> print(state.current_state)  # ConversationState.COLLECTING_SLOTS
        """
        if intent == Intent.BOOKING_INTENT or intent == Intent.INFORMATION:
            # User wants to book
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.COLLECTING_SLOTS
            )
            response = "Хорошо! К какому доктору вы хотите записаться?"
            logger.info(f"Conversation {state.conversation_id}: GREETING → COLLECTING_SLOTS")
            return new_state, response
        elif intent == Intent.GREETING:
            # Just greeting, ask what they want
            response = "Здравствуйте! Чем могу помочь? Хотите записаться на приём?"
            logger.debug(f"Conversation {state.conversation_id}: Greeting acknowledged")
            return state, response
        else:
            # Unclear, ask for clarification
            response = "Извините, я не понял. Вы хотите записаться на приём к доктору?"
            logger.debug(f"Conversation {state.conversation_id}: Unclear greeting intent")
            return state, response

    async def handle_collecting_slots(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle COLLECTING_SLOTS state.

        Extracts slots from message, validates them, and transitions to
        AWAITING_CONFIRMATION when all required slots are collected.
        Transitions to AWAITING_CLARIFICATION if validation fails.

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)

        Example:
            >>> state, response = await handler.handle_collecting_slots(
            ...     state, "к доктору Иванову", Intent.INFORMATION
            ... )
        """
        # Extract doctor name from message
        doctor_name = self.slots.extract_doctor_name(message)

        if doctor_name:
            # Validate doctor name
            is_valid, error = await self.slots.validate_doctor_name(
                doctor_name,
                state.clinic_id
            )

            if is_valid:
                # Add validated doctor slot
                state = self.slots.add_slot(
                    state,
                    "doctor",
                    doctor_name,
                    SlotSource.LLM_EXTRACT,
                    confidence=0.9
                )
                logger.info(f"Conversation {state.conversation_id}: Extracted doctor={doctor_name}")
            else:
                # Invalid doctor, ask for clarification
                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.AWAITING_CLARIFICATION
                )
                logger.warning(
                    f"Conversation {state.conversation_id}: Invalid doctor '{doctor_name}'"
                )
                return new_state, error

        # TODO: Extract date and time slots (requires LLM integration)
        # For now, we'll use placeholder logic

        # Check if all required slots present
        required_slots = ["doctor", "date", "time"]
        if self.slots.has_required_slots(state, required_slots):
            # All slots collected, ask for confirmation
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.AWAITING_CONFIRMATION
            )
            doctor = state.slots["doctor"].value
            date = state.slots["date"].value
            time = state.slots["time"].value
            response = f"Подтверждаете запись к доктору {doctor} на {date} в {time}?"
            logger.info(
                f"Conversation {state.conversation_id}: All slots collected, "
                f"requesting confirmation"
            )
            return new_state, response
        else:
            # Still collecting, ask for next missing slot
            if "doctor" not in state.slots:
                response = "К какому доктору вы хотите записаться?"
            elif "date" not in state.slots:
                response = "На какую дату?"
            elif "time" not in state.slots:
                response = "На какое время?"
            else:
                response = "Пожалуйста, уточните детали записи."

            logger.debug(
                f"Conversation {state.conversation_id}: "
                f"Missing slots: {set(required_slots) - set(state.slots.keys())}"
            )
            return state, response

    async def handle_awaiting_confirmation(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle AWAITING_CONFIRMATION state.

        Processes user confirmation/denial of booking details.
        - CONFIRM → BOOKING (with all slots marked as confirmed)
        - DENY → COLLECTING_SLOTS (to modify details)
        - DISAMBIGUATE → DISAMBIGUATING (unclear response)

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)

        Example:
            >>> state, response = await handler.handle_awaiting_confirmation(
            ...     state, "да", Intent.CONFIRM
            ... )
            >>> print(state.current_state)  # ConversationState.BOOKING
        """
        if intent == Intent.CONFIRM:
            # User confirmed, proceed to booking
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.BOOKING
            )

            # Mark all slots as confirmed
            for slot_name in new_state.slots:
                new_state = self.slots.confirm_slot(new_state, slot_name)

            logger.info(
                f"Conversation {state.conversation_id}: Booking confirmed, "
                f"transitioning to BOOKING"
            )
            return new_state, "processing_booking"  # Trigger actual booking

        elif intent == Intent.DENY:
            # User denied, go back to collecting
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.COLLECTING_SLOTS
            )
            response = "Хорошо, давайте изменим. Что вы хотите изменить?"
            logger.info(
                f"Conversation {state.conversation_id}: Booking denied, "
                f"returning to COLLECTING_SLOTS"
            )
            return new_state, response

        elif intent == Intent.DISAMBIGUATE:
            # Unclear response, ask for clarification
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.DISAMBIGUATING
            )
            response = "Извините, я не понял. Вы подтверждаете запись? Ответьте 'да' или 'нет'."
            logger.debug(
                f"Conversation {state.conversation_id}: Ambiguous confirmation, "
                f"transitioning to DISAMBIGUATING"
            )
            return new_state, response

        else:
            # Unexpected intent
            response = "Пожалуйста, подтвердите запись ('да') или отмените ('нет')."
            logger.debug(
                f"Conversation {state.conversation_id}: Unexpected intent '{intent}' "
                f"in AWAITING_CONFIRMATION"
            )
            return state, response

    async def handle_disambiguating(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle DISAMBIGUATING state.

        Attempts to clarify ambiguous user responses.
        - Clear CONFIRM → back to AWAITING_CONFIRMATION (re-handles confirmation)
        - Clear DENY → COLLECTING_SLOTS
        - Still unclear → increment failure_count, auto-escalate after 3 failures

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)

        Example:
            >>> # After 3 failures
            >>> state.failure_count = 2
            >>> state, response = await handler.handle_disambiguating(
            ...     state, "???", Intent.DISAMBIGUATE
            ... )
            >>> print(state.current_state)  # ConversationState.FAILED
        """
        if intent == Intent.CONFIRM:
            # Clear confirmation, go back to awaiting_confirmation handler
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.AWAITING_CONFIRMATION
            )
            # Re-handle with confirmation
            logger.info(
                f"Conversation {state.conversation_id}: Clear confirmation received, "
                f"re-handling in AWAITING_CONFIRMATION"
            )
            return await self.handle_awaiting_confirmation(
                new_state,
                message,
                Intent.CONFIRM
            )

        elif intent == Intent.DENY:
            # Clear denial, go to collecting_slots
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.COLLECTING_SLOTS
            )
            response = "Хорошо, что вы хотите изменить?"
            logger.info(
                f"Conversation {state.conversation_id}: Clear denial received, "
                f"transitioning to COLLECTING_SLOTS"
            )
            return new_state, response

        else:
            # Still unclear, increment failure count
            state = await self.fsm.increment_failure(state)

            logger.warning(
                f"Conversation {state.conversation_id}: Disambiguation failed, "
                f"failure_count={state.failure_count}"
            )

            if state.failure_count >= 3:
                # Auto-escalate to human - record metrics
                record_escalation("max_failures", state.clinic_id)
                log_auto_escalation(
                    conversation_id=state.conversation_id,
                    clinic_id=state.clinic_id,
                    state=state.current_state.value,
                    failure_count=state.failure_count,
                    reason="max_failures_reached"
                )

                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.FAILED
                )
                response = "Давайте я соединю вас с нашим администратором для помощи."
                logger.error(
                    f"Conversation {state.conversation_id}: Auto-escalation triggered "
                    f"after {state.failure_count} failures"
                )
                return new_state, response
            else:
                response = "Пожалуйста, ответьте просто 'да' или 'нет'."
                return state, response

    async def handle_awaiting_clarification(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle AWAITING_CLARIFICATION state.

        Processes user's correction when a slot value was invalid.
        Re-validates the corrected value and transitions accordingly.

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)

        Example:
            >>> # User provides corrected doctor name
            >>> state, response = await handler.handle_awaiting_clarification(
            ...     state, "к доктору Петрову", Intent.INFORMATION
            ... )
        """
        # Extract corrected doctor name
        doctor_name = self.slots.extract_doctor_name(message)

        if doctor_name:
            # Validate corrected doctor name
            is_valid, error = await self.slots.validate_doctor_name(
                doctor_name,
                state.clinic_id
            )

            if is_valid:
                # Add validated doctor slot
                state = self.slots.add_slot(
                    state,
                    "doctor",
                    doctor_name,
                    SlotSource.LLM_EXTRACT,
                    confidence=0.9
                )

                # Transition back to collecting slots
                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.COLLECTING_SLOTS
                )
                response = "Отлично! На какую дату вы хотите записаться?"
                logger.info(
                    f"Conversation {state.conversation_id}: Clarification successful, "
                    f"doctor={doctor_name}"
                )
                return new_state, response
            else:
                # Still invalid, increment failure
                state = await self.fsm.increment_failure(state)

                if state.failure_count >= 3:
                    # Auto-escalate
                    new_state = await self.fsm.transition_state(
                        state,
                        ConversationState.FAILED
                    )
                    response = "Давайте я соединю вас с нашим администратором для помощи."
                    logger.error(
                        f"Conversation {state.conversation_id}: Auto-escalation after "
                        f"{state.failure_count} clarification failures"
                    )
                    return new_state, response
                else:
                    logger.warning(
                        f"Conversation {state.conversation_id}: Invalid doctor name "
                        f"after clarification, failure_count={state.failure_count}"
                    )
                    return state, error
        else:
            # No doctor name extracted, increment failure
            state = await self.fsm.increment_failure(state)

            if state.failure_count >= 3:
                # Auto-escalate - record metrics
                record_escalation("clarification_failures", state.clinic_id)
                log_auto_escalation(
                    conversation_id=state.conversation_id,
                    clinic_id=state.clinic_id,
                    state=state.current_state.value,
                    failure_count=state.failure_count,
                    reason="clarification_failures"
                )

                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.FAILED
                )
                response = "Давайте я соединю вас с нашим администратором для помощи."
                logger.error(
                    f"Conversation {state.conversation_id}: Auto-escalation after "
                    f"{state.failure_count} clarification failures"
                )
                return new_state, response
            else:
                response = "Пожалуйста, уточните имя доктора."
                logger.debug(
                    f"Conversation {state.conversation_id}: No doctor name in message"
                )
                return state, response

    async def handle_booking(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle BOOKING state.

        Placeholder for actual booking logic. In production, this would:
        - Call external booking API
        - Create appointment in database
        - Send confirmation email/SMS

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)

        Example:
            >>> state, response = await handler.handle_booking(
            ...     state, "", ""
            ... )
            >>> print(state.current_state)  # ConversationState.COMPLETED
        """
        # TODO: Implement actual booking logic
        # For now, immediately transition to COMPLETED

        new_state = await self.fsm.transition_state(
            state,
            ConversationState.COMPLETED
        )

        # Reset failure count on successful booking
        new_state = await self.fsm.reset_failure(new_state)

        doctor = state.slots["doctor"].value
        date = state.slots["date"].value
        time = state.slots["time"].value

        response = (
            f"Ваша запись к доктору {doctor} на {date} в {time} подтверждена! "
            f"Ждём вас в клинике."
        )

        logger.info(
            f"Conversation {state.conversation_id}: Booking completed successfully"
        )

        return new_state, response

    async def handle_completed(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle COMPLETED state (terminal).

        Terminal state - booking is complete. No further transitions.

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (state, response_message) - state unchanged

        Example:
            >>> state, response = await handler.handle_completed(
            ...     state, "спасибо", Intent.ACKNOWLEDGMENT
            ... )
            >>> print(state.current_state)  # ConversationState.COMPLETED (unchanged)
        """
        response = (
            "Ваша запись уже подтверждена. "
            "Если хотите изменить запись, обратитесь к администратору."
        )

        logger.debug(
            f"Conversation {state.conversation_id}: Message received in COMPLETED state"
        )

        return state, response

    async def handle_failed(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle FAILED state (terminal).

        Terminal state - conversation failed, escalated to human.
        No further transitions.

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (state, response_message) - state unchanged

        Example:
            >>> state, response = await handler.handle_failed(
            ...     state, "помогите", Intent.INFORMATION
            ... )
            >>> print(state.current_state)  # ConversationState.FAILED (unchanged)
        """
        response = (
            "Ваш запрос передан администратору. "
            "Мы свяжемся с вами в ближайшее время."
        )

        logger.debug(
            f"Conversation {state.conversation_id}: Message received in FAILED state"
        )

        return state, response
