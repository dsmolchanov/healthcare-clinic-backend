from typing import Dict, List, Any, Optional
from langfuse import observe
from app.services.llm.base_adapter import LLMAdapter, LLMResponse, LLMProvider, ModelCapability
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

    # Hardcoded model capabilities to avoid database dependency
    BUILTIN_MODELS: Dict[str, 'ModelCapability'] = {}

    @classmethod
    def _get_builtin_capabilities(cls) -> Dict[str, 'ModelCapability']:
        """Lazily initialize builtin model capabilities"""
        if not cls.BUILTIN_MODELS:
            cls.BUILTIN_MODELS = {
                'gemini-3-flash-preview': ModelCapability(
                    provider='google',
                    model_name='gemini-3-flash-preview',
                    display_name='Gemini 3 Flash Preview',
                    input_price_per_1m=0.10,
                    output_price_per_1m=0.40,
                    max_input_tokens=1000000,
                    max_output_tokens=8192,
                    avg_output_speed_tokens_per_sec=150.0,
                    avg_ttft_seconds=0.2,
                    p95_latency_ms=1500,
                    supports_streaming=True,
                    supports_tool_calling=True,
                    tool_calling_success_rate=0.95,
                    supports_parallel_tools=True,
                    supports_json_mode=True,
                    supports_structured_output=True,
                    supports_thinking_mode=False,
                    api_endpoint='https://generativelanguage.googleapis.com/v1beta/models',
                    requires_api_key_env_var='GOOGLE_API_KEY',
                    base_url_override=None
                ),
                'gpt-4o-mini': ModelCapability(
                    provider='openai',
                    model_name='gpt-4o-mini',
                    display_name='GPT-4o Mini',
                    input_price_per_1m=0.15,
                    output_price_per_1m=0.60,
                    max_input_tokens=128000,
                    max_output_tokens=16384,
                    avg_output_speed_tokens_per_sec=100.0,
                    avg_ttft_seconds=0.3,
                    p95_latency_ms=2000,
                    supports_streaming=True,
                    supports_tool_calling=True,
                    tool_calling_success_rate=0.98,
                    supports_parallel_tools=True,
                    supports_json_mode=True,
                    supports_structured_output=True,
                    supports_thinking_mode=False,
                    api_endpoint='https://api.openai.com/v1/chat/completions',
                    requires_api_key_env_var='OPENAI_API_KEY',
                    base_url_override=None
                ),
                'gpt-4o': ModelCapability(
                    provider='openai',
                    model_name='gpt-4o',
                    display_name='GPT-4o',
                    input_price_per_1m=2.50,
                    output_price_per_1m=10.00,
                    max_input_tokens=128000,
                    max_output_tokens=16384,
                    avg_output_speed_tokens_per_sec=80.0,
                    avg_ttft_seconds=0.5,
                    p95_latency_ms=3000,
                    supports_streaming=True,
                    supports_tool_calling=True,
                    tool_calling_success_rate=0.99,
                    supports_parallel_tools=True,
                    supports_json_mode=True,
                    supports_structured_output=True,
                    supports_thinking_mode=False,
                    api_endpoint='https://api.openai.com/v1/chat/completions',
                    requires_api_key_env_var='OPENAI_API_KEY',
                    base_url_override=None
                ),
            }
        return cls.BUILTIN_MODELS

    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self.capability_matrix = CapabilityMatrix(supabase_client)
        self._adapter_cache: Dict[str, LLMAdapter] = {}

    async def create_adapter(self, model_name: str) -> LLMAdapter:
        """Create adapter for specific model"""
        if model_name in self._adapter_cache:
            return self._adapter_cache[model_name]

        # First try builtin models (no database query needed)
        builtin = self._get_builtin_capabilities()
        if model_name in builtin:
            capability = builtin[model_name]
            logger.info(f"Using builtin capability for {model_name}")
        else:
            # Fallback to database lookup
            try:
                capability = await self.capability_matrix.load_model(model_name)
            except Exception as e:
                logger.warning(f"Failed to load model {model_name} from database: {e}")
                # Default to gemini-3-flash-preview if model not found
                if 'gemini-3-flash-preview' in builtin:
                    logger.info(f"Falling back to gemini-3-flash-preview")
                    capability = builtin['gemini-3-flash-preview']
                elif 'gpt-4o-mini' in builtin:
                    logger.info(f"Falling back to gpt-4o-mini")
                    capability = builtin['gpt-4o-mini']
                else:
                    raise

        # Create provider-specific adapter
        if capability.provider == LLMProvider.GLM or capability.provider == 'glm':
            adapter = GLMAdapter(capability)
        elif capability.provider == LLMProvider.GOOGLE or capability.provider == 'google':
            adapter = GeminiAdapter(capability)
        elif capability.provider == LLMProvider.OPENAI or capability.provider == 'openai':
            adapter = OpenAIAdapter(capability)
        elif capability.provider == LLMProvider.CEREBRAS or capability.provider == 'cerebras':
            # Cerebras disabled due to httpx 0.28.1 compatibility issues
            logger.warning(f"Cerebras provider disabled, falling back to OpenAI")
            raise ValueError(f"Cerebras provider temporarily disabled")
        else:
            raise ValueError(f"Unsupported provider: {capability.provider}")

        self._adapter_cache[model_name] = adapter
        return adapter

    @observe()
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

    @observe()
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
            # Default to gemini-3-flash-preview for tool calling (fast, 1M context, thinking filtered)
            model = "gemini-3-flash-preview"
            logger.info(f"Using gemini-3-flash-preview for tool calling (default)")

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
        try:
            default_model = await self.capability_matrix.get_default_model()
        except Exception as e:
            logger.warning(f"Failed to get default model: {e}, using builtin gemini-3-flash-preview")
            adapter = await self.create_adapter("gemini-3-flash-preview")
            response = await adapter.generate(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            await self._track_metrics(response, is_fallback=True)
            return response

        if default_model.model_name == failed_model:
            # Default already failed, try gemini-3-flash-preview builtin
            logger.warning("Default model failed, trying gemini-3-flash-preview fallback")
            adapter = await self.create_adapter("gemini-3-flash-preview")
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
        """Fallback to gemini-3-flash-preview for tool calling"""
        # Try gemini-3-flash-preview first (builtin, no DB dependency)
        fallback_model = "gemini-3-flash-preview" if failed_model != "gemini-3-flash-preview" else "gpt-4o-mini"
        logger.warning(f"Tool calling failed, falling back to {fallback_model}")
        adapter = await self.create_adapter(fallback_model)

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


# Singleton instance
_llm_factory_instance: Optional['LLMFactory'] = None


async def get_llm_factory() -> 'LLMFactory':
    """
    Get or create singleton LLM factory instance.

    Returns:
        LLMFactory: Shared LLM factory instance
    """
    global _llm_factory_instance

    if _llm_factory_instance is None:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()
        _llm_factory_instance = LLMFactory(supabase)
        logger.info("LLMFactory singleton created")

    return _llm_factory_instance
