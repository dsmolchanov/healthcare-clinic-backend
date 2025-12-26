"""
Healthcare LangGraph Template
HIPAA-compliant orchestrator for healthcare/dental conversations
Extends base orchestrator with PHI protection and appointment handling

Phase 2 Enhancements:
- Guardrail node (runs BEFORE supervisor for security)
- Language detection node (replaces RoutingStep language detection)
- Session init node with TTL handling for action proposals
- Simple answer agent for fast FAQ path
- Session-aware hydration with parallel fetch
- Plan-then-Execute pattern for bookings
"""

import sys
import os
import re
import asyncio

from ..base_langgraph import BaseLangGraphOrchestrator, BaseConversationState, ComplianceMode, last_value
from langgraph.graph import StateGraph, END
from typing import Optional, Dict, Any, List, Annotated
import logging
from datetime import datetime, timezone, timedelta

# Import ConversationState for unified state tracking
from app.models.conversation_state import FlowState, ConversationState

# Import action models for Plan-then-Execute
from app.services.orchestrator.models.action_plan import ActionPlan, PlanStep, ActionType, PlanExecutionResult
from app.services.orchestrator.models.action_proposal import ActionProposal, ActionProposalType, is_confirmation_response

# Import appointment tools
try:
    from ..tools.appointment_tools import AppointmentTools
    APPOINTMENT_TOOLS_AVAILABLE = True
except ImportError:
    APPOINTMENT_TOOLS_AVAILABLE = False

logger = logging.getLogger(__name__)


class HealthcareConversationState(BaseConversationState):
    """
    Healthcare-specific conversation state.

    Extends BaseConversationState with healthcare/PHI fields.
    All fields use Annotated with last_value reducer for LangGraph checkpointing.
    """
    # PHI-related fields
    contains_phi: Annotated[bool, last_value]
    phi_tokens: Annotated[Optional[Dict[str, str]], last_value]
    de_identified_message: Annotated[Optional[str], last_value]

    # Appointment fields
    appointment_type: Annotated[Optional[str], last_value]
    preferred_date: Annotated[Optional[str], last_value]
    preferred_time: Annotated[Optional[str], last_value]
    doctor_id: Annotated[Optional[str], last_value]

    # Patient context
    patient_id: Annotated[Optional[str], last_value]
    patient_name: Annotated[Optional[str], last_value]
    insurance_verified: Annotated[bool, last_value]

    # Supervisor routing (Phase 3)
    flow_state: Annotated[str, last_value]  # FlowState.value
    active_task: Annotated[Optional[Dict[str, Any]], last_value]  # BookingTask as dict
    next_agent: Annotated[Optional[str], last_value]  # Supervisor routing decision

    # Phase 2: Guardrail fields
    is_emergency: Annotated[bool, last_value]
    phi_detected: Annotated[bool, last_value]
    allowed_tools: Annotated[List[str], last_value]
    blocked_tools: Annotated[List[str], last_value]
    guardrail_action: Annotated[Optional[str], last_value]  # 'escalate', 'restrict', 'allow'
    escalation_reason: Annotated[Optional[str], last_value]

    # Phase 2: Language detection
    detected_language: Annotated[str, last_value]

    # Phase 2: Context hydration
    context_hydrated: Annotated[bool, last_value]
    previous_session_summary: Annotated[Optional[Dict[str, Any]], last_value]

    # Phase 2: Fast path
    fast_path: Annotated[bool, last_value]
    lane: Annotated[Optional[str], last_value]

    # Phase 2: Plan-then-Execute
    action_plan: Annotated[Optional[Dict[str, Any]], last_value]
    plan_results: Annotated[Optional[Dict[str, Any]], last_value]
    plan_completed_steps: Annotated[List[str], last_value]
    plan_execution_error: Annotated[Optional[str], last_value]
    plan_failed_step: Annotated[Optional[str], last_value]
    plan_needs_replanning: Annotated[bool, last_value]

    # Phase 2: Action Proposal (HITL confirmation)
    action_proposal: Annotated[Optional[Dict[str, Any]], last_value]
    awaiting_confirmation: Annotated[bool, last_value]
    pending_action: Annotated[Optional[Dict[str, Any]], last_value]
    pending_action_timestamp: Annotated[Optional[str], last_value]
    pending_action_expired: Annotated[bool, last_value]
    user_confirmed: Annotated[bool, last_value]
    proposal_verified: Annotated[bool, last_value]
    verification_error: Annotated[Optional[str], last_value]

    # Phase 4: Routing control (booking flow fix)
    static_info_skipped_due_to_scheduling: Annotated[Optional[bool], last_value]
    force_reroute_to: Annotated[Optional[str], last_value]
    supervisor_overrode_to_scheduling: Annotated[Optional[bool], last_value]
    supervisor_forced_scheduling: Annotated[Optional[bool], last_value]

    # Phase 4: Extraction fields (booking flow fix)
    booking_intent: Annotated[Optional[str], last_value]
    extracted_booking_info: Annotated[Optional[Dict[str, Any]], last_value]
    preferred_date_raw: Annotated[Optional[str], last_value]
    doctor_preference: Annotated[Optional[str], last_value]
    is_urgent: Annotated[Optional[bool], last_value]
    patient_phone: Annotated[Optional[str], last_value]

    # Phase 4: Clarification flow (booking flow fix)
    awaiting_patient_identification: Annotated[Optional[bool], last_value]
    awaiting_datetime: Annotated[Optional[bool], last_value]
    clarification_count: Annotated[Optional[int], last_value]  # Track >2 → escalate
    needs_human_escalation: Annotated[Optional[bool], last_value]

    # Phase 4: Executor debugging & silent failure prevention
    tools_actually_called: Annotated[Optional[List[str]], last_value]  # Track internal tool execution
    tools_failed: Annotated[Optional[List[Dict[str, Any]]], last_value]  # Track failed tool calls with details
    executor_validation_errors: Annotated[Optional[List[str]], last_value]  # Track validation errors
    planner_validation_errors: Annotated[Optional[List[str]], last_value]  # Track planner validation errors
    booking_blocked_no_availability_check: Annotated[Optional[bool], last_value]  # Strict mode flag
    booking_blocked_no_verification: Annotated[Optional[bool], last_value]  # Strict mode flag
    preferred_date_iso: Annotated[Optional[str], last_value]  # Resolved ISO datetime from natural language


class HealthcareLangGraph(BaseLangGraphOrchestrator):
    """
    Healthcare-specific LangGraph orchestrator
    Implements HIPAA compliance and appointment handling
    """

    def __init__(
        self,
        phi_middleware: Optional[Any] = None,
        appointment_service: Optional[Any] = None,
        enable_emergency_detection: bool = True,
        supabase_client: Optional[Any] = None,
        clinic_id: Optional[str] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize healthcare orchestrator

        Args:
            phi_middleware: PHI de-identification service
            appointment_service: Appointment booking service
            enable_emergency_detection: Enable emergency keyword detection
            supabase_client: Supabase client for database operations
            clinic_id: Clinic identifier
            agent_config: Agent configuration with LLM settings and tools
        """
        # Set all child-specific attributes BEFORE calling super().__init__
        # This is critical because parent's __init__ calls _build_graph() which accesses these attributes
        self.phi_middleware = phi_middleware
        self.appointment_service = appointment_service
        self.enable_emergency_detection = enable_emergency_detection
        self.supabase_client = supabase_client
        self.clinic_id = clinic_id

        # Private caches for lazy-loaded tools (initialized on first access)
        self._appointment_tools = None
        self._price_query_tool = None
        self._faq_tool = None

        # NOW call parent init (which will call _build_graph and safely access our attributes)
        super().__init__(
            compliance_mode=ComplianceMode.HIPAA,
            enable_memory=False,      # Pipeline provides via conversation_memory.py
            enable_rag=False,         # Pipeline provides via PipelineContext.knowledge_context
            enable_checkpointing=False,  # Pipeline provides state via Redis StateManager
            supabase_client=supabase_client,
            agent_config=agent_config
        )

    @property
    def appointment_tools(self):
        """Lazy-load appointment tools on first access."""
        if self._appointment_tools is None and APPOINTMENT_TOOLS_AVAILABLE:
            self._appointment_tools = AppointmentTools(
                supabase_client=self.supabase_client,
                calendar_service=self.appointment_service,
                clinic_id=self.clinic_id
            )
            logger.debug(f"[HealthcareLangGraph] Lazy-loaded AppointmentTools for clinic {self.clinic_id}")
        return self._appointment_tools

    @property
    def price_query_tool(self):
        """Lazy-load price query tool on first access."""
        if self._price_query_tool is None and self.clinic_id:
            from app.tools.price_query_tool import PriceQueryTool
            self._price_query_tool = PriceQueryTool(clinic_id=self.clinic_id)
            logger.debug(f"[HealthcareLangGraph] Lazy-loaded PriceQueryTool for clinic {self.clinic_id}")
        return self._price_query_tool

    @property
    def faq_tool(self):
        """Lazy-load FAQ tool on first access."""
        if self._faq_tool is None and self.clinic_id:
            from app.tools.faq_query_tool import FAQQueryTool
            self._faq_tool = FAQQueryTool(clinic_id=self.clinic_id)
            logger.debug(f"[HealthcareLangGraph] Lazy-loaded FAQQueryTool for clinic {self.clinic_id}")
        return self._faq_tool

    # ========================================================================
    # Phase 4: Scheduling intent detection (booking flow fix)
    # ENHANCED (Opinion 3): Expanded keywords with multilingual variants
    # ========================================================================
    SCHEDULING_KEYWORDS = [
        # English - action verbs (Opinion 4: focus on action verbs)
        'book', 'appointment', 'schedule', 'reschedule', 'cancel',
        'reserve', 'visit', 'see doctor', 'see the doctor',
        # Symptoms/urgency
        'pain', 'hurts', 'ache', 'emergency', 'urgent',
        # Services (Opinion 3: add service types)
        'cleaning', 'checkup', 'exam', 'filling', 'root canal', 'whitening',
        # Contact info submission patterns (Opinion 4: "my phone" not just "phone")
        'my phone', 'my number', 'you can reach me', 'contact me at',
        'my name is', 'i am', 'call me at',
        # Spanish
        'cita', 'reservar', 'programar', 'dolor', 'urgente',
        'mi teléfono', 'mi nombre es', 'me llamo',
        # Russian
        'записаться', 'запись', 'записать', 'болит', 'боль', 'срочно',
        'мой телефон', 'меня зовут', 'мой номер',
        # Portuguese
        'agendar', 'consulta', 'marcar', 'dor', 'urgente',
    ]

    def _looks_like_scheduling(self, message: str) -> bool:
        """Check if message has scheduling intent."""
        m = message.lower()
        # If it's a pure pricing query, don't treat as scheduling
        if self._looks_like_pricing(m):
            return False
        return any(k in m for k in self.SCHEDULING_KEYWORDS)

    # Pricing keywords for distinguishing pricing queries from booking
    PRICING_KEYWORDS = [
        # English
        'price', 'prices', 'cost', 'costs', 'how much', 'fee', 'fees',
        'charge', 'charges', 'rate', 'rates', 'expensive', 'cheap',
        'affordable', 'compare', 'comparison', 'vs', 'versus',
        # Spanish
        'precio', 'precios', 'costo', 'costos', 'cuánto', 'cuanto cuesta',
        'tarifa', 'tarifas', 'comparar',
        # Russian
        'цена', 'цены', 'стоимость', 'сколько стоит', 'сколько',
        'тариф', 'сравнить',
        # Portuguese
        'preço', 'preços', 'custo', 'custos', 'quanto custa',
    ]

    def _looks_like_pricing(self, message: str) -> bool:
        """
        Check if message is a pricing query.

        Used to distinguish "how much is a cleaning?" (pricing) from
        "book me a cleaning" (scheduling).
        """
        m = message.lower()
        return any(k in m for k in self.PRICING_KEYWORDS)

    def _is_contact_info_submission(self, message: str) -> bool:
        """
        Check if user is providing their contact info (not asking for clinic's).

        IMPORTANT (Opinion 4): Distinguish "my phone" from "your phone number".
        "I need your help with my phone" should NOT trigger this - but we check
        for scheduling intent first anyway.
        """
        m = message.lower()
        patterns = [
            'my phone', 'my number', 'you can reach me', 'my name is',
            'mi teléfono', 'mi nombre', 'me llamo',
            'мой телефон', 'меня зовут', 'мой номер',
            'meu telefone', 'meu nome',
        ]
        return any(p in m for p in patterns)

    def _build_graph(self) -> StateGraph:
        """
        Build healthcare-specific workflow graph with supervisor routing.

        Phase 2 Enhanced Architecture:
        - Guardrail node runs BEFORE supervisor (security-first)
        - Language detection moved inside graph
        - Session init handles TTL for pending actions
        - Simple answer agent for fast FAQ path
        - Hydration with parallel context fetch
        - Plan-then-Execute for scheduling operations

        Phase 3: Unified Graph-Gateway Architecture
        - Supervisor node replaces fragmented intent routing
        - Routes to scheduling_agent (appointment) or info_agent (FAQ/price/general)
        - All paths go through phi_redact before exit
        """
        # CRITICAL: Create StateGraph with HealthcareConversationState, not base class
        # This ensures all healthcare fields (action_plan, flow_state, etc.) are available
        from langgraph.graph import StateGraph, END
        workflow = StateGraph(HealthcareConversationState)

        # Add only the nodes we actually use in healthcare flow
        # (entry, process, generate_response, exit are used; intent_classify is NOT - we use supervisor)
        workflow.add_node("entry", self.entry_node)
        workflow.add_node("process", self.process_node)
        workflow.add_node("generate_response", self.generate_response_node)
        workflow.add_node("exit", self.exit_node)

        # Optional memory/RAG nodes from base
        if self.enable_rag:
            workflow.add_node("knowledge_retrieve", self.knowledge_retrieve_node)

        # ==============================================
        # Phase 2: New nodes
        # ==============================================
        workflow.add_node("guardrail", self.guardrail_node)
        workflow.add_node("language_detect", self.language_detect_node)
        workflow.add_node("session_init", self.session_init_node)
        workflow.add_node("hydrate_context", self.hydrate_context_node)
        workflow.add_node("simple_answer", self.simple_answer_node)
        workflow.add_node("booking_extractor", self.booking_info_extractor_node)  # Phase 4: Extract booking info before planning
        workflow.add_node("planner", self.planner_node)
        workflow.add_node("executor", self.executor_node)

        # ==============================================
        # Existing healthcare nodes
        # ==============================================
        workflow.add_node("phi_check", self.phi_check_node)
        workflow.add_node("emergency_check", self.emergency_check_node)
        workflow.add_node("phi_redact", self.phi_redact_node)

        # NEW: Supervisor node replaces fragmented routing (Phase 3)
        workflow.add_node("supervisor", self.supervisor_node)

        # Specialized agent nodes - split into dynamic (tools) and static (cached FAQ)
        workflow.add_node("dynamic_info_agent", self.dynamic_info_agent_node)  # Uses tools for prices/availability
        workflow.add_node("static_info_agent", self.static_info_agent_node)  # Fast-path for cached FAQ
        # Note: Legacy nodes (appointment_handler, price_query, faq_lookup, insurance_verify, info_agent)
        # were replaced - Phase 2 uses planner/executor for scheduling, dynamic/static info agents for queries

        # ==============================================
        # Phase 2: Enhanced flow with guardrail FIRST
        # Entry → Language Detect → Session Init → Guardrail → Hydrate → Simple Answer → ...
        # ==============================================

        # Entry starts the enhanced pipeline
        workflow.add_edge("entry", "language_detect")
        workflow.add_edge("language_detect", "session_init")
        workflow.add_edge("session_init", "guardrail")

        # Guardrail routing - escalate immediately for emergencies
        workflow.add_conditional_edges(
            "guardrail",
            self.guardrail_router,
            {
                "escalate": "phi_redact",  # Emergency goes straight to redact then exit
                "continue": "hydrate_context"
            }
        )

        # After hydration, try simple answer first (fast path)
        workflow.add_edge("hydrate_context", "simple_answer")

        # Simple answer routing - exit early if FAQ handled
        workflow.add_conditional_edges(
            "simple_answer",
            self.simple_answer_router,
            {
                "exit": "phi_redact",  # Fast path handled, skip to exit
                "continue": "emergency_check"  # Continue to existing flow
            }
        )

        # Legacy emergency check (now secondary to guardrail)
        if self.enable_emergency_detection:
            workflow.add_conditional_edges(
                "emergency_check",
                self.emergency_router,
                {
                    "emergency": "phi_redact",  # Emergency goes straight to redact then exit
                    "normal": "phi_check"
                }
            )
        else:
            workflow.add_edge("emergency_check", "phi_check")

        # PHI check leads to supervisor
        workflow.add_edge("phi_check", "supervisor")

        # Supervisor routing (replaces intent_classify routing)
        # Updated: 4-way routing with static_info vs dynamic_info split
        # Phase 4: scheduling now goes through booking_extractor first
        workflow.add_conditional_edges(
            "supervisor",
            self.supervisor_router,
            {
                "scheduling": "booking_extractor",  # Phase 4: Extract info before planning
                "dynamic_info": "dynamic_info_agent",  # New: uses tools for price/availability
                "static_info": "static_info_agent",  # New: fast-path for cached FAQ
                "exit": "phi_redact",
            }
        )

        # Phase 4: Add edge from booking_extractor to planner
        workflow.add_edge("booking_extractor", "planner")

        # ==============================================
        # Phase 2: Plan-then-Execute for scheduling
        # ==============================================
        workflow.add_edge("planner", "executor")

        # Executor routing - handle confirmation, replanning, or completion
        workflow.add_conditional_edges(
            "executor",
            self.executor_router,
            {
                "exit": "phi_redact",  # Awaiting confirmation or complete
                "replan": "planner",  # Need to replan
                "error": "process",  # Error handling
                "continue": "executor",  # More steps to execute
                "complete": "process",  # All done, generate response
            }
        )

        # ==============================================
        # Agent flows
        # ==============================================
        # Dynamic info agent uses tools, then goes to process for response generation
        workflow.add_edge("dynamic_info_agent", "process")
        # Static info agent: may reroute to scheduling if booking intent detected
        # Phase 4: Replaced fixed edge with conditional for force_reroute handling
        workflow.add_conditional_edges(
            "static_info_agent",
            self.static_info_router,
            {
                "booking_extractor": "booking_extractor",  # Reroute to scheduling via extractor
                "phi_redact": "phi_redact",  # Normal path
            }
        )

        # FIX: Insert phi_redact BETWEEN process and generate_response
        # This avoids conflicting edges from generate_response
        # Flow: process -> phi_redact -> generate_response -> exit
        # (Base class adds generate_response -> exit or compliance_audit)

        # Remove the base class edge from process to generate_response and reroute
        # by adding our own edges
        workflow.add_edge("process", "phi_redact")
        workflow.add_edge("phi_redact", "generate_response")

        # Add generate_response -> exit edge (was in base class)
        # Healthcare always uses HIPAA compliance, so route through audit
        workflow.add_edge("generate_response", "exit")

        # Final edge to END and entry point
        workflow.add_edge("exit", END)
        workflow.set_entry_point("entry")

        return workflow

    async def phi_check_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """Check message for PHI and de-identify if needed"""
        logger.debug(f"PHI check - session: {state['session_id']}")

        if self.phi_middleware:
            # Check for PHI
            contains_phi, phi_tokens = await self.phi_middleware.detect_phi(state['message'])

            if contains_phi:
                # De-identify the message
                de_identified = await self.phi_middleware.de_identify(
                    state['message'],
                    phi_tokens
                )
                state['de_identified_message'] = de_identified
                state['phi_tokens'] = phi_tokens
                state['contains_phi'] = True
            else:
                state['contains_phi'] = False
        else:
            # No PHI middleware - assume no PHI (development mode)
            state['contains_phi'] = False

        state['audit_trail'].append({
            "node": "phi_check",
            "timestamp": datetime.utcnow().isoformat(),
            "contains_phi": state['contains_phi']
        })

        return state

    async def emergency_check_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """Check for emergency keywords requiring immediate attention"""
        logger.debug(f"Emergency check - session: {state['session_id']}")

        emergency_keywords = [
            'emergency', 'urgent', 'severe pain', 'bleeding',
            'chest pain', 'difficulty breathing', '911'
        ]

        message_lower = state['message'].lower()
        is_emergency = any(keyword in message_lower for keyword in emergency_keywords)

        if is_emergency:
            state['response'] = (
                "This seems to be an emergency situation. "
                "Please call 911 or go to your nearest emergency room immediately. "
                "For immediate dental emergencies, call our emergency line: 1-800-URGENT-DENTAL"
            )
            state['should_end'] = True

        state['audit_trail'].append({
            "node": "emergency_check",
            "timestamp": datetime.utcnow().isoformat(),
            "is_emergency": is_emergency
        })

        return state

    async def appointment_handler_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """Handle appointment booking requests using LLM factory with tool calling"""
        logger.debug(f"Appointment handler - session: {state['session_id']}")

        # Try LLM-based tool calling first if factory is available
        if self.llm_factory:
            try:
                # Import canonical schemas (replaces tool_definitions.py)
                from app.services.orchestrator.tools.canonical_schemas import (
                    get_openai_tool_schema,
                    validate_tool_call,
                    BookAppointmentInput,
                    QueryPricesInput,
                )

                # Get OpenAI tool schemas from canonical Pydantic models
                appointment_tool_schema = get_openai_tool_schema("book_appointment")
                price_tool_schema = get_openai_tool_schema("query_prices")

                # Prepare messages with appointment context
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a medical appointment assistant. Use tools to book appointments and check pricing. "
                            "Always extract appointment details (date, time, service type) before booking. "
                            f"Patient ID is: {state.get('patient_id')} - use this for bookings."
                        )
                    },
                    {"role": "user", "content": state['message']}
                ]

                # Call LLM with tools (schemas auto-generated from Pydantic)
                # tool_choice='required' forces tool use for scheduling accuracy
                response = await self.llm_factory.generate_with_tools(
                    messages=messages,
                    tools=[appointment_tool_schema, price_tool_schema],
                    model=self.primary_model,
                    temperature=0.3,  # Lower temp for tool calling accuracy
                    tool_choice='required'  # FORCE tool use - don't let LLM skip to text
                )

                # Execute tools if called - with Pydantic validation
                if response.tool_calls:
                    for tool_call in response.tool_calls:
                        if tool_call.name == "query_prices" or tool_call.name == "query_service_prices":
                            # Validate and execute price query
                            if self.price_query_tool:
                                validated = validate_tool_call("query_prices", tool_call.arguments)
                                result = await self.price_query_tool.get_services_by_query(
                                    query=validated.query,
                                    category=validated.category,
                                    limit=validated.limit
                                )
                                state['metadata']['tool_results'] = result

                        elif tool_call.name == "book_appointment" and self.appointment_tools:
                            # Inject patient_id from session context (Semantic Adapter pattern)
                            args = dict(tool_call.arguments)
                            if not args.get('patient_id'):
                                args['patient_id'] = state.get('patient_id')

                            # Validate arguments against canonical schema
                            validated = validate_tool_call("book_appointment", args)

                            # Execute booking with validated Pydantic model
                            booking_result = await self.appointment_tools.book_appointment(
                                patient_id=validated.patient_id,
                                doctor_id=validated.doctor_id,
                                datetime_str=validated.datetime_str,
                                appointment_type=validated.appointment_type,
                                duration_minutes=validated.duration_minutes,
                                notes=validated.notes
                            )
                            state['metadata']['booking_result'] = booking_result

                    # Store response from LLM
                    state['response'] = response.content or "Processing appointment request..."
                    state['audit_trail'].append({
                        "node": "appointment_handler",
                        "timestamp": datetime.utcnow().isoformat(),
                        "tool_calls": len(response.tool_calls),
                        "llm_model": response.model
                    })
                    return state

            except Exception as e:
                logger.warning(f"LLM tool calling failed: {e}, falling back to keyword extraction")

        # Fallback to keyword-based extraction
        # Extract appointment details from message
        message = state.get('message', '').lower()

        # Determine appointment type from message
        if 'cleaning' in message or 'hygiene' in message:
            state['appointment_type'] = 'dental_cleaning'
        elif 'checkup' in message or 'check-up' in message:
            state['appointment_type'] = 'checkup'
        elif 'emergency' in message or 'urgent' in message:
            state['appointment_type'] = 'emergency'
        elif 'consultation' in message:
            state['appointment_type'] = 'consultation'
        else:
            state['appointment_type'] = 'general'

        # Detect user action
        action = 'book'  # default
        if any(word in message for word in ['cancel', 'cancellation']):
            action = 'cancel'
        elif any(word in message for word in ['reschedule', 'change', 'move']):
            action = 'reschedule'

        # Initialize appointment query context
        appointment_query = {
            'action': action,
            'appointment_type': state['appointment_type'],
            'preferred_date': state.get('preferred_date'),
            'preferred_time': state.get('preferred_time'),
            'doctor_id': state.get('doctor_id'),
            'available_slots': [],
            'has_availability': False,
            'error': None,
        }

        # Gather availability data if booking
        if action == 'book':
            if self.appointment_tools:
                try:
                    availability_result = await self.appointment_tools.check_availability(
                        doctor_id=state.get('doctor_id'),
                        date=state.get('preferred_date'),
                        appointment_type=state['appointment_type'],
                        duration_minutes=30 if state['appointment_type'] == 'checkup' else 60
                    )

                    if availability_result['success'] and availability_result.get('available_slots'):
                        slots = availability_result['available_slots']
                        appointment_query['available_slots'] = slots
                        appointment_query['has_availability'] = True
                        state['context']['available_slots'] = slots
                except Exception as e:
                    logger.warning(f"Error checking availability: {e}")
                    appointment_query['error'] = str(e)

            elif self.appointment_service:
                try:
                    available_slots = await self.appointment_service.get_available_slots(
                        appointment_type=state['appointment_type'],
                        date_range=7
                    )
                    if available_slots:
                        appointment_query['available_slots'] = available_slots
                        appointment_query['has_availability'] = True
                        state['context']['available_slots'] = available_slots
                except Exception as e:
                    logger.warning(f"Error checking availability: {e}")
                    appointment_query['error'] = str(e)

        # Store in context for LLM to generate response
        state['context']['appointment_query'] = appointment_query
        logger.info(f"[appointment_handler] Stored context: action={action}, has_availability={appointment_query['has_availability']}")

        # DO NOT set state['response'] - let process_node handle via LLM

        state['audit_trail'].append({
            "node": "appointment_handler",
            "timestamp": datetime.utcnow().isoformat(),
            "appointment_type": state['appointment_type']
        })

        return state

    async def price_query_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Enrich context with price query results for LLM.

        This node ONLY searches and stores data - it does NOT generate responses.
        The process_node will use this data to generate a natural LLM response.

        Pattern: Nodes enrich context → LLM generates response
        """
        logger.debug(f"Price query - session: {state['session_id']}")

        language = state.get('metadata', {}).get('language', 'en')

        # Get cached services from context (populated by hydration step)
        cached_services = state.get('context', {}).get('clinic_services', [])

        if not cached_services:
            logger.warning(f"No cached services available for price query")
            state['context']['price_query'] = {
                'success': False,
                'error': 'no_services_cached',
                'results': []
            }
            state['audit_trail'].append({
                "node": "price_query",
                "timestamp": datetime.utcnow().isoformat(),
                "services_found": 0,
                "cache_hit": False
            })
            return state

        logger.info(f"[price_query] Searching {len(cached_services)} cached services (no DB call)")

        # Extract search terms from message
        message_lower = state.get('message', '').lower()
        search_terms = message_lower

        # Remove common price-related words in multiple languages
        # Use regex for Russian to handle morphological variants
        noise_patterns = [
            r'\bhow\b', r'\bmuch\b', r'\bis\b', r'\bthe\b', r'\bwhat\b', r'\bprice\b',
            r'\bcost\b', r'\bfee\b', r'\bof\b', r'\bfor\b', r'\ba\b', r'\ban\b',
            r'\bсколько\b', r'\bстои\w*\b', r'\bцен\w*\b', r'\bстоимост\w*\b',
            r'\bкак\w*\b', r'\bу\b', r'\bвас\b', r'\bскажи\b', r'\bскажите\b',
            r'\bcuánto\b', r'\bcuesta\b', r'\bprecio\b', r'\bcuanto\b',
            r'\bel\b', r'\bla\b', r'\blos\b', r'\blas\b', r'\bde\b', r'\bpara\b',
            r'[?,.!¿¡]'
        ]
        for pattern in noise_patterns:
            search_terms = re.sub(pattern, ' ', search_terms, flags=re.IGNORECASE)
        search_terms = ' '.join(search_terms.split()).strip()

        # Search cached services with multilingual support
        services = self._search_services_in_memory(cached_services, search_terms, language)

        # Store results in context for LLM to use
        # Format results with localized names for easier LLM consumption
        formatted_results = []
        for svc in services[:5]:  # Top 5 results
            formatted_results.append({
                'name': self._get_localized_field(svc, 'name', language),
                'price': svc.get('base_price') or svc.get('price'),
                'currency': svc.get('currency', 'USD'),
                'description': self._get_localized_field(svc, 'description', language),
                'duration_minutes': svc.get('duration_minutes'),
                'category': svc.get('category')
            })

        state['context']['price_query'] = {
            'success': True,
            'search_terms': search_terms,
            'results': formatted_results,
            'total_matches': len(services)
        }

        # DO NOT set state['response'] - let process_node (LLM) generate it

        state['audit_trail'].append({
            "node": "price_query",
            "timestamp": datetime.utcnow().isoformat(),
            "services_found": len(services),
            "search_terms": search_terms,
            "cache_hit": True,
            "cached_services_count": len(cached_services)
        })

        return state

    def _search_services_in_memory(
        self,
        services: list,
        query: str,
        language: str = 'en'
    ) -> list:
        """
        Search services in memory with multilingual support.

        Args:
            services: List of cached service dicts
            query: Search query (already cleaned)
            language: Language code for field priority

        Returns:
            List of matching services, sorted by relevance
        """
        if not query:
            # Return all services if no query
            return services[:10]

        query_lower = query.lower()
        query_words = query_lower.split()

        # Define field search priority by language
        name_fields = {
            'ru': ['name_ru', 'name', 'name_en'],
            'es': ['name_es', 'name', 'name_en'],
            'en': ['name_en', 'name'],
            'pt': ['name_pt', 'name', 'name_en'],
            'he': ['name_he', 'name', 'name_en'],
        }.get(language, ['name', 'name_en'])

        scored_results = []

        for service in services:
            score = 0
            matched_name = None

            # Check name fields
            for field in name_fields:
                value = service.get(field, '')
                if value:
                    value_lower = value.lower()

                    # Exact match
                    if query_lower == value_lower:
                        score = 100
                        matched_name = value
                        break

                    # Query contained in name
                    if query_lower in value_lower:
                        score = max(score, 80)
                        matched_name = value

                    # All query words found in name
                    if all(word in value_lower for word in query_words):
                        score = max(score, 70)
                        matched_name = value

                    # Any query word found
                    word_matches = sum(1 for word in query_words if word in value_lower)
                    if word_matches > 0:
                        word_score = 30 + (word_matches * 10)
                        if word_score > score:
                            score = word_score
                            matched_name = value

            # Also check category
            category = service.get('category', '').lower()
            if category and query_lower in category:
                score = max(score, 40)

            if score > 0:
                scored_results.append((score, service, matched_name))

        # Sort by score descending
        scored_results.sort(key=lambda x: x[0], reverse=True)

        return [s[1] for s in scored_results]

    def _get_localized_field(self, service: dict, field: str, language: str) -> str:
        """Get localized field value with fallback."""
        # Try localized field first
        localized_key = f"{field}_{language}"
        value = service.get(localized_key)
        if value:
            return value

        # Fallback to default field
        value = service.get(field)
        if value:
            return value

        # Fallback to English
        return service.get(f"{field}_en", '')

    async def faq_lookup_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Handle FAQ queries using the FAQ tool

        This node tries to answer the user's question using the FAQ database.
        If no suitable FAQ is found, it can fall back to RAG.
        """
        logger.debug(f"FAQ lookup - session: {state['session_id']}")

        if not self.faq_tool:
            logger.warning("FAQ tool not initialized, skipping")
            state['context']['faq_results'] = []
            state['context']['faq_success'] = False
            return state

        try:
            message = state.get('message', '')
            # Extract language from state if available
            language = state.get('metadata', {}).get('language', 'en')

            # Search FAQs
            faq_results = await self.faq_tool.search_faqs(
                query=message,
                language=language,
                limit=3,
                min_score=0.2  # Require decent match
            )

            # Store results in context
            state['context']['faq_results'] = faq_results
            state['context']['faq_success'] = (
                len(faq_results) > 0 and
                faq_results[0].get('relevance_score', 0) > 0.5
            )

            # Track in audit trail
            state['audit_trail'].append({
                "node": "faq_lookup",
                "timestamp": datetime.utcnow().isoformat(),
                "faqs_found": len(faq_results),
                "top_score": faq_results[0].get('relevance_score', 0) if faq_results else 0
            })

            # Don't set response here - let process_node generate it via LLM
            # The faq_results are stored in context for process_node to use
            if state['context']['faq_success']:
                logger.info(f"FAQ found with high confidence: {faq_results[0]['question']}")
            else:
                logger.info(f"FAQ match low confidence or no results, will try RAG fallback")

        except Exception as e:
            logger.error(f"FAQ lookup error: {e}", exc_info=True)
            state['context']['faq_results'] = []
            state['context']['faq_success'] = False
            # Don't set response - let process_node handle via LLM

        return state

    async def insurance_verify_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Gather insurance verification context for LLM.

        This node ONLY gathers and stores data - it does NOT generate responses.
        The process_node will use this data to generate a natural LLM response.

        Pattern: Nodes enrich context → LLM generates response
        """
        logger.debug(f"Insurance verification - session: {state['session_id']}")

        # Check for any provided insurance info in the message
        message = state.get('message', '').lower()
        has_provider = any(word in message for word in ['aetna', 'cigna', 'united', 'blue cross', 'kaiser', 'humana'])
        has_member_id = any(char.isdigit() for char in message) and len([c for c in message if c.isdigit()]) > 5

        # Store insurance context for LLM
        state['context']['insurance_query'] = {
            'action': 'verify',
            'has_provider_info': has_provider,
            'has_member_id': has_member_id,
            'verified': False,
            'needs_info': not (has_provider and has_member_id),
        }

        state['insurance_verified'] = False

        # DO NOT set state['response'] - let process_node handle via LLM
        logger.info(f"[insurance_verify] Stored context: needs_info={not (has_provider and has_member_id)}")

        state['audit_trail'].append({
            "node": "insurance_verify",
            "timestamp": datetime.utcnow().isoformat(),
            "verified": state['insurance_verified']
        })

        return state

    async def info_agent_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Unified info agent that handles FAQ, pricing, and general info queries.

        Phase 3: Combines faq_lookup and price_query logic into single agent.
        This node enriches context - process_node generates the response.

        Pattern: Nodes enrich context -> LLM generates response
        """
        logger.debug(f"Info agent - session: {state['session_id']}")

        message = state.get('message', '').lower()

        # Check for price-related queries
        price_keywords = ['price', 'cost', 'how much', 'cuanto', 'cuesta', 'precio',
                         'стоимость', 'сколько', 'цена', 'fee', 'charge']
        is_price_query = any(keyword in message for keyword in price_keywords)

        # Check for FAQ-type queries
        faq_keywords = ['hours', 'location', 'address', 'open', 'close', 'where',
                       'when', 'phone', 'parking', 'insurance', 'accept', 'horario',
                       'donde', 'часы', 'где', 'адрес', 'работаете']
        is_faq_query = any(keyword in message for keyword in faq_keywords)

        # Gather relevant context based on query type
        if is_price_query:
            # Delegate to price_query_node for service lookup
            await self.price_query_node(state)
            logger.info(f"[info_agent] Delegated to price_query_node")

        if is_faq_query:
            # Delegate to faq_lookup_node for FAQ search
            await self.faq_lookup_node(state)
            logger.info(f"[info_agent] Delegated to faq_lookup_node")

        # Mark that this was handled by info_agent
        state['context']['info_agent_handled'] = True
        state['context']['query_type'] = 'price' if is_price_query else ('faq' if is_faq_query else 'general')

        state['audit_trail'].append({
            "node": "info_agent",
            "timestamp": datetime.utcnow().isoformat(),
            "is_price_query": is_price_query,
            "is_faq_query": is_faq_query
        })

        return state

    async def dynamic_info_agent_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Handle dynamic info queries using LLM tool calling.

        Forces tool_choice='required' to ensure backend truth is fetched.
        Tools: query_prices (read-only)

        This replaces pure-text responses for queries that need live data.

        Phase 5 Enhancement: Strict tool-mandatory enforcement for pricing queries.
        The agent MUST call query_prices for any pricing question - no hallucination allowed.
        """
        logger.info(f"[dynamic_info_agent] Processing with tools - session: {state['session_id'][:8]}")

        message = state.get('message', '')
        message_lower = message.lower()
        language = state.get('detected_language', 'en')

        # =========================================================================
        # Phase 5 (5.1): Detect pricing intent - MUST call query_prices
        # =========================================================================
        PRICING_KEYWORDS = [
            'price', 'cost', 'how much', 'fee', 'charge', 'rate', 'expensive',
            'precio', 'costo', 'cuánto', 'cuanto cuesta',
            'цена', 'стоимость', 'сколько стоит', 'сколько',
            'compare', 'comparison', 'vs', 'versus', 'cheaper', 'affordable',
        ]
        is_pricing_query = any(kw in message_lower for kw in PRICING_KEYWORDS)

        if is_pricing_query:
            logger.info("[dynamic_info_agent] PRICING QUERY DETECTED - tool call mandatory")

        # Build system prompt for tool selection
        system_prompt = f"""You are a healthcare clinic assistant. Answer the user's question using the available tools.

For pricing questions, use the query_prices tool to get accurate, up-to-date prices.
Always respond in {language} language.

Clinic context:
{state.get('context', {}).get('clinic_profile', {})}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message}
        ]

        # Get price tool schema
        try:
            from app.services.orchestrator.tools.canonical_schemas import get_openai_tool_schema
            price_tool_schema = get_openai_tool_schema("query_prices")
            tools = [price_tool_schema]
        except Exception as e:
            logger.warning(f"[dynamic_info_agent] Failed to load tool schemas: {e}")
            # Fall back to legacy info_agent behavior
            await self.info_agent_node(state)
            return state

        tool_calls_count = 0

        if self.llm_factory:
            try:
                response = await self.llm_factory.generate_with_tools(
                    messages=messages,
                    tools=tools,
                    model=self.primary_model,
                    temperature=0.3,
                    tool_choice='required'  # FORCE tool use for accuracy
                )

                # Execute any tool calls
                if response.tool_calls:
                    tool_calls_count = len(response.tool_calls)
                    for tool_call in response.tool_calls:
                        result = await self._execute_dynamic_info_tool(tool_call, state)
                        state['context']['tool_results'] = state['context'].get('tool_results', {})
                        state['context']['tool_results'][tool_call.name] = result

                        # =========================================================================
                        # Phase 5: Track tool calls in tools_actually_called
                        # =========================================================================
                        tools_called = state.get('tools_actually_called', []) or []
                        tools_called.append(tool_call.name)
                        state['tools_actually_called'] = tools_called
                        logger.info(f"[dynamic_info_agent] Tool {tool_call.name} executed, result: {str(result)[:100]}")

                    logger.info(f"[dynamic_info_agent] Executed {tool_calls_count} tool calls")
                else:
                    # =========================================================================
                    # Phase 5: LLM didn't call tools despite tool_choice='required'
                    # For pricing queries, force direct tool call as fallback
                    # =========================================================================
                    if is_pricing_query:
                        logger.warning("[dynamic_info_agent] LLM skipped tool call for pricing - forcing direct call")
                        services = self._extract_services_from_message(message_lower)
                        if self.price_query_tool:
                            try:
                                result = await self.price_query_tool.get_services_by_query(
                                    query=message,
                                    limit=5
                                )
                                state['context']['price_query'] = {
                                    'success': True,
                                    'results': result,
                                    'query': message,
                                    'forced': True
                                }
                                tools_called = state.get('tools_actually_called', []) or []
                                tools_called.append('query_prices')
                                state['tools_actually_called'] = tools_called
                                logger.info(f"[dynamic_info_agent] Forced query_prices result: {str(result)[:100]}")
                            except Exception as e:
                                logger.error(f"[dynamic_info_agent] Forced price query failed: {e}")

                state['context']['dynamic_info_handled'] = True

            except Exception as e:
                logger.error(f"[dynamic_info_agent] Tool calling failed: {e}")
                # =========================================================================
                # Phase 5: For pricing queries, try direct tool call even on LLM failure
                # =========================================================================
                if is_pricing_query and self.price_query_tool:
                    logger.info("[dynamic_info_agent] LLM failed but pricing query - trying direct tool call")
                    try:
                        result = await self.price_query_tool.get_services_by_query(query=message, limit=5)
                        state['context']['price_query'] = {'success': True, 'results': result, 'forced': True}
                        tools_called = state.get('tools_actually_called', []) or []
                        tools_called.append('query_prices')
                        state['tools_actually_called'] = tools_called
                    except Exception as e2:
                        logger.error(f"[dynamic_info_agent] Direct price query also failed: {e2}")
                        await self.info_agent_node(state)
                else:
                    # Fallback to legacy enrichment pattern
                    await self.info_agent_node(state)
        else:
            # No LLM factory - use legacy pattern for non-pricing, direct call for pricing
            if is_pricing_query and self.price_query_tool:
                logger.info("[dynamic_info_agent] No LLM but pricing query - direct tool call")
                try:
                    result = await self.price_query_tool.get_services_by_query(query=message, limit=5)
                    state['context']['price_query'] = {'success': True, 'results': result, 'forced': True}
                    tools_called = state.get('tools_actually_called', []) or []
                    tools_called.append('query_prices')
                    state['tools_actually_called'] = tools_called
                except Exception as e:
                    logger.error(f"[dynamic_info_agent] Direct price query failed: {e}")
                    await self.info_agent_node(state)
            else:
                await self.info_agent_node(state)

        state['audit_trail'].append({
            "node": "dynamic_info_agent",
            "timestamp": datetime.utcnow().isoformat(),
            "tool_calls": tool_calls_count,
        })

        return state

    async def _execute_dynamic_info_tool(self, tool_call, state: HealthcareConversationState) -> dict:
        """Execute a dynamic info tool call and return results."""
        tool_name = tool_call.name
        arguments = tool_call.arguments if hasattr(tool_call, 'arguments') else {}

        logger.info(f"[dynamic_info_agent] Executing tool: {tool_name} with args: {arguments}")

        if tool_name == "query_prices" and self.price_query_tool:
            try:
                result = await self.price_query_tool.get_services_by_query(
                    query=arguments.get('query', state.get('message', '')),
                    category=arguments.get('category'),
                    limit=arguments.get('limit', 5)
                )
                # Store in context for process_node
                state['context']['price_query'] = {
                    'success': True,
                    'results': result,
                    'query': arguments.get('query', '')
                }
                return {'success': True, 'results': result}
            except Exception as e:
                logger.error(f"[dynamic_info_agent] Price query failed: {e}")
                return {'success': False, 'error': str(e)}

        logger.warning(f"[dynamic_info_agent] Unknown tool: {tool_name}")
        return {'success': False, 'error': f'Unknown tool: {tool_name}'}

    async def static_info_agent_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Fast-path for static FAQ queries.
        Uses cached clinic profile, no LLM needed.

        Handles: hours, location, phone, address, parking
        Response is set directly from cached data.

        PHASE 4 FIX: Short-circuits to scheduling if booking intent detected.
        This prevents "Book me... phone 555-123" from returning clinic phone.
        """
        logger.info(f"[static_info_agent] Fast-path processing - session: {state['session_id'][:8]}")

        message = state.get('message', '')
        message_lower = message.lower()

        # CRITICAL FIX (Phase 4): If message has scheduling intent, DO NOT handle here
        # This prevents "Book me... phone 555-123" from returning clinic phone
        if self._looks_like_scheduling(message_lower) or self._is_contact_info_submission(message_lower):
            logger.info(f"[static_info_agent] Detected scheduling intent, forcing reroute to scheduling")
            state["static_info_skipped_due_to_scheduling"] = True
            state["force_reroute_to"] = "scheduling"

            state['audit_trail'].append({
                "node": "static_info_agent",
                "timestamp": datetime.utcnow().isoformat(),
                "action": "reroute_to_scheduling",
                "reason": "scheduling_intent_detected"
            })
            return state

        language = state.get('detected_language', 'en')
        clinic = state.get('context', {}).get('clinic_profile', {})

        response = None

        # Check for hours query
        if any(p in message_lower for p in ['hours', 'open', 'close', 'when', 'horario', 'часы', 'работаете']):
            hours = clinic.get('business_hours', clinic.get('hours', 'Please call for hours'))
            templates = {
                'en': f"Our hours are: {hours}",
                'es': f"Nuestro horario es: {hours}",
                'ru': f"Наши часы работы: {hours}",
                'pt': f"Nosso horário é: {hours}",
                'he': f"שעות הפעילות שלנו: {hours}",
            }
            response = templates.get(language, templates['en'])

        # Check for location query
        elif any(p in message_lower for p in ['address', 'location', 'where', 'dirección', 'donde', 'адрес', 'где']):
            address = clinic.get('address', clinic.get('location', 'Please call for address'))
            templates = {
                'en': f"We're located at: {address}",
                'es': f"Estamos ubicados en: {address}",
                'ru': f"Мы находимся по адресу: {address}",
                'pt': f"Estamos localizados em: {address}",
                'he': f"אנחנו נמצאים ב: {address}",
            }
            response = templates.get(language, templates['en'])

        # Check for phone query
        elif any(p in message_lower for p in ['phone', 'call', 'number', 'teléfono', 'телефон', 'номер']):
            phone = clinic.get('phone', clinic.get('phone_number', 'Please check our website'))
            templates = {
                'en': f"You can reach us at: {phone}",
                'es': f"Puede contactarnos al: {phone}",
                'ru': f"Наш телефон: {phone}",
                'pt': f"Você pode nos ligar em: {phone}",
                'he': f"ניתן ליצור קשר בטלפון: {phone}",
            }
            response = templates.get(language, templates['en'])

        # Check for parking query
        elif any(p in message_lower for p in ['parking', 'park', 'estacionamiento', 'парковка']):
            parking = clinic.get('parking_info', 'Free parking available on-site')
            templates = {
                'en': f"Parking information: {parking}",
                'es': f"Información de estacionamiento: {parking}",
                'ru': f"Информация о парковке: {parking}",
            }
            response = templates.get(language, templates['en'])

        if response:
            state['response'] = response
            state['fast_path'] = True
            state['lane'] = 'static_info'
            logger.info(f"[static_info_agent] Fast-path response generated")
        else:
            # Fallback: try FAQ lookup
            await self.faq_lookup_node(state)
            faq_results = state.get('context', {}).get('faq_results', [])
            if faq_results and faq_results[0].get('relevance_score', 0) > 0.5:
                state['response'] = faq_results[0].get('answer', '')
                state['fast_path'] = True
                state['lane'] = 'static_info'
            else:
                # No cached answer found - should not happen if routing is correct
                logger.warning(f"[static_info_agent] No static answer found for: {message[:50]}")
                state['response'] = "I'm not sure about that. Would you like me to help you book an appointment instead?"

        state['audit_trail'].append({
            "node": "static_info_agent",
            "timestamp": datetime.utcnow().isoformat(),
            "fast_path": state.get('fast_path', False),
        })

        return state

    async def phi_redact_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """Redact PHI from response before sending"""
        logger.debug(f"PHI redaction - session: {state['session_id']}")

        if self.phi_middleware and state.get('response'):
            # Check response for any PHI
            contains_phi, phi_tokens = await self.phi_middleware.detect_phi(state['response'])

            if contains_phi:
                # Redact PHI from response
                state['response'] = await self.phi_middleware.redact(
                    state['response'],
                    phi_tokens
                )

        state['audit_trail'].append({
            "node": "phi_redact",
            "timestamp": datetime.utcnow().isoformat()
        })

        return state

    def emergency_router(self, state: HealthcareConversationState) -> str:
        """Route based on emergency detection"""
        # Check if emergency was detected
        for entry in state['audit_trail']:
            if entry.get('node') == 'emergency_check' and entry.get('is_emergency'):
                return "emergency"
        return "normal"

    async def supervisor_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Central routing decision with few-shot examples.

        ENHANCEMENT (Expert Opinions 1 & 2):
        - 5+ grounded examples to reduce hallucination from 10% to <2%
        - Handles ambiguous cases like "Do you have availability for a root canal next week?"

        Routes to:
        - "scheduling" - Appointment booking, availability, rescheduling, cancellation
        - "info" - FAQ, pricing, hours, location, insurance, service details
        - "exit" - Simple acknowledgments, goodbyes, no response needed
        """
        logger.debug(f"Supervisor node - session: {state['session_id']}")

        message = state["messages"][-1].content if state.get("messages") else state.get("message", "")
        flow_state = state.get("flow_state", "idle")
        active_task = state.get("active_task")

        # Log incoming state for debugging
        logger.info(f"[supervisor] Input: flow_state={flow_state}, active_task={active_task}, message='{message[:50]}...'")

        # =========================================================================
        # CRITICAL (Opinions 1,2,3,4): STATE-AWARE ROUTING
        # If we're awaiting a clarification response, BYPASS classification entirely
        # and force routing back to scheduling/booking_extractor
        # =========================================================================
        if state.get("awaiting_patient_identification") or state.get("awaiting_datetime"):
            logger.info(f"[supervisor] Awaiting slot filling, bypassing classification -> scheduling")
            state["next_agent"] = "scheduling"
            state["supervisor_forced_scheduling"] = True

            state['audit_trail'].append({
                "node": "supervisor",
                "timestamp": datetime.utcnow().isoformat(),
                "decision": "scheduling",
                "reason": "awaiting_clarification_response",
                "awaiting_patient": state.get("awaiting_patient_identification"),
                "awaiting_datetime": state.get("awaiting_datetime"),
            })
            return state

        # CRITICAL FIX: If already in scheduling flow, short confirmations MUST stay in scheduling
        # This handles "Да", "Yes", "Ok", "Sí" when user is confirming a slot
        message_lower = message.lower().strip()
        confirmation_words = ['да', 'yes', 'ok', 'okay', 'sure', 'sí', 'si', 'хорошо', 'ладно', 'давай', 'конечно', 'угу']
        is_short_confirmation = message_lower in confirmation_words or len(message_lower) <= 5

        if flow_state == FlowState.SCHEDULING.value and is_short_confirmation:
            # User is confirming something in scheduling flow - stay in scheduling
            logger.info(f"[supervisor] Keeping in scheduling flow: short confirmation '{message}' in scheduling state")
            state["next_agent"] = "scheduling"
            state['audit_trail'].append({
                "node": "supervisor",
                "timestamp": datetime.utcnow().isoformat(),
                "decision": "scheduling",
                "reason": "short_confirmation_in_scheduling_flow",
                "flow_state": flow_state
            })
            return state

        # Few-shot supervisor prompt (reduces hallucination 10% -> <2%)
        # Updated: 4-way routing to separate static FAQ from dynamic queries requiring tools
        supervisor_prompt = f"""You are a routing supervisor for a healthcare clinic assistant.

Current state: {flow_state}
Active task: {active_task}

CRITICAL RULE: If flow_state is "scheduling", user is in an active booking conversation.
Short responses like "yes", "да", "ok", numbers, or times should STAY in scheduling.

Route this message to the appropriate agent:
- "scheduling" - Appointment booking, rescheduling, cancellation, PAIN/SYMPTOMS (need to see doctor), OR any response while in scheduling flow
- "dynamic_info" - Pricing queries, availability checks, "do you have X?", capacity questions (requires backend lookup)
- "static_info" - Static FAQ: hours, location, phone, address, parking (cached clinic info)
- "exit" - Explicit goodbyes like "bye", "до свидания", "thanks bye" (NOT simple "ok" or "да")

EXAMPLES (follow these patterns):
User: "How much is a filling?" -> dynamic_info (needs price lookup)
User: "What's the cost of a cleaning?" -> dynamic_info (needs price lookup)
User: "Do you have availability tomorrow?" -> dynamic_info (needs availability check)
User: "What are your hours?" -> static_info (cached in clinic profile)
User: "Where are you located?" -> static_info (cached address)
User: "What's your phone number?" -> static_info (cached phone)
User: "I need to come in on Tuesday" -> scheduling
User: "I want to book an appointment" -> scheduling (intent is booking)
User: "My tooth hurts" -> scheduling (pain = need to see doctor = booking)
User: "У меня болит зуб" -> scheduling (Russian: my tooth hurts = needs appointment)
User: "I'm in pain" -> scheduling (needs urgent appointment)
User: "Thanks, bye!" -> exit
User: "Okay" (in scheduling flow) -> scheduling (continue current task)
User: "Да" (in scheduling flow) -> scheduling (Russian "yes" - continue booking)
User: "16" or "16:00" (in scheduling flow) -> scheduling (time selection)
User: "Actually, never mind" -> exit
User: "Can I book an appointment?" -> scheduling
User: "What services do you offer?" -> dynamic_info (needs service list)
User: "I want to cancel my appointment" -> scheduling
User: "Да" or "Yes" (confirming offered slot) -> scheduling
User: "острая боль" -> scheduling (acute pain = urgent appointment needed)
User: "Сколько стоит?" -> dynamic_info (Russian: how much does it cost?)
User: "Book me a cleaning tomorrow at 10am. My name is John Smith, phone 555-123-4567." -> scheduling (booking with contact info)
User: "I'd like to schedule an appointment. You can reach me at 555-000-1234." -> scheduling (scheduling + providing phone)
User: "Can I book for Tuesday? My phone is 555-987-6543." -> scheduling (booking + phone = STILL BOOKING)
User: "Запишите меня на чистку. Мой телефон 555-111-2222." -> scheduling (Russian: book me + my phone)
User: "Mi nombre es María, quiero una cita mañana." -> scheduling (Spanish: my name + want appointment)

User message: {message}

Respond with ONLY one word: scheduling, dynamic_info, static_info, or exit"""

        if self.llm_factory:
            try:
                from app.services.llm.tiers import ModelTier
                response = await self.llm_factory.generate_for_tier(
                    tier=ModelTier.ROUTING,
                    messages=[{"role": "system", "content": supervisor_prompt}],
                    temperature=0.1,
                    max_tokens=10,
                    clinic_id=self.clinic_id,
                    session_id=state.get('session_id'),
                )

                decision = response.content.strip().lower()
                # Clean up common variations - order matters (check specific before general)
                if "scheduling" in decision:
                    decision = "scheduling"
                elif "dynamic_info" in decision or "dynamic" in decision:
                    decision = "dynamic_info"
                elif "static_info" in decision or "static" in decision:
                    decision = "static_info"
                elif "exit" in decision:
                    decision = "exit"
                else:
                    # Changed: default to dynamic_info (uses tools) instead of info (text-only)
                    # This is safer for accuracy - better to call tools unnecessarily than miss data
                    decision = "dynamic_info"

                logger.info(f"[supervisor] Initial routing decision: {decision} for message: {message[:50]}...")

                # Phase 4: Post-hoc override - Scheduling intent takes priority over static_info/dynamic_info
                if decision in ("static_info", "dynamic_info") and self._looks_like_scheduling(message):
                    logger.info(f"[supervisor] OVERRIDE: {decision} -> scheduling (scheduling intent detected)")
                    decision = "scheduling"
                    state["supervisor_overrode_to_scheduling"] = True

            except Exception as e:
                logger.warning(f"Supervisor LLM call failed: {e}")
                # Phase 4: Smart fallback based on message content
                if self._looks_like_scheduling(message):
                    decision = "scheduling"
                    logger.info("[supervisor] Fallback: scheduling keywords detected")
                else:
                    decision = "dynamic_info"
                    logger.info("[supervisor] Fallback: defaulting to dynamic_info")
        else:
            # Fallback to keyword-based routing if no LLM factory
            message_lower = message.lower()

            # Keywords for each routing decision
            scheduling_keywords = ['book', 'appointment', 'schedule', 'reschedule', 'cancel', 'pain', 'hurts', 'болит']
            price_keywords = ['price', 'cost', 'how much', 'cuanto', 'стоимость', 'сколько', 'fee', 'charge']
            availability_keywords = ['available', 'availability', 'slot', 'opening', 'free', 'when can']
            static_keywords = ['hours', 'location', 'address', 'phone', 'parking', 'где', 'адрес', 'часы']
            exit_keywords = ['bye', 'goodbye', 'до свидания', 'thanks bye', 'adios']

            if any(word in message_lower for word in scheduling_keywords):
                decision = "scheduling"
            elif any(word in message_lower for word in price_keywords + availability_keywords):
                decision = "dynamic_info"
            elif any(word in message_lower for word in static_keywords):
                decision = "static_info"
            elif any(word in message_lower for word in exit_keywords):
                decision = "exit"
            else:
                # Changed: default to dynamic_info (uses tools) for safety
                decision = "dynamic_info"
            logger.info(f"[supervisor] Keyword-based routing: {decision}")

        state["next_agent"] = decision

        # Update flow_state based on decision
        if decision == "scheduling" and flow_state != FlowState.SCHEDULING.value:
            state["flow_state"] = FlowState.SCHEDULING.value
        elif decision in ["dynamic_info", "static_info"] and flow_state not in [FlowState.SCHEDULING.value]:
            state["flow_state"] = FlowState.INFO.value

        state['audit_trail'].append({
            "node": "supervisor",
            "timestamp": datetime.utcnow().isoformat(),
            "decision": decision,
            "flow_state": state.get("flow_state")
        })

        return state

    def supervisor_router(self, state: HealthcareConversationState) -> str:
        """Route based on supervisor decision."""
        # Default to dynamic_info (tool-using path) instead of info (text-only)
        return state.get("next_agent", "dynamic_info")

    # ========================================================================
    # Phase 2: New Nodes - Guardrail, Language Detection, Session Init
    # ========================================================================

    async def guardrail_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Security guardrail that runs BEFORE supervisor.

        Per Opinion 4, Section 4.1:
        "Guardrails are security checks that must run before any agentic logic."

        Checks:
        1. Emergency detection (911, immediate danger)
        2. PHI detection in outbound responses
        3. Tool call validation (block certain tools in certain states)
        4. Rate limiting / abuse detection

        Returns:
            State with guardrail_action set to 'escalate', 'restrict', or 'allow'
        """
        message = state.get('message', '').lower()
        logger.info(f"[guardrail] Checking message: '{message[:80]}...' session={state.get('session_id', 'unknown')[:8]}")

        guardrail_action = 'allow'
        blocked_tools = []
        escalation_reason = None
        is_emergency = False

        # 1. Emergency detection (highest priority)
        emergency_patterns = [
            # English
            '911', 'emergency', 'heart attack', 'cant breathe', "can't breathe",
            'severe bleeding', 'suicidal', 'overdose', 'dying', 'severe pain',
            # Russian (боль alone is too generic, need qualifier)
            'помогите', 'умираю', 'острая боль', 'сильная боль', 'скорая', 'очень больно', 'нестерпимая боль',
            # Spanish
            'emergencia', 'no puedo respirar', 'dolor severo', 'urgente', 'dolor agudo',
            # Portuguese
            'emergência', 'dor forte', 'não consigo respirar',
            # Hebrew
            'חירום', 'כאב חזק',
        ]
        matched_pattern = None
        for pattern in emergency_patterns:
            if pattern in message:
                matched_pattern = pattern
                break

        if matched_pattern:
            is_emergency = True
            guardrail_action = 'escalate'
            escalation_reason = 'emergency_detected'
            logger.warning(f"[guardrail] 🚨 EMERGENCY DETECTED: pattern='{matched_pattern}' in message: {message[:50]}...")

            # Generate emergency response in user's language
            language = state.get('language', 'en')
            emergency_responses = {
                'en': "I understand you're experiencing a medical emergency. Please call 911 immediately or go to your nearest emergency room. Your health is our priority, and emergency services are best equipped to help you right now.",
                'ru': "Я понимаю, что у вас неотложная медицинская ситуация. Пожалуйста, немедленно позвоните 911 или обратитесь в ближайшую скорую помощь. Ваше здоровье — наш приоритет, и экстренные службы лучше всего оснащены, чтобы помочь вам прямо сейчас.",
                'es': "Entiendo que está experimentando una emergencia médica. Por favor llame al 911 inmediatamente o vaya a la sala de emergencias más cercana. Su salud es nuestra prioridad.",
                'pt': "Entendo que você está passando por uma emergência médica. Por favor, ligue para o 192 imediatamente ou vá ao pronto-socorro mais próximo.",
                'he': "אני מבין שאתה חווה מצב חירום רפואי. אנא התקשר למד״א 101 מיד או גש לחדר מיון הקרוב אליך.",
            }
            state['response'] = emergency_responses.get(language, emergency_responses['en'])
            state['should_escalate'] = True

        # 2. PHI in outbound - check if we're about to send PHI
        # (This is checked after response generation in phi_redact_node)
        # Here we just mark if PHI was detected in incoming message
        phi_patterns = [
            r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
            r'\b\d{9}\b',  # 9-digit number (possible SSN)
        ]
        import re
        phi_detected = any(re.search(p, message) for p in phi_patterns)

        # 3. Tool restrictions based on state
        flow_state = state.get('flow_state', 'idle')
        if flow_state == 'escalated':
            # In escalated state, block all booking tools
            blocked_tools = ['book_appointment', 'cancel_appointment', 'reschedule_appointment']
            guardrail_action = 'restrict' if guardrail_action != 'escalate' else guardrail_action

        # 4. Abuse detection (simple rate check - in practice use Redis)
        # This is a placeholder - real implementation would check Redis

        # Calculate allowed tools
        all_tools = ['check_availability', 'book_appointment', 'cancel_appointment',
                     'query_prices', 'query_services', 'query_doctors']
        allowed_tools = [t for t in all_tools if t not in blocked_tools]

        # Update state
        state['is_emergency'] = is_emergency
        state['phi_detected'] = phi_detected
        state['allowed_tools'] = allowed_tools
        state['blocked_tools'] = blocked_tools
        state['guardrail_action'] = guardrail_action
        state['escalation_reason'] = escalation_reason

        state['audit_trail'].append({
            "node": "guardrail",
            "timestamp": datetime.utcnow().isoformat(),
            "action": guardrail_action,
            "is_emergency": is_emergency,
            "phi_detected": phi_detected,
            "blocked_tools": blocked_tools,
        })

        return state

    def guardrail_router(self, state: HealthcareConversationState) -> str:
        """Route based on guardrail action."""
        action = state.get('guardrail_action', 'allow')
        if action == 'escalate':
            return 'escalate'
        # 'restrict' and 'allow' both continue to supervisor
        return 'continue'

    async def language_detect_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Detect language from user message.

        Replaces RoutingStep language detection - now happens inside the graph.
        Uses character analysis for fast, reliable detection.
        """
        logger.debug(f"Language detect node - session: {state['session_id']}")

        message = state.get('message', '')
        language = 'en'  # Default

        if message:
            text_len = len(message)
            if text_len > 0:
                # Cyrillic → Russian
                cyrillic = sum(1 for c in message if '\u0400' <= c <= '\u04FF')
                if cyrillic / text_len > 0.3:
                    language = 'ru'
                else:
                    # Hebrew
                    hebrew = sum(1 for c in message if '\u0590' <= c <= '\u05FF')
                    if hebrew / text_len > 0.3:
                        language = 'he'
                    else:
                        # Spanish indicators
                        message_lower = message.lower()
                        spanish_markers = ['hola', 'gracias', 'señor', 'está', 'qué', 'cómo', 'buenos', 'buenas']
                        if any(m in message_lower for m in spanish_markers):
                            language = 'es'
                        else:
                            # Portuguese indicators
                            portuguese_markers = ['olá', 'obrigado', 'você', 'não', 'bom dia']
                            if any(m in message_lower for m in portuguese_markers):
                                language = 'pt'

        state['detected_language'] = language
        state['metadata']['language'] = language

        state['audit_trail'].append({
            "node": "language_detect",
            "timestamp": datetime.utcnow().isoformat(),
            "detected_language": language,
        })

        return state

    async def session_init_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Initialize session state, handle TTL for pending actions.

        Per Opinion 3 feedback:
        - Expired proposals are cleared on first message
        - TTL prevents "zombie" confirmations
        - Session rotation on timeout

        Checks:
        1. Is there a pending action proposal?
        2. Has it expired (TTL exceeded)?
        3. Is this message a confirmation/rejection?
        """
        logger.debug(f"Session init node - session: {state['session_id']}")

        # Check for pending action from previous turn
        pending_action = state.get('pending_action')
        pending_timestamp = state.get('pending_action_timestamp')
        awaiting_confirmation = state.get('awaiting_confirmation', False)
        proposal_expired = False
        user_confirmed = False

        if pending_action and pending_timestamp:
            try:
                # Parse timestamp
                ts = datetime.fromisoformat(pending_timestamp.replace('Z', '+00:00'))
                age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
                ttl = pending_action.get('ttl_seconds', 3600)

                if age_seconds > ttl:
                    # Proposal expired - clear it
                    proposal_expired = True
                    logger.info(f"[session_init] Pending action expired after {age_seconds:.0f}s (TTL: {ttl}s)")
                    state['pending_action'] = None
                    state['awaiting_confirmation'] = False
                    awaiting_confirmation = False

            except Exception as e:
                logger.warning(f"[session_init] Error checking pending action TTL: {e}")

        # If awaiting confirmation and message is short, check for confirmation
        if awaiting_confirmation and not proposal_expired:
            message = state.get('message', '').lower().strip()
            language = state.get('detected_language', 'en')
            confirmation = is_confirmation_response(message, language)

            if confirmation is True:
                user_confirmed = True
                logger.info(f"[session_init] User confirmed pending action")
            elif confirmation is False:
                # User rejected - clear pending action
                state['pending_action'] = None
                state['awaiting_confirmation'] = False
                logger.info(f"[session_init] User rejected pending action")

        # Update state
        state['pending_action_expired'] = proposal_expired
        state['user_confirmed'] = user_confirmed

        state['audit_trail'].append({
            "node": "session_init",
            "timestamp": datetime.utcnow().isoformat(),
            "awaiting_confirmation": awaiting_confirmation,
            "proposal_expired": proposal_expired,
            "user_confirmed": user_confirmed,
        })

        return state

    async def hydrate_context_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Session-aware context hydration with parallel fetch.

        Per Opinion 4, Section 2.2:
        "Parallel hydration of clinic, patient, services, and doctors context."

        Uses asyncio.gather for parallel DB calls:
        - Clinic profile
        - Patient profile (if phone number available)
        - Clinic services (cached with TTL)
        - Clinic doctors (cached with TTL)
        - Previous session summary (for continuity)
        """
        logger.debug(f"Hydrate context node - session: {state['session_id']}")

        # Skip if already hydrated (from pipeline context)
        if state.get('context_hydrated'):
            logger.debug("[hydrate_context] Already hydrated from pipeline, skipping")
            return state

        clinic_id = state.get('metadata', {}).get('clinic_id') or self.clinic_id
        phone_number = state.get('metadata', {}).get('phone_number')
        session_id = state.get('session_id')

        # Prepare fetch tasks
        async def fetch_clinic():
            if not clinic_id or not self.supabase_client:
                return {}
            try:
                result = self.supabase_client.table('clinics').select('*').eq('id', clinic_id).single().execute()
                return result.data if result.data else {}
            except Exception as e:
                logger.warning(f"[hydrate_context] Failed to fetch clinic: {e}")
                return {}

        async def fetch_patient():
            if not phone_number or not self.supabase_client:
                return {}
            try:
                result = self.supabase_client.table('patients').select('*').eq('phone', phone_number).single().execute()
                return result.data if result.data else {}
            except Exception as e:
                logger.debug(f"[hydrate_context] No patient found for phone: {e}")
                return {}

        async def fetch_services():
            if not clinic_id or not self.supabase_client:
                return []
            try:
                result = self.supabase_client.table('services').select('*').eq('clinic_id', clinic_id).execute()
                return result.data if result.data else []
            except Exception as e:
                logger.warning(f"[hydrate_context] Failed to fetch services: {e}")
                return []

        async def fetch_doctors():
            if not clinic_id or not self.supabase_client:
                return []
            try:
                result = self.supabase_client.table('doctors').select('*').eq('clinic_id', clinic_id).execute()
                return result.data if result.data else []
            except Exception as e:
                logger.warning(f"[hydrate_context] Failed to fetch doctors: {e}")
                return []

        async def fetch_previous_summary():
            # Fetch summary of previous session (if any) for continuity
            return {}  # Placeholder - would query session_summaries table

        # Parallel fetch all context
        try:
            clinic, patient, services, doctors, prev_summary = await asyncio.gather(
                fetch_clinic(),
                fetch_patient(),
                fetch_services(),
                fetch_doctors(),
                fetch_previous_summary(),
                return_exceptions=True
            )

            # Handle any exceptions from gather
            if isinstance(clinic, Exception):
                logger.warning(f"[hydrate_context] clinic fetch error: {clinic}")
                clinic = {}
            if isinstance(patient, Exception):
                logger.warning(f"[hydrate_context] patient fetch error: {patient}")
                patient = {}
            if isinstance(services, Exception):
                logger.warning(f"[hydrate_context] services fetch error: {services}")
                services = []
            if isinstance(doctors, Exception):
                logger.warning(f"[hydrate_context] doctors fetch error: {doctors}")
                doctors = []
            if isinstance(prev_summary, Exception):
                logger.warning(f"[hydrate_context] prev_summary fetch error: {prev_summary}")
                prev_summary = {}

        except Exception as e:
            logger.error(f"[hydrate_context] Parallel fetch failed: {e}")
            clinic, patient, services, doctors, prev_summary = {}, {}, [], [], {}

        # Update context
        ctx = state.get('context', {})
        ctx['clinic_profile'] = clinic
        ctx['patient_profile'] = patient
        ctx['clinic_services'] = services
        ctx['clinic_doctors'] = doctors
        state['context'] = ctx
        state['context_hydrated'] = True
        state['previous_session_summary'] = prev_summary

        # Also update patient fields in state
        if patient:
            state['patient_id'] = patient.get('id')
            state['patient_name'] = patient.get('name', patient.get('first_name'))

        state['audit_trail'].append({
            "node": "hydrate_context",
            "timestamp": datetime.utcnow().isoformat(),
            "clinic_loaded": bool(clinic),
            "patient_loaded": bool(patient),
            "services_count": len(services),
            "doctors_count": len(doctors),
        })

        return state

    async def simple_answer_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Fast-path node for simple FAQ/price queries.

        Per Opinion 4, Section 2.5:
        "Simple questions about hours, location, prices can be answered immediately
        without full planning/execution cycle."

        This node:
        1. Checks if query matches FAQ patterns
        2. Does direct context lookup (no LLM needed)
        3. Returns formatted answer immediately

        Routes to:
        - Exit (response generated) if simple answer found
        - Continue to supervisor if complex query

        CRITICAL (Phase 4 fix): If message has scheduling intent, SKIP fast-path
        to allow supervisor to route to scheduling flow.
        """
        logger.debug(f"Simple answer node - session: {state['session_id']}")

        message = state.get('message', '').lower()
        ctx = state.get('context', {})
        language = state.get('detected_language', 'en')

        # =========================================================================
        # CRITICAL FIX: Check for scheduling intent FIRST
        # If message has booking intent, skip FAQ fast-path entirely
        # This fixes the bug where "phone 555-123-4567" triggers clinic phone response
        # =========================================================================
        if self._looks_like_scheduling(message) or self._is_contact_info_submission(message):
            logger.info(f"[simple_answer] Scheduling intent detected, skipping fast-path")
            state['fast_path'] = False
            state['audit_trail'].append({
                "node": "simple_answer",
                "timestamp": datetime.utcnow().isoformat(),
                "fast_path": False,
                "reason": "scheduling_intent_detected",
            })
            return state

        # FAQ patterns (only checked if NOT scheduling intent)
        hours_patterns = ['hours', 'open', 'close', 'when', 'horario', 'часы', 'работаете']
        location_patterns = ['address', 'location', 'where', 'dirección', 'donde', 'адрес', 'где']
        phone_patterns = ['phone', 'call', 'number', 'teléfono', 'телефон', 'номер']

        clinic = ctx.get('clinic_profile', {})

        # Check for hours query
        if any(p in message for p in hours_patterns):
            hours = clinic.get('business_hours', clinic.get('hours'))
            if hours:
                # Format response in user's language
                templates = {
                    'en': f"Our hours are: {hours}",
                    'es': f"Nuestro horario es: {hours}",
                    'ru': f"Наши часы работы: {hours}",
                    'pt': f"Nosso horário é: {hours}",
                    'he': f"שעות הפעילות שלנו: {hours}",
                }
                state['response'] = templates.get(language, templates['en'])
                state['fast_path'] = True
                state['lane'] = 'FAQ'
                logger.info(f"[simple_answer] Fast-path hours response")
                return state

        # Check for location query
        if any(p in message for p in location_patterns):
            address = clinic.get('address', clinic.get('location'))
            if address:
                templates = {
                    'en': f"We're located at: {address}",
                    'es': f"Estamos ubicados en: {address}",
                    'ru': f"Мы находимся по адресу: {address}",
                    'pt': f"Estamos localizados em: {address}",
                    'he': f"אנחנו נמצאים ב: {address}",
                }
                state['response'] = templates.get(language, templates['en'])
                state['fast_path'] = True
                state['lane'] = 'FAQ'
                logger.info(f"[simple_answer] Fast-path location response")
                return state

        # Check for phone query
        if any(p in message for p in phone_patterns):
            phone = clinic.get('phone', clinic.get('phone_number'))
            if phone:
                templates = {
                    'en': f"You can reach us at: {phone}",
                    'es': f"Puede contactarnos al: {phone}",
                    'ru': f"Наш телефон: {phone}",
                    'pt': f"Você pode nos ligar em: {phone}",
                    'he': f"ניתן ליצור קשר בטלפון: {phone}",
                }
                state['response'] = templates.get(language, templates['en'])
                state['fast_path'] = True
                state['lane'] = 'FAQ'
                logger.info(f"[simple_answer] Fast-path phone response")
                return state

        # No simple answer found - continue to supervisor
        state['fast_path'] = False

        state['audit_trail'].append({
            "node": "simple_answer",
            "timestamp": datetime.utcnow().isoformat(),
            "fast_path": state['fast_path'],
        })

        return state

    def simple_answer_router(self, state: HealthcareConversationState) -> str:
        """Route based on simple answer result."""
        if state.get('fast_path') and state.get('response'):
            return 'exit'  # Response generated, skip to exit
        return 'continue'  # Continue to supervisor

    def static_info_router(self, state: HealthcareConversationState) -> str:
        """
        Route from static_info - may reroute to scheduling if intent detected.

        Phase 4: If static_info_agent detected scheduling intent, it sets
        force_reroute_to='scheduling'. In this case, go to booking_extractor
        (or planner if booking_extractor not yet added) instead of phi_redact.
        """
        if state.get("force_reroute_to") == "scheduling":
            logger.info("[static_info_router] Force reroute to booking_extractor")
            return "booking_extractor"  # Go to scheduling flow (via booking extractor)
        return "phi_redact"  # Normal path

    # ========================================================================
    # Phase 4: Booking Info Extraction (booking flow fix)
    # ========================================================================

    async def booking_info_extractor_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Extract structured booking info from natural language message.

        Per Opinion 1, Section 3.2:
        "Instead of trying to parse 'tomorrow at 10am' and 'my name is John Smith, phone 555-123...'
        inside the planner, introduce one dedicated node before planning."

        ENHANCED (Opinion 4): Includes conversation history for multi-turn context.
        ENHANCED (Opinion 2): Normalizes phone to digits only.
        ENHANCED (Opinion 2): Keeps dates as natural language for semantic adapter.

        Uses LLM with JSON response format for reliable extraction.
        """
        logger.info(f"[booking_extractor] Extracting booking info - session: {state['session_id'][:8]}")

        # Handle clarification responses - clear flags first
        if state.get('awaiting_patient_identification'):
            logger.info("[booking_extractor] Processing patient identification response")
            state['awaiting_patient_identification'] = False

        if state.get('awaiting_datetime'):
            logger.info("[booking_extractor] Processing datetime response")
            state['awaiting_datetime'] = False

        message = state.get('message', '')
        clinic_timezone = state.get('metadata', {}).get('clinic_timezone', 'America/New_York')

        # Get current date for context (but don't force LLM to calculate - Opinion 2)
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(clinic_timezone)
        except ImportError:
            import pytz
            tz = pytz.timezone(clinic_timezone)
        current_datetime = datetime.now(tz)

        # =========================================================================
        # CRITICAL (Opinion 4): Include conversation history for multi-turn context
        # Without this, "Tomorrow at 10am, cleaning" after "I want to book" loses intent
        # =========================================================================
        messages = state.get('messages', [])
        recent_turns = messages[-6:] if len(messages) > 6 else messages  # Last 3 exchanges
        conversation_context = ""
        if recent_turns and len(recent_turns) > 1:
            conversation_context = "Recent conversation:\n"
            for msg in recent_turns:
                role = "User" if getattr(msg, 'role', 'user') == "user" else "Agent"
                content = getattr(msg, 'content', str(msg))[:200]
                conversation_context += f"{role}: {content}\n"
            conversation_context += "\n"

        # =========================================================================
        # ENHANCED (Opinion 3): Add few-shot examples for ~15% accuracy boost
        # =========================================================================
        extraction_prompt = f"""Extract booking information from the user message.
Current date/time: {current_datetime.strftime('%A, %B %d, %Y %I:%M %p')} ({clinic_timezone})

{conversation_context}Current user message: "{message}"

EXAMPLES:
User: "Book me a cleaning tomorrow at 2pm. John Doe, 123-456-7890." -> {{"intent": "book", "service_type": "cleaning", "requested_date": "tomorrow", "requested_time": "2pm", "patient_name": "John Doe", "patient_phone": "1234567890", "doctor_preference": null, "urgency": "normal"}}
User: "My tooth really hurts, I need to see someone today" -> {{"intent": "book", "service_type": null, "requested_date": "today", "requested_time": null, "patient_name": null, "patient_phone": null, "doctor_preference": null, "urgency": "urgent"}}
User: "Tomorrow at 10am works for me" (after booking context) -> {{"intent": "book", "service_type": null, "requested_date": "tomorrow", "requested_time": "10am", "patient_name": null, "patient_phone": null, "doctor_preference": null, "urgency": "normal"}}

Extract these fields if present (leave null if not mentioned):
- intent: "book" | "reschedule" | "cancel" | "check_availability" (infer from conversation if not explicit)
- service_type: The type of appointment (cleaning, checkup, exam, etc.)
- requested_date: Natural language date ONLY (e.g., "tomorrow", "next Tuesday") - do NOT convert to ISO
- requested_time: Natural language time ONLY (e.g., "10am", "morning") - do NOT calculate
- patient_name: Patient's name if provided
- patient_phone: Phone number if provided (digits only, remove formatting)
- doctor_preference: Doctor name if mentioned (e.g., "Dr. Smith")
- urgency: "urgent" if pain/emergency mentioned, "normal" otherwise

Return valid JSON only, no markdown:
{{"intent": "...", "service_type": "...", "requested_date": "...", "requested_time": "...", "patient_name": "...", "patient_phone": "...", "doctor_preference": "...", "urgency": "..."}}"""

        extracted = {}

        if self.llm_factory:
            try:
                from app.services.llm.tiers import ModelTier
                response = await self.llm_factory.generate_for_tier(
                    tier=ModelTier.REASONING,
                    messages=[{"role": "system", "content": extraction_prompt}],
                    temperature=0.1,
                    max_tokens=200,
                    clinic_id=self.clinic_id,
                    session_id=state.get('session_id'),
                    response_format={"type": "json_object"},
                )

                import json
                extracted = json.loads(response.content)
                logger.info(f"[booking_extractor] Extracted: {extracted}")

            except Exception as e:
                logger.warning(f"[booking_extractor] LLM extraction failed: {e}")
                # Fallback to regex-based extraction
                extracted = self._fallback_booking_extraction(message)
        else:
            extracted = self._fallback_booking_extraction(message)

        # =========================================================================
        # Map extracted info to state fields expected by planner
        # ENHANCED (Opinion 2): Normalize phone to digits only for DB lookups
        # =========================================================================
        if extracted.get('intent'):
            state['booking_intent'] = extracted['intent']

        if extracted.get('service_type'):
            # FIXED: Map extracted service types to valid enum values
            # Schema expects: consultation, checkup, dental_cleaning, emergency, followup, procedure, general
            service_mapping = {
                'cleaning': 'dental_cleaning',
                'teeth cleaning': 'dental_cleaning',
                'dental cleaning': 'dental_cleaning',
                'clean': 'dental_cleaning',
                'checkup': 'checkup',
                'check up': 'checkup',
                'exam': 'checkup',
                'examination': 'checkup',
                'consultation': 'consultation',
                'consult': 'consultation',
                'emergency': 'emergency',
                'urgent': 'emergency',
                'follow up': 'followup',
                'followup': 'followup',
                'follow-up': 'followup',
                'procedure': 'procedure',
                'root canal': 'procedure',
                'filling': 'procedure',
                'extraction': 'procedure',
                'whitening': 'procedure',
            }
            raw_service = extracted['service_type'].lower()
            state['appointment_type'] = service_mapping.get(raw_service, 'general')

        if extracted.get('requested_date') or extracted.get('requested_time'):
            # Keep as natural language - semantic adapter will parse to ISO
            # (Opinion 2: LLMs struggle with date math, let Python handle it)
            # FIXED: Handle None values explicitly (not just missing keys)
            req_date = extracted.get('requested_date') or ''
            req_time = extracted.get('requested_time') or ''
            date_str = f"{req_date} {req_time}".strip()
            if date_str:
                state['preferred_date'] = date_str
                state['preferred_date_raw'] = date_str

        if extracted.get('patient_name'):
            state['patient_name'] = extracted['patient_name']

        if extracted.get('patient_phone'):
            # CRITICAL (Opinion 2): Normalize phone to digits only
            # DB has "5551234567" but user says "555-123-4567" - mismatch breaks lookup
            raw_phone = extracted['patient_phone']
            normalized_phone = ''.join(filter(str.isdigit, str(raw_phone)))
            state['patient_phone'] = normalized_phone
            logger.info(f"[booking_extractor] Normalized phone: {raw_phone} -> {normalized_phone}")

        if extracted.get('doctor_preference'):
            # Store as doctor_preference (name), will be resolved to UUID in planner
            state['doctor_preference'] = extracted['doctor_preference']

        if extracted.get('urgency') == 'urgent':
            state['is_urgent'] = True

        state['extracted_booking_info'] = extracted

        state['audit_trail'].append({
            "node": "booking_extractor",
            "timestamp": datetime.utcnow().isoformat(),
            "extracted_fields": list(k for k, v in extracted.items() if v),
        })

        return state

    def _fallback_booking_extraction(self, message: str) -> dict:
        """Regex-based fallback for booking info extraction."""
        extracted = {}
        message_lower = message.lower()

        # Intent detection
        if any(w in message_lower for w in ['book', 'schedule', 'appointment', 'запис']):
            extracted['intent'] = 'book'
        elif any(w in message_lower for w in ['cancel', 'отмен']):
            extracted['intent'] = 'cancel'
        elif any(w in message_lower for w in ['reschedule', 'перенес']):
            extracted['intent'] = 'reschedule'

        # Service type
        services = ['cleaning', 'checkup', 'exam', 'filling', 'root canal', 'whitening',
                    'чистка', 'осмотр', 'пломба', 'limpieza', 'examen']
        for service in services:
            if service in message_lower:
                extracted['service_type'] = service
                break

        # Phone number (any 10+ digit sequence)
        phone_match = re.search(r'[\d\-\(\)\s]{10,}', message)
        if phone_match:
            extracted['patient_phone'] = re.sub(r'[^\d]', '', phone_match.group())

        # Name after "my name is" or similar
        name_patterns = [
            r"my name is\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            r"i'm\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            r"меня зовут\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)",
            r"mi nombre es\s+([A-Z][a-záéíóúñ]+(?:\s+[A-Z][a-záéíóúñ]+)?)",
        ]
        for pattern in name_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                extracted['patient_name'] = match.group(1)
                break

        # Time patterns
        time_patterns = [
            r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))',
            r'(\d{1,2}:\d{2})',
            r'(morning|afternoon|evening)',
        ]
        for pattern in time_patterns:
            match = re.search(pattern, message_lower)
            if match:
                extracted['requested_time'] = match.group(1)
                break

        # Date patterns
        if 'tomorrow' in message_lower or 'завтра' in message_lower or 'mañana' in message_lower:
            extracted['requested_date'] = 'tomorrow'
        elif 'today' in message_lower or 'сегодня' in message_lower or 'hoy' in message_lower:
            extracted['requested_date'] = 'today'

        return extracted

    async def _resolve_doctor_id(self, doctor_name: str, state: HealthcareConversationState) -> Optional[str]:
        """
        Resolve doctor name to UUID.

        Per Opinion 4: "Dr. Smith" -> "doc-123-uuid"
        If multiple matches (Dr. John Smith vs Dr. Jane Smith), returns None
        and planner should ask user to clarify.
        """
        if not doctor_name:
            return None

        clinic_id = state.get('metadata', {}).get('clinic_id')
        if not clinic_id:
            return None

        # Try to find doctor in clinic's staff
        clinic_profile = state.get('context', {}).get('clinic_profile', {})
        doctors = clinic_profile.get('doctors', [])

        # Normalize search term
        search_term = doctor_name.lower().replace('dr.', '').replace('dr', '').strip()

        matches = []
        for doc in doctors:
            doc_name = doc.get('name', '').lower()
            if search_term in doc_name or doc_name in search_term:
                matches.append(doc)

        if len(matches) == 1:
            return matches[0].get('id')
        elif len(matches) > 1:
            # Multiple matches - ambiguous
            logger.warning(f"[planner] Ambiguous doctor: '{doctor_name}' matches {[m.get('name') for m in matches]}")
            # Could set state['needs_doctor_clarification'] = True and list options
            return None
        else:
            # No matches - may be typo or doctor not at this clinic
            logger.warning(f"[planner] No doctor match for: '{doctor_name}'")
            return None

    def _generate_booking_summary(
        self,
        adapted_args: dict,
        state: HealthcareConversationState,
        step_name: str,
        language: str = 'en'
    ) -> str:
        """
        Generate informative human_summary for ActionProposal.

        FIXED (Phase 4): Instead of generic "Book the selected appointment",
        include actual booking details: patient name, service type, date/time, doctor.

        Example output:
        - "Book dental cleaning for John Smith on Dec 27 at 10:00 AM"
        - "Записать Марию на чистку зубов на 27 декабря в 10:00"
        """
        # Extract booking details from args and state
        patient_name = adapted_args.get('patient_name') or state.get('patient_name') or 'patient'
        service_type = adapted_args.get('appointment_type') or state.get('appointment_type') or 'appointment'
        datetime_str = adapted_args.get('datetime_str') or state.get('preferred_date') or ''

        # Get doctor name if available
        doctor_id = adapted_args.get('doctor_id')
        doctor_name = None
        if doctor_id:
            # Try to resolve doctor ID back to name for display
            clinic_profile = state.get('context', {}).get('clinic_profile', {})
            for doc in clinic_profile.get('doctors', []):
                if doc.get('id') == doctor_id:
                    doctor_name = doc.get('name')
                    break
        if not doctor_name:
            doctor_name = state.get('doctor_preference')

        # Build summary based on language
        if language == 'ru':
            parts = [f"Записать {patient_name}"]
            if service_type and service_type != 'appointment':
                service_ru = {
                    'cleaning': 'на чистку зубов',
                    'checkup': 'на осмотр',
                    'exam': 'на обследование',
                    'filling': 'на пломбирование',
                    'root canal': 'на лечение корневого канала',
                    'whitening': 'на отбеливание',
                }.get(service_type.lower(), f'на {service_type}')
                parts.append(service_ru)
            if doctor_name:
                parts.append(f"к {doctor_name}")
            if datetime_str:
                parts.append(f"на {datetime_str}")
            return ' '.join(parts)

        elif language == 'es':
            parts = [f"Reservar {service_type} para {patient_name}"]
            if doctor_name:
                parts.append(f"con {doctor_name}")
            if datetime_str:
                parts.append(f"el {datetime_str}")
            return ' '.join(parts)

        else:  # English default
            parts = [f"Book {service_type} for {patient_name}"]
            if doctor_name:
                parts.append(f"with {doctor_name}")
            if datetime_str:
                parts.append(f"on {datetime_str}")
            return ' '.join(parts)

    # ========================================================================
    # Phase 4: Executor Debugging & Silent Failure Prevention Helpers
    # ========================================================================

    async def _resolve_datetime_for_tool(self, natural_date: str, clinic_timezone: str) -> Optional[str]:
        """
        Convert natural language date to ISO format for tool arguments.

        Handles: "tomorrow", "next Tuesday", "January 15th", etc.
        Returns None if parsing fails (caller should ask for clarification).

        Phase 4 (4.3): Semantic adapter error handling with dateparser.
        """
        if not natural_date:
            return None

        try:
            from dateparser import parse as dateparser_parse
        except ImportError:
            logger.warning("[datetime_resolver] dateparser not installed, falling back to basic parsing")
            dateparser_parse = None

        try:
            # Get timezone object
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(clinic_timezone)
            except ImportError:
                import pytz
                tz = pytz.timezone(clinic_timezone)

            now = datetime.now(tz)

            if dateparser_parse:
                # Parse the natural language date with dateparser
                parsed = dateparser_parse(
                    natural_date,
                    settings={
                        'PREFER_DATES_FROM': 'future',
                        'RELATIVE_BASE': now.replace(tzinfo=None),  # dateparser expects naive datetime
                        'TIMEZONE': clinic_timezone,
                        'RETURN_AS_TIMEZONE_AWARE': True,
                    }
                )

                if parsed:
                    iso_str = parsed.isoformat()
                    logger.info(f"[datetime_resolver] '{natural_date}' -> {iso_str}")
                    return iso_str
                else:
                    logger.warning(f"[datetime_resolver] Could not parse: '{natural_date}'")
                    return None
            else:
                # Basic fallback parsing without dateparser
                # Handle common patterns
                date_lower = natural_date.lower().strip()
                if 'tomorrow' in date_lower:
                    target = now + timedelta(days=1)
                    return target.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
                elif 'today' in date_lower:
                    return now.replace(minute=0, second=0, microsecond=0).isoformat()
                else:
                    logger.warning(f"[datetime_resolver] No dateparser, cannot parse: '{natural_date}'")
                    return None

        except Exception as e:
            logger.error(f"[datetime_resolver] Error parsing '{natural_date}': {e}")
            return None

    def _validate_tool_arguments(self, tool_name: str, arguments: dict) -> tuple:
        """
        Validate that arguments match the expected tool signature.

        Returns (is_valid: bool, error_message: Optional[str]).

        Phase 4 (4.4): Catch argument mismatches before silent failures.
        """
        TOOL_SCHEMAS = {
            'check_availability': {
                'required': ['date'],
                'optional': ['doctor_id', 'appointment_type', 'duration_minutes'],
            },
            'book_appointment': {
                'required': ['datetime_str', 'appointment_type'],
                'optional': ['patient_identifier', 'patient_name', 'patient_phone', 'doctor_id', 'duration_minutes', 'patient_id'],
            },
            'query_prices': {
                'required': [],
                'optional': ['service_type', 'services'],
            },
            'cancel_appointment': {
                'required': ['patient_id'],
                'optional': ['appointment_id'],
            },
        }

        schema = TOOL_SCHEMAS.get(tool_name)
        if not schema:
            return True, None  # Unknown tool, skip validation

        missing = []
        for field in schema['required']:
            if not arguments.get(field):
                missing.append(field)

        if missing:
            return False, f"Missing required fields for {tool_name}: {missing}"

        return True, None

    def _extract_services_from_message(self, message: str) -> list:
        """
        Extract service types mentioned in user message.

        Phase 5 (5.1): Used for direct query_prices calls when LLM fails.
        """
        SERVICE_PATTERNS = {
            'cleaning': ['cleaning', 'limpieza', 'чистка', 'clean'],
            'whitening': ['whitening', 'blanqueamiento', 'отбеливание', 'whiten'],
            'root_canal': ['root canal', 'endodoncia', 'удаление нерва', 'root-canal'],
            'filling': ['filling', 'empaste', 'пломба', 'cavity'],
            'checkup': ['checkup', 'exam', 'revisión', 'осмотр', 'check-up', 'examination'],
            'extraction': ['extraction', 'extracción', 'удаление', 'remove', 'pull'],
            'crown': ['crown', 'corona', 'коронка'],
            'implant': ['implant', 'implante', 'имплант'],
        }

        found = []
        msg_lower = message.lower()
        for service, patterns in SERVICE_PATTERNS.items():
            if any(p in msg_lower for p in patterns):
                found.append(service)

        return found if found else ['general']

    def _validate_response_against_tools(self, state: HealthcareConversationState, proposed_response: str) -> str:
        """
        Validate that responses don't contain hallucinated data.

        Phase 5 (5.3): Block responses with times/prices if tools weren't called.

        Returns:
            - Original response if valid
            - Safe alternative response if hallucination detected
        """
        import re

        tools_called = state.get('tools_actually_called', []) or []

        # Check for time patterns (availability)
        time_patterns = re.findall(
            r'\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?|\d{1,2}\s*(?:AM|PM|am|pm)',
            proposed_response
        )

        if time_patterns and 'check_availability' not in tools_called:
            logger.error(f"[validate_response] BLOCKING hallucinated times: {time_patterns}")
            logger.error(f"[validate_response] tools_called={tools_called}, but response has times")
            return "Let me check what times are available for you. What date works best?"

        # Check for price patterns
        price_patterns = re.findall(
            r'\$\d+(?:\.\d{2})?(?:\s*[-–]\s*\$\d+(?:\.\d{2})?)?',
            proposed_response
        )

        if price_patterns and 'query_prices' not in tools_called:
            logger.error(f"[validate_response] BLOCKING hallucinated prices: {price_patterns}")
            logger.error(f"[validate_response] tools_called={tools_called}, but response has prices")
            return "Let me look up the current pricing for you. Which service are you interested in?"

        return proposed_response

    # ========================================================================
    # Phase 2: Plan-then-Execute Nodes
    # ========================================================================

    async def planner_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Create action plan for complex scheduling operations.

        Per Opinion 4, Section 3.2:
        "Plan-then-Execute patterns yield better predictability and security."

        This node:
        1. Analyzes the scheduling intent
        2. Creates a typed ActionPlan with required steps
        3. Identifies which steps need confirmation
        """
        logger.debug(f"Planner node - session: {state['session_id']}")

        message = state.get('message', '').lower()
        ctx = state.get('context', {})
        language = state.get('detected_language', 'en')

        # =========================================================================
        # Phase 4: Use extracted booking info with fallbacks
        # =========================================================================
        extracted = state.get('extracted_booking_info', {})

        # Prefer extracted values, fall back to state, then defaults
        doctor_preference = state.get('doctor_preference') or extracted.get('doctor_preference')
        preferred_date = state.get('preferred_date') or extracted.get('requested_date')
        appointment_type = state.get('appointment_type') or extracted.get('service_type') or 'general'
        patient_name = state.get('patient_name') or extracted.get('patient_name')
        patient_phone = state.get('patient_phone') or extracted.get('patient_phone')

        # =========================================================================
        # CRITICAL (Opinion 4): Resolve doctor name -> UUID
        # "Dr. Smith" must become a real doctor_id before passing to tools
        # =========================================================================
        doctor_id = state.get('doctor_id')  # May already be resolved
        if not doctor_id and doctor_preference:
            doctor_id = await self._resolve_doctor_id(doctor_preference, state)
            if doctor_id:
                state['doctor_id'] = doctor_id
                logger.info(f"[planner] Resolved doctor: {doctor_preference} -> {doctor_id}")
            else:
                # Could not resolve - may need to ask user to clarify
                logger.warning(f"[planner] Could not resolve doctor: {doctor_preference}")
                # Continue without doctor_id - may get any available doctor

        # Build patient identifier for lookup (phone preferred, then name)
        patient_identifier = None
        if patient_phone:
            patient_identifier = patient_phone
        elif patient_name:
            patient_identifier = patient_name

        logger.info(f"[planner] Using: doctor={doctor_id}, date={preferred_date}, type={appointment_type}, patient_id={patient_identifier}")

        # Determine action type from extracted intent or message keywords
        action_type = state.get('booking_intent', 'book')  # Use extracted intent if available
        if action_type not in ['book', 'cancel', 'reschedule', 'check_availability']:
            action_type = 'book'  # Default to book
            if any(w in message for w in ['cancel', 'cancelar', 'отменить']):
                action_type = 'cancel'
            elif any(w in message for w in ['reschedule', 'reprogramar', 'перенести']):
                action_type = 'reschedule'

        # Build plan based on action type
        steps = []

        if action_type == 'book':
            # Standard booking flow - now using extracted values
            steps = [
                PlanStep(
                    action=ActionType.CHECK_AVAILABILITY,
                    tool_name="check_availability",
                    arguments={
                        "doctor_id": doctor_id,  # Now a UUID, not a name
                        "date": preferred_date,  # Semantic adapter will parse natural language
                        "appointment_type": appointment_type,
                        "duration_minutes": 30,
                    },
                    requires_confirmation=False,
                    description="Check available appointment slots"
                ),
                PlanStep(
                    action=ActionType.BOOK_APPOINTMENT,
                    tool_name="book_appointment",
                    arguments={
                        "patient_identifier": patient_identifier,  # Phone or name for lookup
                        "patient_name": patient_name,
                        "patient_phone": patient_phone,
                        "doctor_id": doctor_id,
                        "datetime_str": preferred_date,
                        "appointment_type": appointment_type,
                        "duration_minutes": 30,
                    },
                    requires_confirmation=True,  # HITL for bookings
                    description="Book the selected appointment"
                ),
            ]
            goal = "Book appointment for patient"

        elif action_type == 'cancel':
            steps = [
                PlanStep(
                    action=ActionType.CANCEL_APPOINTMENT,
                    tool_name="cancel_appointment",
                    arguments={
                        "patient_id": state.get('patient_id'),
                    },
                    requires_confirmation=True,  # HITL for cancellations
                    description="Cancel existing appointment"
                ),
            ]
            goal = "Cancel patient's appointment"

        elif action_type == 'reschedule':
            steps = [
                PlanStep(
                    action=ActionType.CHECK_AVAILABILITY,
                    tool_name="check_availability",
                    arguments={
                        "doctor_id": doctor_id,  # Use Phase 4 resolved value
                        "date": preferred_date,  # Use Phase 4 extracted value
                        "appointment_type": appointment_type,
                        "duration_minutes": 30,
                    },
                    requires_confirmation=False,
                    description="Find new available slots"
                ),
                PlanStep(
                    action=ActionType.RESCHEDULE_APPOINTMENT,
                    tool_name="reschedule_appointment",
                    arguments={
                        "patient_identifier": patient_identifier,
                        "patient_id": state.get('patient_id'),
                    },
                    requires_confirmation=True,
                    description="Move appointment to new time"
                ),
            ]
            goal = "Reschedule patient's appointment"

        elif action_type == 'check_availability':
            # User just wants to check availability, not book yet
            steps = [
                PlanStep(
                    action=ActionType.CHECK_AVAILABILITY,
                    tool_name="check_availability",
                    arguments={
                        "doctor_id": doctor_id,
                        "date": preferred_date,
                        "appointment_type": appointment_type,
                        "duration_minutes": 30,
                    },
                    requires_confirmation=False,
                    description="Check available appointment slots"
                ),
            ]
            goal = "Check appointment availability"

        else:
            # Fallback: default to booking flow
            logger.warning(f"[planner] Unknown action_type '{action_type}', defaulting to book")
            steps = [
                PlanStep(
                    action=ActionType.CHECK_AVAILABILITY,
                    tool_name="check_availability",
                    arguments={
                        "doctor_id": doctor_id,
                        "date": preferred_date,
                        "appointment_type": appointment_type,
                        "duration_minutes": 30,
                    },
                    requires_confirmation=False,
                    description="Check available appointment slots"
                ),
                PlanStep(
                    action=ActionType.BOOK_APPOINTMENT,
                    tool_name="book_appointment",
                    arguments={
                        "patient_identifier": patient_identifier,
                        "patient_name": patient_name,
                        "patient_phone": patient_phone,
                        "doctor_id": doctor_id,
                        "datetime_str": preferred_date,
                        "appointment_type": appointment_type,
                        "duration_minutes": 30,
                    },
                    requires_confirmation=True,
                    description="Book the selected appointment"
                ),
            ]
            goal = "Book appointment for patient"

        # Create typed plan
        plan = ActionPlan(
            goal=goal,
            steps=steps,
            requires_human_confirmation=any(s.requires_confirmation for s in steps),
            estimated_steps=len(steps),
        )

        # Store plan in state (as dict for serialization)
        state['action_plan'] = {
            'goal': plan.goal,
            'steps': [s.to_execution_dict() for s in plan.steps],
            'requires_human_confirmation': plan.requires_human_confirmation,
            'estimated_steps': plan.estimated_steps,
            'created_at': plan.created_at.isoformat(),
        }
        state['plan_completed_steps'] = []
        state['plan_needs_replanning'] = False

        logger.info(f"[planner] Created plan: {plan.goal} with {len(steps)} steps")
        logger.info(f"[planner] Plan stored - goal: {state.get('action_plan', {}).get('goal')}, steps: {len(state.get('action_plan', {}).get('steps', []))}")

        state['audit_trail'].append({
            "node": "planner",
            "timestamp": datetime.utcnow().isoformat(),
            "action_type": action_type,
            "plan_steps": len(steps),
            "requires_confirmation": plan.requires_human_confirmation,
        })

        return state

    async def executor_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Execute action plan step by step with semantic adaptation.

        This node:
        1. Gets next step from plan
        2. Adapts arguments using SemanticAdapter (name→UUID, natural date→ISO)
        3. Validates arguments against canonical schema
        4. Checks if step requires confirmation
        5. If confirmation needed, creates ActionProposal and pauses
        6. If no confirmation needed, executes step
        7. Handles errors and replanning triggers

        Enhanced with Semantic Adapter (per Opinion 3):
        - Doctor names resolved to UUIDs
        - Natural language dates parsed to ISO format
        - Patient IDs injected from session context
        """
        # Import semantic adapter and validation
        from app.services.orchestrator.tools.semantic_adapter import (
            SemanticAdapter,
            adapt_tool_arguments,
        )
        from app.services.orchestrator.tools.canonical_schemas import validate_tool_call
        from pydantic import ValidationError

        logger.info(f"[executor] Starting - session: {state.get('session_id', 'unknown')[:8]}")
        logger.info(f"[executor] State keys: {list(state.keys())}")

        # =========================================================================
        # Phase 4 (4.1): Initialize tool tracking variables
        # =========================================================================
        tools_called = state.get('tools_actually_called', []) or []
        tools_failed = state.get('tools_failed', []) or []

        plan = state.get('action_plan')
        if not plan:
            logger.error("[executor] NO ACTION PLAN - this should not happen after planner")
            state['executor_error'] = 'no_action_plan'
            # Generate a response explaining we need more context
            state['response'] = state.get('response', "I'll help you book an appointment. What service do you need?")
            return state

        steps = plan.get('steps', [])
        logger.info(f"[executor] Plan has {len(steps)} steps: {[s.get('tool_name', 'unknown') for s in steps]}")

        # Create SemanticAdapter for this execution context
        adapter = SemanticAdapter(
            clinic_id=state.get('metadata', {}).get('clinic_id', ''),
            context=state.get('context', {}),
            supabase_client=self.supabase if hasattr(self, 'supabase') else None,
            clinic_timezone=state.get('metadata', {}).get('clinic_timezone', 'UTC'),
        )

        completed_steps = state.get('plan_completed_steps', [])
        steps = plan.get('steps', [])

        # Find next step to execute
        next_step_idx = len(completed_steps)
        if next_step_idx >= len(steps):
            logger.info("[executor] All steps completed")
            state['plan_results'] = {'success': True, 'message': 'All steps completed'}
            return state

        next_step = steps[next_step_idx]
        step_name = next_step.get('tool_name', 'unknown')

        logger.info(f"[executor] Executing step {next_step_idx + 1}/{len(steps)}: {step_name}")

        # =========================================================================
        # Phase 4: Mandatory field validation (booking flow fix)
        # For book_appointment, validate mandatory fields BEFORE proceeding
        # =========================================================================
        if step_name == "book_appointment":
            args = next_step.get('arguments', {})
            patient_identifier = args.get('patient_identifier') or args.get('patient_phone') or args.get('patient_name')

            if not patient_identifier:
                # Missing patient info - ask for clarification
                logger.warning("[executor] Missing patient identifier for booking")

                # ENHANCED (Opinion 3): Track clarification count, escalate after 2+
                clarification_count = (state.get('clarification_count') or 0) + 1
                state['clarification_count'] = clarification_count

                if clarification_count > 2:
                    # Too many clarifications - escalate to human
                    logger.warning(f"[executor] Escalating after {clarification_count} clarification attempts")
                    state['needs_human_escalation'] = True
                    state['response'] = "I'm having trouble processing your booking. Let me connect you with our staff who can assist you directly."
                    return state

                state['awaiting_patient_identification'] = True

                language = state.get('detected_language', 'en')
                # ENHANCED (Opinion 3): Dynamic clarification with context
                service_type = args.get('appointment_type', 'appointment')
                clarification_messages = {
                    'en': f"I'd be happy to book your {service_type} for you. Could you please provide your name and phone number so I can find your record?",
                    'es': f"Con gusto le ayudo con su cita de {service_type}. ¿Podría proporcionarme su nombre y número de teléfono?",
                    'ru': f"С удовольствием запишу вас на {service_type}. Не могли бы вы назвать ваше имя и номер телефона?",
                }
                state['response'] = clarification_messages.get(language, clarification_messages['en'])

                state['audit_trail'].append({
                    "node": "executor",
                    "timestamp": datetime.utcnow().isoformat(),
                    "action": "request_patient_info",
                    "reason": "missing_patient_identifier",
                    "clarification_count": clarification_count
                })
                return state

            if not args.get('datetime_str'):
                # Missing appointment time
                logger.warning("[executor] Missing datetime for booking")

                clarification_count = (state.get('clarification_count') or 0) + 1
                state['clarification_count'] = clarification_count

                if clarification_count > 2:
                    state['needs_human_escalation'] = True
                    state['response'] = "I'm having trouble processing your booking. Let me connect you with our staff."
                    return state

                state['awaiting_datetime'] = True

                language = state.get('detected_language', 'en')
                clarification_messages = {
                    'en': "When would you like to schedule your appointment? You can say something like 'tomorrow at 2pm' or 'next Monday morning'.",
                    'es': "¿Cuándo le gustaría programar su cita?",
                    'ru': "Когда бы вы хотели записаться? Можете сказать, например, 'завтра в 14:00'.",
                }
                state['response'] = clarification_messages.get(language, clarification_messages['en'])

                state['audit_trail'].append({
                    "node": "executor",
                    "timestamp": datetime.utcnow().isoformat(),
                    "action": "request_datetime",
                    "reason": "missing_datetime",
                    "clarification_count": clarification_count
                })
                return state

        # Check if step requires confirmation
        if next_step.get('requires_confirmation'):
            # Check if user already confirmed
            if state.get('user_confirmed'):
                # User confirmed - execute the step
                logger.info(f"[executor] User confirmed, executing {step_name}")
                state['user_confirmed'] = False  # Reset for next confirmation
                state['awaiting_confirmation'] = False
            else:
                # Adapt arguments before creating proposal (for display and later execution)
                raw_args = next_step.get('arguments', {})
                try:
                    adapted_args = await adapt_tool_arguments(
                        tool_name=step_name,
                        raw_arguments=raw_args,
                        adapter=adapter,
                    )
                    logger.debug(f"[executor] Adapted args for confirmation: {adapted_args}")
                except Exception as e:
                    logger.warning(f"[executor] Failed to adapt args for confirmation: {e}")
                    adapted_args = raw_args

                # Need confirmation - create proposal with adapted arguments
                # FIXED (Phase 4): slot must be dict, not string
                slot_value = adapted_args.get('datetime_str') or adapted_args.get('slot')
                if isinstance(slot_value, str) and slot_value:
                    # Convert datetime string to slot dict format
                    slot_dict = {"datetime": slot_value}
                elif isinstance(slot_value, dict):
                    slot_dict = slot_value
                else:
                    slot_dict = None

                # FIXED (Phase 4): Generate informative human_summary with actual booking details
                # Instead of generic "Book the selected appointment", include patient name, time, etc.
                human_summary = self._generate_booking_summary(
                    adapted_args=adapted_args,
                    state=state,
                    step_name=step_name,
                    language=state.get('detected_language', 'en')
                )

                proposal = ActionProposal(
                    type=ActionProposalType.BOOK_APPOINTMENT,  # Adjust based on step
                    patient_id=adapted_args.get('patient_id', state.get('patient_id', '') or ''),
                    provider_id=adapted_args.get('doctor_id'),
                    slot=slot_dict,
                    human_summary=human_summary,
                    execution_params=adapted_args,  # Use adapted arguments
                    ttl_seconds=1800,  # 30 minute TTL
                )

                state['action_proposal'] = proposal.to_state_dict()
                state['awaiting_confirmation'] = True
                state['pending_action'] = proposal.to_state_dict()
                state['pending_action_timestamp'] = datetime.now(timezone.utc).isoformat()

                # Generate confirmation message
                language = state.get('detected_language', 'en')
                state['response'] = proposal.to_confirmation_message(language)

                logger.info(f"[executor] Awaiting confirmation for {step_name}")

                state['audit_trail'].append({
                    "node": "executor",
                    "timestamp": datetime.utcnow().isoformat(),
                    "step": step_name,
                    "awaiting_confirmation": True,
                    "adapted_args": list(adapted_args.keys()),
                })

                return state

        # Execute the step (no confirmation needed or already confirmed)
        try:
            # Get the appropriate tool
            tool = self._get_tool_for_step(next_step)

            # =========================================================================
            # Phase 4 (4.1): Enhanced logging - log tool call attempt
            # =========================================================================
            raw_arguments = next_step.get('arguments', {})
            logger.info(f"[executor] Attempting tool call: {step_name}")
            logger.info(f"[executor] Tool arguments: {raw_arguments}")

            if tool:
                # Step 1: Adapt arguments using SemanticAdapter
                # This resolves: doctor names → UUIDs, natural dates → ISO, injects patient_id
                adapted_arguments = await adapt_tool_arguments(
                    tool_name=step_name,
                    raw_arguments=raw_arguments,
                    adapter=adapter,
                )
                logger.info(f"[executor] Adapted arguments for {step_name}: {list(adapted_arguments.keys())}")

                # Step 2: Validate against canonical schema (catches mismatches before runtime)
                try:
                    validated_input = validate_tool_call(step_name, adapted_arguments)
                    # Convert Pydantic model to dict for tool execution
                    validated_args = validated_input.model_dump(exclude_none=True)
                    logger.debug(f"[executor] Validated args: {validated_args}")
                except ValidationError as ve:
                    # =========================================================================
                    # Phase 4 (4.1): Track validation errors
                    # =========================================================================
                    logger.error(f"[executor] VALIDATION ERROR for {step_name}: {ve}")
                    tools_failed.append({'tool': step_name, 'error': 'validation_error', 'details': str(ve)})
                    validation_errors = state.get('executor_validation_errors', []) or []
                    validation_errors.append(str(ve))
                    state['executor_validation_errors'] = validation_errors
                    state['plan_execution_error'] = f"Invalid arguments for {step_name}: {ve.error_count()} validation errors"
                    state['plan_failed_step'] = step_name
                    state['tools_failed'] = tools_failed
                    return state

                # Step 3: Execute tool with validated arguments
                result = await tool(**validated_args)

                # =========================================================================
                # Phase 4 (4.1): Track successful tool calls
                # =========================================================================
                tools_called.append(step_name)
                logger.info(f"[executor] Tool {step_name} SUCCESS: {str(result)[:200]}")

                # Store result
                if not state.get('plan_results'):
                    state['plan_results'] = {'outputs': {}}
                state['plan_results']['outputs'][step_name] = result

                # Mark step as completed
                completed_steps.append(step_name)
                state['plan_completed_steps'] = completed_steps

                logger.info(f"[executor] Step {step_name} completed successfully")
            else:
                # =========================================================================
                # Phase 4 (4.1): Track tool not found errors
                # =========================================================================
                logger.error(f"[executor] TOOL NOT FOUND: {step_name}")
                tools_failed.append({'tool': step_name, 'error': 'tool_not_found'})
                state['plan_execution_error'] = f"Tool not found: {step_name}"

        except ValidationError as ve:
            # =========================================================================
            # Phase 4 (4.1): Catch any validation errors that slip through
            # =========================================================================
            logger.error(f"[executor] Validation error in {step_name}: {ve}")
            tools_failed.append({'tool': step_name, 'error': 'validation_error', 'details': str(ve)})
            validation_errors = state.get('executor_validation_errors', []) or []
            validation_errors.append(str(ve))
            state['executor_validation_errors'] = validation_errors
            state['plan_execution_error'] = f"Validation error: {ve}"
            state['plan_failed_step'] = step_name

        except Exception as e:
            # =========================================================================
            # Phase 4 (4.1): Track execution errors
            # =========================================================================
            logger.error(f"[executor] TOOL EXECUTION FAILED for {step_name}: {e}")
            tools_failed.append({'tool': step_name, 'error': 'execution_error', 'details': str(e)})
            state['plan_execution_error'] = str(e)
            state['plan_failed_step'] = step_name

            # Check if we should replan
            if next_step_idx < len(steps) - 1:
                state['plan_needs_replanning'] = True

        # =========================================================================
        # Phase 4 (4.1): Store tool tracking in state and log execution summary
        # =========================================================================
        state['tools_actually_called'] = tools_called
        state['tools_failed'] = tools_failed
        logger.info(f"[executor] Execution complete. Called: {tools_called}, Failed: {tools_failed}")

        # =========================================================================
        # Phase 4 (4.2): STRICT MODE - No confirmations without tool verification
        # =========================================================================
        booking_intent = state.get('booking_intent')

        if booking_intent in ('book', 'check_availability'):
            if 'check_availability' not in tools_called:
                logger.error("[executor] STRICT MODE VIOLATION: Booking without check_availability")

                # Check why it failed
                validation_errors = state.get('executor_validation_errors', [])

                if validation_errors:
                    logger.error(f"[executor] Validation errors prevented tool call: {validation_errors}")
                    # Provide user-friendly message about what's missing
                    state['response'] = "I need a bit more information to check availability. Could you tell me what date and time you're looking for?"
                    state['awaiting_datetime'] = True
                elif tools_failed:
                    logger.error(f"[executor] Tool failures: {tools_failed}")
                    state['response'] = "I'm having trouble checking our schedule right now. Let me try again - what date works for you?"
                else:
                    logger.error("[executor] Unknown reason for missing check_availability")
                    state['response'] = "I couldn't verify the available times. Could you tell me when you'd like to come in?"

                # DO NOT generate confirmation - force clarification
                state['booking_blocked_no_availability_check'] = True

        state['audit_trail'].append({
            "node": "executor",
            "timestamp": datetime.utcnow().isoformat(),
            "step": step_name,
            "tools_called": tools_called,
            "tools_failed": tools_failed,
            "validation_errors": state.get('executor_validation_errors', []),
            "completed": step_name in completed_steps,
            "error": state.get('plan_execution_error'),
        })

        return state

    def _get_tool_for_step(self, step: Dict[str, Any]) -> Optional[Any]:
        """Get the appropriate tool callable for a plan step."""
        tool_name = step.get('tool_name')

        if tool_name == 'check_availability' and self.appointment_tools:
            return self.appointment_tools.check_availability
        elif tool_name == 'book_appointment' and self.appointment_tools:
            return self.appointment_tools.book_appointment
        elif tool_name == 'cancel_appointment' and self.appointment_tools:
            return self.appointment_tools.cancel_appointment
        elif tool_name == 'query_prices' and self.price_query_tool:
            return self.price_query_tool.get_services_by_query

        return None

    def executor_router(self, state: HealthcareConversationState) -> str:
        """Route based on executor result."""
        logger.info(f"[executor_router] awaiting_confirmation={state.get('awaiting_confirmation')}, action_plan={bool(state.get('action_plan'))}")

        # =========================================================================
        # Phase 4: Handle clarification and escalation flows (booking flow fix)
        # =========================================================================

        # ENHANCED: Human escalation - exit immediately
        if state.get('needs_human_escalation'):
            logger.info("[executor_router] Routing to exit for human escalation")
            return 'exit'  # Exit flow, response already set

        # Awaiting clarification - exit to get response to user
        if state.get('awaiting_patient_identification') or state.get('awaiting_datetime'):
            logger.info("[executor_router] Awaiting clarification, exiting to user")
            return 'exit'  # Exit flow so user gets the clarification prompt

        # ENHANCED (Opinion 4): Handle slot taken - trigger replan with different time
        if state.get('slot_taken_error'):
            logger.info("[executor_router] Slot taken, replanning with different time")
            state['replan_reason'] = 'slot_taken'
            return 'replan'

        # =========================================================================
        # Phase 4 (4.2): Handle strict mode violations - exit without confirmation
        # =========================================================================
        if state.get('booking_blocked_no_availability_check') or state.get('booking_blocked_no_verification'):
            logger.info("[executor_router] Strict mode violation - exiting for clarification")
            return 'exit'  # Exit flow, clarification response already set

        # =========================================================================
        # Phase 5 (5.4): Availability-first enforcement - no booking without check
        # =========================================================================
        tools_called = state.get('tools_actually_called', []) or []
        booking_intent = state.get('booking_intent')

        # If user wants to book and we haven't checked availability, go back to planner
        if booking_intent == 'book' and 'check_availability' not in tools_called:
            # Only force replan if we're trying to complete the booking step
            plan = state.get('action_plan', {})
            completed_steps = state.get('plan_completed_steps', [])

            # Check if we've attempted book_appointment without check_availability
            if 'book_appointment' in [s.get('tool_name') for s in plan.get('steps', [])]:
                if 'check_availability' not in completed_steps:
                    logger.warning("[executor_router] AVAILABILITY-FIRST: Booking without availability check")
                    state['force_availability_check'] = True
                    state['replan_reason'] = 'missing_availability_check'
                    return 'planner'  # Go back to planner to add availability step

        # =========================================================================
        # Existing routing logic
        # =========================================================================

        if state.get('awaiting_confirmation'):
            return 'exit'  # Wait for user response
        if state.get('plan_needs_replanning'):
            return 'replan'
        if state.get('plan_execution_error'):
            return 'error'

        # Check if all steps completed
        plan = state.get('action_plan')
        if not plan:
            logger.warning("[executor_router] No plan found, routing to error for graceful handling")
            return 'error'  # No plan = error, not complete

        completed = len(state.get('plan_completed_steps', []))
        total = len(plan.get('steps', []))

        if completed >= total:
            return 'complete'
        return 'continue'  # More steps to execute

    # ========================================================================
    # End of Phase 2 Nodes
    # ========================================================================

    async def process(
        self,
        message: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        patient_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process healthcare conversation.

        Enhanced for Phase 3B integration with:
        - Pipeline context injection
        - UnifiedStateManager flow state tracking
        - State transition reporting for pipeline

        Args:
            message: User message
            session_id: Session ID
            metadata: Optional metadata
            patient_id: Optional patient identifier
            context: Optional pipeline context with:
                - clinic_profile: Clinic info from pipeline
                - patient_profile: Patient info from pipeline
                - constraints: ConversationConstraints dict
                - language: Detected language
                - flow_state: Current flow state (Phase 3A)
                - turn_status: Current turn status (Phase 3A)
                - conversation_history: Prior messages

        Returns:
            Dict with:
                - response: Generated response text
                - intent: Classified intent
                - audit_trail: List of processed nodes
                - state_transition: Optional new flow state
                - context: Updated context dict
        """
        # Extract context values
        ctx = context or {}
        patient_id = patient_id or ctx.get('patient_profile', {}).get('id')
        patient_name = ctx.get('patient_profile', {}).get('name')
        flow_state = ctx.get('flow_state', 'idle')
        turn_status = ctx.get('turn_status', 'user_turn')

        # Merge context into metadata for orchestrator access
        enriched_metadata = metadata or {}
        enriched_metadata.update({
            'clinic_profile': ctx.get('clinic_profile', {}),
            'patient_profile': ctx.get('patient_profile', {}),
            'constraints': ctx.get('constraints', {}),
            'language': ctx.get('language', 'es'),
            'flow_state': flow_state,
            'turn_status': turn_status,
            'conversation_history': ctx.get('conversation_history', []),
        })

        # Create healthcare-specific initial state
        initial_state = HealthcareConversationState(
            session_id=session_id,
            message=message,
            context=ctx,
            intent=None,
            response=None,
            metadata=enriched_metadata,
            memories=None,
            knowledge=ctx.get('knowledge', []),
            error=None,
            should_end=False,
            next_node=None,
            compliance_mode="hipaa",
            compliance_checks=[],
            audit_trail=[],
            contains_phi=False,
            phi_tokens=None,
            de_identified_message=None,
            appointment_type=None,
            preferred_date=None,
            preferred_time=None,
            doctor_id=None,
            patient_id=patient_id,
            patient_name=patient_name,
            insurance_verified=False,
            # Phase 3: Supervisor routing fields
            flow_state=flow_state,
            active_task=ctx.get('active_task'),
            next_agent=None,
            # Phase 2: Guardrail fields
            is_emergency=False,
            phi_detected=False,
            allowed_tools=[],
            blocked_tools=[],
            guardrail_action=None,
            escalation_reason=None,
            # Phase 2: Language detection
            detected_language=ctx.get('language', 'en'),
            # Phase 2: Context hydration
            context_hydrated=bool(ctx.get('clinic_profile')),  # True if pipeline already hydrated
            previous_session_summary=None,
            # Phase 2: Fast path
            fast_path=False,
            lane=ctx.get('lane'),
            # Phase 2: Plan-then-Execute
            action_plan=None,
            plan_results=None,
            plan_completed_steps=[],
            plan_execution_error=None,
            plan_failed_step=None,
            plan_needs_replanning=False,
            # Phase 2: Action Proposal (HITL)
            action_proposal=None,
            awaiting_confirmation=ctx.get('awaiting_confirmation', False),
            pending_action=ctx.get('pending_action'),
            pending_action_timestamp=ctx.get('pending_action_timestamp'),
            pending_action_expired=False,
            user_confirmed=False,
            proposal_verified=False,
            verification_error=None,
        )

        try:
            # Run the graph
            if self.enable_checkpointing:
                result = await self.compiled_graph.ainvoke(
                    initial_state,
                    {"configurable": {"thread_id": session_id}}
                )
            else:
                result = await self.compiled_graph.ainvoke(initial_state)

            # Determine state transition based on result
            state_transition = self._determine_state_transition(result)

            # Return enriched result for pipeline integration
            return {
                'response': result.get('response'),
                'intent': result.get('intent'),
                'audit_trail': result.get('audit_trail', []),
                'state_transition': state_transition,
                'context': result.get('context', {}),
                'should_escalate': result.get('should_end') and 'emergency' in str(result.get('response', '')).lower(),
                'pending_action': result.get('metadata', {}).get('pending_action'),
                # Phase 4-6: Internal tool tracking for eval harness
                'tools_actually_called': result.get('tools_actually_called', []),
                'tools_failed': result.get('tools_failed', []),
                'executor_validation_errors': result.get('executor_validation_errors', []),
                'planner_validation_errors': result.get('planner_validation_errors', []),
                'hallucination_blocked': result.get('hallucination_blocked', False),
                'booking_blocked_no_availability_check': result.get('booking_blocked_no_availability_check', False),
            }

        except Exception as e:
            logger.error(f"Error processing healthcare message: {e}")
            return {
                'session_id': session_id,
                'response': "I encountered an error processing your message. Please try again.",
                'error': str(e),
                'state_transition': None,
            }

    async def process_node(self, state: HealthcareConversationState) -> HealthcareConversationState:
        """
        Healthcare-specific process node that uses enriched context from specialized nodes.

        This overrides base process_node to:
        1. Check for specialized context (price_query, faq_results, etc.)
        2. Inject those results prominently into the LLM prompt
        3. Let LLM generate natural, conversational responses

        Pattern: Nodes enrich context → LLM generates response
        """
        logger.debug(f"Healthcare process_node - session: {state['session_id']}")

        # EARLY RETURN: If response already set by specialized agent, skip generation
        if state.get('response'):
            logger.info(f"[process_node] Response already set ({len(state['response'])} chars), skipping generation")
            state['audit_trail'].append({
                "node": "process",
                "timestamp": datetime.utcnow().isoformat(),
                "skipped": True,
                "reason": "response_already_set"
            })
            return state

        # Check for specialized context that needs to be included in prompt
        pipeline_ctx = state.get('context', {})
        price_query = pipeline_ctx.get('price_query', {})
        faq_results = pipeline_ctx.get('faq_results', [])
        appointment_query = pipeline_ctx.get('appointment_query', {})
        insurance_query = pipeline_ctx.get('insurance_query', {})

        # Build specialized context section for LLM
        specialized_context = []

        # Include price query results if available
        if price_query.get('success') and price_query.get('results'):
            results = price_query['results']
            price_info = "User asked about prices. Here are the matching services:\n"
            for svc in results:
                name = svc.get('name', 'Service')
                price = svc.get('price')
                currency = svc.get('currency', 'USD')
                if price:
                    price_info += f"- {name}: {price} {currency}\n"
                else:
                    price_info += f"- {name}: price varies\n"
            specialized_context.append(price_info)
            logger.info(f"[process_node] Injecting price query results: {len(results)} services")

        # Include FAQ results if available (high confidence matches)
        if faq_results and len(faq_results) > 0:
            faq = faq_results[0]
            if faq.get('relevance_score', 0) > 0.5:
                faq_info = f"Relevant FAQ found:\nQ: {faq.get('question', '')}\nA: {faq.get('answer', '')}"
                specialized_context.append(faq_info)
                logger.info(f"[process_node] Injecting FAQ result")

        # Include appointment query results if available
        if appointment_query:
            action = appointment_query.get('action', 'book')
            appointment_type = appointment_query.get('appointment_type', 'appointment')
            has_availability = appointment_query.get('has_availability', False)
            available_slots = appointment_query.get('available_slots', [])

            if action == 'cancel':
                appt_info = (
                    "User wants to CANCEL an appointment.\n"
                    "Ask them for their appointment ID or the date/time of their appointment to proceed."
                )
            elif action == 'reschedule':
                appt_info = (
                    "User wants to RESCHEDULE an appointment.\n"
                    "Ask them for their current appointment details and their preferred new time."
                )
            elif has_availability and available_slots:
                # Format slots for LLM
                slot_descriptions = []
                for slot in available_slots[:5]:  # Limit to 5 slots
                    start = slot.get('start', '')
                    if start:
                        try:
                            dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                            slot_descriptions.append(dt.strftime('%B %d at %I:%M %p'))
                        except:
                            slot_descriptions.append(start)

                appt_info = (
                    f"User wants to book a {appointment_type}.\n"
                    f"Available time slots ({len(available_slots)} total):\n"
                    + "\n".join(f"- {s}" for s in slot_descriptions)
                    + "\nHelp them choose a time or offer alternatives if needed."
                )
            else:
                # Include current time context to prevent LLM hallucinating past slots
                current_time = datetime.now()
                appt_info = (
                    f"User wants to book a {appointment_type}.\n"
                    f"No availability found for the requested time period.\n"
                    f"Current time is {current_time.strftime('%H:%M')} on {current_time.strftime('%B %d')}.\n"
                    "IMPORTANT: Do NOT suggest any specific times or dates. Instead:\n"
                    "- Ask when they would prefer to come in\n"
                    "- Offer to add them to a waitlist if appropriate\n"
                    "- Suggest they call the clinic for urgent needs"
                )

            specialized_context.append(appt_info)
            logger.info(f"[process_node] Injecting appointment context: action={action}, has_availability={has_availability}")

        # Include insurance query results if available
        if insurance_query:
            needs_info = insurance_query.get('needs_info', True)
            has_provider = insurance_query.get('has_provider_info', False)
            has_member_id = insurance_query.get('has_member_id', False)

            if needs_info:
                missing = []
                if not has_provider:
                    missing.append("insurance provider name")
                if not has_member_id:
                    missing.append("member ID")

                insurance_info = (
                    "User wants to verify their insurance coverage.\n"
                    f"Still need: {', '.join(missing)}.\n"
                    "Ask them for the missing information in a helpful way."
                )
            else:
                insurance_info = (
                    "User wants to verify their insurance coverage.\n"
                    "They've provided their insurance provider and member ID.\n"
                    "Let them know you'll verify their coverage and get back to them, "
                    "or ask if they have any other questions."
                )

            specialized_context.append(insurance_info)
            logger.info(f"[process_node] Injecting insurance context: needs_info={needs_info}")

        # PHASE 4: Include extracted booking info in response generation
        # This ensures patient name, service type, etc. appear in responses
        extracted_booking = state.get('extracted_booking_info', {})
        if extracted_booking:
            booking_parts = ["Current booking request details:"]

            patient_name = extracted_booking.get('patient_name') or state.get('patient_name')
            if patient_name:
                booking_parts.append(f"- Patient: {patient_name}")

            patient_phone = extracted_booking.get('patient_phone') or state.get('patient_phone')
            if patient_phone:
                booking_parts.append(f"- Phone: {patient_phone}")

            service_type = extracted_booking.get('service_type') or state.get('appointment_type')
            if service_type:
                booking_parts.append(f"- Service: {service_type}")

            requested_date = extracted_booking.get('requested_date') or state.get('preferred_date')
            requested_time = extracted_booking.get('requested_time')
            if requested_date or requested_time:
                datetime_str = f"{requested_date or ''} {requested_time or ''}".strip()
                booking_parts.append(f"- Requested time: {datetime_str}")

            doctor_pref = extracted_booking.get('doctor_preference') or state.get('doctor_preference')
            if doctor_pref:
                booking_parts.append(f"- Doctor preference: {doctor_pref}")

            if extracted_booking.get('urgency') == 'urgent' or state.get('is_urgent'):
                booking_parts.append("- URGENT: Patient indicated pain or emergency")

            booking_info = "\n".join(booking_parts)
            booking_info += "\n\nIMPORTANT: Reference these details (especially patient name) in your response."
            specialized_context.append(booking_info)
            logger.info(f"[process_node] Injecting extracted booking info: {list(extracted_booking.keys())}")

        # If we have specialized context, create enhanced prompt
        if specialized_context:
            # Get language for response
            language = state.get('metadata', {}).get('language', 'en')
            language_instruction = {
                'ru': 'Respond in Russian.',
                'es': 'Respond in Spanish.',
                'pt': 'Respond in Portuguese.',
                'he': 'Respond in Hebrew.',
                'en': 'Respond in English.'
            }.get(language, 'Respond in English.')

            specialized_section = "\n\n".join(specialized_context)

            # Build enhanced system prompt
            enhanced_prompt = f"""You are a friendly healthcare assistant. Use the following information to answer the user's question naturally and conversationally.

{specialized_section}

Instructions:
- {language_instruction}
- Be natural and conversational, not robotic
- Present prices in a helpful way, not as a formatted list
- For appointments: mention 2-3 available times naturally, not as a bullet list
- If user wants to cancel/reschedule, be helpful and ask for needed info
- If multiple services match, mention the most relevant ones
- Keep the response concise but friendly"""

            if self.llm_factory:
                try:
                    messages = [
                        {"role": "system", "content": enhanced_prompt},
                    ]

                    # Include conversation history for context continuity
                    # This fixes the bug where follow-up questions lose context
                    conversation_history = state.get('context', {}).get('conversation_history', [])
                    if conversation_history:
                        for msg in conversation_history[-10:]:  # Last 10 messages to avoid token overflow
                            role = msg.get('role', 'user')
                            content = msg.get('content', msg.get('text', ''))
                            if role in ('user', 'assistant') and content:
                                messages.append({"role": role, "content": content})

                    # Add current message
                    messages.append({"role": "user", "content": state['message']})

                    response = await self.llm_factory.generate(
                        messages=messages,
                        model=self.primary_model,
                        temperature=0.7,
                        max_tokens=500
                    )

                    state['response'] = response.content

                    # =========================================================================
                    # Phase 5 (5.3): Validate response against tools - block hallucinated data
                    # =========================================================================
                    validated_response = self._validate_response_against_tools(state, response.content)
                    if validated_response != response.content:
                        logger.warning(f"[process_node] Response modified by hallucination validator")
                        state['response'] = validated_response
                        state['hallucination_blocked'] = True

                    state['metadata']['llm_provider'] = response.provider
                    state['metadata']['llm_model'] = response.model
                    state['metadata']['specialized_context_used'] = True

                    state['audit_trail'].append({
                        "node": "process",
                        "timestamp": datetime.utcnow().isoformat(),
                        "llm_used": True,
                        "specialized_context": True,
                        "context_types": list(pipeline_ctx.keys()),
                        "hallucination_blocked": state.get('hallucination_blocked', False)
                    })

                    return state

                except Exception as e:
                    logger.warning(f"LLM with specialized context failed: {e}, falling back to base")

        # Fall back to base implementation for general queries
        return await super().process_node(state)

    def _determine_state_transition(self, result: Dict[str, Any]) -> Optional[str]:
        """
        Determine flow state transition based on graph result.

        Maps orchestrator outcomes to Phase 3A FlowState values.
        Uses supervisor's flow_state decision (Phase 3) when available.
        """
        # Phase 3: Use supervisor's flow_state if set
        flow_state = result.get('flow_state')
        if flow_state and flow_state != 'idle':
            logger.debug(f"Using supervisor flow_state: {flow_state}")
            return flow_state

        # Check for explicit state in result
        if result.get('should_end'):
            # Check if escalation or completion
            response = str(result.get('response', '')).lower()
            if 'emergency' in response or '911' in response:
                return 'escalated'
            return 'completed'

        # Check intent for booking flow (legacy fallback)
        intent = result.get('intent')
        if intent == 'appointment':
            # Check if appointment was booked
            context = result.get('context', {})
            if context.get('appointment_booked'):
                return 'completed'
            elif context.get('available_slots'):
                return 'presenting_slots'
            else:
                return 'collecting_slots'

        # Info-seeking flows (legacy fallback)
        if intent in ('faq_query', 'price_query', 'insurance'):
            return 'info'

        # No transition needed
        return None


# Example usage
if __name__ == "__main__":
    import asyncio

    async def test_healthcare():
        # Create healthcare orchestrator
        orchestrator = HealthcareLangGraph(
            phi_middleware=None,  # Would use actual PHI service
            appointment_service=None,  # Would use actual appointment service
            enable_emergency_detection=True
        )

        # Test appointment request
        result = await orchestrator.process(
            message="I need to schedule a dental cleaning next week",
            session_id="patient_123",
            patient_id="P456789"
        )

        print(f"Response: {result.get('response')}")
        print(f"Intent: {result.get('intent')}")
        print(f"Contains PHI: {result.get('contains_phi')}")
        print(f"Audit trail: {len(result.get('audit_trail', []))} nodes")

        # Test emergency
        emergency_result = await orchestrator.process(
            message="I have severe chest pain and difficulty breathing",
            session_id="patient_emergency",
            patient_id="P987654"
        )

        print(f"\nEmergency Response: {emergency_result.get('response')}")

    asyncio.run(test_healthcare())