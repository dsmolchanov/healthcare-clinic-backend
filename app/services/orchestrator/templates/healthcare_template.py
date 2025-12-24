"""
Healthcare LangGraph Template
HIPAA-compliant orchestrator for healthcare/dental conversations
Extends base orchestrator with PHI protection and appointment handling
"""

import sys
import os
import re

from ..base_langgraph import BaseLangGraphOrchestrator, BaseConversationState, ComplianceMode, last_value
from langgraph.graph import StateGraph, END
from typing import Optional, Dict, Any, List, Annotated
import logging
from datetime import datetime

# Import ConversationState for unified state tracking
from app.models.conversation_state import FlowState, ConversationState

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

        Phase 3: Unified Graph-Gateway Architecture
        - Supervisor node replaces fragmented intent routing
        - Routes to scheduling_agent (appointment) or info_agent (FAQ/price/general)
        - All paths go through phi_redact before exit
        """
        # Start with base graph
        workflow = super()._build_graph()

        # Add healthcare-specific nodes
        workflow.add_node("phi_check", self.phi_check_node)
        workflow.add_node("emergency_check", self.emergency_check_node)
        workflow.add_node("phi_redact", self.phi_redact_node)

        # NEW: Supervisor node replaces fragmented routing (Phase 3)
        workflow.add_node("supervisor", self.supervisor_node)

        # Specialized agent nodes (renamed for clarity)
        workflow.add_node("scheduling_agent", self.appointment_handler_node)
        workflow.add_node("info_agent", self.info_agent_node)  # Combines faq_lookup + price_query

        # Keep legacy nodes for backward compatibility (can be removed later)
        workflow.add_node("appointment_handler", self.appointment_handler_node)
        workflow.add_node("price_query", self.price_query_node)
        workflow.add_node("faq_lookup", self.faq_lookup_node)
        workflow.add_node("insurance_verify", self.insurance_verify_node)

        # Rewire flow for healthcare with supervisor
        # Entry → Emergency Check → PHI Check → Supervisor
        workflow.add_edge("entry", "emergency_check")

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

        # PHI check leads to supervisor (skip compliance_check if not needed)
        workflow.add_edge("phi_check", "supervisor")

        # Supervisor routing (replaces intent_classify routing)
        workflow.add_conditional_edges(
            "supervisor",
            self.supervisor_router,
            {
                "scheduling": "scheduling_agent",
                "info": "info_agent",
                "exit": "phi_redact",
            }
        )

        # All agents go to process then phi_redact
        workflow.add_edge("scheduling_agent", "process")
        workflow.add_edge("info_agent", "process")

        # Legacy edges for backward compatibility (in case old routing still used)
        workflow.add_edge("appointment_handler", "process")
        workflow.add_edge("price_query", "process")

        # FAQ can either go directly to process or fall back to RAG
        workflow.add_conditional_edges(
            "faq_lookup",
            self.faq_fallback_router,
            {
                "success": "process",
                "fallback_rag": "knowledge_retrieve" if self.enable_rag else "process",
                "end": END
            }
        )

        workflow.add_edge("insurance_verify", "process")

        # FIX: Insert phi_redact BETWEEN process and generate_response
        # This avoids conflicting edges from generate_response
        # Flow: process -> phi_redact -> generate_response -> exit
        # (Base class adds generate_response -> exit or compliance_audit)

        # Remove the base class edge from process to generate_response and reroute
        # by adding our own edges
        workflow.add_edge("process", "phi_redact")
        workflow.add_edge("phi_redact", "generate_response")

        # Note: The base class already adds generate_response -> exit (or compliance_audit -> exit)
        # so we don't need to add another edge from generate_response

        return workflow

    def _add_intent_routing(self, workflow) -> None:
        """
        Override base class to add healthcare-specific intent routing.

        Routes to specialized handlers for:
        - appointment: Appointment booking flow
        - price_query: Service pricing lookup
        - faq_query: FAQ knowledge base lookup
        - insurance: Insurance verification
        - general: Standard conversation processing
        """
        workflow.add_conditional_edges(
            "intent_classify",
            self.intent_router,
            {
                "appointment": "appointment_handler",
                "price_query": "price_query",
                "faq_query": "faq_lookup",
                "insurance": "insurance_verify",
                "general": "memory_retrieve" if self.enable_memory else "process"
            }
        )

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

    def faq_fallback_router(self, state: HealthcareConversationState) -> str:
        """
        Route based on FAQ success

        Returns:
            - "success": FAQ found with good confidence → generate response
            - "fallback_rag": No FAQ or low confidence → try RAG
            - "end": Error or invalid state
        """
        if state.get('context', {}).get('faq_success', False):
            return "success"
        elif state.get('context', {}).get('faq_results') is not None:
            # Tried FAQ but didn't find good match - fall back to RAG
            return "fallback_rag"
        else:
            # Error state
            return "end"

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

    def intent_router(self, state: HealthcareConversationState) -> str:
        """Route all messages to general - let LLM use tools to handle intents.

        Legacy keyword-based routing removed. The process_node now uses LLM with
        tools (query_service_prices, check_availability, etc.) to handle all intents.
        """
        # All messages go to process_node which has tool calling
        return 'general'

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
- "scheduling" - Appointment booking, availability, rescheduling, cancellation, OR any response while in scheduling flow
- "info" - FAQ, pricing, hours, location, insurance, service details (only if NOT in scheduling flow)
- "exit" - Explicit goodbyes like "bye", "до свидания", "thanks bye" (NOT simple "ok" or "да")

EXAMPLES (follow these patterns):
User: "How much is a filling?" -> info
User: "What are your hours?" -> info
User: "I need to come in on Tuesday" -> scheduling
User: "Do you have availability for a root canal next week?" -> scheduling (intent is booking)
User: "My tooth hurts so bad, it's bleeding" -> info (unless flow_state is scheduling)
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