# File: clinics/backend/app/services/direct_lane/direct_tool_executor.py

from typing import Dict, Any, Optional, List, Tuple
import asyncio
from datetime import datetime
import logging
import time
import hashlib
import uuid
import os

from app.services.direct_lane.tool_intent_classifier import DirectToolIntent, ToolIntentMatch
from app.services.direct_lane.circuit_breaker import CircuitBreaker
from postgrest.exceptions import APIError

logger = logging.getLogger(__name__)

class DirectToolExecutor:
    """
    Execute tools directly without LangGraph orchestration.

    STRICT BUDGETS:
    - Total per-turn budget: 800ms backend
    - FAQ: < 150ms
    - Price: < 100ms
    - Availability: < 200ms
    - Booking: < 700ms (includes hold + confirm)

    SAFETY:
    - Circuit breaker on consecutive failures (5 failures → open for 60s)
    - Automatic fallback to LangGraph on timeout or circuit open
    """

    def __init__(self, clinic_id: str, supabase_client, api_supabase_client=None):
        self.clinic_id = clinic_id
        self.supabase = supabase_client
        self.api_supabase = api_supabase_client or supabase_client

        # Initialize circuit breaker (5 failures → open for 60s)
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60
        )

        # Per-turn budget enforcement
        self.max_duration_ms = 800

        # Memory retrieval budget (50ms for mem0 context)
        self.memory_budget_ms = max(
            50,
            int(os.getenv("DIRECT_LANE_MEMORY_BUDGET_MS", "250"))
        )
        self._price_tool = None
        self._redis_client = None

    async def execute_tool(
        self,
        tool_match: ToolIntentMatch,
        context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Execute a tool based on intent match with STRICT BUDGET.

        Args:
            tool_match: Result from ToolIntentClassifier
            context: Session context

        Returns:
            {
                "success": bool,
                "response": str,  # Formatted for user
                "tool_used": str,
                "latency_ms": int,
                "metadata": dict,
                "fallback_triggered": bool  # True if circuit breaker opened
            }
        """
        start_time = time.time()

        try:
            # Check circuit breaker
            if self.circuit_breaker.is_open(tool_match.intent.value):
                logger.warning(f"Circuit breaker OPEN for {tool_match.intent.value}")
                return {
                    "success": False,
                    "error": "Circuit breaker is open",
                    "fallback_triggered": True,
                    "latency_ms": int((time.time() - start_time) * 1000)
                }

            # Execute tool with timeout
            result = await asyncio.wait_for(
                self._execute_tool_internal(tool_match, context),
                timeout=self.max_duration_ms / 1000  # Convert to seconds
            )

            # Add metadata
            latency_ms = max(1, int((time.time() - start_time) * 1000))
            result["latency_ms"] = latency_ms
            result["tool_used"] = tool_match.intent.value
            result["routing_path"] = "direct_function_call"
            if result.get("success"):
                self.circuit_breaker.record_success(tool_match.intent.value)
                result["fallback_triggered"] = False

                # Warn if approaching budget
                if latency_ms > self.max_duration_ms * 0.8:
                    logger.warning(
                        f"Direct lane approaching budget: {latency_ms}ms "
                        f"(threshold: {self.max_duration_ms}ms)"
                    )

                logger.info(f"Direct tool execution: {tool_match.intent.value} in {latency_ms}ms")
            else:
                self.circuit_breaker.record_failure(tool_match.intent.value)
                result["fallback_triggered"] = result.get("fallback_triggered", True)
                logger.warning(
                    f"Direct tool execution failed for {tool_match.intent.value}: "
                    f"{result.get('error', 'unknown error')}"
                )

            return result

        except asyncio.TimeoutError:
            # Budget exceeded → fallback to LangGraph
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"Direct tool TIMEOUT: {tool_match.intent.value} "
                f"exceeded {self.max_duration_ms}ms budget"
            )
            self.circuit_breaker.record_failure(tool_match.intent.value)

            return {
                "success": False,
                "error": "Budget exceeded",
                "fallback_triggered": True,
                "latency_ms": latency_ms
            }

        except Exception as e:
            # Unexpected error → record failure and fallback
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Direct tool execution failed: {e}", exc_info=True)
            self.circuit_breaker.record_failure(tool_match.intent.value)

            return {
                "success": False,
                "error": str(e),
                "tool_used": tool_match.intent.value,
                "latency_ms": latency_ms,
                "fallback_triggered": True
            }

    async def _get_memory_context(
        self,
        phone_number: str,
        query: str,
        limit: int = 2
    ) -> List[str]:
        """
        Retrieve mem0 conversation context with timeout protection.

        Args:
            phone_number: User's phone number
            query: Query for retrieving relevant memories
            limit: Maximum number of memories to retrieve

        Returns:
            List of memory strings, or empty list if unavailable/timeout
        """
        try:
            from app.memory.conversation_memory import get_memory_manager

            memory_manager = get_memory_manager()

            # Retrieve memories with strict timeout (50ms budget)
            memories = await asyncio.wait_for(
                memory_manager.get_memory_context(
                    phone_number=phone_number,
                    clinic_id=self.clinic_id,
                    query=query,
                    limit=limit
                ),
                timeout=self.memory_budget_ms / 1000  # Convert to seconds
            )

            logger.info(f"Retrieved {len(memories)} memories for {phone_number[:8]}*** in direct lane")
            return memories

        except asyncio.TimeoutError:
            logger.warning(
                f"Memory retrieval timed out (>{self.memory_budget_ms}ms), "
                "continuing without context"
            )
            return []

        except Exception as e:
            logger.warning(f"Memory retrieval failed: {e}, continuing without context")
            return []

    async def _store_memory_async(
        self,
        phone_number: str,
        user_message: str,
        assistant_response: str,
        session_id: str,
        metadata: Optional[Dict] = None
    ):
        """
        Store conversation turn in mem0 (fire-and-forget, non-blocking).

        Args:
            phone_number: User's phone number
            user_message: User's message
            assistant_response: Assistant's response
            session_id: Session identifier
            metadata: Optional metadata
        """
        try:
            from app.memory.conversation_memory import get_memory_manager

            memory_manager = get_memory_manager()

            # Fire-and-forget storage (don't await to avoid blocking response)
            asyncio.create_task(
                memory_manager.store_conversation_turn(
                    session_id=session_id,
                    user_message=user_message,
                    assistant_response=assistant_response,
                    phone_number=phone_number,
                    metadata=metadata
                )
            )

            logger.debug(f"Queued memory storage for session {session_id}")

        except Exception as e:
            # Don't fail the request if memory storage fails
            logger.warning(f"Failed to queue memory storage: {e}")

    async def _execute_tool_internal(
        self,
        tool_match: ToolIntentMatch,
        context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Internal method to execute a tool with mem0 context.

        Args:
            tool_match: Intent match from classifier
            context: Session context

        Returns:
            Tool execution result
        """
        # NEW: Retrieve mem0 context for personalization
        phone_number = context.get('phone_number') if context else None
        memories = []

        if phone_number:
            # Get relevant memories (budget: 50ms)
            query = tool_match.extracted_args.get('query', '') or context.get('message', '')
            memories = await self._get_memory_context(
                phone_number=phone_number,
                query=query,
                limit=2  # Keep it fast
            )

        # Execute tool with memory context
        if tool_match.intent == DirectToolIntent.FAQ_QUERY:
            result = await self._execute_faq(tool_match.extracted_args, context, memories)

        elif tool_match.intent == DirectToolIntent.PRICE_QUERY:
            result = await self._execute_price_query(tool_match.extracted_args, context, memories)

        elif tool_match.intent == DirectToolIntent.CHECK_AVAILABILITY:
            result = await self._execute_availability_check(tool_match.extracted_args, context, memories)

        elif tool_match.intent == DirectToolIntent.BOOK_APPOINTMENT:
            result = await self._execute_booking(tool_match.extracted_args, context, memories)

        else:
            return {
                "success": False,
                "error": f"Unknown tool intent: {tool_match.intent}"
            }

        # NEW: Store conversation turn in mem0 (async, non-blocking)
        if result.get("success") and phone_number:
            user_message = context.get('message', '') if context else ''
            assistant_response = result.get('response', '')

            await self._store_memory_async(
                phone_number=phone_number,
                user_message=user_message,
                assistant_response=assistant_response,
                session_id=context.get('session_id', str(uuid.uuid4())) if context else str(uuid.uuid4()),
                metadata={
                    'tool': tool_match.intent.value,
                    'clinic_id': self.clinic_id,
                    'timestamp': datetime.utcnow().isoformat()
                }
            )

        return result

    async def _execute_faq(
        self,
        args: Dict,
        context: Optional[Dict],
        memories: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Execute FAQ query using Redis cache with simple matching (budget: <150ms).

        Args:
            args: Extracted arguments (query, language)
            context: Session context
            memories: Relevant memories from mem0

        Returns:
            FAQ query result
        """
        query = args.get("query", "")
        language = args.get("language", "en")

        try:
            # Get FAQs from Redis cache (fast!)
            from app.services.clinic_data_cache import ClinicDataCache
            from app.config import get_redis_client

            redis = get_redis_client()
            cache = ClinicDataCache(redis, default_ttl=3600)
            faqs = await cache.get_faqs(self.clinic_id, self.supabase)

            # Filter by language if specified
            if language:
                faqs = [faq for faq in faqs if faq.get('language') == language]

            # Simple substring matching (fast and good enough for direct lane)
            query_lower = query.lower()
            matches = [
                faq for faq in faqs
                if query_lower in faq.get('question', '').lower()
                or query_lower in faq.get('answer', '').lower()
                or any(query_lower in tag.lower() for tag in faq.get('tags', []))
            ]

            # Sort by priority and take top 3
            matches.sort(key=lambda x: x.get('priority', 0), reverse=True)
            matches = matches[:3]

            if not matches:
                # Use memory context to provide more helpful response
                memory_context = ""
                if memories:
                    memory_context = (
                        " Based on our previous conversations, "
                        "I remember you were interested in certain topics."
                    )

                return {
                    "success": True,
                    "response": (
                        f"I couldn't find an answer to that question.{memory_context} "
                        "Would you like me to connect you with a staff member?"
                    ),
                    "faqs": []
                }

            # Format response
            lines = []
            for faq in matches:
                lines.append(f"**Q: {faq['question']}**")
                lines.append(f"A: {faq['answer']}\n")

            # Add personalized context if available
            if memories and len(memories) > 0:
                lines.append("\n_Based on our previous conversations, this might be helpful._")

            return {
                "success": True,
                "response": "\n".join(lines),
                "faqs": matches,
                "metadata": {
                    "source": "faq_cache",
                    "query": query,
                    "language": language,
                    "results_count": len(matches),
                    "used_memories": len(memories) if memories else 0
                }
            }

        except Exception as e:
            logger.error(f"FAQ query failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _execute_price_query(
        self,
        args: Dict,
        context: Optional[Dict],
        memories: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Execute price query using multilingual resilient search with memory context (budget: <100ms).

        Uses multi-layer search:
        - Layer 0: Exact alias matching (zero-miss for key services)
        - Layer 1: Dual-language FTS (Russian + English)
        - Layer 2: FTS with OR relaxation and prefix matching
        - Layer 3: Trigram fuzzy matching (typo tolerance)

        Args:
            args: Extracted arguments (query)
            context: Session context
            memories: Relevant memories from mem0

        Returns:
            Price query result
        """
        query = args.get("query", "")

        # Check if query references previous context ("that service", "same one", etc.)
        if memories and any(ref in query.lower() for ref in ['that', 'same', 'previous', 'last']):
            for memory in memories:
                if 'service' in memory.lower() or 'price' in memory.lower():
                    logger.info(f"Using memory context to resolve query: {query}")
                    break

        try:
            services, used_legacy_rpc = await self._price_search_with_cache(
                query=query,
                limit=5,
                session_id=context.get('session_id') if context else None
            )

            if not services:
                memory_hint = ""
                if memories:
                    memory_hint = " You previously asked about some services - would you like me to recall those?"

                return {
                    "success": True,
                    "response": f"No services found matching '{query}'.{memory_hint} Please try a different search term.",
                    "metadata": {
                        "services_found": 0,
                        "query": query,
                        "search_stage": "none",
                        "used_memories": len(memories) if memories else 0,
                        "legacy_rpc_fallback": used_legacy_rpc
                    }
                }

            lines = [f"Found {len(services)} service(s):\n"]
            for i, svc in enumerate(services, 1):
                price_value = svc.get('base_price')
                if price_value is None:
                    price_value = svc.get('price')
                price_str = f"${price_value:.2f}" if price_value else "Contact us"
                lines.append(f"{i}. **{svc['name']}** - {price_str}")
                if svc.get('description'):
                    lines.append(f"   {svc['description'][:80]}...")

            if memories:
                for memory in memories:
                    if any(svc['name'].lower() in memory.lower() for svc in services):
                        lines.append("\n_I remember you were interested in this before!_")
                        break

            return {
                "success": True,
                "response": "\n".join(lines),
                "metadata": {
                    "services_found": len(services),
                    "query": query,
                    "search_stage": services[0].get('search_stage', 'unknown') if services else 'none',
                    "used_memories": len(memories) if memories else 0,
                    "legacy_rpc_fallback": used_legacy_rpc
                }
            }

        except Exception as e:
            logger.error(f"Price query failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _price_search_with_cache(
        self,
        query: str,
        limit: int,
        session_id: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Run price search using cached services first, falling back to RPC if needed.

        Returns:
            (services, used_legacy_rpc)
        """
        try:
            price_tool = self._get_price_tool()
            results = await price_tool.get_services_by_query(
                query=query,
                limit=limit,
                session_id=session_id
            )

            deduped: Dict[str, Dict[str, Any]] = {}
            for service in results:
                service_id = service.get("id")
                if not service_id:
                    continue
                deduped.setdefault(service_id, service)

            return list(deduped.values()), False
        except Exception as tool_exc:
            logger.warning(f"PriceQueryTool failed ({tool_exc}); falling back to RPC search")
            services, used_legacy = self._price_search_via_rpc(query, limit, session_id)
            return services, used_legacy

    def _get_price_tool(self):
        """
        Lazily instantiate PriceQueryTool with Redis caching.
        """
        if self._price_tool is not None:
            return self._price_tool

        from app.tools.price_query_tool import PriceQueryTool
        from app.config import get_redis_client

        if self._redis_client is None:
            self._redis_client = get_redis_client()

        self._price_tool = PriceQueryTool(
            clinic_id=self.clinic_id,
            redis_client=self._redis_client
        )
        return self._price_tool

    def _price_search_via_rpc(
        self,
        query: str,
        limit: int,
        session_id: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Legacy fallback using direct RPC calls (no cache). Returns services and
        whether we fell back to the legacy multilingual RPC.
        """
        payload = {
            'p_clinic_id': self.clinic_id,
            'p_query': query,
            'p_limit': limit,
            'p_min_score': 0.01,
            'p_session_id': session_id or str(uuid.uuid4())
        }

        used_legacy_rpc = False

        try:
            response = self.api_supabase.rpc('search_services_v1', payload).execute()
        except APIError as api_err:
            used_legacy_rpc = True
            logger.warning(
                "Primary price search RPC failed (%s). Falling back to legacy multilingual search.",
                getattr(api_err, 'message', api_err)
            )
            response = self.supabase.rpc('search_services_multilingual', payload).execute()

        services = response.data or []
        deduped: Dict[str, Dict[str, Any]] = {}
        for service in services:
            service_id = service.get("id")
            if not service_id:
                continue
            deduped.setdefault(service_id, service)

        return list(deduped.values()), used_legacy_rpc

    async def _execute_availability_check(
        self,
        args: Dict,
        context: Optional[Dict],
        memories: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Execute availability check with memory context (budget: <200ms).

        Args:
            args: Extracted arguments (date)
            context: Session context
            memories: Relevant memories from mem0

        Returns:
            Availability check result
        """
        date_str = args.get("date")

        if not date_str:
            memory_date_hint = ""
            if memories:
                # Check if user has preferred times from previous conversations
                for memory in memories:
                    if any(time in memory.lower() for time in ['morning', 'afternoon', 'evening', 'usual']):
                        memory_date_hint = " I remember you usually prefer certain times."
                        break

            return {
                "success": False,
                "response": f"Please specify a date (e.g., 'today', 'tomorrow', or a specific date).{memory_date_hint}"
            }

        # Call materialized view query (fast!)
        result = await self._check_availability_fast(date_str)

        if not result.get("success"):
            return {
                "success": False,
                "response": result.get("error", "Failed to check availability")
            }

        # Format response
        slots = result.get("available_slots", [])
        if not slots:
            return {
                "success": True,
                "response": f"No available slots on {date_str}. Would you like to check another date?"
            }

        lines = [f"Available slots on {date_str}:\n"]

        # Filter/prioritize slots based on user preferences from memory
        preferred_times = []
        if memories:
            for memory in memories:
                if 'morning' in memory.lower() and any('morning' in str(slot.get('slot_start', '')).lower() for slot in slots):
                    preferred_times.append('morning')
                if 'afternoon' in memory.lower():
                    preferred_times.append('afternoon')
                if 'evening' in memory.lower():
                    preferred_times.append('evening')

        for i, slot in enumerate(slots[:5], 1):
            start = datetime.fromisoformat(slot['slot_start'])
            time_str = start.strftime('%I:%M %p')

            # Highlight preferred times based on memory
            if preferred_times:
                hour = start.hour
                if ('morning' in preferred_times and 6 <= hour < 12) or \
                   ('afternoon' in preferred_times and 12 <= hour < 17) or \
                   ('evening' in preferred_times and 17 <= hour < 21):
                    time_str += " ⭐"  # Mark preferred time

            lines.append(f"{i}. {time_str}")

        lines.append("\nReply with a slot number to book, or ask for another date.")

        if preferred_times:
            lines.append(f"\n_⭐ = Your usual preferred time_")

        return {
            "success": True,
            "response": "\n".join(lines),
            "metadata": {
                "date": date_str,
                "slots_count": len(slots),
                "slots": slots,  # Store for booking context
                "used_memories": len(memories) if memories else 0,
                "preferred_times": preferred_times
            }
        }

    async def _check_availability_fast(self, date_str: str) -> Dict[str, Any]:
        """Query materialized availability view (ultra-fast)"""
        try:
            from datetime import date, timedelta

            # Parse date
            if date_str == "today":
                target_date = date.today()
            elif date_str == "tomorrow":
                target_date = date.today() + timedelta(days=1)
            else:
                target_date = datetime.fromisoformat(date_str).date()

            # Query materialized view via RPC
            response = self.supabase.rpc(
                'get_available_slots_fast',
                {
                    'p_clinic_id': self.clinic_id,
                    'p_doctor_id': None,  # Get all doctors for now
                    'p_date': target_date.isoformat(),
                    'p_duration_minutes': 30
                }
            ).execute()

            if not response.data:
                return {"success": True, "available_slots": []}

            return {
                "success": True,
                "available_slots": response.data
            }

        except Exception as e:
            logger.error(f"Availability check failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _execute_booking(
        self,
        args: Dict,
        context: Optional[Dict],
        memories: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Execute appointment booking using two-phase saga with memory context (budget: <700ms).

        Flow:
        1. create_hold (200ms)
        2. confirm_hold (300ms)
        3. Queue calendar sync (async, <100ms)

        Args:
            args: Extracted arguments (selected_slot)
            context: Session context
            memories: Relevant memories from mem0

        Returns:
            Booking result
        """
        # Extract slot from context
        selected_slot = args.get("selected_slot") or (context.get("selected_slot") if context else None)

        if not selected_slot:
            memory_hint = ""
            if memories:
                # Check if user has previously booked appointments
                for memory in memories:
                    if 'appointment' in memory.lower() or 'booked' in memory.lower():
                        memory_hint = " You've booked appointments with us before!"
                        break

            return {
                "success": False,
                "response": f"Please select a time slot first.{memory_hint} Ask 'What times are available?' to see options."
            }

        # Generate idempotency keys
        session_id = context.get("session_id", str(uuid.uuid4())) if context else str(uuid.uuid4())
        hold_idem_key = hashlib.sha256(f"{session_id}:hold:{selected_slot}".encode()).hexdigest()
        confirm_idem_key = hashlib.sha256(f"{session_id}:confirm:{selected_slot}".encode()).hexdigest()

        try:
            # Phase 1: Create hold
            hold_result = self.supabase.rpc(
                'create_hold',
                {
                    'p_slot_id': selected_slot.get('slot_id'),
                    'p_doctor_id': selected_slot.get('doctor_id'),
                    'p_room_id': selected_slot.get('room_id'),
                    'p_service_id': context.get('service_id') if context else None,
                    'p_patient_id': context.get('patient_id') if context else None,
                    'p_start_time': selected_slot.get('slot_start'),
                    'p_end_time': selected_slot.get('slot_end'),
                    'p_clinic_id': self.clinic_id,
                    'p_idempotency_key': hold_idem_key
                }
            ).execute()

            if not hold_result.data or not hold_result.data.get('success'):
                error_msg = hold_result.data.get('message', 'Failed to hold slot') if hold_result.data else 'Failed to hold slot'
                return {
                    "success": False,
                    "response": error_msg
                }

            hold_id = hold_result.data.get('hold_id')

            # Phase 2: Confirm hold
            confirm_result = self.supabase.rpc(
                'confirm_hold',
                {
                    'p_hold_id': hold_id,
                    'p_idempotency_key': confirm_idem_key
                }
            ).execute()

            if not confirm_result.data or not confirm_result.data.get('success'):
                # Compensate: release hold
                await self._release_hold(hold_id, "Confirmation failed")
                error_msg = confirm_result.data.get('message', 'Failed to confirm appointment') if confirm_result.data else 'Failed to confirm appointment'
                return {
                    "success": False,
                    "response": error_msg
                }

            appointment_id = confirm_result.data.get('appointment_id')

            # Success!
            start_time = datetime.fromisoformat(selected_slot.get('slot_start'))

            # Personalize confirmation based on memory
            confirmation = f"Your appointment has been booked for {start_time.strftime('%B %d at %I:%M %p')}."

            if memories:
                # Check if this is a returning patient
                for memory in memories:
                    if any(phrase in memory.lower() for phrase in ['previous appointment', 'last visit', 'came before']):
                        confirmation += " We look forward to seeing you again!"
                        break
                else:
                    # Check if patient has preferences
                    if any('doctor' in mem.lower() for mem in memories):
                        confirmation += " We've noted your preferences."

            return {
                "success": True,
                "response": confirmation,
                "metadata": {
                    "appointment_id": appointment_id,
                    "hold_id": hold_id,
                    "calendar_sync": "queued",
                    "used_memories": len(memories) if memories else 0
                }
            }

        except Exception as e:
            logger.error(f"Booking failed: {e}", exc_info=True)
            # Attempt compensation if we got a hold
            if 'hold_id' in locals():
                await self._release_hold(hold_id, f"Error: {str(e)}")

            return {
                "success": False,
                "response": "Failed to book appointment. Please try again or contact us."
            }

    async def _release_hold(self, hold_id: str, reason: str):
        """Compensating action: release a hold"""
        try:
            self.supabase.rpc(
                'release_hold',
                {
                    'p_hold_id': hold_id,
                    'p_reason': reason
                }
            ).execute()
            logger.info(f"Released hold {hold_id}: {reason}")
        except Exception as e:
            logger.error(f"Failed to release hold {hold_id}: {e}")
