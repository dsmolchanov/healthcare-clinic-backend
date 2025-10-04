"""
Orchestrator Factory
Dynamically creates LangGraph orchestrators from agent configurations
"""
from typing import Optional, Dict, Any
import logging
from app.services.agent_service import AgentConfig
from app.services.orchestrator.base_langgraph import BaseLangGraphOrchestrator
from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph
from app.services.orchestrator.templates.general_template import GeneralLangGraph

logger = logging.getLogger(__name__)


class OrchestratorFactory:
    """Factory for creating LangGraph orchestrators from agent configs"""

    def __init__(self):
        self._orchestrator_cache = {}

    async def create_orchestrator(
        self,
        agent_config: AgentConfig,
        context: Optional[Dict[str, Any]] = None
    ):
        """
        Create orchestrator instance from agent configuration

        Args:
            agent_config: Agent configuration
            context: Additional context (clinic_id, services, etc.)

        Returns:
            Orchestrator instance (BaseLangGraphOrchestrator or subclass)
        """
        # Check cache
        cache_key = f"orchestrator:{agent_config.id}"
        if cache_key in self._orchestrator_cache:
            logger.debug(f"Using cached orchestrator for agent {agent_config.name}")
            return self._orchestrator_cache[cache_key]

        # Determine orchestrator type
        orchestrator_type = agent_config.orchestrator_type
        base_template = agent_config.base_template

        logger.info(
            f"Creating orchestrator: type={orchestrator_type}, "
            f"template={base_template}, agent={agent_config.name}"
        )

        # Create orchestrator based on type
        if base_template == "HealthcareLangGraph":
            orchestrator = await self._create_healthcare_orchestrator(
                agent_config, context
            )
        elif base_template == "GeneralLangGraph":
            orchestrator = await self._create_general_orchestrator(
                agent_config, context
            )
        else:
            # Default to base orchestrator
            orchestrator = await self._create_base_orchestrator(
                agent_config, context
            )

        # Cache it
        self._orchestrator_cache[cache_key] = orchestrator

        return orchestrator

    async def _create_healthcare_orchestrator(
        self,
        agent_config: AgentConfig,
        context: Optional[Dict[str, Any]]
    ) -> HealthcareLangGraph:
        """Create healthcare-specific orchestrator"""
        # Import dependencies
        from app.services.orchestrator.tools.appointment_tools import AppointmentTools
        from app.db.supabase_client import get_supabase_client

        # Get services from context
        clinic_id = context.get("clinic_id") if context else None
        supabase_client = context.get("supabase_client") if context else get_supabase_client()

        # Initialize PHI middleware if compliance mode is HIPAA
        phi_middleware = None
        if agent_config.langgraph_config.get("compliance_mode") == "hipaa":
            try:
                from app.security.phi_encryption import get_phi_middleware
                phi_middleware = get_phi_middleware()
            except ImportError:
                logger.warning("PHI middleware not available, creating orchestrator without PHI protection")
                phi_middleware = None

        # Initialize appointment tools if calendar capability enabled
        appointment_service = None
        if "calendar_integration" in agent_config.capabilities:
            appointment_service = AppointmentTools(
                supabase_client=supabase_client,
                clinic_id=clinic_id
            )

        # Create orchestrator (HealthcareLangGraph doesn't accept enable_* parameters)
        orchestrator = HealthcareLangGraph(
            phi_middleware=phi_middleware,
            appointment_service=appointment_service,
            enable_emergency_detection=True,
            supabase_client=supabase_client,
            clinic_id=clinic_id
        )

        # Override system prompt from agent config
        orchestrator.system_prompt = agent_config.system_prompt

        return orchestrator

    async def _create_general_orchestrator(
        self,
        agent_config: AgentConfig,
        context: Optional[Dict[str, Any]]
    ) -> GeneralLangGraph:
        """Create general-purpose orchestrator"""
        orchestrator = GeneralLangGraph(
            enable_memory=agent_config.langgraph_config.get("enable_memory", True),
            enable_rag=agent_config.langgraph_config.get("enable_rag", True),
            enable_checkpointing=agent_config.langgraph_config.get("enable_checkpointing", True)
        )

        orchestrator.system_prompt = agent_config.system_prompt

        return orchestrator

    async def _create_base_orchestrator(
        self,
        agent_config: AgentConfig,
        context: Optional[Dict[str, Any]]
    ) -> BaseLangGraphOrchestrator:
        """Create base orchestrator"""
        orchestrator = BaseLangGraphOrchestrator(
            enable_memory=agent_config.langgraph_config.get("enable_memory", True),
            enable_rag=agent_config.langgraph_config.get("enable_rag", True),
            enable_checkpointing=agent_config.langgraph_config.get("enable_checkpointing", True)
        )

        orchestrator.system_prompt = agent_config.system_prompt

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