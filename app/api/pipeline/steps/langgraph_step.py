"""
LangGraphExecutionStep - Route complex conversations through LangGraph orchestrator.

Phase 3B of the Agentic Flow Architecture Refactor.

This step routes complex AI path conversations through the existing LangGraph
orchestrator for multi-turn flows requiring state tracking and tool coordination.

Usage:
    - SCHEDULING/COMPLEX lanes are routed through LangGraph
    - Feature flagged via ENABLE_LANGGRAPH + clinic whitelist
    - Falls through to LLMGenerationStep if skipped
"""

import logging
import time
from typing import Tuple, Any, Optional

from cachetools import TTLCache

from ..base import PipelineStep
from ..context import PipelineContext
from app.config import (
    ENABLE_LANGGRAPH,
    LANGGRAPH_CLINIC_WHITELIST,
    LANGGRAPH_ENABLED_LANES,
)
from app.services.state_model import FlowState, TurnStatus, ConversationState as StateModelConversationState
from app.services.state_manager import get_state_manager

logger = logging.getLogger(__name__)


class LangGraphExecutionStep(PipelineStep):
    """
    Routes complex conversations through LangGraph orchestrator.

    Used for:
    - Multi-turn booking flows (SCHEDULING lane)
    - Complex queries requiring multiple tool calls (COMPLEX lane)
    - Conversations requiring state tracking

    Feature flags:
    - ENABLE_LANGGRAPH: Master switch for LangGraph routing
    - LANGGRAPH_CLINIC_WHITELIST: List of clinic IDs to enable (empty = disabled for all)
    - LANGGRAPH_ENABLED_LANES: Lanes that trigger LangGraph (default: SCHEDULING, COMPLEX)
    """

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
        # Cache up to 5 orchestrators with 15-minute TTL
        # Conservative for 512MB: worst-case 5 × 50MB = 250MB cache
        # 15-min TTL accommodates healthcare's async WhatsApp pacing
        self._orchestrator_cache: TTLCache = TTLCache(maxsize=5, ttl=900)

    @property
    def name(self) -> str:
        return "langgraph_execution"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute LangGraph step.

        Checks feature flags and routes to orchestrator if applicable.

        Returns:
            - (ctx, False) if LangGraph handled the response (stop pipeline)
            - (ctx, True) if skipped (continue to LLMGenerationStep)
        """
        start_time = time.time()

        # 1. Check master feature flag
        if not ENABLE_LANGGRAPH:
            logger.debug("LangGraph disabled via ENABLE_LANGGRAPH=false")
            return ctx, True

        # 2. Check clinic whitelist
        clinic_id = ctx.effective_clinic_id
        if LANGGRAPH_CLINIC_WHITELIST and clinic_id not in LANGGRAPH_CLINIC_WHITELIST:
            logger.debug(f"LangGraph skipped: clinic {clinic_id} not in whitelist")
            return ctx, True

        # 3. Check if lane triggers LangGraph
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
            # 4. Get or create orchestrator for this clinic
            orchestrator = await self._get_orchestrator(ctx)
            if not orchestrator:
                logger.warning("Failed to create orchestrator, falling through to LLM step")
                return ctx, True

            # 5. Build initial state from pipeline context
            initial_state = self._build_orchestrator_state(ctx)

            # 6. Execute graph
            result = await orchestrator.process(
                message=ctx.message,
                session_id=ctx.session_id or "unknown",
                metadata=initial_state.get("metadata", {}),
                context=initial_state.get("context", {}),
            )

            # 7. Extract response
            if result and result.get("response"):
                ctx.response = result["response"]
                ctx.response_metadata["langgraph"] = True
                ctx.response_metadata["orchestrator_audit"] = result.get("audit_trail", [])
                ctx.response_metadata["langgraph_intent"] = result.get("intent")

                # 8. Update state based on orchestrator result
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
        """
        # Determine state transition from result
        state_transition = result.get("state_transition")
        intent = result.get("intent")
        appointment_booked = result.get("context", {}).get("appointment_booked", False)

        # Update flow state on context
        if state_transition:
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

        # Optionally persist to state manager if available
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
        """
        clinic_id = ctx.effective_clinic_id

        if clinic_id in self._orchestrator_cache:
            return self._orchestrator_cache[clinic_id]

        try:
            # Import orchestrator components
            from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph

            # Build agent config from context
            agent_config = {
                "llm_settings": {
                    "primary_model": "gpt-4o-mini",
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

            self._orchestrator_cache[clinic_id] = orchestrator
            logger.info(f"[LangGraph] Created orchestrator for clinic {clinic_id}, cache size: {len(self._orchestrator_cache)}/{self._orchestrator_cache.maxsize}")

            return orchestrator

        except Exception as e:
            logger.error(f"[LangGraph] Failed to create orchestrator: {e}", exc_info=True)
            return None

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
