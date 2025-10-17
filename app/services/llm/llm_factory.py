from typing import Dict, List, Any, Optional
from app.services.llm.base_adapter import LLMAdapter, LLMResponse, LLMProvider
from app.services.llm.capability_matrix import CapabilityMatrix
from app.services.llm.adapters.glm_adapter import GLMAdapter
from app.services.llm.adapters.gemini_adapter import GeminiAdapter
from app.services.llm.adapters.openai_adapter import OpenAIAdapter
# from app.services.llm.adapters.cerebras_adapter import CerebrasAdapter  # Disabled due to httpx compatibility
import logging
import time

logger = logging.getLogger(__name__)


class LLMFactory:
    """Unified LLM factory with multi-provider support"""

    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self.capability_matrix = CapabilityMatrix(supabase_client)
        self._adapter_cache: Dict[str, LLMAdapter] = {}

    async def create_adapter(self, model_name: str) -> LLMAdapter:
        """Create adapter for specific model"""
        if model_name in self._adapter_cache:
            return self._adapter_cache[model_name]

        # Load capability
        capability = await self.capability_matrix.load_model(model_name)

        # Create provider-specific adapter
        if capability.provider == LLMProvider.GLM:
            adapter = GLMAdapter(capability)
        elif capability.provider == LLMProvider.GOOGLE:
            adapter = GeminiAdapter(capability)
        elif capability.provider == LLMProvider.OPENAI:
            adapter = OpenAIAdapter(capability)
        elif capability.provider == LLMProvider.CEREBRAS:
            # Cerebras disabled due to httpx 0.28.1 compatibility issues
            logger.warning(f"Cerebras provider disabled, falling back to OpenAI")
            raise ValueError(f"Cerebras provider temporarily disabled")
        else:
            raise ValueError(f"Unsupported provider: {capability.provider}")

        self._adapter_cache[model_name] = adapter
        return adapter

    async def generate(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        requires_tools: bool = False,
        **kwargs
    ) -> LLMResponse:
        """Generate response with automatic model selection"""

        # Route to appropriate model if not specified
        if not model:
            capability = await self.capability_matrix.route_by_requirements(
                requires_tools=requires_tools,
                max_latency_ms=kwargs.get('max_latency_ms', 2000),
                prefer_speed=kwargs.get('prefer_speed', False)
            )
            model = capability.model_name
            logger.info(f"Auto-routed to model: {model}")

        # Get adapter
        adapter = await self.create_adapter(model)

        # Generate
        try:
            response = await adapter.generate(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

            # Track metrics
            await self._track_metrics(response)

            return response

        except Exception as e:
            logger.error(f"Generation failed for {model}: {e}")
            # Try fallback
            return await self._fallback_generate(messages, model, temperature, max_tokens, **kwargs)

    async def generate_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate response with tool calling"""

        # Route to model with tool support
        if not model:
            # Force GPT-4o-mini for tool calling (supports tool calling properly)
            model = "gpt-4o-mini"
            logger.info(f"Using GPT-4o-mini for tool calling (forced default)")

        # Get adapter
        adapter = await self.create_adapter(model)

        # Validate tool calling support
        if not adapter.capability.supports_tool_calling:
            raise ValueError(f"Model {model} does not support tool calling")

        # Generate with tools
        try:
            response = await adapter.generate_with_tools(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

            # Track metrics
            await self._track_metrics(response, tool_calls_count=len(response.tool_calls))

            return response

        except Exception as e:
            logger.error(f"Tool calling failed for {model}: {e}")
            # Try fallback with tools
            return await self._fallback_generate_with_tools(
                messages, tools, model, temperature, max_tokens, **kwargs
            )

    async def _fallback_generate(
        self,
        messages: List[Dict[str, str]],
        failed_model: str,
        temperature: float,
        max_tokens: Optional[int],
        **kwargs
    ) -> LLMResponse:
        """Fallback to default model"""
        default_model = await self.capability_matrix.get_default_model()

        if default_model.model_name == failed_model:
            # Default already failed, try OpenAI
            logger.warning("Default model failed, trying OpenAI fallback")
            adapter = await self.create_adapter("gpt-5-nano")
        else:
            logger.warning(f"Falling back to default model: {default_model.model_name}")
            adapter = await self.create_adapter(default_model.model_name)

        response = await adapter.generate(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )

        await self._track_metrics(response, is_fallback=True)
        return response

    async def _fallback_generate_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        failed_model: str,
        temperature: float,
        max_tokens: Optional[int],
        **kwargs
    ) -> LLMResponse:
        """Fallback to OpenAI for tool calling (highest accuracy)"""
        logger.warning("Tool calling failed, falling back to OpenAI GPT-5-mini")
        adapter = await self.create_adapter("gpt-5-mini")

        response = await adapter.generate_with_tools(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )

        await self._track_metrics(response, tool_calls_count=len(response.tool_calls), is_fallback=True)
        return response

    async def _track_metrics(
        self,
        response: LLMResponse,
        tool_calls_count: int = 0,
        is_fallback: bool = False
    ):
        """Track LLM usage metrics to database"""
        try:
            # Calculate cost
            capability = await self.capability_matrix.load_model(response.model)
            input_cost = (response.usage['input_tokens'] / 1_000_000) * capability.input_price_per_1m
            output_cost = (response.usage['output_tokens'] / 1_000_000) * capability.output_price_per_1m
            total_cost = input_cost + output_cost

            # Log metrics
            logger.info(
                f"LLM metrics: model={response.model}, "
                f"tokens={response.usage['total_tokens']}, "
                f"cost=${total_cost:.6f}, "
                f"latency={response.latency_ms}ms, "
                f"tools={tool_calls_count}, "
                f"fallback={is_fallback}"
            )

            # Could insert to message_metrics table here
            # (deferred to integration phase)

        except Exception as e:
            logger.warning(f"Failed to track metrics: {e}")
