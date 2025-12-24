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

        # Specialized agent nodes
        workflow.add_node("info_agent", self.info_agent_node)  # Combines faq_lookup + price_query
        # Note: Legacy nodes (appointment_handler, price_query, faq_lookup, insurance_verify)
        # were removed - Phase 2 uses planner/executor for scheduling, info_agent for queries

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
        workflow.add_conditional_edges(
            "supervisor",
            self.supervisor_router,
            {
                "scheduling": "planner",  # Phase 2: Plan-then-Execute for scheduling
                "info": "info_agent",
                "exit": "phi_redact",
            }
        )

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
        workflow.add_edge("info_agent", "process")

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
                # Import tool definitions
                from app.tools.tool_definitions import APPOINTMENT_BOOKING_TOOL, PRICE_QUERY_TOOL

                # Prepare messages with appointment context
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a medical appointment assistant. Use tools to book appointments and check pricing. "
                            "Always extract appointment details (date, time, service type) before booking."
                        )
                    },
                    {"role": "user", "content": state['message']}
                ]

                # Call LLM with tools
                response = await self.llm_factory.generate_with_tools(
                    messages=messages,
                    tools=[APPOINTMENT_BOOKING_TOOL, PRICE_QUERY_TOOL],
                    model=self.primary_model,
                    temperature=0.3  # Lower temp for tool calling accuracy
                )

                # Execute tools if called
                if response.tool_calls:
                    for tool_call in response.tool_calls:
                        if tool_call.name == "query_service_prices":
                            # Execute price query
                            if self.price_query_tool:
                                result = await self.price_query_tool.get_services_by_query(
                                    query=tool_call.arguments.get('query'),
                                    limit=5
                                )
                                state['metadata']['tool_results'] = result

                        elif tool_call.name == "book_appointment" and self.appointment_tools:
                            # Execute booking
                            booking_result = await self.appointment_tools.book_appointment(
                                patient_id=state.get('patient_id'),
                                doctor_id=tool_call.arguments.get('doctor_id'),
                                appointment_datetime=tool_call.arguments.get('datetime'),
                                appointment_type=tool_call.arguments.get('service_type'),
                                notes=tool_call.arguments.get('notes')
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
        supervisor_prompt = f"""You are a routing supervisor for a healthcare clinic assistant.

Current state: {flow_state}
Active task: {active_task}

CRITICAL RULE: If flow_state is "scheduling", user is in an active booking conversation.
Short responses like "yes", "да", "ok", numbers, or times should STAY in scheduling.

Route this message to the appropriate agent:
- "scheduling" - Appointment booking, availability, rescheduling, cancellation, PAIN/SYMPTOMS (need to see doctor), OR any response while in scheduling flow
- "info" - FAQ, pricing, hours, location, insurance, service details (only if NOT in scheduling flow and no symptoms)
- "exit" - Explicit goodbyes like "bye", "до свидания", "thanks bye" (NOT simple "ok" or "да")

EXAMPLES (follow these patterns):
User: "How much is a filling?" -> info
User: "What are your hours?" -> info
User: "I need to come in on Tuesday" -> scheduling
User: "Do you have availability for a root canal next week?" -> scheduling (intent is booking)
User: "My tooth hurts" -> scheduling (pain = need to see doctor = booking)
User: "У меня болит зуб" -> scheduling (Russian: my tooth hurts = needs appointment)
User: "I'm in pain" -> scheduling (needs urgent appointment)
User: "Thanks, bye!" -> exit
User: "Okay" (in scheduling flow) -> scheduling (continue current task)
User: "Да" (in scheduling flow) -> scheduling (Russian "yes" - continue booking)
User: "16" or "16:00" (in scheduling flow) -> scheduling (time selection)
User: "Actually, never mind" -> exit
User: "How much does a cleaning cost?" -> info
User: "Can I book an appointment?" -> scheduling
User: "What services do you offer?" -> info
User: "I want to cancel my appointment" -> scheduling
User: "Да" or "Yes" (confirming offered slot) -> scheduling
User: "острая боль" -> scheduling (acute pain = urgent appointment needed)

User message: {message}

Respond with ONLY one word: scheduling, info, or exit"""

        if self.llm_factory:
            try:
                response = await self.llm_factory.generate(
                    messages=[{"role": "system", "content": supervisor_prompt}],
                    model="gpt-4o-mini",  # Fast, cheap model for routing
                    temperature=0.1,
                    max_tokens=10,
                )

                decision = response.content.strip().lower()
                # Clean up common variations
                if "scheduling" in decision:
                    decision = "scheduling"
                elif "info" in decision:
                    decision = "info"
                elif "exit" in decision:
                    decision = "exit"
                else:
                    decision = "info"  # Safe fallback

                logger.info(f"[supervisor] Routing decision: {decision} for message: {message[:50]}...")

            except Exception as e:
                logger.warning(f"Supervisor LLM call failed: {e}, defaulting to info")
                decision = "info"
        else:
            # Fallback to keyword-based routing if no LLM factory
            message_lower = message.lower()
            if any(word in message_lower for word in ['book', 'appointment', 'schedule', 'reschedule', 'cancel', 'availability', 'available']):
                decision = "scheduling"
            elif any(word in message_lower for word in ['bye', 'thanks', 'thank you', 'goodbye', 'ok', 'okay']):
                decision = "exit"
            else:
                decision = "info"
            logger.info(f"[supervisor] Keyword-based routing: {decision}")

        state["next_agent"] = decision

        # Update flow_state based on decision
        if decision == "scheduling" and flow_state != FlowState.SCHEDULING.value:
            state["flow_state"] = FlowState.SCHEDULING.value
        elif decision == "info" and flow_state not in [FlowState.SCHEDULING.value]:
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
        return state.get("next_agent", "info")

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
        """
        logger.debug(f"Simple answer node - session: {state['session_id']}")

        message = state.get('message', '').lower()
        ctx = state.get('context', {})
        language = state.get('detected_language', 'en')

        # FAQ patterns
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

        # Determine action type from message
        action_type = 'book'  # Default
        if any(w in message for w in ['cancel', 'cancelar', 'отменить']):
            action_type = 'cancel'
        elif any(w in message for w in ['reschedule', 'reprogramar', 'перенести']):
            action_type = 'reschedule'

        # Build plan based on action type
        steps = []

        if action_type == 'book':
            # Standard booking flow
            # check_availability expects: doctor_id, date, appointment_type, duration_minutes
            steps = [
                PlanStep(
                    action=ActionType.CHECK_AVAILABILITY,
                    tool_name="check_availability",
                    arguments={
                        "doctor_id": state.get('doctor_id'),
                        "date": state.get('preferred_date'),
                        "appointment_type": state.get('appointment_type', 'general'),
                        "duration_minutes": 30,
                    },
                    requires_confirmation=False,
                    description="Check available appointment slots"
                ),
                PlanStep(
                    action=ActionType.BOOK_APPOINTMENT,
                    tool_name="book_appointment",
                    arguments={
                        "patient_id": state.get('patient_id') or "unknown",
                        "doctor_id": state.get('doctor_id'),
                        "datetime_str": state.get('preferred_date'),  # Will be set from available slots
                        "appointment_type": state.get('appointment_type', 'general'),
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
                        "doctor_id": state.get('doctor_id'),
                        "date": state.get('preferred_date'),
                        "appointment_type": state.get('appointment_type', 'general'),
                        "duration_minutes": 30,
                    },
                    requires_confirmation=False,
                    description="Find new available slots"
                ),
                PlanStep(
                    action=ActionType.RESCHEDULE_APPOINTMENT,
                    tool_name="reschedule_appointment",
                    arguments={"patient_id": state.get('patient_id')},
                    requires_confirmation=True,
                    description="Move appointment to new time"
                ),
            ]
            goal = "Reschedule patient's appointment"

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
        Execute action plan step by step.

        This node:
        1. Gets next step from plan
        2. Checks if step requires confirmation
        3. If confirmation needed, creates ActionProposal and pauses
        4. If no confirmation needed, executes step
        5. Handles errors and replanning triggers
        """
        logger.info(f"[executor] Starting - session: {state.get('session_id', 'unknown')[:8]}")
        logger.info(f"[executor] State keys: {list(state.keys())}")

        plan = state.get('action_plan')
        if not plan:
            logger.warning(f"[executor] No plan to execute - state has action_plan: {state.get('action_plan')}")
            # Generate a response explaining we need more context
            state['response'] = state.get('response', "I'll help you book an appointment. What service do you need?")
            return state

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

        # Check if step requires confirmation
        if next_step.get('requires_confirmation'):
            # Check if user already confirmed
            if state.get('user_confirmed'):
                # User confirmed - execute the step
                logger.info(f"[executor] User confirmed, executing {step_name}")
                state['user_confirmed'] = False  # Reset for next confirmation
                state['awaiting_confirmation'] = False
            else:
                # Need confirmation - create proposal
                proposal = ActionProposal(
                    type=ActionProposalType.BOOK_APPOINTMENT,  # Adjust based on step
                    patient_id=state.get('patient_id', ''),
                    provider_id=next_step.get('arguments', {}).get('doctor_id'),
                    slot=next_step.get('arguments', {}).get('slot'),
                    human_summary=next_step.get('description', 'Complete the action'),
                    execution_params=next_step.get('arguments', {}),
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
                })

                return state

        # Execute the step (no confirmation needed or already confirmed)
        try:
            # Get the appropriate tool
            tool = self._get_tool_for_step(next_step)

            if tool:
                arguments = next_step.get('arguments', {})
                result = await tool(**arguments)

                # Store result
                if not state.get('plan_results'):
                    state['plan_results'] = {'outputs': {}}
                state['plan_results']['outputs'][step_name] = result

                # Mark step as completed
                completed_steps.append(step_name)
                state['plan_completed_steps'] = completed_steps

                logger.info(f"[executor] Step {step_name} completed successfully")
            else:
                logger.warning(f"[executor] No tool found for {step_name}")
                state['plan_execution_error'] = f"Tool not found: {step_name}"

        except Exception as e:
            logger.error(f"[executor] Step {step_name} failed: {e}")
            state['plan_execution_error'] = str(e)
            state['plan_failed_step'] = step_name

            # Check if we should replan
            if next_step_idx < len(steps) - 1:
                state['plan_needs_replanning'] = True

        state['audit_trail'].append({
            "node": "executor",
            "timestamp": datetime.utcnow().isoformat(),
            "step": step_name,
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
                    state['metadata']['llm_provider'] = response.provider
                    state['metadata']['llm_model'] = response.model
                    state['metadata']['specialized_context_used'] = True

                    state['audit_trail'].append({
                        "node": "process",
                        "timestamp": datetime.utcnow().isoformat(),
                        "llm_used": True,
                        "specialized_context": True,
                        "context_types": list(pipeline_ctx.keys())
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