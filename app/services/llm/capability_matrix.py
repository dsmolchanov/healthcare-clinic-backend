from typing import Dict, List, Optional, Any
from app.services.llm.base_adapter import ModelCapability, LLMProvider
import logging

logger = logging.getLogger(__name__)


class CapabilityMatrix:
    """Query and route to models based on capability requirements"""

    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self._cache: Dict[str, ModelCapability] = {}

    async def load_model(self, model_name: str) -> ModelCapability:
        """Load model metadata from database"""
        if model_name in self._cache:
            return self._cache[model_name]

        result = self.supabase.schema('public').table('llm_models')\
            .select('*')\
            .eq('model_name', model_name)\
            .eq('is_active', True)\
            .single()\
            .execute()

        if not result.data:
            raise ValueError(f"Model '{model_name}' not found or inactive")

        capability = ModelCapability(**result.data)
        self._cache[model_name] = capability
        return capability

    async def get_default_model(self) -> ModelCapability:
        """Get the default model (GLM-4.5)"""
        result = self.supabase.schema('public').table('llm_models')\
            .select('*')\
            .eq('is_default', True)\
            .eq('is_active', True)\
            .single()\
            .execute()

        if not result.data:
            raise ValueError("No default model configured")

        return ModelCapability(**result.data)

    async def route_by_requirements(
        self,
        requires_tools: bool = False,
        requires_parallel_tools: bool = False,
        requires_json_mode: bool = False,
        max_latency_ms: Optional[int] = None,
        max_cost_per_1m: Optional[float] = None,
        prefer_speed: bool = False
    ) -> ModelCapability:
        """Select best model matching requirements"""

        query = self.supabase.schema('public').table('llm_models')\
            .select('*')\
            .eq('is_active', True)\
            .eq('is_production_ready', True)

        # Apply hard requirements
        if requires_tools:
            query = query.eq('supports_tool_calling', True)
        if requires_parallel_tools:
            query = query.eq('supports_parallel_tools', True)
        if requires_json_mode:
            query = query.eq('supports_json_mode', True)
        if max_latency_ms:
            query = query.lte('p95_latency_ms', max_latency_ms)
        if max_cost_per_1m:
            query = query.lte('output_price_per_1m', max_cost_per_1m)

        result = query.execute()

        if not result.data:
            logger.warning("No models match requirements, falling back to default")
            return await self.get_default_model()

        # Sort by preference
        candidates = [ModelCapability(**row) for row in result.data]

        # Filter out Cerebras models (temporarily disabled due to httpx compatibility)
        candidates = [m for m in candidates if m.provider != LLMProvider.CEREBRAS]

        if not candidates:
            logger.warning("No compatible models found after filtering, falling back to default")
            return await self.get_default_model()

        if prefer_speed:
            # Sort by output speed descending
            candidates.sort(key=lambda m: m.avg_output_speed, reverse=True)
        else:
            # Sort by cost ascending, then tool success rate descending
            candidates.sort(
                key=lambda m: (
                    m.output_price_per_1m,
                    -(m.tool_calling_success_rate or 0)
                )
            )

        selected = candidates[0]
        logger.info(f"Routed to model: {selected.model_name} (provider: {selected.provider})")
        return selected
