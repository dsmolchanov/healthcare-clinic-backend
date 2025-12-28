"""
LangGraphExecutionStep - Route complex conversations through LangGraph or FSM orchestrator.

Phase 3B of the Agentic Flow Architecture Refactor.

This step routes complex AI path conversations through either:
- FSM orchestrator (USE_FSM_ORCHESTRATOR=true) - Pure Python state machine, fast and deterministic
- LangGraph orchestrator (default) - Multi-node graph with LLM reasoning

Usage:
    - SCHEDULING/COMPLEX lanes are routed through orchestrator
    - Falls through to LLMGenerationStep if skipped
"""

import logging
import os
import time
from typing import Dict, Tuple, Any, Optional

from cachetools import TTLCache

from ..base import PipelineStep
from ..context import PipelineContext
from app.config import LANGGRAPH_ENABLED_LANES
from app.services.state_model import FlowState, TurnStatus, ConversationState as StateModelConversationState
from app.services.state_manager import get_state_manager
from app.services.orchestrator.thread_ids import make_thread_id, make_checkpoint_ns

logger = logging.getLogger(__name__)

# Feature flag for FSM orchestrator
# Phase 6: FSM is now the default orchestrator (87.5% eval pass rate achieved)
# Set USE_FSM_ORCHESTRATOR=false to fall back to LangGraph if needed
USE_FSM_ORCHESTRATOR = os.environ.get("USE_FSM_ORCHESTRATOR", "true").lower() == "true"


class LangGraphExecutionStep(PipelineStep):
    """
    Routes complex conversations through LangGraph orchestrator.

    Used for:
    - Multi-turn booking flows (SCHEDULING lane)
    - Complex queries requiring multiple tool calls (COMPLEX lane)
    - Conversations requiring state tracking

    Always enabled for SCHEDULING/COMPLEX lanes (feature flags removed in Phase 2).
    """

    # In-memory state store for when Redis is unavailable (e.g., in evals)
    # Key: session_id, Value: serialized FSM state dict
    _in_memory_state_store: Dict[str, Any] = {}

    def __init__(
        self,
        supabase_client=None,
        redis_client=None,
    ):
        """
        Initialize LangGraphExecutionStep.

        Args:
            supabase_client: Supabase client for orchestrator initialization
            redis_client: Redis client for state management
        """
        self.supabase = supabase_client
        self.redis = redis_client
        # Cache up to 2 orchestrators with 10-minute TTL
        # Conservative for 1GB: worst-case 2 × 50MB = 100MB cache
        # 10-min TTL is sufficient for WhatsApp conversation pacing
        self._orchestrator_cache: TTLCache = TTLCache(maxsize=2, ttl=600)

    @property
    def name(self) -> str:
        return "langgraph_execution"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute LangGraph step.

        Routes SCHEDULING/COMPLEX lanes through orchestrator.

        Returns:
            - (ctx, False) if LangGraph handled the response (stop pipeline)
            - (ctx, True) if skipped (continue to LLMGenerationStep)
        """
        start_time = time.time()
        clinic_id = ctx.effective_clinic_id

        # Check if lane triggers LangGraph
        # ctx.lane may be Lane enum or string; normalize to uppercase string for comparison
        lane = ctx.lane
        if hasattr(lane, 'value'):
            lane_str = lane.value.upper()  # Lane enum -> "COMPLEX"
        else:
            lane_str = (lane or "COMPLEX").upper()

        if lane_str not in [l.upper() for l in LANGGRAPH_ENABLED_LANES]:
            logger.debug(f"LangGraph skipped: lane {lane_str} not in enabled lanes {LANGGRAPH_ENABLED_LANES}")
            return ctx, True

        logger.info(f"[LangGraph] Routing {lane} conversation for clinic {clinic_id}")

        try:
            # 4. Load flow_state from Redis (state_manager stores it there)
            if self.redis and ctx.session_id:
                try:
                    redis_key = f"session:{ctx.session_id}"
                    stored_state = self.redis.hget(redis_key, 'conversation_state')
                    if stored_state:
                        ctx.flow_state = stored_state if isinstance(stored_state, str) else stored_state.decode('utf-8')
                        logger.info(f"[LangGraph] Loaded flow_state={ctx.flow_state} from Redis")
                    else:
                        logger.debug(f"[LangGraph] No flow_state in Redis, using default: {ctx.flow_state}")
                except Exception as e:
                    logger.warning(f"[LangGraph] Failed to load flow_state from Redis: {e}")

            # 5. Generate session-scoped thread_id for checkpointer
            patient_id = ctx.from_phone or "unknown"  # Phone as patient identifier
            session_id = ctx.session_id or "unknown"
            thread_id = make_thread_id(clinic_id, patient_id, session_id)
            checkpoint_ns = make_checkpoint_ns(clinic_id)

            # Store in context for observability
            ctx.thread_id = thread_id
            ctx.checkpoint_ns = checkpoint_ns

            # Log session continuity vs rotation
            is_new_session = getattr(ctx, 'is_new_session', False)
            if is_new_session:
                reset_type = getattr(ctx, 'reset_type', 'unknown')
                logger.info(
                    f"[LangGraph] New session - fresh thread_id: {thread_id[:30]}... "
                    f"(reset_type={reset_type})"
                )
            else:
                logger.debug(f"[LangGraph] Continuing session - thread_id: {thread_id[:30]}...")

            # 6. Get or create orchestrator for this clinic
            orchestrator = await self._get_orchestrator(ctx)
            if not orchestrator:
                logger.warning("Failed to create orchestrator, falling through to LLM step")
                return ctx, True

            # 7. Build initial state from pipeline context
            initial_state = self._build_orchestrator_state(ctx)

            # 8. Execute orchestrator (FSM or LangGraph)
            if USE_FSM_ORCHESTRATOR:
                # FSM orchestrator - pass state for multi-turn persistence
                fsm_state = self._load_fsm_state(ctx)
                result = await orchestrator.process(
                    message=ctx.message,
                    session_id=session_id,
                    state=fsm_state,
                    language=ctx.detected_language or "en",
                )
            else:
                # LangGraph orchestrator
                result = await orchestrator.process(
                    message=ctx.message,
                    session_id=session_id,
                    metadata=initial_state.get("metadata", {}),
                    context=initial_state.get("context", {}),
                )

            # 9. Extract response
            if result and result.get("response"):
                ctx.response = result["response"]
                ctx.response_metadata["langgraph"] = not USE_FSM_ORCHESTRATOR
                ctx.response_metadata["fsm"] = USE_FSM_ORCHESTRATOR
                ctx.response_metadata["orchestrator_audit"] = result.get("audit_trail", [])
                ctx.response_metadata["langgraph_intent"] = result.get("intent")
                ctx.response_metadata["route"] = result.get("route")

                # Phase 6: Expose internal tool tracking for eval harness
                # FSM uses "tools_called", LangGraph uses "tools_actually_called"
                # FSM format: [{"name": "tool_name", "args": {...}}]
                # LangGraph format: ["tool_name", ...]
                # Normalize to list of tool names for eval harness
                raw_tools = result.get("tools_called", result.get("tools_actually_called", []))
                tools_called = []
                for t in raw_tools:
                    if isinstance(t, dict):
                        tools_called.append(t.get("name", str(t)))
                    else:
                        tools_called.append(str(t))
                ctx.response_metadata["internal_tools_called"] = tools_called
                ctx.response_metadata["internal_tools_failed"] = result.get("tools_failed", [])
                ctx.response_metadata["executor_validation_errors"] = result.get("executor_validation_errors", [])
                ctx.response_metadata["planner_validation_errors"] = result.get("planner_validation_errors", [])
                ctx.response_metadata["hallucination_blocked"] = result.get("hallucination_blocked", False)
                ctx.response_metadata["booking_blocked_no_availability"] = result.get("booking_blocked_no_availability_check", False)
                ctx.response_metadata["guardrail_triggered"] = result.get("guardrail_triggered", False)

                # 10. Update state based on orchestrator result
                await self._update_state_from_result(ctx, result)

                duration_ms = (time.time() - start_time) * 1000
                ctx.step_timings["langgraph_execution"] = duration_ms
                logger.info(f"[LangGraph] Completed in {duration_ms:.1f}ms")

                # Response generated, stop pipeline
                return ctx, False

            # No response from orchestrator, fall through to LLM step
            logger.warning("[LangGraph] Orchestrator returned no response, falling through to LLM")
            return ctx, True

        except Exception as e:
            logger.error(f"[LangGraph] Execution failed: {e}", exc_info=True)
            # On error, fall through to LLM step as safety net
            ctx.response_metadata["langgraph_error"] = str(e)
            return ctx, True

    def _load_fsm_state(self, ctx: PipelineContext) -> Optional[Dict[str, Any]]:
        """
        Load FSM state from Redis or in-memory store for multi-turn conversations.

        Falls back to in-memory store when Redis is unavailable (e.g., in evals).
        Returns None for new conversations.
        """
        if not ctx.session_id:
            return None

        # Try Redis first if available
        if self.redis:
            try:
                redis_key = f"fsm_state:{ctx.session_id}"
                stored_state = self.redis.get(redis_key)
                if stored_state:
                    import json
                    state_dict = json.loads(stored_state if isinstance(stored_state, str) else stored_state.decode('utf-8'))
                    logger.debug(f"[FSM] Loaded state from Redis: stage={state_dict.get('stage')}")
                    return state_dict
            except Exception as e:
                logger.warning(f"[FSM] Failed to load state from Redis: {e}")

        # Fallback to in-memory store (for evals/testing without Redis)
        if ctx.session_id in self._in_memory_state_store:
            state_dict = self._in_memory_state_store[ctx.session_id]
            logger.debug(f"[FSM] Loaded state from in-memory store: stage={state_dict.get('stage')}")
            return state_dict

        return None

    def _build_orchestrator_state(self, ctx: PipelineContext) -> Dict[str, Any]:
        """
        Build initial state for orchestrator from PipelineContext.

        Converts pipeline context into the format expected by BaseLangGraphOrchestrator.
        """
        # Extract constraints using to_dict() for proper serialization
        constraints_dict = {}
        if ctx.constraints:
            try:
                constraints_dict = ctx.constraints.to_dict()
            except Exception:
                # Fallback to basic extraction for testing
                constraints_dict = {
                    "excluded_doctors": list(ctx.constraints.excluded_doctors or []),
                    "excluded_services": list(ctx.constraints.excluded_services or []),
                    "desired_doctor": ctx.constraints.desired_doctor,
                    "desired_service": ctx.constraints.desired_service,
                }

        # Build context dict for orchestrator
        context = {
            "clinic_profile": ctx.clinic_profile or {},
            "patient_profile": ctx.patient_profile or {},
            "clinic_doctors": ctx.clinic_doctors or [],    # Available doctors
            "clinic_services": ctx.clinic_services or [],  # Available services with prices
            "clinic_faqs": ctx.clinic_faqs or [],          # Clinic FAQs for context
            "constraints": constraints_dict,
            "language": ctx.detected_language,
            "lane": ctx.lane,
            "flow_state": ctx.flow_state,
            "turn_status": ctx.turn_status,
            "conversation_history": ctx.session_messages or [],
        }

        # Build metadata
        metadata = {
            "phone_number": ctx.from_phone,
            "clinic_id": ctx.effective_clinic_id,
            "session_id": ctx.session_id,
            "correlation_id": ctx.correlation_id,
            "channel": ctx.channel,
            "profile_name": ctx.profile_name,
        }

        return {
            "session_id": ctx.session_id or "unknown",
            "message": ctx.message,
            "context": context,
            "metadata": metadata,
            "memories": [],
            "knowledge": ctx.knowledge_context or [],
        }

    async def _update_state_from_result(
        self,
        ctx: PipelineContext,
        result: Dict[str, Any]
    ):
        """
        Update pipeline context and state manager from orchestrator result.

        Maps orchestrator state transitions to the two-layer state model.
        CRITICAL: Persists flow_state to Redis for multi-turn conversations.
        """
        # Determine state transition from result
        state_transition = result.get("state_transition")
        flow_state_from_result = result.get("flow_state")  # Direct from orchestrator
        intent = result.get("intent")
        appointment_booked = result.get("context", {}).get("appointment_booked", False)

        # Update flow state on context - prefer explicit flow_state from orchestrator
        if flow_state_from_result:
            ctx.flow_state = flow_state_from_result
            logger.info(f"[LangGraph] Flow state from orchestrator: {flow_state_from_result}")
        elif state_transition:
            # Map orchestrator state transition string to FlowState enum
            try:
                new_flow_state = FlowState(state_transition)
                ctx.flow_state = new_flow_state.value
            except ValueError:
                logger.debug(f"Unknown state transition: {state_transition}")
        elif appointment_booked:
            ctx.flow_state = FlowState.COMPLETED.value
        elif intent == "appointment":
            ctx.flow_state = FlowState.COLLECTING_SLOTS.value

        # Update turn status if agent promised followup
        should_escalate = result.get("should_escalate", False)
        if should_escalate:
            ctx.turn_status = TurnStatus.ESCALATED.value
            ctx.should_escalate = True
        elif result.get("pending_action"):
            ctx.turn_status = TurnStatus.AGENT_ACTION_PENDING.value
            ctx.last_agent_action = result.get("pending_action")

        # Persist FSM state for multi-turn continuity
        if USE_FSM_ORCHESTRATOR and ctx.session_id:
            fsm_state = result.get("state")
            if fsm_state:
                # Try Redis first
                if self.redis:
                    try:
                        import json
                        redis_key = f"fsm_state:{ctx.session_id}"
                        self.redis.set(redis_key, json.dumps(fsm_state))
                        # Set TTL to 30 minutes for conversation continuity
                        self.redis.expire(redis_key, 1800)
                        logger.debug(f"[FSM] Persisted state to Redis: stage={fsm_state.get('stage')}")
                    except Exception as e:
                        logger.warning(f"[FSM] Failed to persist state to Redis: {e}")
                        # Fall through to in-memory
                else:
                    # Fallback to in-memory store (for evals/testing without Redis)
                    self._in_memory_state_store[ctx.session_id] = fsm_state
                    logger.debug(f"[FSM] Persisted state to in-memory store: stage={fsm_state.get('stage')}")

        # Persist flow_state to Redis for multi-turn continuity (LangGraph)
        if self.redis and ctx.session_id and ctx.flow_state:
            try:
                redis_key = f"session:{ctx.session_id}"
                self.redis.hset(redis_key, 'conversation_state', ctx.flow_state)
                # Set TTL to 30 minutes for conversation continuity
                self.redis.expire(redis_key, 1800)
                logger.debug(f"[LangGraph] Persisted flow_state={ctx.flow_state} to Redis")
            except Exception as e:
                logger.warning(f"[LangGraph] Failed to persist flow_state to Redis: {e}")

        # Also persist via state manager if available
        if self.redis and ctx.session_id:
            try:
                state_manager = get_state_manager(
                    session_id=ctx.session_id,
                    clinic_id=ctx.effective_clinic_id,
                    redis_client=self.redis,
                    supabase_client=self.supabase,
                    use_fsm=False,  # AI path
                )

                if should_escalate:
                    await state_manager.mark_escalated()
                elif appointment_booked:
                    await state_manager.mark_resolved()
                elif flow_state_from_result:
                    # Persist explicit flow_state from orchestrator
                    try:
                        new_state = FlowState(flow_state_from_result)
                        await state_manager.update_flow_state(new_state)
                    except ValueError:
                        pass
                elif state_transition:
                    try:
                        new_state = FlowState(state_transition)
                        await state_manager.update_flow_state(new_state)
                    except ValueError:
                        pass
            except Exception as e:
                logger.warning(f"Failed to update state manager: {e}")

    async def _get_orchestrator(self, ctx: PipelineContext):
        """
        Get or create orchestrator instance for clinic.

        Uses caching to avoid creating multiple instances per clinic.
        Returns FSM orchestrator if USE_FSM_ORCHESTRATOR flag is set.
        """
        clinic_id = ctx.effective_clinic_id

        # Check cache key includes orchestrator type
        cache_key = f"{clinic_id}_{'fsm' if USE_FSM_ORCHESTRATOR else 'langgraph'}"
        if cache_key in self._orchestrator_cache:
            return self._orchestrator_cache[cache_key]

        try:
            if USE_FSM_ORCHESTRATOR:
                # Use FSM orchestrator - pure Python, fast and deterministic
                return await self._create_fsm_orchestrator(ctx, cache_key)
            else:
                # Use LangGraph orchestrator - multi-node graph with LLM reasoning
                return await self._create_langgraph_orchestrator(ctx, cache_key)

        except Exception as e:
            logger.error(f"[Orchestrator] Failed to create orchestrator: {e}", exc_info=True)
            return None

    async def _create_fsm_orchestrator(self, ctx: PipelineContext, cache_key: str):
        """Create FSM orchestrator instance."""
        from app.services.orchestrator.fsm_orchestrator import FSMOrchestrator
        from app.services.orchestrator.tools.appointment_tools import AppointmentTools
        from app.tools.price_query_tool import PriceQueryTool
        from app.services.llm import LLMFactory

        clinic_id = ctx.effective_clinic_id

        # Initialize LLM factory for router (requires supabase for capability matrix)
        llm_factory = LLMFactory(supabase_client=self.supabase)

        # Initialize tools
        appointment_tools = AppointmentTools(
            supabase_client=self.supabase,
            clinic_id=clinic_id
        ) if self.supabase else None

        price_tool = None
        try:
            price_tool = PriceQueryTool(
                clinic_id=clinic_id,
                redis_client=self.redis
            )
        except Exception as e:
            logger.warning(f"[FSM] Failed to create PriceQueryTool: {e}")

        # Create FSM orchestrator
        orchestrator = FSMOrchestrator(
            clinic_id=clinic_id,
            llm_factory=llm_factory,
            supabase_client=self.supabase,
            appointment_tools=appointment_tools,
            price_tool=price_tool,
            clinic_profile=ctx.clinic_profile or {},
        )

        self._orchestrator_cache[cache_key] = orchestrator
        logger.info(f"[FSM] Created FSM orchestrator for clinic {clinic_id}")

        return orchestrator

    async def _create_langgraph_orchestrator(self, ctx: PipelineContext, cache_key: str):
        """Create LangGraph orchestrator instance."""
        from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph

        clinic_id = ctx.effective_clinic_id

        # Build agent config from context
        # Model selection: TIER_TOOL_CALLING_MODEL > default
        primary_model = os.environ.get("TIER_TOOL_CALLING_MODEL", "gpt-5-mini")
        agent_config = {
            "llm_settings": {
                "primary_model": primary_model,
                "temperature": 0.7,
                "max_tokens": 1024,
            },
            "capabilities": ["calendar_integration"] if ctx.clinic_profile else [],
        }

        # Create orchestrator
        orchestrator = HealthcareLangGraph(
            supabase_client=self.supabase,
            clinic_id=clinic_id,
            agent_config=agent_config,
        )

        self._orchestrator_cache[cache_key] = orchestrator
        logger.info(f"[LangGraph] Created orchestrator for clinic {clinic_id}, cache size: {len(self._orchestrator_cache)}/{self._orchestrator_cache.maxsize}")

        return orchestrator

    def invalidate_cache(self, clinic_id: Optional[str] = None):
        """
        Invalidate orchestrator cache.

        Args:
            clinic_id: Specific clinic to invalidate, or None for all
        """
        if clinic_id:
            self._orchestrator_cache.pop(clinic_id, None)
        else:
            self._orchestrator_cache.clear()
