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
- Answer service integration for information queries

State Flow:
    GREETING → COLLECTING_SLOTS → AWAITING_CONFIRMATION → BOOKING → COMPLETED
    Alternative paths: AWAITING_CLARIFICATION, DISAMBIGUATING, FAILED
"""

from typing import Tuple
import logging
import uuid
from datetime import datetime, date, time, timedelta, timezone

from .models import FSMState, ConversationState, IntentResult
from .intent_router import Intent, IntentRouter
from .slot_manager import SlotManager
from .manager import FSMManager
from .answer_service import AnswerService
from .constants import SlotSource
from .llm_slot_extractor import LLMSlotExtractor
from .metrics import (
    record_escalation,
    record_fallback_hit,
    record_known_intent_fallback,
    record_intent_detection,
    record_response_type
)
from .logger import log_auto_escalation, log_fallback_hit, log_response_type
from .coverage import validate_coverage

# P2 Phase 2B: Import new services
from app.services.availability_service import AvailabilityService
from app.services.appointment_hold_service import AppointmentHoldService
from app.observability.langfuse_tracer import llm_observability
from app.db.supabase_client import get_supabase_client

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
        slot_manager: SlotManager,
        answer_service: AnswerService
    ):
        """
        Initialize StateHandler with dependencies.

        Args:
            fsm_manager: FSM orchestration manager
            intent_router: Intent detection router
            slot_manager: Slot validation and evidence tracking
            answer_service: Answer service for information queries
        """
        self.fsm = fsm_manager
        self.intent = intent_router
        self.slots = slot_manager
        self.answers = answer_service
        self.llm_extractor = LLMSlotExtractor()  # Hybrid FSM+LLM

        # P2 Phase 2B: Initialize availability and hold services
        self.supabase = get_supabase_client(schema='healthcare')
        self.availability_service = AvailabilityService(self.supabase)
        self.hold_service = AppointmentHoldService(self.supabase)

    async def handle_greeting(
        self,
        state: FSMState,
        message: str,
        intent: IntentResult
    ) -> Tuple[FSMState, str]:
        """
        Handle GREETING state with full intent coverage.

        Supported intents:
        - GREETING: Welcome message with booking offer
        - BOOKING_INTENT/INFORMATION: Transition to COLLECTING_SLOTS
        - TOPIC_CHANGE: Answer query via AnswerService
        - DENY: Show help menu
        - CONFIRM: Context-aware confirmation (if last_prompt set)
        - Fallback: Only for truly unknown intents

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent with topic/entities

        Returns:
            Tuple of (new_state, response_message)
        """
        # Record intent detection (Task #74)
        record_intent_detection(
            state=state.current_state.value,
            intent=intent.label,
            topic=intent.topic if hasattr(intent, 'topic') else None,
            clinic_id=state.clinic_id
        )

        # 1) Handle TOPIC_CHANGE (pricing, hours, address, etc.)
        if intent.label == Intent.TOPIC_CHANGE:
            logger.info(
                f"[Greeting] TOPIC_CHANGE detected: topic={intent.topic}, "
                f"entities={intent.entities}"
            )

            # Store inquiry context for follow-up handling
            state.set_inquiry_context(intent.topic, intent.entities)

            # Route to appropriate AnswerService method
            if intent.topic == "pricing":
                response = await self.answers.answer_pricing(
                    state.clinic_id,
                    intent.entities
                )
            elif intent.topic == "hours":
                response = await self.answers.answer_hours(state.clinic_id)
            elif intent.topic == "address":
                response = await self.answers.answer_address(state.clinic_id)
            elif intent.topic == "phone":
                response = await self.answers.answer_phone(state.clinic_id)
            elif intent.topic == "services":
                response = await self.answers.answer_services(state.clinic_id)
            else:
                # Unclassified topic
                response = await self.answers.answer_general(
                    state.clinic_id,
                    message
                )

            # Set last_prompt for follow-up YES/NO
            state.set_last_prompt(
                kind="yes_no",
                question="booking_after_info",
                context={
                    "topic": intent.topic,
                    "entities": intent.entities
                }
            )

            logger.info(
                f"[Greeting] Answered {intent.topic} query, "
                f"set prompt context for follow-up"
            )

            # Record response type: template (from AnswerService)
            record_response_type("template", state.current_state.value, state.clinic_id)

            return state, response

        # 2) Handle DENY (user says "no" to implicit booking offer)
        elif intent.label == Intent.DENY:
            logger.info("[Greeting] DENY detected - showing help menu")

            # Clear any previous prompt context
            state.clear_last_prompt()

            response = (
                "Понятно. С чем могу помочь?\n\n"
                "• Узнать цены на услуги\n"
                "• Адрес и график работы\n"
                "• Записаться на приём\n\n"
                "Просто напишите, что вас интересует."
            )

            return state, response

        # 3) Handle CONFIRM (context-aware)
        elif intent.label == Intent.CONFIRM:
            prompt_context = state.get_prompt_question()

            if prompt_context == "booking_offer":
                # User confirmed they want to book
                logger.info("[Greeting] CONFIRM → booking_offer, transitioning to COLLECTING_SLOTS")

                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.COLLECTING_SLOTS,
                    intent="booking_intent"
                )

                state.clear_last_prompt()

                return new_state, "Отлично! К какому доктору хотите записаться?"

            elif prompt_context == "booking_after_info":
                # User confirmed after getting info (pricing, hours, etc.)
                logger.info(
                    f"[Greeting] CONFIRM → booking_after_info "
                    f"(after {state.inquiry_topic} query)"
                )

                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.COLLECTING_SLOTS,
                    intent="booking_intent"
                )

                state.clear_last_prompt()
                state.clear_inquiry_context()

                return new_state, "Отлично! К какому доктору хотите записаться?"

            else:
                # Confirmation without clear context
                logger.warning(
                    f"[Greeting] CONFIRM without clear context: "
                    f"prompt_context={prompt_context}"
                )

                response = "На что вы ответили 'да'? Уточните, пожалуйста."
                return state, response

        # 4) Handle BOOKING_INTENT or INFORMATION (existing logic)
        elif intent.label == Intent.BOOKING_INTENT or intent.label == Intent.INFORMATION:
            logger.info("[Greeting] BOOKING_INTENT detected, transitioning to COLLECTING_SLOTS")

            new_state = await self.fsm.transition_state(
                state,
                ConversationState.COLLECTING_SLOTS,
                intent="booking_intent"
            )

            response = "Хорошо! К какому доктору вы хотите записаться?"
            return new_state, response

        # 5) Handle GREETING (existing logic with prompt tracking)
        elif intent.label == Intent.GREETING:
            logger.debug("[Greeting] Greeting acknowledged, offering help")

            # Set last_prompt so follow-up YES/NO is understood
            state.set_last_prompt(
                kind="yes_no",
                question="booking_offer",
                text="Хотите записаться на приём?"
            )

            response = "Здравствуйте! Чем могу помочь? Хотите записаться на приём?"
            return state, response

        # 6) Fallback (ONLY for truly unknown intents)
        else:
            logger.warning(
                f"[Greeting] Unknown intent: {intent.label}, "
                f"falling back to help menu"
            )

            # Record fallback metrics (Task #74)
            record_fallback_hit(
                state=state.current_state.value,
                intent=intent.label,
                clinic_id=state.clinic_id
            )

            # Check if this was a KNOWN intent that fell to fallback (CRITICAL)
            if validate_coverage(state.current_state, intent.label):
                record_known_intent_fallback(
                    state=state.current_state.value,
                    intent=intent.label,
                    clinic_id=state.clinic_id
                )
                # Structured logging
                log_fallback_hit(
                    conversation_id=state.conversation_id,
                    clinic_id=state.clinic_id,
                    state=state.current_state.value,
                    intent=intent.label,
                    is_known_intent=True
                )
            else:
                # Unknown intent fallback (acceptable)
                log_fallback_hit(
                    conversation_id=state.conversation_id,
                    clinic_id=state.clinic_id,
                    state=state.current_state.value,
                    intent=intent.label,
                    is_known_intent=False
                )

            # Response type: fallback
            record_response_type("fallback", state.current_state.value, state.clinic_id)

            # Even unknown intents get a helpful menu, not "didn't understand"
            response = (
                "Могу помочь с:\n"
                "• Ценами на услуги\n"
                "• Графиком работы и адресом\n"
                "• Записью на приём\n\n"
                "Что вас интересует?"
            )

            return state, response

    async def handle_collecting_slots(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        Handle COLLECTING_SLOTS state with Hybrid FSM+LLM approach.

        Uses LLM to extract slots intelligently, then validates them using
        existing validation logic. The FSM controls flow, LLM handles NLU.

        Args:
            state: Current FSM state
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)

        Example:
            >>> state, response = await handler.handle_collecting_slots(
            ...     state, "запишите меня к терапевту на завтра в 14:00", Intent.INFORMATION
            ... )
            >>> # LLM extracts all three slots: doctor="терапевт", date="завтра", time="14:00"
        """
        required_slots = ["doctor", "date", "time"]

        # 1. Determine which slots are still missing
        missing_slots = [s for s in required_slots if s not in state.slots]

        logger.info(
            f"Conversation {state.conversation_id}: "
            f"Collecting slots. Missing: {missing_slots}, Message: '{message}'"
        )

        # 2. Use LLM to extract ALL missing slots from user's message
        # This is the HYBRID part - smart extraction instead of brittle regex
        # P2 Phase 2B: Wrap LLM call with Langfuse tracing
        if missing_slots:
            llm_extracted = await llm_observability.extract_slots_with_tracing(
                message=message,
                missing_slots=missing_slots,
                clinic_id=state.clinic_id,
                session_id=state.conversation_id,
                fsm_state=state.current_state.value,
                llm_extractor=self.llm_extractor
            )

            logger.info(
                f"Conversation {state.conversation_id}: "
                f"LLM extracted {len(llm_extracted)} slots: {list(llm_extracted.keys())}"
            )

            # 3. Validate and add each extracted slot
            for slot_name, slot_data in llm_extracted.items():
                slot_value = slot_data["value"]
                confidence = slot_data.get("confidence", 0.8)

                if slot_name == "doctor":
                    # Validate doctor (handle both names and specialties)
                    doctor_value = slot_value
                    slot_type = slot_data.get("type", "name")

                    # Try validation
                    is_valid, error_msg, doctor_id = await self.slots.validate_doctor_name(
                        doctor_value,
                        state.clinic_id
                    )

                    if is_valid and doctor_id:
                        # Add validated doctor slot with UUID
                        state = self.slots.add_slot(
                            state,
                            "doctor",
                            doctor_id,  # Store UUID instead of name
                            SlotSource.LLM_EXTRACT,
                            confidence=confidence
                        )
                        # Also store the name for display
                        state = self.slots.add_slot(
                            state,
                            "doctor_name",
                            doctor_value,
                            SlotSource.LLM_EXTRACT,
                            confidence=confidence
                        )
                        logger.info(
                            f"Conversation {state.conversation_id}: "
                            f"✅ Validated doctor={doctor_value} (id={doctor_id}, type={slot_type})"
                        )
                    else:
                        # Invalid doctor - return error and ask for clarification
                        logger.warning(
                            f"Conversation {state.conversation_id}: "
                            f"❌ Invalid doctor '{doctor_value}'"
                        )
                        new_state = await self.fsm.transition_state(
                            state,
                            ConversationState.AWAITING_CLARIFICATION
                        )
                        return new_state, error_msg

                elif slot_name == "date":
                    # Validate date (uses clinic timezone)
                    is_valid, error_msg = await self.slots.validate_date_slot(
                        slot_value,
                        state.clinic_id
                    )

                    if is_valid:
                        state = self.slots.add_slot(
                            state,
                            "date",
                            slot_value,
                            SlotSource.LLM_EXTRACT,
                            confidence=confidence
                        )
                        logger.info(
                            f"Conversation {state.conversation_id}: "
                            f"✅ Validated date={slot_value}"
                        )
                    else:
                        # Invalid date - return error
                        logger.warning(
                            f"Conversation {state.conversation_id}: "
                            f"❌ Invalid date '{slot_value}': {error_msg}"
                        )
                        new_state = await self.fsm.transition_state(
                            state,
                            ConversationState.AWAITING_CLARIFICATION
                        )
                        return new_state, error_msg

                elif slot_name == "time":
                    # For now, accept any time format (TODO: validate against clinic hours)
                    state = self.slots.add_slot(
                        state,
                        "time",
                        slot_value,
                        SlotSource.LLM_EXTRACT,
                        confidence=confidence
                    )
                    logger.info(
                        f"Conversation {state.conversation_id}: "
                        f"✅ Extracted time={slot_value}"
                    )

        # 4. Check if all required slots are now present
        # P2 Phase 2B: Branch logic based on collected slots
        if "doctor" in state.slots and "date" in state.slots and "time" not in state.slots:
            # Path A: Doctor + Date collected → Show available slots
            doctor_id = state.slots["doctor"].value
            date_str = state.slots["date"].value
            doctor_name = state.slots.get("doctor_name", {}).value if "doctor_name" in state.slots else "доктору"

            try:
                appointment_date = date.fromisoformat(date_str)
            except ValueError:
                # Invalid date format
                logger.error(f"Invalid date format: {date_str}")
                return state, "Неверный формат даты. Пожалуйста, укажите дату снова."

            # Get clinic timezone for availability check
            clinic_timezone = await self.availability_service.get_clinic_timezone(state.clinic_id)

            # Query available slots
            logger.info(
                f"Conversation {state.conversation_id}: "
                f"Querying available slots for doctor={doctor_id}, date={date_str}"
            )

            available_slots = await self.availability_service.get_available_slots(
                clinic_id=state.clinic_id,
                doctor_id=doctor_id,
                preferred_date=appointment_date,
                clinic_timezone=clinic_timezone,
                limit=5
            )

            if not available_slots:
                # No slots available on this date
                logger.warning(
                    f"Conversation {state.conversation_id}: "
                    f"No available slots for doctor={doctor_id} on {date_str}"
                )

                # Clear date so user can choose again
                if "date" in state.slots:
                    del state.slots["date"]

                response = (
                    f"К сожалению, на {date_str} нет свободных слотов к {doctor_name}.\n"
                    f"Попробуйте другую дату."
                )

                return state, response

            # Transition to PRESENTING_SLOTS with cached slots
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.PRESENTING_SLOTS
            )

            # Store available slots in state
            new_state.available_slots = available_slots

            # Format slots for display
            slots_text = "\n".join([
                f"{i+1}. {slot['display']}"
                for i, slot in enumerate(available_slots)
            ])

            response = (
                f"Доступные слоты к {doctor_name} на {date_str}:\n\n"
                f"{slots_text}\n\n"
                f"Выберите номер слота (1-{len(available_slots)}) или напишите своё время."
            )

            logger.info(
                f"Conversation {state.conversation_id}: "
                f"Presenting {len(available_slots)} available slots"
            )

            return new_state, response

        elif self.slots.has_required_slots(state, required_slots):
            # Path B: All slots collected (existing logic)
            # All slots collected and confirmed - transition to confirmation
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.AWAITING_CONFIRMATION
            )

            doctor_name = state.slots.get("doctor_name", state.slots.get("doctor", {}).value)
            date = state.slots["date"].value
            time = state.slots["time"].value

            response = (
                f"Отлично! Давайте подтвердим запись:\n"
                f"• Доктор: {doctor_name}\n"
                f"• Дата: {date}\n"
                f"• Время: {time}\n\n"
                f"Всё верно?"
            )

            logger.info(
                f"Conversation {state.conversation_id}: "
                f"All slots collected, requesting confirmation"
            )
            return new_state, response

        # 5. Still missing slots - ask for the next one
        # This is still TEMPLATE-based (FSM controls responses), but smarter
        missing_after_extraction = [s for s in required_slots if s not in state.slots]

        if "doctor" in missing_after_extraction:
            response = "К какому доктору или специалисту вы хотите записаться?"
        elif "date" in missing_after_extraction:
            doctor_name = state.slots.get("doctor_name", {}).value if "doctor_name" in state.slots else "доктору"
            response = f"Отлично! На какую дату вы хотите записаться к {doctor_name}?"
        elif "time" in missing_after_extraction:
            date = state.slots["date"].value
            response = f"Хорошо, на {date}. А в какое время вам удобно?"
        else:
            response = "Пожалуйста, уточните детали записи."

        logger.debug(
            f"Conversation {state.conversation_id}: "
            f"Still missing: {missing_after_extraction}"
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
            # User denied - release hold and go back to collecting
            # P2 Phase 2B: Release hold if exists
            if state.hold_id:
                logger.info(
                    f"Conversation {state.conversation_id}: "
                    f"Releasing hold {state.hold_id} due to user denial"
                )

                await self.hold_service.release_hold(
                    state.hold_id,
                    reason="user_denied"
                )

                # Clear hold from state
                state.hold_id = None
                state.hold_expires_at = None

            new_state = await self.fsm.transition_state(
                state,
                ConversationState.COLLECTING_SLOTS
            )

            response = "Хорошо, давайте изменим. Что вы хотите изменить?"

            logger.info(
                f"Conversation {state.conversation_id}: "
                f"Booking denied, returning to COLLECTING_SLOTS"
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

    async def handle_presenting_slots(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        P2 Phase 2B: Handle user selection of time slot from presented options.

        Supports:
        - Numeric selection (1-5)
        - Free-form time input ("14:00")

        Creates durable hold on selected slot before confirmation.

        Args:
            state: Current FSM state with cached available_slots
            message: User's message
            intent: Detected intent

        Returns:
            Tuple of (new_state, response_message)
        """
        available_slots = state.available_slots

        if not available_slots:
            # State corrupted - available_slots should be set in COLLECTING_SLOTS
            logger.error(
                f"Conversation {state.conversation_id}: "
                f"PRESENTING_SLOTS state has no available_slots"
            )

            new_state = await self.fsm.transition_state(
                state,
                ConversationState.COLLECTING_SLOTS
            )
            return new_state, "Пожалуйста, выберите дату снова."

        # 1. Try to parse as slot number (1-5)
        try:
            slot_number = int(message.strip())
            if 1 <= slot_number <= len(available_slots):
                # Valid slot selection
                selected_slot = available_slots[slot_number - 1]
                time_str = selected_slot['time']
                start_datetime_utc_str = selected_slot['start_datetime_utc']

                logger.info(
                    f"Conversation {state.conversation_id}: "
                    f"User selected slot #{slot_number}: {time_str}"
                )

                # Add time slot to state
                state = self.slots.add_slot(
                    state,
                    "time",
                    time_str,
                    SlotSource.USER_CONFIRM,  # User explicitly selected
                    confidence=1.0
                )

                # Create hold for this slot
                return await self._create_hold_and_confirm(
                    state,
                    start_datetime_utc_str,
                    selected_slot['date'],
                    time_str
                )
            else:
                # Number out of range
                response = (
                    f"Пожалуйста, выберите номер от 1 до {len(available_slots)} "
                    f"или напишите время в формате ЧЧ:ММ."
                )
                return state, response

        except ValueError:
            # Not a number - try to extract time from free-form text
            pass

        # 2. Try to extract time from free-form text using LLM
        llm_extracted = await llm_observability.extract_slots_with_tracing(
            message=message,
            missing_slots=["time"],
            clinic_id=state.clinic_id,
            session_id=state.conversation_id,
            fsm_state=state.current_state.value,
            llm_extractor=self.llm_extractor
        )

        if "time" in llm_extracted:
            time_str = llm_extracted["time"]["value"]

            # Validate that time matches one of the available slots
            matching_slot = next(
                (slot for slot in available_slots if slot['time'] == time_str),
                None
            )

            if matching_slot:
                # Valid time - create hold
                logger.info(
                    f"Conversation {state.conversation_id}: "
                    f"User requested time {time_str} which matches available slot"
                )

                state = self.slots.add_slot(
                    state,
                    "time",
                    time_str,
                    SlotSource.LLM_EXTRACT,
                    confidence=llm_extracted["time"].get("confidence", 0.8)
                )

                return await self._create_hold_and_confirm(
                    state,
                    matching_slot['start_datetime_utc'],
                    matching_slot['date'],
                    time_str
                )
            else:
                # User requested time not in available slots
                logger.warning(
                    f"Conversation {state.conversation_id}: "
                    f"User requested unavailable time {time_str}"
                )

                response = (
                    f"К сожалению, {time_str} не доступно.\n"
                    f"Пожалуйста, выберите из предложенных вариантов (1-{len(available_slots)})."
                )
                return state, response
        else:
            # Could not extract time
            response = (
                f"Не понял. Пожалуйста, выберите номер слота (1-{len(available_slots)}) "
                f"или напишите время в формате ЧЧ:ММ."
            )
            return state, response

    async def _create_hold_and_confirm(
        self,
        state: FSMState,
        start_datetime_utc_str: str,
        date_str: str,
        time_str: str
    ) -> Tuple[FSMState, str]:
        """
        Helper method to create hold and transition to confirmation.

        Args:
            state: Current FSM state
            start_datetime_utc_str: Slot start time in UTC (ISO format)
            date_str: Appointment date (ISO format)
            time_str: Appointment time (HH:MM format)

        Returns:
            Tuple of (new_state, response_message)
        """
        # Generate booking_request_id for end-to-end idempotency
        if not state.booking_request_id:
            state.booking_request_id = str(uuid.uuid4())

        # Parse datetime
        start_datetime_utc = datetime.fromisoformat(start_datetime_utc_str)
        doctor_id = state.slots["doctor"].value
        doctor_name = state.slots.get("doctor_name", {}).value if "doctor_name" in state.slots else "доктору"

        # Get clinic timezone for hold creation
        clinic_timezone = await self.availability_service.get_clinic_timezone(state.clinic_id)

        # Attempt to create hold
        logger.info(
            f"Conversation {state.conversation_id}: "
            f"Creating hold for slot {date_str} {time_str}"
        )

        success, hold_id, response_data = await self.hold_service.create_hold(
            clinic_id=state.clinic_id,
            doctor_id=doctor_id,
            start_time=start_datetime_utc,
            duration_minutes=30,  # TODO: Make configurable or get from doctor schedule
            conversation_id=state.conversation_id,
            booking_request_id=state.booking_request_id,
            patient_phone=state.conversation_id,  # WhatsApp phone number
            clinic_timezone=clinic_timezone
        )

        if success:
            # Hold created successfully!
            state.hold_id = hold_id
            state.hold_expires_at = response_data['hold_expires_at']

            # Clear available_slots (no longer needed)
            state.available_slots = []

            # Transition to AWAITING_CONFIRMATION
            new_state = await self.fsm.transition_state(
                state,
                ConversationState.AWAITING_CONFIRMATION
            )

            # Format expiry time in clinic timezone
            expires_at = state.hold_expires_at
            if clinic_timezone != "UTC":
                from zoneinfo import ZoneInfo
                clinic_tz = ZoneInfo(clinic_timezone)
                expires_at = state.hold_expires_at.astimezone(clinic_tz)

            expires_time = expires_at.strftime("%H:%M")

            response = (
                f"✅ Забронировал для вас запись:\n"
                f"• Доктор: {doctor_name}\n"
                f"• Дата: {date_str}\n"
                f"• Время: {time_str}\n\n"
                f"⏰ Бронь действует до {expires_time} ({response_data['expires_in_minutes']} минут).\n"
                f"Подтвердите записью 'да' или измените 'нет'."
            )

            logger.info(
                f"Conversation {state.conversation_id}: "
                f"Hold created {hold_id}, expires at {state.hold_expires_at}"
            )

            return new_state, response

        else:
            # Hold failed - slot already taken or other error
            logger.warning(
                f"Conversation {state.conversation_id}: "
                f"Hold creation failed for {date_str} {time_str}"
            )

            alternatives = response_data.get('alternatives', [])

            if alternatives:
                # Update available_slots with new alternatives
                state.available_slots = alternatives

                alt_text = "\n".join([
                    f"{i+1}. {alt['display']}"
                    for i, alt in enumerate(alternatives[:3])
                ])

                response = (
                    f"❌ К сожалению, это время уже занято.\n\n"
                    f"Ближайшие доступные слоты:\n{alt_text}\n\n"
                    f"Выберите номер слота или напишите 'отмена'."
                )
            else:
                # No alternatives - return to collecting
                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.COLLECTING_SLOTS
                )

                response = (
                    f"❌ К сожалению, это время уже занято. "
                    f"Давайте выберем другую дату."
                )

                return new_state, response

            return state, response

    async def handle_booking(
        self,
        state: FSMState,
        message: str,
        intent: str
    ) -> Tuple[FSMState, str]:
        """
        P2 Phase 2B: Create appointment using atomic RPC.

        Uses `healthcare.confirm_hold_and_create_appointment()` to:
        1. Validate hold is still valid (not expired)
        2. Create appointment record
        3. Convert hold status from 'held' → 'reserved'

        All in a single atomic transaction.

        Args:
            state: Current FSM state with hold_id
            message: User's message (ignored in this state)
            intent: Detected intent (ignored in this state)

        Returns:
            Tuple of (new_state, response_message)
        """
        doctor_id = state.slots["doctor"].value
        doctor_name = state.slots.get("doctor_name", {}).value if "doctor_name" in state.slots else "доктору"
        date_str = state.slots["date"].value
        time_str = state.slots["time"].value

        logger.info(
            f"Conversation {state.conversation_id}: "
            f"Creating appointment via atomic RPC, hold_id={state.hold_id}"
        )

        try:
            # Call atomic RPC to confirm hold and create appointment
            # P2 Phase 2B: Use atomic RPC instead of separate operations
            result = self.supabase.rpc(
                'confirm_hold_and_create_appointment',
                {
                    'p_hold_id': state.hold_id,
                    'p_patient_id': None,  # TODO: Get or create patient first
                    'p_service_id': None,  # TODO: Add service selection to FSM
                    'p_appointment_type': 'general',
                    'p_reason_for_visit': None,  # TODO: Optionally collect from user
                    'p_booking_request_id': state.booking_request_id
                }
            ).execute()

            if result.data and len(result.data) > 0:
                booking_result = result.data[0]

                if booking_result['success']:
                    # Booking succeeded!
                    appointment_id = booking_result['appointment_id']

                    logger.info(
                        f"Conversation {state.conversation_id}: "
                        f"Appointment created successfully: {appointment_id}"
                    )

                    # Transition to COMPLETED
                    new_state = await self.fsm.transition_state(
                        state,
                        ConversationState.COMPLETED
                    )

                    # Reset failure count
                    new_state = await self.fsm.reset_failure(new_state)

                    # P2 Phase 2B: Track successful booking in Langfuse
                    llm_observability.score_booking_outcome(
                        session_id=state.conversation_id,
                        success=True,
                        booking_id=appointment_id
                    )

                    response = (
                        f"✅ Ваша запись подтверждена!\n\n"
                        f"• Доктор: {doctor_name}\n"
                        f"• Дата: {date_str}\n"
                        f"• Время: {time_str}\n\n"
                        f"Ждём вас в клинике. Мы отправим напоминание за день до приёма."
                    )

                    return new_state, response
                else:
                    # RPC failed (hold expired or other error)
                    error_message = booking_result.get('error_message', 'Unknown error')

                    logger.error(
                        f"Conversation {state.conversation_id}: "
                        f"Atomic RPC failed: {error_message}"
                    )

                    # Transition to FAILED
                    new_state = await self.fsm.transition_state(
                        state,
                        ConversationState.FAILED
                    )

                    # P2 Phase 2B: Track failed booking in Langfuse
                    llm_observability.score_booking_outcome(
                        session_id=state.conversation_id,
                        success=False,
                        reason=error_message
                    )

                    if "expired" in error_message.lower():
                        response = (
                            f"❌ К сожалению, бронь истекла.\n"
                            f"Давайте попробуем снова. К какому доктору вы хотите записаться?"
                        )
                    else:
                        response = (
                            f"❌ Ошибка при создании записи: {error_message}\n"
                            f"Пожалуйста, свяжитесь с нашим администратором."
                        )

                    return new_state, response
            else:
                # No result from RPC
                logger.error(
                    f"Conversation {state.conversation_id}: "
                    f"RPC returned no data"
                )

                new_state = await self.fsm.transition_state(
                    state,
                    ConversationState.FAILED
                )

                llm_observability.score_booking_outcome(
                    session_id=state.conversation_id,
                    success=False,
                    reason="RPC returned no data"
                )

                response = (
                    f"❌ Ошибка при создании записи.\n"
                    f"Пожалуйста, свяжитесь с нашим администратором."
                )

                return new_state, response

        except Exception as e:
            logger.error(
                f"Conversation {state.conversation_id}: "
                f"Exception during booking: {e}",
                exc_info=True
            )

            new_state = await self.fsm.transition_state(
                state,
                ConversationState.FAILED
            )

            llm_observability.score_booking_outcome(
                session_id=state.conversation_id,
                success=False,
                reason=str(e)
            )

            response = (
                f"❌ Ошибка при создании записи.\n"
                f"Пожалуйста, свяжитесь с нашим администратором."
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
