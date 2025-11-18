"""
Multilingual Message Processor with Dynamic Language Support
Handles WhatsApp messages with AI processing in ANY language
"""

import os
import json
import uuid
import time
import asyncio
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List, Optional, Set, Tuple

import logging
from pydantic import BaseModel, Field

from supabase import create_client, Client

# Phase 0-6: Import constraint management services
from app.services.session_manager import SessionManager
from app.services.conversation_constraints import ConstraintsManager, ConversationConstraints
from app.services.constraint_extractor import ConstraintExtractor
from app.services.tool_state_gate import ToolStateGate
from app.services.state_echo_formatter import StateEchoFormatter

logger = logging.getLogger(__name__)

# Pinecone removed (Phase 3) - using Supabase FTS + Redis cache instead

_llm_factory: Optional[Any] = None
_supabase_client: Optional[Client] = None
_openai_embeddings_client: Optional[Any] = None


async def get_llm_factory():
    """Return a cached LLM factory, creating it lazily."""
    from app.services.llm.llm_factory import LLMFactory

    global _llm_factory
    if _llm_factory is None:
        init_start = time.time()
        supabase = get_supabase_client()
        if not supabase:
            raise RuntimeError("Supabase client is required for LLM factory")

        _llm_factory = LLMFactory(supabase)
        logger.info("✅ INIT LLM Factory - Complete (%.2fs)", time.time() - init_start)

    return _llm_factory


def get_supabase_client() -> Optional[Client]:
    """Return a cached Supabase client, creating it lazily."""

    global _supabase_client
    if _supabase_client is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

        if not url or not key:
            logger.warning("Supabase credentials missing; skipping Supabase client initialization")
            return None

        init_start = time.time()
        try:
            _supabase_client = create_client(url, key)
            logger.info("✅ INIT Supabase - Complete (%.2fs)", time.time() - init_start)
        except Exception as exc:
            logger.warning("⚠️ INIT Supabase - Failed: %s", exc)
            _supabase_client = None
            return None

    return _supabase_client


async def get_llm_factory():
    """Return LLM factory for all LLM operations."""
    from app.services.llm.llm_factory import LLMFactory

    global _llm_factory
    if _llm_factory is None:
        supabase = get_supabase_client()
        if not supabase:
            raise RuntimeError("Supabase client is required for LLM factory")

        _llm_factory = LLMFactory(supabase)
        logger.info("✅ INIT LLM Factory - Complete")

    return _llm_factory

class MessageRequest(BaseModel):
    """Request model for incoming WhatsApp messages"""
    from_phone: str
    to_phone: str
    body: str
    message_sid: str
    clinic_id: str
    clinic_name: str
    message_type: str = "text"
    media_url: Optional[str] = None
    channel: str = "whatsapp"
    profile_name: str = "Usuario"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class MessageResponse(BaseModel):
    """Response model for processed messages"""
    message: str
    session_id: str
    status: str = "success"
    detected_language: str = "unknown"
    metadata: Dict[str, Any] = Field(default_factory=dict)


# PineconeKnowledgeBase removed (Phase 3) - using Supabase FTS instead


class MultilingualMessageProcessor:
    """Multilingual message processor with automatic language detection, RAG support, and persistent memory"""

    def __init__(self):
        # Import memory manager
        from app.memory.conversation_memory import get_memory_manager
        self.memory_manager = get_memory_manager()

        # Initialize async message logger (combined RPC for conversation + metrics)
        from app.api.async_message_logger import AsyncMessageLogger
        strict_logging = os.getenv("CONVERSATION_LOG_FAIL_FAST", "false").lower() == "true"
        self.message_logger = AsyncMessageLogger(get_supabase_client(), strict=strict_logging)

        # Initialize response analyzer for conversation turn tracking
        from app.services.response_analyzer import ResponseAnalyzer
        self.response_analyzer = ResponseAnalyzer()

        # Phase 6: Lazy-load escalation and follow-up handlers
        from app.services.escalation_handler import EscalationHandler
        from app.services.followup_scheduler import FollowupScheduler
        self.escalation_handler = EscalationHandler()
        self.followup_scheduler = FollowupScheduler()

        # Phase 0-6: Initialize constraint management services
        from app.config import get_redis_client
        redis_client = get_redis_client()
        self.session_manager = SessionManager(redis_client, get_supabase_client())
        self.constraints_manager = ConstraintsManager(redis_client)
        self.constraint_extractor = ConstraintExtractor()
        self.state_gate = ToolStateGate()
        self.state_echo_formatter = StateEchoFormatter()

        self._org_to_clinic_cache: Dict[str, str] = {}
        self._known_clinic_ids: Set[str] = set()
        self._patient_upsert_cache: Dict[Tuple[str, str], float] = {}
        self._patient_upsert_cache_ttl = max(int(os.getenv("PATIENT_UPSERT_CACHE_SECONDS", "120")), 0)
        self._clinic_cache_warm_timestamps: Dict[str, float] = {}
        self._clinic_cache_inflight: Set[str] = set()
        self._clinic_cache_warm_ttl = int(os.getenv("CLINIC_CACHE_WARM_TTL_SECONDS", "900"))
        self._clinic_profile_cache: Dict[str, Dict[str, Any]] = {}

    async def process_message(self, request: MessageRequest) -> MessageResponse:
        """Process incoming WhatsApp message with AI, RAG, and persistent memory"""

        # Phase 8: Import asyncio for parallel I/O
        import asyncio
        import time

        # Track processing start time for metrics
        processing_start_time = time.time()

        # Ensure we have a valid phone number for downstream systems
        if not request.from_phone or request.from_phone.lower() == 'unknown':
            fallback_phone = None
            if isinstance(request.metadata, dict):
                fallback_phone = (
                    request.metadata.get('from_number')
                    or request.metadata.get('phone_number')
                    or request.metadata.get('from')
                )
            if not fallback_phone and request.message_sid and request.message_sid.startswith('whatsapp_'):
                parts = request.message_sid.split('_', 2)
                if len(parts) > 1 and parts[1]:
                    fallback_phone = parts[1]

            if fallback_phone:
                request.from_phone = fallback_phone
                if isinstance(request.metadata, dict):
                    request.metadata.setdefault('from_number', fallback_phone)
                    request.metadata.setdefault('phone_number', fallback_phone)
                    request.metadata.setdefault('from', fallback_phone)

        # Store request data for logging
        self.current_from_phone = request.from_phone
        self.current_to_phone = request.to_phone
        self.current_message_sid = request.message_sid

        resolved_request_clinic_id = self._get_clinic_id_from_organization(request.clinic_id)

        # Get or create persistent session (needs to run first)
        session = await self.memory_manager.get_or_create_session(
            phone_number=request.from_phone,
            clinic_id=resolved_request_clinic_id or request.clinic_id,
            channel=request.channel
        )

        session_id = session['id']

        raw_clinic_identifier = (
            (session.get('metadata') or {}).get('clinic_id')
            or session.get('clinic_id')
            or resolved_request_clinic_id
            or request.clinic_id
        )

        effective_clinic_id = self._get_clinic_id_from_organization(raw_clinic_identifier)

        # Phase 0: Check session boundary (prevents old state from polluting new conversations)
        managed_session_id, is_new_session = await self.session_manager.check_and_manage_boundary(
            phone=request.from_phone,
            clinic_id=effective_clinic_id or request.clinic_id,
            message=request.body,
            current_time=datetime.utcnow()
        )

        # If new session detected, clear constraints from previous episode
        if is_new_session:
            logger.info(f"🆕 New session detected: {managed_session_id}")
            await self.constraints_manager.clear_constraints(managed_session_id)

            # Get carryover data from previous session (language, allergies, etc.)
            carryover = await self.session_manager.get_carryover_data(managed_session_id)

            # Note: Carryover reminders will be added to additional_context later if needed

        if effective_clinic_id:
            self._known_clinic_ids.add(effective_clinic_id)

        # CONSOLIDATED HYDRATION: Use Task #2 CacheService to load all context in <100ms
        # Replaces 7-8 separate queries with single optimized call
        from app.services.cache_service import CacheService
        from app.config import get_redis_client

        cache_service = CacheService(
            redis_client=get_redis_client(),
            supabase_client=get_supabase_client()
        )

        # Hydrate complete context (clinic, patient, session_state)
        hydrated = await cache_service.hydrate_context(
            clinic_id=effective_clinic_id,
            phone=request.from_phone,
            session_id=session_id
        )

        # Extract hydrated data
        clinic_profile = hydrated.get('clinic', {})
        patient_profile = hydrated.get('patient', {})
        session_state_data = hydrated.get('session_state', {})

        # Make services available for router
        clinic_services = hydrated.get('services', [])
        clinic_doctors = hydrated.get('doctors', [])
        clinic_faqs = hydrated.get('faqs', [])

        resolved_clinic_name = (
            clinic_profile.get('name')
            or (session.get('name') if isinstance(session, dict) else None)
            or request.clinic_name
            or "Clinic"
        )

        if self._should_warm_clinic_cache(effective_clinic_id):
            self._clinic_cache_inflight.add(effective_clinic_id)
            asyncio.create_task(self._warm_clinic_cache(effective_clinic_id))

        # Create or update patient record from WhatsApp contact
        await self._upsert_patient_from_whatsapp(
            clinic_id=effective_clinic_id or request.clinic_id,
            phone=request.from_phone,
            profile_name=request.profile_name,
            detected_language=None  # Will be detected later
        )

        # Kick off message storage (fire-and-forget)
        store_msg_task = asyncio.create_task(
            self.memory_manager.store_message(
                session_id=session_id,
                role='user',
                content=request.body,
                phone_number=request.from_phone,
                metadata={
                    'message_sid': request.message_sid,
                    'profile_name': request.profile_name,
                    'clinic_id': effective_clinic_id,
                    'from_number': request.from_phone,
                    'channel': request.channel,
                    'instance_name': request.metadata.get('instance_name') if isinstance(request.metadata, dict) else None
                }
            )
        )

        # Memory-specific queries still run in parallel (not part of clinic bundle)
        history_task = self.memory_manager.get_conversation_history(
            phone_number=request.from_phone,
            clinic_id=effective_clinic_id,
            limit=20,
            include_all_sessions=True
        )

        prefs_task = self.memory_manager.get_user_preferences(
            phone_number=request.from_phone,
            clinic_id=effective_clinic_id
        )

        memory_task = self.memory_manager.get_memory_context(
            phone_number=request.from_phone,
            clinic_id=effective_clinic_id,
            query=request.body
        )

        # Gather memory queries in parallel (return_exceptions to not fail on single error)
        results = await asyncio.gather(
            history_task,
            prefs_task,
            memory_task,
            return_exceptions=True
        )

        # Unpack results with error handling
        conversation_history = results[0] if not isinstance(results[0], Exception) else []
        user_preferences = results[1] if not isinstance(results[1], Exception) else {}
        memory_context = results[2] if not isinstance(results[2], Exception) else []

        user_preferences = user_preferences or {}
        is_new_conversation = len(conversation_history) == 0
        patient_name = None
        patient_id = None

        if patient_profile:
            patient_id = patient_profile.get('id')
            first_name = (patient_profile.get('first_name') or '').strip()
            last_name = (patient_profile.get('last_name') or '').strip()

            generic_names = {'whatsapp', 'unknown', 'user'}
            first_is_generic = first_name.lower() in generic_names
            last_is_generic = last_name.lower() in generic_names or not last_name

            if first_name and not first_is_generic:
                patient_name = first_name
                if last_name and not last_is_generic:
                    patient_name = f"{first_name} {last_name}".strip()

            if patient_name and not user_preferences.get('preferred_name'):
                user_preferences['preferred_name'] = patient_name

        # Log any errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Parallel I/O error in task {i}: {result}")

        # Format history for AI context
        session_messages = []
        for msg in conversation_history[-10:]:  # Use last 10 messages for context
            session_messages.append({
                'role': msg.get('role', 'user'),
                'content': msg.get('content', '')
            })

        # Check if agent has pending action
        session_turn_status = session.get('turn_status')
        last_agent_action = session.get('last_agent_action')
        pending_since = session.get('pending_since')

        additional_context = ""

        if session_turn_status == 'agent_action_pending' and last_agent_action:
            # Agent previously promised to get back
            time_pending = ""
            if pending_since:
                try:
                    from datetime import timezone
                    pending_dt = datetime.fromisoformat(pending_since.replace('Z', '+00:00'))
                    hours_pending = (datetime.now(timezone.utc) - pending_dt).total_seconds() / 3600
                    time_pending = f" (pending for {hours_pending:.1f} hours)"
                except Exception:
                    pass

            additional_context = f"""

⚠️ CRITICAL CONTEXT - YOU PREVIOUSLY PROMISED TO FOLLOW UP:
In your last message, you told the user: "{last_agent_action}"{time_pending}

The user is now following up. You MUST:
1. Acknowledge you said you'd get back to them
2. Provide the answer or information you promised
3. If you still don't have the answer, apologize and escalate to a human

DO NOT say "let me check" again. Either provide substantive information or escalate.
"""
            logger.warning(f"⚠️ Injecting pending action context: {last_agent_action}")

        elif session_turn_status == 'escalated':
            additional_context = """

This conversation has been escalated to a human agent.
Provide a brief acknowledgment that their request is being handled by the team.
DO NOT attempt to answer complex questions yourself.
"""

        conversation_state_context = (
            "This is the first turn with this user. Provide a warm introduction, confirm clinic details, and collect any necessary intake information before addressing their request."
            if is_new_conversation else
            "The user has chatted with the clinic before. Maintain continuity, reference any relevant prior context, and move quickly to the substance of their request."
        )

        if additional_context:
            additional_context += f"\n\n{conversation_state_context}"
        else:
            additional_context = conversation_state_context

        # FAQ/Price queries now handled by direct lane (Phase 1)
        # No need for FAQ FTS here - it's done in direct_tool_executor with Redis cache

        # Pinecone RAG removed - using Supabase FTS via direct lane and tools
        # All knowledge queries now handled by:
        # 1. Direct lane for FAQ/Price (Redis cache + Supabase FTS)
        # 2. LLM with tools for complex queries (no RAG needed)
        logger.info(f"Processing with simplified flow (no RAG)")

        # No RAG knowledge context (removed in Phase 3)
        relevant_knowledge = []

        # Phase 6: Check if conversation should be escalated BEFORE generating response
        if self.escalation_handler:
            escalation_check = await self.escalation_handler.check_if_should_escalate(
                conversation_context="\n".join([
                    f"{msg['role']}: {msg['content']}"
                    for msg in session_messages[-5:]
                ]),
                user_message=request.body
            )

            if escalation_check['should_escalate']:
                logger.warning(f"⚠️ Escalating conversation: {escalation_check['reason']}")

                escalation_result = await self.escalation_handler.escalate_conversation(
                    session_id=session_id,
                    reason=escalation_check['reason'],
                    metadata={'confidence': escalation_check['confidence']}
                )

                # Store escalation message
                await self.memory_manager.store_message(
                    session_id=session_id,
                    role='assistant',
                    content=escalation_result['holding_message'],
                    phone_number=request.from_phone,
                    metadata={
                        'escalated': True,
                        'reason': escalation_check['reason']
                    }
                )

                # Return holding message instead of regular response
                return MessageResponse(
                    message=escalation_result['holding_message'],
                    session_id=session_id,
                    status="success",
                    detected_language="multilingual",
                    metadata={
                        'escalated': True,
                        'reason': escalation_check['reason']
                    }
                )

        # Extract name from user message if present
        extracted_first, extracted_last = self._extract_name_from_message(request.body)

        # FAST-PATH ROUTING: Classify message and handle FAQ/PRICE queries without LLM
        from app.services.router_service import RouterService, Lane
        from app.services.fast_path_service import FastPathService
        from app.services.language_service import LanguageService
        from app.services.session_service import SessionService
        from app.config import get_redis_client

        # Initialize services for routing
        redis_client = get_redis_client()
        language_service = LanguageService(redis_client)
        session_service = SessionService(get_supabase_client())

        # Build context for router (using hydrated data + memory)
        router_context = {
            'patient': {
                'id': patient_id,
                'name': patient_name,
                'phone': request.from_phone
            },
            'clinic': {
                'id': effective_clinic_id,
                'name': resolved_clinic_name,
                'services': clinic_services,  # From hydrated context
                'doctors': clinic_doctors,    # From hydrated context
                'faqs': clinic_faqs          # From hydrated context
            },
            'session_state': {
                'turn_status': session_turn_status,
                'last_agent_action': last_agent_action
            },
            'history': session_messages,
            'memory': memory_context,  # Add memory for fast-path personalization
            'preferences': user_preferences  # Add preferences for personalization
        }

        # Classify message into lane
        router = RouterService(language_service, session_service)
        lane, metadata = await router.classify(request.body, router_context)

        logger.info(f"Message classified as {lane} lane (confidence: {metadata.get('confidence', 0):.2f})")

        # Handle fast-path lanes (FAQ, PRICE, SERVICE_INFO) without LLM
        if lane in [Lane.FAQ, Lane.PRICE, Lane.SERVICE_INFO]:
            fast_path = FastPathService(language_service, session_service)

            if lane == Lane.FAQ:
                result = await fast_path.handle_faq_query(request.body, router_context)
            elif lane == Lane.PRICE:
                service_id = metadata.get('service_id')
                confidence = metadata.get('confidence', 0)
                result = await fast_path.handle_price_query(
                    request.body, router_context, service_id, confidence
                )
            else:  # SERVICE_INFO lane
                service_context = metadata.get('service_context')
                result = await fast_path.handle_service_info_query(
                    request.body, router_context, service_context
                )

            # If fast-path succeeded, return the response
            if result and not result.get('fallback_to_complex'):
                ai_response = result.get('reply', '')
                detected_language = result.get('language', 'en')

                # Store assistant response
                await self.memory_manager.store_message(
                    session_id=session_id,
                    role='assistant',
                    content=ai_response,
                    phone_number=request.from_phone,
                    metadata={
                        'lane': lane,
                        'fast_path': True,
                        'latency_ms': result.get('latency_ms', 0)
                    }
                )

                # Return fast-path response
                return MessageResponse(
                    message=ai_response,
                    session_id=session_id,
                    status="success",
                    detected_language=detected_language,
                    metadata={
                        'lane': lane,
                        'fast_path': True,
                        'latency_ms': result.get('latency_ms', 0)
                    }
                )
            else:
                logger.info(f"Fast-path failed or requested fallback, proceeding to LLM")

        # Phase 2: Extract and update constraints from user message
        # Use simple language detection from message (before full LLM call)
        def detect_language_simple(text: str) -> str:
            """Simple language detection based on character sets"""
            if any(c in text for c in 'абвгдежзийклмнопрстуфхцчшщъыьэюя'):
                return 'ru'
            elif any(c in text for c in 'áéíóúñü'):
                return 'es'
            elif any('\u0590' <= c <= '\u05FF' for c in text):
                return 'he'
            else:
                return 'en'

        detected_language_prelim = detect_language_simple(request.body)

        constraints = await self._extract_and_update_constraints(
            session_id=session_id,
            message=request.body,
            detected_language=detected_language_prelim
        )

        # Log active constraints for observability
        if constraints.excluded_doctors or constraints.excluded_services:
            logger.info(
                f"📋 Active constraints: "
                f"desired={constraints.desired_service}, "
                f"excluded_docs={list(constraints.excluded_doctors)}, "
                f"excluded_svc={list(constraints.excluded_services)}"
            )

        # Generate AI response with full context (for SCHEDULING, COMPLEX lanes, or fast-path fallback)
        ai_response, detected_language = await self._generate_response(
            user_message=request.body,
            clinic_name=resolved_clinic_name,
            clinic_id=effective_clinic_id or resolved_request_clinic_id or request.clinic_id,
            session_id=session_id,  # Phase 4: Pass session_id for tool validation
            session_history=session_messages,
            knowledge_context=[],  # No RAG - knowledge via tools only
            memory_context=memory_context,
            user_preferences=user_preferences,
            additional_context=additional_context,
            clinic_profile=clinic_profile,
            constraints=constraints  # Phase 3: Pass constraints for prompt injection
        )

        # Phase 6: Prepend state echo if significant constraints were added
        if (constraints and (
            constraints.excluded_doctors
            or constraints.excluded_services
            or constraints.desired_service
            or constraints.time_window_start
        )):
            state_echo = self.state_echo_formatter.format_correction_acknowledgment(
                constraints,
                language=detected_language
            )
            # Prepend state echo to AI response
            ai_response = state_echo + "\n\n" + ai_response

        # Update patient with extracted name and detected language
        if extracted_first or detected_language:
            await self._upsert_patient_from_whatsapp(
                clinic_id=effective_clinic_id or request.clinic_id,
                phone=request.from_phone,
                profile_name=request.profile_name,
                detected_language=detected_language,
                extracted_first_name=extracted_first,
                extracted_last_name=extracted_last
            )

        # Store assistant response in persistent storage
        response_metadata = {
            'detected_language': detected_language,
            'memory_context_used': len(memory_context),
            'clinic_id': effective_clinic_id or resolved_request_clinic_id or request.clinic_id,
            'from_number': request.from_phone,
            'channel': request.channel
        }

        # Hybrid search metadata removed (no more RAG)

        # Log assistant message WITH metrics in ONE async call
        llm_metrics = getattr(self, '_llm_metrics', {})

        result = await self.message_logger.log_message_with_metrics(
            session_id=session_id,
            role='assistant',
            content=ai_response,
            metadata=response_metadata,

            # LLM metrics
            llm_provider=llm_metrics.get('llm_provider'),
            llm_model=llm_metrics.get('llm_model'),
            llm_tokens_input=llm_metrics.get('llm_tokens_input', 0),
            llm_tokens_output=llm_metrics.get('llm_tokens_output', 0),
            llm_latency_ms=llm_metrics.get('llm_latency_ms', 0),
            llm_cost_usd=llm_metrics.get('llm_cost_usd', 0),

            # RAG metrics
            rag_queries=1 if relevant_knowledge else 0,
            rag_chunks_retrieved=len(relevant_knowledge) if relevant_knowledge else 0,
            rag_latency_ms=getattr(self, '_rag_latency_ms', 0),

            # Memory metrics
            mem0_queries=1 if memory_context else 0,
            mem0_memories_retrieved=len(memory_context) if memory_context else 0,

            # Total
            total_latency_ms=int((time.time() - processing_start_time) * 1000),
            total_cost_usd=llm_metrics.get('llm_cost_usd', 0),

            # Platform events
            log_platform_events=True,
            agent_id=response_metadata.get('agent_id')
        )

        assistant_message_id = result.get('message_id') if result.get('success') else None

        if assistant_message_id:
            asyncio.create_task(
                self.memory_manager.schedule_mem0_message_update(
                    message_id=assistant_message_id,
                    phone_number=request.from_phone,
                    clinic_id=effective_clinic_id or resolved_request_clinic_id or request.clinic_id,
                    content=ai_response,
                    metadata=dict(response_metadata),
                    session_uuid=session_id,
                    role='assistant'
                )
            )

        # Analyze the response to determine turn status
        logger.info("Analyzing agent response to determine turn status...")
        response_analysis = await self.response_analyzer.analyze_agent_response(
            response=ai_response,
            conversation_context="\n".join([
                f"{msg['role']}: {msg['content']}"
                for msg in session_messages[-5:]  # Last 5 messages for context
            ])
        )

        # Update session turn status based on analysis
        new_turn_status = response_analysis.get('turn_status', 'user_turn')
        logger.info(f"Updating conversation turn status to: {new_turn_status}")

        update_data = {
            'turn_status': new_turn_status,
            'updated_at': datetime.utcnow().isoformat()
        }

        # If agent promised to follow up, store that
        if response_analysis.get('promises_followup'):
            update_data['last_agent_action'] = response_analysis.get('followup_action', 'Follow up on pending request')
            update_data['pending_since'] = datetime.utcnow().isoformat()
            logger.warning(f"⚠️ Agent promised follow-up: {update_data['last_agent_action']}")

        # If conversation appears resolved
        if new_turn_status == 'resolved':
            update_data['status'] = 'ended'
            update_data['ended_at'] = datetime.utcnow().isoformat()

        # Phase 6: If agent has pending action, schedule follow-up
        if self.followup_scheduler and new_turn_status == 'agent_action_pending' and response_analysis.get('promises_followup'):
            logger.info("Agent has pending action, scheduling follow-up...")

            try:
                followup_schedule = await self.followup_scheduler.analyze_and_schedule_followup(
                    session_id=session_id,
                    last_10_messages=session_messages[-10:] if len(session_messages) >= 10 else session_messages,
                    last_agent_action=response_analysis.get('followup_action', '')
                )

                if followup_schedule['should_schedule']:
                    await self.followup_scheduler.store_scheduled_followup(
                        session_id=session_id,
                        followup_at=followup_schedule['followup_at'],
                        context=followup_schedule
                    )
                    logger.info(f"✅ Follow-up scheduled for {followup_schedule['followup_at'].isoformat()}")
            except Exception as e:
                logger.error(f"Failed to schedule follow-up: {e}")

        # Update the session
        try:
            supabase_client = get_supabase_client()
            if supabase_client:
                supabase_client.table('conversation_sessions').update(
                    update_data
                ).eq('id', session_id).execute()

                logger.info(f"✅ Session turn status updated successfully")
        except Exception as e:
            logger.error(f"Failed to update session turn status: {e}")

        # Store analysis in message metadata
        response_metadata['turn_analysis'] = response_analysis

        # Phase 8: ASYNC LOGGING - Fire-and-forget, never block user response
        async def safe_log():
            """Background logging task that never blocks"""
            try:
                await self._log_conversation(
                    session_id=session_id,
                    clinic_id=effective_clinic_id or resolved_request_clinic_id or request.clinic_id,
                    user_message=request.body,
                    ai_response=ai_response,
                    language=detected_language
                )
            except Exception as e:
                logger.debug(f"Background logging failed (non-critical): {e}")

        # Start logging in background, don't wait
        asyncio.create_task(safe_log())

        # Build response metadata
        response_info = {
            "message_count": len(session_messages) + 2,  # Including current exchange
            "memory_context_used": len(memory_context),
            "has_history": not is_new_conversation,
            "is_new_conversation": is_new_conversation,
            "conversation_stage": "new" if is_new_conversation else "continuation",
            "clinic_id": effective_clinic_id or resolved_request_clinic_id or request.clinic_id
        }

        if patient_id:
            response_info["patient_id"] = patient_id
        if patient_name:
            response_info["patient_name"] = patient_name

        # Hybrid search removed - no longer tracking search metadata

        return MessageResponse(
            message=ai_response,
            session_id=session_id,
            status="success",
            detected_language=detected_language,
            metadata=response_info
        )

    async def _generate_response(
        self,
        user_message: str,
        clinic_name: str,
        clinic_id: str,
        session_id: str,  # Phase 4: For tool validation
        session_history: List[Dict],
        knowledge_context: List[str] = None,
        memory_context: List[str] = None,
        user_preferences: Dict[str, Any] = None,
        additional_context: str = "",
        clinic_profile: Optional[Dict[str, Any]] = None,
        constraints: Optional[ConversationConstraints] = None  # Phase 3: For prompt injection
    ) -> tuple[str, str]:
        """Generate AI response using OpenAI with automatic language detection, RAG context, and memory"""

        clinic_profile = clinic_profile or {}
        location_parts = []
        if clinic_profile.get('city'):
            location_parts.append(clinic_profile['city'])
        if clinic_profile.get('state'):
            location_parts.append(clinic_profile['state'])
        if clinic_profile.get('country'):
            location_parts.append(clinic_profile['country'])
        profile_location = clinic_profile.get('location') or ', '.join([part for part in location_parts if part]) or clinic_profile.get('timezone') or 'Unknown'

        services_list = clinic_profile.get('services') or []
        services_text = ', '.join(services_list[:6]) if services_list else "Information available upon request"

        hours = clinic_profile.get('hours') or {}
        weekday_hours = hours.get('weekdays') or hours.get('monday') or "Not provided"
        saturday_hours = hours.get('saturday') or "Not provided"
        sunday_hours = hours.get('sunday') or "Not provided"

        # Build knowledge context section with enhanced formatting
        knowledge_section = ""
        if knowledge_context:
            knowledge_section = "\n\nRelevant Information from Knowledge Base:\n"

            # Group by type if we have structured results
            doctors_info = []
            services_info = []
            general_info = []

            for i, info in enumerate(knowledge_context[:5], 1):  # Use top 5 results with hybrid search
                if not info:
                    continue

                # Try to categorize based on content
                info_lower = info.lower()
                if any(word in info_lower for word in ['dr.', 'doctor', 'physician', 'specialist']):
                    doctors_info.append(info)
                elif any(word in info_lower for word in ['service:', 'procedure', 'treatment', 'duration:', 'price:']):
                    services_info.append(info)
                else:
                    general_info.append(info)

            # Format by category
            if doctors_info:
                knowledge_section += "\nAvailable Doctors:\n"
                for info in doctors_info[:2]:
                    knowledge_section += f"• {info}\n"

            if services_info:
                knowledge_section += "\nServices & Procedures:\n"
                for info in services_info[:2]:
                    knowledge_section += f"• {info}\n"

            if general_info:
                knowledge_section += "\nAdditional Information:\n"
                for info in general_info[:2]:
                    knowledge_section += f"• {info}\n"

            knowledge_section += "\nUse the above information to provide accurate, personalized responses.\n"

        # Build memory context section
        memory_section = ""
        if memory_context:
            memory_section = "\n\nPrevious Context and User Information:\n"
            for i, memory in enumerate(memory_context[:3], 1):
                memory_section += f"- {memory}\n"
            memory_section += "\n"

        # Build user preferences section
        preferences_section = ""
        if user_preferences:
            if user_preferences.get('preferred_name'):
                preferences_section += f"User prefers to be called: {user_preferences['preferred_name']}\n"
            # Don't include stored language preference - we want to respond in the current message's language
            # if user_preferences.get('language'):
            #     preferences_section += f"User's preferred language: {user_preferences['language']}\n"
            if user_preferences.get('appointment_preferences'):
                preferences_section += f"User preferences: {', '.join(user_preferences['appointment_preferences'][:2])}\n"

        # Build conversation summary if we have history
        conversation_summary = ""
        if session_history:
            # Extract key information from conversation
            user_name = None
            mentioned_services = []
            mentioned_doctors = []
            mentioned_topics = []

            for msg in session_history:
                if msg['role'] == 'user':
                    content_lower = msg['content'].lower()
                    content = msg['content']

                    # Try to extract name mentions
                    if 'me llamo' in content_lower or 'my name is' in content_lower or 'soy' in content_lower:
                        # Extract potential name from the message
                        parts = content.split()
                        for i, part in enumerate(parts):
                            if part.lower() in ['llamo', 'soy', 'is'] and i + 1 < len(parts):
                                potential_name = parts[i + 1].strip('.,!?')
                                if potential_name and len(potential_name) > 2:
                                    user_name = potential_name
                                    break

                    # Track mentioned doctors - look for doctor names or "врач"/"doctor"
                    if 'врач' in content_lower or 'doctor' in content_lower or 'доктор' in content_lower or 'dr.' in content_lower.replace('.', ''):
                        # Extract potential doctor names (capitalized words near doctor keywords)
                        words = content.split()
                        for i, word in enumerate(words):
                            # Look for capitalized names near doctor keywords
                            if word and word[0].isupper() and len(word) > 2:
                                # Check if this is near a doctor-related word
                                context_words = ' '.join(words[max(0, i-2):min(len(words), i+3)]).lower()
                                if any(kw in context_words for kw in ['врач', 'doctor', 'доктор', 'dr']):
                                    mentioned_doctors.append(word)

                    # Track mentioned services
                    if 'limpieza' in content_lower or 'cleaning' in content_lower or 'чистка' in content_lower:
                        mentioned_services.append('dental cleaning')
                    if 'cita' in content_lower or 'appointment' in content_lower or 'запись' in content_lower or 'записаться' in content_lower:
                        mentioned_services.append('appointment scheduling')

                    # Track general topics being discussed
                    if 'price' in content_lower or 'cost' in content_lower or 'цена' in content_lower or 'стоимость' in content_lower:
                        mentioned_topics.append('pricing information')
                    if 'schedule' in content_lower or 'hours' in content_lower or 'расписание' in content_lower or 'время работы' in content_lower:
                        mentioned_topics.append('schedule/hours')
                    if 'about' in content_lower or 'про' in content_lower or 'о ' in content_lower:
                        mentioned_topics.append('general information request')

            # Build summary if we found any context
            if user_name or mentioned_services or mentioned_doctors or mentioned_topics:
                conversation_summary = "\n\nIMPORTANT CONTEXT FROM THIS CONVERSATION:\n"
                if user_name:
                    conversation_summary += f"- The user's name is {user_name}. USE THEIR NAME when appropriate.\n"
                if mentioned_doctors:
                    # Deduplicate and list doctors
                    unique_doctors = list(set(mentioned_doctors))
                    conversation_summary += f"- The user has been asking about these doctors: {', '.join(unique_doctors)}. REMEMBER this context and continue the discussion about these specific doctors.\n"
                if mentioned_services:
                    conversation_summary += f"- The user has expressed interest in: {', '.join(set(mentioned_services))}\n"
                if mentioned_topics:
                    conversation_summary += f"- Topics being discussed: {', '.join(set(mentioned_topics))}\n"
                conversation_summary += "\n"

        # Doctor info is now fetched via tool calling (get_clinic_info tool) instead of being in system prompt
        # This allows for more dynamic and flexible information retrieval
        doctor_info_text = "Use the get_clinic_info tool to retrieve staff information"

        # Phase 3: Build constraints section FIRST (before all other context)
        constraints_section = self._build_constraints_section(constraints) if constraints else ""

        # Build system prompt for multilingual support with memory
        system_prompt = f"""You ARE {clinic_name}, speaking directly to patients. You represent the clinic itself, not a separate assistant or intermediary. When patients message you, they are messaging the clinic directly.

CRITICAL LANGUAGE RULE: You MUST maintain conversation language consistency. Use the language of the conversation (from previous messages). Only switch languages if the user clearly switches to a different language with a complete sentence. Single words or medical terms (like "veneer", "implant", "consultation") DO NOT indicate a language switch - these are universal terms. Stay in the current conversation language unless the user explicitly writes a full sentence in a different language.
{constraints_section}{additional_context}{conversation_summary}{memory_section}{preferences_section}{knowledge_section}
Clinic Information:
- Name: {clinic_name}
- Location: {profile_location}
- Services: {services_text}
- Staff: {doctor_info_text if doctor_info_text else "Information available upon request"}
- Hours:
  - Monday-Friday: {weekday_hours}
  - Saturday: {saturday_hours}
  - Sunday: {sunday_hours}

Instructions:
1. ABSOLUTELY CRITICAL: Maintain conversation language consistency. Stay in the current conversation language unless the user explicitly switches with a full sentence.
2. Be friendly, professional, and helpful
3. If you know the user's name from the conversation, USE IT in your responses
4. **MAINTAIN CONVERSATION CONTEXT**: Pay close attention to what the user asked about in previous messages. If they asked about a specific doctor, service, or topic, CONTINUE that conversation thread. Don't forget what was just discussed.
   - **CRITICAL FOR APPOINTMENTS**: If the user is in the middle of booking an appointment (asked about a doctor, agreed to book), you MUST complete that appointment booking before addressing new topics. If they ask about something else mid-booking, acknowledge it but remind them "Let me finish booking your appointment with Dr. [Name] first, then I can help with [new topic]."
5. **USE TOOLS when needed**: You have access to tools for querying service prices and clinic information. Use them when:
   - Users ask about pricing or costs (use query_service_prices tool)
   - Users ask about doctors, staff, or clinic details (use get_clinic_info tool)
   - You need up-to-date information from the database
6. **CRITICAL: When tools return doctor information with specializations, you MUST quote the specialization EXACTLY as provided. DO NOT paraphrase, abbreviate, or add extra details to medical specializations. Copy them word-for-word.**
7. Use the knowledge base information when answering questions about the clinic, staff, or services
8. YOU ARE THE CLINIC - Never suggest calling the clinic or contacting the clinic
9. For appointments, help schedule directly or gather information needed
10. Keep responses concise (2-3 sentences maximum)
11. If uncertain about something, say "Let me check with our specialists and get back to you" or "I need to consult with the team about that"
12. Build on previous context - don't treat each message as isolated. If the user asks a follow-up question, assume it's about the same topic/person they just asked about.

LANGUAGE CONSISTENCY EXAMPLES:
- Conversation in English + User writes "veneer" → Continue in English
- Conversation in English + User writes "Hola, ¿cuántos doctores tienen?" → Switch to Spanish
- Conversation in Spanish + User writes "implant" → Continue in Spanish (single medical term)
- User asks about "Dr. Dan" then says "veneer" → Continue the same conversation flow about appointment with Dr. Dan

IMPORTANT BEHAVIORS:
- For appointments: "What day and time work best for you?" NOT "Please call us"
- For unknown info: "Let me verify that with our team" NOT "Please contact the clinic"
- For emergencies: "We can see you right away" NOT "Call the clinic immediately"
- You ARE the clinic, speak as the clinic itself, in first person plural (we/our)"""

        messages = [
            {"role": "system", "content": system_prompt}
        ]

        # Add MORE conversation history for better context
        for msg in session_history[-12:]:  # Increased from 8 to 12 messages for better context retention
            messages.append({
                "role": msg['role'],
                "content": msg['content']
            })

        # Add current message
        messages.append({"role": "user", "content": user_message})

        try:
            import asyncio
            import time
            import json
            from openai import AsyncOpenAI

            # Track LLM metrics
            llm_start = time.time()

            # Import tool schemas for function calling
            from app.api.tool_schemas import get_tool_schemas

            # Get available tools for this clinic
            tool_schemas = get_tool_schemas(clinic_id)
            logger.info(f"Loaded {len(tool_schemas)} tool schemas for clinic {clinic_id}")

            # Phase 8: Add timeout to LLM call (budget: 20s max for tool calling + memory retrieval)
            try:
                # Try LLM Factory first (GPT-5-nano), fallback to direct OpenAI if factory not ready
                try:
                    factory = await get_llm_factory()
                    llm_response = await asyncio.wait_for(
                        factory.generate_with_tools(
                            messages=messages,
                            tools=tool_schemas,
                            model=None,  # Let factory choose best tool-calling model (GPT-4o-mini default)
                            temperature=1.0,  # GPT-5-nano only supports default temperature of 1.0
                            max_tokens=300
                        ),
                        timeout=20.0  # Increased from 10s to allow for tool calling + mem0 retrieval
                    )

                    # Check if LLM wants to call tools
                    if llm_response.tool_calls and len(llm_response.tool_calls) > 0:
                        logger.info(f"LLM requesting {len(llm_response.tool_calls)} tool call(s)")

                        # P0 GUARD: Initialize call budget tracker
                        MAX_CALENDAR_CALLS_PER_MESSAGE = 10
                        calendar_calls_made = 0

                        # Execute tool calls
                        tool_results = []
                        for tool_call in llm_response.tool_calls:
                            # LLM Factory returns normalized ToolCall with .name and .arguments
                            tool_name = tool_call.name
                            tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else json.loads(tool_call.arguments)

                            logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

                            # Phase 4: Validate tool call against constraints BEFORE execution
                            is_valid, error_msg, suggested_fixes = self.state_gate.validate_tool_call(
                                tool_name=tool_name,
                                arguments=tool_args,
                                constraints=constraints or ConversationConstraints()
                            )

                            if not is_valid:
                                # BLOCK invalid call
                                logger.error(f"🚫 BLOCKED tool call: {error_msg}")

                                # Return error to LLM for correction
                                tool_results.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": json.dumps({
                                        "success": False,
                                        "error": "constraint_violation",
                                        "message": error_msg,
                                        "suggested_fixes": suggested_fixes
                                    })
                                })
                                continue  # Skip execution

                            elif suggested_fixes:
                                # REWRITE parameters
                                logger.info(f"🔄 Applying suggested fixes: {suggested_fixes}")
                                tool_args_original = tool_args.copy()
                                tool_args.update(suggested_fixes)

                            # P0 GUARD: Enforce calendar call budget
                            if tool_name == "check_availability":
                                calendar_calls_made += 1
                                if calendar_calls_made > MAX_CALENDAR_CALLS_PER_MESSAGE:
                                    logger.error(
                                        f"🚨 BUDGET EXCEEDED: {calendar_calls_made} calendar calls in single message. "
                                        f"Max allowed: {MAX_CALENDAR_CALLS_PER_MESSAGE}"
                                    )

                                    # Return friendly error instead of executing
                                    tool_result = {
                                        "error": "too_many_calendar_queries",
                                        "message": "I'm having trouble finding availability. Let me connect you with our team to help directly.",
                                        "requires_escalation": True,
                                        "calls_attempted": calendar_calls_made
                                    }

                                    # Add to tool results
                                    tool_results.append({
                                        "tool_call_id": tool_call.id,
                                        "role": "tool",
                                        "name": tool_name,
                                        "content": json.dumps(tool_result)
                                    })

                                    # Break out of tool execution loop
                                    break

                            # Execute appropriate tool
                            if tool_name == "query_service_prices":
                                from app.tools.price_query_tool import PriceQueryTool
                                from app.config import get_redis_client

                                # Get Redis client for caching
                                redis_client = get_redis_client()
                                price_tool = PriceQueryTool(clinic_id=clinic_id, redis_client=redis_client)
                                services = await price_tool.get_services_by_query(**tool_args)

                                # Format results
                                if services:
                                    result_text = "Found services:\n"
                                    for svc in services[:5]:
                                        # Handle both base_price and price field names
                                        price_value = svc.get('price') or svc.get('base_price')
                                        price = f"${price_value:.2f}" if price_value else "Price on request"
                                        result_text += f"- {svc['name']}: {price}\n"
                                else:
                                    result_text = "No services found matching your query."

                                tool_results.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": result_text
                                })

                            elif tool_name == "get_clinic_info":
                                from app.tools.clinic_info_tool import ClinicInfoTool
                                from app.config import get_redis_client
                                # Use the local get_supabase_client function (defined at top of this file)
                                supabase_client = get_supabase_client()
                                redis_client = get_redis_client()

                                # Create tool instance
                                tool = ClinicInfoTool(clinic_id=clinic_id, redis_client=redis_client)

                                # Get info_type from tool call arguments
                                info_type = tool_call.arguments.get('info_type', 'all')

                                # Route to appropriate method based on info_type
                                if info_type == 'doctors':
                                    result = await tool.get_doctor_count(supabase_client)
                                    # Format with explicit doctor-to-specialization mapping to prevent LLM hallucination
                                    if result.get('specializations'):
                                        # Build explicit list: "Dr. Name (Specialization)"
                                        doctor_details = []
                                        for spec, names in result['specializations'].items():
                                            for name in names:
                                                doctor_details.append(f"{name} (specialization: {spec})")
                                        info = f"The clinic has {result['total_doctors']} doctors:\n" + "\n".join(doctor_details)
                                    else:
                                        # Fallback if no specializations
                                        info = f"The clinic has {result['total_doctors']} doctors: {', '.join(result['doctor_list'])}"

                                elif info_type == 'location':
                                    clinic_info = await tool.get_clinic_info(supabase_client)
                                    address_parts = [clinic_info.get('address', 'Not available')]
                                    if clinic_info.get('city'):
                                        address_parts.append(clinic_info.get('city'))
                                    if clinic_info.get('state'):
                                        address_parts.append(clinic_info.get('state'))
                                    if clinic_info.get('country'):
                                        address_parts.append(clinic_info.get('country'))
                                    full_address = ', '.join(address_parts)
                                    info = f"Address: {full_address}\nPhone: {clinic_info.get('phone', 'Not available')}\nEmail: {clinic_info.get('email', 'Not available')}"

                                elif info_type == 'hours':
                                    clinic_info = await tool.get_clinic_info(supabase_client)
                                    hours = clinic_info.get('hours', {})
                                    if hours:
                                        info = "Business Hours:\n" + "\n".join([f"{day.capitalize()}: {time}" for day, time in hours.items()])
                                    else:
                                        info = "Business hours not available"

                                elif info_type == 'services':
                                    # Use cached services
                                    from app.services.clinic_data_cache import ClinicDataCache
                                    cache = ClinicDataCache(redis_client, default_ttl=3600)
                                    services = await cache.get_services(clinic_id, supabase_client)
                                    if services:
                                        service_names = [s.get('name', '') for s in services[:10]]
                                        info = f"We offer {len(services)} services including: {', '.join(service_names)}"
                                        if len(services) > 10:
                                            info += f" and {len(services) - 10} more..."
                                    else:
                                        info = "Service information not available"

                                else:  # 'all' or unknown
                                    # Get comprehensive clinic info
                                    doctor_result = await tool.get_doctor_count(supabase_client)
                                    clinic_info = await tool.get_clinic_info(supabase_client)

                                    info_parts = []
                                    if clinic_info.get('name'):
                                        info_parts.append(f"Clinic: {clinic_info['name']}")
                                    if clinic_info.get('address'):
                                        info_parts.append(f"Address: {clinic_info['address']}")
                                    if doctor_result.get('total_doctors'):
                                        info_parts.append(f"Doctors: {doctor_result['total_doctors']}")
                                        info_parts.append(f"Doctor list: {', '.join(doctor_result['doctor_list'])}")

                                    info = "\n".join(info_parts) if info_parts else "Clinic information not available"

                                logger.info(f"✅ get_clinic_info tool (type={info_type}) returned {len(info)} chars: {info[:200]}...")
                                tool_results.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": info
                                })

                            elif tool_name == "check_availability":
                                from app.services.reservation_tools import ReservationTools

                                # Extract patient_id from session if available
                                patient_id = None
                                if session_history and len(session_history) > 0:
                                    # Try to get patient_id from session metadata
                                    for msg in session_history:
                                        if msg.get('metadata', {}).get('patient_id'):
                                            patient_id = msg['metadata']['patient_id']
                                            break

                                # Instantiate ReservationTools
                                reservation_tools = ReservationTools(
                                    clinic_id=clinic_id,
                                    patient_id=patient_id
                                )

                                # Execute tool
                                result = await reservation_tools.check_availability_tool(**tool_args)

                                # Format result for LLM
                                if result.get('success'):
                                    slots = result.get('available_slots', [])
                                    if slots:
                                        result_text = f"Found {len(slots)} available slots:\n"
                                        for slot in slots[:5]:  # Show top 5
                                            result_text += f"- {slot['date']} at {slot['start_time']} with {slot['doctor_name']}\n"
                                        if result.get('recommendation'):
                                            result_text += f"\nRecommendation: {result['recommendation']}"
                                    else:
                                        result_text = "No available slots found for the requested service and timeframe."
                                else:
                                    result_text = f"Error checking availability: {result.get('error', 'Unknown error')}"

                                logger.info(f"✅ check_availability tool returned: {result_text[:200]}...")
                                tool_results.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": result_text
                                })

                            elif tool_name == "book_appointment":
                                from app.services.reservation_tools import ReservationTools

                                # Extract patient_id from patient_info or session
                                patient_id = None
                                if 'patient_info' in tool_args and 'phone' in tool_args['patient_info']:
                                    # TODO: Look up patient_id by phone number
                                    # For now, will be created in booking service
                                    pass

                                reservation_tools = ReservationTools(
                                    clinic_id=clinic_id,
                                    patient_id=patient_id
                                )

                                result = await reservation_tools.book_appointment_tool(**tool_args)

                                if result.get('success'):
                                    appt = result.get('appointment', {})
                                    confirmation = result.get('confirmation_message', 'Appointment booked successfully')
                                    result_text = f"✅ {confirmation}\n"
                                    result_text += f"Appointment ID: {result.get('appointment_id')}\n"
                                    if appt:
                                        result_text += f"Doctor: {appt.get('doctor_name', 'TBD')}\n"
                                        result_text += f"Date: {appt.get('date')} at {appt.get('start_time')}"
                                else:
                                    result_text = f"❌ Booking failed: {result.get('error', 'Unknown error')}"

                                logger.info(f"✅ book_appointment tool returned: {result_text[:200]}...")
                                tool_results.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": result_text
                                })

                            elif tool_name == "cancel_appointment":
                                from app.services.reservation_tools import ReservationTools

                                reservation_tools = ReservationTools(
                                    clinic_id=clinic_id
                                )

                                result = await reservation_tools.cancel_appointment_tool(**tool_args)

                                if result.get('success'):
                                    result_text = f"✅ Appointment cancelled successfully"
                                    if result.get('cancelled_count', 0) > 1:
                                        result_text += f" ({result['cancelled_count']} appointments cancelled)"
                                else:
                                    result_text = f"❌ Cancellation failed: {result.get('error', 'Unknown error')}"

                                logger.info(f"✅ cancel_appointment tool returned: {result_text}")
                                tool_results.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": result_text
                                })

                            elif tool_name == "reschedule_appointment":
                                from app.services.reservation_tools import ReservationTools

                                reservation_tools = ReservationTools(
                                    clinic_id=clinic_id
                                )

                                result = await reservation_tools.reschedule_appointment_tool(**tool_args)

                                if result.get('success'):
                                    result_text = f"✅ Appointment rescheduled successfully to {tool_args['new_datetime']}"
                                    if result.get('rescheduled_count', 0) > 1:
                                        result_text += f" ({result['rescheduled_count']} appointments rescheduled)"
                                else:
                                    result_text = f"❌ Rescheduling failed: {result.get('error', 'Unknown error')}"

                                logger.info(f"✅ reschedule_appointment tool returned: {result_text}")
                                tool_results.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": result_text
                                })

                        # Add tool results to messages and get final response
                        messages.append({
                            "role": "assistant",
                            "content": llm_response.content or "",
                            "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments}} for tc in llm_response.tool_calls]
                        })

                        for tool_result in tool_results:
                            messages.append(tool_result)

                        # Get final response from LLM with tool results
                        final_response = await factory.generate(
                            messages=messages,
                            model=None,  # Use same model as tool calling (GLM-4.6)
                            temperature=0.7,
                            max_tokens=300
                        )
                        ai_response = final_response.content
                    else:
                        # No tool calls, use direct response
                        ai_response = llm_response.content

                    # Calculate LLM latency
                    llm_latency_ms = int((time.time() - llm_start) * 1000)

                    # Store metrics for later logging (using factory response format)
                    self._llm_metrics = {
                        'llm_provider': llm_response.provider,
                        'llm_model': llm_response.model,
                        'llm_tokens_input': llm_response.usage.get('input_tokens', 0),
                        'llm_tokens_output': llm_response.usage.get('output_tokens', 0),
                        'llm_latency_ms': llm_latency_ms,
                        'llm_cost_usd': self._calculate_factory_cost(llm_response)
                    }

                except (ValueError, RuntimeError) as factory_error:
                    # Factory not available (table doesn't exist), fallback to direct OpenAI GPT-5-nano
                    logger.warning(f"LLM Factory not available ({factory_error}), using direct OpenAI GPT-5-nano")

                    api_key = os.environ.get("OPENAI_API_KEY")
                    if not api_key:
                        raise RuntimeError("OPENAI_API_KEY not configured")

                    client = AsyncOpenAI(api_key=api_key)
                    openai_response = await asyncio.wait_for(
                        client.chat.completions.create(
                            model="gpt-5-nano",
                            messages=messages,
                            # temperature not set - GPT-5-nano only supports default (1.0)
                            max_completion_tokens=300  # GPT-5-nano requires max_completion_tokens, not max_tokens
                        ),
                        timeout=10.0
                    )
                    ai_response = openai_response.choices[0].message.content

                    # Calculate LLM latency
                    llm_latency_ms = int((time.time() - llm_start) * 1000)

                    # Store metrics for OpenAI fallback
                    self._llm_metrics = {
                        'llm_provider': 'openai',
                        'llm_model': 'gpt-5-nano',
                        'llm_tokens_input': openai_response.usage.prompt_tokens if openai_response.usage else 0,
                        'llm_tokens_output': openai_response.usage.completion_tokens if openai_response.usage else 0,
                        'llm_latency_ms': llm_latency_ms,
                        'llm_cost_usd': self._calculate_gpt5_nano_cost(openai_response.usage) if openai_response.usage else 0
                    }

            except asyncio.TimeoutError:
                logger.error("LLM call exceeded 20s timeout, using fallback")
                # Quick template fallback with smart content detection
                from app.services.intent_router import IntentRouter
                intent_router = IntentRouter()
                lang = intent_router._detect_language(user_message)

                # Check if user is asking about doctors
                user_lower = user_message.lower()
                is_doctor_query = any(keyword in user_lower for keyword in [
                    'doctor', 'доктор', 'врач', 'médico', 'docteur'
                ])

                if is_doctor_query and doctor_info_text:
                    # Provide doctor information directly
                    doctor_fallbacks = {
                        'en': f"We have the following doctors:\n\n{doctor_info_text}",
                        'ru': f"У нас работают следующие врачи:\n\n{doctor_info_text}",
                        'es': f"Tenemos los siguientes médicos:\n\n{doctor_info_text}",
                        'he': f"יש לנו את הרופאים הבאים:\n\n{doctor_info_text}",
                        'pt': f"Temos os seguintes médicos:\n\n{doctor_info_text}"
                    }
                    ai_response = doctor_fallbacks.get(lang, doctor_fallbacks['en'])
                else:
                    # Generic fallback
                    fallbacks = {
                        'en': "I understand. Let me help you with that.",
                        'ru': "Понятно. Позвольте мне помочь вам с этим.",
                        'es': "Entiendo. Déjame ayudarte con eso.",
                        'he': "אני מבין. תן לי לעזור לך עם זה.",
                        'pt': "Eu entendo. Deixe-me ajudá-lo com isso."
                    }
                    ai_response = fallbacks.get(lang, fallbacks['en'])

                # Store timeout metrics
                self._llm_metrics = {
                    'llm_provider': 'openai',
                    'llm_model': 'gpt-5-nano',
                    'llm_tokens_input': 0,
                    'llm_tokens_output': 0,
                    'llm_latency_ms': int((time.time() - llm_start) * 1000),
                    'llm_cost_usd': 0,
                    'error_occurred': True,
                    'error_message': 'LLM timeout'
                }

            # Clean up response by removing <think> tags and reasoning
            ai_response = self._clean_llm_response(ai_response, user_message)

            # Phase 8: Use fast heuristic language detection instead of second LLM call
            from app.services.intent_router import IntentRouter
            intent_router = IntentRouter()
            detected_language = intent_router._detect_language(ai_response)

            return ai_response, detected_language

        except RuntimeError as e:
            logger.error(f"OpenAI client not available for response generation: {e}")
            return (
                "We are experiencing configuration issues. Please try again shortly.",
                "unknown",
            )
        except Exception as e:
            print(f"Error generating AI response: {e}")
            # Fallback response in multiple languages
            fallback = (
                "I apologize for the technical issue. Let me check with our team and get back to you shortly. / "
                "Disculpe el problema técnico. Permítame consultar con nuestro equipo y le responderé pronto. / "
                "Desculpe o problema técnico. Deixe-me verificar com nossa equipe e retornarei em breve."
            )
            return fallback, "multilingual"

    def _clean_llm_response(self, response: str, user_message: str = "") -> str:
        """Remove <think> tags and any reasoning text from LLM response

        Some LLMs may leak internal reasoning or XML-style tags into responses.
        This function cleans them up before sending to users.

        Strategy:
        1. Remove complete <think>...</think> blocks
        2. If </think> appears without opening, remove everything from start to </think>
        3. If <think> appears without closing, remove everything from <think> to end
        4. Split by these patterns and keep only actual response text
        """
        import re

        # Log raw response for debugging
        logger.info(f"🔍 Raw LLM response (length: {len(response)}): {response[:500]}{'...' if len(response) > 500 else ''}")

        # First, remove all complete <think>...</think> blocks (including nested/multiple)
        while re.search(r'<think>.*?</think>', response, flags=re.DOTALL):
            response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)

        # If there's a remaining </think> tag, everything before it is reasoning
        # Remove from start up to and including the </think> tag
        if '</think>' in response:
            parts = response.split('</think>')
            # Keep everything after the LAST </think> tag
            response = parts[-1]

        # If there's a remaining <think> tag, everything after it is reasoning
        # Remove from <think> tag to end
        if '<think>' in response:
            response = response.split('<think>')[0]

        # Clean up multiple consecutive newlines
        response = re.sub(r'\n{3,}', '\n\n', response)

        # Strip leading/trailing whitespace
        response = response.strip()

        # If response is empty after cleaning, return multilingual fallback
        if not response:
            logger.warning("⚠️ Response was empty after cleaning think tags - using multilingual fallback")

            # Detect user's language for appropriate fallback
            from app.services.intent_router import IntentRouter
            intent_router = IntentRouter()
            lang = intent_router._detect_language(user_message) if user_message else 'en'

            # Return helpful fallback in user's language
            fallbacks = {
                'en': "I understand. How can I help you today?",
                'ru': "Понимаю. Чем могу помочь?",
                'es': "Entiendo. ¿En qué puedo ayudarte?",
                'he': "אני מבין. במה אוכל לעזור?",
                'pt': "Entendo. Como posso ajudar?"
            }
            return fallbacks.get(lang, fallbacks['en'])

        return response

    def _calculate_llm_cost(self, usage) -> float:
        """Calculate cost for OpenAI GPT-4o-mini (legacy method - deprecated)"""
        # GPT-4o-mini pricing (as of 2025) - DEPRECATED
        input_cost_per_1m = 0.150  # $0.150 per 1M input tokens
        output_cost_per_1m = 0.600  # $0.600 per 1M output tokens

        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        input_cost = (input_tokens / 1_000_000) * input_cost_per_1m
        output_cost = (output_tokens / 1_000_000) * output_cost_per_1m

        return round(input_cost + output_cost, 6)

    def _calculate_gpt5_nano_cost(self, usage) -> float:
        """Calculate cost for OpenAI GPT-5-nano"""
        # GPT-5-nano pricing (as of 2025)
        input_cost_per_1m = 0.05  # $0.05 per 1M input tokens
        output_cost_per_1m = 0.40  # $0.40 per 1M output tokens

        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        input_cost = (input_tokens / 1_000_000) * input_cost_per_1m
        output_cost = (output_tokens / 1_000_000) * output_cost_per_1m

        return round(input_cost + output_cost, 6)

    def _calculate_factory_cost(self, llm_response) -> float:
        """Calculate cost from LLM Factory response using capability matrix pricing"""
        try:
            # Get pricing from capability matrix (already calculated by factory)
            # For now, use simple estimation based on provider
            pricing_map = {
                'glm': {'input': 0.60, 'output': 2.20},  # GLM-4.6 per 1M tokens
                'google': {'input': 0.10, 'output': 0.40},  # Gemini Flash-Lite
                'openai': {'input': 0.05, 'output': 0.40},  # GPT-5-nano
            }

            provider = llm_response.provider
            if provider not in pricing_map:
                return 0.0

            pricing = pricing_map[provider]
            input_tokens = llm_response.usage.get('input_tokens', 0)
            output_tokens = llm_response.usage.get('output_tokens', 0)

            input_cost = (input_tokens / 1_000_000) * pricing['input']
            output_cost = (output_tokens / 1_000_000) * pricing['output']

            return round(input_cost + output_cost, 6)
        except Exception as e:
            logger.warning(f"Failed to calculate factory cost: {e}")
            return 0.0

    async def _detect_response_language(self, text: str) -> str:
        """Detect the language of the AI response for logging purposes"""
        try:
            client = get_openai_client()

            # Quick language detection for logging
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Detect the language and respond with ONLY the language name in English (e.g., Spanish, English, Portuguese, French, German, Chinese, Japanese, Arabic, etc.)"
                    },
                    {
                        "role": "user",
                        "content": f"What language is this: {text[:100]}"  # Use first 100 chars
                    }
                ],
                temperature=0,
                max_tokens=20
            )
            return response.choices[0].message.content.strip()
        except RuntimeError:
            logger.error("OpenAI client not available for language detection")
            return "unknown"
        except Exception:
            return "unknown"

    def _get_clinic_id_from_organization(self, organization_id: str) -> str:
        """Map organization_id to actual clinic_id

        Since request.clinic_id actually contains organization_id,
        we need to look up the real clinic_id from the clinics table.
        """
        if not organization_id:
            return organization_id

        if organization_id in self._known_clinic_ids:
            return organization_id

        try:
            client = get_supabase_client()
            if not client:
                logger.warning("Supabase client unavailable; cannot map clinic_id")
                return organization_id

            # Return cached mapping if available
            if organization_id in self._org_to_clinic_cache:
                return self._org_to_clinic_cache[organization_id]

            # If value already matches a known clinic id, short-circuit
            if organization_id in self._org_to_clinic_cache.values():
                self._known_clinic_ids.add(organization_id)
                return organization_id

            # First try to treat the value as a clinic_id
            try:
                by_id = client.table('clinics').select('id').eq('id', organization_id).limit(1).execute()
                if by_id.data:
                    clinic_id = by_id.data[0]['id']
                    self._org_to_clinic_cache[organization_id] = clinic_id
                    self._known_clinic_ids.add(clinic_id)
                    return clinic_id
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(f"Clinic lookup by id failed for {organization_id}: {exc}")

            # Look up clinic by organization_id
            result = client.table('clinics').select('id, organization_id').eq(
                'organization_id', organization_id
            ).limit(1).execute()

            if result.data and len(result.data) > 0:
                clinic_id = result.data[0]['id']
                # Cache the mapping
                self._org_to_clinic_cache[organization_id] = clinic_id
                self._known_clinic_ids.add(clinic_id)
                logger.debug(f"Mapped org {organization_id[:8]}... to clinic {clinic_id[:8]}...")
                return clinic_id
            else:
                # Fallback: get first clinic (for single-clinic deployments)
                logger.warning(f"No clinic found for org {organization_id}, using first clinic")
                all_clinics = client.table('clinics').select('id').limit(1).execute()
                if all_clinics.data:
                    clinic_id = all_clinics.data[0]['id']
                    self._org_to_clinic_cache[organization_id] = clinic_id
                    self._known_clinic_ids.add(clinic_id)
                    return clinic_id
                else:
                    logger.error("No clinics found in database!")
                    return organization_id

        except Exception as e:
            logger.error(f"Error mapping organization to clinic: {e}")
            return organization_id

    async def _get_clinic_profile(self, clinic_id: Optional[str]) -> Dict[str, Any]:
        """Fetch clinic profile data with Redis caching."""
        if not clinic_id:
            return {}

        if clinic_id in self._clinic_profile_cache:
            return self._clinic_profile_cache[clinic_id]

        try:
            from app.tools.clinic_info_tool import ClinicInfoTool
            from app.config import get_redis_client

            supabase_client = get_supabase_client()
            redis_client = get_redis_client()
            tool = ClinicInfoTool(clinic_id=clinic_id, redis_client=redis_client)
            profile = await tool.get_clinic_info(supabase_client)

            if profile:
                self._clinic_profile_cache[clinic_id] = profile
                return profile
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(f"Unable to fetch clinic profile for {clinic_id}: {exc}")

        return {}

    def _should_warm_clinic_cache(self, clinic_id: Optional[str]) -> bool:
        """Determine if clinic cache warming should be triggered."""
        if not clinic_id:
            return False

        if self._clinic_cache_warm_ttl < 0:
            return False  # Explicitly disabled

        if clinic_id in self._clinic_cache_inflight:
            return False  # Already warming

        last_warm = self._clinic_cache_warm_timestamps.get(clinic_id)
        if last_warm is None:
            return True

        if self._clinic_cache_warm_ttl == 0:
            return False  # Warm once per process

        return (perf_counter() - last_warm) > self._clinic_cache_warm_ttl

    async def _warm_clinic_cache(self, clinic_id: str):
        """Warm clinic doctors/services/FAQs into Redis asynchronously."""
        if not clinic_id:
            return

        try:
            from app.startup_warmup import warmup_clinic_data

            logger.info("🚀 Warming clinic cache for %s", clinic_id[:8] + "..." if len(clinic_id) > 8 else clinic_id)
            success = await warmup_clinic_data([clinic_id])

            if success:
                self._clinic_cache_warm_timestamps[clinic_id] = perf_counter()
                logger.info("✅ Clinic cache warmed for %s", clinic_id[:8] + "..." if len(clinic_id) > 8 else clinic_id)
            else:
                logger.warning("Clinic cache warmup returned falsy result for %s", clinic_id)
                self._clinic_cache_warm_timestamps.pop(clinic_id, None)
        except Exception as exc:
            logger.warning("Clinic cache warmup failed for %s: %s", clinic_id, exc)
            self._clinic_cache_warm_timestamps.pop(clinic_id, None)
        finally:
            self._clinic_cache_inflight.discard(clinic_id)

    async def _fetch_patient_profile(
        self,
        clinic_id: Optional[str],
        phone: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch patient profile data for personalization."""
        if not phone:
            return None

        client = get_supabase_client()
        if not client:
            return None

        resolved_clinic_id = self._get_clinic_id_from_organization(clinic_id) if clinic_id else clinic_id
        clean_phone = phone.replace("@s.whatsapp.net", "")

        def _query():
            return (
                client
                .schema('healthcare')
                .table('patients')
                .select('id, first_name, last_name, preferred_language')
                .eq('clinic_id', resolved_clinic_id or clinic_id)
                .eq('phone', clean_phone)
                .limit(1)
                .execute()
            )

        try:
            import asyncio
            response = await asyncio.to_thread(_query)
        except Exception as exc:
            logger.debug("Patient profile lookup failed for %s/%s: %s", clinic_id, clean_phone, exc)
            return None

        if response and getattr(response, "data", None):
            data = response.data
            if isinstance(data, list) and data:
                return data[0]

        return None

    async def _upsert_patient_from_whatsapp(
        self,
        clinic_id: str,
        phone: str,
        profile_name: str = None,
        detected_language: str = None,
        extracted_first_name: str = None,
        extracted_last_name: str = None
    ):
        """Create or update patient record from WhatsApp conversation"""
        try:
            client = get_supabase_client()
            if not client:
                logger.debug("Supabase client unavailable; skipping patient upsert")
                return

            # Map organization_id to actual clinic_id
            actual_clinic_id = self._get_clinic_id_from_organization(clinic_id)
            self._known_clinic_ids.add(actual_clinic_id)

            if self._patient_upsert_cache_ttl > 0:
                cache_key = (actual_clinic_id, phone)
                cached_at = self._patient_upsert_cache.get(cache_key)
                if cached_at and (perf_counter() - cached_at) < self._patient_upsert_cache_ttl:
                    logger.debug(
                        "Skipping patient upsert (cached %.2fs) clinic=%s phone=%s",
                        perf_counter() - cached_at,
                        actual_clinic_id,
                        phone
                    )
                    return

            # Use extracted names if available, otherwise use profile name
            result = client.rpc('upsert_patient_from_whatsapp', {
                'p_clinic_id': actual_clinic_id,
                'p_phone': phone,
                'p_first_name': extracted_first_name,
                'p_last_name': extracted_last_name,
                'p_profile_name': profile_name,
                'p_preferred_language': detected_language or 'English'
            }).execute()

            if result.data and len(result.data) > 0:
                patient_info = result.data[0]
                if patient_info.get('is_new'):
                    logger.info(f"✅ Created new patient from WhatsApp: {phone} -> {patient_info.get('patient_id')}")
                else:
                    updated = patient_info.get('updated_fields', [])
                    if updated:
                        logger.info(f"✅ Updated patient from WhatsApp: {phone} (fields: {', '.join(updated)})")
            else:
                logger.warning(f"Patient upsert returned no data for {phone}")

            if self._patient_upsert_cache_ttl > 0:
                self._patient_upsert_cache[(actual_clinic_id, phone)] = perf_counter()

        except Exception as e:
            logger.error(f"Failed to upsert patient from WhatsApp: {e}")

    def _extract_name_from_message(self, message: str) -> tuple[str, str]:
        """Extract first and last name from user message

        Handles patterns like:
        - "Me llamo Juan Pérez"
        - "My name is John Smith"
        - "I'm Maria Garcia"
        - "Soy Carlos López"
        - "Eu sou João Silva"
        """
        import re

        message_lower = message.lower()

        # Patterns for name extraction
        patterns = [
            # Spanish
            r'(?:me llamo|mi nombre es|soy)\s+([A-ZÁ-Ü][a-zá-ü]+(?:\s+[A-ZÁ-Ü][a-zá-ü]+)+)',
            # English
            r'(?:my name is|i\'m|i am|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
            # Portuguese
            r'(?:meu nome é|eu sou)\s+([A-ZÁ-Ü][a-zá-ü]+(?:\s+[A-ZÁ-Ü][a-zá-ü]+)+)',
            # Hebrew
            r'(?:שמי|קוראים לי)\s+([א-ת]+(?:\s+[א-ת]+)+)',
            # Russian
            r'(?:меня зовут|я)\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                full_name = match.group(1).strip()
                # Split into first and last name
                parts = full_name.split()
                if len(parts) >= 2:
                    first_name = parts[0]
                    last_name = ' '.join(parts[1:])
                    logger.info(f"📝 Extracted name from message: {first_name} {last_name}")
                    return (first_name, last_name)
                elif len(parts) == 1:
                    return (parts[0], '')

        return (None, None)

    async def _log_conversation(
        self,
        session_id: str,
        clinic_id: str,
        user_message: str,
        ai_response: str,
        language: str
    ):
        """Log conversation to database using RPC function"""
        try:
            client = get_supabase_client()
            if not client:
                logger.debug("Supabase client unavailable; skipping conversation log")
                return

            # Use RPC function to log conversation
            # Updated parameter names to match new RPC signature
            result = client.rpc('log_whatsapp_conversation', {
                'p_clinic_id': clinic_id,
                'p_from_phone': getattr(self, 'current_from_phone', ''),
                'p_to_phone': getattr(self, 'current_to_phone', ''),
                'p_message_content': user_message,  # Changed from p_message
                'p_ai_response': ai_response,  # Changed from p_response
                'p_message_sid': getattr(self, 'current_message_sid', f'msg_{session_id}'),
                'p_detected_language': language,  # Changed from p_language
                'p_organization_id': None  # Optional, will be looked up from clinic_id
            }).execute()

            if result.data:
                if result.data.get('success'):
                    logger.info(f"Conversation logged successfully: session_id={result.data.get('session_id')}")
                elif result.data.get('error') and 'duplicate key value violates unique constraint' in result.data.get('error', '').lower():
                    logger.debug("Skipping duplicate WhatsApp conversation log for message_sid=%s", getattr(self, 'current_message_sid', 'unknown'))
                else:
                    logger.warning(f"Failed to log conversation: {result.data}")
        except Exception as e:
            # If logging fails, just print error (non-critical for MVP)
            print(f"Could not log to database: {e}")

    async def _extract_and_update_constraints(
        self,
        session_id: str,
        message: str,
        detected_language: str
    ) -> ConversationConstraints:
        """Extract constraints from user message and update storage (Phase 2)"""

        # Detect forget/exclusion patterns
        entities_to_exclude = self.constraint_extractor.detect_forget_pattern(message, detected_language)

        if entities_to_exclude:
            logger.info(f"🚫 Detected exclusions: {entities_to_exclude}")

            # Add each entity to exclusions
            for entity in entities_to_exclude:
                # Determine if it's a doctor or service based on context
                # For now, add to both and let validation handle it
                await self.constraints_manager.update_constraints(
                    session_id,
                    exclude_doctor=entity,
                    exclude_service=entity
                )

        # Detect switch patterns ("instead of X, want Y")
        switch_result = self.constraint_extractor.detect_switch_pattern(message, detected_language)

        if switch_result:
            exclude_entity, desired_entity = switch_result
            logger.info(f"🔄 Detected switch: {exclude_entity} → {desired_entity}")

            await self.constraints_manager.update_constraints(
                session_id,
                desired_service=desired_entity,
                exclude_service=exclude_entity
            )

        # Detect time window normalization
        time_window = self.constraint_extractor.normalize_time_window(
            message,
            datetime.now(),
            detected_language
        )

        if time_window:
            logger.info(f"📅 Normalized time window: {time_window[2]}")
            await self.constraints_manager.update_constraints(
                session_id,
                time_window=time_window
            )

        # Return updated constraints
        return await self.constraints_manager.get_constraints(session_id)

    def _build_constraints_section(self, constraints: ConversationConstraints) -> str:
        """
        Build constraints section for system prompt (Phase 3).

        This section is ALWAYS injected FIRST, before any other context.
        Format is structured (YAML-like) for easier LLM parsing.
        """
        if not constraints or (
            not constraints.desired_service
            and not constraints.desired_doctor
            and not constraints.excluded_doctors
            and not constraints.excluded_services
            and not constraints.time_window_start
        ):
            return ""

        lines = ["\n🔒 CONVERSATION CONSTRAINTS (MUST ENFORCE):\n"]

        # Current intent
        if constraints.desired_service:
            lines.append(f"  - Current Service: {constraints.desired_service}")
        if constraints.desired_doctor:
            lines.append(f"  - Preferred Doctor: {constraints.desired_doctor}")

        # Exclusions
        if constraints.excluded_doctors:
            excluded_docs = ", ".join(constraints.excluded_doctors)
            lines.append(f"  - NEVER suggest these doctors: {excluded_docs}")
        if constraints.excluded_services:
            excluded_svcs = ", ".join(constraints.excluded_services)
            lines.append(f"  - NEVER suggest these services: {excluded_svcs}")

        # Time window
        if constraints.time_window_start:
            lines.append(
                f"  - Time Window: {constraints.time_window_display} "
                f"({constraints.time_window_start} to {constraints.time_window_end})"
            )

        lines.append("\nIMPORTANT: These constraints OVERRIDE all other context. "
                    "If a tool call violates these, STOP and ask for clarification.\n")

        return "\n".join(lines)

# FastAPI endpoint handler
async def handle_process_message(request: MessageRequest) -> MessageResponse:
    """Main endpoint handler for processing messages"""
    processor = MultilingualMessageProcessor()
    return await processor.process_message(request)
