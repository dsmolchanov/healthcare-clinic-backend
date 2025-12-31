"""FSM-based orchestrator that replaces LangGraph.

This orchestrator uses a simple architecture:
1. Pre-FSM guardrails (emergency/PHI detection)
2. One-shot router (single LLM call)
3. FSM.step() for business logic (no LLM)
4. Tool execution for actions
5. Post-FSM guardrails (PHI redaction)
6. Response formatting

Key principle: LLMs understand; Code decides.
"""

import logging
import re
from typing import Dict, Any, Optional, Callable, Awaitable, Union
from dataclasses import asdict

try:
    import phonenumbers
    from phonenumbers import NumberParseException
    PHONENUMBERS_AVAILABLE = True
except ImportError:
    PHONENUMBERS_AVAILABLE = False

from app.services.orchestrator.fsm.types import (
    Action, AskUser, CallTool, Respond, Escalate,
    UserEvent, ToolResultEvent, RouterOutput
)
from app.services.orchestrator.fsm.state import (
    BookingState, PricingState, BookingStage, PricingStage
)
from app.services.orchestrator.fsm import booking_fsm, pricing_fsm
from app.services.orchestrator.fsm.router import route_message, fallback_router
from app.services.orchestrator.fsm.text_utils import is_affirmative, is_rejection
from app.tools.clinic_info_tool import ClinicInfoTool
from app.config import get_redis_client
# Preserve existing guardrails
from app.services.orchestrator.templates.handlers.guardrails import (
    detect_emergency, detect_phi_ssn, get_emergency_response_by_language,
    get_pii_response_by_language
)
from app.services.orchestrator.templates.handlers.phi_handler import (
    detect_phi_basic, redact_phi_basic
)

logger = logging.getLogger(__name__)


def validate_phone_number(phone: Optional[str], default_region: str = "MX") -> Optional[str]:
    """
    Validate and normalize phone number.

    Args:
        phone: Phone number string to validate
        default_region: Default region code (MX for Mexico)

    Returns:
        E.164 formatted phone or None if invalid
    """
    if not phone:
        return None

    # If phonenumbers library is available, use it for proper validation
    if PHONENUMBERS_AVAILABLE:
        try:
            # Try parsing with default region
            parsed = phonenumbers.parse(phone, default_region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except NumberParseException:
            pass

    # Fallback: if it looks like E.164 already, use as-is
    # E.164 format: +[country code][subscriber number], 10-15 digits
    if phone.startswith('+') and len(phone) >= 10:
        # Basic validation: contains mostly digits
        clean = phone.replace('+', '').replace(' ', '').replace('-', '')
        if clean.isdigit() and 9 <= len(clean) <= 15:
            return phone

    return None


class FSMOrchestrator:
    """
    FSM-based orchestrator that replaces LangGraph.

    Architecture:
    1. Pre-FSM guardrails (emergency/PHI detection)
    2. One-shot router (single LLM call)
    3. FSM.step() for business logic (no LLM)
    4. Tool execution for actions
    5. Post-FSM guardrails (PHI redaction)
    6. Response formatting
    """

    def __init__(
        self,
        clinic_id: str,
        llm_factory: Any,
        supabase_client: Any = None,
        appointment_tools: Any = None,
        price_tool: Any = None,
        clinic_profile: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the FSM orchestrator.

        Args:
            clinic_id: Clinic identifier
            llm_factory: LLMFactory for router calls
            supabase_client: Database client
            appointment_tools: AppointmentTools instance (injected)
            price_tool: PriceQueryTool instance (injected)
            clinic_profile: Clinic configuration
        """
        self.clinic_id = clinic_id
        self.llm_factory = llm_factory
        self.supabase_client = supabase_client
        self.clinic_profile = clinic_profile or {}
        self.appointment_tools = appointment_tools
        self.price_tool = price_tool

        # Tool registry - maps tool names to handlers
        self.tool_registry: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {}
        self._register_tools()

    def _register_tools(self) -> None:
        """Register tools with argument adapters."""
        if self.appointment_tools:
            self.tool_registry["check_availability"] = self._check_availability_adapter
            self.tool_registry["book_appointment"] = self._book_appointment_adapter
            self.tool_registry["cancel_appointment"] = self._cancel_appointment_adapter

        if self.price_tool:
            self.tool_registry["query_service_prices"] = self._query_prices_adapter

    async def _check_availability_adapter(self, **args: Any) -> Dict[str, Any]:
        """Adapter: translate FSM args to AppointmentTools signature.

        FSM uses: service_type, date, time_preference, doctor_name
        Tool expects: doctor_id, date, appointment_type, duration_minutes
        """
        try:
            result = await self.appointment_tools.check_availability(
                doctor_id=args.get("doctor_id"),
                date=args.get("date"),
                appointment_type=args.get("service_type", "general"),
                duration_minutes=30,  # Default
            )
            return result
        except Exception as e:
            logger.error(f"check_availability failed: {e}")
            return {"success": False, "available_slots": [], "error": str(e)}

    async def _book_appointment_adapter(self, **args: Any) -> Dict[str, Any]:
        """Adapter: translate FSM args to AppointmentTools signature."""
        try:
            result = await self.appointment_tools.book_appointment(
                patient_id=args.get("patient_id", "unknown"),
                doctor_id=args.get("doctor_id"),
                datetime_str=args.get("datetime_str"),
                appointment_type=args.get("appointment_type", "general"),
                notes=args.get("notes"),
            )
            return result
        except Exception as e:
            logger.error(f"book_appointment failed: {e}")
            return {"success": False, "error": str(e)}

    async def _cancel_appointment_adapter(self, **args: Any) -> Dict[str, Any]:
        """Adapter for cancellation."""
        try:
            result = await self.appointment_tools.cancel_appointment(
                appointment_id=args.get("appointment_id"),
                reason=args.get("reason"),
            )
            return result
        except Exception as e:
            logger.error(f"cancel_appointment failed: {e}")
            return {"success": False, "error": str(e)}

    async def _query_prices_adapter(self, **args: Any) -> Dict[str, Any]:
        """Adapter for price queries."""
        try:
            results = await self.price_tool.get_services_by_query(
                query=args.get("query"),
                limit=5,
            )
            return {"results": results, "success": True}
        except Exception as e:
            logger.error(f"query_service_prices failed: {e}")
            return {"results": [], "success": False, "error": str(e)}

    async def process(
        self,
        message: str,
        session_id: str,
        state: Optional[Dict[str, Any]] = None,
        language: str = "en",
        # NEW: Session context from WhatsApp for pre-population
        user_phone: Optional[str] = None,
        user_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a user message through the FSM.

        Args:
            message: User's message text
            session_id: Session identifier for state persistence
            state: Previous FSM state (serialized dict)
            language: Language code
            user_phone: WhatsApp phone number (JID) for pre-population
            user_name: WhatsApp display name (pushName) for pre-population

        Returns:
            {
                "response": str,
                "state": dict,  # Serialized FSM state for persistence
                "tools_called": list,
                "route": str,
                "guardrail_triggered": bool,
            }
        """
        tools_called: list = []

        # ==========================================
        # STEP 0: PRE-FSM GUARDRAILS (CRITICAL)
        # ==========================================
        # Check for emergency BEFORE any processing
        is_emergency, emergency_pattern = detect_emergency(message)
        if is_emergency:
            logger.warning(f"[GUARDRAIL] Emergency detected: {emergency_pattern}")
            return {
                "response": get_emergency_response_by_language(language),
                "state": None,
                "tools_called": [],
                "route": "emergency",
                "guardrail_triggered": True,
            }

        # Check for PHI/SSN in input
        if detect_phi_ssn(message):
            logger.warning("[GUARDRAIL] PHI/SSN detected in input")
            return {
                "response": get_pii_response_by_language(language),
                "state": state,  # Preserve state
                "tools_called": [],
                "route": "guardrail",
                "guardrail_triggered": True,
            }

        # ==========================================
        # STEP 1: Load or initialize state WITH session context
        # ==========================================
        fsm_state = self._load_state(state, user_phone=user_phone, user_name=user_name)

        # ==========================================
        # STEP 2: Route message (one LLM call)
        # ==========================================
        try:
            router_output = await route_message(message, self.llm_factory, language)
        except Exception as e:
            logger.error(f"Router failed: {e}")
            router_output = fallback_router(message, language)

        logger.info(f"[FSM] Route: {router_output.route}")

        # ==========================================
        # STEP 3: Handle based on route
        # ==========================================
        if router_output.route == "exit":
            return {
                "response": self._get_goodbye(language),
                "state": None,
                "tools_called": [],
                "route": "exit",
                "guardrail_triggered": False,
            }

        # Handle doctor_info tangent (fetch doctor list from DB)
        if router_output.route == "doctor_info":
            return await self._handle_doctor_info(
                router_output, fsm_state, language
            )

        if router_output.route == "info":
            return await self._handle_info(message, language)

        if router_output.route == "pricing":
            return await self._handle_pricing(message, router_output, language)

        if router_output.route == "cancel":
            # Pass serialized state for appointment_id lookup
            serialized_state = self._serialize_state(fsm_state) if fsm_state else None
            return await self._handle_cancel(message, language, serialized_state)

        if router_output.route == "irrelevant":
            # CONTEXT-AWARE ROUTING: Check if this looks like a yes/no response
            # before treating it as truly irrelevant. This catches typos like "Yse"
            # that the LLM router might not recognize.
            message_len = len(message.split())
            if message_len <= 3:  # Short responses are likely yes/no
                if is_affirmative(message, language) or is_rejection(message, language):
                    # This is likely a yes/no response to a previous question
                    # Route it through scheduling instead of irrelevant
                    logger.info(f"[FSM] Rerouting short affirmative/rejection from 'irrelevant' to 'scheduling': {message}")
                    return await self._handle_scheduling(
                        message, router_output, fsm_state, language, tools_called
                    )
            return await self._handle_irrelevant(message, language)

        # Default: scheduling (booking)
        return await self._handle_scheduling(
            message, router_output, fsm_state, language, tools_called
        )

    async def _handle_scheduling(
        self,
        message: str,
        router_output: RouterOutput,
        state: BookingState,
        language: str,
        tools_called: list,
    ) -> Dict[str, Any]:
        """Handle booking flow through FSM."""

        # Create user event
        event = UserEvent(text=message, router=router_output, language=language)

        # FSM step
        state, actions = booking_fsm.step(state, event)

        # Process actions until we get a response
        response_text = ""
        max_iterations = 10  # Prevent infinite loops

        for _ in range(max_iterations):
            for action in actions:
                if isinstance(action, Respond):
                    response_text = action.text
                    break

                if isinstance(action, AskUser):
                    response_text = action.text
                    break

                if isinstance(action, Escalate):
                    # Log escalation for analytics
                    logger.info(f"[FSM] Escalation: {action.reason}")
                    # Don't set response - next action should be Respond
                    continue

                if isinstance(action, CallTool):
                    # Execute tool
                    tool_name = action.name
                    tool_args = action.args
                    tools_called.append({"name": tool_name, "args": tool_args})

                    handler = self.tool_registry.get(tool_name)
                    if handler:
                        try:
                            result = await handler(**tool_args)
                            success = result.get("success", True)
                        except Exception as e:
                            logger.error(f"Tool {tool_name} failed: {e}")
                            result = {"error": str(e)}
                            success = False
                    else:
                        logger.warning(f"Unknown tool: {tool_name}")
                        result = {"error": f"Unknown tool: {tool_name}"}
                        success = False

                    # Feed result back to FSM
                    tool_event = ToolResultEvent(
                        tool_name=tool_name,
                        result=result,
                        success=success
                    )
                    state, actions = booking_fsm.step(state, tool_event)
                    break  # Process new actions

            if response_text:
                break

        # Post-FSM guardrails: redact any PHI in response
        response_text = self._redact_phi_in_response(response_text)

        return {
            "response": response_text,
            "state": self._serialize_state(state),
            "tools_called": tools_called,
            "route": "scheduling",
            "guardrail_triggered": False,
        }

    async def _handle_pricing(
        self,
        message: str,
        router_output: RouterOutput,
        language: str,
    ) -> Dict[str, Any]:
        """Handle pricing queries through FSM."""

        state = PricingState(language=language)
        event = UserEvent(text=message, router=router_output, language=language)
        tools_called: list = []

        # FSM step
        state, actions = pricing_fsm.step(state, event)

        # Process actions
        response_text = ""

        for action in actions:
            if isinstance(action, Respond):
                response_text = action.text
                break

            if isinstance(action, CallTool):
                tool_name = action.name
                tool_args = action.args
                tools_called.append({"name": tool_name, "args": tool_args})

                handler = self.tool_registry.get(tool_name)
                if handler:
                    try:
                        result = await handler(**tool_args)
                        success = result.get("success", True)
                    except Exception as e:
                        logger.error(f"Tool {tool_name} failed: {e}")
                        result = {"error": str(e), "results": []}
                        success = False
                else:
                    result = {"error": f"Unknown tool: {tool_name}", "results": []}
                    success = False

                # Feed result back to FSM
                tool_event = ToolResultEvent(
                    tool_name=tool_name,
                    result=result,
                    success=success
                )
                state, actions = pricing_fsm.step(state, tool_event)

                # Check for response in new actions
                for a in actions:
                    if isinstance(a, Respond):
                        response_text = a.text
                        break

        return {
            "response": response_text,
            "state": None,  # Pricing is single-turn
            "tools_called": tools_called,
            "route": "pricing",
            "guardrail_triggered": False,
        }

    async def _handle_info(
        self,
        message: str,
        language: str,
    ) -> Dict[str, Any]:
        """Handle info queries (hours, location, time, etc.)."""
        # Use clinic profile for info
        info = self.clinic_profile
        msg_lower = message.lower()

        # Check if this is a time/timezone query
        time_keywords = ['what time', 'current time', 'time is it', 'timezone',
                        'который час', 'que hora', 'qué hora']
        is_time_query = any(kw in msg_lower for kw in time_keywords)

        if is_time_query:
            time_responses = {
                'en': "I don't have access to real-time clock information. "
                      "Our clinic is open Monday-Friday 9am-5pm. "
                      "Would you like to schedule an appointment?",
                'ru': "У меня нет доступа к информации о текущем времени. "
                      "Наша клиника работает с понедельника по пятницу с 9:00 до 17:00. "
                      "Хотите записаться на приём?",
                'es': "No tengo acceso a la hora actual. "
                      "Nuestra clínica está abierta de lunes a viernes de 9am a 5pm. "
                      "¿Le gustaría programar una cita?",
            }
            return {
                "response": time_responses.get(language, time_responses['en']),
                "state": None,
                "tools_called": [],
                "route": "info",
                "guardrail_triggered": False,
            }

        # Standard info response
        responses = {
            'en': f"Our clinic is open Monday-Friday 9am-5pm. "
                  f"You can reach us at {info.get('phone', 'our front desk')}. "
                  f"Is there anything else I can help with?",
            'ru': f"Наша клиника работает с понедельника по пятницу с 9:00 до 17:00. "
                  f"Вы можете связаться с нами по телефону {info.get('phone', 'регистратуры')}. "
                  f"Могу ли я помочь вам с чем-то ещё?",
            'es': f"Nuestra clínica está abierta de lunes a viernes de 9am a 5pm. "
                  f"Puede contactarnos al {info.get('phone', 'recepción')}. "
                  f"¿Hay algo más en lo que pueda ayudarle?",
        }

        return {
            "response": responses.get(language, responses['en']),
            "state": None,
            "tools_called": [],
            "route": "info",
            "guardrail_triggered": False,
        }

    async def _handle_cancel(
        self,
        message: str,
        language: str,
        state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Handle cancellation requests.

        Attempts to call cancel_appointment tool if we have an appointment_id.
        Falls back to asking for confirmation.

        Args:
            message: User message
            language: Language code
            state: FSM state with potential appointment_id
        """
        tools_called: list = []

        # Check if we have a recent appointment_id from a booking
        appointment_id = None
        if state:
            appointment_id = state.get('appointment_id')

        # If we have cancel_appointment tool and an appointment_id, attempt cancellation
        if "cancel_appointment" in self.tool_registry and appointment_id:
            handler = self.tool_registry["cancel_appointment"]
            try:
                result = await handler(
                    appointment_id=appointment_id,
                    reason="User requested cancellation"
                )
                tools_called.append({"name": "cancel_appointment", "args": {"appointment_id": appointment_id}})

                if result.get("success"):
                    # Successful cancellation
                    responses = {
                        'en': "I've cancelled your appointment. Is there anything else I can help with?",
                        'ru': "Я отменил(а) вашу запись. Могу ли я помочь вам с чем-то ещё?",
                        'es': "He cancelado su cita. ¿Hay algo más en lo que pueda ayudarle?",
                    }
                    return {
                        "response": responses.get(language, responses['en']),
                        "state": None,  # Clear state after cancellation
                        "tools_called": tools_called,
                        "route": "cancel",
                        "guardrail_triggered": False,
                    }
            except Exception as e:
                logger.error(f"Cancel appointment failed: {e}")

        # No appointment to cancel or cancellation failed - ask for confirmation
        # Check if user is asking to cancel a recent appointment
        confirm_responses = {
            'en': "I understand you want to cancel your appointment. Could you confirm the date and time of the appointment you'd like to cancel?",
            'ru': "Понятно, вы хотите отменить запись. Не могли бы вы подтвердить дату и время записи, которую хотите отменить?",
            'es': "Entiendo que desea cancelar su cita. ¿Podría confirmar la fecha y hora de la cita que desea cancelar?",
        }

        return {
            "response": confirm_responses.get(language, confirm_responses['en']),
            "state": state,  # Preserve state for follow-up
            "tools_called": tools_called,
            "route": "cancel",
            "guardrail_triggered": False,
        }

    async def _handle_doctor_info(
        self,
        router_output: RouterOutput,
        state: BookingState,
        language: str,
    ) -> Dict[str, Any]:
        """Handle doctor info tangent questions.

        Fetches actual doctor list from database for accurate matching.

        Args:
            router_output: Router output with doctor_name and doctor_info_kind
            state: Current booking state
            language: Language code
        """
        # Fetch doctor list from database (uses Redis cache if available)
        doctor_list = []
        try:
            if self.supabase_client:
                # Get redis client for cache access
                redis_client = get_redis_client()
                clinic_tool = ClinicInfoTool(self.clinic_id, redis_client=redis_client)
                doctor_info = await clinic_tool.get_doctor_count(self.supabase_client)
                # Format as "Dr. FirstName LastName"
                doctor_list = [
                    f"Dr. {name}" if not name.lower().startswith('dr') else name
                    for name in doctor_info.get('doctor_list', [])
                ]
                logger.info(f"[FSM] Fetched {len(doctor_list)} doctors for clinic {self.clinic_id}")
        except Exception as e:
            logger.warning(f"Failed to fetch doctor list: {e}")
            # Will fall back to hardcoded default in handler

        # Call FSM tangent handler with actual doctor list
        new_state, actions = booking_fsm.handle_doctor_info_tangent(
            state=state,
            doctor_name=router_output.doctor_name,
            doctor_info_kind=router_output.doctor_info_kind or "exists",
            lang=language,
            doctor_list=doctor_list if doctor_list else None,  # Pass None to use default
        )

        # Extract response
        response_text = ""
        for action in actions:
            if isinstance(action, AskUser):
                response_text = action.text
                break
            if isinstance(action, Respond):
                response_text = action.text
                break

        return {
            "response": response_text,
            "state": self._serialize_state(new_state),
            "tools_called": [],
            "route": "doctor_info",
            "guardrail_triggered": False,
        }

    async def _handle_irrelevant(
        self,
        message: str,
        language: str,
    ) -> Dict[str, Any]:
        """Handle out-of-scope/irrelevant queries.

        Politely redirects user back to dental/clinic topics.
        """
        responses = {
            'en': "I can only help with dental appointments and clinic-related questions. "
                  "Would you like to schedule an appointment or ask about our services?",
            'ru': "Я могу помочь только с вопросами о стоматологических приёмах и услугах клиники. "
                  "Хотите записаться на приём или узнать о наших услугах?",
            'es': "Solo puedo ayudar con citas dentales y preguntas relacionadas con la clínica. "
                  "¿Le gustaría programar una cita o preguntar sobre nuestros servicios?",
        }

        return {
            "response": responses.get(language, responses['en']),
            "state": None,
            "tools_called": [],
            "route": "irrelevant",
            "guardrail_triggered": False,
        }

    def _load_state(
        self,
        state_dict: Optional[Dict[str, Any]],
        user_phone: Optional[str] = None,
        user_name: Optional[str] = None,
    ) -> BookingState:
        """Load FSM state from serialized dict, pre-populating from session.

        Args:
            state_dict: Serialized FSM state
            user_phone: WhatsApp phone number (JID) for pre-population
            user_name: WhatsApp display name (pushName) for pre-population

        Returns:
            BookingState with pre-populated fields if available
        """
        # Validate phone number before use (cuts ~15% invalid)
        validated_phone = validate_phone_number(user_phone)

        # Store display_name separately from patient_name for compliance
        # WhatsApp pushName may be nickname like "Dmitry 😎" - not legal name
        display_name = user_name

        if not state_dict:
            # NEW: Initialize with session context if available
            return BookingState(
                patient_phone=validated_phone,
                patient_name=None,  # Don't assume display_name is legal name
                display_name=display_name,  # Store separately for reference
            )

        try:
            # Convert stage string back to enum
            stage_str = state_dict.get('stage', 'INTENT')
            stage = BookingStage[stage_str] if isinstance(stage_str, str) else stage_str

            # Pre-populate patient info from session if not already in state
            patient_phone = state_dict.get('patient_phone') or validated_phone
            patient_name = state_dict.get('patient_name')  # Don't auto-fill from display_name
            stored_display_name = state_dict.get('display_name') or display_name

            return BookingState(
                stage=stage,
                service_type=state_dict.get('service_type'),
                target_date=state_dict.get('target_date'),
                time_of_day=state_dict.get('time_of_day'),
                doctor_name=state_dict.get('doctor_name'),
                doctor_id=state_dict.get('doctor_id'),
                patient_name=patient_name,
                patient_phone=patient_phone,  # Pre-populated & validated
                patient_id=state_dict.get('patient_id'),
                display_name=stored_display_name,  # WhatsApp pushName (may be nickname)
                available_slots=state_dict.get('available_slots', []),
                selected_slot=state_dict.get('selected_slot'),
                appointment_id=state_dict.get('appointment_id'),
                confirmation_message=state_dict.get('confirmation_message'),
                has_pain=state_dict.get('has_pain', False),
                language=state_dict.get('language', 'en'),
                clarification_count=state_dict.get('clarification_count', 0),
                # Phase 1: Contextual response handling
                awaiting_field=state_dict.get('awaiting_field'),
                pending_action=state_dict.get('pending_action'),
                user_prefers_concise=state_dict.get('user_prefers_concise', False),
            )
        except Exception as e:
            logger.warning(f"Failed to load state, starting fresh: {e}")
            return BookingState(
                patient_phone=validated_phone,
                display_name=display_name,
            )

    def _serialize_state(self, state: BookingState) -> Dict[str, Any]:
        """Serialize FSM state to dict for persistence."""
        return {
            'stage': state.stage.name,  # Convert enum to string
            'service_type': state.service_type,
            'target_date': state.target_date,
            'time_of_day': state.time_of_day,
            'doctor_name': state.doctor_name,
            'doctor_id': state.doctor_id,
            'patient_name': state.patient_name,
            'patient_phone': state.patient_phone,
            'patient_id': state.patient_id,
            'display_name': state.display_name,  # WhatsApp pushName
            'available_slots': state.available_slots,
            'selected_slot': state.selected_slot,
            'appointment_id': state.appointment_id,
            'confirmation_message': state.confirmation_message,
            'has_pain': state.has_pain,
            'language': state.language,
            'clarification_count': state.clarification_count,
            # Phase 1: Contextual response handling
            'awaiting_field': state.awaiting_field,
            'pending_action': state.pending_action,
            'user_prefers_concise': state.user_prefers_concise,
        }

    def _get_goodbye(self, language: str) -> str:
        """Get localized goodbye message."""
        goodbyes = {
            'en': "Thank you for contacting us. Have a great day!",
            'ru': "Спасибо за обращение. Хорошего дня!",
            'es': "Gracias por contactarnos. ¡Que tenga un buen día!",
        }
        return goodbyes.get(language, goodbyes['en'])

    def _redact_phi_in_response(self, text: str) -> str:
        """Apply PHI redaction to response text."""
        if not text:
            return text

        # Detect PHI patterns first
        has_phi, phi_tokens = detect_phi_basic(text)
        if has_phi:
            logger.warning("[GUARDRAIL] PHI detected in response, redacting")
            return redact_phi_basic(text, phi_tokens)

        return text
