"""
Agent Service
Loads and manages agent configurations with LangGraph support
"""
from typing import Optional, Dict, Any, List
from app.db.supabase_client import get_supabase_client
from datetime import datetime
import logging
import random

logger = logging.getLogger(__name__)


class AgentConfig:
    """Agent configuration with merged template + customizations"""

    def __init__(self, data: Dict[str, Any]):
        self.id = data["id"]
        self.organization_id = data["organization_id"]
        self.name = data["name"]
        self.type = data["type"]
        self.parent_agent_id = data.get("parent_agent_id")
        self.configuration = data.get("configuration", {})
        self.langgraph_config = data.get("langgraph_config", {})
        self.delegation_config = data.get("delegation_config", [])
        self.quick_ack_config = data.get("quick_ack_config", {})
        self.capabilities = data.get("capabilities", [])
        self.tools = data.get("tools", [])
        self.is_active = data["is_active"]

    @property
    def system_prompt(self) -> str:
        """Get system prompt"""
        return self.configuration.get("system_prompt", "You are a helpful assistant.")

    @property
    def language(self) -> str:
        """Get default language"""
        return self.configuration.get("language", "en")

    @property
    def orchestrator_type(self) -> str:
        """Get LangGraph orchestrator type"""
        return self.langgraph_config.get("orchestrator_type", "general")

    @property
    def base_template(self) -> str:
        """Get base template class name"""
        return self.langgraph_config.get("base_template", "BaseLangGraphOrchestrator")

    def get_quick_ack_message(self, language: str = "en") -> Optional[str]:
        """Get quick ack message for language with randomization"""
        if not self.quick_ack_config.get("enabled"):
            return None

        messages = self.quick_ack_config.get("messages", {})
        lang_messages = messages.get(language, messages.get("en", []))

        if not lang_messages:
            return "One moment..."

        if self.quick_ack_config.get("randomize") and len(lang_messages) > 1:
            return random.choice(lang_messages)

        return lang_messages[0]

    def get_delegation_rule(self, intent: str) -> Optional[Dict[str, Any]]:
        """Get delegation rule for intent"""
        for rule in self.delegation_config:
            if rule.get("intent") == intent:
                return rule
        return None

    def should_delegate(self, intent: str) -> bool:
        """Check if intent should be delegated"""
        rule = self.get_delegation_rule(intent)
        return rule is not None and "delegate_to_type" in rule


class AgentService:
    """Service for managing multi-agent configurations"""

    def __init__(self):
        self.supabase = get_supabase_client()
        self._cache = {}  # Simple in-memory cache

    async def get_agent_for_organization(
        self,
        organization_id: str,
        agent_type: str = "receptionist"
    ) -> Optional[AgentConfig]:
        """
        Get active agent for organization by type

        Args:
            organization_id: Organization UUID
            agent_type: Agent type (receptionist, appointment_specialist, etc.)

        Returns:
            AgentConfig or None if no active agent
        """
        # Check cache first (cache for 5 minutes)
        cache_key = f"agent:org:{organization_id}:type:{agent_type}"
        if cache_key in self._cache:
            cached_at, cached_config = self._cache[cache_key]
            if (datetime.now() - cached_at).seconds < 300:  # 5 min
                return cached_config

        try:
            # Fetch from database using RPC function
            result = self.supabase.rpc(
                "get_agent_for_organization",
                {
                    "org_id": organization_id,
                    "agent_type": agent_type
                }
            ).execute()

            if not result.data:
                logger.warning(
                    f"No active agent of type '{agent_type}' found for org {organization_id}"
                )
                return None

            agent_data = result.data[0]
            config = AgentConfig(agent_data)

            # Cache it
            self._cache[cache_key] = (datetime.now(), config)

            logger.info(
                f"Loaded agent config: {config.name} (id={config.id}, type={config.type})"
            )
            return config

        except Exception as e:
            logger.error(
                f"Failed to load agent for org {organization_id}, type {agent_type}: {e}"
            )
            return None

    async def get_child_agents(
        self,
        parent_agent_id: str
    ) -> List[AgentConfig]:
        """Get all child specialist agents for an orchestrator"""
        try:
            result = self.supabase.rpc(
                "get_child_agents",
                {"parent_uuid": parent_agent_id}
            ).execute()

            return [AgentConfig(data) for data in result.data]

        except Exception as e:
            logger.error(f"Failed to load child agents for {parent_agent_id}: {e}")
            return []

    async def get_agent_by_id(self, agent_id: str) -> Optional[AgentConfig]:
        """Get agent by ID"""
        try:
            result = self.supabase.rpc(
                "get_agent_by_id",
                {"agent_uuid": agent_id}
            ).execute()

            if result.data:
                return AgentConfig(result.data[0])
            return None

        except Exception as e:
            logger.error(f"Failed to load agent {agent_id}: {e}")
            return None

    def invalidate_cache(self, organization_id: str = None):
        """Invalidate cache for organization (or all)"""
        if organization_id:
            # Clear all entries for this org
            keys_to_remove = [k for k in self._cache.keys() if f"org:{organization_id}" in k]
            for key in keys_to_remove:
                self._cache.pop(key, None)
        else:
            self._cache.clear()


# Singleton instance
_agent_service = None


def get_agent_service() -> AgentService:
    """Get singleton agent service"""
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service