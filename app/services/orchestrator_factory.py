"""
Orchestrator Factory
Creates FSM orchestrators for conversation handling.

Phase 6: Now uses pure FSM orchestrator (legacy LangGraph removed).
"""
from typing import Optional, Dict, Any
import logging
from app.services.agent_service import AgentConfig
from app.services.orchestrator.fsm_orchestrator import FSMOrchestrator
from app.config import get_redis_client

logger = logging.getLogger(__name__)


class OrchestratorFactory:
    """Factory for creating FSM orchestrators from agent configs"""

    def __init__(self):
        self._orchestrator_cache = {}

    async def create_orchestrator(
        self,
        agent_config: AgentConfig,
        context: Optional[Dict[str, Any]] = None
    ) -> FSMOrchestrator:
        """
        Create FSM orchestrator instance from agent configuration

        Args:
            agent_config: Agent configuration
            context: Additional context (clinic_id, services, etc.)

        Returns:
            FSMOrchestrator instance
        """
        # Check cache
        cache_key = f"orchestrator:{agent_config.id}"
        if cache_key in self._orchestrator_cache:
            logger.debug(f"Using cached orchestrator for agent {agent_config.name}")
            return self._orchestrator_cache[cache_key]

        logger.info(
            f"Creating FSM orchestrator for agent={agent_config.name}"
        )

        # Create FSM orchestrator
        orchestrator = await self._create_fsm_orchestrator(agent_config, context)

        # Cache it
        self._orchestrator_cache[cache_key] = orchestrator

        return orchestrator

    async def _create_fsm_orchestrator(
        self,
        agent_config: AgentConfig,
        context: Optional[Dict[str, Any]]
    ) -> FSMOrchestrator:
        """Create FSM orchestrator"""
        from app.services.orchestrator.tools.appointment_tools import AppointmentTools
        from app.tools.price_query_tool import PriceQueryTool
        from app.services.llm import LLMFactory
        from app.db.supabase_client import get_supabase_client

        # Get services from context
        clinic_id = context.get("clinic_id") if context else None
        supabase_client = context.get("supabase_client") if context else get_supabase_client()
        clinic_profile = context.get("clinic_profile", {}) if context else {}

        # Initialize LLM factory
        llm_factory = LLMFactory(supabase_client=supabase_client)

        # Initialize tools
        appointment_tools = None
        if "calendar_integration" in agent_config.capabilities:
            appointment_tools = AppointmentTools(
                supabase_client=supabase_client,
                clinic_id=clinic_id
            )

        price_tool = None
        try:
            # Pass redis_client to use cached services from startup warmup
            redis_client = get_redis_client()
            price_tool = PriceQueryTool(clinic_id=clinic_id, redis_client=redis_client)
        except Exception as e:
            logger.warning(f"Failed to create PriceQueryTool: {e}")

        # Create FSM orchestrator
        orchestrator = FSMOrchestrator(
            clinic_id=clinic_id,
            llm_factory=llm_factory,
            supabase_client=supabase_client,
            appointment_tools=appointment_tools,
            price_tool=price_tool,
            clinic_profile=clinic_profile,
        )

        return orchestrator

    def invalidate_cache(self, agent_id: str = None):
        """Invalidate orchestrator cache"""
        if agent_id:
            cache_key = f"orchestrator:{agent_id}"
            self._orchestrator_cache.pop(cache_key, None)
        else:
            self._orchestrator_cache.clear()


# Singleton
_orchestrator_factory = None


def get_orchestrator_factory() -> OrchestratorFactory:
    """Get singleton orchestrator factory"""
    global _orchestrator_factory
    if _orchestrator_factory is None:
        _orchestrator_factory = OrchestratorFactory()
    return _orchestrator_factory
