"""
Healthcare LangGraph Template
HIPAA-compliant orchestrator for healthcare/dental conversations
Extends base orchestrator with PHI protection and appointment handling
"""

import sys
import os

from ..base_langgraph import BaseLangGraphOrchestrator, BaseConversationState, ComplianceMode, last_value
from langgraph.graph import StateGraph, END
from typing import Optional, Dict, Any, List, Annotated
import logging
from datetime import datetime

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
        Build healthcare-specific workflow graph
        Adds PHI protection and appointment nodes
        """
        # Start with base graph
        workflow = super()._build_graph()

        # Add healthcare-specific nodes
        workflow.add_node("phi_check", self.phi_check_node)
        workflow.add_node("emergency_check", self.emergency_check_node)
        workflow.add_node("appointment_handler", self.appointment_handler_node)
        workflow.add_node("price_query", self.price_query_node)
        workflow.add_node("faq_lookup", self.faq_lookup_node)
        workflow.add_node("insurance_verify", self.insurance_verify_node)
        workflow.add_node("phi_redact", self.phi_redact_node)

        # Rewire flow for healthcare
        # Entry → Emergency Check → PHI Check → Compliance → Intent
        workflow.add_edge("entry", "emergency_check")

        if self.enable_emergency_detection:
            workflow.add_conditional_edges(
                "emergency_check",
                self.emergency_router,
                {
                    "emergency": "exit",  # Immediate escalation
                    "normal": "phi_check"
                }
            )
        else:
            workflow.add_edge("emergency_check", "phi_check")

        workflow.add_edge("phi_check", "compliance_check")

        # Intent routing is handled by _add_intent_routing() override

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

        # Add PHI redaction before exit
        # Note: generate_response_node now preserves response from process_node (no duplicate LLM call)
        workflow.add_edge("generate_response", "phi_redact")
        workflow.add_edge("phi_redact", "memory_store" if self.enable_memory else "exit")

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

        # Use appointment tools if available
        if self.appointment_tools:
            # Check if user wants to book, cancel, or reschedule
            if any(word in message for word in ['cancel', 'cancellation']):
                # Handle cancellation
                state['response'] = (
                    "I can help you cancel your appointment. "
                    "Please provide your appointment ID or the date/time of your appointment."
                )
            elif any(word in message for word in ['reschedule', 'change', 'move']):
                # Handle rescheduling
                state['response'] = (
                    "I can help you reschedule your appointment. "
                    "Please provide your current appointment details and your preferred new time."
                )
            else:
                # Check availability for new appointment
                availability_result = await self.appointment_tools.check_availability(
                    doctor_id=state.get('doctor_id'),
                    date=state.get('preferred_date'),
                    appointment_type=state['appointment_type'],
                    duration_minutes=30 if state['appointment_type'] == 'checkup' else 60
                )

                if availability_result['success'] and availability_result.get('available_slots'):
                    slots = availability_result['available_slots']
                    state['context']['available_slots'] = slots

                    # Format response with available times
                    if len(slots) > 3:
                        # Show first 3 slots
                        slot_list = []
                        for slot in slots[:3]:
                            start = datetime.fromisoformat(slot['start'])
                            slot_list.append(start.strftime('%B %d at %I:%M %p'))

                        state['response'] = (
                            f"I can help you schedule a {state['appointment_type']}. "
                            f"Here are some available times:\n"
                            f"• {slot_list[0]}\n"
                            f"• {slot_list[1]}\n"
                            f"• {slot_list[2]}\n"
                            f"\nWould any of these work for you? I have {len(slots)} total slots available."
                        )
                    else:
                        state['response'] = (
                            f"I can help you schedule a {state['appointment_type']}. "
                            f"We have {len(slots)} available slots. "
                            "What date and time works best for you?"
                        )
                else:
                    state['response'] = (
                        "I apologize, but we don't have any immediate availability. "
                        "Would you like to check another date or be added to our waitlist?"
                    )
        elif self.appointment_service:
            # Fall back to original appointment service if available
            available_slots = await self.appointment_service.get_available_slots(
                appointment_type=state['appointment_type'],
                date_range=7
            )

            if available_slots:
                state['context']['available_slots'] = available_slots
                state['response'] = (
                    f"I can help you schedule a {state['appointment_type']}. "
                    f"We have {len(available_slots)} available slots in the next week. "
                    "What date and time works best for you?"
                )
            else:
                state['response'] = (
                    "I apologize, but we don't have any immediate availability. "
                    "Would you like to be added to our waitlist?"
                )
        else:
            state['response'] = "I'll help you schedule an appointment. Please provide your preferred date and time."

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
        noise_words = [
            'how', 'much', 'is', 'the', 'what', 'price', 'cost', 'fee', 'of', 'for', 'a', 'an',
            'сколько', 'стоит', 'цена', 'стоимость', 'какая', 'какой', 'у', 'вас',
            'cuánto', 'cuesta', 'precio', 'cuanto', 'el', 'la', 'los', 'las', 'de', 'para',
            '?', ',', '.', '!', '¿', '¡'
        ]
        for word in noise_words:
            search_terms = search_terms.replace(word, ' ')
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
        """Verify insurance information"""
        logger.debug(f"Insurance verification - session: {state['session_id']}")

        # Placeholder for insurance verification
        state['insurance_verified'] = False
        state['response'] = (
            "I can help verify your insurance coverage. "
            "Please provide your insurance provider and member ID."
        )

        state['audit_trail'].append({
            "node": "insurance_verify",
            "timestamp": datetime.utcnow().isoformat(),
            "verified": state['insurance_verified']
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
        """Route based on classified intent"""
        # Check for FAQ queries using keyword detection
        message_lower = state.get('message', '').lower()

        # FAQ keywords (hours, location, insurance, parking, etc.)
        if any(word in message_lower for word in ['hours', 'open', 'location', 'address', 'where', 'parking', 'do you accept', 'do you offer']):
            return 'faq_query'

        # Price queries
        if any(word in message_lower for word in ['price', 'cost', 'fee', 'how much', 'сколько', 'стоит', 'цена', 'стоимость']):
            return 'price_query'

        intent = state.get('intent', 'general')
        if intent == 'appointment':
            return 'appointment'
        elif intent == 'insurance':
            return 'insurance'
        else:
            return 'general'

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
            insurance_verified=False
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

        # Check for specialized context that needs to be included in prompt
        pipeline_ctx = state.get('context', {})
        price_query = pipeline_ctx.get('price_query', {})
        faq_results = pipeline_ctx.get('faq_results', [])

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
- If multiple services match, mention the most relevant ones
- Don't say "Here are the prices" - be more natural
- Keep the response concise but friendly"""

            if self.llm_factory:
                try:
                    messages = [
                        {"role": "system", "content": enhanced_prompt},
                        {"role": "user", "content": state['message']}
                    ]

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
        """
        # Check for explicit state in result
        if result.get('should_end'):
            # Check if escalation or completion
            response = str(result.get('response', '')).lower()
            if 'emergency' in response or '911' in response:
                return 'escalated'
            return 'completed'

        # Check intent for booking flow
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

        # Info-seeking flows
        if intent in ('faq_query', 'price_query', 'insurance'):
            return 'info_seeking'

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